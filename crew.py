import os
import json
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from langchain_openai import ChatOpenAI
from enum import Enum


# ---------------------------------------------------------------------------
# Task type enum
# ---------------------------------------------------------------------------
class TaskType(str, Enum):
    PLANNING = "planning"
    CODING   = "coding"
    REVIEW   = "review"


# ---------------------------------------------------------------------------
# SML Router
# Uses a lightweight model (Llama 3.1 8B via Groq) to classify the current
# task type and return the appropriate LLM handle.
# ---------------------------------------------------------------------------
class SMLRouter:
    """
    Small-Model Router: classifies a task description into one of
    [planning, coding, review] and returns the right LLM.
    """

    SYSTEM_PROMPT = """You are a routing classifier for a coding agent pipeline.
    Given a task description, output ONLY a JSON object with a single key
    'task_type' whose value is one of: 'planning', 'coding', 'review'.
    Do not add any explanation."""

    def __init__(self, frontier_llm: ChatOpenAI, local_llm: ChatOpenAI):
        # The router itself uses a fast, cheap small model
        self.router_llm = ChatOpenAI(
            openai_api_base="https://api.groq.com/openai/v1",
            openai_api_key=os.getenv("GROQ_API_KEY"),
            model_name="llama-3.1-8b-instant",
            temperature=0.0,
            max_tokens=32,
        )
        self.frontier_llm = frontier_llm
        self.local_llm    = local_llm

        # Explicit override map (task yaml key → forced type)
        self._overrides: dict[str, TaskType] = {
            "planning_task": TaskType.PLANNING,
            "coding_task":   TaskType.CODING,
            "review_task":   TaskType.REVIEW,
        }

    def classify(self, task_description: str, task_key: str | None = None) -> TaskType:
        """Classify by task_key override first, then SML inference."""
        if task_key and task_key in self._overrides:
            return self._overrides[task_key]

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user",   "content": task_description[:512]},
        ]
        try:
            response = self.router_llm.invoke(messages)
            payload  = json.loads(response.content.strip())
            return TaskType(payload["task_type"])
        except Exception:
            # Fallback heuristic
            desc_lower = task_description.lower()
            if any(w in desc_lower for w in ["design", "plan", "architect", "specification"]):
                return TaskType.PLANNING
            if any(w in desc_lower for w in ["implement", "write", "code", "develop"]):
                return TaskType.CODING
            return TaskType.REVIEW

    def route(self, task_description: str, task_key: str | None = None) -> ChatOpenAI:
        """Return the right LLM for the classified task type."""
        task_type = self.classify(task_description, task_key)
        print(f"[SMLRouter] '{task_key or 'dynamic'}' → {task_type.value}")

        if task_type == TaskType.CODING:
            return self.local_llm        # Qwen Coder 12B via Ollama
        return self.frontier_llm         # Frontier model for planning + review


# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------
@CrewBase
class CodingAgencyCrew():
    """Hybrid Coding Agency Crew — SML-routed local + cloud LLMs."""

    MAX_REVIEW_ITERATIONS = 3

    def __init__(self) -> None:
        # ── Frontier LLM (FreeLLM / OpenRouter) ──────────────────────────
        self.frontier_llm = ChatOpenAI(
            openai_api_base=os.getenv("FREELLM_BASE_URL", "http://localhost:3001/v1"),
            openai_api_key=os.getenv("FREELLMAPI_KEY", "none"),
            model_name="auto",
            temperature=0.2,
        )

        # ── Local LLM: Qwen Coder 12B via Ollama ─────────────────────────
        self.local_llm = ChatOpenAI(
            openai_api_base=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            openai_api_key="ollama",
            model_name="qwen2.5-coder:12b",
            temperature=0.1,
        )

        # ── SML Router ────────────────────────────────────────────────────
        self.router = SMLRouter(
            frontier_llm=self.frontier_llm,
            local_llm=self.local_llm,
        )

    # ── Agents ────────────────────────────────────────────────────────────

    @agent
    def senior_architect(self) -> Agent:
        return Agent(
            config=self.agents_config['senior_architect'],
            llm=self.router.route(
                self.agents_config['senior_architect']['goal'],
                task_key="planning_task",
            ),
            verbose=True,
        )

    @agent
    def senior_developer(self) -> Agent:
        return Agent(
            config=self.agents_config['senior_developer'],
            llm=self.router.route(
                self.agents_config['senior_developer']['goal'],
                task_key="coding_task",
            ),
            verbose=True,
        )

    @agent
    def qa_engineer(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_engineer'],
            llm=self.router.route(
                self.agents_config['qa_engineer']['goal'],
                task_key="review_task",
            ),
            verbose=True,
        )

    # ── Tasks ─────────────────────────────────────────────────────────────

    @task
    def planning_task(self) -> Task:
        return Task(config=self.tasks_config['planning_task'])

    @task
    def coding_task(self) -> Task:
        return Task(config=self.tasks_config['coding_task'])

    @task
    def review_task(self) -> Task:
        return Task(
            config=self.tasks_config['review_task'],
            output_file='report.md',
        )

    # ── Crew + self-healing loop ──────────────────────────────────────────

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )

    def run_with_healing(self, inputs: dict) -> str:
        """
        Run the full pipeline with a self-healing loop.
        If the QA review finds errors, the feedback is injected back into
        the coding task and the code+review cycle repeats (max 3 iterations).
        """
        iteration   = 0
        feedback    = ""
        final_result = None

        while iteration < self.MAX_REVIEW_ITERATIONS:
            iteration += 1
            print(f"\n[Pipeline] Iteration {iteration}/{self.MAX_REVIEW_ITERATIONS}")

            # Inject prior review feedback into coding task on retry
            if feedback:
                augmented = dict(inputs)
                augmented["review_feedback"] = (
                    f"\n\nPrevious review found issues — fix them:\n{feedback}"
                )
            else:
                augmented = inputs

            result = self.crew().kickoff(inputs=augmented)
            final_result = result

            # Parse result to check if QA flagged errors
            output_text = str(result)
            no_errors = any(
                phrase in output_text.lower()
                for phrase in ["no issues", "lgtm", "approved", "no errors", "passes all"]
            )

            if no_errors:
                print(f"[Pipeline] QA passed on iteration {iteration}. Done.")
                break

            # Extract feedback for next iteration
            feedback = output_text
            print(f"[Pipeline] QA found issues. Retrying (iteration {iteration+1}).")

        return str(final_result)
