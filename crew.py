import os
import json
import asyncio
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

    # Keywords that signal LOCAL (coding) — matched as whole words
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
        """Ask the local 1.5B model: 'api' or 'local'?"""
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
        """Word-boundary keyword match to avoid substring false positives."""
        words = set(text.lower().split())
        if words & self._LOCAL_KEYWORDS:
            return "local"
        return "api"

    def route(self, task_description: str, task_key: str | None = None) -> LLM:
        """Return the right LLM. Override map is always checked first."""
        if task_key and task_key in self._OVERRIDES:
            destination = self._OVERRIDES[task_key]
        else:
            destination = self._infer(task_description)

        icons = {"api": "🌐", "local": "💻"}
        print(f"[SMLRouter] '{task_key or 'dynamic'}' → {icons[destination]} {destination}")
        return self.local_llm if destination == "local" else self.api_llm


# ---------------------------------------------------------------------------
# QA result parser
# ---------------------------------------------------------------------------
def _parse_qa_result(raw: str) -> dict:
    """
    Parse the structured JSON output from the QA review task.
    Returns {"approved": bool, "issues": [...]} always — never raises.
    Falls back to approved=False if output is malformed.
    """
    try:
        text = raw.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        result = json.loads(text)
        approved = bool(result.get("approved", False))
        issues   = result.get("issues", [])
        return {"approved": approved, "issues": issues}
    except Exception:
        # Fallback: scan for legacy approval signals
        lower = raw.lower()
        approved = any(p in lower for p in ["lgtm", "no issues", "no errors", "approved", "passes all"])
        return {"approved": approved, "issues": [], "raw": raw}


# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------
@CrewBase
class CodingAgencyCrew():
    """Hybrid Coding Agency Crew — binary api/local routing via local SML."""

    MAX_REVIEW_ITERATIONS = 3

    # Lock prevents concurrent requests from sharing state on the same instance
    _lock = threading.Lock()

    def __init__(self) -> None:
        ollama_base   = os.getenv("OLLAMA_BASE_URL",   "http://localhost:11434")
        freellm_base  = os.getenv("FREELLM_BASE_URL",  "http://localhost:3001/v1")
        freellm_key   = os.getenv("FREELLMAPI_KEY",    "none")
        # Timeout in seconds for a full crew kickoff (default 10 min)
        self.llm_timeout = int(os.getenv("LLM_TIMEOUT_SEC", "600"))

        self.api_llm = LLM(
            model="openai/gpt-4o",
            base_url=freellm_base,
            api_key=freellm_key,
            temperature=0.2,
            timeout=self.llm_timeout,
        )

        self.local_llm = LLM(
            model="ollama/qwen2.5-coder:12b",
            base_url=ollama_base,
            temperature=0.1,
            timeout=self.llm_timeout,
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
        """
        Full plan → code → review loop with structured JSON QA gate.
        Thread-safe: acquires _lock so concurrent requests don't share state.
        """
        with self._lock:
            iteration    = 0
            feedback     = ""
            final_result = ""

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
                final_result = str(result)

                qa = _parse_qa_result(final_result)

                if qa["approved"]:
                    print(f"[Pipeline] ✅ QA approved on iteration {iteration}.")
                    break

                issues_summary = "; ".join(
                    f"[{i.get('severity','?')}] {i.get('description','')}" 
                    for i in qa.get("issues", [])
                ) or final_result  # fallback to raw if no structured issues

                feedback = issues_summary
                print(f"[Pipeline] ⚠️  QA found {len(qa.get('issues',[]))} issue(s) — retrying ({iteration+1}/{self.MAX_REVIEW_ITERATIONS}).")

            return final_result
