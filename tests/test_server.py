"""
Test suite for FastAPI server (v0.3 — single-pass pipeline).

All tests mock crew_instance so no real LLM or Ollama is needed.

Run with:
    uv run pytest tests/test_server.py -v
"""
import sys
import os
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
import server as srv


# ---------------------------------------------------------------------------
# Shared fixture: TestClient with mocked crew_instance
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    mock_crew = MagicMock()
    # run() is called in a thread via asyncio.to_thread — must return a plain string
    mock_crew.run.return_value = "def binary_search(): pass"
    # plan_only / code_only use ad-hoc Crew instances — mock kickoff on them
    mock_inner_crew = MagicMock()
    mock_inner_crew.kickoff.return_value = "mocked output"
    mock_crew.senior_architect.return_value = MagicMock()
    mock_crew.senior_developer.return_value = MagicMock()
    mock_crew.planning_task.return_value = MagicMock()
    mock_crew.coding_task.return_value = MagicMock()

    with patch("server.crew_instance", mock_crew), \
         patch("server.CodingAgencyCrew", return_value=mock_crew):
        with TestClient(srv.app, raise_server_exceptions=False) as c:
            # Override crew_instance directly (lifespan may reset it)
            srv.crew_instance = mock_crew
            yield c


# ---------------------------------------------------------------------------
# Health & info
# ---------------------------------------------------------------------------

class TestHealthAndInfo:

    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_api_models(self, client):
        r = client.get("/api/models")
        assert r.status_code == 200
        data = r.json()
        assert "router" in data
        assert "planner" in data
        assert "coder" in data

    def test_oai_models_list(self, client):
        r = client.get("/v1/models")
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert "coding-agency" in ids
        assert "coding-code" in ids
        assert "coding-plan" in ids
        # coding-review was removed in v0.3 (delegated to harness)
        assert "coding-review" not in ids


# ---------------------------------------------------------------------------
# Native endpoints
# ---------------------------------------------------------------------------

class TestNativeEndpoints:

    def test_run_pipeline(self, client):
        r = client.post("/api/run", json={
            "user_request": "write a binary search function",
            "language": "Python",
        })
        assert r.status_code == 200
        body = r.json()
        assert "result" in body
        assert "request_id" in body
        assert "elapsed_sec" in body

    def test_run_default_language_is_python(self, client):
        r = client.post("/api/run", json={"user_request": "write a sort function"})
        assert r.status_code == 200
        assert "result" in r.json()

    def test_plan_endpoint(self, client):
        with patch("server.Crew") as MockCrew:
            mock_c = MagicMock()
            mock_c.kickoff.return_value = "plan output"
            MockCrew.return_value = mock_c
            r = client.post("/api/plan", json={
                "user_request": "design a cache system",
                "language": "Python",
            })
        assert r.status_code == 200
        assert "result" in r.json()

    def test_code_endpoint(self, client):
        with patch("server.Crew") as MockCrew:
            mock_c = MagicMock()
            mock_c.kickoff.return_value = "code output"
            MockCrew.return_value = mock_c
            r = client.post("/api/code", json={
                "user_request": "implement a stack",
                "language": "Python",
            })
        assert r.status_code == 200
        assert "result" in r.json()

    def test_review_endpoint_removed(self, client):
        # /api/review was removed in v0.3 — review is delegated to the harness
        r = client.post("/api/review", json={
            "user_request": "review this code",
            "language": "Python",
        })
        assert r.status_code == 404

    def test_missing_user_request_returns_422(self, client):
        r = client.post("/api/run", json={"language": "Python"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoint
# ---------------------------------------------------------------------------

class TestOAIEndpoint:

    def _chat(self, client, model="coding-agency", content="write a binary search", stream=False):
        return client.post("/v1/chat/completions", json={
            "model":    model,
            "messages": [{"role": "user", "content": content}],
            "stream":   stream,
        })

    def test_coding_agency_model(self, client):
        r = self._chat(client, model="coding-agency")
        assert r.status_code == 200

    def test_coding_code_model(self, client):
        with patch("server.Crew") as MockCrew:
            mock_c = MagicMock()
            mock_c.kickoff.return_value = "code output"
            MockCrew.return_value = mock_c
            r = self._chat(client, model="coding-code")
        assert r.status_code == 200

    def test_coding_plan_model(self, client):
        with patch("server.Crew") as MockCrew:
            mock_c = MagicMock()
            mock_c.kickoff.return_value = "plan output"
            MockCrew.return_value = mock_c
            r = self._chat(client, model="coding-plan")
        assert r.status_code == 200

    def test_coding_review_model_falls_back_to_full_pipeline(self, client):
        # coding-review no longer exists — unknown models fall back to full pipeline
        r = self._chat(client, model="coding-review")
        assert r.status_code == 200

    def test_unknown_model_falls_back_to_full_pipeline(self, client):
        r = self._chat(client, model="some-random-model")
        assert r.status_code == 200

    def test_response_has_oai_fields(self, client):
        r = self._chat(client)
        assert r.status_code == 200
        body = r.json()
        assert "id" in body
        assert "choices" in body
        assert body["choices"][0]["message"]["role"] == "assistant"

    def test_language_detection_python(self, client):
        r = self._chat(client, content="write a Python class for a stack")
        assert r.status_code == 200

    def test_language_detection_typescript(self, client):
        r = self._chat(client, content="write a TypeScript interface for a user")
        assert r.status_code == 200

    def test_streaming_returns_event_stream(self, client):
        r = self._chat(client, stream=True)
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]

    def test_empty_messages_returns_422_or_500(self, client):
        r = client.post("/v1/chat/completions", json={
            "model":    "coding-agency",
            "messages": [],
        })
        assert r.status_code in (422, 500)
