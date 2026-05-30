"""Graphiti Memory MCP Service — port 8002

graphiti-core 0.3.0 verified:
  Graphiti.__init__(uri, user, password, llm_client=None)
  LLMConfig(model, base_url, api_key)
  OpenAIClient.get_embedder() -> self.client.embeddings
  embedding call: embedder.create(input=[text], model=...) <- intercepted
  build_indices_and_constraints() hardcodes 1024d          <- fully replaced

Architecture:
  LLM (entity extraction) -> FreeLLMAPI on host :3001  (fast, cloud model)
  Embedder (vectors)      -> Ollama nomic-embed-text    (local 768d)
  Graph DB                -> Neo4j 5.26                 (local)
"""

import os
from datetime import datetime, timezone

from openai import AsyncOpenAI
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.llm_client.config import LLMConfig

# ── Config ──────────────────────────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI",            "bolt://neo4j:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",           "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD",       "changeme")

# FreeLLMAPI — LLM for entity extraction (runs on host)
FREELLM_BASE   = os.getenv("FREELLM_BASE_URL",     "http://host.docker.internal:3001/v1")
FREELLM_KEY    = os.getenv("FREELLM_API_KEY",      os.getenv("OPENAI_API_KEY", "freellm"))
LLM_MODEL      = os.getenv("GRAPHITI_LLM_MODEL",   "claude-3-5-haiku")  # any model FreeLLM exposes

# Ollama — embedder only (local, no cloud needed)
OLLAMA_BASE    = os.getenv("OLLAMA_BASE_URL",       "http://host.docker.internal:11434")
EMBED_MODEL    = os.getenv("GRAPHITI_EMBED_MODEL",  "nomic-embed-text")
EMBED_DIM      = int(os.getenv("GRAPHITI_EMBED_DIM", "768"))

OLLAMA_OPENAI_BASE = OLLAMA_BASE.rstrip("/") + "/v1"


# ── Embedder proxy ─────────────────────────────────────────────────────────────
class _EmbedderProxy:
    """Intercepts embedder.create() and forces model=EMBED_MODEL via Ollama.

    Graphiti 0.3.0 hardcodes model='text-embedding-3-small' in
    nodes.py, edges.py, search.py, utils.py. We override it every call
    and forward to a separate Ollama AsyncOpenAI client.
    """
    def __init__(self, model: str, base_url: str):
        self._model = model
        self._client = AsyncOpenAI(api_key="ollama", base_url=base_url)

    async def create(self, **kwargs):
        kwargs["model"] = self._model
        return await self._client.embeddings.create(**kwargs)


class _PatchedOpenAIClient(OpenAIClient):
    """OpenAIClient that points LLM at FreeLLMAPI but embeddings at Ollama."""
    def get_embedder(self):
        return _EmbedderProxy(EMBED_MODEL, OLLAMA_OPENAI_BASE)


# ── Index creation (full replacement for graphiti's build_indices_and_constraints) ──
async def _build_indices(driver, dim: int):
    """Complete Neo4j index setup for graphiti 0.3.0 with correct vector dimensions."""
    queries = [
        # Vector indices
        f"""
        CREATE VECTOR INDEX fact_embedding IF NOT EXISTS
        FOR ()-[r:RELATES_TO]-() ON (r.fact_embedding)
        OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}
        """,
        f"""
        CREATE VECTOR INDEX name_embedding IF NOT EXISTS
        FOR (n:Entity) ON (n.name_embedding)
        OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}
        """,
        f"""
        CREATE VECTOR INDEX community_name_embedding IF NOT EXISTS
        FOR (n:Community) ON (n.name_embedding)
        OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}
        """,
        # Fulltext indices
        "CREATE FULLTEXT INDEX name_and_summary IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.summary]",
        "CREATE FULLTEXT INDEX episode_content IF NOT EXISTS FOR (n:Episodic) ON EACH [n.content]",
        "CREATE FULLTEXT INDEX name_and_fact IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON EACH [r.name, r.fact]",
        # Uniqueness constraints
        "CREATE CONSTRAINT entity_uuid IF NOT EXISTS FOR (n:Entity) REQUIRE n.uuid IS UNIQUE",
        "CREATE CONSTRAINT episodic_uuid IF NOT EXISTS FOR (n:Episodic) REQUIRE n.uuid IS UNIQUE",
        "CREATE CONSTRAINT community_uuid IF NOT EXISTS FOR (n:Community) REQUIRE n.uuid IS UNIQUE",
        "CREATE CONSTRAINT relation_uuid IF NOT EXISTS FOR ()-[r:RELATES_TO]-() REQUIRE r.uuid IS UNIQUE",
        # Property indices
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


# ── App + Graphiti singleton ────────────────────────────────────────────────────
app = FastAPI(title="Memory MCP Service", version="2.0.0")
_graphiti: Graphiti | None = None


async def get_graphiti() -> Graphiti:
    global _graphiti
    if _graphiti is None:
        # LLM -> FreeLLMAPI (fast cloud model for entity extraction)
        llm = _PatchedOpenAIClient(
            config=LLMConfig(
                model=LLM_MODEL,
                base_url=FREELLM_BASE,
                api_key=FREELLM_KEY,
            )
        )
        _graphiti = Graphiti(
            uri=NEO4J_URI,
            user=NEO4J_USER,
            password=NEO4J_PASSWORD,
            llm_client=llm,
        )
        await _build_indices(_graphiti.driver, EMBED_DIM)
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
    return {"status": "ok", "service": "memory-mcp",
            "llm": f"{FREELLM_BASE} / {LLM_MODEL}",
            "embedder": f"{OLLAMA_BASE} / {EMBED_MODEL} ({EMBED_DIM}d)"}


@app.get("/tools")
async def list_tools():
    return {"tools": [
        {"name": "memory_recall",      "description": "Semantic search over the knowledge graph.",       "parameters": {"query": "string", "limit": "int (default 10)"}},
        {"name": "memory_add_episode", "description": "Ingest a new episode into the graph.",            "parameters": {"name": "string", "content": "string", "source": "string"}},
        {"name": "memory_get_context", "description": "Retrieve all graph facts for a specific entity.", "parameters": {"entity": "string"}},
        {"name": "memory_task_log",    "description": "Log a completed or failed task.",                 "parameters": {"task": "string", "status": "string", "files_modified": "list", "decisions": "list", "notes": "string"}},
        {"name": "memory_snapshot",    "description": "Export the full knowledge graph (debug).",        "parameters": {}},
    ]}


@app.post("/mcp/memory_recall")
async def memory_recall(req: RecallRequest):
    g = await get_graphiti()
    try:
        results = await g.search(req.query, num_results=req.limit)
        facts = [
            {"uuid": str(r.uuid), "fact": r.fact, "valid_at": r.valid_at.isoformat() if r.valid_at else None}
            for r in results
        ]
        return {"query": req.query, "results": facts, "count": len(facts)}
    except Exception as e:
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
            reference_time=datetime.now(timezone.utc),
        )
        return {"status": "ok", "episode": req.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mcp/memory_get_context")
async def memory_get_context(req: ContextRequest):
    g = await get_graphiti()
    try:
        results = await g.search(f"context and relationships for {req.entity}", num_results=20)
        return {
            "entity": req.entity,
            "facts": [{"fact": r.fact, "valid_at": r.valid_at.isoformat() if r.valid_at else None} for r in results],
        }
    except Exception as e:
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
            reference_time=datetime.now(timezone.utc),
        )
        return {"status": "logged", "task": req.task, "task_status": req.status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/mcp/memory_snapshot")
async def memory_snapshot():
    g = await get_graphiti()
    try:
        results = await g.search("*", num_results=200)
        return {
            "total_facts": len(results),
            "facts": [{"fact": r.fact, "valid_at": r.valid_at.isoformat() if r.valid_at else None} for r in results],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
