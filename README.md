# agents_crew

[![CI](https://github.com/MoriondoTommaso/agents_crew/actions/workflows/ci.yml/badge.svg)](https://github.com/MoriondoTommaso/agents_crew/actions/workflows/ci.yml)

Infrastructure for agentic development with **OpenCode** as harness and
**Graphiti** for persistent cross-session memory via Neo4j.

Embeddings and entity extraction run entirely on **Ollama** — zero API costs.

## Architecture

```
OpenCode CLI (Mac/Linux host)
    │  OPENAI_BASE_URL=http://localhost:3001/v1
    ▼
FreeLLMAPI / OpenRouter / Groq / local Ollama  :3001
    └── planning, architecture, review, code gen

OpenCode MCP client
    │  SSE → http://localhost:8002/sse
    ▼
Memory service (Docker, port 8002)
    └── FastMCP (4 tools) → Graphiti → Neo4j 5.26
            ├── embedding:          Ollama nomic-embed-text (OLLAMA_BASE_URL/v1/embeddings)
            └── entity extraction:  FreeLLMAPI / Ollama responses (FREELLM_BASE_URL)
```

MCP transport is pure SSE — no REST, no health endpoint, no HTTP verbs.

## Quick Start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Ollama](https://ollama.com/) running on the host (`ollama serve`)
- [OpenCode CLI](https://opencode.ai) — `npm i -g opencode-ai`
- FreeLLMAPI or any OpenAI-compatible endpoint
- GitHub personal access token with scope `repo`

### 1. Clone and configure

```bash
git clone https://github.com/MoriondoTommaso/agents_crew
cd agents_crew
cp .env.example .env
# edit .env with your credentials
```

Required variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `OPENAI_BASE_URL` | `http://localhost:3001/v1` | LLM endpoint for OpenCode |
| `OPENAI_API_KEY` | — | API key for the LLM provider |
| `NEO4J_PASSWORD` | `changeme` | ⚠️ Change before first run |
| `FREELLM_BASE_URL` | `http://host.docker.internal:3001/v1` | LLM for entity extraction (inside Docker) |
| `FREELLM_API_KEY` | falls back to `OPENAI_API_KEY` | API key for entity extraction LLM |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama endpoint for embeddings |

### 2. Pull Ollama models

```bash
make models
# → nomic-embed-text   274MB   embeddings
```

### 3. Start the stack

```bash
make up
```

| Container | Port | Role |
|---|---|---|
| `neo4j` | 7474 / 7687 | Graph database |
| `memory` | 8002 | Graphiti MCP service (FastMCP SSE) |

### 4. Seed the knowledge graph (first time only)

```bash
make bootstrap
# scans .py files in the repo and ingests structure into the knowledge graph
```

### 5. Launch OpenCode

```bash
make opencode
```

OpenCode reads the MCP server config from `~/.config/opencode/opencode.jsonc`
(or a local `opencode.json` per project).

Example global config (`~/.config/opencode/opencode.jsonc`):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "memory": {
        "url": "http://localhost:8002/sse"
      }
    }
  }
}
```

## Agentic workflow

Every session follows this loop:

```
1. RECALL     memory_recall — semantic search over the knowledge graph
2. PLAN       plan the minimum diff needed
3. BRANCH     git checkout -b feat/<slug> (never main)
4. IMPLEMENT  write → bash → read output → fix → repeat
5. PR         gh pr create
6. LOG        memory_task_log — persist task outcome to the knowledge graph
```

## Memory MCP — available tools

| Tool | Arguments | Description |
|---|---|---|
| `memory_recall` | `query: str, limit?: int` | Semantic search over graph facts |
| `memory_add_episode` | `name: str, content: str, source?: str` | Ingest a new episode (triggers LLM entity extraction) |
| `memory_get_context` | `entity: str` | Retrieve all graph facts for a specific entity |
| `memory_task_log` | `task: str, status: str, files_modified?: list[str], decisions?: list[str], notes?: str` | Log a completed/failed task |

All tools return JSON via MCP protocol (SSE transport).

## Changing the LLM endpoint

Edit `.env` — no container restart needed:

```bash
# FreeLLMAPI (default)
OPENAI_BASE_URL=http://localhost:3001/v1
OPENAI_API_KEY=your-key

# OpenRouter
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-...

# Groq
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=gsk_...

# Ollama local
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
```

## Makefile commands

```bash
make up          # start Docker stack (neo4j + memory)
make down        # stop stack
make bootstrap   # seed knowledge graph from codebase (first run)
make models      # pull Ollama models
make opencode    # launch OpenCode
make logs        # follow all logs
make clean       # stop + destroy volumes (full reset)
```

## Debugging

```bash
# Container logs
make logs

# Neo4j Browser (visual graph)
open http://localhost:7474
# login: neo4j / <NEO4J_PASSWORD from .env>

# Full memory reset
make clean && make up && make bootstrap
```

## Working across repositories

The Docker stack (neo4j + memory) is shared. OpenCode only sees the directory
it is launched from. To isolate memory by project:

```bash
# In the project's .env
GRAPHITI_GROUP_ID=project-b
# then: make down && make up
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_BASE_URL` | `http://localhost:3001/v1` | LLM endpoint (OpenAI-compatible) |
| `OPENAI_API_KEY` | — | LLM API key |
| `FREELLM_BASE_URL` | `http://host.docker.internal:3001/v1` | LLM endpoint inside Docker |
| `FREELLM_API_KEY` | falls back to `OPENAI_API_KEY` | API key for the Docker LLM |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama endpoint (embeddings + optional LLM) |
| `NEO4J_PASSWORD` | `changeme` | ⚠️ Change before first use |
| `NEO4J_URI` | `bolt://neo4j:7687` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `GITHUB_TOKEN` | — | GitHub PAT with scope `repo` |
| `GRAPHITI_LLM_MODEL` | `auto` | Model for entity extraction |
| `GRAPHITI_EMBED_PROVIDER` | `ollama` | Embedding provider |
| `GRAPHITI_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `GRAPHITI_EMBED_DIM` | `768` | Embedding dimension |
| `GRAPHITI_EMBED_BASE_URL` | set automatically | Override embed endpoint |
| `GRAPHITI_EMBED_API_KEY` | set automatically | Override embed API key |
| `GRAPHITI_GROUP_ID` | `agents` | Memory namespace (one per project) |
