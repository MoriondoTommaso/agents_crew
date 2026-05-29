.PHONY: up down build logs agent bootstrap test clean

# ── Docker Compose ──────────────────────────────────────────────────────────
up:
	docker compose up --build -d
	@echo "Stack is up. Run 'make bootstrap' to seed the knowledge graph (first time only)."

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

# ── Agent ───────────────────────────────────────────────────────────────────
agent:
	docker attach openclaw

# ── Memory bootstrap (run once after first `make up`) ───────────────────────
bootstrap:
	@echo "Seeding knowledge graph from codebase ..."
	docker compose exec memory python bootstrap.py
	@echo "Bootstrap complete. Graph is ready."

# ── Tests ───────────────────────────────────────────────────────────────────
test:
	uv run python test_pipeline.py

# ── Cleanup ─────────────────────────────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	docker image prune -f
