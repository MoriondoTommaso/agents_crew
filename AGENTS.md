# Coding Agent — Istruzioni per OpenCode

Sei un agente di sviluppo software esperto. Hai accesso a tool per filesystem, git, GitHub, shell e memoria persistente.

## Stack del progetto

| Componente | Dettaglio |
|---|---|
| Memoria persistente | Graphiti + Neo4j su `http://localhost:8002` |
| LLM proxy | FreeLLMAPI su `http://localhost:3001/v1` (o altro endpoint via OPENAI_BASE_URL) |
| Embedding/estrazione entità | Ollama locale (`nomic-embed-text`, `qwen2.5:1.5b`) |
| Repo default | `MoriondoTommaso/agents_crew` |

## Tool disponibili

### Memory MCP (`http://localhost:8002`)
| Tool | Quando usarlo |
|---|---|
| `memory_recall` | **Sempre prima** — cerca contesto rilevante prima di iniziare |
| `memory_get_context` | Per dettagli su un file o classe specifica |
| `memory_add_episode` | Dopo ogni decisione significativa |
| `memory_task_log` | **Sempre dopo** — logga il task completato |
| `memory_snapshot` | Debug — esporta tutto il graph |

### Built-in OpenCode
- **File**: `read`, `write`, `edit`, `list_directory`
- **Shell**: `bash` — esegui, testa, vedi output, correggi
- **Git/GitHub**: branch, commit, PR
- **Web**: ricerca documentazione

## Workflow standard (da seguire ogni volta)

```
1. RECALL     memory_recall(query=<riassunto task>)
              memory_get_context(entity=<file principale>)

2. PLAN       3-5 bullet points prima di scrivere codice
              Minima diff — cambia solo quello necessario

3. BRANCH     git checkout -b feat/<slug>  (mai lavorare su main)

4. IMPLEMENT  read → edit → test
              bash per eseguire e verificare output reale
              memory_add_episode() per ogni decisione non ovvia

5. PR         gh pr create con titolo e descrizione chiari

6. LOG        memory_task_log(task, status, files_modified, decisions)
```

## Regole assolute

- **Mai committare su `main` direttamente**
- **Mai saltare RECALL o LOG** — la memoria vale solo se aggiornata
- Un branch per task: `feat/`, `fix/`, `chore/`, `refactor/`
- Commit format: `type(scope): description` (es. `fix(memory): use ollama embedder`)
- Type hints su tutte le funzioni
- Testa il codice con `bash` prima di aprire PR
- Diff minimali — non riformattare righe non toccate

## Struttura repo

```
agents_crew/
├── AGENTS.md              ← questo file (istruzioni agente)
├── MEMORY.md              ← seed knowledge graph al bootstrap
├── Makefile               ← up / bootstrap / opencode / freellm / models
├── .env.example           ← copia in .env e compila
├── .opencode/
│   └── config.json        ← MCP config per OpenCode
├── memory/
│   ├── Dockerfile
│   ├── service.py         ← Graphiti MCP (embedding Ollama locale)
│   └── bootstrap.py       ← scansione AST codebase → graph
└── docker-compose.yml     ← 2 container: neo4j, memory
```

## Modelli Ollama richiesti

```bash
ollama pull nomic-embed-text   # embedding (~274MB) — per Graphiti
ollama pull qwen2.5:1.5b       # entity extraction (~1GB) — per Graphiti
```

## Quick reference comandi

```bash
make up          # avvia stack Docker (neo4j + memory)
make bootstrap   # popola knowledge graph dal codebase (solo prima volta)
make opencode    # lancia OpenCode puntato a FreeLLMAPI (o altro endpoint)
make freellm     # avvia FreeLLMAPI server su :3001
make models      # pull modelli Ollama necessari
make logs        # segui tutti i log
make down        # ferma tutto
make clean       # ferma + distrugge volumi (reset completo)
```

## Cambio endpoint LLM (zero sbatti)

Per usare un provider diverso basta cambiare due righe in `.env`:

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
