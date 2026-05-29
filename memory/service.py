"""Graphiti Memory MCP Service — port 8002

Exposes 5 MCP tools for the coding agent:
  - memory_recall        : semantic search over the knowledge graph
  - memory_add_episode   : ingest a new episode (task, decision, observation)
  - memory_get_context   : retrieve subgraph for a specific entity/file
  - memory_task_log      : log a completed or failed task with affected files
  - memory_snapshot      : dump full graph as JSON (debug / export)
"""

import os
import json
import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType

# ── Config ─────────────────────────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://neo4j:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBED_MODEL    = os.getenv("GRAPHITI_EMBED_MODEL", "text-embedding-3-small")
LLM_MODEL      = os.getenv("GRAPHITI_LLM_MODEL",   "gpt-4o-mini")

# ── App + Graphiti client ──────────────────────────────────────────────────
app = FastAPI(title="Memory MCP Service", version="1.0.0")
_graphiti: Graphiti | None = None


async def get_graphiti() -> Graphiti:
    global _graphiti
    if _graphiti is None:
        _graphiti = Graphiti(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        await _graphiti.build_indices_and_constraints()
    return _graphiti


# ── Pydantic models ────────────────────────────────────────────────────────
class RecallRequest(BaseModel):
    query: str
    limit: int = 10

class EpisodeRequest(BaseModel):
    name: str          # short label, e.g. "fix keyword routing"
    content: str       # free-text description of what happened
    source: str = "agent"  # agent | human | system

class ContextRequest(BaseModel):
    entity: str        # file path or entity name, e.g. "crew.py" or "SMLRouter"

class TaskLogRequest(BaseModel):
    task: str
    status: str        # "completed" | "failed" | "in_progress"
    files_modified: list[str] = []
    decisions: list[str] = []
    notes: str = ""


# ── Endpoints ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "memory-mcp"}


@app.get("/tools")
async def list_tools():
    """MCP tool manifest."""
    return {
        "tools": [
            {
                "name": "memory_recall",
                "description": "Semantic search over the knowledge graph. Returns relevant facts, decisions, and context from past tasks.",
                "parameters": {"query": "string", "limit": "int (default 10)"}
            },
            {
                "name": "memory_add_episode",
                "description": "Ingest a new episode into the graph (observation, decision, task result). Call after every significant action.",
                "parameters": {"name": "string", "content": "string", "source": "string (agent|human|system)"}
            },
            {
                "name": "memory_get_context",
                "description": "Retrieve all graph facts related to a specific entity (file, class, function, concept).",
                "parameters": {"entity": "string"}
            },
            {
                "name": "memory_task_log",
                "description": "Log a completed, failed, or in-progress task with affected files and decisions made.",
                "parameters": {
                    "task": "string",
                    "status": "completed|failed|in_progress",
                    "files_modified": "list[string]",
                    "decisions": "list[string]",
                    "notes": "string"
                }
            },
            {
                "name": "memory_snapshot",
                "description": "Export the full knowledge graph as JSON. Use for debugging or external inspection.",
                "parameters": {}
            }
        ]
    }


@app.post("/mcp/memory_recall")
async def memory_recall(req: RecallRequest):
    g = await get_graphiti()
    try:
        results = await g.search(req.query, num_results=req.limit)
        facts = [
            {
                "uuid": str(r.uuid),
                "fact": r.fact,
                "valid_at": r.valid_at.isoformat() if r.valid_at else None,
                "source_node": r.source_node_uuid,
            }
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
        results = await g.search(
            f"context and relationships for {req.entity}",
            num_results=20,
        )
        return {
            "entity": req.entity,
            "facts": [{"fact": r.fact, "valid_at": r.valid_at.isoformat() if r.valid_at else None} for r in results],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mcp/memory_task_log")
async def memory_task_log(req: TaskLogRequest):
    g = await get_graphiti()
    content_parts = [
        f"Task: {req.task}",
        f"Status: {req.status}",
        f"Files modified: {', '.join(req.files_modified) if req.files_modified else 'none'}",
        f"Decisions: {'; '.join(req.decisions) if req.decisions else 'none'}",
    ]
    if req.notes:
        content_parts.append(f"Notes: {req.notes}")
    content = "\n".join(content_parts)
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
    """Return basic graph stats. Full export via Neo4j Browser at :7474."""
    g = await get_graphiti()
    try:
        results = await g.search("*", num_results=200)
        return {
            "total_facts": len(results),
            "facts": [{"fact": r.fact, "valid_at": r.valid_at.isoformat() if r.valid_at else None} for r in results],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
