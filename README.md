# Hybrid Coding Agency

A lightweight **SML router** that exposes an OpenAI-compatible API backed by two LLMs,
plus an MCP tool server that gives the agent read/write access to the local filesystem
and GitHub.

| Task | Model | Where |
|---|---|---|
| Planning & architecture | Frontier LLM (via FreeLLM) | API |
| Code generation | `qwen2.5-coder:14b` | Local (Ollama) |
| Routing decisions | `qwen2.5:1.5b` | Local (Ollama) |

Designed to sit behind an agentic harness (Open-Claw). The harness brings context,
MCP tools, and the iteration loop — this router brings **cost efficiency**:
zero API spend on code generation.

## Architecture

```
Open-Claw (harness)
    │  /v1/chat/completions
    ▼
Hybrid Coding Agency   :8000   plan → code pipeline
    │ plan                │ code
FreeLLM API         Ollama local
(gpt-4o / etc)      (qwen2.5-coder:14b)

Open-Claw MCP tools:
    ├── filesystem   read/write /workspace via mcp-server :8001
    └── github       branch / commit / PR via GitHub API
```

Single-pass pipeline: `plan → code → return`.
Iteration is handled by the harness (Open-Claw executes code, reads output, loops).

## Project Structure

```
agents_crew/
├── server.py              # FastAPI app — OpenAI-compatible endpoints
├── crew.py                # CodingAgencyCrew + SMLRouter
├── main.py                # CLI entry point
├── config/
│   ├── agents.yaml        # Agent definitions
│   └── tasks.yaml         # Task definitions
├── mcp/
│   ├── server.py          # MCP tool server (filesystem + GitHub)
│   ├── Dockerfile
│   └── requirements.txt
├── openclaw/
│   ├── openclaw.json      # Open-Claw config (models + MCP server)
│   └── system-prompt.md   # Agent system prompt with tool instructions
├── skills/
│   ├── README.md          # How to use skills
│   └── coding-agent.md    # Full coding workflow skill
├── tests/
├── test_pipeline.py       # Unit + integration smoke tests
├── e2e_task_test.py       # End-to-end 6-step test
├── Dockerfile             # coding-agency container
├── docker-compose.yml     # Full stack: coding-agency + mcp-server + openclaw
├── Makefile
├── .env.example
└── pyproject.toml
```

## Quick Start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Mac/Windows)
  or Docker Engine + Compose plugin (Linux)
- [Ollama](https://ollama.com/) running on the host with models pulled:
  ```bash
  ollama pull qwen2.5-coder:14b
  ollama pull qwen2.5:1.5b
  ```
- A FreeLLM (or any OpenAI-compatible) endpoint for planning.
- *(Optional)* A GitHub personal access token with `repo` scope for GitHub tools.

### 1. Configure

```bash
git clone https://github.com/MoriondoTommaso/agents_crew
cd agents_crew
cp .env.example .env
```

Edit `.env`:

```bash
# Required
FREELLM_BASE_URL=http://localhost:3001/v1
FREELLMAPI_KEY=your-key

# Optional — enables github_* MCP tools
GITHUB_TOKEN=ghp_...
GITHUB_OWNER=MoriondoTommaso
GITHUB_REPO=agents_crew
```

### 2. Start the stack

```bash
make up
# or:
docker compose up --build -d
```

This starts three containers:

| Container | Port | Role |
|---|---|---|
| `coding-agency` | 8000 | Plan → code pipeline |
| `mcp-server` | 8001 | Filesystem + GitHub tools |
| `openclaw` | — | Agentic harness (CLI) |

### 3. Attach to Open-Claw

```bash
make agent
# or:
docker attach openclaw
```

Give it a task:

```
Add a /api/review endpoint to server.py that sends code to the senior architect for review.
```

Open-Claw will: read the codebase → plan → create a branch → implement → commit → open a PR.

### 4. Verify

```bash
# Health checks
curl http://localhost:8000/health
curl http://localhost:8001/health

# Full E2E test (with server running)
uv run python e2e_task_test.py
```

## API Endpoints

### Coding Agency (`:8000`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Server + crew status |
| `POST` | `/v1/chat/completions` | OpenAI-compatible entry point |
| `POST` | `/api/run` | Full plan → code pipeline |
| `POST` | `/api/plan` | Planning task only |
| `POST` | `/api/code` | Coding task only |
| `GET` | `/api/models` | Active model info |
| `GET` | `/v1/models` | OAI-compatible model list |

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "coding-agency", "messages": [{"role": "user", "content": "write a binary search in Python"}]}'
```

### MCP Server (`:8001`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Server status |
| `GET` | `/tools` | MCP tool manifest |
| `POST` | `/mcp` | JSON-RPC 2.0 tool call |

#### Available MCP tools

| Tool | Description |
|---|---|
| `read_file` | Read a file from `/workspace` |
| `write_file` | Write/create a file in `/workspace` |
| `list_directory` | List directory contents |
| `delete_file` | Delete a file |
| `github_get_file` | Read a file from GitHub |
| `github_create_branch` | Create a feature branch |
| `github_create_or_update_file` | Commit a file to GitHub |
| `github_create_pr` | Open a pull request |
| `github_list_prs` | List pull requests |

```bash
# Example: read a file via MCP
curl http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "server.py"}}}'
```

## PIPELINE_MODE

| Value | Behaviour | When to use |
|---|---|---|
| `hybrid` *(default)* | SMLRouter decides per-task: planning → API, coding → local | Best cost/quality balance |
| `api` | All tasks → frontier LLM | Ollama unavailable, max quality |
| `local` | All tasks → Ollama | Offline, cost control, privacy |

## Skills

Skills are Markdown prompt packs that give Open-Claw specialised context.
See [`skills/README.md`](skills/README.md) for details.

| Skill | Description |
|---|---|
| `coding-agent` | Full coding workflow: explore → branch → implement → PR |

## Configuration

All settings via `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `PIPELINE_MODE` | `hybrid` | Routing mode |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_CODER_MODEL` | `qwen2.5-coder:14b` | Code generation model |
| `OLLAMA_ROUTER_MODEL` | `qwen2.5:1.5b` | Routing model |
| `FREELLM_BASE_URL` | `http://localhost:3001/v1` | FreeLLM endpoint |
| `FREELLMAPI_KEY` | `none` | FreeLLM API key |
| `LLM_TIMEOUT_SEC` | `600` | Hard timeout per LLM call |
| `GITHUB_TOKEN` | *(empty)* | GitHub PAT — enables GitHub MCP tools |
| `GITHUB_OWNER` | *(empty)* | Default GitHub owner |
| `GITHUB_REPO` | *(empty)* | Default GitHub repo |

## Testing

```bash
# Unit tests only (no server needed)
uv run python test_pipeline.py

# Full E2E test (stack must be running)
make up
uv run python e2e_task_test.py
```

## Harness integration (without Docker)

### Open-Claw (manual onboard)

```bash
openclaw onboard --non-interactive \
  --auth-choice custom-api-key \
  --custom-base-url "http://localhost:8000/v1" \
  --custom-model-id "coding-agency" \
  --custom-api-key "local" \
  --custom-compatibility openai

openclaw models set custom/coding-agency
```

### Aider

```bash
aider --openai-api-base http://localhost:8000/v1 --model coding-agency
```

### Continue.dev

```json
{
  "title": "Coding Agency",
  "provider": "openai",
  "model": "coding-agency",
  "apiBase": "http://localhost:8000/v1",
  "apiKey": "local"
}
```

## Development

```bash
uv run ruff check .    # lint
uv run python test_pipeline.py   # unit tests
```
