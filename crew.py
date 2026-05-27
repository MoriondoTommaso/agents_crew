import os
import json
import threading
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
# ---------------------------------------------------------------------------
class SMLRouter:
    """
    Routes tasks to either:
    - api_llm  : FreeLLM endpoint (planning + review)
    - local_llm: Ollama qwen2.5-coder:12b (coding)

    The router itself uses qwen2.5:1.5b locally — no external calls ever.
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

    _LOCAL_KEYWORDS = {"implement", "write", "code", "develop", "build", "generate", "create"}

    def __init__(self, api_llm: LLM, local_llm: LLM):
        self.api_llm   = api_llm
        self.local_llm = local_llm

        self._router_llm = LLM(
            model="ollama/qwen2.5:1.5b",
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.0,
            max_tokens=16,
        )

    def _infer(self, task_description: str) -> str:
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user",   "content": task_description[:512]},
        ]
        try:
            raw = self._router_llm.call(messages)
            raw = raw.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(raw)["route"]
        except Exception as e:
            print(f"[SMLRouter] Fallback triggered: {e}")
            return self._keyword_fallback(task_description)

    def _keyword_fallback(self, text: str) -> str:
        words = set(text.lower().split())
        if words & self._LOCAL_KEYWORDS:
            return "local"
        return "api"

    def route(self, task_description: str, task_key: str | None = None) -> LLM:
        if task_key and task_key in self._OVERRIDES:
            destination = self._OVERRIDES[task_key]
        else:
            destination = self._infer(task_description)

        icons = {"api": "🌐", "local": "💻"}
        print(f"[SMLRouter] '{task_key or 'dynamic'}' → {icons[destination]} {destination}")
        return self.local_llm if destination == "local" else self.api_llm


# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------
@CrewBase
class CodingAgencyCrew():
    """
    Hybrid Coding Agency Crew — single-pass pipeline.

    plan (api) → code (local) → output

    No internal self-healing loop: if used with an agentic harness
    (Open-Claw, Aider, Continue.dev), the harness handles iteration
    with real code execution tools — far more reliable than LLM-only review.
    """

    # Lock prevents concurrent requests from sharing CrewAI instance state
    _lock = threading.Lock()

    def __init__(self) -> None:
        ollama_base  = os.getenv("OLLAMA_BASE_URL",  "http://localhost:11434")
        freellm_base = os.getenv("FREELLM_BASE_URL", "http://localhost:3001/v1")
        freellm_key  = os.getenv("FREELLMAPI_KEY",   "none")
        llm_timeout  = int(os.getenv("LLM_TIMEOUT_SEC", "600"))

        self.api_llm = LLM(
            model="openai/gpt-4o",
            base_url=freellm_base,
            api_key=freellm_key,
            temperature=0.2,
            timeout=llm_timeout,
        )

        self.local_llm = LLM(
            model="ollama/qwen2.5-coder:12b",
            base_url=ollama_base,
            temperature=0.1,
            timeout=llm_timeout,
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

    @task
    def planning_task(self) -> Task:
        return Task(config=self.tasks_config['planning_task'])

    @task
    def coding_task(self) -> Task:
        return Task(config=self.tasks_config['coding_task'])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )

    def run(self, inputs: dict) -> str:
        """Single-pass plan → code. Thread-safe."""
        with self._lock:
            result = self.crew().kickoff(inputs=inputs)
            return str(result)
