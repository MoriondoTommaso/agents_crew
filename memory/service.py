"""Graphiti Memory MCP Service — port 8002

Exposes 5 MCP tools for the coding agent:
  - memory_recall        : semantic search over the knowledge graph
  - memory_add_episode   : ingest a new episode (task, decision, observation)
  - memory_get_context   : retrieve subgraph for a specific entity/file
  - memory_task_log      : log a completed or failed task with affected files
  - memory_snapshot      : dump full graph as JSON (debug / export)

Embeddings: local via Ollama (nomic-embed-text) — no OpenAI key required.
LLM for entity extraction: Ollama (qwen2.5:1.5b) — zero API cost.

Note on graphiti-core 0.3.x imports:
  LLMClient  → graphiti_core.llm_client.client
  LLMConfig  → graphiti_core.llm_client.config
  EmbedderClient → graphiti_core.embedder.client
"""

import os
import asyncio
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.client import EmbedderClient

# ── Config ─────────────────────────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI",       "bolt://neo4j:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",      "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD",  "changeme")
OLLAMA_BASE    = os.getenv("OLLAMA_BASE_URL",  "http://host.docker.internal:11434")
EMBED_MODEL    = os.getenv("GRAPHITI_EMBED_MODEL", "nomic-embed-text")
LLM_MODEL      = os.getenv("GRAPHITI_LLM_MODEL",   "qwen2.5:1.5b")


# ── Ollama embedder ────────────────────────────────────────────────────────
class OllamaEmbedder(EmbedderClient):
    """Thin wrapper around Ollama /api/embeddings for Graphiti."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model    = model

    async def create(self, input: str | list[str]) -> list[list[float]]:
        texts = [input] if isinstance(input, str) else input
        async with httpx.AsyncClient(timeout=60) as client:
            results = []
            for text in texts:
                resp = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                resp.raise_for_status()
                results.append(resp.json()["embedding"])
        return results


# ── Ollama LLM client ──────────────────────────────────────────────────────
class OllamaLLMClient(LLMClient):
    """Thin wrapper around Ollama /api/chat for Graphiti entity extraction."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model    = model
        config = LLMConfig(model=model)
        super().__init__(config)

    async def _generate_response(
        self,
        messages: list[dict],
        response_model=None,
        **kwargs,
    ) -> str:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model":    self.model,
                    "messages": messages,
                    "stream":   False,
                    "options":  {"temperature": 0.0},
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]


# ── App + Graphiti client ──────────────────────────────────────────────────
app = FastAPI(title="Memory MCP Service", version="2.0.0")
_graphiti: Graphiti | None = None


async def get_graphiti() -> Graphiti:
    global _graphiti
    if _graphiti is None:
        embedder  = OllamaEmbedder(base_url=OLLAMA_BASE, model=EMBED_MODEL)
        llm       = OllamaLLMClient(base_url=OLLAMA_BASE, model=LLM_MODEL)
        _graphiti = Graphiti(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
            llm_client=llm,
            embedder=embedder,
        )
        await _graphiti.build_indices_and_constraints()
    return _graphiti


# ── Pydantic models ────────────────────────────────────────────────────────
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


# ── Endpoints ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "memory-mcp",
        "embed_model": EMBED_MODEL,
        "llm_model": LLM_MODEL,
        "ollama_base": OLLAMA_BASE,
    }


@app.get("/tools")
async def list_tools():
    return {
        "tools": [
            {"name": "memory_recall",      "description": "Semantic search over the knowledge graph.",        "parameters": {"query": "string", "limit": "int (default 10)"}},
            {"name": "memory_add_episode", "description": "Ingest a new episode into the graph.",             "parameters": {"name": "string", "content": "string", "source": "string"}},
            {"name": "memory_get_context", "description": "Retrieve all graph facts for a specific entity.",  "parameters": {"entity": "string"}},
            {"name": "memory_task_log",    "description": "Log a completed or failed task.",                  "parameters": {"task": "string", "status": "string", "files_modified": "list", "decisions": "list", "notes": "string"}},
            {"name": "memory_snapshot",    "description": "Export the full knowledge graph (debug).",         "parameters": {}},
        ]
    }


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
