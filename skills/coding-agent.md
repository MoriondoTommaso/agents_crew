# Skill: Coding Agent

A complete workflow skill for implementing features, fixing bugs, and refactoring code
in the `agents_crew` repository using the MCP filesystem and GitHub tools.

---

## Context

- **Stack:** Python 3.12, FastAPI, crewAI, Ollama, Docker Compose.
- **Entry points:** `server.py` (FastAPI app), `crew.py` (crewAI crew + SMLRouter).
- **Config:** `config/agents.yaml`, `config/tasks.yaml`.
- **Tests:** `test_pipeline.py` (unit + integration), `e2e_task_test.py` (E2E).
- **MCP tools available:** `read_file`, `write_file`, `list_directory`, `delete_file`,
  `github_get_file`, `github_create_branch`, `github_create_or_update_file`,
  `github_create_pr`, `github_list_prs`.

---

## Standard Workflow

### 1. Explore
```
list_directory path="."
list_directory path="config"
read_file path="crew.py"
read_file path="server.py"
```
Understand the existing structure before writing a single line.

### 2. Plan
Think step by step:
- What is the minimal change required?
- Which files are affected?
- Are there existing tests to extend?
- Could this break anything?

### 3. Branch
```
github_create_branch branch="feat/<short-description>"
```
Always work on a feature branch, never directly on `main`.

### 4. Implement
For each file change:
```
read_file path="<file>"          # always read before writing
write_file path="<file>" content="..."
github_create_or_update_file path="<file>" content="..." message="<commit message>" branch="feat/<short-description>"
```

Commit message format: `<type>(<scope>): <description>`
Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

### 5. Test
If tests exist, note which ones cover the change and whether they need updating.
Always update `test_pipeline.py` when adding or changing behaviour in `crew.py` or `server.py`.

### 6. Pull Request
```
github_create_pr
  title="<type>(<scope>): <description>"
  head="feat/<short-description>"
  base="main"
  body="## Summary\n<what and why>\n\n## Changes\n- ...\n\n## Testing\n- ..."
```

### 7. Report
Summarise:
- What was changed and why.
- Files modified.
- PR URL.

---

## Code Quality Rules

- Type hints on all function signatures.
- Docstrings on all public classes and methods.
- Match existing code style — no reformatting unrelated lines.
- Minimal diffs — change only what is necessary.
- No commented-out code left in.
- All new behaviour covered by at least one test.

---

## Commit Message Examples

```
feat(router): add review_task step to SMLRouter
fix(crew): inject technical_design into run() inputs
refactor(server): extract _extract_code helper
test(pipeline): add unit test for keyword fallback edge cases
docs(readme): add MCP server setup section
```

---

## Common Patterns in This Codebase

### Adding a new task to the crew
1. Add agent entry in `config/agents.yaml`.
2. Add task entry in `config/tasks.yaml`.
3. Add `@agent` + `@task` methods in `crew.py`.
4. Add the task to the `@crew` method's `tasks` list.
5. Update `SMLRouter._OVERRIDES` with the new task key.

### Adding a new API endpoint
1. Add the route in `server.py`.
2. Call `crew_instance.<method>()` wrapped in `_run_with_timeout`.
3. Add the endpoint to the API table in `README.md`.
4. Add an integration test in `test_pipeline.py`.

### Changing routing logic
1. Edit `SMLRouter._OVERRIDES` for hard-coded routes.
2. Edit `_API_KEYWORDS` / `_LOCAL_KEYWORDS` for keyword fallback.
3. Add unit tests in `test_pipeline.py` → `TestSMLRouterKeywordFallback`.
