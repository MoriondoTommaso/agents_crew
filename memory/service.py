"""Graphiti Memory MCP Service — port 8002"""

import asyncio
import logging
import os
import traceback
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.nodes import EpisodeType
from openai import AsyncOpenAI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory")

# ── Config ──────────────────────────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI",            "bolt://neo4j:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",           "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD",       "changeme")

FREELLM_BASE   = os.getenv("FREELLM_BASE_URL",     "http://host.docker.internal:3001/v1")
FREELLM_KEY    = os.getenv("FREELLM_API_KEY",      os.getenv("OPENAI_API_KEY", "freellm"))
LLM_MODEL      = os.getenv("GRAPHITI_LLM_MODEL",   "auto")

# ── Embedder config — two modes ─────────────────────────────────────────────
# Mode A (default): Ollama local — free, no internet, needs `ollama serve`
#   GRAPHITI_EMBED_PROVIDER=ollama  (default)
#   GRAPHITI_EMBED_MODEL=nomic-embed-text
#   GRAPHITI_EMBED_DIM=768
#
# Mode B: any OpenAI-compatible cloud endpoint — no Ollama required
#   GRAPHITI_EMBED_PROVIDER=openai
#   GRAPHITI_EMBED_BASE_URL=https://api.openai.com/v1
#   GRAPHITI_EMBED_API_KEY=sk-...
#   GRAPHITI_EMBED_MODEL=text-embedding-3-small
#   GRAPHITI_EMBED_DIM=1536
#
# Works with: OpenAI, Groq, VoyageAI, OpenRouter, any /embeddings endpoint.

OLLAMA_BASE         = os.getenv("OLLAMA_BASE_URL",          "http://host.docker.internal:11434")
EMBED_PROVIDER      = os.getenv("GRAPHITI_EMBED_PROVIDER",  "ollama")  # "ollama" | "openai"

_OLLAMA_OPENAI_BASE = OLLAMA_BASE.rstrip("/") + "/v1"

# Base URL for embeddings: default to Ollama, override with GRAPHITI_EMBED_BASE_URL
EMBED_BASE_URL      = os.getenv("GRAPHITI_EMBED_BASE_URL",
                                 _OLLAMA_OPENAI_BASE if EMBED_PROVIDER == "ollama"
                                 else "https://api.openai.com/v1")

# API key for embeddings: Ollama needs no key; cloud endpoints need one
EMBED_API_KEY       = os.getenv("GRAPHITI_EMBED_API_KEY",
                                 "ollama" if EMBED_PROVIDER == "ollama"
                                 else os.getenv("OPENAI_API_KEY", ""))

# Default model + dimension per provider
_DEFAULT_MODEL = "nomic-embed-text" if EMBED_PROVIDER == "ollama" else "text-embedding-3-small"
_DEFAULT_DIM   = "768"              if EMBED_PROVIDER == "ollama" else "1536"

EMBED_MODEL  = os.getenv("GRAPHITI_EMBED_MODEL", _DEFAULT_MODEL)
EMBED_DIM    = int(os.getenv("GRAPHITI_EMBED_DIM", _DEFAULT_DIM))

GROUP_ID     = os.getenv("GRAPHITI_GROUP_ID", "agents")


# ── Embedder proxy ────────────────────────────────────────────────────────────
class _EmbedderProxy:
    """Thin wrapper that routes embedding calls to the configured endpoint."""

    def __init__(self):
        self._model = EMBED_MODEL
        self._client = AsyncOpenAI(
            api_key=EMBED_API_KEY,
            base_url=EMBED_BASE_URL,
        )
        logger.info(
            "Embedder: provider=%s model=%s base_url=%s dim=%d",
            EMBED_PROVIDER, EMBED_MODEL, EMBED_BASE_URL, EMBED_DIM,
        )

    async def create(self, **kwargs):
        kwargs["model"] = self._model
        return await self._client.embeddings.create(**kwargs)


class _PatchedOpenAIClient(OpenAIClient):
    """OpenAIClient with embedder redirected to the configured provider."""

    def get_embedder(self):
        return _EmbedderProxy()


# ── Index creation ─────────────────────────────────────────────────────────────
async def _build_indices(driver, dim: int):
    queries = [
        f"CREATE VECTOR INDEX fact_embedding IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.fact_embedding) OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}",
        f"CREATE VECTOR INDEX name_embedding IF NOT EXISTS FOR (n:Entity) ON (n.name_embedding) OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}",
        f"CREATE VECTOR INDEX community_name_embedding IF NOT EXISTS FOR (n:Community) ON (n.name_embedding) OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}",
        "CREATE FULLTEXT INDEX name_and_summary IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.summary]",
        "CREATE FULLTEXT INDEX episode_content IF NOT EXISTS FOR (n:Episodic) ON EACH [n.content]",
        "CREATE FULLTEXT INDEX name_and_fact IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON EACH [r.name, r.fact]",
        "CREATE CONSTRAINT entity_uuid IF NOT EXISTS FOR (n:Entity) REQUIRE n.uuid IS UNIQUE",
        "CREATE CONSTRAINT episodic_uuid IF NOT EXISTS FOR (n:Episodic) REQUIRE n.uuid IS UNIQUE",
        "CREATE CONSTRAINT community_uuid IF NOT EXISTS FOR (n:Community) REQUIRE n.uuid IS UNIQUE",
        "CREATE CONSTRAINT relation_uuid IF NOT EXISTS FOR ()-[r:RELATES_TO]-() REQUIRE r.uuid IS UNIQUE",
        "CREATE INDEX entity_group_id IF NOT EXISTS FOR (n:Entity) ON (n.group_id)",
        "CREATE INDEX episodic_group_id IF NOT EXISTS FOR (n:Episodic) ON (n.group_id)",
        "CREATE INDEX community_group_id IF NOT EXISTS FOR (n:Community) ON (n.group_id)",
        "CREATE INDEX relation_group_id IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.group_id)",
        "CREATE INDEX episodic_created_at IF NOT EXISTS FOR (n:Episodic) ON (n.created_at)",
        "CREATE INDEX relation_created_at IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.created_at)",
        "CREATE INDEX relation_expired_at IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.expired_at)",
    ]
    for q in queries:
        await driver.execute_query(q.strip())


# ── App + Graphiti singleton (async-safe) ─────────────────────────────────────
app = FastAPI(title="Memory MCP Service", version="2.4.0")
_graphiti: Graphiti | None = None
_graphiti_lock = asyncio.Lock()


async def get_graphiti() -> Graphiti:
    global _graphiti
    if _graphiti is None:
        async with _graphiti_lock:
            if _graphiti is None:  # double-checked locking
                logger.info(
                    "Initializing Graphiti: LLM=%s @ %s  embed=%s/%s(%dd)  group_id=%s",
                    LLM_MODEL, FREELLM_BASE, EMBED_PROVIDER, EMBED_MODEL, EMBED_DIM, GROUP_ID,
                )
                llm = _PatchedOpenAIClient(
                    config=LLMConfig(
                        model=LLM_MODEL,
                        base_url=FREELLM_BASE,
                        api_key=FREELLM_KEY,
                    )
                )
                g = Graphiti(
                    uri=NEO4J_URI,
                    user=NEO4J_USER,
                    password=NEO4J_PASSWORD,
                    llm_client=llm,
                )
                await _build_indices(g.driver, EMBED_DIM)
                _graphiti = g
    return _graphiti


# ── Pydantic models ──────────────────────────────────────────────────────────
class RecallRequest(BaseModel):
    query: str
    limit: int = 10

class EpisodeRequest(BaseModel):
    name: str
    content: str
    source: str = "agent"

class ContextRequest(BaseModel):
    entity: str

class TaskLogRequest(BaseModel):
    task: str
    status: str
    files_modified: list[str] = []
    decisions: list[str] = []
    notes: str = ""


# ── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "memory-mcp",
        "version": "2.4.0",
        "llm": f"{FREELLM_BASE} / {LLM_MODEL}",
        "embed_provider": EMBED_PROVIDER,
        "embedder": f"{EMBED_BASE_URL} / {EMBED_MODEL} ({EMBED_DIM}d)",
        "group_id": GROUP_ID,
    }


@app.get("/tools")
async def list_tools():
    return {"tools": [
        {"name": "memory_recall",      "description": "Semantic search over the knowledge graph.",       "parameters": {"query": "string", "limit": "int (default 10)"}},
        {"name": "memory_add_episode", "description": "Ingest a new episode into the graph.",            "parameters": {"name": "string", "content": "string", "source": "string"}},
        {"name": "memory_get_context", "description": "Retrieve all graph facts for a specific entity.", "parameters": {"entity": "string"}},
        {"name": "memory_task_log",    "description": "Log a completed or failed task.",                 "parameters": {"task": "string", "status": "string", "files_modified": "list", "decisions": "list", "notes": "string"}},
        {"name": "memory_snapshot",    "description": "Export raw facts from the knowledge graph (debug).", "parameters": {}},
    ]}


@app.post("/mcp/memory_recall")
async def memory_recall(req: RecallRequest):
    g = await get_graphiti()
    try:
        results = await g.search(req.query, num_results=req.limit, group_ids=[GROUP_ID])
        facts = [
            {"uuid": str(r.uuid), "fact": r.fact, "valid_at": r.valid_at.isoformat() if r.valid_at else None}
            for r in results
        ]
        return {"query": req.query, "results": facts, "count": len(facts)}
    except Exception as e:
        logger.error("memory_recall error: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mcp/memory_add_episode")
async def memory_add_episode(req: EpisodeRequest):
    g = await get_graphiti()
    try:
        await g.add_episode(
            name=req.name,
            episode_body=req.content,
            source=EpisodeType.text,
            source_description=req.source,
            reference_time=datetime.now(UTC),
            group_id=GROUP_ID,
        )
        return {"status": "ok", "episode": req.name}
    except Exception:
        logger.error("memory_add_episode error:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=traceback.format_exc())


@app.post("/mcp/memory_get_context")
async def memory_get_context(req: ContextRequest):
    g = await get_graphiti()
    try:
        results = await g.search(
            f"context and relationships for {req.entity}",
            num_results=20,
            group_ids=[GROUP_ID],
        )
        return {
            "entity": req.entity,
            "facts": [{"fact": r.fact, "valid_at": r.valid_at.isoformat() if r.valid_at else None} for r in results],
        }
    except Exception as e:
        logger.error("memory_get_context error: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mcp/memory_task_log")
async def memory_task_log(req: TaskLogRequest):
    g = await get_graphiti()
    content = "\n".join(filter(None, [
        f"Task: {req.task}",
        f"Status: {req.status}",
        f"Files modified: {', '.join(req.files_modified) or 'none'}",
        f"Decisions: {'; '.join(req.decisions) or 'none'}",
        f"Notes: {req.notes}" if req.notes else None,
    ]))
    try:
        await g.add_episode(
            name=f"task:{req.task[:60]}",
            episode_body=content,
            source=EpisodeType.text,
            source_description="agent_task_log",
            reference_time=datetime.now(UTC),
            group_id=GROUP_ID,
        )
        return {"status": "logged", "task": req.task, "task_status": req.status}
    except Exception as e:
        logger.error("memory_task_log error: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/mcp/memory_snapshot")
async def memory_snapshot():
    """Return raw facts from Neo4j directly (bypasses Graphiti search quirks)."""
    g = await get_graphiti()
    try:
        records, _, _ = await g.driver.execute_query(
            """
            MATCH ()-[r:RELATES_TO]->()
            RETURN r.fact AS fact, r.valid_at AS valid_at, r.group_id AS group_id
            ORDER BY r.created_at DESC
            LIMIT 200
            """
        )
        facts = [
            {"fact": r["fact"], "valid_at": str(r["valid_at"]) if r["valid_at"] else None, "group_id": r["group_id"]}
            for r in records
        ]
        return {"total_facts": len(facts), "facts": facts}
    except Exception as e:
        logger.error("memory_snapshot error: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))
