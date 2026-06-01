# SPEC.md — Code Review & Fix Specification

> Authoritative spec for the `agents_crew` repository.
> OpenCode must read this file at the start of every session via `get_skill` or direct file read.
> Last updated: 2026-06-01

---

## 1. Architecture Overview

```
MAC HOST
├── opencode          (TUI agent, reads ~/.config/opencode/opencode.json)
├── freellmapi        (LLM proxy on :3001, optional — can use Ollama directly)
└── ollama serve      (local models on :11434)

DOCKER (docker compose up)
├── neo4j:5.26        (graph DB, ports :7474 :7687)
└── memory-mcp        (Graphiti MCP service, port :8002)
    ├── GET  /health
    ├── GET  /sse           ← MCP SSE endpoint (OpenCode connects here)
    ├── POST /mcp/messages  ← MCP SSE message handler
    └── POST /mcp/*         ← Legacy REST (keep for curl debugging)
```

### Data flow
```
OpenCode
  │
  ├─ LLM calls ──→ http://localhost:3001/v1  (FreeLLMAPI)
  │                        │
  │                        └──→ http://127.0.0.1:11434  (Ollama)
  │
  └─ MCP tools ──→ http://localhost:8002/sse
                        ├── memory_recall
                        ├── memory_add_episode
                        ├── memory_get_context
                        ├── memory_task_log
                        ├── memory_snapshot
                        └── get_skill
```

---

## 2. File Inventory & Status

| File | Status | Issue |
|---|---|---|
| `memory/service.py` | ⚠ BROKEN | Missing `mcp[sse]` import; SSE endpoint added but `SseServerTransport` API may differ across `mcp` versions |
| `memory/Dockerfile` | ✅ FIXED | `COPY . .` (context is `./memory`) |
| `memory/requirements.txt` | ⚠ INCOMPLETE | `mcp[sse]>=1.0` added but version unpinned; `mcp` package API changed between 0.x and 1.x |
| `memory/bootstrap.py` | ⚠ PARTIAL | Only indexes `.py` files; `.md` files not ingested (TASK-02) |
| `docker-compose.yml` | ✅ OK | Skills dir not mounted — add volume `./skills:/workspace/skills:ro` |
| `Makefile` | ⚠ BUG | `check-deps` reads `OLLAMA_BASE_URL` from env but `.env` uses `OLLAMA_BASE_URL=http://127.0.0.1:11434` — curl check passes only if Ollama is already running |
| `opencode.json` | ⚠ WRONG LOCATION | Must be in repo root. Format depends on OpenCode version |
| `.opencode/config.json` | 🗑 DELETE | This path crashes OpenCode 0.0.55. Remove it |
| `AGENTS.md` | ✅ OK | Authoritative agent instructions |
| `skills/*.md` | ✅ OK | Not yet reachable by MCP — needs volume mount + `get_skill` tool |
| `tests/test_memory_service.py` | ⚠ STALE | Tests written against old REST-only `service.py`; need updating for SSE |

---

## 3. Bugs to Fix (Priority Order)

### BUG-01 — `service.py` SSE transport API (CRITICAL)

**Problem:** `mcp.server.sse.SseServerTransport` constructor and `connect_sse` signature
changed in `mcp>=1.0`. The current code uses the 0.x API.

**Fix:** Use the stable `mcp>=1.3` API:

```python
# requirements.txt
mcp>=1.3

# service.py — correct SSE wiring for mcp>=1.3
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memory-skills")

# Register tools with @mcp.tool() decorator
@mcp.tool()
async def memory_recall(query: str, limit: int = 10) -> str:
    ...

# Mount on FastAPI
app.mount("/", mcp.get_asgi_app())
```

Using `FastMCP` from `mcp.server.fastmcp` is the stable high-level API
that handles SSE, POST /messages, and tool registration automatically.
The `/sse` and `/mcp/messages` routes are created automatically.

---

### BUG-02 — Skills directory not mounted in Docker (HIGH)

**Problem:** `service.py` reads skills from `/workspace/skills` but
`docker-compose.yml` mounts only `.:/workspace:ro` — the whole repo root.
This actually works, but `SKILLS_DIR` default in `service.py` is `/workspace/skills`
and needs to match the mount path.

**Verify:** After `make up`:
```bash
docker exec memory ls /workspace/skills/
# Should show: coding-agent.md  coding-workflow.md  memory-agent.md
```

If empty → the volume is not mounted. Fix in `docker-compose.yml`:
```yaml
volumes:
  - .:/workspace:ro          # repo root already includes skills/
```
This is already correct. The bug is if `SKILLS_DIR` env var is wrong.

---

### BUG-03 — `.opencode/config.json` crashes OpenCode (CRITICAL)

**Problem:** The file `.opencode/config.json` in the repo root causes
`Error: 4 of 5 requests failed` when OpenCode starts.
OpenCode 0.0.55 does NOT read from `.opencode/config.json`.

**Fix:**
```bash
rm .opencode/config.json
# or delete the entire .opencode/ directory if it has no other content
```

OpenCode reads its config from (in order of precedence):
1. `~/.config/opencode/opencode.json` (global, on Mac host)
2. `opencode.json` in the current working directory
3. Environment variables (`OPENAI_BASE_URL`, `OPENAI_API_KEY`)

---

### BUG-04 — `opencode.json` format wrong (HIGH)

**Problem:** The `opencode.json` in repo root uses a schema that OpenCode
0.0.55 does not recognize, causing `agent coder not found`.

**Fix — check OpenCode docs for your version:**
```bash
opencode --version
opencode --help
```

For OpenCode 0.0.55, the correct `opencode.json` is:
```json
{
  "model": "openai/qwen2.5-coder:14b",
  "keybinds": {},
  "mcp": {
    "memory": {
      "type": "sse",
      "url": "http://localhost:8002/sse"
    }
  }
}
```

The provider/API key are set via **environment variables only**:
```bash
export OPENAI_BASE_URL=http://localhost:3001/v1
export OPENAI_API_KEY=freell
```
Or via `make opencode` which injects them from `.env`.

---

### BUG-05 — `Makefile check-deps` reads wrong env var (MEDIUM)

**Problem:** `check-deps` tries to read `OLLAMA_BASE_URL` from the shell environment,
but the value is in `.env`. The grep reads from `.env` correctly for `EMBED_PROVIDER`
but then uses `$OLLAMA_BASE_URL` which is not exported.

**Fix in Makefile:**
```make
check-deps:
	@OLLAMA_URL=$$(grep -v '^#' .env 2>/dev/null | grep ^OLLAMA_BASE_URL | cut -d= -f2); \
	 OLLAMA_URL=$${OLLAMA_URL:-http://127.0.0.1:11434}; \
	 curl -sf "$$OLLAMA_URL" > /dev/null 2>&1 \
		&& echo "  ✓ Ollama reachable at $$OLLAMA_URL" \
		|| (echo "  ✗ Ollama NOT reachable — run: ollama serve" && exit 1)
```

---

## 4. MCP Service — Correct Implementation

The `memory/service.py` must be rewritten to use `FastMCP` from `mcp>=1.3`.
This is the only stable API for SSE in 2025-2026.

### Tool list (all 6 tools must be exposed)

| Tool | Input | Output |
|---|---|---|
| `memory_recall` | `query: str, limit: int = 10` | JSON list of facts |
| `memory_add_episode` | `name: str, content: str, source: str = "agent"` | `{status: ok}` |
| `memory_get_context` | `entity: str` | JSON list of facts |
| `memory_task_log` | `task: str, status: str, files_modified: list, decisions: list, notes: str` | `{status: logged}` |
| `memory_snapshot` | _(none)_ | JSON dump of all facts |
| `get_skill` | `name: str` (enum of available skills) | Markdown content of skill file |

### Skills available

| Skill name | File | Purpose |
|---|---|---|
| `coding-agent` | `skills/coding-agent.md` | Agent persona + rules |
| `coding-workflow` | `skills/coding-workflow.md` | Step-by-step dev workflow |
| `memory-agent` | `skills/memory-agent.md` | How to use memory tools |

---

## 5. OpenCode Configuration (Mac Host Only)

Do NOT put OpenCode config inside the repo. Configure it globally:

```bash
# ~/.config/opencode/opencode.json
{
  "model": "openai/qwen2.5-coder:14b",
  "mcp": {
    "memory": {
      "type": "sse",
      "url": "http://localhost:8002/sse"
    }
  }
}
```

API key and base URL via env vars (already in `.env`, injected by `make opencode`):
```
OPENAI_BASE_URL=http://localhost:3001/v1
OPENAI_API_KEY=freell
```

---

## 6. Task Backlog (from AGENTS.md — unchanged)

See `AGENTS.md` section `## 📋 Task Backlog` for the full list.
Before picking a task, always run:
```bash
gh pr list          # check nothing already in progress
curl -s http://localhost:8002/health | jq .   # verify memory service is up
```

---

## 7. Startup Checklist

Run in this exact order every time:

```bash
# 1. Ollama (background terminal)
ollama serve

# 2. FreeLLMAPI (background terminal, optional — can use Ollama directly)
make freellm

# 3. Docker stack
make up

# 4. Verify memory service
curl -s http://localhost:8002/health | jq '{status, mcp_sse, skills}'
# Expected:
# {
#   "status": "ok",
#   "mcp_sse": "http://localhost:8002/sse",
#   "skills": ["coding-agent", "coding-workflow", "memory-agent"]
# }

# 5. Seed knowledge graph (first time only)
make bootstrap

# 6. Launch OpenCode
make opencode
```

---

## 8. What OpenCode Must Do at Session Start

1. Call `get_skill(name="coding-workflow")` to load the dev workflow
2. Call `memory_recall(query="recent tasks and decisions")` to load context
3. Read `AGENTS.md` if not already in context
4. Only then start working on the user's task

---

## 9. Files OpenCode Must NOT Touch

- `.env` (secrets)
- `neo4j-data/` volume
- `~/.config/opencode/` (host config, outside repo)
- Any file outside `~/code_base/agents/`

---

## 10. Acceptance Tests

After implementing BUG-01 fix (FastMCP rewrite):

```bash
# SSE endpoint responds
curl -s -N -H "Accept: text/event-stream" http://localhost:8002/sse &
sleep 1 && kill %1
# Should output: data: ... (not 404)

# All 6 tools visible
curl -s http://localhost:8002/tools | jq '[.tools[].name]'
# ["memory_recall","memory_add_episode","memory_get_context",
#  "memory_task_log","memory_snapshot","get_skill"]

# Skills mounted correctly
docker exec memory ls /workspace/skills/
# coding-agent.md  coding-workflow.md  memory-agent.md

# OpenCode starts without error
opencode --version  # should print version, not crash
make opencode       # should open TUI with model shown bottom-right
```
