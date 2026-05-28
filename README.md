# Hybrid Coding Agency

A lightweight **SML router** that exposes an OpenAI-compatible API endpoint backed by two LLMs:

| Task | Model | Where |
|---|---|---|
| Planning & architecture | Frontier LLM (via FreeLLM) | API |
| Code generation | `qwen2.5-coder:12b` | Local (Ollama) |
| Routing decisions | `qwen2.5:1.5b` | Local (Ollama) |

Designed to sit behind an agentic harness (Open-Claw, Aider, Continue.dev). The harness brings context, tools, and iteration — this router brings **cost efficiency**: zero API spend on code generation.

## Architecture

```
Harness (Open-Claw / Aider)
        ↓  POST /v1/chat/completions
Hybrid Coding Agency (this server)
        ↓ plan          ↓ code
   FreeLLM API     Ollama local
  (gpt-4o / etc)  (qwen2.5-coder:12b)
```

Single-pass pipeline: `plan → code → return`. No internal review loop — iteration is delegated to the harness via real code execution tools.

## Quick Start

**Prerequisites:** [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com/) with `qwen2.5-coder:12b` and `qwen2.5:1.5b` pulled.

```bash
# 1. Clone and install
git clone https://github.com/MoriondoTommaso/agents_crew
cd agents_crew
uv sync

# 2. Configure
cp .env.example .env
# edit .env with your FreeLLM endpoint and key

# 3. Pull Ollama models (required only for PIPELINE_MODE=hybrid or local)
ollama pull qwen2.5-coder:12b
ollama pull qwen2.5:1.5b

# 4. Start
uv run uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Server is ready at `http://localhost:8000`. Check `GET /health`.

## Docker

The fastest way to get a reproducible, isolated environment.  
Ollama keeps running on your host — the container reaches it via `host.docker.internal`.

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Mac/Windows) or Docker Engine + Compose plugin (Linux), plus Ollama running on the host with the models already pulled.

```bash
# 1. Configure
cp .env.example .env
# edit .env — OLLAMA_BASE_URL will be overridden automatically by docker-compose

# 2. Build and start
docker compose up --build

# Detached (background)
docker compose up --build -d
docker compose logs -f
```

Server is available at `http://localhost:8000`.

### Useful commands

```bash
docker compose up -d               # start (detached)
docker compose down                # stop + remove container
docker compose restart             # restart without rebuild
docker compose build --no-cache    # force full rebuild (e.g. after pyproject.toml change)
docker ps                          # check container status (shows healthy/starting)
curl http://localhost:8000/health  # verify server is up
```

### How host networking works

| Platform | `host.docker.internal` |
|---|---|
| Docker Desktop (Mac / Windows) | Resolved automatically |
| Linux (Docker Engine) | Added via `extra_hosts: host.docker.internal:host-gateway` in `docker-compose.yml` |

If you run Ollama on a different machine or port, override in `.env`:
```bash
OLLAMA_BASE_URL=http://192.168.1.42:11434
```

> **Note:** The `environment` block in `docker-compose.yml` sets `OLLAMA_BASE_URL` to `host.docker.internal` by default, overriding whatever is in `.env`. This is intentional — `localhost` inside a container points to the container itself, not the host.

## API Endpoints

### OpenAI-compatible

```
POST /v1/chat/completions
```

Drop-in replacement for any OpenAI client. Point your harness at `http://localhost:8000` and set the model to one of:

| Model | Behaviour |
|---|---|
| `coding-agency` | Full plan → code pipeline (default) |
| `coding-plan` | Planning only (frontier LLM) |
| `coding-code` | Code generation only (local LLM) |

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "coding-agency", "messages": [{"role": "user", "content": "write a binary search in Python"}]}'
```

### Native endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Server + crew status |
| `POST` | `/api/run` | Full pipeline |
| `POST` | `/api/plan` | Planning task only |
| `POST` | `/api/code` | Coding task only |
| `GET` | `/api/models` | Active model info |
| `GET` | `/v1/models` | OAI-compatible model list |

## PIPELINE_MODE

Set `PIPELINE_MODE` in your `.env` to control routing behaviour without touching code:

| Value | Behaviour | When to use |
|---|---|---|
| `hybrid` *(default)* | SMLRouter decides per-task: planning/review → API, coding → local | Normal use — best cost/quality balance |
| `api` | All tasks → frontier LLM, Ollama not needed | Ollama unavailable, max quality needed |
| `local` | All tasks → Ollama, zero API spend | Offline work, cost control, privacy |

```bash
# In .env:
PIPELINE_MODE=hybrid   # default
PIPELINE_MODE=api      # no Ollama needed
PIPELINE_MODE=local    # no API needed
```

### SMLRouter keyword fallback

When the router LLM (`qwen2.5:1.5b`) is unreachable, the router falls back to keyword matching.  
Priority order — **first match wins**:

1. **API keywords** (review, plan, design, architect, analyse, audit, evaluate) → `api`
2. **Local keywords** (write, implement, code, develop, build, generate, create) → `local`
3. **Default** → `api`

API keywords are checked first, so `"review this Python code"` correctly routes to `api` despite containing the word `code`.

## Harness Integration

### Open-Claw

```bash
openclaw onboard --non-interactive \
  --auth-choice custom-api-key \
  --custom-base-url "http://localhost:8000/v1" \
  --custom-model-id "coding-agency" \
  --custom-api-key "local" \
  --custom-compatibility openai

openclaw models set custom/coding-agency
```

Or manually in `~/.openclaw/openclaw.json` under `models.providers` — see the [OpenClaw docs](https://docs.openclaw.ai/providers/models).

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

## Configuration

All settings via `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `PIPELINE_MODE` | `hybrid` | Routing mode: `hybrid` / `api` / `local` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `FREELLM_BASE_URL` | `http://localhost:3001/v1` | FreeLLM endpoint |
| `FREELLMAPI_KEY` | `none` | FreeLLM API key |
| `LLM_TIMEOUT_SEC` | `600` | Hard timeout per request (align with harness timeout) |

> **Tip:** If your harness has a 120s timeout, set `LLM_TIMEOUT_SEC=110` so the server returns a clean 504 before the harness drops the connection.

## Testing

```bash
# Unit tests only (no server or Ollama needed)
uv run python test_pipeline.py

# Full integration tests (start the server first)
make up   # or: uv run uvicorn server:app --reload
uv run python test_pipeline.py

# Quick mode — only health check + full pipeline
QUICK=1 uv run python test_pipeline.py

# Against a custom host
BASE_URL=http://192.168.1.10:8000 uv run python test_pipeline.py
```

### What the smoke test covers

| Test | Requires server | What it checks |
|---|---|---|
| routing keyword fallback | ✗ | `_keyword_fallback()` routes 6 tasks correctly |
| PIPELINE_MODE env parsing | ✗ | `from_env()` handles all values + invalid fallback |
| health check | ✓ | `GET /health` returns `crew_ready: true` |
| model list | ✓ | `/v1/models` lists all 3 model ids |
| plan only | ✓ | `/api/plan` returns a non-trivial plan |
| code only | ✓ | `/api/code` returns Python with `def` |
| full pipeline | ✓ | `/api/run` returns result + elapsed + request_id |
| OAI full / plan / code | ✓ | OAI-compat format correct for each model |
| streaming | ✓ | SSE chunks assemble to non-empty string |

## Development

```bash
# Lint
uv run ruff check .
```

## Project Structure

```
agents_crew/
├── server.py           # FastAPI app + OpenAI-compatible endpoints
├── crew.py             # CodingAgencyCrew + SMLRouter
├── test_pipeline.py    # End-to-end smoke test (unit + integration)
├── config/
│   ├── agents.yaml     # Agent definitions
│   └── tasks.yaml      # Task definitions
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── pyproject.toml
```
