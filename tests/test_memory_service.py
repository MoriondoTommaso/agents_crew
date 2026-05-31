"""
Test suite for the Graphiti Memory MCP Service (memory/service.py).

All tests use FastAPI TestClient with Graphiti fully mocked —
no Neo4j, no Ollama, no LLM required.

Run with:
    uv run pytest tests/test_memory_service.py -v
"""
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fact(uuid="abc123", fact="service.py imports fastapi", valid_at=None):
    m = MagicMock()
    m.uuid = uuid
    m.fact = fact
    m.valid_at = valid_at
    return m


def _patched_client():
    """Return a TestClient with Graphiti fully mocked."""
    import service as svc

    mock_g = MagicMock()
    mock_g.search = AsyncMock(return_value=[_make_fact()])
    mock_g.add_episode = AsyncMock(return_value=None)
    mock_g.driver = MagicMock()
    mock_g.driver.execute_query = AsyncMock(
        return_value=([{"fact": "x uses y", "valid_at": None, "group_id": "agents"}], None, None)
    )

    async def _mock_get_graphiti():
        return mock_g

    svc.app.dependency_overrides = {}
    original = svc.get_graphiti
    svc.get_graphiti = _mock_get_graphiti
    client = TestClient(svc.app, raise_server_exceptions=False)
    return client, svc, original, mock_g


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_returns_ok(self):
        client, svc, orig, _ = _patched_client()
        try:
            r = client.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "ok"
            assert "group_id" in body
            assert "llm" in body
            assert "embedder" in body
        finally:
            svc.get_graphiti = orig

    def test_tools_endpoint_lists_all_tools(self):
        client, svc, orig, _ = _patched_client()
        try:
            r = client.get("/tools")
            assert r.status_code == 200
            names = [t["name"] for t in r.json()["tools"]]
            assert "memory_recall" in names
            assert "memory_add_episode" in names
            assert "memory_get_context" in names
            assert "memory_task_log" in names
            assert "memory_snapshot" in names
        finally:
            svc.get_graphiti = orig


# ---------------------------------------------------------------------------
# memory_recall
# ---------------------------------------------------------------------------

class TestMemoryRecall:

    def test_recall_returns_facts(self):
        client, svc, orig, mock_g = _patched_client()
        try:
            r = client.post("/mcp/memory_recall",
                            json={"query": "FastAPI service", "limit": 3})
            assert r.status_code == 200
            body = r.json()
            assert body["count"] == 1
            assert body["results"][0]["fact"] == "service.py imports fastapi"
        finally:
            svc.get_graphiti = orig

    def test_recall_missing_query_returns_422(self):
        client, svc, orig, _ = _patched_client()
        try:
            r = client.post("/mcp/memory_recall", json={"limit": 3})
            assert r.status_code == 422
        finally:
            svc.get_graphiti = orig

    def test_recall_passes_group_id(self):
        client, svc, orig, mock_g = _patched_client()
        try:
            client.post("/mcp/memory_recall", json={"query": "test", "limit": 5})
            mock_g.search.assert_called_once()
            call_kwargs = mock_g.search.call_args
            assert "group_ids" in call_kwargs.kwargs
        finally:
            svc.get_graphiti = orig


# ---------------------------------------------------------------------------
# memory_add_episode
# ---------------------------------------------------------------------------

class TestAddEpisode:

    def test_add_episode_returns_ok(self):
        client, svc, orig, mock_g = _patched_client()
        try:
            r = client.post("/mcp/memory_add_episode", json={
                "name": "file:memory/service.py",
                "content": "File: memory/service.py\nFunctions: health, memory_recall",
                "source": "bootstrap",
            })
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
        finally:
            svc.get_graphiti = orig

    def test_add_episode_calls_add_episode_with_group_id(self):
        client, svc, orig, mock_g = _patched_client()
        try:
            client.post("/mcp/memory_add_episode", json={
                "name": "test-ep",
                "content": "some content",
            })
            mock_g.add_episode.assert_called_once()
            kwargs = mock_g.add_episode.call_args.kwargs
            assert kwargs["group_id"] == svc.GROUP_ID
        finally:
            svc.get_graphiti = orig

    def test_add_episode_missing_fields_returns_422(self):
        client, svc, orig, _ = _patched_client()
        try:
            r = client.post("/mcp/memory_add_episode", json={"name": "only-name"})
            assert r.status_code == 422
        finally:
            svc.get_graphiti = orig


# ---------------------------------------------------------------------------
# memory_task_log
# ---------------------------------------------------------------------------

class TestTaskLog:

    def test_task_log_returns_logged(self):
        client, svc, orig, _ = _patched_client()
        try:
            r = client.post("/mcp/memory_task_log", json={
                "task": "implement feature X",
                "status": "completed",
                "files_modified": ["memory/service.py"],
                "decisions": ["used asyncio.Lock for singleton"],
                "notes": "fixed group_id bug",
            })
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "logged"
            assert body["task_status"] == "completed"
        finally:
            svc.get_graphiti = orig

    def test_task_log_minimal_payload(self):
        client, svc, orig, _ = _patched_client()
        try:
            r = client.post("/mcp/memory_task_log", json={
                "task": "quick fix",
                "status": "completed",
            })
            assert r.status_code == 200
        finally:
            svc.get_graphiti = orig


# ---------------------------------------------------------------------------
# memory_snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:

    def test_snapshot_returns_facts(self):
        client, svc, orig, _ = _patched_client()
        try:
            r = client.get("/mcp/memory_snapshot")
            assert r.status_code == 200
            body = r.json()
            assert "total_facts" in body
            assert "facts" in body
            assert body["total_facts"] >= 1
        finally:
            svc.get_graphiti = orig


# ---------------------------------------------------------------------------
# memory_get_context
# ---------------------------------------------------------------------------

class TestGetContext:

    def test_get_context_returns_facts(self):
        client, svc, orig, _ = _patched_client()
        try:
            r = client.post("/mcp/memory_get_context", json={"entity": "service.py"})
            assert r.status_code == 200
            body = r.json()
            assert body["entity"] == "service.py"
            assert "facts" in body
        finally:
            svc.get_graphiti = orig

    def test_get_context_missing_entity_returns_422(self):
        client, svc, orig, _ = _patched_client()
        try:
            r = client.post("/mcp/memory_get_context", json={})
            assert r.status_code == 422
        finally:
            svc.get_graphiti = orig
