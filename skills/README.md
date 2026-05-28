# Skills

Skills are reusable prompt packs that give Open-Claw specialised knowledge for a specific domain.
Each skill is a single Markdown file that the agent loads as additional context.

## How to use a skill

Paste the skill contents into your conversation with Open-Claw, or reference it in your system prompt:

```
/load skills/coding-agent.md
```

Or prefix your task with the skill name:

```
[skill: coding-agent] Refactor the SMLRouter to support a review_task step.
```

## Available skills

| Skill | File | Description |
|-------|------|-------------|
| Coding Agent | `coding-agent.md` | Full-stack coding workflow: read → branch → implement → PR |

## Adding a new skill

1. Create `skills/your-skill-name.md`.
2. Follow the structure in `coding-agent.md` (Context, Workflow, Rules, Examples).
3. Add a row to the table above.
