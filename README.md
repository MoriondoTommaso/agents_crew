# agents_crew

Infrastruttura per sviluppo agentico con **Claude Code** come motore,
**LiteLLM** per routing ibrido frontier/locale, e **Graphiti** per memoria
persistente cross-sessione.

Zero costi API per embedding e code generation — tutto locale via Ollama.

## Architettura

```
Claude Code CLI (Mac)
    │  ANTHROPIC_BASE_URL=http://localhost:4000
    ▼
LiteLLM proxy :4000
    ├── claude-opus-4-5 / claude-sonnet-4-5  →  FreeLLM (planning, review)
    └── claude-haiku-4-5                     →  Ollama qwen2.5-coder:14b (codice)

MCP: Memory service :8002
    └── Graphiti + Neo4j
            ├── embedding:          Ollama nomic-embed-text  (locale, gratuito)
            └── entity extraction:  Ollama qwen2.5:1.5b      (locale, gratuito)
```

## Struttura repo

```
agents_crew/
├── CLAUDE.md                  ← istruzioni comportamento per Claude Code
├── MEMORY.md                  ← seed knowledge graph (bootstrap)
├── Makefile
├── .env.example
├── .claude/
│   └── settings.json          ← MCP config (memory server)
├── litellm/
│   └── config.yaml            ← routing FreeLLM / Ollama
├── memory/
│   ├── Dockerfile
│   ├── service.py             ← Graphiti MCP, 5 tool, embedding locale
│   └── bootstrap.py           ← scansione AST codebase → graph
└── docker-compose.yml         ← 3 container: litellm, neo4j, memory
```

## Quick Start

### Prerequisiti

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Ollama](https://ollama.com/) in esecuzione sull'host
- [Claude Code CLI](https://code.claude.com/docs/en/quickstart)
- FreeLLM o qualsiasi endpoint OpenAI-compatible per i task di planning
- GitHub personal access token con scope `repo`

### 1. Installa Claude Code

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

### 2. Configura

```bash
git clone https://github.com/MoriondoTommaso/agents_crew
cd agents_crew
cp .env.example .env
```

Modifica `.env`:

```bash
FREELLM_BASE_URL=http://localhost:3001/v1
FREELLMAPI_KEY=none
GITHUB_TOKEN=ghp_...
NEO4J_PASSWORD=cambia-questa
LITELLM_MASTER_KEY=sk-local
```

### 3. Pull modelli Ollama

```bash
make models
# → nomic-embed-text   274MB   embedding memoria
# → qwen2.5:1.5b       ~1GB    entity extraction memoria
# → qwen2.5-coder:14b  ~9GB    code generation
```

### 4. Avvia lo stack

```bash
make up
```

| Container | Porta | Ruolo |
|---|---|---|
| `litellm` | 4000 | Proxy Anthropic→OpenAI, routing FreeLLM/Ollama |
| `neo4j` | 7474 / 7687 | Graph database |
| `memory` | 8002 | Graphiti MCP service |

### 5. Seed knowledge graph (solo prima volta)

```bash
make bootstrap
# → scansiona tutti i .py del repo
# → ingesta struttura nel knowledge graph
```

### 6. Lancia Claude Code

```bash
make claude
```

Claude Code si apre nel terminale, legge `CLAUDE.md` automaticamente,
carica il MCP memory da `.claude/settings.json`, e punta a LiteLLM
come backend LLM.

## Workflow agentico

Ogni sessione segue questo loop automatico:

```
1. RECALL     cerca contesto rilevante in Graphiti
2. PLAN       pianifica la minima diff necessaria
3. BRANCH     git checkout -b feat/<slug>
4. IMPLEMENT  write → bash (esegui) → leggi output → correggi → ripeti
5. PR         gh pr create
6. LOG        memory_task_log → salva nel knowledge graph
```

L'agentic loop (scrive → esegue → vede errore → corregge) è nativo
in Claude Code — non serve orchestrazione esterna.

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
# Verifica health con modelli attivi
curl http://localhost:8002/health

# Ricerca manuale nel graph
curl -s -X POST http://localhost:8002/mcp/memory_recall \
  -H 'Content-Type: application/json' \
  -d '{"query": "LiteLLM routing config", "limit": 5}'
```

## LiteLLM routing

Claude Code usa nomi modello Anthropic — LiteLLM li mappa ai backend reali:

| Modello Claude Code | Backend | Uso consigliato |
|---|---|---|
| `claude-opus-4-5` | FreeLLM → gpt-4o | Planning, architettura, review |
| `claude-sonnet-4-5` | FreeLLM → gpt-4o | Task medi |
| `claude-haiku-4-5` | Ollama → qwen2.5-coder:14b | Code generation, task rapidi |

Per cambiare modello al volo dentro Claude Code: `/model`

## Variabili d'ambiente

| Variabile | Default | Descrizione |
|---|---|---|
| `FREELLM_BASE_URL` | `http://localhost:3001/v1` | Endpoint FreeLLM |
| `FREELLMAPI_KEY` | `none` | API key FreeLLM |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Endpoint Ollama |
| `LITELLM_MASTER_KEY` | `sk-local` | Chiave proxy LiteLLM (= ANTHROPIC_API_KEY per Claude Code) |
| `NEO4J_PASSWORD` | `changeme` | Password Neo4j |
| `GITHUB_TOKEN` | — | GitHub PAT scope `repo` |
| `GRAPHITI_EMBED_MODEL` | `nomic-embed-text` | Modello embedding locale |
| `GRAPHITI_LLM_MODEL` | `qwen2.5:1.5b` | Modello entity extraction locale |

## Comandi Makefile

```bash
make up          # avvia stack Docker
make down        # ferma stack
make bootstrap   # pull modelli + seed knowledge graph (prima volta)
make models      # pull tutti i modelli Ollama
make claude      # lancia Claude Code puntato a LiteLLM
make logs        # segui tutti i log
make clean       # ferma + distrugge volumi (reset completo)
```

## Comandi utili dentro Claude Code

| Comando | Azione |
|---|---|
| `Shift+Tab` | Plan mode — pianifica senza eseguire |
| `/model` | Cambia modello al volo |
| `/resume` | Riprende sessione precedente |
| `/exit` | Esce |

```bash
# Riprende l'ultima sessione
make claude -- --continue
```

## Debug

```bash
# Log di tutti i container
make logs

# Health check singoli servizi
curl http://localhost:4000/health   # LiteLLM
curl http://localhost:8002/health   # Memory

# Neo4j Browser (graph visuale)
open http://localhost:7474
# login: neo4j / <NEO4J_PASSWORD da .env>

# Reset completo memoria
make clean && make up && make bootstrap
```

## Lavorare su repo diverse

Claude Code vede solo la cartella in cui viene lanciato.
Lo stack Docker (LiteLLM + memory) è condiviso tra tutti i progetti.

```bash
# Progetto diverso
cd ~/progetti/altro-repo
ANTHROPIC_BASE_URL=http://localhost:4000 \
ANTHROPIC_API_KEY=sk-local \
claude
```

La memoria Graphiti è condivisa tra sessioni e repo.
Per isolare la memoria per progetto, modifica `memory/service.py`
aggiungendo un `group_id` distinto per repo.

## Skills

File Markdown in `skills/` con workflow e context specializzati.
Caricali esplicitamente nel prompt:

```
> Leggi ./skills/coding-agent.md e segui il workflow per aggiungere...
```

| Skill | Descrizione |
|---|---|
| `coding-agent.md` | Workflow completo: recall → branch → implement → PR → log |
| `memory-agent.md` | Come usare i tool Graphiti, esempi curl, reset |
