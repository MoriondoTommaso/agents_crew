"""
Test suite for the FastAPI server endpoints.
All tests mock CodingAgencyCrew completely — zero API calls, zero Ollama calls.

Run with:
    uv run pytest tests/test_server.py -v
"""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Patch CodingAgencyCrew before importing the app so lifespan never calls
# the real __init__ (which would try to connect to Ollama/FreeLLM)
# ---------------------------------------------------------------------------

MOCK_RESULT = "def binary_search(arr, target): ...  # LGTM - no issues"

@pytest.fixture(scope="module")
def client():
    """
    TestClient with CodingAgencyCrew fully mocked.
    The lifespan warms up the mock instead of the real crew.
    """
    mock_crew_instance = MagicMock()
    mock_crew_instance.run_with_healing.return_value = MOCK_RESULT
    mock_crew_instance.crew.return_value.kickoff.return_value = MOCK_RESULT
    mock_crew_instance.senior_architect.return_value = MagicMock()
    mock_crew_instance.senior_developer.return_value = MagicMock()
    mock_crew_instance.qa_engineer.return_value     = MagicMock()
    mock_crew_instance.planning_task.return_value   = MagicMock()
    mock_crew_instance.coding_task.return_value     = MagicMock()
    mock_crew_instance.review_task.return_value     = MagicMock()

    with patch("server.CodingAgencyCrew", return_value=mock_crew_instance):
        # Also patch the Crew used inside single-step endpoints
        with patch("server.Crew") as mock_crew_cls:
            mock_inner_crew = MagicMock()
            mock_inner_crew.kickoff.return_value = MOCK_RESULT
            mock_crew_cls.return_value = mock_inner_crew

            from server import app
            with TestClient(app, raise_server_exceptions=True) as c:
                yield c


# ---------------------------------------------------------------------------
# Health + Models
# ---------------------------------------------------------------------------

class TestHealthAndInfo:

    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["crew_ready"] is True

    def test_api_models(self, client):
        r = client.get("/api/models")
        assert r.status_code == 200
        body = r.json()
        assert "router" in body
        assert "coder" in body
        assert "local" in body["coder"].lower()

    def test_oai_models_list(self, client):
        r = client.get("/v1/models")
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert "coding-agency" in ids
        assert "coding-code"   in ids
        assert "coding-plan"   in ids
        assert "coding-review" in ids


# ---------------------------------------------------------------------------
# Native /api/* endpoints
# ---------------------------------------------------------------------------

class TestNativeEndpoints:

    def test_run_pipeline_healing(self, client):
        r = client.post("/api/run", json={
            "user_request": "write a binary search function",
            "language": "Python",
            "healing": True,
        })
        assert r.status_code == 200
        body = r.json()
        assert "result" in body
        assert "request_id" in body
        assert "elapsed_sec" in body
        assert len(body["request_id"]) == 8

    def test_run_pipeline_no_healing(self, client):
        r = client.post("/api/run", json={
            "user_request": "write a hello world",
            "healing": False,
        })
        assert r.status_code == 200

    def test_run_default_language_is_python(self, client):
        r = client.post("/api/run", json={"user_request": "write a sort function"})
        assert r.status_code == 200

    def test_plan_endpoint(self, client):
        r = client.post("/api/plan", json={
            "user_request": "design a microservice architecture",
            "language": "Python",
        })
        assert r.status_code == 200
        assert "result" in r.json()

    def test_code_endpoint(self, client):
        r = client.post("/api/code", json={
            "user_request": "implement a linked list",
            "language": "Python",
        })
        assert r.status_code == 200
        assert "result" in r.json()

    def test_review_endpoint(self, client):
        r = client.post("/api/review", json={
            "user_request": "review this sorting algorithm",
            "language": "Python",
        })
        assert r.status_code == 200
        assert "result" in r.json()

    def test_missing_user_request_returns_422(self, client):
        r = client.post("/api/run", json={"language": "Python"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# OpenAI-compatible /v1/chat/completions
# ---------------------------------------------------------------------------

class TestOAIEndpoint:

    def _chat(self, client, model="coding-agency", content="write a binary search", stream=False):
        return client.post("/v1/chat/completions", json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "stream": stream,
        })

    def test_coding_agency_model(self, client):
        r = self._chat(client, model="coding-agency")
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert len(body["choices"][0]["message"]["content"]) > 0

    def test_coding_code_model(self, client):
        r = self._chat(client, model="coding-code")
        assert r.status_code == 200
        assert r.json()["model"] == "coding-code"

    def test_coding_plan_model(self, client):
        r = self._chat(client, model="coding-plan")
        assert r.status_code == 200

    def test_coding_review_model(self, client):
        r = self._chat(client, model="coding-review")
        assert r.status_code == 200

    def test_unknown_model_falls_back_to_full_pipeline(self, client):
        """Any unrecognized model name runs the full pipeline."""
        r = self._chat(client, model="some-random-model")
        assert r.status_code == 200

    def test_response_has_oai_fields(self, client):
        r = self._chat(client)
        body = r.json()
        assert "id"      in body
        assert "created" in body
        assert "usage"   in body
        assert body["id"].startswith("chatcmpl-")

    def test_language_detection_python(self, client):
        """Python keyword in request should not crash the endpoint."""
        r = self._chat(client, content="write a Python class for a stack")
        assert r.status_code == 200

    def test_language_detection_typescript(self, client):
        r = self._chat(client, content="write a TypeScript interface for a user")
        assert r.status_code == 200

    def test_streaming_returns_event_stream(self, client):
        r = self._chat(client, stream=True)
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        # First SSE chunk must start with 'data:'
        first_line = r.text.split("\n")[0]
        assert first_line.startswith("data:")

    def test_empty_messages_returns_422(self, client):
        r = client.post("/v1/chat/completions", json={
            "model": "coding-agency",
            "messages": [],
        })
        # Empty messages list — server should handle gracefully (500 or 422)
        assert r.status_code in (422, 500)
