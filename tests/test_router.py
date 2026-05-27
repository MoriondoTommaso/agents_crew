"""
Test suite for SMLRouter.
All unit tests run 100% locally — no Ollama, no API calls.

Key facts from crew.py:
- SMLRouter.route(task_description, task_key=None) -> LLM
- _OVERRIDES: planning_task -> api, coding_task -> local, review_task -> api
- _infer() calls self._router_llm.call(messages) -> parses {"route": "api|local"}
- _keyword_fallback(text) -> "local" or "api" (string, not TaskType)
- route() returns self.local_llm if destination=="local" else self.api_llm

Run unit tests only (no Ollama needed):
    uv run pytest tests/test_router.py -m 'not integration' -v

Run all including real Ollama:
    uv run pytest tests/test_router.py -v
"""
import pytest
from unittest.mock import MagicMock
from crewai import LLM

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crew import SMLRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_router(mock_router_response: str | None = None) -> SMLRouter:
    """
    Build a SMLRouter with mocked api_llm and local_llm.
    If mock_router_response is provided, also mocks _router_llm.call()
    so no Ollama connection is made.
    """
    api_llm = MagicMock(spec=LLM)
    local_llm = MagicMock(spec=LLM)

    # Patch __init__'s LLM() call for _router_llm before instantiation
    # by building router then replacing _router_llm
    import unittest.mock as mock
    with mock.patch("crew.LLM") as mock_llm_cls:
        mock_llm_cls.return_value = MagicMock(spec=LLM)
        router = SMLRouter(api_llm=api_llm, local_llm=local_llm)

    if mock_router_response is not None:
        router._router_llm = MagicMock(spec=LLM)
        router._router_llm.call.return_value = mock_router_response

    return router


# ---------------------------------------------------------------------------
# Unit tests: override map (no model call at all)
# ---------------------------------------------------------------------------

class TestOverrideMap:

    def test_planning_task_key_returns_api_llm(self):
        router = make_router()
        result = router.route("design the architecture", task_key="planning_task")
        assert result is router.api_llm

    def test_coding_task_key_returns_local_llm(self):
        router = make_router()
        result = router.route("implement the feature", task_key="coding_task")
        assert result is router.local_llm

    def test_review_task_key_returns_api_llm(self):
        router = make_router()
        result = router.route("review the code", task_key="review_task")
        assert result is router.api_llm

    def test_override_ignores_description(self):
        """coding_task key wins even if description sounds like review."""
        router = make_router()
        result = router.route("review this code please", task_key="coding_task")
        assert result is router.local_llm

    def test_unknown_task_key_falls_through_to_infer(self):
        """A task_key not in _OVERRIDES should fall through to _infer."""
        router = make_router(mock_router_response='{"route": "local"}')
        result = router.route("do something", task_key="unknown_task")
        assert result is router.local_llm


# ---------------------------------------------------------------------------
# Unit tests: SML binary inference with mocked _router_llm.call()
# ---------------------------------------------------------------------------

class TestSMLInference:

    def test_local_route_returns_local_llm(self):
        router = make_router(mock_router_response='{"route": "local"}')
        assert router.route("implement a REST API") is router.local_llm

    def test_api_route_returns_api_llm(self):
        router = make_router(mock_router_response='{"route": "api"}')
        assert router.route("design the database schema") is router.api_llm

    def test_api_route_review(self):
        router = make_router(mock_router_response='{"route": "api"}')
        assert router.route("review the code for bugs") is router.api_llm

    def test_markdown_fence_stripped(self):
        """Model wraps JSON in ```json...``` — must be handled."""
        raw = '```json\n{"route": "local"}\n```'
        router = make_router(mock_router_response=raw)
        assert router.route("write a sorting algorithm") is router.local_llm

    def test_whitespace_stripped(self):
        router = make_router(mock_router_response='  {"route": "api"}  ')
        assert router.route("review the PR") is router.api_llm


# ---------------------------------------------------------------------------
# Unit tests: keyword fallback (broken model output)
# ---------------------------------------------------------------------------

class TestKeywordFallback:

    def test_fallback_on_invalid_json_returns_api_llm(self):
        """Invalid JSON triggers keyword fallback; 'implement' → local."""
        router = make_router(mock_router_response="I think it's local!")
        # 'implement' is a coding keyword → local
        assert router.route("implement a feature") is router.local_llm

    def test_fallback_on_empty_response_returns_api_llm(self):
        """Empty response triggers fallback; 'design' → api."""
        router = make_router(mock_router_response="")
        assert router.route("design the system architecture") is router.api_llm

    def test_fallback_coding_keywords_return_local(self):
        router = make_router(mock_router_response="{invalid}")
        for kw in ["implement", "write", "code", "develop", "build", "generate"]:
            assert router._keyword_fallback(kw) == "local"

    def test_fallback_api_keywords_return_api(self):
        router = make_router(mock_router_response="{invalid}")
        for kw in ["design", "plan", "architect", "review", "specification"]:
            assert router._keyword_fallback(kw) == "api"

    def test_fallback_defaults_to_api(self):
        router = make_router(mock_router_response="{invalid}")
        assert router._keyword_fallback("do something unspecified") == "api"


# ---------------------------------------------------------------------------
# Integration tests: real Ollama qwen2.5:0.5b (skipped if Ollama not running)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRealOllamaRouter:
    """
    Requires Ollama running with qwen2.5:0.5b pulled.
    Automatically skipped if Ollama is not reachable.
    """

    @pytest.fixture(autouse=True)
    def check_ollama(self):
        import httpx
        try:
            httpx.get("http://localhost:11434/api/tags", timeout=2)
        except Exception:
            pytest.skip("Ollama not running")

    def _real_router(self):
        """Real router — uses actual Ollama for _router_llm."""
        from crewai import LLM
        api_llm = MagicMock(spec=LLM)
        local_llm = MagicMock(spec=LLM)
        return SMLRouter(api_llm=api_llm, local_llm=local_llm)

    def test_coding_intent_routes_local(self):
        router = self._real_router()
        result = router.route("implement a FastAPI server with JWT auth")
        assert result is router.local_llm

    def test_planning_intent_routes_api(self):
        router = self._real_router()
        result = router.route("design the architecture for a microservice system")
        assert result is router.api_llm

    def test_review_intent_routes_api(self):
        router = self._real_router()
        result = router.route("review this Python code for security vulnerabilities")
        assert result is router.api_llm
