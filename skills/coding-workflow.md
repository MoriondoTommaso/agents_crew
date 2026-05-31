# Coding Workflow Skill

This skill defines the standard end-to-end workflow for every coding task.
Load it at the start of any development session.

## Core Loop

```
RECALL → PLAN → BRANCH → IMPLEMENT → TEST → PR → LOG
```

### 1. RECALL

Always start by pulling relevant context from memory:

```
memory_recall(query="<brief task summary>")
memory_get_context(entity="<main file or module>")
```

Do NOT skip this step — memory contains previous decisions, known bugs, and
architectural constraints that will save you from repeating mistakes.

### 2. PLAN

- Write 3–5 bullet points describing the minimal diff needed
- Confirm: does this touch anything flagged in memory as sensitive or broken?
- Prefer the smallest change that solves the problem

### 3. BRANCH

```bash
git checkout -b <type>/<short-slug>
```

Branch naming convention:

| Prefix | When to use |
|---|---|
| `feat/` | New feature or capability |
| `fix/` | Bug fix |
| `chore/` | Tooling, deps, hygiene |
| `refactor/` | Code restructure with no behaviour change |
| `docs/` | Documentation only |

**Never commit directly to `main`.**

### 4. IMPLEMENT

```
read file → edit → bash (run/test) → read output → fix → repeat
```

- Use `bash` to actually run the code and read real output — never assume it works
- Add type hints to every new function signature
- Keep diffs minimal: don't reformat lines you didn't change
- Log non-obvious decisions as episodes:
  ```
  memory_add_episode(name="decision:<slug>", content="Chose X over Y because...")
  ```

### 5. TEST

```bash
# Run test suite before opening PR
pytest tests/ -v --tb=short

# Lint
ruff check <changed files>
```

If tests don't exist for the change, write them.

### 6. PR

```bash
gh pr create --title "type(scope): description" --body "<what and why>"
```

Commit format: `type(scope): description`
Examples:
- `fix(memory): pass group_id on all search calls`
- `feat(bootstrap): support .ts and .go files`
- `chore(ci): upgrade actions to avoid Node.js 20 deprecation`

### 7. LOG

Always close the loop — log the completed task to memory:

```
memory_task_log(
  task="<what you did>",
  status="completed",           # or "failed"
  files_modified=["path/to/file.py"],
  decisions=["key decision made"],
  notes="<anything useful for next session>"
)
```

## Absolute Rules

- Never skip RECALL or LOG
- Never commit to `main`
- One branch per task
- Run bash to verify before opening PR
- Minimal diffs only
