# Issue-to-Patch Automation Pipeline

## Goal

Build and understand an advanced RAG application that accepts a GitHub issue, finds the relevant repository context, proposes a source-code change, emits a valid `.patch` file, and validates the result automatically.

The project should eventually demonstrate:

- Python and LangGraph orchestration
- GitHub API ingestion
- Repository cloning and Git operations
- BM25 and hybrid code retrieval
- OpenAPI-based service boundaries
- LLM tool use and structured outputs
- Multi-agent reasoning with human approval
- Automated patch validation
- Evaluation, latency/cost tracking, persistence, and adversarial testing

## Scope note: résumé stack vs. extended stack

The résumé line commits to **Python, GitHub API, BM25, OpenAPI, Git, and LLMs**. That is the mandatory core and it is enough to tell the whole story.

Everything else in this plan — LangGraph, dense embeddings, hybrid ranking, a dedicated vector store, multi-agent debate, containerized sandboxing — is an **extension** that maps onto a box in the architecture diagram. Build the core end to end first. Add an extension only when a measured limitation of the core justifies it, and record that measurement.

## Architecture Coverage

| Architecture element | Role in this project | Main output |
|---|---|---|
| **User Query** | GitHub issue URL, repository plus issue number, or a natural-language bug report | Normalized issue request |
| **Reasoning Engine** | Plans the investigation, chooses tools, and routes between retrieval, coding, validation, and revision | Investigation state and next action |
| **Planner** | Breaks the issue into hypotheses, search terms, files to inspect, and acceptance criteria | Investigation plan |
| **Tool Execution** | Calls GitHub, Git, search, tests, patch, and filesystem tools | Tool observations |
| **Conditional Router** | Decides whether more retrieval, code analysis, patch generation, validation, or human review is needed | Next graph node |
| **Multi-Agent System** | Separate specialist agents for issue analysis, code navigation, patch authoring, and review | Candidate diagnosis and patch |
| **Human Validation** | Gatekeeper approves risky actions; auditor checks traceability; strategist reviews the plan | Approval or revision request |
| **Evaluation** | LLM judge plus deterministic checks for correctness, precision, recall, latency, and cost | Evaluation report |
| **Database Layer** | Relational run metadata plus vector/document storage for indexed repository content | Durable state and retrieval data |
| **Data Sources** | GitHub issues, comments, PRs, commits, repository files, tests, and docs. The diagram's "documents / spreadsheets / images" map here to issue attachments: screenshots of errors or UI bugs (vision or OCR), pasted logs and stack traces, and the occasional CSV or spreadsheet in a bug report | Raw source artifacts |
| **Data Processing** | Parses, chunks, enriches, and indexes source material | Searchable document records |
| **Re-structuring Data** | Document parser and structure analyzer preserve paths, symbols, imports, tests, and line ranges | Structured code chunks |
| **Structure-Aware Chunking** | Table/code structure preservation, heading detection, and boundary detection adapted for source code | High-quality chunks |
| **Metadata Creation** | Summary generation, keyword extraction, and question generation for each chunk | Retrieval metadata |
| **Stress Testing** | Prompt injection, biased assumptions, information evasion, malformed issues, and adversarial repositories | Safety and robustness findings |
| **Feedback Loop** | Two loops. (1) *In-run*: a failed validation, weak evidence, or a low evaluation score routes back through the Conditional Router to `RevisePatch` or more retrieval. (2) *Cross-run*: aggregated evaluation and human feedback update prompts, retrieval settings, routing thresholds, and agent policies offline | Corrected run and improved future runs |

## Target Workflow

```text
GitHub issue
    -> issue ingestion and normalization
    -> repository snapshot
    -> source parsing and structure-aware indexing
    -> BM25 / hybrid retrieval
    -> investigation graph
    -> diagnosis and candidate patch
    -> human validation for risky actions
    -> isolated patch application
    -> tests, diff checks, and patch validation
    -> evaluation report
    -> approved .patch artifact
```

The system must never silently claim success. Every run ends in one of these states:

- `PATCH_VALIDATED`
- `PATCH_REQUIRES_HUMAN_REVIEW`
- `PATCH_REJECTED`
- `INVESTIGATION_INCONCLUSIVE`

## Learning and Build Plan

### Phase 0: Python, Git, and debugging foundations

Learn before adding RAG abstractions:

- Python typing, dataclasses, exceptions, generators, async I/O, and packaging
- `pytest`, fixtures, mocks, logging, and configuration management
- Git objects, branches, commits, diffs, hunks, merge conflicts, and patch files
- HTTP fundamentals, authentication, retries, pagination, rate limits, and timeouts
- JSON Schema and structured data validation

Build:

- A CLI that clones a repository into a temporary directory
- A command that reads an issue fixture and produces a normalized `IssueRequest`
- A safe Git wrapper that records every command and refuses unsafe paths
- Unit tests for malformed URLs, missing repositories, and invalid issue numbers

Exit criteria:

- You can explain a Git diff and apply/reject a patch programmatically.
- The CLI produces reproducible run metadata without calling an LLM.

### Phase 1: Data sources and ingestion

Implement the **Data Sources** layer with a GitHub client.

Learn:

- GitHub REST API and optional GraphQL API
- Issue bodies, comments, labels, linked pull requests, commits, and repository metadata
- Pagination, caching, rate-limit handling, and authenticated requests
- Snapshotting a repository at a known commit for reproducibility

Build nodes:

1. `FetchIssue`
2. `FetchIssueConversation`
3. `FetchRepositoryMetadata`
4. `CloneRepositorySnapshot`
5. `CollectRelatedChanges`

Store raw artifacts unchanged before transformation. Redact secrets and record source URLs, commit SHA, timestamps, and content hashes.

Deliverables:

- GitHub API client
- Raw artifact directory format
- Repository snapshot manifest
- Fixtures and mocked API tests

### Phase 2: Parsing, restructuring, and metadata

Implement the full **Data Processing** block.

Learn:

- Why naive fixed-size chunks fail for code
- ASTs and language-aware parsing
- File, class, function, import, test, and line-range relationships
- Chunk overlap, parent-child context, and deduplication
- Metadata that improves retrieval without polluting the text

Build:

- `DocumentParser` for Markdown, Python, JSON, YAML, and plain text
- `StructureAnalyzer` for symbols, imports, call sites, tests, and line ranges
- `StructureAwareChunker` that keeps functions/classes intact when possible
- `BoundaryDetector` for headings, symbols, files, and test sections
- `SummaryGenerator`, `KeywordExtractor`, and `QuestionGenerator`

Each indexed chunk should contain:

```json
{
  "chunk_id": "stable-hash",
  "repository": "owner/name",
  "commit_sha": "...",
  "path": "src/module.py",
  "language": "python",
  "symbol": "parse_issue",
  "line_start": 10,
  "line_end": 42,
  "content": "...",
  "summary": "...",
  "keywords": ["issue", "parser"],
  "references": ["tests/test_parser.py"],
  "content_hash": "..."
}
```

Exit criteria:

- A chunk can be traced back to an exact file and line range.
- Re-indexing the same commit is deterministic.
- Retrieval never mixes content from different repository commits.

### Phase 3: BM25 retrieval, then hybrid retrieval

Implement the **Database Layer** and retrieval service.

Start with BM25 because it makes ranking behavior inspectable and teaches the fundamentals:

- Tokenization for natural language and source code
- Identifier splitting such as `parseIssue` -> `parse`, `issue`
- Document frequency, term frequency, and length normalization
- Exact path, symbol, and error-message matches
- Top-k retrieval, score inspection, and ranking tests

Then add:

- Dense embeddings for semantic similarity
- Metadata filters by repository, commit, language, and path
- Reciprocal Rank Fusion or weighted hybrid ranking
- Query expansion from issue labels, stack traces, and symbols
- Parent-context expansion after retrieving a focused chunk

Advanced retrieval techniques to layer in once BM25 and basic hybrid work. Add each one only if it moves recall@k or MRR on the labeled benchmark:

- **Contextual retrieval**: prepend a short LLM-generated context line to each chunk before indexing (for example, "retry logic of the GitHub client").
- **Cross-encoder reranking** of the top ~50 candidates before passing evidence to the graph.
- **HyDE**: embed a hypothetical patch or answer sketch instead of the raw issue text.
- **Query decomposition** for issues that report several symptoms at once.
- **Stack-trace-aware retrieval**: parse trace frames into file, symbol, and line, and boost exact matches.
- **Diff-history retrieval**: use `git log -L` and `git blame` to pull the commits that last touched a candidate region.

Recommended storage split:

- PostgreSQL or SQLite for runs, artifacts, metadata, and audit records
- A vector-capable store for embeddings
- BM25 index for lexical retrieval
- Object/filesystem storage for raw repository snapshots and patches

Deliverables:

- `search(query, filters, top_k)` API
- Retrieval traces showing query, scores, selected chunks, and discarded candidates
- Recall@k and MRR evaluation on labeled issue-to-file examples
- A comparison report: BM25 vs dense vs hybrid

### Phase 4: OpenAPI service boundary

Expose stable services through **OpenAPI**.

Ordering note: scaffold these contracts now, but expect to finalize the request and response shapes after Phase 5, once the graph state is real. Keep the handlers thin so the schema follows the graph, not the reverse.

Define contracts for:

- `POST /runs` to start an issue investigation
- `GET /runs/{run_id}` to inspect state
- `POST /runs/{run_id}/approve` for human validation
- `GET /runs/{run_id}/retrieval` for evidence and scores
- `GET /runs/{run_id}/patch` for the generated artifact
- `POST /search` for repository retrieval
- `POST /validate-patch` for deterministic validation

Use Pydantic models for request and response validation. Generate and inspect the OpenAPI document. Test the API independently from the graph so orchestration logic is not hidden inside HTTP handlers.

### Phase 5: Reasoning engine and LangGraph state machine

Implement the **Reasoning Engine** as an explicit graph rather than a single agent loop.

Core state:

```python
class InvestigationState(TypedDict):
    run_id: str
    issue: IssueRequest
    repository: RepositorySnapshot | None
    plan: InvestigationPlan | None
    evidence: list[Evidence]
    hypotheses: list[Hypothesis]
    selected_files: list[SourceLocation]
    candidate_patch: PatchArtifact | None
    validation: ValidationReport | None
    human_decision: HumanDecision | None
    evaluation: EvaluationReport | None
    next_action: str
    errors: list[str]
```

Graph nodes:

1. `NormalizeRequest`
2. `PlanInvestigation`
3. `RetrieveIssueContext`
4. `RetrieveCodeContext`
5. `AnalyzeRootCause`
6. `SelectAdditionalEvidence`
7. `DraftPatch`
8. `RunPatchValidation`
9. `RequestHumanValidation`
10. `RevisePatch`
11. `EvaluateRun`
12. `PersistRun`

Router rules should be deterministic where possible:

- Missing repository or issue data -> ingestion retry/error
- Low retrieval confidence -> query expansion or human review
- Conflicting evidence -> additional retrieval
- Patch changes unrelated files -> reject or human review
- Tests fail -> revise once or reject
- Risky file or destructive command -> human gate
- Valid patch with passing checks -> evaluation and completion

### Phase 6: Multi-agent system

Implement the **Multi-Agent System** only after the single graph works.

Recommended agents:

- **Issue Analyst**: extracts symptoms, expected behavior, constraints, and acceptance criteria.
- **Repository Cartographer**: identifies likely modules, symbols, dependencies, and tests.
- **Root-Cause Analyst**: compares hypotheses against retrieved evidence.
- **Patch Author**: proposes the smallest change and emits a structured patch plan.
- **Test Strategist**: selects existing tests and proposes focused regression tests.
- **Patch Reviewer**: checks scope, correctness, security, and compatibility.

Agents must communicate through typed artifacts, not free-form hidden messages. Require each claim to cite evidence by chunk ID, file path, and line range.

Start with sequential execution. Later experiment with parallel retrieval and debate only where the evaluation data shows benefit.

### Phase 7: Patch generation and deterministic validation

The primary artifact is a standard Git `.patch`, not merely an LLM response.

Pick the patch format explicitly and keep it consistent:

- `git diff > fix.patch`, applied with `git apply` (and pre-checked with `git apply --check`): simplest, carries no commit identity.
- `git format-patch`, applied with `git am`: carries author, date, and commit message, and is what "a `.patch` file" usually means for a GitHub contribution.

This project produces `git format-patch`-style patches so every artifact is a self-contained, attributable commit against the recorded SHA. `git apply --check` is still used as a fast pre-validation gate.

Pipeline:

1. Generate a structured edit plan.
2. Apply edits only in an isolated temporary worktree.
3. Commit the change in the worktree and export it with `git format-patch`.
4. Check that the patch parses and applies cleanly with `git apply --check`.
5. Check changed paths against the allowed scope.
6. Run formatting, linting, type checks, and focused tests.
7. Optionally run the repository test suite with time limits.
8. Revert the worktree after artifact generation.
9. Store the patch, validation logs, and test results.

Validation checks:

- Patch syntax is valid.
- Patch applies to the recorded commit SHA.
- No binary or secret files were changed unexpectedly.
- No path traversal or command injection is possible.
- Diff is minimal and related to the issue.
- Tests cover the reported behavior.
- The patch can be reapplied from a clean checkout.

### Phase 8: Human validation and auditability

Implement the **Human Validation** block with three roles:

- **Gatekeeper**: approves repository access, commands, and patch application.
- **Auditor**: verifies evidence citations, tool calls, changed files, and final claims.
- **Strategist**: decides whether to broaden retrieval, revise the hypothesis, or stop.

Create an approval screen or CLI review that shows:

- Issue and acceptance criteria
- Evidence used by each conclusion
- Proposed files and diff
- Commands that will run
- Test results
- Risk flags
- Approve, reject, or request revision

Every tool call and decision must be append-only in the run audit log.

### Phase 9: Evaluation, observability, and feedback

Implement the **Evaluation** block with both deterministic and model-based evaluation.

Deterministic metrics:

- File retrieval recall@k
- Symbol retrieval recall@k
- MRR and nDCG
- Patch apply rate
- Test pass rate
- Regression rate
- Unrelated-file change rate
- Median and p95 latency
- Token and API cost per run

LLM judge dimensions, always grounded in artifacts:

- Root-cause correctness
- Evidence sufficiency
- Patch relevance
- Patch minimality
- Explanation faithfulness

Do not use the LLM judge as the only success signal. Store evaluation inputs, outputs, model versions, prompts, and scores so runs can be compared.

Use the **Feedback Loop** to update:

- BM25 tokenization and field weights
- Chunking boundaries and metadata
- Query expansion rules
- Router thresholds
- Agent prompts and tool permissions
- Human-review triggers

### Phase 10: Stress testing and production hardening

Implement the **Stress Testing** block.

Test categories from the architecture:

- **Prompt Injection**: malicious text in issue bodies, comments, code, README files, and test fixtures.
- **Biased Opinion**: misleading issue descriptions that force an unsupported diagnosis.
- **Information Evasion**: incomplete stack traces, vague reports, missing reproduction steps, and contradictory comments.
- **Repository attacks**: hostile filenames, huge files, symlinks, generated code, secrets, and untrusted build scripts.
- **Operational failures**: GitHub rate limits, network timeouts, deleted branches, stale commits, partial indexes, and model outages.

Security controls:

- Treat repository content as untrusted data.
- Keep system instructions separate from retrieved text.
- Restrict tools and shell commands with an allowlist.
- Run repository commands in a sandbox with time, memory, and network limits.
- Never expose tokens to the model or patch artifact.
- Enforce repository and commit boundaries at retrieval time.
- Require human approval for writes and risky execution.

## Suggested Repository Layout

```text
issue-to-patch/
├── pyproject.toml
├── README.md
├── RAG_LEARNING_PLAN.md
├── src/issue_to_patch/
│   ├── api/                 # OpenAPI routes and schemas
│   ├── config/              # Settings and model configuration
│   ├── ingestion/           # GitHub API and repository snapshots
│   ├── processing/          # Parsing, analysis, chunking, metadata
│   ├── retrieval/           # BM25, embeddings, hybrid ranking
│   ├── graph/               # LangGraph state and conditional routing
│   ├── agents/              # Specialist agents and typed artifacts
│   ├── patching/            # Diff creation, application, validation
│   ├── evaluation/          # Metrics, judges, traces, cost
│   ├── safety/              # Permissions, sandboxing, stress tests
│   └── persistence/         # SQL, vector, object storage adapters
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── retrieval/
│   ├── graph/
│   ├── patch_fixtures/
│   └── adversarial/
├── evals/
│   ├── labeled_issues.jsonl
│   └── benchmark_repositories/
└── artifacts/               # Local run output, ignored by Git
```

## Recommended Technology Progression

1. Python, `pytest`, `httpx`, Pydantic, Git CLI.
2. GitHub REST API and repository fixtures.
3. SQLite/PostgreSQL for durable state.
4. BM25 with an inspectable search implementation.
5. Tree-sitter or another parser for structure-aware code analysis.
6. LangGraph for stateful orchestration.
7. An embedding model and hybrid retrieval.
8. FastAPI and generated OpenAPI contracts.
9. A model provider with structured output and tool calling.
10. Containerized sandboxing, tracing, and evaluation dashboards.

Choose one model provider and one storage stack initially. Learn the abstractions before adding provider-specific complexity.

## Milestone Schedule

### Milestone 1: Deterministic issue-to-diff prototype

Input a local issue fixture and repository snapshot. Produce a manually guided, valid patch with tests. No LLM required.

### Milestone 2: BM25 code search

Index 500+ files, retrieve relevant files and symbols, expose scores, and measure recall@k.

### Milestone 3: Single-agent RAG investigator

Use a graph with planning, retrieval, diagnosis, patch drafting, and validation. All claims cite retrieved evidence.

### Milestone 4: GitHub and OpenAPI integration

Run from a GitHub issue URL through an API. Persist state and artifacts. Make runs resumable.

### Milestone 5: Multi-agent review and human gate

Add specialist agents, approval checkpoints, audit trails, and safe tool permissions.

### Milestone 6: Advanced retrieval and evaluation

Add hybrid ranking, query expansion, reranking, parent context, labeled benchmarks, and judge-assisted evaluation.

### Milestone 7: Production hardening

Add adversarial tests, sandboxing, rate-limit recovery, observability, cost controls, and failure-state recovery.

## Definition of Done

The project is advanced enough to discuss in depth when it can:

- Start from a GitHub issue and pin the repository to a commit SHA.
- Index a repository with structure-aware chunks and traceable metadata.
- Retrieve relevant code using BM25 and compare it with hybrid retrieval.
- Route work through a stateful reasoning graph with explicit stop conditions.
- Use multiple agents whose claims cite evidence and whose outputs are typed.
- Generate a standard `.patch` file in an isolated worktree.
- Validate patch syntax, scope, application, tests, and regression risk.
- Pause for human approval when confidence or risk requires it.
- Persist the complete run, tool calls, evidence, decisions, patch, and evaluation.
- Report precision, recall, latency, cost, and failure modes.
- Detect prompt injection and reject unsafe repository instructions.

## How to Study Each Feature

For every component, follow the same loop:

1. Implement the smallest deterministic version.
2. Write a test that exposes its failure mode.
3. Add an LLM or probabilistic component only after the baseline is measurable.
4. Record the changed metric and inspect failures manually.
5. Document the tradeoff in the run report.

This keeps the project a learning system rather than a collection of opaque agent prompts.
