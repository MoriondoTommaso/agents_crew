import os
import json
from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
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

    _OVERRIDES: dict[str, TaskType] = {
        "planning_task": TaskType.PLANNING,
        "coding_task":   TaskType.CODING,
        "review_task":   TaskType.REVIEW,
    }

    def __init__(self, frontier_llm: LLM, local_llm: LLM):
        self.frontier_llm = frontier_llm
        self.local_llm    = local_llm

        self._router_llm = LLM(
            model="ollama/qwen2.5:0.5b",
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.0,
            max_tokens=32,
        )

    def classify(self, task_description: str, task_key: str | None = None) -> TaskType:
        if task_key and task_key in self._OVERRIDES:
            return self._OVERRIDES[task_key]

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user",   "content": task_description[:512]},
        ]
        try:
            response = self._router_llm.call(messages)
            raw = response.strip().replace("```json", "").replace("```", "").strip()
            payload = json.loads(raw)
            return TaskType(payload["task_type"])
        except Exception as e:
            print(f"[SMLRouter] Inference fallback triggered: {e}")
            return self._keyword_fallback(task_description)

    def _keyword_fallback(self, text: str) -> TaskType:
        t = text.lower()
        if any(w in t for w in ["design", "plan", "architect", "specification", "requirement"]):
            return TaskType.PLANNING
        if any(w in t for w in ["implement", "write", "code", "develop", "build"]):
            return TaskType.CODING
        return TaskType.REVIEW

    def route(self, task_description: str, task_key: str | None = None) -> LLM:
        task_type = self.classify(task_description, task_key)
        emoji = {TaskType.PLANNING: "📐", TaskType.CODING: "💻", TaskType.REVIEW: "🔍"}
        print(f"[SMLRouter] '{task_key or 'dynamic'}' → {emoji[task_type]} {task_type.value}")
        if task_type == TaskType.CODING:
            return self.local_llm
        return self.frontier_llm


# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------
@CrewBase
class CodingAgencyCrew():
    """Hybrid Coding Agency Crew — fully local SML router, zero routing API calls."""

    MAX_REVIEW_ITERATIONS = 3

    def __init__(self) -> None:
        ollama_base  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        freellm_base = os.getenv("FREELLM_BASE_URL", "http://localhost:3001/v1")
        freellm_key  = os.getenv("FREELLMAPI_KEY", "none")

        # FreeLLM exposes an OpenAI-compatible endpoint.
        # LiteLLM needs a real model name string — we use gpt-4o as the
        # canonical name; FreeLLM will map it to the best available model.
        self.frontier_llm = LLM(
            model="openai/gpt-4o",
            base_url=freellm_base,
            api_key=freellm_key,
            temperature=0.2,
        )

        self.local_llm = LLM(
            model="ollama/qwen2.5-coder:12b",
            base_url=ollama_base,
            temperature=0.1,
        )

        self.router = SMLRouter(
            frontier_llm=self.frontier_llm,
            local_llm=self.local_llm,
        )

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

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )

    def run_with_healing(self, inputs: dict) -> str:
        iteration    = 0
        feedback     = ""
        final_result = None

        while iteration < self.MAX_REVIEW_ITERATIONS:
            iteration += 1
            print(f"\n{'='*60}")
            print(f"[Pipeline] Iteration {iteration}/{self.MAX_REVIEW_ITERATIONS}")
            print(f"{'='*60}")

            run_inputs = dict(inputs)
            run_inputs["review_feedback"] = (
                f"\n\n--- Previous QA review found issues, fix them ---\n{feedback}"
                if feedback else ""
            )

            result      = self.crew().kickoff(inputs=run_inputs)
            final_result = result
            output_text = str(result).lower()

            if any(p in output_text for p in ["lgtm", "no issues", "no errors", "approved", "passes all"]):
                print(f"[Pipeline] ✅ QA passed on iteration {iteration}.")
                break

            feedback = str(result)
            print(f"[Pipeline] ⚠️  QA found issues — retrying ({iteration+1}/{self.MAX_REVIEW_ITERATIONS}).")

        return str(final_result)
