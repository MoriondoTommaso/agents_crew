"""
Test suite for the Graphiti Memory MCP Service (memory/service.py).

All heavy dependencies (graphiti_core, neo4j, openai) are mocked at the
sys.modules level BEFORE service.py is imported, so no external services
or optional packages are required to run the tests.

Run with:
    uv run pytest tests/test_memory_service.py -v
or:
    pytest tests/test_memory_service.py -v
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Stub out all heavy deps BEFORE importing service
# ---------------------------------------------------------------------------

def _stub_graphiti_modules():
    """Inject lightweight stubs so service.py can be imported without
    installing graphiti_core, neo4j, openai, or python-dotenv.
    """
    dotenv_mod = MagicMock()
    dotenv_mod.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_mod)

    openai_mod = MagicMock()
    openai_mod.AsyncOpenAI = MagicMock
    sys.modules.setdefault("openai", openai_mod)

    for mod in [
        "graphiti_core",
        "graphiti_core.graphiti",
        "graphiti_core.nodes",
        "graphiti_core.llm_client",
        "graphiti_core.llm_client.openai_client",
        "graphiti_core.llm_client.config",
    ]:
        sys.modules.setdefault(mod, MagicMock())

    sys.modules["graphiti_core"].Graphiti = MagicMock
    sys.modules["graphiti_core.nodes"].EpisodeType = MagicMock()
    sys.modules["graphiti_core.nodes"].EpisodeType.text = "text"
    sys.modules["graphiti_core.llm_client.openai_client"].OpenAIClient = MagicMock
    sys.modules["graphiti_core.llm_client.config"].LLMConfig = MagicMock


_stub_graphiti_modules()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory"))

import pytest  # noqa: E402
import service as svc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fact(uuid="abc123", fact="service.py imports fastapi", valid_at=None):
    m = MagicMock()
    m.uuid = uuid
    m.fact = fact
    m.valid_at = valid_at
    return m


def _mock_graphiti():
    mock_g = MagicMock()
    mock_g.search = AsyncMock(return_value=[_make_fact()])
    mock_g.add_episode = AsyncMock(return_value=None)
    mock_g.driver = MagicMock()
    mock_g.driver.execute_query = AsyncMock(
        return_value=(
            [{"fact": "x uses y", "valid_at": None, "group_id": "agents"}],
            None,
            None,
        )
    )
    return mock_g


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_graphiti_singleton():
    """Reset the module-level singleton and replace get_graphiti before each test."""
    original_singleton = svc._graphiti
    original_fn = svc.get_graphiti
    mock_g = _mock_graphiti()

    async def _mock_get():
        return mock_g

    svc._graphiti = None
    svc.get_graphiti = _mock_get
    yield mock_g
    svc._graphiti = original_singleton
    svc.get_graphiti = original_fn


@pytest.fixture()
def client():
    return TestClient(svc.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "group_id" in body
        assert "llm" in body
        assert "embedder" in body

    def test_tools_endpoint_lists_all_tools(self, client):
        r = client.get("/tools")
        assert r.status_code == 200
        names = [t["name"] for t in r.json()["tools"]]
        assert "memory_recall" in names
        assert "memory_add_episode" in names
        assert "memory_get_context" in names
        assert "memory_task_log" in names
        assert "memory_snapshot" in names


# ---------------------------------------------------------------------------
# memory_recall
# ---------------------------------------------------------------------------

class TestMemoryRecall:

    def test_recall_returns_facts(self, client, reset_graphiti_singleton):
        r = client.post("/mcp/memory_recall",
                        json={"query": "FastAPI service", "limit": 3})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["results"][0]["fact"] == "service.py imports fastapi"

    def test_recall_missing_query_returns_422(self, client):
        r = client.post("/mcp/memory_recall", json={"limit": 3})
        assert r.status_code == 422

    def test_recall_passes_group_id(self, client, reset_graphiti_singleton):
        client.post("/mcp/memory_recall", json={"query": "test", "limit": 5})
        mock_g = reset_graphiti_singleton
        mock_g.search.assert_called_once()
        assert "group_ids" in mock_g.search.call_args.kwargs


# ---------------------------------------------------------------------------
# memory_add_episode
# ---------------------------------------------------------------------------

class TestAddEpisode:

    def test_add_episode_returns_ok(self, client):
        r = client.post("/mcp/memory_add_episode", json={
            "name": "file:memory/service.py",
            "content": "File: memory/service.py\nFunctions: health, memory_recall",
            "source": "bootstrap",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_add_episode_calls_add_episode_with_group_id(self, client, reset_graphiti_singleton):
        client.post("/mcp/memory_add_episode", json={
            "name": "test-ep",
            "content": "some content",
        })
        mock_g = reset_graphiti_singleton
        mock_g.add_episode.assert_called_once()
        assert mock_g.add_episode.call_args.kwargs["group_id"] == svc.GROUP_ID

    def test_add_episode_missing_fields_returns_422(self, client):
        r = client.post("/mcp/memory_add_episode", json={"name": "only-name"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# memory_task_log
# ---------------------------------------------------------------------------

class TestTaskLog:

    def test_task_log_returns_logged(self, client):
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

    def test_task_log_minimal_payload(self, client):
        r = client.post("/mcp/memory_task_log", json={
            "task": "quick fix",
            "status": "completed",
        })
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# memory_snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:

    def test_snapshot_returns_facts(self, client):
        r = client.get("/mcp/memory_snapshot")
        assert r.status_code == 200
        body = r.json()
        assert "total_facts" in body
        assert "facts" in body
        assert body["total_facts"] >= 1


# ---------------------------------------------------------------------------
# memory_get_context
# ---------------------------------------------------------------------------

class TestGetContext:

    def test_get_context_returns_facts(self, client):
        r = client.post("/mcp/memory_get_context", json={"entity": "service.py"})
        assert r.status_code == 200
        body = r.json()
        assert body["entity"] == "service.py"
        assert "facts" in body

    def test_get_context_missing_entity_returns_422(self, client):
        r = client.post("/mcp/memory_get_context", json={})
        assert r.status_code == 422
