.PHONY: up down build logs opencode freellm bootstrap check-deps clean models

# ── Docker stack ──────────────────────────────────────────────────────────────────
up: check-deps
	docker compose up --build -d
	@echo ""
	@echo "Stack is up:  Memory :8002  |  Neo4j :7474"
	@echo "Run 'make bootstrap' to seed the knowledge graph (first time only)."
	@echo "  make bootstrap              → seeds agents_crew itself"
	@echo "  make bootstrap TARGET_DIR=~  → seeds another project"
	@echo "Run 'make opencode'  to start the OpenCode agent."
	@echo "Run 'make freellm'   to start the FreeLLMAPI server (if not running)."

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f

# ── OpenCode agent ────────────────────────────────────────────────────────────────
opencode:
	@if [ ! -f .env ]; then echo "ERROR: .env file not found. Copy .env.example to .env and fill in your keys."; exit 1; fi
	@echo "Starting OpenCode ..."
	@env $$(grep -v '^#' .env | grep -v '^$$' | xargs) opencode

# ── FreeLLMAPI server (runs on host, not in Docker) ────────────────────────
freellm:
	@echo "Starting FreeLLMAPI server on :3001 ..."
	@echo "Dashboard: http://localhost:5173"
	@if [ ! -d "../freellmapi" ]; then \
		echo "  Cloning freellmapi ..."; \
		git clone https://github.com/tashfeenahmed/freellmapi ../freellmapi; \
		cd ../freellmapi && npm install; \
	fi
	cd ../freellmapi && npm run dev

# ── Memory bootstrap (seed the knowledge graph from any project) ──────────
TARGET_DIR ?= $(CURDIR)
bootstrap:
	@bash "$(dir $(abspath $(lastword $(MAKEFILE_LIST))))bootstrap.sh" \
	  "$(TARGET_DIR)" "$(GROUP_ID)"

# ── Ollama model management ─────────────────────────────────────────────────────────
models:
	@EMBED_PROVIDER=$$(grep -v '^#' .env 2>/dev/null | grep GRAPHITI_EMBED_PROVIDER | cut -d= -f2); \
		EMBED_PROVIDER=$${EMBED_PROVIDER:-ollama}; \
		if [ "$$EMBED_PROVIDER" = "ollama" ]; then \
			echo "Pulling Ollama embedder model ..."; \
			ollama pull $$(grep -v '^#' .env 2>/dev/null | grep GRAPHITI_EMBED_MODEL | cut -d= -f2 || echo nomic-embed-text); \
			echo "Model ready."; \
		else \
			echo "Embed provider is '$$EMBED_PROVIDER' — no Ollama model needed."; \
		fi

# ── Pre-flight dependency check ─────────────────────────────────────────────────────
check-deps:
	@if [ ! -f .env ]; then echo "ERROR: .env file not found. Copy .env.example to .env and fill in your keys."; exit 1; fi
	@echo "Checking host dependencies ..."
	@command -v opencode > /dev/null 2>&1 \
		&& echo "  ✓ OpenCode installed" \
		|| echo "  ⚠  OpenCode not found — install: brew install opencode-ai/tap/opencode"
	@EMBED_PROVIDER=$$(grep -v '^#' .env 2>/dev/null | grep GRAPHITI_EMBED_PROVIDER | cut -d= -f2); \
		EMBED_PROVIDER=$${EMBED_PROVIDER:-ollama}; \
		OLLAMA_URL=$${OLLAMA_BASE_URL:-http://localhost:11434}; \
		if [ "$$EMBED_PROVIDER" = "ollama" ]; then \
			curl -sf "$$OLLAMA_URL" > /dev/null 2>&1 \
				&& echo "  ✓ Ollama reachable at $$OLLAMA_URL" \
				|| (echo "  ✗ Ollama NOT reachable — run: ollama serve" && exit 1); \
		else \
			echo "  ✓ Embed provider is '$$EMBED_PROVIDER' — Ollama not required."; \
		fi
	@echo "  ✓ All deps OK."

# ── Cleanup ────────────────────────────────────────────────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	docker image prune -f
