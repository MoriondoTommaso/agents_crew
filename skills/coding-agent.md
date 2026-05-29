# Skill: Coding Agent

Workflow completo per implementare feature, fixare bug e fare refactoring
nella repo `agents_crew` usando Claude Code con tool built-in e memory MCP.

## Stack

- **Runtime**: Claude Code CLI sul Mac
- **LLM proxy**: LiteLLM porta 4000 (FreeLLM / Ollama)
- **Memoria**: Graphiti MCP a `http://localhost:8002`
- **Tool built-in**: file, bash, git, GitHub, web

## Workflow

### 1. Recall
```
memory_recall(query=<descrizione task>)
memory_get_context(entity=<file principale>)
```

### 2. Plan
- Minima diff necessaria
- File coinvolti
- Test da aggiornare
- Rischi di regressione

### 3. Branch
```bash
git checkout -b feat/<slug>
```

### 4. Implement
```bash
# Leggi prima di scrivere
cat <file>
# Modifica
# Esegui e verifica
python <script> || pytest tests/
```

### 5. PR
```bash
gh pr create --title "type(scope): desc" --body "## Summary\n..."
```

### 6. Log
```
memory_task_log(task, status="completed", files_modified=[...], decisions=[...])
```

## Commit format

`feat` `fix` `refactor` `test` `docs` `chore` — `type(scope): description`

## Aggiornare il memory service

1. Modifica `memory/service.py`
2. `docker compose build memory && docker compose restart memory`
3. Verifica: `curl http://localhost:8002/health`

## Aggiornare il LiteLLM routing

1. Modifica `litellm/config.yaml`
2. `docker compose restart litellm`
3. Verifica: `curl http://localhost:4000/health`
