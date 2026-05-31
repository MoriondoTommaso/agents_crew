# Project Memory — agents_crew

This file is ingested by `bootstrap.py` into the Graphiti knowledge graph on
first run. Update it manually when architectural decisions change.

## Architectural Decisions

- **Agent harness**: OpenCode CLI (runs on host Mac/Linux), reads `AGENTS.md` automatically
- **LLM routing**: FreeLLMAPI on host `:3001` — OpenAI-compatible proxy, swap via `OPENAI_BASE_URL`
- **Memory**: Graphiti + Neo4j — persistent cross-session knowledge graph
- **Embedding**: Ollama `nomic-embed-text` local — zero API cost
- **Entity extraction**: Graphiti LLM (configurable via `GRAPHITI_LLM_MODEL`)
- **MCP tools**: memory service at `http://localhost:8002` (5 tools)
- **GitHub tools**: built-in OpenCode — no custom MCP server needed
- **Filesystem tools**: built-in OpenCode — no custom MCP server needed
- **Docker stack**: 2 containers only — `neo4j` + `memory`

## Stack History (removed)

- **CrewAI + SMLRouter** — replaced by OpenCode
- **LiteLLM proxy** — replaced by FreeLLMAPI + direct `OPENAI_BASE_URL`
- **Claude Code CLI** — replaced by OpenCode
- **coding-agency FastAPI server** — no longer needed
- **litellm container** — removed from docker-compose

## Active Models

| Model | Provider | Purpose |
|---|---|---|
| `nomic-embed-text` | Ollama local | Graphiti embeddings |
| configurable | FreeLLMAPI / Ollama | Graphiti entity extraction |
| any OpenAI-compat model | FreeLLMAPI / OpenRouter / Groq | Agent reasoning |

## Key Files

- `memory/service.py` — Graphiti MCP server, 5 tools, port 8002
- `memory/bootstrap.py` — codebase scan → Graphiti ingestion
- `docker-compose.yml` — 2 containers: neo4j, memory
- `AGENTS.md` — agent behaviour instructions (authoritative)
- `.opencode/config.json` — MCP config for OpenCode
- `.env.example` — all configurable variables with comments

## Memory Group IDs

- Default group: `agents` (set via `GRAPHITI_GROUP_ID` in `.env`)
- Change per-project for isolated memory namespaces

## CI / Quality Gates

- GitHub Actions `.github/workflows/ci.yml` runs on every push and PR
- Jobs: ruff lint → pytest (13 tests, fully mocked) → Docker build
- All tests run without external services (Neo4j, Ollama, LLM)
