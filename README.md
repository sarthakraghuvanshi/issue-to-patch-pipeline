# Issue-to-Patch Automation Pipeline

RAG-based pipeline that ingests a GitHub issue, retrieves relevant repository
context (BM25 → hybrid), proposes a source change via a stateful reasoning graph,
and emits a **validated `.patch` file**. Every run ends in exactly one of
`PATCH_VALIDATED`, `PATCH_REQUIRES_HUMAN_REVIEW`, `PATCH_REJECTED`, or
`INVESTIGATION_INCONCLUSIVE` — it never silently claims success.

- **What / why:** [RAG_LEARNING_PLAN.md](RAG_LEARNING_PLAN.md)
- **How / order / deploy:** [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

## Status

- [x] Bootstrap (IMPLEMENTATION_PLAN.md §2)
- [x] Sprint 1 — deterministic issue → validated `.patch`, no LLM, no network
- [x] Sprint 2 — GitHub ingestion (async client, retries, pagination, ETag cache; raw archive + snapshot)
- [x] Sprint 3 — structure-aware chunking (tree-sitter) + rule-based metadata; `index` / `show-chunk`
- [ ] Sprint 4 — BM25 retrieval, then hybrid

## Quickstart

```bash
make install      # uv sync + Python 3.12
make check        # ruff + mypy + pytest (offline, fake LLM)
make migrate      # create the SQLite schema via Alembic

# Sprint 1: fix a bug deterministically from an issue + repo + edit plan
uv run issue-to-patch run \
  --issue path/to/issue.json \
  --repo  path/to/local/repo \
  --edit-plan path/to/plan.json \
  --scope 'src/**'
# exit code: 0 validated · 10 needs-human · 20 rejected · 30 inconclusive

make up           # local infra: postgres+pgvector, redis, langfuse, minio
```

An **edit plan** is JSON: `{"message": "...", "edits": [{"path": "...", "old": "...", "new": "..."}]}`.
`old` must match exactly once; `old: ""` on a missing file creates it.

```bash
# Sprint 2: fetch a real GitHub issue + repo into artifacts/<run_id>/
export ITP_GITHUB_TOKEN=ghp_xxx        # optional; anonymous works within rate limits
uv run issue-to-patch ingest --issue-url https://github.com/OWNER/REPO/issues/123
# writes raw/*.json (issue, comments, repo, related changes, snapshot manifest)
# + snapshot/ pinned to a commit SHA

# Sprint 3: chunk a snapshot into structure-aware pieces + metadata
uv run issue-to-patch index --snapshot artifacts/<run_id>/snapshot
uv run issue-to-patch show-chunk <chunk_id> --metadata   # source, byte-identical to the file
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
