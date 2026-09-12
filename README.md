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
- [x] Sprint 4 — BM25 + dense (hashing embedder) + hybrid (RRF) retrieval; `search` / `eval-retrieval`
- [x] Milestone 2 gate — 20 labeled issues over langchain-ai/langchain (3,125 files); hybrid
      beats BM25 (see [evals/README.md](evals/README.md)) — hybrid stays the default mode
- [x] Sprint 5 — single-agent LangGraph reasoning engine (12 nodes, deterministic router,
      human-review gate, real Anthropic provider); `investigate`
- [ ] Sprint 6 — OpenAPI service + durable (cross-process) resumability

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

# Sprint 4: rank indexed chunks against a bug description
uv run issue-to-patch search "add() returns the wrong result" \
  --snapshot artifacts/<run_id>/snapshot --mode hybrid --explain
uv run issue-to-patch eval-retrieval --labeled evals/labeled_issues.jsonl

# Sprint 5: investigate + draft a patch through the reasoning graph, pause for human review
export ITP_LLM_PROVIDER=anthropic ITP_LLM_API_KEY=sk-ant-...   # or provider=openai + an OPENAI key
# (leave ITP_LLM_PROVIDER=fake, the default, for offline/FakeLLM use)
uv run issue-to-patch investigate --issue "add() returns the wrong result" \
  --snapshot artifacts/<run_id>/snapshot --scope 'src/**'
# prints the root-cause hypothesis + its citations, the diff, validation checks,
# then pauses (exit 10) until you resolve it:
uv run issue-to-patch investigate --issue "..." --snapshot ... --decision approve
# --decision is only resumable within the same process/invocation for now — the
# checkpointer is in-memory; a durable one (so `approve` can come from a separate
# command, or an API call) is Sprint 6.
```

> The local `artifacts/dev.db` is disposable. If a sprint changes the schema and an
> old DB errors with `no such column`, run `make reset-db` and re-index (or
> `make migrate` if you know the DB is only one revision behind).

## Layout

| Path | Role |
|---|---|
| `src/issue_to_patch/config/` | `Settings` (env-driven, fails fast) |
| `src/issue_to_patch/llm/` | the only seam to a language model; `FakeLLM` for tests, `AnthropicLLM`/`OpenAILLM` for real |
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
