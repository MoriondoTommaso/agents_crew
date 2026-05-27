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
# SML Router — fully local, zero API calls
# Uses qwen2.5:0.5b via Ollama for intent classification
# ---------------------------------------------------------------------------
class SMLRouter:
    """
    Small-Model Router: classifies a task description into one of
    [planning, coding, review] using a local 0.5B model.
    No API calls, <100ms latency on Apple Silicon.
    """

    SYSTEM_PROMPT = (
        "You are a routing classifier for a coding agent pipeline. "
        "Given a task description, output ONLY a raw JSON object with a "
        "single key 'task_type' whose value is one of: "
        "'planning', 'coding', 'review'. "
        "No explanation, no markdown, just the JSON."
    )

    # Hardcoded overrides — no inference needed for known task keys
    _OVERRIDES: dict[str, TaskType] = {
        "planning_task": TaskType.PLANNING,
        "coding_task":   TaskType.CODING,
        "review_task":   TaskType.REVIEW,
    }

    def __init__(self, frontier_llm: ChatOpenAI, local_llm: ChatOpenAI):
        self.frontier_llm = frontier_llm
        self.local_llm    = local_llm

        # Router: local qwen2.5:0.5b — tiny, fast, free
        self._router_llm = ChatOpenAI(
            openai_api_base=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            openai_api_key="ollama",
            model_name="qwen2.5:0.5b",
            temperature=0.0,
            max_tokens=32,
        )

    def classify(self, task_description: str, task_key: str | None = None) -> TaskType:
        """Classify task type. Override map is checked first."""
        # 1. Fast path: hardcoded override
        if task_key and task_key in self._OVERRIDES:
            return self._OVERRIDES[task_key]

        # 2. SML inference via local Ollama
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user",   "content": task_description[:512]},
        ]
        try:
            response = self._router_llm.invoke(messages)
            raw = response.content.strip()
            # Strip potential markdown code fences
            raw = raw.replace("```json", "").replace("```", "").strip()
            payload = json.loads(raw)
            return TaskType(payload["task_type"])
        except Exception as e:
            print(f"[SMLRouter] Inference fallback triggered: {e}")
            return self._keyword_fallback(task_description)

    def _keyword_fallback(self, text: str) -> TaskType:
        """Heuristic fallback when SML output is unparseable."""
        t = text.lower()
        if any(w in t for w in ["design", "plan", "architect", "specification", "requirement"]):
            return TaskType.PLANNING
        if any(w in t for w in ["implement", "write", "code", "develop", "build"]):
            return TaskType.CODING
        return TaskType.REVIEW

    def route(self, task_description: str, task_key: str | None = None) -> ChatOpenAI:
        """Return the right LLM for the given task."""
        task_type = self.classify(task_description, task_key)
        emoji = {TaskType.PLANNING: "📐", TaskType.CODING: "💻", TaskType.REVIEW: "🔍"}
        print(f"[SMLRouter] '{task_key or 'dynamic'}' → {emoji[task_type]} {task_type.value}")

        if task_type == TaskType.CODING:
            return self.local_llm      # qwen2.5-coder:12b via Ollama
        return self.frontier_llm       # frontier model for planning + review


# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------
@CrewBase
class CodingAgencyCrew():
    """Hybrid Coding Agency Crew — fully local SML router, zero routing API calls."""

    MAX_REVIEW_ITERATIONS = 3

    def __init__(self) -> None:
        # ── Frontier LLM (FreeLLM / OpenRouter) ──────────────────────────────
        self.frontier_llm = ChatOpenAI(
            openai_api_base=os.getenv("FREELLM_BASE_URL", "http://localhost:3001/v1"),
            openai_api_key=os.getenv("FREELLMAPI_KEY", "none"),
            model_name="auto",
            temperature=0.2,
        )

        # ── Local LLM: Qwen Coder 12B via Ollama ─────────────────────────────
        self.local_llm = ChatOpenAI(
            openai_api_base=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            openai_api_key="ollama",
            model_name="qwen2.5-coder:12b",
            temperature=0.1,
        )

        # ── SML Router: local qwen2.5:0.5b, zero external calls ───────────────
        self.router = SMLRouter(
            frontier_llm=self.frontier_llm,
            local_llm=self.local_llm,
        )

    # ── Agents ────────────────────────────────────────────────────────────────

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

    # ── Tasks ─────────────────────────────────────────────────────────────────

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

    # ── Crew + self-healing loop ───────────────────────────────────────────────

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
        Run the full pipeline with self-healing loop.
        QA review feedback is injected back into the coding task
        on each retry until LGTM or max iterations reached.
        """
        iteration    = 0
        feedback     = ""
        final_result = None

        while iteration < self.MAX_REVIEW_ITERATIONS:
            iteration += 1
            print(f"\n{'='*60}")
            print(f"[Pipeline] Iteration {iteration}/{self.MAX_REVIEW_ITERATIONS}")
            print(f"{'='*60}")

            run_inputs = dict(inputs)
            if feedback:
                run_inputs["review_feedback"] = (
                    f"\n\n--- Previous QA review found issues, fix them ---\n{feedback}"
                )
            else:
                run_inputs.setdefault("review_feedback", "")

            result       = self.crew().kickoff(inputs=run_inputs)
            final_result = result
            output_text  = str(result).lower()

            passed = any(
                phrase in output_text
                for phrase in ["lgtm", "no issues", "no errors", "approved", "passes all"]
            )

            if passed:
                print(f"[Pipeline] ✅ QA passed on iteration {iteration}.")
                break
            else:
                feedback = str(result)
                print(f"[Pipeline] ⚠️  QA found issues — retrying ({iteration+1}/{self.MAX_REVIEW_ITERATIONS}).")

        return str(final_result)
