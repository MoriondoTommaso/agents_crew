"""
Test suite for SMLRouter.
All tests run 100% locally via Ollama qwen2.5:0.5b.
No external API calls are made.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from langchain_openai import ChatOpenAI

# Add parent dir to path so we can import crew
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crew import SMLRouter, TaskType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_router(mock_sml_response: str | None = None) -> SMLRouter:
    """
    Build a SMLRouter with:
    - frontier_llm: fully mocked (never called in routing)
    - local_llm:    fully mocked (never called in routing)
    - _router_llm:  mocked to return `mock_sml_response` if provided,
                    otherwise uses real Ollama (integration tests)
    """
    frontier = MagicMock(spec=ChatOpenAI)
    local    = MagicMock(spec=ChatOpenAI)
    router   = SMLRouter(frontier_llm=frontier, local_llm=local)

    if mock_sml_response is not None:
        mock_msg = MagicMock()
        mock_msg.content = mock_sml_response
        router._router_llm = MagicMock()
        router._router_llm.invoke.return_value = mock_msg

    return router


# ---------------------------------------------------------------------------
# Unit tests: override map (no model call at all)
# ---------------------------------------------------------------------------

class TestOverrideMap:
    """These tests never call any model."""

    def test_planning_task_key(self):
        router = make_router()
        assert router.classify("anything", task_key="planning_task") == TaskType.PLANNING

    def test_coding_task_key(self):
        router = make_router()
        assert router.classify("anything", task_key="coding_task") == TaskType.CODING

    def test_review_task_key(self):
        router = make_router()
        assert router.classify("anything", task_key="review_task") == TaskType.REVIEW

    def test_override_ignores_description(self):
        """Even if description says 'review', key override wins."""
        router = make_router()
        assert router.classify("review this code please", task_key="coding_task") == TaskType.CODING


# ---------------------------------------------------------------------------
# Unit tests: SML inference with mocked model output
# ---------------------------------------------------------------------------

class TestSMLInference:
    """Mock the router LLM to test parsing logic without Ollama running."""

    def test_clean_json_coding(self):
        router = make_router(mock_sml_response='{"task_type": "coding"}')
        assert router.classify("implement a REST API") == TaskType.CODING

    def test_clean_json_planning(self):
        router = make_router(mock_sml_response='{"task_type": "planning"}')
        assert router.classify("design the database schema") == TaskType.PLANNING

    def test_clean_json_review(self):
        router = make_router(mock_sml_response='{"task_type": "review"}')
        assert router.classify("check the code for bugs") == TaskType.REVIEW

    def test_markdown_fence_stripped(self):
        """Small models often wrap JSON in ```json ... ``` — must be handled."""
        raw = "```json\n{\"task_type\": \"coding\"}\n```"
        router = make_router(mock_sml_response=raw)
        assert router.classify("write a sorting algorithm") == TaskType.CODING

    def test_whitespace_in_response(self):
        router = make_router(mock_sml_response='  {"task_type": "review"}  ')
        assert router.classify("review the PR") == TaskType.REVIEW


# ---------------------------------------------------------------------------
# Unit tests: keyword fallback (broken/empty model output)
# ---------------------------------------------------------------------------

class TestKeywordFallback:
    """Test the heuristic fallback when SML output is garbage."""

    def test_fallback_on_invalid_json(self):
        router = make_router(mock_sml_response="I think it's coding!")
        # invalid JSON → fallback, description contains 'implement'
        assert router.classify("implement a feature") == TaskType.CODING

    def test_fallback_on_empty_response(self):
        router = make_router(mock_sml_response="")
        # empty → fallback, description contains 'design'
        assert router.classify("design the system architecture") == TaskType.PLANNING

    def test_fallback_planning_keywords(self):
        router = make_router(mock_sml_response="{invalid}")
        for kw in ["design", "plan", "architect", "specification", "requirement"]:
            assert router._keyword_fallback(kw) == TaskType.PLANNING

    def test_fallback_coding_keywords(self):
        router = make_router(mock_sml_response="{invalid}")
        for kw in ["implement", "write", "code", "develop", "build"]:
            assert router._keyword_fallback(kw) == TaskType.CODING

    def test_fallback_defaults_to_review(self):
        """Unknown description defaults to review."""
        router = make_router(mock_sml_response="{invalid}")
        assert router._keyword_fallback("do something unspecified") == TaskType.REVIEW


# ---------------------------------------------------------------------------
# Unit tests: route() returns correct LLM handle
# ---------------------------------------------------------------------------

class TestRouteReturnsCorrectLLM:
    """Verify that route() returns local_llm for coding, frontier for rest."""

    def test_coding_returns_local_llm(self):
        router = make_router(mock_sml_response='{"task_type": "coding"}')
        result = router.route("implement something")
        assert result is router.local_llm

    def test_planning_returns_frontier_llm(self):
        router = make_router(mock_sml_response='{"task_type": "planning"}')
        result = router.route("design something")
        assert result is router.frontier_llm

    def test_review_returns_frontier_llm(self):
        router = make_router(mock_sml_response='{"task_type": "review"}')
        result = router.route("review something")
        assert result is router.frontier_llm

    def test_override_coding_key_returns_local_llm(self):
        router = make_router()  # no mock needed, override fires before inference
        result = router.route("anything", task_key="coding_task")
        assert result is router.local_llm

    def test_override_planning_key_returns_frontier_llm(self):
        router = make_router()
        result = router.route("anything", task_key="planning_task")
        assert result is router.frontier_llm


# ---------------------------------------------------------------------------
# Integration test: real Ollama qwen2.5:0.5b (skip if Ollama not running)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRealOllamaRouter:
    """
    Runs only when Ollama is active and qwen2.5:0.5b is pulled.
    Skip with: pytest tests/test_router.py -m 'not integration'
    """

    @pytest.fixture(autouse=True)
    def check_ollama(self):
        import httpx
        try:
            httpx.get("http://localhost:11434/api/tags", timeout=2)
        except Exception:
            pytest.skip("Ollama not running")

    def test_coding_intent(self):
        router = make_router()  # uses real _router_llm
        result = router.classify("implement a FastAPI server with JWT auth")
        assert result == TaskType.CODING

    def test_planning_intent(self):
        router = make_router()
        result = router.classify("design the architecture for a microservice system")
        assert result == TaskType.PLANNING

    def test_review_intent(self):
        router = make_router()
        result = router.classify("review this Python code for security vulnerabilities")
        assert result == TaskType.REVIEW
