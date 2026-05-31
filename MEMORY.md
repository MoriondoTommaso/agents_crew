# Project Memory — agents_crew

Questo file viene ingerito da `bootstrap.py` nel knowledge graph al primo avvio.
Aggiornalo manualmente quando cambiano decisioni architetturali importanti.

## Decisioni architetturali

- **Motore agente**: Claude Code CLI (locale sul Mac), non OpenClaw
- **LLM routing**: LiteLLM proxy (porta 4000) — claude-opus-4-5 → FreeLLM, claude-haiku-4-5 → Ollama
- **Memoria**: Graphiti + Neo4j — knowledge graph persistente cross-sessione
- **Embedding**: Ollama `nomic-embed-text` locale — zero costo API
- **Entity extraction**: Ollama `qwen2.5:1.5b` locale — zero costo API
- **GitHub tools**: built-in Claude Code (`group:github`) — non serve server MCP custom
- **Filesystem tools**: built-in Claude Code (`group:files`) — non serve server MCP custom

## Stack rimosso (storia)

- **CrewAI + SMLRouter** — sostituito da Claude Code + LiteLLM
- **OpenClaw** — sostituito da Claude Code CLI
- **coding-agency FastAPI** — non più necessario
- **mcp-server custom** — rimpiazzato da tool built-in Claude Code

## Modelli in uso

| Modello | Provider | Uso |
|---|---|---|
| `nomic-embed-text` | Ollama locale | Embedding Graphiti |
| `qwen2.5:1.5b` | Ollama locale | Entity extraction Graphiti |
| `qwen2.5-coder:14b` | Ollama locale | Code generation (via LiteLLM haiku alias) |
| `gpt-4o` | FreeLLM | Planning, review, architettura (via LiteLLM opus/sonnet alias) |

## File chiave

- `memory/service.py` — MCP server Graphiti, 5 tool, embedding locale
- `memory/bootstrap.py` — scansione AST codebase, ingesta in Graphiti
- `litellm/config.yaml` — routing LLM ibrido
- `docker-compose.yml` — 3 container: litellm, neo4j, memory
- `CLAUDE.md` — istruzioni comportamento per Claude Code
