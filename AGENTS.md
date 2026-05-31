# Coding Agent — Instructions for OpenCode

You are an expert software development agent. You have access to tools for
filesystem, git, GitHub, shell, and persistent memory.

## Project Stack

| Component | Detail |
|---|---|
| Persistent memory | Graphiti + Neo4j at `http://localhost:8002` |
| LLM proxy | FreeLLMAPI at `http://localhost:3001/v1` (swap via `OPENAI_BASE_URL`) |
| Embeddings / entity extraction | Ollama local (`nomic-embed-text`, configurable) |
| Default repo | `MoriondoTommaso/agents_crew` |

## Available Tools

### Memory MCP (`http://localhost:8002`)
| Tool | When to use |
|---|---|
| `memory_recall` | **Always first** — pull relevant context before starting |
| `memory_get_context` | For details on a specific file or class |
| `memory_add_episode` | After every non-obvious decision |
| `memory_task_log` | **Always last** — log the completed task |
| `memory_snapshot` | Debug — dump all raw graph facts |

### Built-in OpenCode
- **Files**: `read`, `write`, `edit`, `list_directory`
- **Shell**: `bash` — run, test, read output, fix
- **Git / GitHub**: branch, commit, PR
- **Web**: search documentation

## Standard Workflow (follow every time)

```
1. RECALL     memory_recall(query=<task summary>)
              memory_get_context(entity=<main file>)

2. PLAN       3–5 bullet points before writing code
              Minimal diff — change only what is necessary

3. BRANCH     git checkout -b <type>/<slug>   ← NEVER work on main

4. IMPLEMENT  read → edit → bash (run/test) → read output → fix → repeat
              memory_add_episode() for every non-obvious decision

5. TEST       pytest tests/ -v --tb=short
              ruff check <changed files>
              Fix ALL failures before opening PR.

6. PR         gh pr create --title "type(scope): description" --body "<what and why>"
              Wait for CI to pass. Do NOT merge if CI is red.

7. LOG        memory_task_log(task, status, files_modified, decisions, notes)
```

## 🛑 Hard Rules — Never Break These

These are non-negotiable. Violating them will cause the PR to be rejected.

1. **Never `git push` to `main` directly.** Branch protection is enforced
   server-side — the push will be rejected. Always use a feature branch.

2. **Never open a PR if `pytest` or `ruff` fail locally.**
   Run both before `gh pr create`. CI will catch it anyway, but it wastes time.

3. **Never commit secrets, API keys, or `.env` files.**
   Use `.env.example` for templates. Actual keys go in `.env` (gitignored).

4. **Never skip RECALL at the start or LOG at the end.**
   Memory is only useful if it is kept up to date.

5. **Never install packages outside `pyproject.toml` or `requirements.txt`.**
   If a new dependency is needed, add it to the project manifest first.

6. **One branch per task.** Do not accumulate multiple unrelated changes
   on a single branch.

## CI Contract

Every PR must pass all three CI jobs before merge:

| Job | Command | Must pass |
|---|---|---|
| Lint | `ruff check memory/ tests/` | Yes |
| Tests | `pytest tests/test_memory_service.py -v` | Yes (13/13) |
| Docker build | `docker build ./memory` | Yes |

If CI is red, fix the issue on the same branch before requesting merge.
Do NOT open a new branch to work around a failing CI.

## Branch Naming

| Prefix | When |
|---|---|
| `feat/` | New feature |
| `fix/` | Bug fix |
| `chore/` | Tooling, deps, hygiene |
| `refactor/` | Restructure with no behaviour change |
| `docs/` | Documentation only |
| `test/` | Tests only |

## Commit Format

```
type(scope): short description
```

Examples:
- `feat(memory): add DELETE /mcp/memory_clear endpoint`
- `fix(bootstrap): handle empty Python files gracefully`
- `chore(ci): add anyio to test dependencies`
- `test(memory): cover snapshot pagination`

## 📋 Task Backlog

Pick one task at a time. Start with RECALL, end with LOG.
Mark the task as in-progress by creating the branch — do not pick a task
already being worked on (check open PRs first with `gh pr list`).

### Priority 1 — Core Improvements

**TASK-01: Add `DELETE /mcp/memory_clear` endpoint**
- Branch: `feat/memory-clear-endpoint`
- Description: Add an endpoint that deletes all episodes and facts for a given
  `group_id` from Neo4j without wiping the whole database. Useful for resetting
  memory without running `make clean`.
- Acceptance: endpoint exists, returns `{"status": "cleared", "group_id": ...}`,
  covered by at least one new test.

**TASK-02: Bootstrap supports `.md` files**
- Branch: `feat/bootstrap-markdown`
- Description: `memory/bootstrap.py` currently only indexes `.py` files. Extend
  it to also ingest `.md` files (AGENTS.md, README.md, MEMORY.md, skills/*.md)
  as plain-text episodes so the agent can recall architectural decisions.
- Acceptance: `.md` files appear in `memory_snapshot` after bootstrap.

**TASK-03: `memory_recall` returns source file metadata**
- Branch: `feat/recall-metadata`
- Description: Each result from `memory_recall` currently returns `uuid`, `fact`,
  and `valid_at`. Add `source_description` (the episode source field already
  stored by Graphiti) to the response so the agent knows which file a fact
  came from.
- Acceptance: `results[n].source` present in response, test updated.

### Priority 2 — Developer Experience

**TASK-04: `make test` shortcut**
- Branch: `chore/makefile-test-target`
- Description: Add a `make test` target that runs `ruff check` + `pytest` locally
  without Docker. Also add `make lint` as a standalone target.
- Acceptance: `make test` exits 0 on clean repo, `make lint` exits 0.

**TASK-05: Health endpoint returns Neo4j connectivity status**
- Branch: `feat/health-neo4j-check`
- Description: The `GET /health` endpoint currently returns static config values.
  Add a live check: attempt a lightweight Cypher query (`RETURN 1`) and include
  `"neo4j": "ok"` or `"neo4j": "unreachable"` in the response.
- Acceptance: when Neo4j is up the field is `"ok"`, test mocks both states.

**TASK-06: Bootstrap dry-run flag**
- Branch: `feat/bootstrap-dry-run`
- Description: Add a `--dry-run` flag to `bootstrap.py` that prints which files
  would be ingested and how many chunks, without actually calling Graphiti.
  Useful for debugging without burning API quota.
- Acceptance: `python bootstrap.py --dry-run` prints a summary and exits 0.

### Priority 3 — Observability

**TASK-07: Structured JSON logging**
- Branch: `feat/structured-logging`
- Description: Replace the current `logging.basicConfig` in `service.py` with
  structured JSON logging (one JSON object per line). Fields: `timestamp`,
  `level`, `logger`, `message`, plus any extra kwargs passed to the log call.
  Use stdlib only — no new dependencies.
- Acceptance: each log line is valid JSON, existing tests still pass.

**TASK-08: `GET /metrics` Prometheus endpoint**
- Branch: `feat/prometheus-metrics`
- Description: Add a `/metrics` endpoint exposing basic counters:
  `memory_recall_total`, `memory_add_episode_total`, `memory_task_log_total`,
  `memory_errors_total`. Use `prometheus_client` (add to Dockerfile deps).
- Acceptance: `curl /metrics` returns valid Prometheus text format.

## Repository Structure

```
agents_crew/
├── AGENTS.md              ← this file (agent instructions, authoritative)
├── MEMORY.md              ← seed knowledge graph for bootstrap
├── Makefile               ← up / bootstrap / opencode / freellm / models
├── .env.example           ← copy to .env and fill in keys
├── .opencode/
│   └── config.json        ← MCP config for OpenCode
├── memory/
│   ├── Dockerfile
│   ├── service.py         ← Graphiti MCP (5 tools, port 8002)
│   └── bootstrap.py       ← codebase scan → knowledge graph
├── tests/
│   └── test_memory_service.py
├── skills/
│   ├── coding-workflow.md
│   └── memory-agent.md
└── docker-compose.yml     ← 2 containers: neo4j, memory
```

## Ollama Models Required

```bash
ollama pull nomic-embed-text   # embeddings (~274MB)
ollama pull qwen2.5:1.5b       # entity extraction (~1GB, optional)
```

## Quick Reference

```bash
make up          # start Docker stack (neo4j + memory)
make bootstrap   # seed knowledge graph from codebase
make opencode    # launch OpenCode agent
make freellm     # start FreeLLMAPI server on :3001
make models      # pull Ollama models
make logs        # follow all logs
make down        # stop everything
make clean       # stop + destroy volumes (full reset)
```

## LLM Endpoint Swap (zero friction)

```bash
# FreeLLMAPI (default)
OPENAI_BASE_URL=http://localhost:3001/v1
OPENAI_API_KEY=freellmapi-...

# OpenRouter
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-...

# Groq
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=gsk_...

# Local Ollama only
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
```
