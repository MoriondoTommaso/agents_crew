.PHONY: up down build logs opencode freellm bootstrap check-deps clean models

# ── Docker stack ──────────────────────────────────────────────────────────────
up: check-deps
	docker compose up --build -d
	@echo ""
	@echo "Stack is up:  Memory :8002  |  Neo4j :7474"
	@echo "Run 'make bootstrap' to seed the knowledge graph (first time only)."
	@echo "Run 'make opencode'  to start the OpenCode agent."
	@echo "Run 'make freellm'   to start the FreeLLMAPI server (if not running)."

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f

# ── OpenCode agent ────────────────────────────────────────────────────────────
# OpenCode reads OPENAI_BASE_URL + OPENAI_API_KEY from env.
# Swap those two vars in .env to use any other OpenAI-compatible endpoint.
opencode:
	@echo "Starting OpenCode (OPENAI_BASE_URL=$${OPENAI_BASE_URL:-http://localhost:3001/v1}) ..."
	@export $$(grep -v '^#' .env | xargs) 2>/dev/null; \
		OPENAI_BASE_URL=$${OPENAI_BASE_URL:-http://localhost:3001/v1} \
		OPENAI_API_KEY=$${OPENAI_API_KEY} \
		opencode

# ── FreeLLMAPI server (runs on host, not in Docker) ───────────────────────────
freellm:
	@echo "Starting FreeLLMAPI server on :3001 ..."
	@echo "Dashboard: http://localhost:5173"
	@if [ ! -d "../freellmapi" ]; then \
		echo "  Cloning freellmapi ..."; \
		git clone https://github.com/tashfeenahmed/freellmapi ../freellmapi; \
		cd ../freellmapi && npm install; \
	fi
	cd ../freellmapi && npm run dev

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
	@echo "All models ready."

# ── Pre-flight dependency check ───────────────────────────────────────────────
check-deps:
	@echo "Checking host dependencies ..."
	@command -v opencode > /dev/null 2>&1 \
		&& echo "  ✓ OpenCode installed" \
		|| echo "  ⚠  OpenCode not found — install: brew install opencode-ai/tap/opencode"
	@OLLAMA_URL=$${OLLAMA_BASE_URL:-http://localhost:11434}; \
		curl -sf "$$OLLAMA_URL" > /dev/null 2>&1 \
		&& echo "  ✓ Ollama reachable at $$OLLAMA_URL" \
		|| (echo "  ✗ Ollama NOT reachable — run: ollama serve" && exit 1)
	@echo "  ✓ All deps OK."

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	docker image prune -f
