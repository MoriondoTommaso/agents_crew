.PHONY: help up down build logs agent test lint

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*##"}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Build and start all services (detached)
	docker compose up --build -d
	echo "\n✅  Stack up. Run: make agent"

down: ## Stop and remove all containers
	docker compose down

build: ## Rebuild images without starting
	docker compose build

logs: ## Follow logs from all services
	docker compose logs -f

logs-server: ## Follow logs from coding-agency only
	docker compose logs -f coding-agency

agent: ## Attach to the OpenClaw interactive CLI
	docker compose up -d
	docker attach openclaw

restart-server: ## Restart only the coding-agency (after code changes)
	docker compose restart coding-agency

test: ## Run the test suite (no Docker needed)
	uv run pytest tests/ -v

lint: ## Lint with ruff
	uv run ruff check .

health: ## Check server health
	curl -s http://localhost:8000/health | python3 -m json.tool
