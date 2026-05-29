# Skill: memory-agent

How to use the Graphiti knowledge-graph memory effectively.

## When to RECALL

Call `memory_recall` at the **start of every task**, before reading any file:

```
memory_recall(query="<one-sentence task description>")
```

This returns relevant facts from past sessions: previous decisions on related files, known bugs, architectural choices, and completed tasks.

Also call `memory_get_context` for the primary file you will touch:

```
memory_get_context(entity="crew.py")
```

## When to ADD EPISODES

Call `memory_add_episode` when you:
- Make a non-obvious architectural decision
- Discover a constraint or bug that is not obvious from reading the code
- Learn something about how a module is used by others

Keep it short (2-5 sentences). Bad episode: "I edited crew.py". Good episode: "SMLRouter keyword fallback now checks API keywords first (review, plan, design) before local keywords (code, implement, build). This prevents prompts like 'Review this code' from being misrouted to local LLM."

## When to LOG

Always call `memory_task_log` as the **last step** of every task:

```
memory_task_log(
    task="Add rate limiting to /api endpoint",
    status="completed",
    files_modified=["server.py", "requirements.txt"],
    decisions=["Used slowapi library for FastAPI compatibility"],
    notes="Rate limit set to 60 req/min per IP"
)
```

This is what makes the agent smarter over time. If you skip this, the next session starts from zero.

## Neo4j Browser

The graph is inspectable at http://localhost:7474 (user: neo4j, password from .env).

Useful Cypher queries:
```cypher
// All episodes
MATCH (e:Episode) RETURN e.name, e.created_at ORDER BY e.created_at DESC LIMIT 20

// All entities
MATCH (n:Entity) RETURN n.name, labels(n) LIMIT 50

// Relations around a file
MATCH (n {name: 'crew.py'})-[r]-(m) RETURN n, r, m
```
