.PHONY: up down build logs claude bootstrap check-deps clean models

# ── Docker stack ──────────────────────────────────────────────────────────────
up: check-deps
	docker compose up --build -d
	@echo ""
	@echo "Stack is up:  LiteLLM :4000  |  Memory :8002  |  Neo4j :7474"
	@echo "Run 'make bootstrap' to seed the knowledge graph (first time only)."
	@echo "Run 'make claude'    to start Claude Code."

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f

# ── Claude Code ───────────────────────────────────────────────────────────────
claude:
	@echo "Starting Claude Code (ANTHROPIC_BASE_URL=http://localhost:4000) ..."
	ANTHROPIC_BASE_URL=http://localhost:4000 \
	ANTHROPIC_API_KEY=$${LITELLM_MASTER_KEY:-sk-local} \
	claude

# ── Memory bootstrap (run once after first make up) ───────────────────────────
bootstrap:
	@echo "Pulling Ollama models required by memory service ..."
	ollama pull nomic-embed-text
	ollama pull qwen2.5:1.5b
	@echo "Seeding knowledge graph from codebase ..."
	docker compose exec memory python bootstrap.py
	@echo "Bootstrap complete."

# ── Ollama model management ───────────────────────────────────────────────────
models:
	@echo "Pulling all required Ollama models ..."
	ollama pull nomic-embed-text
	ollama pull qwen2.5:1.5b
	ollama pull qwen2.5-coder:14b
	@echo "All models ready."

# ── Pre-flight dependency check ───────────────────────────────────────────────
check-deps:
	@echo "Checking host dependencies ..."
	@command -v claude > /dev/null 2>&1 \
		&& echo "  ✓ Claude Code installed" \
		|| echo "  ⚠  Claude Code not found — install from https://code.claude.com"
	@OLLAMA_URL=$${OLLAMA_BASE_URL:-http://localhost:11434}; \
		curl -sf "$$OLLAMA_URL" > /dev/null 2>&1 \
		&& echo "  ✓ Ollama reachable at $$OLLAMA_URL" \
		|| (echo "  ✗ Ollama NOT reachable — run: ollama serve" && exit 1)
	@echo "  ✓ All deps OK."

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	docker image prune -f
