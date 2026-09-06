# Issue-to-Patch Automation Pipeline — Implementation & Deployment Plan

Companion to [RAG_LEARNING_PLAN.md](RAG_LEARNING_PLAN.md). The learning plan says *what* each
architecture box is and *why*. This document says *how* to build it, *in what order*, and *how to
ship it*. Every sprint below ends in something runnable and tested.

---

## 0. Principles

1. **Deterministic core first, LLM later.** Every node gets a rule-based version and a test that
   pins its failure mode before a model is added.
2. **One run = one immutable record.** A run pins `owner/repo@sha`, stores every tool call
   append-only, and ends in exactly one of `PATCH_VALIDATED`, `PATCH_REQUIRES_HUMAN_REVIEW`,
   `PATCH_REJECTED`, `INVESTIGATION_INCONCLUSIVE`.
3. **Repo content is untrusted input.** It never reaches a system prompt, never runs outside the
   sandbox, never sees a token.
4. **Ship a thin slice end to end early.** Milestone 1 is deployable. Later milestones widen it.
5. **The API is the deployable unit.** The graph is a library the API calls; both are testable in
   isolation.

---

## 1. Tooling & stack decisions (lock these before writing code)

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | `match`, `X | None`, `tomllib` |
| Packaging / venv | `uv` + `pyproject.toml` | fast, lockfile via `uv lock` |
| Lint / format | `ruff` (lint + format) | one tool |
| Types | `mypy --strict` on `src/` | loosen per-module only with a reason |
| Tests | `pytest`, `pytest-asyncio`, `pytest-cov`, `respx` (httpx mocking) | |
| HTTP | `httpx` (async) | retries + timeouts in one client wrapper |
| Models | `pydantic` v2 + `pydantic-settings` | all cross-boundary data is a model |
| API | `FastAPI` + `uvicorn` (dev) / `gunicorn -k uvicorn.workers.UvicornWorker` (prod) | OpenAPI auto-generated |
| Relational | SQLite in dev → **PostgreSQL 16** in staging/prod | `SQLAlchemy` 2.x + `Alembic` |
| Vector | **pgvector** extension on the same Postgres | one datastore; swap to Qdrant only if measured need |
| Lexical | custom inspectable BM25 (or `bm25s`) | must expose per-term scores |
| Code parsing | `tree-sitter` + `tree-sitter-python` (add languages later) | |
| Orchestration | `langgraph` with a Postgres checkpointer | resumable runs |
| LLM provider | pick **one** (structured output + tool calling). Wrap behind `llm/client.py` | never call the SDK directly from nodes |
| Sandbox | ephemeral Docker container, `--network none`, cpu/mem/pids limits, read-only mount | |
| Object storage | local `artifacts/` dir in dev → S3-compatible (MinIO / R2) in prod | snapshots, patches, logs |
| Tracing / cost | Langfuse (self-hosted via compose) or OTel → Langfuse | token + latency per node |
| Task execution | synchronous in-process until Milestone 4, then `arq` worker + Redis | |
| CI | GitHub Actions | lint, type, test, build image, push to GHCR |
| Deploy target | Docker Compose on a single VM (primary). Fly.io / Render as alternative | |

> Do not add Qdrant, Celery, Kubernetes, or a second model provider until an eval number says the
> current choice is the bottleneck.

---

## 2. Repository bootstrap (day 1)

```bash
uv init issue-to-patch && cd issue-to-patch
uv add httpx pydantic pydantic-settings fastapi uvicorn "sqlalchemy>=2" alembic \
       psycopg[binary] pgvector langgraph langchain-core tree-sitter tree-sitter-python \
       tenacity structlog
uv add --dev pytest pytest-asyncio pytest-cov respx ruff mypy types-requests
git init && gh repo create   # private
```

Create the layout from [RAG_LEARNING_PLAN.md](RAG_LEARNING_PLAN.md#L404) (`src/issue_to_patch/...`).

Add, before any feature code:

- `pyproject.toml`: ruff, mypy, pytest, coverage config.
- `.pre-commit-config.yaml`: ruff, ruff-format, mypy, trailing-whitespace.
- `.github/workflows/ci.yml`: matrix on 3.12; steps = `uv sync`, `ruff check`, `ruff format --check`,
  `mypy src`, `pytest --cov --cov-fail-under=80`.
- `src/issue_to_patch/config/settings.py`: `Settings(BaseSettings)` — DB URL, GitHub token,
  model key, sandbox image, artifact path. Fails fast on missing required vars.
- `src/issue_to_patch/logging.py`: `structlog` JSON logs with a `run_id` contextvar.
- `docker-compose.yml`: `postgres` (pgvector image), `langfuse` (+ its Postgres), optional `minio`.
- `Makefile` / `justfile`: `up`, `test`, `lint`, `migrate`, `run-cli`, `serve`.

**Exit gate:** `make up && make test` is green on an empty project; CI passes on the first PR.

---

## 3. Build sequence

Each sprint maps to a milestone in the learning plan. "Files" are the primary modules touched;
"Gate" is the demo + test bar to move on.

### Sprint 1 — Deterministic issue-to-patch prototype  (Milestone 1, Phases 0 & 7 core)

Goal: local issue fixture + repo snapshot → a real `.patch` + validation report, **no LLM**.

Tasks:
- `ingestion/models.py`: `IssueRequest`, `RepositorySnapshot`, `RunRecord`.
- `ingestion/normalize.py`: parse a GitHub issue URL / `owner/repo#n` / raw text → `IssueRequest`.
- `ingestion/git_ops.py`: `SafeGit` wrapper — allowlisted subcommands, logs every invocation,
  rejects paths outside the worktree, no network after clone.
- `ingestion/snapshot.py`: shallow clone into a temp dir, resolve to a commit SHA, write a
  `snapshot_manifest.json` (url, sha, timestamp, file count, tree hash).
- `patching/worktree.py`: create an isolated `git worktree`, apply a structured `EditPlan`
  (list of `{path, anchor, old, new}`), commit, `git format-patch`, then tear down.
- `patching/validate.py`: the checks from [Phase 7](RAG_LEARNING_PLAN.md#L317) —
  syntax, applies to SHA, scope allowlist, no binaries/secrets, minimality heuristic.
- `persistence/store.py`: SQLAlchemy models + Alembic migration for `runs`, `tool_calls`,
  `artifacts`. Write a `RunRecord` for every invocation.
- `cli.py` (`typer` or argparse): `issue-to-patch run --issue fixtures/issue_1.json --repo <path> --edit-plan plan.json`.

Tests (`tests/unit`, `tests/patch_fixtures`):
- malformed URL / missing repo / bad issue number → typed errors.
- a known `EditPlan` on a fixture repo produces a byte-stable patch.
- `git apply --check` passes; a scope-violating plan is rejected.
- re-running the same inputs yields the same `run` content hash.

**Gate:** `cli run` on a fixture emits `artifacts/<run_id>/fix.patch` + `validation.json`,
persists the run, and never calls a network service. Deploy this CLI as a container (Section 5).

### Sprint 2 — GitHub ingestion  (Milestone 4 part 1, Phase 1)

Tasks:
- `ingestion/github.py`: async `GitHubClient` on `httpx` — auth, `tenacity` retries with
  respect for `Retry-After`, pagination iterator, ETag caching, per-call logging.
- Nodes as plain functions first: `fetch_issue`, `fetch_issue_conversation`,
  `fetch_repository_metadata`, `clone_repository_snapshot`, `collect_related_changes`.
- Raw-artifact writer: store unmodified JSON under `artifacts/<run_id>/raw/`, redact tokens,
  record source URL + content hash.
- Vision/OCR hook stub for image attachments (implement later; interface now).

Tests: `respx`-mocked API for pagination, 403 rate-limit, 404, secondary-rate-limit backoff;
fixture-backed golden files for the raw artifact format.

**Gate:** `cli ingest --issue-url <real URL>` produces a complete raw artifact set + a snapshot
pinned to a SHA, offline-replayable from cassettes.

### Sprint 3 — Parsing, restructuring, metadata  (Phase 2)

Tasks:
- `processing/parser.py`: `DocumentParser` for `.py .md .json .yaml .txt`.
- `processing/structure.py`: `StructureAnalyzer` (tree-sitter) → symbols, imports, call sites,
  test functions, line ranges; build a file→symbol→test reference graph.
- `processing/chunker.py`: `StructureAwareChunker` keeping functions/classes whole;
  `BoundaryDetector`; parent-child links; stable `chunk_id` hash.
- `processing/metadata.py`: `SummaryGenerator`, `KeywordExtractor`, `QuestionGenerator`
  (rule-based first: keywords = identifiers + docstring terms; LLM variants behind a flag).
- Emit the chunk record schema from [Phase 2](RAG_LEARNING_PLAN.md#L142).

Tests: chunk traces to exact file+lines; deterministic re-index of the same SHA (hash equality);
a function longer than the window is not split mid-body.

**Gate:** `cli index --snapshot <manifest>` writes N chunk records; `cli show-chunk <id>` prints
source that matches the file exactly.

### Sprint 4 — Retrieval: BM25 then hybrid  (Milestone 2, then 6 part 1; Phase 3)

Tasks:
- `retrieval/tokenize.py`: NL + code tokenizer, identifier splitting, path/error-message tokens.
- `retrieval/bm25.py`: inspectable index — returns `{chunk_id, score, term_contributions}`.
- `retrieval/service.py`: `search(query, filters, top_k)` with metadata filters
  (repo, sha, language, path). Retrieval trace object (query, scores, kept, discarded).
- `persistence/vector.py`: pgvector table + `embed()` behind the LLM wrapper.
- `retrieval/hybrid.py`: RRF / weighted fusion; query expansion from labels + stack frames;
  parent-context expansion.
- `retrieval/rerank.py` (later): cross-encoder over top ~50.
- `evals/`: `labeled_issues.jsonl` (issue → gold files/symbols), `evals/run_retrieval.py`
  computing recall@k, MRR, nDCG; a `BM25 vs dense vs hybrid` report.

**Gate (Milestone 2):** index 500+ files, `cli search "<issue text>"` shows ranked files with
scores, recall@k reported on ≥20 labeled issues. Hybrid must beat BM25 on the same set before it
stays on.

### Sprint 5 — Single-agent reasoning graph  (Milestone 3, Phase 5)

Tasks:
- `graph/state.py`: `InvestigationState` + all referenced models
  (`Evidence`, `Hypothesis`, `SourceLocation`, `PatchArtifact`, `ValidationReport`,
  `HumanDecision`, `EvaluationReport`).
- `graph/nodes/`: the 12 nodes from [Phase 5](RAG_LEARNING_PLAN.md#L252), each a pure
  `state -> partial state` function.
- `graph/router.py`: deterministic router implementing the rules table; thresholds in config.
- `graph/build.py`: assemble the `StateGraph`, attach the **Postgres checkpointer** (resumable),
  interrupt-before `RequestHumanValidation`.
- `llm/client.py`: structured-output + tool-calling wrapper, token/cost accounting, one retry on
  schema-validation failure, provider chosen in config.
- Tool layer: GitHub / SafeGit / search / test-runner / patch tools as typed callables with an
  allowlist; the model never gets a raw shell.

Tests: `tests/graph` with a fake LLM returning fixed structured outputs — assert routing for each
rule (low confidence → expansion, unrelated files → reject, tests fail → revise once, etc.);
every terminal path sets one of the four run states.

**Gate:** `cli run --issue-url <URL>` drives the full graph on a real issue, pauses at the human
node, and every claim in the output carries a `chunk_id` + file + line citation.

### Sprint 6 — OpenAPI service + persistence/resumability  (Milestone 4 part 2, Phase 4)

Tasks:
- `api/schemas.py`: Pydantic request/response models.
- `api/routes.py`: the endpoints from [Phase 4](RAG_LEARNING_PLAN.md#L217) — thin handlers that
  call the graph/services; no orchestration logic in handlers.
- `POST /runs` starts a run (sync now; enqueues once the worker exists);
  `GET /runs/{id}` returns state; `POST /runs/{id}/approve` resumes from the checkpoint;
  `/retrieval`, `/patch`, `/search`, `/validate-patch`.
- `arq` worker + Redis; `POST /runs` returns `202` + `run_id`; graph runs in the worker;
  checkpointer makes it resumable across restarts.
- Auth: static API key / bearer for now; per-run rate limit.
- Commit the generated `openapi.json`; contract test that it does not change unexpectedly.

Tests: `tests/integration` with `httpx.ASGITransport` — full run via API against mocked GitHub +
fake LLM; kill the worker mid-run and resume; approve/reject flows.

**Gate:** a run started via `POST /runs` from a GitHub URL completes through the API, survives a
worker restart, and exposes evidence + patch over HTTP.

### Sprint 7 — Multi-agent + human validation + audit  (Milestone 5, Phases 6 & 8)

Tasks:
- `agents/`: the six agents from [Phase 6](RAG_LEARNING_PLAN.md#L281) as nodes that produce typed
  artifacts; sequential execution; each claim cites evidence by `chunk_id`/path/lines.
- Replace the monolithic `AnalyzeRootCause` / `DraftPatch` nodes with the agent sub-graph;
  keep the deterministic single-agent path behind a flag for comparison.
- `safety/permissions.py`: tool allowlist + risk classifier (risky file globs, destructive
  commands) → forces the human gate.
- Human validation: `Gatekeeper` / `Auditor` / `Strategist` decisions on the review payload from
  [Phase 8](RAG_LEARNING_PLAN.md#L335); CLI review screen + `POST /runs/{id}/approve` body.
- `persistence/audit.py`: append-only audit log (hash-chained rows) of every tool call + decision.

Tests: an agent claim without a citation is rejected; a patch touching a risky path cannot reach
`PATCH_VALIDATED` without an approval row; audit log is tamper-evident (broken chain detected).

**Gate:** multi-agent run on a real issue, human approves via API, audit log replays the full
decision trail.

### Sprint 8 — Evaluation, observability, feedback  (Milestone 6 part 2, Phase 9)

Tasks:
- `evaluation/metrics.py`: all deterministic metrics from
  [Phase 9](RAG_LEARNING_PLAN.md#L351) computed from stored artifacts.
- `evaluation/judge.py`: LLM judge on the 5 grounded dimensions; store prompt + model version +
  scores; never the sole success signal.
- `evaluation/run_suite.py`: run the graph over `evals/labeled_issues.jsonl`, emit a JSON + HTML
  report (retrieval, patch apply rate, test pass rate, regression rate, latency p50/p95, $/run).
- Langfuse tracing wired through `llm/client.py` and every node; cost dashboard.
- `feedback/`: scripts that turn eval output into proposed changes to BM25 weights, chunk
  boundaries, expansion rules, router thresholds, agent prompts — applied via PR, never
  auto-merged.
- CI job: run a small eval set on every PR, fail if a headline metric regresses beyond a margin.

**Gate:** `make eval` produces a comparable report; two runs of the suite on the same code give
metrics within noise; Langfuse shows per-node latency and token cost.

### Sprint 9 — Stress testing + production hardening  (Milestone 7, Phase 10)

Tasks:
- `tests/adversarial/`: prompt-injection corpora in issue bodies/comments/README/fixtures;
  biased-diagnosis issues; information-evasion issues; hostile repos (huge files, symlinks,
  secrets, hostile filenames).
- Assert: injected instructions never change tool calls or system behavior; misleading issues end
  `INVESTIGATION_INCONCLUSIVE` rather than a wrong confident patch.
- `safety/sandbox.py`: finalize the Docker sandbox — `--network none`, `--read-only`,
  `--pids-limit`, `--memory`, `--cpus`, non-root, tmpfs workdir, wall-clock timeout; all repo
  commands (tests, lint) go through it.
- Operational resilience: GitHub 429/5xx recovery, network timeouts, deleted branch / stale SHA,
  partial index detection, model-provider outage → `PATCH_REQUIRES_HUMAN_REVIEW` not a crash.
- Secret scanning on patches (`detect-secrets` / `gitleaks`) before an artifact is stored.
- Load test: N concurrent runs, confirm worker backpressure + rate limits hold.

**Gate:** the adversarial suite passes in CI; no code path runs a repo command outside the
sandbox (enforced by a test that greps for `subprocess` calls bypassing `safety/sandbox`).

---

## 4. Data model (persist from Sprint 1, extend each sprint)

| Table | Key columns |
|---|---|
| `runs` | `run_id`, `repo`, `commit_sha`, `issue_ref`, `state`, `created_at`, `finished_at`, `cost_usd`, `content_hash` |
| `tool_calls` | `run_id`, `seq`, `tool`, `args_redacted`, `result_hash`, `ts`, `prev_hash`, `row_hash` |
| `evidence` | `run_id`, `chunk_id`, `path`, `line_start`, `line_end`, `used_by` |
| `artifacts` | `run_id`, `kind` (`patch`/`validation`/`raw`/`eval`), `uri`, `hash` |
| `human_decisions` | `run_id`, `role`, `decision`, `reason`, `ts` |
| `chunks` | `chunk_id`, `repo`, `commit_sha`, `path`, `language`, `symbol`, `lines`, `content`, `summary`, `keywords`, `embedding` (pgvector), `content_hash` |
| `eval_results` | `suite_id`, `run_id`, `metric`, `value`, `model_version` |

Alembic migration per sprint. Never delete a run; supersede.

---

## 5. Containerization & sandbox

Two images from one repo:

- **`app`**: API + worker + CLI (same image, different entrypoints). Multi-stage:
  `uv sync --no-dev` → slim runtime, non-root user, `gunicorn` for the API,
  `arq` for the worker.
- **`sandbox`**: minimal image with git + the target repo's toolchain (python + pytest to start).
  Never contains project secrets. Launched per validation with:
  `docker run --rm --network none --read-only --pids-limit 256 --memory 1g --cpus 2 \
   --user 65534 -v <worktree>:/work:ro --tmpfs /work-rw sandbox <cmd>`.

`.dockerignore` excludes `artifacts/`, `.venv`, `evals/benchmark_repositories`.
The app container needs the Docker socket (or a rootless nested runtime) to spawn the sandbox —
document this as a deployment requirement and restrict the socket via a proxy
(`tecnativa/docker-socket-proxy`, only `POST /containers`).

---

## 6. Environments & configuration

| Env | DB | Vector | Object store | LLM | Sandbox |
|---|---|---|---|---|---|
| `local` | SQLite | pgvector (compose) | `artifacts/` | real, low quota | Docker |
| `ci` | Postgres service | pgvector | tmp dir | **fake** LLM | Docker |
| `staging` | Postgres (compose on VM) | pgvector | MinIO | real | Docker-socket-proxy |
| `prod` | managed Postgres + pgvector | same | S3 / R2 | real | isolated runner |

All config via env vars → `Settings`. Secrets: `.env` locally (git-ignored), GitHub Actions
secrets in CI, VM env file `chmod 600` in staging/prod (or a secrets manager). The LLM/GitHub
tokens are never mounted into the `sandbox` image.

---

## 7. CI/CD

**CI (`ci.yml`, every PR):** `ruff` → `mypy` → `pytest` (unit + integration + adversarial, fake
LLM) → build `app` image → small eval set → fail on metric regression.

**Release (`release.yml`, on tag `v*`):**
1. Build + push `app` and `sandbox` to GHCR, tagged with the version and the git SHA.
2. Run Alembic migrations against staging.
3. `docker compose pull && docker compose up -d` on the staging VM over SSH
   (or `flyctl deploy` / Render deploy hook).
4. Smoke test: `POST /runs` with a canned fixture issue → expect `PATCH_VALIDATED`.
5. Manual approval → same steps against prod. Keep the previous image tag for rollback
   (`docker compose up -d` with the prior tag).

**Migrations:** forward-only, backward-compatible for one release (add column → backfill →
drop later). Never block a deploy on a long backfill.

---

## 8. Deployment topology (primary: single VM + Compose)

```
                    ┌────────────────────────────────────────────┐
   GitHub issue ──▶ │  Caddy/Traefik (TLS)                        │
                    │   └─ app:api  (gunicorn, N workers)         │
                    │        │  enqueue                           │
                    │   app:worker (arq)  ──▶ docker-socket-proxy │──▶ sandbox (ephemeral)
                    │        │                                    │
                    │   redis   postgres+pgvector   minio         │
                    │   langfuse (+ its postgres)                 │
                    └────────────────────────────────────────────┘
```

- Backups: nightly `pg_dump` + MinIO bucket sync off-box; test a restore before Milestone 7.
- Resource sizing: start 2 vCPU / 4 GB + separate disk for Postgres; sandbox runs are the spiky
  load — cap concurrent validations in worker config.
- Alternative: Fly.io (`app` machine + `worker` machine + Fly Postgres + Upstash Redis); sandbox
  via a Fly Machine spawned per run. Same image, different orchestration.

---

## 9. Observability, cost, runbook

- **Traces:** Langfuse span per node, per tool call, per LLM call (prompt, tokens, latency, $).
- **Metrics:** Prometheus endpoint on the API — run count by terminal state, p50/p95 run latency,
  sandbox failures, GitHub rate-limit hits, $/run rolling average.
- **Alerts:** `PATCH_REQUIRES_HUMAN_REVIEW` rate spike, worker queue depth, DB disk, $/run over
  budget, provider error rate.
- **Runbook entries:** GitHub rate-limited (backoff + surface), model outage (runs park at human
  review), sandbox host out of disk (prune + alert), bad release (redeploy previous tag + `alembic
  downgrade` only if the migration was reversible), stuck run (resume from checkpoint or mark
  `INVESTIGATION_INCONCLUSIVE`).

---

## 10. Definition of "deployed"

- [ ] `POST /runs` with a real GitHub issue URL on prod returns a `run_id`, and the run completes
      through the graph to one of the four terminal states.
- [ ] Human approval works end to end via the API against a live run.
- [ ] Every run persists: pinned SHA, tool calls (hash-chained), evidence citations, patch,
      validation logs, evaluation, cost.
- [ ] `git apply` of the produced `.patch` on a clean checkout of the pinned SHA succeeds.
- [ ] No repo command runs outside the sandbox; no secret reaches the model or a patch.
- [ ] `make eval` on prod-equivalent config reports recall@k, MRR, patch apply rate, test pass
      rate, regression rate, latency p50/p95, $/run.
- [ ] Adversarial suite green in CI; injected instructions do not alter behavior.
- [ ] CI builds and pushes images; a tagged release deploys to staging then prod with a
      one-command rollback.
- [ ] Nightly backups run and a restore has been tested.

---

## 11. Suggested calendar (adjust to your pace)

| Weeks | Sprints | Outcome |
|---|---|---|
| 1–2 | 1 | Deterministic issue→patch CLI, containerized, in CI |
| 3 | 2 | GitHub ingestion + reproducible snapshots |
| 4–5 | 3 | Structure-aware indexing |
| 6–7 | 4 | BM25 search over 500+ files with recall@k; hybrid if it wins |
| 8–9 | 5 | Single-agent graph, cited evidence, resumable |
| 10 | 6 | OpenAPI service + worker, deployed to staging |
| 11–12 | 7 | Multi-agent + human gate + audit trail |
| 13 | 8 | Evaluation suite + Langfuse + feedback scripts |
| 14–15 | 9 | Adversarial hardening + sandbox + prod deploy |

Solo and learning: expect ~1.5–2× this. Keep each sprint's gate non-negotiable — a green gate is
the checkpoint you can stop and resume from.
