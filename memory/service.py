"""Graphiti Memory MCP Service — FastMCP (SSE), port 8002.

Exposes exactly 4 tools:
  - memory_recall
  - memory_add_episode
  - memory_get_context
  - memory_task_log
"""

import json
import logging
import os
import traceback
from datetime import UTC, datetime

from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.nodes import EpisodeType
from mcp.server.fastmcp import FastMCP
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory")

# ── Config ────────────────────────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")

FREELLM_BASE = os.getenv("FREELLM_BASE_URL", "http://host.docker.internal:3001/v1")
FREELLM_KEY = os.getenv("FREELLM_API_KEY", os.getenv("OPENAI_API_KEY", "freellm"))
LLM_MODEL = os.getenv("GRAPHITI_LLM_MODEL", "auto")

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
EMBED_PROVIDER = os.getenv("GRAPHITI_EMBED_PROVIDER", "ollama")
_OLLAMA_OPENAI_BASE = OLLAMA_BASE.rstrip("/") + "/v1"
EMBED_BASE_URL = os.getenv(
    "GRAPHITI_EMBED_BASE_URL",
    _OLLAMA_OPENAI_BASE if EMBED_PROVIDER == "ollama" else "https://api.openai.com/v1",
)
EMBED_API_KEY = os.getenv(
    "GRAPHITI_EMBED_API_KEY",
    "ollama" if EMBED_PROVIDER == "ollama" else os.getenv("OPENAI_API_KEY", ""),
)
_DEFAULT_MODEL = "nomic-embed-text" if EMBED_PROVIDER == "ollama" else "text-embedding-3-small"
_DEFAULT_DIM = "768" if EMBED_PROVIDER == "ollama" else "1536"
EMBED_MODEL = os.getenv("GRAPHITI_EMBED_MODEL", _DEFAULT_MODEL)
EMBED_DIM = int(os.getenv("GRAPHITI_EMBED_DIM", _DEFAULT_DIM))
GROUP_ID = os.getenv("GRAPHITI_GROUP_ID", "agents")


# ── Embedder proxy ────────────────────────────────────────────────────────────
class _EmbedderProxy:
    def __init__(self):
        self._model = EMBED_MODEL
        self._client = AsyncOpenAI(api_key=EMBED_API_KEY, base_url=EMBED_BASE_URL)
        logger.info(
            "Embedder: provider=%s model=%s base_url=%s dim=%d",
            EMBED_PROVIDER, EMBED_MODEL, EMBED_BASE_URL, EMBED_DIM,
        )

    async def create(self, **kwargs):
        kwargs["model"] = self._model
        return await self._client.embeddings.create(**kwargs)


class _PatchedOpenAIClient(OpenAIClient):
    def get_embedder(self):
        return _EmbedderProxy()


# ── Index creation ────────────────────────────────────────────────────────────
async def _build_indices(driver, dim: int):
    queries = [
        f"CREATE VECTOR INDEX fact_embedding IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.fact_embedding) OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}",
        f"CREATE VECTOR INDEX name_embedding IF NOT EXISTS FOR (n:Entity) ON (n.name_embedding) OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}",
        f"CREATE VECTOR INDEX community_name_embedding IF NOT EXISTS FOR (n:Community) ON (n.name_embedding) OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}",
        "CREATE FULLTEXT INDEX name_and_summary IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.summary]",
        "CREATE FULLTEXT INDEX episode_content IF NOT EXISTS FOR (n:Episodic) ON EACH [n.content]",
        "CREATE FULLTEXT INDEX name_and_fact IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON EACH [r.name, r.fact]",
    ]
    for q in queries:
        await driver.execute_query(q.strip())


# ── Graphiti singleton ────────────────────────────────────────────────────────
_graphiti: Graphiti | None = None

async def get_graphiti() -> Graphiti:
    global _graphiti
    if _graphiti is None:
        logger.info(
            "Initializing Graphiti: LLM=%s @ %s  embed=%s/%s(%dd)  group_id=%s",
            LLM_MODEL, FREELLM_BASE, EMBED_PROVIDER, EMBED_MODEL, EMBED_DIM, GROUP_ID,
        )
        llm = _PatchedOpenAIClient(config=LLMConfig(
            model=LLM_MODEL, base_url=FREELLM_BASE, api_key=FREELLM_KEY,
        ))
        g = Graphiti(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD, llm_client=llm)
        await _build_indices(g.driver, EMBED_DIM)
        _graphiti = g
    return _graphiti


# ── FastMCP server ────────────────────────────────────────────────────────────
mcp = FastMCP("memory")


@mcp.tool()
async def memory_recall(query: str, limit: int = 10) -> str:
    """Semantic search over the knowledge graph. Call this FIRST on every task."""
    g = await get_graphiti()
    try:
        results = await g.search(
            query,
            num_results=limit,
            group_ids=[GROUP_ID],
        )
        facts = [
            {
                "uuid": str(r.uuid),
                "fact": r.fact,
                "valid_at": r.valid_at.isoformat() if r.valid_at else None,
            }
            for r in results
        ]
        return json.dumps(facts, indent=2)
    except Exception:
        return f"Error: {traceback.format_exc()}"


@mcp.tool()
async def memory_add_episode(name: str, content: str, source: str = "agent") -> str:
    """Ingest a new episode into the knowledge graph."""
    g = await get_graphiti()
    try:
        await g.add_episode(
            name=name,
            episode_body=content,
            source=EpisodeType.text,
            source_description=source,
            reference_time=datetime.now(UTC),
            group_id=GROUP_ID,
        )
        return json.dumps({"status": "ok", "episode": name})
    except Exception:
        return f"Error: {traceback.format_exc()}"


@mcp.tool()
async def memory_get_context(entity: str) -> str:
    """Retrieve all graph facts for a specific entity."""
    g = await get_graphiti()
    try:
        results = await g.search(
            f"context and relationships for {entity}",
            num_results=20,
            group_ids=[GROUP_ID],
        )
        facts = [
            {
                "uuid": str(r.uuid),
                "fact": r.fact,
                "valid_at": r.valid_at.isoformat() if r.valid_at else None,
            }
            for r in results
        ]
        return json.dumps(facts, indent=2)
    except Exception:
        return f"Error: {traceback.format_exc()}"


@mcp.tool()
async def memory_task_log(
    task: str,
    status: str,
    files_modified: list[str] | None = None,
    decisions: list[str] | None = None,
    notes: str = "",
) -> str:
    """Log a completed or failed task into the knowledge graph.
    Call this LAST on every task.
    """
    g = await get_graphiti()
    content = "\n".join(
        filter(None, [
            f"Task: {task}",
            f"Status: {status}",
            f"Files modified: {', '.join(files_modified or []) or 'none'}",
            f"Decisions: {'; '.join(decisions or []) or 'none'}",
            f"Notes: {notes}" if notes else None,
        ])
    )
    try:
        await g.add_episode(
            name=f"task:{task[:60]}",
            episode_body=content,
            source=EpisodeType.text,
            source_description="agent_task_log",
            reference_time=datetime.now(UTC),
            group_id=GROUP_ID,
        )
        return json.dumps({"status": "logged", "task": task, "result": status})
    except Exception:
        return f"Error: {traceback.format_exc()}"


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8002)
