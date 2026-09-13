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
      human-review gate, real Anthropic + OpenAI providers); `investigate`
- [x] Sprint 6 — FastAPI service (`POST /runs`, `GET /runs/{id}`, `.../approve`,
      `.../retrieval`, `.../patch`, `/search`, `/validate-patch`) over a durable
      (SQLite-file) checkpointer — a run started by one request can be approved by a
      completely different one, even after a process restart. `make serve`
- [x] Sprint 7a — human validation roles (Gatekeeper/Auditor/Strategist) + risk
      classification (only a Gatekeeper can clear a patch touching a risky path) +
      a tamper-evident audit trail (`audit` CLI command, `GET /runs/{id}/audit`)
- [x] Sprint 7b — multi-agent system: Issue Analyst, Repository Cartographer,
      Root-Cause Analyst, Patch Author, Test Strategist, Patch Reviewer — six typed,
      cited artifacts replacing the single-LLM-call analysis/drafting, behind
      `ITP_AGENT_MODE=multi`. Same graph, same validation, same human gate either way.
- [x] Sprint 8 — evaluation: deterministic per-run/suite metrics, a grounded LLM
      judge (never the sole success signal), `make eval` / `eval-suite` / `judge`.
      Real cost tracking wired through (`Run.cost_usd` had existed since Sprint 1
      but nothing filled it in) and the graph now writes its patch/validation to
      disk like Sprint 1's deterministic pipeline always did.

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
# checkpointer is in-memory; the API (below) uses a durable one instead.

# Sprint 6: the same investigation, over HTTP — a run started here can be approved
# by a completely separate request (or process), because state lives in a SQLite
# file, not in memory:
make serve   # uvicorn on :8000; docs at /docs, contract at /openapi.json
curl -s localhost:8000/runs -X POST -H 'content-type: application/json' -d '{
  "issue": "add() returns the wrong result",
  "snapshot": "artifacts/<run_id>/snapshot",
  "scope": ["src/**"]
}'   # -> {"run_id": "...", "status": "AWAITING_HUMAN_REVIEW", "hypothesis": {...}, ...}
curl -s localhost:8000/runs/<run_id>/approve -X POST -H 'content-type: application/json' \
  -d '{"decision": "approve"}'
# set ITP_API_KEY and send `Authorization: Bearer <key>` once this leaves local dev —
# Settings refuses to start with no key in staging/prod.

# Sprint 7a: a patch touching a risky path (.github/**, secrets, migrations, lockfiles,
# pyproject.toml, ... see safety/permissions.py) needs the Gatekeeper role specifically —
# an Auditor's or Strategist's "approve" ends the run PATCH_REQUIRES_HUMAN_REVIEW, not
# PATCH_VALIDATED:
uv run issue-to-patch investigate --resume <run_id> --decision approve \
  --reviewer alice --role gatekeeper
# every tool call and human decision is append-only and hash-chained; replay + verify it:
uv run issue-to-patch audit <run_id>          # or GET /runs/{run_id}/audit

# Sprint 7b: the same investigation, but AnalyzeRootCause/DraftPatch run as six
# specialist agents instead of one LLM call each - same graph, same citations,
# same validation and human gate:
export ITP_AGENT_MODE=multi
uv run issue-to-patch investigate --issue "add() returns the wrong result" \
  --snapshot artifacts/<run_id>/snapshot --scope 'src/**'

# Sprint 8: evaluation - retrieval metrics from a labeled set, and/or deterministic +
# judge metrics from runs you already made. make eval reuses evals/labeled_issues.jsonl:
make eval                                    # -> evals/report.{json,html}
uv run issue-to-patch eval-suite --run-id <run_id> --run-id <run_id2> --judge
uv run issue-to-patch judge <run_id>          # score one run on its own
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
| `src/issue_to_patch/agents/` | the six specialists (`ITP_AGENT_MODE=multi`), each a typed, cited artifact |
| `src/issue_to_patch/patching/` | worktree edits, `git format-patch`, deterministic validation |
| `src/issue_to_patch/evaluation/` | deterministic per-run/suite metrics, the grounded LLM judge, the combined `SuiteReport` (JSON + HTML) |
| `src/issue_to_patch/safety/` | allowlists, sandbox, stress tests |
| `src/issue_to_patch/persistence/` | SQL / vector / object-storage adapters; `audit.py` replays + verifies a run's hash-chained tool-call and human-decision trail |
| `src/issue_to_patch/api/` | FastAPI: schemas, thin routes, auth + rate-limit deps; `openapi.json` committed at repo root (`make openapi` to regenerate, checked by a contract test) |

## Configuration

Copy `.env.example` to `.env`. All variables are prefixed `ITP_`.

## What Sprint 6 deliberately leaves out

`POST /runs` runs the graph **synchronously in the request** rather than enqueuing
it to a background worker (`arq` + Redis, per the original plan). The durable SQLite
checkpointer already delivers the property that actually matters — a paused run can
be approved from a separate request or process — without needing Redis running in
this environment. A real job queue is a drop-in addition once that infra exists: the
graph and checkpointer don't change, only *who* calls `graph.invoke()`.

## What Sprint 8 deliberately leaves out

- **`test_pass_rate` / `regression_rate`** in `RunMetrics` are always `None`. Measuring
  them means actually executing a target repository's own test suite — untrusted code,
  which the "repo content is untrusted data" principle says never runs unsandboxed.
  That sandbox is Sprint 9's job; faking a pass rate without one would be worse than
  admitting it isn't measured yet.
- **Langfuse tracing** isn't wired up — it needs a running Langfuse instance (in
  `docker-compose.yml`, never started in this environment). `LLMClient.cost_usd` and
  the hash-chained tool-call log already give per-run cost and a full call sequence
  without it; swapping in real tracing later doesn't change either.
- **The feedback loop** (auto-proposing changes to BM25 weights, chunk boundaries,
  router thresholds, agent prompts from eval results) needs a real history of eval
  runs to learn from. With only a handful of runs so far, a feedback script would have
  nothing to propose — worth building once `eval-suite` has actually accumulated data.
- **A CI gate that fails on metric regression** isn't wired into `.github/workflows/ci.yml`.
  `make eval` is real and runnable today; gating CI on it needs either `ITP_LLM_PROVIDER=fake`
  (numbers that don't mean anything for judge scores) or a real provider key held as a
  CI secret — a deliberate choice for whoever deploys this, not one to make silently here.
