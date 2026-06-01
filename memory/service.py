"""Graphiti Memory MCP Service — port 8002

Exposes:
  - REST endpoints  : /health  /tools  /mcp/*
  - MCP SSE server  : /sse   (OpenCode / any MCP client connects here)
"""

import asyncio
import logging
import os
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.nodes import EpisodeType
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from openai import AsyncOpenAI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory")

# ── Config ────────────────────────────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI",            "bolt://neo4j:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",           "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD",       "changeme")

FREELLM_BASE   = os.getenv("FREELLM_BASE_URL",     "http://host.docker.internal:3001/v1")
FREELLM_KEY    = os.getenv("FREELLM_API_KEY",      os.getenv("OPENAI_API_KEY", "freellm"))
LLM_MODEL      = os.getenv("GRAPHITI_LLM_MODEL",   "auto")

OLLAMA_BASE         = os.getenv("OLLAMA_BASE_URL",         "http://host.docker.internal:11434")
EMBED_PROVIDER      = os.getenv("GRAPHITI_EMBED_PROVIDER", "ollama")
_OLLAMA_OPENAI_BASE = OLLAMA_BASE.rstrip("/") + "/v1"
EMBED_BASE_URL      = os.getenv("GRAPHITI_EMBED_BASE_URL",
                                 _OLLAMA_OPENAI_BASE if EMBED_PROVIDER == "ollama"
                                 else "https://api.openai.com/v1")
EMBED_API_KEY       = os.getenv("GRAPHITI_EMBED_API_KEY",
                                 "ollama" if EMBED_PROVIDER == "ollama"
                                 else os.getenv("OPENAI_API_KEY", ""))
_DEFAULT_MODEL = "nomic-embed-text" if EMBED_PROVIDER == "ollama" else "text-embedding-3-small"
_DEFAULT_DIM   = "768"              if EMBED_PROVIDER == "ollama" else "1536"
EMBED_MODEL  = os.getenv("GRAPHITI_EMBED_MODEL", _DEFAULT_MODEL)
EMBED_DIM    = int(os.getenv("GRAPHITI_EMBED_DIM", _DEFAULT_DIM))
GROUP_ID     = os.getenv("GRAPHITI_GROUP_ID", "agents")

# Skills directory (mounted as /workspace/skills inside Docker)
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", "/workspace/skills"))


# ── Embedder proxy ────────────────────────────────────────────────────────────
class _EmbedderProxy:
    def __init__(self):
        self._model = EMBED_MODEL
        self._client = AsyncOpenAI(api_key=EMBED_API_KEY, base_url=EMBED_BASE_URL)
        logger.info("Embedder: provider=%s model=%s base_url=%s dim=%d",
                    EMBED_PROVIDER, EMBED_MODEL, EMBED_BASE_URL, EMBED_DIM)

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


# ── Graphiti singleton ────────────────────────────────────────────────────────
_graphiti: Graphiti | None = None
_graphiti_lock = asyncio.Lock()

async def get_graphiti() -> Graphiti:
    global _graphiti
    if _graphiti is None:
        async with _graphiti_lock:
            if _graphiti is None:
                logger.info("Initializing Graphiti: LLM=%s @ %s  embed=%s/%s(%dd)  group_id=%s",
                            LLM_MODEL, FREELLM_BASE, EMBED_PROVIDER, EMBED_MODEL, EMBED_DIM, GROUP_ID)
                llm = _PatchedOpenAIClient(config=LLMConfig(
                    model=LLM_MODEL, base_url=FREELLM_BASE, api_key=FREELLM_KEY))
                g = Graphiti(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD, llm_client=llm)
                await _build_indices(g.driver, EMBED_DIM)
                _graphiti = g
    return _graphiti


# ── Skills helper ─────────────────────────────────────────────────────────────
def list_skills() -> list[str]:
    if not SKILLS_DIR.exists():
        return []
    return [f.stem for f in SKILLS_DIR.glob("*.md") if f.stem != "README"]

def read_skill(name: str) -> str:
    path = SKILLS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill '{name}' not found. Available: {list_skills()}")
    return path.read_text()


# ── MCP Server ────────────────────────────────────────────────────────────────
mcp = Server("memory-skills")

@mcp.list_tools()
async def handle_list_tools() -> list[Tool]:
    skills = list_skills()
    return [
        Tool(name="memory_recall",
             description="Semantic search over the knowledge graph. Call this FIRST on every task.",
             inputSchema={"type": "object", "properties": {
                 "query": {"type": "string"},
                 "limit": {"type": "integer", "default": 10}},
             "required": ["query"]}),
        Tool(name="memory_add_episode",
             description="Ingest a new episode into the knowledge graph.",
             inputSchema={"type": "object", "properties": {
                 "name": {"type": "string"},
                 "content": {"type": "string"},
                 "source": {"type": "string", "default": "agent"}},
             "required": ["name", "content"]}),
        Tool(name="memory_get_context",
             description="Retrieve all graph facts for a specific entity.",
             inputSchema={"type": "object", "properties": {
                 "entity": {"type": "string"}},
             "required": ["entity"]}),
        Tool(name="memory_task_log",
             description="Log a completed or failed task into the knowledge graph.",
             inputSchema={"type": "object", "properties": {
                 "task": {"type": "string"},
                 "status": {"type": "string"},
                 "files_modified": {"type": "array", "items": {"type": "string"}, "default": []},
                 "decisions": {"type": "array", "items": {"type": "string"}, "default": []},
                 "notes": {"type": "string", "default": ""}},
             "required": ["task", "status"]}),
        Tool(name="memory_snapshot",
             description="Export raw facts from the knowledge graph (debug/inspection).",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_skill",
             description=f"Load a skill document (instructions/workflow). Available skills: {', '.join(skills) or 'none'}.",
             inputSchema={"type": "object", "properties": {
                 "name": {"type": "string", "enum": skills or ["none"]}},
             "required": ["name"]}),
    ]


@mcp.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    # ── get_skill ──────────────────────────────────────────────────────────────
    if name == "get_skill":
        try:
            content = read_skill(arguments["name"])
            return [TextContent(type="text", text=content)]
        except FileNotFoundError as e:
            return [TextContent(type="text", text=str(e))]

    # ── memory_snapshot ────────────────────────────────────────────────────────
    if name == "memory_snapshot":
        g = await get_graphiti()
        try:
            records, _, _ = await g.driver.execute_query(
                "MATCH ()-[r:RELATES_TO]->() "
                "RETURN r.fact AS fact, r.valid_at AS valid_at, r.group_id AS group_id "
                "ORDER BY r.created_at DESC LIMIT 200")
            facts = [{"fact": r["fact"], "valid_at": str(r["valid_at"]) if r["valid_at"] else None}
                     for r in records]
            import json
            return [TextContent(type="text", text=json.dumps({"total_facts": len(facts), "facts": facts}, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    # ── memory_recall ──────────────────────────────────────────────────────────
    if name == "memory_recall":
        g = await get_graphiti()
        try:
            results = await g.search(arguments["query"],
                                     num_results=arguments.get("limit", 10),
                                     group_ids=[GROUP_ID])
            import json
            facts = [{"fact": r.fact, "valid_at": r.valid_at.isoformat() if r.valid_at else None}
                     for r in results]
            return [TextContent(type="text", text=json.dumps(facts, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {traceback.format_exc()}")]

    # ── memory_add_episode ─────────────────────────────────────────────────────
    if name == "memory_add_episode":
        g = await get_graphiti()
        try:
            await g.add_episode(
                name=arguments["name"],
                episode_body=arguments["content"],
                source=EpisodeType.text,
                source_description=arguments.get("source", "agent"),
                reference_time=datetime.now(UTC),
                group_id=GROUP_ID,
            )
            return [TextContent(type="text", text=f"Episode '{arguments['name']}' added.")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {traceback.format_exc()}")]

    # ── memory_get_context ─────────────────────────────────────────────────────
    if name == "memory_get_context":
        g = await get_graphiti()
        try:
            results = await g.search(f"context and relationships for {arguments['entity']}",
                                     num_results=20, group_ids=[GROUP_ID])
            import json
            facts = [{"fact": r.fact} for r in results]
            return [TextContent(type="text", text=json.dumps(facts, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    # ── memory_task_log ────────────────────────────────────────────────────────
    if name == "memory_task_log":
        g = await get_graphiti()
        content = "\n".join(filter(None, [
            f"Task: {arguments['task']}",
            f"Status: {arguments['status']}",
            f"Files modified: {', '.join(arguments.get('files_modified', [])) or 'none'}",
            f"Decisions: {'; '.join(arguments.get('decisions', [])) or 'none'}",
            f"Notes: {arguments['notes']}" if arguments.get("notes") else None,
        ]))
        try:
            await g.add_episode(
                name=f"task:{arguments['task'][:60]}",
                episode_body=content,
                source=EpisodeType.text,
                source_description="agent_task_log",
                reference_time=datetime.now(UTC),
                group_id=GROUP_ID,
            )
            return [TextContent(type="text", text=f"Task '{arguments['task']}' logged as {arguments['status']}.")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Memory MCP Service", version="3.0.0")
sse_transport = SseServerTransport("/mcp/messages")

@app.get("/sse")
async def sse_endpoint(request: Request):
    """MCP SSE endpoint — OpenCode connects here."""
    async with sse_transport.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp.run(streams[0], streams[1], mcp.create_initialization_options())

@app.post("/mcp/messages")
async def mcp_messages(request: Request):
    """MCP message POST endpoint (required by SSE transport)."""
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)


# ── Legacy REST endpoints (keep for curl/debug) ───────────────────────────────
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

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "memory-mcp",
        "version": "3.0.0",
        "llm": f"{FREELLM_BASE} / {LLM_MODEL}",
        "embed_provider": EMBED_PROVIDER,
        "embedder": f"{EMBED_BASE_URL} / {EMBED_MODEL} ({EMBED_DIM}d)",
        "group_id": GROUP_ID,
        "mcp_sse": "http://localhost:8002/sse",
        "skills": list_skills(),
    }

@app.get("/tools")
async def list_tools_rest():
    return {"tools": [
        {"name": "memory_recall"},
        {"name": "memory_add_episode"},
        {"name": "memory_get_context"},
        {"name": "memory_task_log"},
        {"name": "memory_snapshot"},
        {"name": "get_skill", "available": list_skills()},
    ]}

@app.post("/mcp/memory_recall")
async def rest_memory_recall(req: RecallRequest):
    g = await get_graphiti()
    try:
        results = await g.search(req.query, num_results=req.limit, group_ids=[GROUP_ID])
        facts = [{"uuid": str(r.uuid), "fact": r.fact,
                  "valid_at": r.valid_at.isoformat() if r.valid_at else None} for r in results]
        return {"query": req.query, "results": facts, "count": len(facts)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/mcp/memory_add_episode")
async def rest_memory_add_episode(req: EpisodeRequest):
    g = await get_graphiti()
    try:
        await g.add_episode(name=req.name, episode_body=req.content, source=EpisodeType.text,
                            source_description=req.source, reference_time=datetime.now(UTC),
                            group_id=GROUP_ID)
        return {"status": "ok", "episode": req.name}
    except Exception:
        raise HTTPException(status_code=500, detail=traceback.format_exc())

@app.get("/mcp/memory_snapshot")
async def rest_memory_snapshot():
    g = await get_graphiti()
    try:
        records, _, _ = await g.driver.execute_query(
            "MATCH ()-[r:RELATES_TO]->() RETURN r.fact AS fact, r.valid_at AS valid_at, "
            "r.group_id AS group_id ORDER BY r.created_at DESC LIMIT 200")
        facts = [{"fact": r["fact"], "valid_at": str(r["valid_at"]) if r["valid_at"] else None,
                  "group_id": r["group_id"]} for r in records]
        return {"total_facts": len(facts), "facts": facts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
