.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help install up down logs lint fmt type test check migrate run-cli serve clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Sync the virtualenv from the lockfile (installs Python 3.12 if needed)
	uv python install 3.12
	uv sync

up: ## Start local infra (postgres+pgvector, redis, langfuse, minio)
	docker compose up -d

down: ## Stop local infra
	docker compose down

logs: ## Tail infra logs
	docker compose logs -f

lint: ## Ruff lint
	uv run ruff check .

fmt: ## Ruff format
	uv run ruff format .

type: ## mypy (strict) on the source tree
	uv run mypy

test: ## Run the test suite with coverage
	uv run pytest

check: lint type test ## Everything CI runs

migrate: ## Apply database migrations (added in Sprint 1)
	uv run alembic upgrade head

run-cli: ## Run the CLI, e.g. `make run-cli ARGS="version"`
	uv run issue-to-patch $(ARGS)

serve: ## Run the API locally (added in Sprint 6)
	uv run uvicorn issue_to_patch.api.app:app --reload

clean: ## Remove caches and local run output
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage artifacts/*.db
