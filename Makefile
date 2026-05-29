.PHONY: up down build logs agent bootstrap test check-deps clean

# ── Docker Compose ────────────────────────────────────────────────────────────
up: check-deps
	docker compose up --build -d
	@echo ""
	@echo "Stack is up."
	@echo "  Run 'make bootstrap' to seed the knowledge graph (first time only)."
	@echo "  Run 'make agent'     to attach to Open-Claw."

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

# ── Agent ─────────────────────────────────────────────────────────────────────
agent:
	docker attach openclaw

# ── Memory bootstrap (run once after first make up) ───────────────────────────
bootstrap:
	@echo "Seeding knowledge graph from codebase ..."
	docker compose exec memory python bootstrap.py
	@echo "Bootstrap complete. Graph is ready."

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	uv run python test_pipeline.py

# ── Pre-flight dependency check ───────────────────────────────────────────────
check-deps:
	@echo "Checking host dependencies ..."
	@OLLAMA_URL=$${OLLAMA_BASE_URL:-http://localhost:11434}; \
		curl -sf "$$OLLAMA_URL" > /dev/null 2>&1 \
		&& echo "  ✓ Ollama reachable at $$OLLAMA_URL" \
		|| (echo "  ✗ Ollama NOT reachable at $$OLLAMA_URL" && \
		    echo "    → run: ollama serve" && exit 1)
	@FREELLM_URL=$${FREELLM_BASE_URL:-http://localhost:3001}; \
		curl -sf "$$FREELLM_URL" > /dev/null 2>&1 \
		&& echo "  ✓ FreeLLM reachable at $$FREELLM_URL" \
		|| (echo "  ✗ FreeLLM NOT reachable at $$FREELLM_URL" && \
		    echo "    → see README: how to start FreeLLM" && exit 1)
	@echo "All dependencies OK."

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	docker image prune -f
