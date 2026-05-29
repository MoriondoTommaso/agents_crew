# Skill: Memory Agent

Come usare il knowledge graph Graphiti per mantenere contesto cross-sessione.

## Endpoint base

`http://localhost:8002`

## Tool disponibili

| Tool | Endpoint | Parametri |
|---|---|---|
| `memory_recall` | `POST /mcp/memory_recall` | `query: str`, `limit: int` |
| `memory_add_episode` | `POST /mcp/memory_add_episode` | `name: str`, `content: str`, `source: str` |
| `memory_get_context` | `POST /mcp/memory_get_context` | `entity: str` |
| `memory_task_log` | `POST /mcp/memory_task_log` | `task`, `status`, `files_modified`, `decisions`, `notes` |
| `memory_snapshot` | `GET /mcp/memory_snapshot` | — |

## Quando usare cosa

- **Inizio sessione**: `memory_recall` con il topic del task
- **Inizio task su file specifico**: `memory_get_context(entity="nome_file.py")`
- **Decisione architetturale**: `memory_add_episode` con spiegazione
- **Fine task**: `memory_task_log` con files modificati e decisioni
- **Debug**: `memory_snapshot` o Neo4j Browser su `http://localhost:7474`

## Esempi curl

```bash
# Recall
curl -s -X POST http://localhost:8002/mcp/memory_recall \
  -H 'Content-Type: application/json' \
  -d '{"query": "LiteLLM routing config", "limit": 5}'

# Add episode
curl -s -X POST http://localhost:8002/mcp/memory_add_episode \
  -H 'Content-Type: application/json' \
  -d '{"name": "decision: use nomic-embed-text", "content": "Scelto nomic-embed-text per embedding locale. Zero costo API, 274MB, qualità sufficiente per codebase search.", "source": "agent"}'

# Task log
curl -s -X POST http://localhost:8002/mcp/memory_task_log \
  -H 'Content-Type: application/json' \
  -d '{"task": "fix memory embedder", "status": "completed", "files_modified": ["memory/service.py"], "decisions": ["OllamaEmbedder custom class"], "notes": ""}'
```

## Reset completo memoria

```bash
make clean   # distrugge volume neo4j-data
make up
make bootstrap
```
