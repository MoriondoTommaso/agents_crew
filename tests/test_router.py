"""
Test suite for SMLRouter.

Unit tests (no Ollama required):
 - TestOverrideMap     : task_key override map always wins
 - TestSMLInference    : _infer() parses JSON correctly
 - TestKeywordFallback : _keyword_fallback() covers edge cases

Run with:
    uv run pytest tests/test_router.py -v
"""
import sys
import os
import json
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
from crewai import LLM
from crew import SMLRouter


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def router():
    """SMLRouter with fully mocked LLMs and a mocked _router_llm."""
    api_llm   = MagicMock(spec=LLM)
    local_llm = MagicMock(spec=LLM)

    with patch("crew.LLM") as mock_llm_cls:
        mock_llm_cls.return_value = MagicMock(spec=LLM)
        r = SMLRouter(api_llm=api_llm, local_llm=local_llm)

    r.api_llm   = api_llm
    r.local_llm = local_llm
    return r


# ---------------------------------------------------------------------------
# Override map (task_key always wins, no LLM call ever)
# ---------------------------------------------------------------------------

class TestOverrideMap:

    def test_planning_task_key_returns_api_llm(self, router):
        assert router.route("anything", task_key="planning_task") is router.api_llm

    def test_coding_task_key_returns_local_llm(self, router):
        assert router.route("anything", task_key="coding_task") is router.local_llm

    def test_review_task_key_returns_api_llm(self, router):
        assert router.route("anything", task_key="review_task") is router.api_llm

    def test_override_ignores_description(self, router):
        # Even a "write code" description is overridden to api by planning_task key
        assert router.route("write code implement build", task_key="planning_task") is router.api_llm

    def test_unknown_task_key_falls_through_to_infer(self, router):
        router._router_llm = MagicMock()
        router._router_llm.call.return_value = '{"route": "local"}'
        result = router.route("implement a cache", task_key="unknown_task")
        assert result is router.local_llm


# ---------------------------------------------------------------------------
# _infer(): JSON parsing
# ---------------------------------------------------------------------------

class TestSMLInference:

    def _router_with_response(self, raw: str):
        api_llm   = MagicMock(spec=LLM)
        local_llm = MagicMock(spec=LLM)
        with patch("crew.LLM") as mock_llm_cls:
            mock_llm_cls.return_value = MagicMock(spec=LLM)
            r = SMLRouter(api_llm=api_llm, local_llm=local_llm)
        r.api_llm   = api_llm
        r.local_llm = local_llm
        r._router_llm = MagicMock()
        r._router_llm.call.return_value = raw
        return r

    def test_local_route_returns_local_llm(self):
        r = self._router_with_response('{"route": "local"}')
        assert r.route("implement a binary search", task_key="unknown") is r.local_llm

    def test_api_route_returns_api_llm(self):
        r = self._router_with_response('{"route": "api"}')
        assert r.route("plan the architecture", task_key="unknown") is r.api_llm

    def test_api_route_review(self):
        r = self._router_with_response('{"route": "api"}')
        assert r.route("review code for security", task_key="unknown") is r.api_llm

    def test_markdown_fence_stripped(self):
        r = self._router_with_response('```json\n{"route": "local"}\n```')
        assert r.route("write a function", task_key="unknown") is r.local_llm

    def test_whitespace_stripped(self):
        r = self._router_with_response('  {"route": "api"}  ')
        assert r.route("review this", task_key="unknown") is r.api_llm


# ---------------------------------------------------------------------------
# _keyword_fallback()
# ---------------------------------------------------------------------------

class TestKeywordFallback:

    def _router_with_error(self):
        api_llm   = MagicMock(spec=LLM)
        local_llm = MagicMock(spec=LLM)
        with patch("crew.LLM") as mock_llm_cls:
            mock_llm_cls.return_value = MagicMock(spec=LLM)
            r = SMLRouter(api_llm=api_llm, local_llm=local_llm)
        r.api_llm   = api_llm
        r.local_llm = local_llm
        r._router_llm = MagicMock()
        r._router_llm.call.side_effect = Exception("Ollama unreachable")
        return r

    def test_fallback_on_invalid_json_returns_api_llm(self):
        api_llm   = MagicMock(spec=LLM)
        local_llm = MagicMock(spec=LLM)
        with patch("crew.LLM") as mock_llm_cls:
            mock_llm_cls.return_value = MagicMock(spec=LLM)
            r = SMLRouter(api_llm=api_llm, local_llm=local_llm)
        r.api_llm   = api_llm
        r.local_llm = local_llm
        r._router_llm = MagicMock()
        r._router_llm.call.return_value = "not valid json at all"
        result = r.route("plan architecture", task_key="unknown")
        assert result is r.api_llm

    def test_fallback_on_empty_response_returns_api_llm(self):
        api_llm   = MagicMock(spec=LLM)
        local_llm = MagicMock(spec=LLM)
        with patch("crew.LLM") as mock_llm_cls:
            mock_llm_cls.return_value = MagicMock(spec=LLM)
            r = SMLRouter(api_llm=api_llm, local_llm=local_llm)
        r.api_llm   = api_llm
        r.local_llm = local_llm
        r._router_llm = MagicMock()
        r._router_llm.call.return_value = ""
        result = r.route("review this code", task_key="unknown")
        assert result is r.api_llm

    def test_fallback_coding_keywords_return_local(self):
        r = self._router_with_error()
        for kw in ["implement a cache", "write a function", "code a parser",
                   "develop an API", "build a server", "generate a class"]:
            assert r.route(kw, task_key="unknown") is r.local_llm, f"Failed for: {kw}"

    def test_fallback_api_keywords_return_api(self):
        r = self._router_with_error()
        assert r.route("plan the architecture", task_key="unknown") is r.api_llm

    def test_fallback_defaults_to_api(self):
        r = self._router_with_error()
        assert r.route("something unrelated", task_key="unknown") is r.api_llm
