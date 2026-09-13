.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help install up down logs lint fmt type test check migrate run-cli serve openapi eval clean

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

migrate: ## Apply database migrations
	uv run alembic upgrade head

reset-db: ## Delete the local SQLite DB (dev only; re-index afterwards)
	rm -f artifacts/dev.db artifacts/*.db

run-cli: ## Run the CLI, e.g. `make run-cli ARGS="version"`
	uv run issue-to-patch $(ARGS)

serve: ## Run the API locally
	uv run uvicorn issue_to_patch.api.app:app --reload

openapi: ## Regenerate openapi.json from the live app (run after a route change)
	uv run python scripts/generate_openapi.py

eval: ## Retrieval metrics from evals/labeled_issues.jsonl -> evals/report.{json,html}
	uv run issue-to-patch eval-suite --labeled evals/labeled_issues.jsonl \
		--out-json evals/report.json --out-html evals/report.html

demo: ## Sprint 1 hands-on: build a buggy repo and fix it end to end
	bash examples/try_sprint1.sh

clean: ## Remove caches and local run output
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage artifacts/*.db
