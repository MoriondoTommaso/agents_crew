.PHONY: help install up up-server down build logs logs-server agent restart-server test lint health

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*##"}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Bootstrap everything on a fresh machine (deps + .env + models + OpenClaw + Docker build)
	chmod +x install.sh && ./install.sh

up: ## Build and start ALL services (coding-agency + openclaw) in background
	docker compose up --build -d
	@echo "\n\033[32m✔\033[0m  Stack up. Run: make agent"

up-server: ## Start only the coding-agency server (use openclaw locally)
	docker compose up --build -d coding-agency
	@echo "\n\033[32m✔\033[0m  Server up on :8000. Run: openclaw"

down: ## Stop and remove all containers
	docker compose down

build: ## Rebuild images without starting
	docker compose build

logs: ## Follow logs from all services
	docker compose logs -f

logs-server: ## Follow logs from coding-agency only
	docker compose logs -f coding-agency

agent: ## Attach to the OpenClaw CLI inside Docker
	docker compose up -d
	docker attach openclaw

restart-server: ## Restart coding-agency (after code changes)
	docker compose restart coding-agency

test: ## Run the test suite
	uv run pytest tests/ -v

lint: ## Lint with ruff
	uv run ruff check .

health: ## Check server health
	curl -s http://localhost:8000/health | python3 -m json.tool
