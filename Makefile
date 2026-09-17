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
models: ## Download Qwen3-VL GGUF weights (~13 GB; TIERS=cpu for just the 4B)
	./scripts/download_models.sh

.PHONY: docs-assets
docs-assets: ## Fetch the Swagger UI files /api/docs serves same-origin (done in the image build)
	./scripts/fetch_docs_assets.sh

# ---------------- local inference ----------------
.PHONY: llama-gpu
llama-gpu: ## Serve Qwen3-VL-8B on 127.0.0.1:18080 (Metal locally)
	llama-server -m $(LLAMA_8B) --mmproj $(MMPROJ_8B) \
		-ngl 99 -c 8192 --parallel 2 --jinja --host 127.0.0.1 --port 18080

.PHONY: llama-cpu
llama-cpu: ## Serve Qwen3-VL-4B on 127.0.0.1:18081 (fallback tier)
	llama-server -m $(LLAMA_4B) --mmproj $(MMPROJ_4B) \
		-ngl 0 -c 4096 --parallel 1 --jinja --host 127.0.0.1 --port 18081

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

.PHONY: images
images: ## Build both container images exactly as CI and deployment do
	docker build -t vlm-leads-api backend
	docker build -t vlm-leads-web -f frontend/Dockerfile .

.PHONY: eval
eval: ## Evaluate the CPU tier (4B on :18081) against the card set
	cd backend && uv run python ../eval/run_eval.py \
		--tier cpu --base-url http://127.0.0.1:18081/v1 \
		--model Qwen3VL-4B-Instruct-Q4_K_M \
		--json-out ../eval/results/latest-4b.json

.PHONY: eval-gpu
eval-gpu: ## Evaluate the primary tier (8B on :18080) against the card set
	cd backend && uv run python ../eval/run_eval.py \
		--tier gpu --base-url http://127.0.0.1:18080/v1 \
		--model Qwen3VL-8B-Instruct-Q8_0 --timeout 600 \
		--json-out ../eval/results/latest-8b.json

.PHONY: cards
cards: ## Regenerate the synthetic evaluation cards
	cd backend && uv run python ../eval/generate_cards.py --out ../eval/cards/synthetic

# ---------------- deploy ----------------
# --env-file is required, not cosmetic: compose derives its project directory
# from the compose file's location, so with -f deploy/... it looks for
# deploy/.env, finds nothing, and every variable fails to interpolate.
.PHONY: deploy-gpu
deploy-gpu: ## Bring up the GPU profile on the server
	docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile gpu up -d --build

.PHONY: deploy-cpu
deploy-cpu: ## Bring up the CPU-only profile (credit-saving mode)
	docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile cpu up -d --build
