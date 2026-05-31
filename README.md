# agents_crew

[![CI](https://github.com/MoriondoTommaso/agents_crew/actions/workflows/ci.yml/badge.svg)](https://github.com/MoriondoTommaso/agents_crew/actions/workflows/ci.yml)

Infrastruttura per sviluppo agentico con **OpenCode** come harness,
**FreeLLMAPI** per routing ibrido frontier/locale, e **Graphiti** per
memoria persistente cross-sessione.

Zero costi API per embedding — tutto locale via Ollama.

## Architettura

```
OpenCode CLI (host Mac/Linux)
    │  OPENAI_BASE_URL=http://localhost:3001/v1
    ▼
FreeLLMAPI :3001  (o qualsiasi endpoint OpenAI-compatible)
    ├── planning / architettura / review  →  cloud (Gemini, GPT-4o, Claude…)
    └── code generation                  →  Ollama locale (opzionale)

MCP: Memory service :8002  (Docker)
    └── Graphiti + Neo4j
            ├── embedding:          Ollama nomic-embed-text  (locale, gratuito)
            └── entity extraction:  FreeLLMAPI / Ollama      (configurabile)
```

## Struttura repo

```
agents_crew/
├── AGENTS.md                  ← istruzioni comportamento per OpenCode
├── MEMORY.md                  ← seed knowledge graph (bootstrap)
├── Makefile                   ← comandi up / bootstrap / opencode / models
├── .env.example               ← template variabili d'ambiente
├── .opencode/
│   └── config.json            ← MCP config per OpenCode
├── memory/
│   ├── Dockerfile
│   ├── service.py             ← Graphiti MCP, 5 tool, porta 8002
│   └── bootstrap.py           ← scansione codebase → knowledge graph
├── docker-compose.yml         ← 2 container: neo4j, memory
└── tests/
    └── test_memory_service.py ← 13 test, zero dipendenze esterne
```

## Quick Start

### Prerequisiti

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Ollama](https://ollama.com/) in esecuzione sull'host
- [OpenCode CLI](https://opencode.ai) — `npm i -g opencode-ai`
- FreeLLMAPI o qualsiasi endpoint OpenAI-compatible
- GitHub personal access token con scope `repo`

### 1. Clona e configura

```bash
git clone https://github.com/MoriondoTommaso/agents_crew
cd agents_crew
cp .env.example .env
```

Modifica `.env` con il tuo endpoint e credenziali:

```bash
# LLM endpoint (FreeLLMAPI, OpenRouter, Groq, Ollama puro...)
OPENAI_BASE_URL=http://localhost:3001/v1
OPENAI_API_KEY=la-tua-chiave

# Memory service
NEO4J_PASSWORD=cambia-questa
GRAPHITI_LLM_MODEL=auto

# GitHub
GITHUB_TOKEN=ghp_...
```

### 2. Pull modelli Ollama

```bash
make models
# → nomic-embed-text   274MB   embedding memoria
# → qwen2.5:1.5b       ~1GB    entity extraction memoria (opzionale)
```

### 3. Avvia lo stack

```bash
make up
```

| Container | Porta | Ruolo |
|---|---|---|
| `neo4j` | 7474 / 7687 | Graph database |
| `memory` | 8002 | Graphiti MCP service |

### 4. Seed knowledge graph (solo prima volta)

```bash
make bootstrap
# scansiona .py del repo e ingesta struttura nel knowledge graph
```

### 5. Lancia OpenCode

```bash
make opencode
# oppure direttamente:
opencode
```

OpenCode legge `AGENTS.md` automaticamente, carica il MCP memory
da `.opencode/config.json`, e usa FreeLLMAPI come backend LLM.

## Workflow agentico

Ogni sessione segue questo loop:

```
1. RECALL     cerca contesto rilevante in Graphiti
2. PLAN       pianifica la minima diff necessaria
3. BRANCH     git checkout -b feat/<slug>  (mai su main)
4. IMPLEMENT  write → bash → leggi output → correggi → ripeti
5. PR         gh pr create
6. LOG        memory_task_log → salva nel knowledge graph
```

## Memory MCP — tool disponibili

Endpoint base: `http://localhost:8002`

| Tool | Endpoint | Descrizione |
|---|---|---|
| `memory_recall` | `POST /mcp/memory_recall` | Ricerca semantica nel graph |
| `memory_add_episode` | `POST /mcp/memory_add_episode` | Ingesta un episodio |
| `memory_get_context` | `POST /mcp/memory_get_context` | Contesto per entità/file |
| `memory_task_log` | `POST /mcp/memory_task_log` | Log task completato/fallito |
| `memory_snapshot` | `GET /mcp/memory_snapshot` | Export graph (debug) |

```bash
# Health check
curl http://localhost:8002/health

# Ricerca manuale
curl -s -X POST http://localhost:8002/mcp/memory_recall \
  -H 'Content-Type: application/json' \
  -d '{"query": "routing config", "limit": 5}'
```

## Cambio endpoint LLM (zero sbatti)

Basta cambiare due righe in `.env` — nessun riavvio di container:

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

# Ollama locale puro
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
```

## Variabili d'ambiente

| Variabile | Default | Descrizione |
|---|---|---|
| `OPENAI_BASE_URL` | `http://localhost:3001/v1` | Endpoint LLM (OpenAI-compatible) |
| `OPENAI_API_KEY` | — | API key provider |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Endpoint Ollama |
| `NEO4J_PASSWORD` | `changeme` | ⚠️ Cambiare prima di usare |
| `GITHUB_TOKEN` | — | GitHub PAT scope `repo` |
| `GRAPHITI_LLM_MODEL` | `auto` | Modello per entity extraction |
| `GRAPHITI_EMBED_MODEL` | `nomic-embed-text` | Modello embedding locale |
| `GRAPHITI_GROUP_ID` | `agents` | Namespace memoria (un valore per progetto) |

## Comandi Makefile

```bash
make up          # avvia stack Docker (neo4j + memory)
make down        # ferma stack
make bootstrap   # seed knowledge graph dal codebase (prima volta)
make models      # pull modelli Ollama
make opencode    # lancia OpenCode
make logs        # segui tutti i log
make clean       # ferma + distrugge volumi (reset completo)
```

## Sviluppo e test

```bash
# Installa dipendenze dev
pip install -e ".[dev]" httpx

# Esegui test (zero dipendenze esterne, tutto mockato)
pytest tests/test_memory_service.py -v

# Lint
ruff check memory/ tests/test_memory_service.py
```

La CI gira automaticamente su ogni push e PR — vedi badge sopra.

## Debug

```bash
# Log container
make logs

# Health singoli servizi
curl http://localhost:8002/health

# Neo4j Browser (graph visuale)
open http://localhost:7474
# login: neo4j / <NEO4J_PASSWORD da .env>

# Reset completo memoria
make clean && make up && make bootstrap
```

## Lavorare su repo diverse

Lo stack Docker (neo4j + memory) è condiviso. OpenCode vede solo la cartella
in cui viene lanciato. Per isolare la memoria per progetto:

```bash
# In .env del progetto B
GRAPHITI_GROUP_ID=progetto-b
# poi riavvia: make down && make up
```
