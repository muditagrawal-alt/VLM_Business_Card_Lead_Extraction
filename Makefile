# ============================================================
# VLM Business Card Lead Extraction
# ============================================================
.DEFAULT_GOAL := help
SHELL := /bin/bash

MODELS_DIR := models
LLAMA_8B   := $(MODELS_DIR)/Qwen3VL-8B-Instruct-Q8_0.gguf
MMPROJ_8B  := $(MODELS_DIR)/mmproj-Qwen3VL-8B-Instruct-F16.gguf
LLAMA_4B   := $(MODELS_DIR)/Qwen3VL-4B-Instruct-Q4_K_M.gguf
MMPROJ_4B  := $(MODELS_DIR)/mmproj-Qwen3VL-4B-Instruct-F16.gguf

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------- setup ----------------
.PHONY: install
install: install-backend install-frontend ## Install all dependencies

.PHONY: install-backend
install-backend: ## Install Python dependencies
	cd backend && uv venv --python 3.12 && uv pip install -e ".[dev]"

.PHONY: install-frontend
install-frontend: ## Install Node dependencies
	cd frontend && npm install

.PHONY: models
models: ## Download Qwen3-VL GGUF weights (~14 GB)
	./scripts/download_models.sh

# ---------------- local inference ----------------
.PHONY: llama-gpu
llama-gpu: ## Serve Qwen3-VL-8B on port 8080 (Metal locally / CUDA in prod)
	llama-server -m $(LLAMA_8B) --mmproj $(MMPROJ_8B) \
		-ngl 99 -c 8192 --parallel 2 --jinja --host 0.0.0.0 --port 8080

.PHONY: llama-cpu
llama-cpu: ## Serve Qwen3-VL-4B on port 8081 (CPU fallback tier)
	llama-server -m $(LLAMA_4B) --mmproj $(MMPROJ_4B) \
		-ngl 0 -c 4096 --parallel 1 --jinja --host 0.0.0.0 --port 8081

# ---------------- dev ----------------
.PHONY: dev
dev: ## Run API + worker + frontend (needs Postgres up)
	@$(MAKE) -j3 dev-api dev-worker dev-frontend

.PHONY: dev-api
dev-api: ## Run the API with hot reload
	cd backend && uv run uvicorn app.main:app --reload --port 8000

.PHONY: dev-worker
dev-worker: ## Run the extraction worker
	cd backend && uv run python -m app.worker

.PHONY: dev-frontend
dev-frontend: ## Run the Vite dev server
	cd frontend && npm run dev

.PHONY: db-up
db-up: ## Start Postgres in Docker
	docker compose -f deploy/docker-compose.yml up -d postgres

.PHONY: db-down
db-down: ## Stop local infrastructure
	docker compose -f deploy/docker-compose.yml down

.PHONY: migrate
migrate: ## Apply database migrations
	cd backend && uv run alembic upgrade head

.PHONY: migration
migration: ## Create a migration: make migration M="add leads table"
	cd backend && uv run alembic revision --autogenerate -m "$(M)"

# ---------------- quality ----------------
.PHONY: test
test: test-backend test-frontend ## Run all tests

.PHONY: test-backend
test-backend: ## Run Python tests (skips tests needing a real model)
	cd backend && uv run pytest -m "not model"

.PHONY: test-frontend
test-frontend: ## Run frontend tests
	cd frontend && npm run test

.PHONY: lint
lint: ## Lint and type-check everything
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright
	cd frontend && npm run lint && npm run typecheck

.PHONY: format
format: ## Auto-format everything
	cd backend && uv run ruff check --fix . && uv run ruff format .
	cd frontend && npm run format

.PHONY: eval
eval: ## Run the accuracy evaluation across provider tiers
	cd backend && uv run python -m eval.run_eval

# ---------------- deploy ----------------
.PHONY: deploy-gpu
deploy-gpu: ## Bring up the GPU profile on the server
	docker compose -f deploy/docker-compose.prod.yml --profile gpu up -d --build

.PHONY: deploy-cpu
deploy-cpu: ## Bring up the CPU-only profile (credit-saving mode)
	docker compose -f deploy/docker-compose.prod.yml --profile cpu up -d --build
