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

# 3. Pull models
ollama pull qwen2.5-coder:12b
ollama pull qwen2.5:1.5b

# 4. Start
uv run uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Server is ready at `http://localhost:8000`. Check `GET /health`.

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

## Harness Integration

### Open-Claw

Add a provider in your Open-Claw config:

```json
{
  "provider": "openai",
  "base_url": "http://localhost:8000/v1",
  "api_key": "local",
  "model": "coding-agency"
}
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

## Configuration

All settings via `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `FREELLM_BASE_URL` | `http://localhost:3001/v1` | FreeLLM endpoint |
| `FREELLMAPI_KEY` | `none` | FreeLLM API key |
| `LLM_TIMEOUT_SEC` | `600` | Hard timeout per request (align with harness timeout) |

> **Tip:** If your harness has a 120s timeout, set `LLM_TIMEOUT_SEC=110` so the server returns a clean 504 before the harness drops the connection.

## Development

```bash
# Run tests (no Ollama or API needed — all mocked)
uv run pytest tests/ -v

# Lint
uv run ruff check .
```

## Project Structure

```
agents_crew/
├── server.py          # FastAPI app + OpenAI-compatible endpoints
├── crew.py            # CodingAgencyCrew + SMLRouter
├── config/
│   ├── agents.yaml    # Agent definitions
│   └── tasks.yaml     # Task definitions
├── tests/
│   ├── test_router.py # SMLRouter unit tests
│   └── test_server.py # FastAPI endpoint tests
├── .env.example
└── pyproject.toml
```
