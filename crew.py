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
# SML Router
# Binary decision: API (FreeLLM picks the model) vs LOCAL (qwen2.5-coder:12b)
# ---------------------------------------------------------------------------
class SMLRouter:
    """
    Routes tasks to either:
      - api_llm  : FreeLLM endpoint (planning + review) — FreeLLM picks the model
      - local_llm: Ollama qwen2.5-coder:12b (coding)   — zero API consumption

    The router itself uses qwen2.5:0.5b locally — no external calls ever.
    """

    SYSTEM_PROMPT = (
        "You are a routing classifier. "
        "Given a task description, reply with ONLY a JSON object: "
        '{"route": "api"} if the task requires reasoning, planning, architecture, '
        'or code review. Reply {"route": "local"} if the task requires writing, '
        "implementing, or generating code. No explanation, no markdown."
    )

    _OVERRIDES: dict[str, str] = {
        "planning_task": "api",
        "coding_task":   "local",
        "review_task":   "api",
    }

    def __init__(self, api_llm: LLM, local_llm: LLM):
        self.api_llm   = api_llm
        self.local_llm = local_llm

        self._router_llm = LLM(
            model="ollama/qwen2.5:0.5b",
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.0,
            max_tokens=16,
        )

    def _infer(self, task_description: str) -> str:
        """Ask the local 0.5B model: 'api' or 'local'?"""
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user",   "content": task_description[:512]},
        ]
        try:
            raw = self._router_llm.call(messages)
            raw = raw.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(raw)["route"]  # "api" or "local"
        except Exception as e:
            print(f"[SMLRouter] Fallback triggered: {e}")
            return self._keyword_fallback(task_description)

    def _keyword_fallback(self, text: str) -> str:
        t = text.lower()
        if any(w in t for w in ["implement", "write", "code", "develop", "build", "generate"]):
            return "local"
        return "api"

    def route(self, task_description: str, task_key: str | None = None) -> LLM:
        """Return the right LLM. Override map is always checked first."""
        # 1. Hardcoded override for known task keys (no model call)
        if task_key and task_key in self._OVERRIDES:
            destination = self._OVERRIDES[task_key]
        else:
            # 2. SML inference for dynamic tasks
            destination = self._infer(task_description)

        icons = {"api": "🌐", "local": "💻"}
        print(f"[SMLRouter] '{task_key or 'dynamic'}' → {icons[destination]} {destination}")

        return self.local_llm if destination == "local" else self.api_llm


# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------
@CrewBase
class CodingAgencyCrew():
    """Hybrid Coding Agency Crew — binary api/local routing via local SML."""

    MAX_REVIEW_ITERATIONS = 3

    def __init__(self) -> None:
        ollama_base  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        freellm_base = os.getenv("FREELLM_BASE_URL", "http://localhost:3001/v1")
        freellm_key  = os.getenv("FREELLMAPI_KEY", "none")

        # API LLM: FreeLLM proxy — it picks the best available model automatically.
        # We pass gpt-4o as a canonical name; FreeLLM ignores it and routes freely.
        self.api_llm = LLM(
            model="openai/gpt-4o",
            base_url=freellm_base,
            api_key=freellm_key,
            temperature=0.2,
        )

        # Local LLM: Qwen Coder 12B — heavy lifting, zero API consumption.
        self.local_llm = LLM(
            model="ollama/qwen2.5-coder:12b",
            base_url=ollama_base,
            temperature=0.1,
        )

        self.router = SMLRouter(
            api_llm=self.api_llm,
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

            result       = self.crew().kickoff(inputs=run_inputs)
            final_result = result
            output_text  = str(result).lower()

            if any(p in output_text for p in ["lgtm", "no issues", "no errors", "approved", "passes all"]):
                print(f"[Pipeline] ✅ QA passed on iteration {iteration}.")
                break

            feedback = str(result)
            print(f"[Pipeline] ⚠️  QA found issues — retrying ({iteration+1}/{self.MAX_REVIEW_ITERATIONS}).")

        return str(final_result)
