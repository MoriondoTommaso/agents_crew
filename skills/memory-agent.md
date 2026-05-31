# Memory Agent Skill

This skill explains how to use the Graphiti memory tools effectively.
The memory service runs at `http://localhost:8002`.

## Available Tools

| Tool | Endpoint | When to use |
|---|---|---|
| `memory_recall` | `POST /mcp/memory_recall` | At the start of every task — semantic search |
| `memory_add_episode` | `POST /mcp/memory_add_episode` | After every non-obvious decision |
| `memory_get_context` | `POST /mcp/memory_get_context` | To load all facts about a specific file/entity |
| `memory_task_log` | `POST /mcp/memory_task_log` | At the end of every task |
| `memory_snapshot` | `GET /mcp/memory_snapshot` | Debug — dump all raw facts from Neo4j |

## Quick Reference

```bash
# Health check (shows LLM model, embedder, group_id)
curl http://localhost:8002/health

# Semantic search
curl -s -X POST http://localhost:8002/mcp/memory_recall \
  -H 'Content-Type: application/json' \
  -d '{"query": "routing config", "limit": 5}'

# Add an episode
curl -s -X POST http://localhost:8002/mcp/memory_add_episode \
  -H 'Content-Type: application/json' \
  -d '{"name": "decision:use-asyncio-lock", "content": "Used asyncio.Lock for Graphiti singleton to prevent race conditions on first init."}'

# Log a completed task
curl -s -X POST http://localhost:8002/mcp/memory_task_log \
  -H 'Content-Type: application/json' \
  -d '{"task": "fix bootstrap multi-language support", "status": "completed", "files_modified": ["memory/bootstrap.py"], "decisions": ["added .ts .go .rs extensions"], "notes": ""}'

# Dump all facts
curl http://localhost:8002/mcp/memory_snapshot | jq .facts
```

## Memory Namespacing

All episodes and searches are scoped to `GRAPHITI_GROUP_ID` (default: `agents`).
To isolate memory per project:

1. Add `GRAPHITI_GROUP_ID=my-project` to the project's `.env`
2. Restart the memory container: `make down && make up`
3. Re-run bootstrap: `make bootstrap`

## Bootstrap — When to Run

Run `make bootstrap` when:
- First time setting up the stack
- You've added significant new files to the codebase
- After `make clean` (volumes wiped)

Bootstrap scans all `.py` files under `/workspace`, chunks them, and ingests
each chunk as an episode into Graphiti. After bootstrap, `memory_recall` can
find functions, classes, and architectural patterns by semantic similarity.

## Resetting Memory

```bash
# Full reset (wipes Neo4j volume)
make clean && make up && make bootstrap
```

There is no partial reset endpoint yet — this is tracked as a future work item.

## Known Limitations

- `valid_at` field is always `null` — temporal reasoning not functional (graphiti-core 0.3.x)
- Bootstrap only indexes `.py` files — `.ts`, `.go`, `.rs`, `.md` not yet supported
- `memory_snapshot` has a hardcoded `LIMIT 200` — no pagination
- No memory clear/reset endpoint exposed yet
