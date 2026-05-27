"""
Test suite for SMLRouter (binary api/local routing).
All tests run 100% locally — no external API calls, no Ollama required
except for the integration class.

Run unit tests only (no Ollama needed):
    uv run pytest tests/test_router.py -m 'not integration' -v

Run all including real Ollama:
    uv run pytest tests/test_router.py -v
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from crewai import LLM

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crew import SMLRouter, TaskType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_router(mock_sml_response: str | None = None) -> SMLRouter:
    """
    Build a SMLRouter with:
    - api_llm / local_llm: MagicMock (never called during routing)
    - _router_llm: mocked to return mock_sml_response if provided,
                   otherwise uses real Ollama (integration tests only)
    """
    api_llm   = MagicMock(spec=LLM)
    local_llm = MagicMock(spec=LLM)
    router    = SMLRouter(api_llm=api_llm, local_llm=local_llm)

    if mock_sml_response is not None:
        router._router_llm = MagicMock(spec=LLM)
        router._router_llm.call.return_value = mock_sml_response

    return router


# ---------------------------------------------------------------------------
# Unit tests: override map (no model call at all)
# ---------------------------------------------------------------------------

class TestOverrideMap:

    def test_planning_task_key(self):
        assert make_router().classify("anything", task_key="planning_task") == TaskType.PLANNING

    def test_coding_task_key(self):
        assert make_router().classify("anything", task_key="coding_task") == TaskType.CODING

    def test_review_task_key(self):
        assert make_router().classify("anything", task_key="review_task") == TaskType.REVIEW

    def test_override_ignores_description(self):
        """Even if description says 'review', coding_task key override wins."""
        assert make_router().classify("review this code please", task_key="coding_task") == TaskType.CODING


# ---------------------------------------------------------------------------
# Unit tests: SML binary inference with mocked model output
# ---------------------------------------------------------------------------

class TestSMLInference:

    def test_local_route(self):
        router = make_router(mock_sml_response='{"route": "local"}')
        assert router.classify("implement a REST API") == TaskType.CODING

    def test_api_route_planning(self):
        router = make_router(mock_sml_response='{"route": "api"}')
        assert router.classify("design the database schema") == TaskType.PLANNING

    def test_api_route_review(self):
        router = make_router(mock_sml_response='{"route": "api"}')
        assert router.classify("review the code for bugs") == TaskType.REVIEW

    def test_markdown_fence_stripped(self):
        """Small models often wrap JSON in ```json...``` — must be handled."""
        raw = "```json\n{\"route\": \"local\"}\n```"
        router = make_router(mock_sml_response=raw)
        assert router.classify("write a sorting algorithm") == TaskType.CODING

    def test_whitespace_stripped(self):
        router = make_router(mock_sml_response='  {"route": "api"}  ')
        assert router.classify("review the PR") == TaskType.REVIEW


# ---------------------------------------------------------------------------
# Unit tests: keyword fallback (broken model output)
# ---------------------------------------------------------------------------

class TestKeywordFallback:

    def test_fallback_on_invalid_json(self):
        router = make_router(mock_sml_response="I think it's local!")
        assert router.classify("implement a feature") == TaskType.CODING

    def test_fallback_on_empty_response(self):
        router = make_router(mock_sml_response="")
        assert router.classify("design the system architecture") == TaskType.PLANNING

    def test_fallback_coding_keywords(self):
        router = make_router(mock_sml_response="{invalid}")
        for kw in ["implement", "write", "code", "develop", "build", "generate"]:
            assert router._keyword_fallback(kw) == "local"

    def test_fallback_api_keywords(self):
        router = make_router(mock_sml_response="{invalid}")
        for kw in ["design", "plan", "architect", "review", "specification"]:
            assert router._keyword_fallback(kw) == "api"

    def test_fallback_defaults_to_api(self):
        router = make_router(mock_sml_response="{invalid}")
        assert router._keyword_fallback("do something unspecified") == "api"


# ---------------------------------------------------------------------------
# Unit tests: route() returns correct LLM handle
# ---------------------------------------------------------------------------

class TestRouteReturnsCorrectLLM:

    def test_local_route_returns_local_llm(self):
        router = make_router(mock_sml_response='{"route": "local"}')
        assert router.route("implement something") is router.local_llm

    def test_api_route_returns_api_llm(self):
        router = make_router(mock_sml_response='{"route": "api"}')
        assert router.route("design something") is router.api_llm

    def test_coding_task_key_returns_local_llm(self):
        router = make_router()
        assert router.route("anything", task_key="coding_task") is router.local_llm

    def test_planning_task_key_returns_api_llm(self):
        router = make_router()
        assert router.route("anything", task_key="planning_task") is router.api_llm

    def test_review_task_key_returns_api_llm(self):
        router = make_router()
        assert router.route("anything", task_key="review_task") is router.api_llm


# ---------------------------------------------------------------------------
# Integration test: real Ollama qwen2.5:0.5b (skip if Ollama not running)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRealOllamaRouter:
    """
    Requires Ollama running with qwen2.5:0.5b pulled.
    Skip automatically if Ollama is not reachable.
    """

    @pytest.fixture(autouse=True)
    def check_ollama(self):
        import httpx
        try:
            httpx.get("http://localhost:11434/api/tags", timeout=2)
        except Exception:
            pytest.skip("Ollama not running")

    def test_coding_intent_routes_local(self):
        router = make_router()
        result = router.route("implement a FastAPI server with JWT auth")
        assert result is router.local_llm

    def test_planning_intent_routes_api(self):
        router = make_router()
        result = router.route("design the architecture for a microservice system")
        assert result is router.api_llm

    def test_review_intent_routes_api(self):
        router = make_router()
        result = router.route("review this Python code for security vulnerabilities")
        assert result is router.api_llm
