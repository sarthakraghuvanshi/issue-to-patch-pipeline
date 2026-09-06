# Issue-to-Patch Automation Pipeline

RAG-based pipeline that ingests a GitHub issue, retrieves relevant repository
context (BM25 → hybrid), proposes a source change via a stateful reasoning graph,
and emits a **validated `.patch` file**. Every run ends in exactly one of
`PATCH_VALIDATED`, `PATCH_REQUIRES_HUMAN_REVIEW`, `PATCH_REJECTED`, or
`INVESTIGATION_INCONCLUSIVE` — it never silently claims success.

- **What / why:** [RAG_LEARNING_PLAN.md](RAG_LEARNING_PLAN.md)
- **How / order / deploy:** [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

## Status

Bootstrap complete (IMPLEMENTATION_PLAN.md §2). Feature sprints not started.

## Quickstart

```bash
make install      # uv sync + Python 3.12
make check        # ruff + mypy + pytest (offline, fake LLM)
make run-cli ARGS="version"
make run-cli ARGS="config"     # effective settings, secrets redacted
make up           # local infra: postgres+pgvector, redis, langfuse, minio
```

## Layout

| Path | Role |
|---|---|
| `src/issue_to_patch/config/` | `Settings` (env-driven, fails fast) |
| `src/issue_to_patch/llm/` | the only seam to a language model; `FakeLLM` for tests |
| `src/issue_to_patch/ingestion/` | Data Sources: GitHub client, issue normalization, snapshots |
| `src/issue_to_patch/processing/` | parsing, structure analysis, structure-aware chunking, metadata |
| `src/issue_to_patch/retrieval/` | BM25, embeddings, hybrid ranking |
| `src/issue_to_patch/graph/` | LangGraph reasoning engine + deterministic router |
| `src/issue_to_patch/agents/` | specialist agents, typed cited artifacts |
| `src/issue_to_patch/patching/` | worktree edits, `git format-patch`, deterministic validation |
| `src/issue_to_patch/evaluation/` | metrics, grounded LLM judge, cost |
| `src/issue_to_patch/safety/` | allowlists, sandbox, stress tests |
| `src/issue_to_patch/persistence/` | SQL / vector / object-storage adapters |
| `src/issue_to_patch/api/` | FastAPI + generated OpenAPI |

## Configuration

Copy `.env.example` to `.env`. All variables are prefixed `ITP_`.
