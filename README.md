# agents_crew

[![CI](https://github.com/MoriondoTommaso/agents_crew/actions/workflows/ci.yml/badge.svg)](https://github.com/MoriondoTommaso/agents_crew/actions/workflows/ci.yml)

A Docker stack that gives [OpenCode](https://opencode.ai) **persistent memory** across sessions and projects.

Memory is powered by [Graphiti](https://github.com/getzep/graphiti) (knowledge graph) + Neo4j.
Embeddings and entity extraction run entirely on **Ollama** — zero external API costs.

---

## How it works

```
┌─────────────────────────────────────────────────────┐
│                    MAC HOST                         │
│                                                     │
│  opencode (your project dir)                        │
│      │                                              │
│      ├── LLM calls ──► :3001 (FreeLLMAPI / Ollama)  │
│      │                                              │
│      └── MCP tools ──► :8002/sse ──────────────┐   │
│                                                 │   │
│  ollama serve  ◄────────────────────────────┐   │   │
│                                             │   │   │
└─────────────────────────────────────────────┼───┼───┘
                                              │   │
┌─────────────────── DOCKER ──────────────────┼───┼───┐
│                                             │   │   │
│  memory container :8002                     │   │   │
│  └── FastMCP (SSE) ◄────────────────────────┘◄──┘   │
│       ├── memory_recall                             │
│       ├── memory_add_episode                        │
│       ├── memory_get_context                        │
│       └── memory_task_log                           │
│            │                                        │
│            └──► Graphiti ──► Neo4j :7687            │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### The three pieces

**1. The MCP server (Docker, always running)**
Runs as a Docker container on `:8002`. Exposes 4 memory tools to OpenCode via
SSE. Talks to Neo4j which persists the knowledge graph to disk. Stays alive
between sessions — memory is permanent until you run `make clean`.

**2. Bootstrap (one-time per project)**
Scans all source files in a project directory (`.py`, `.ts`, `.md`, `.json`,
`.go`, `.rs`, and more), extracts structure and content, and ingests them as
episodes into the graph under a project-specific namespace (`group_id`).
After bootstrap, OpenCode already knows the full project structure before
opening a single file.

**3. OpenCode (your IDE agent)**
Connects to the MCP server at startup via SSE. From that point, it has
4 memory tools available natively. At the start of every task it calls
`memory_recall` to load context. At the end it calls `memory_task_log`
to persist what was done — so the next session picks up exactly where
the last one left off.

---

## What happens during a session

```
You open OpenCode in your project
        │
        ▼
OpenCode calls memory_recall("current task")
        │
        ▼
Graphiti searches the knowledge graph
and returns relevant facts about the project
        │
        ▼
OpenCode works with full context — knows the file
structure, past decisions, previous tasks
        │
        ▼
While working, OpenCode calls memory_add_episode()
to save non-obvious decisions as they are made
        │
        ▼
Task done → OpenCode calls memory_task_log()
to persist what was built, which files changed,
and what decisions were made
        │
        ▼
Next session: memory_recall picks up all of this
```

---

## Memory tools

| Tool | When to call | What it does |
|---|---|---|
| `memory_recall` | **Always first** | Semantic search over all graph facts for the current project |
| `memory_add_episode` | During work | Saves a decision, discovery, or architectural note to the graph |
| `memory_get_context` | On demand | Returns all known facts about a specific entity (file, class, service) |
| `memory_task_log` | **Always last** | Logs the completed task with files changed, decisions made, and status |

Memory is **namespaced per project** via `GRAPHITI_GROUP_ID`. Bootstrapping
`project-alpha` does not affect the memory of `project-beta`.

---

## Setup (first time only)

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Ollama](https://ollama.com/) running on the host
- [OpenCode CLI](https://opencode.ai) installed
- An OpenAI-compatible LLM endpoint (FreeLLMAPI, OpenRouter, Groq, or local Ollama)

### 1. Clone and configure

```bash
git clone https://github.com/MoriondoTommaso/agents_crew ~/code_base/agents
cd ~/code_base/agents
cp .env.example .env
# edit .env — at minimum set NEO4J_PASSWORD and FREELLM_BASE_URL
```

Key variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `NEO4J_PASSWORD` | `changeme` | ⚠️ Change this before first run |
| `FREELLM_BASE_URL` | `http://host.docker.internal:3001/v1` | LLM endpoint for entity extraction (inside Docker) |
| `FREELLM_API_KEY` | falls back to `OPENAI_API_KEY` | API key for entity extraction LLM |
| `GRAPHITI_LLM_MODEL` | `auto` | Model name for entity extraction |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama endpoint for embeddings |
| `GRAPHITI_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `OPENAI_BASE_URL` | `http://localhost:3001/v1` | LLM endpoint for OpenCode (host) |
| `OPENAI_API_KEY` | — | API key for OpenCode LLM |

### 2. Pull Ollama embedding model

```bash
make models
# pulls nomic-embed-text (~274MB)
```

### 3. Start the Docker stack

```bash
make up
# starts neo4j :7474/:7687 + memory MCP :8002
```

### 4. Configure OpenCode globally (Mac)

Create this file once — it applies to every project on your machine:

```bash
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json << 'EOF'
{
  "mcpServers": {
    "memory": {
      "type": "sse",
      "url": "http://localhost:8002/sse"
    }
  }
}
EOF
```

Or use a per-project config by copying `opencode.json.example` to any project root:

```bash
cp ~/code_base/agents/opencode.json.example ~/code_base/YOUR_PROJECT/opencode.json
```

---

## Using memory on a new project

Every new project needs a one-time bootstrap to seed its memory.
After that, every OpenCode session automatically has full project context.

### Step 1 — Make sure the stack is running

```bash
cd ~/code_base/agents
docker compose up -d
```

### Step 2 — Bootstrap the project

```bash
~/code_base/agents/bootstrap.sh ~/code_base/YOUR_PROJECT
```

This scans all source files and ingests them into the graph under
the namespace `YOUR_PROJECT` (inferred from the directory name).

Test without writing to Neo4j first:

```bash
~/code_base/agents/bootstrap.sh ~/code_base/YOUR_PROJECT --dry-run
# prints every file that would be ingested
```

Use a custom namespace:

```bash
~/code_base/agents/bootstrap.sh ~/code_base/YOUR_PROJECT my-custom-id
```

### Step 3 — Copy OpenCode config to the project

```bash
cp ~/code_base/agents/opencode.json.example ~/code_base/YOUR_PROJECT/opencode.json
```

### Step 4 — Open OpenCode

```bash
cd ~/code_base/YOUR_PROJECT
opencode
```

OpenCode now connects to the memory server at startup. The 4 memory tools
are available immediately. The agent will call `memory_recall` at the start
of every task and `memory_task_log` at the end — keeping memory up to date
automatically across sessions.

---

## Daily workflow (once setup is done)

```bash
# Terminal 1 — keep Ollama running
ollama serve

# Terminal 2 — keep Docker stack running
cd ~/code_base/agents && docker compose up -d

# Terminal 3 — work in your project
cd ~/code_base/YOUR_PROJECT
opencode
```

The Docker stack only needs to restart if you reboot your machine.

---

## Makefile reference

```bash
make up                              # start neo4j + memory
make down                            # stop stack
make build                           # rebuild images without cache
make logs                            # follow container logs
make bootstrap                       # bootstrap agents_crew itself
make bootstrap TARGET_DIR=~/proj     # bootstrap any other project
make bootstrap TARGET_DIR=~/proj GROUP_ID=my-id  # custom namespace
make models                          # pull Ollama models
make opencode                        # launch OpenCode (injects .env)
make clean                           # stop + destroy all volumes (full reset)
```

---

## Debugging

```bash
# Check container logs
make logs

# Verify SSE endpoint is alive
curl -s -N --max-time 2 http://localhost:8002/sse
# should print one or more "data: ..." lines

# Browse the knowledge graph visually
open http://localhost:7474
# login: neo4j / <NEO4J_PASSWORD from .env>

# Full memory reset
make clean && make up && make bootstrap
```

---

## LLM endpoint options

```bash
# FreeLLMAPI (default — proxies to Ollama locally)
OPENAI_BASE_URL=http://localhost:3001/v1
OPENAI_API_KEY=freellm

# OpenRouter
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-...

# Groq
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=gsk_...

# Ollama directly
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
```
