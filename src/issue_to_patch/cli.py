"""Command-line entrypoint.

``version`` / ``config`` (bootstrap) · ``run`` (Sprint 1, deterministic) ·
``ingest`` (Sprint 2) · ``index`` / ``show-chunk`` (Sprint 3) ·
``search`` / ``eval-retrieval`` / ``build-eval-set`` (Sprint 4) ·
``investigate`` (Sprint 5, the LangGraph reasoning engine).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from issue_to_patch import __version__
from issue_to_patch.config import get_settings
from issue_to_patch.logging import configure_logging
from issue_to_patch.run_states import RunState

if TYPE_CHECKING:
    from issue_to_patch.graph import InvestigationHandle

app = typer.Typer(add_completion=False, help="Issue-to-Patch Automation Pipeline")

# Exit code per terminal state, so shell / CI can branch on the outcome.
_EXIT_CODES = {
    RunState.PATCH_VALIDATED: 0,
    RunState.PATCH_REQUIRES_HUMAN_REVIEW: 10,
    RunState.PATCH_REJECTED: 20,
    RunState.INVESTIGATION_INCONCLUSIVE: 30,
}


@app.callback()
def _main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def config() -> None:
    """Print the effective configuration with secrets redacted."""
    settings = get_settings()
    typer.echo(json.dumps(json.loads(settings.model_dump_json()), indent=2, default=str))


@app.command()
def run(
    issue: Annotated[str, typer.Option(help="Issue URL, owner/repo#n, fixture path, or raw text")],
    repo: Annotated[str, typer.Option(help="Local path or URL of the repository to snapshot")],
    edit_plan: Annotated[Path, typer.Option(help="Path to an EditPlan JSON file")],
    scope: Annotated[
        list[str] | None,
        typer.Option(help="Glob(s) the patch must stay within; defaults to the plan's own paths"),
    ] = None,
) -> None:
    """Deterministically turn an issue + repo + edit plan into a validated .patch (no LLM)."""
    from issue_to_patch.pipeline import run_deterministic

    result = run_deterministic(
        issue_ref=issue,
        repo_source=repo,
        edit_plan_path=edit_plan,
        allowed_scope=scope,
    )

    typer.echo(f"run_id:        {result.run_id}")
    typer.echo(f"issue:         {result.issue.reference}")
    typer.echo(f"base_sha:      {result.snapshot.commit_sha if result.snapshot else '-'}")
    typer.echo(f"changed_files: {result.patch.changed_files if result.patch else []}")
    typer.echo(f"content_hash:  {result.content_hash}")
    typer.echo(f"state:         {result.state.value}")
    if result.validation:
        for check in result.validation.checks:
            typer.echo(f"  [{check.status.value:>4}] {check.name} {check.detail}".rstrip())
    typer.echo(f"artifacts:     {result.run_dir}")
    raise typer.Exit(_EXIT_CODES[result.state])


@app.command()
def ingest(
    issue_url: Annotated[str, typer.Option(help="GitHub issue URL or owner/repo#n")],
    token: Annotated[
        str | None, typer.Option(help="GitHub token (else ITP_GITHUB_TOKEN, else anonymous)")
    ] = None,
    repo_source: Annotated[
        str | None,
        typer.Option(help="Override the clone source (local path or URL); default = clone_url"),
    ] = None,
    ref: Annotated[
        str | None, typer.Option(help="Branch, tag, or SHA to pin; default = repo default branch")
    ] = None,
) -> None:
    """Fetch a GitHub issue + its repo into a raw artifact set and a pinned snapshot."""
    import asyncio
    import uuid

    from issue_to_patch.ingestion.github import GitHubClient
    from issue_to_patch.ingestion.ingest import ingest_issue
    from issue_to_patch.logging import bind_run_id

    settings = get_settings()
    run_id = uuid.uuid4().hex[:16]
    run_dir = settings.artifacts_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    resolved_token = token or (
        settings.github_token.get_secret_value() if settings.github_token else None
    )

    async def _go() -> None:
        async with GitHubClient(token=resolved_token, base_url=settings.github_api_base) as client:
            result = await ingest_issue(
                issue_url, run_dir, client=client, repo_source=repo_source, ref=ref
            )
        issue = result.conversation.issue
        typer.echo(f"run_id:         {run_id}")
        typer.echo(f"repo:           {result.repository.full_name}")
        typer.echo(f"issue:          #{issue.number} {issue.title}")
        typer.echo(f"comments:       {len(result.conversation.comments)}")
        typer.echo(f"related:        {len(result.related_changes)}")
        typer.echo(f"attachments:    {len(result.attachment_urls)}")
        typer.echo(f"commit_sha:     {result.snapshot.commit_sha}")
        typer.echo(f"raw artifacts:  {len(result.raw_records)} in {run_dir / 'raw'}")

    with bind_run_id(run_id):
        asyncio.run(_go())


@app.command()
def index(
    snapshot: Annotated[
        Path, typer.Option(help="Snapshot directory (contains repo/ and snapshot_manifest.json)")
    ],
    max_lines: Annotated[int, typer.Option(help="Max lines before a big class is split")] = 200,
) -> None:
    """Parse a snapshot into structure-aware chunks and store them."""
    from issue_to_patch.ingestion.snapshot import load_snapshot
    from issue_to_patch.persistence import Store
    from issue_to_patch.processing import index_snapshot

    settings = get_settings()
    snap = load_snapshot(snapshot)
    store = Store(settings.database_url)
    store.create_all()
    result = index_snapshot(snap, store, max_lines=max_lines, out_dir=Path(snapshot).parent)

    typer.echo(f"repository:     {result.repository}")
    typer.echo(f"commit_sha:     {result.commit_sha}")
    typer.echo(f"files seen:     {result.files_seen}")
    typer.echo(f"files indexed:  {result.files_indexed}")
    typer.echo(f"files skipped:  {result.files_skipped}")
    typer.echo(f"chunks written: {result.chunks_written}")
    typer.echo(f"chunks.jsonl:   {Path(snapshot).parent / 'chunks.jsonl'}")


@app.command("show-chunk")
def show_chunk(
    chunk_id: Annotated[str, typer.Argument(help="Chunk id from `index` output / chunks.jsonl")],
    metadata: Annotated[bool, typer.Option(help="Also print summary/keywords/questions")] = False,
) -> None:
    """Print the stored source for one chunk (byte-identical to the file)."""
    from issue_to_patch.persistence import Store

    row = Store(get_settings().database_url).get_chunk(chunk_id)
    if row is None:
        typer.echo(f"no chunk {chunk_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"# {row.path}:{row.line_start}-{row.line_end}  ({row.kind}  symbol={row.symbol})")
    if metadata:
        typer.echo(f"# summary:   {row.summary}")
        typer.echo(f"# keywords:  {', '.join(row.keywords)}")
        typer.echo(f"# questions: {' | '.join(row.questions)}")
        typer.echo(f"# refs:      {', '.join(row.reference_paths)}")
    typer.echo("-" * 72)
    typer.echo(row.content)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Issue text / bug description to search for")],
    snapshot: Annotated[
        Path | None, typer.Option(help="Snapshot dir (reads repo + sha from its manifest)")
    ] = None,
    repo: Annotated[str | None, typer.Option(help="owner/name (if not using --snapshot)")] = None,
    sha: Annotated[str | None, typer.Option(help="commit SHA (if not using --snapshot)")] = None,
    mode: Annotated[str, typer.Option(help="bm25 | dense | hybrid")] = "hybrid",
    top_k: Annotated[int, typer.Option(help="How many results")] = 10,
    explain: Annotated[bool, typer.Option(help="Show per-term BM25 contributions")] = False,
) -> None:
    """Rank indexed chunks against a query. Shows scores; --explain shows why."""
    from issue_to_patch.ingestion.snapshot import load_snapshot
    from issue_to_patch.persistence import Store
    from issue_to_patch.retrieval import RetrievalMode, RetrievalService, SearchFilters

    if snapshot is not None:
        snap = load_snapshot(snapshot)
        repo, sha = snap.repo, snap.commit_sha
    if not repo or not sha:
        typer.echo("give --snapshot, or both --repo and --sha", err=True)
        raise typer.Exit(2)

    service = RetrievalService(Store(get_settings().database_url))
    trace = service.search(
        query,
        SearchFilters(repository=repo, commit_sha=sha),
        top_k=top_k,
        mode=RetrievalMode(mode),
    )
    typer.echo(
        f"mode={trace.mode.value}  candidates={trace.candidates_considered}  "
        f"terms={len(trace.expanded_terms)}"
    )
    for r in trace.results:
        tag = " [parent-context]" if r.added_as_parent_context else ""
        typer.echo(
            f"  {r.rank:>2}. {r.score:>7.4f}  {r.path}:{r.line_start}-{r.line_end}  "
            f"{r.symbol or r.kind}{tag}"
        )
        if explain and r.chunk_id in trace.term_contributions:
            top_terms = trace.term_contributions[r.chunk_id][:5]
            typer.echo(
                "        " + ", ".join(f"{c.term}(tf={c.tf}, +{c.contribution})" for c in top_terms)
            )


@app.command("eval-retrieval")
def eval_retrieval(
    labeled: Annotated[Path, typer.Option(help="Path to labeled_issues.jsonl")],
    top_k: Annotated[int, typer.Option(help="Cutoff for retrieval")] = 10,
) -> None:
    """Score BM25 vs dense vs hybrid on labeled issue->file examples."""
    from issue_to_patch.persistence import Store
    from issue_to_patch.retrieval import RetrievalService, evaluate, load_labeled_issues
    from issue_to_patch.retrieval.evaluation import render_report

    issues = load_labeled_issues(labeled)
    if not issues:
        typer.echo("no labeled issues found", err=True)
        raise typer.Exit(2)
    service = RetrievalService(Store(get_settings().database_url))
    report = evaluate(service, issues, top_k=top_k)
    typer.echo(render_report(report))


@app.command("build-eval-set")
def build_eval_set(
    repo: Annotated[str, typer.Option(help="owner/name, e.g. langchain-ai/langchain")],
    commit_sha: Annotated[str, typer.Option(help="The SHA you indexed (pins the labels to it)")],
    out: Annotated[Path, typer.Option(help="Output JSONL path")] = Path(
        "evals/labeled_issues.jsonl"
    ),
    count: Annotated[int, typer.Option(help="Target number of labeled examples")] = 20,
    max_files: Annotated[int, typer.Option(help="Skip PRs touching more code files than this")] = 4,
    query_filter: Annotated[
        str, typer.Option(help="Extra GitHub search qualifiers, e.g. 'label:bug'")
    ] = "",
    token: Annotated[
        str | None, typer.Option(help="GitHub token (else ITP_GITHUB_TOKEN, else anonymous)")
    ] = None,
) -> None:
    """Mine merged bug-fix PRs into a labeled retrieval-eval set (issue text -> changed files)."""
    import asyncio

    from issue_to_patch.ingestion.github import GitHubClient
    from issue_to_patch.persistence import Store
    from issue_to_patch.retrieval.dataset import build_labeled_issues, write_jsonl
    from issue_to_patch.retrieval.evaluation import LabeledIssue

    settings = get_settings()
    resolved_token = token or (
        settings.github_token.get_secret_value() if settings.github_token else None
    )
    store = Store(settings.database_url)
    indexed = store.indexed_paths(repo, commit_sha)
    if not indexed:
        typer.echo(f"no chunks indexed for {repo}@{commit_sha[:12]} — run `index` first", err=True)
        raise typer.Exit(2)

    async def _go() -> list[LabeledIssue]:
        async with GitHubClient(token=resolved_token, base_url=settings.github_api_base) as client:
            return await build_labeled_issues(
                client,
                repo,
                commit_sha,
                indexed_paths=indexed,
                count=count,
                max_files=max_files,
                extra_query=query_filter,
            )

    rows = asyncio.run(_go())
    if not rows:
        typer.echo(
            "no usable PRs found — try --query-filter 'label:bug' or a larger --count", err=True
        )
        raise typer.Exit(1)
    write_jsonl(rows, out)
    typer.echo(f"wrote {len(rows)} labeled examples to {out}")
    for r in rows:
        typer.echo(f"  {r.issue_id:<32} gold={r.gold_files}")


def _print_investigation(handle: InvestigationHandle) -> RunState | None:
    """Print run_id, issue, top hypothesis + cites, changed_files + full
    diff, and validation checks. Returns the terminal RunState once the run
    has finished, or None while it's paused awaiting human review. Shared by
    `investigate` and `auto` so this view can't drift between the two."""
    state = handle.state
    typer.echo(f"run_id:        {handle.run_id}")
    typer.echo(
        f"view in browser: http://127.0.0.1:8000/ui/runs/{handle.run_id}  (needs `make serve`)"
    )
    if (parsed_issue := state.get("issue")) is not None:
        typer.echo(f"issue:         {parsed_issue.reference}")
    if hypotheses := state.get("hypotheses"):
        top = hypotheses[0]
        typer.echo(f"root cause:    {top.summary} (confidence={top.confidence:.2f})")
        for loc in top.cites:
            typer.echo(
                f"  cites:       {loc.path}:{loc.line_start}-{loc.line_end} [{loc.chunk_id}]"
            )
    if (patch := state.get("candidate_patch")) is not None:
        typer.echo(f"changed_files: {patch.changed_files}")
        # A human can't approve/reject/revise a change they can't see.
        typer.echo("--- diff ---")
        typer.echo(patch.patch_text)
        typer.echo("--- end diff ---")
    if (validation := state.get("validation")) is not None:
        for check in validation.checks:
            typer.echo(f"  [{check.status.value:>4}] {check.name} {check.detail}".rstrip())
    if handle.awaiting_human:
        typer.echo("status:        AWAITING_HUMAN_REVIEW")
        return None
    final_state = state["final_state"]
    assert final_state is not None  # PersistRun always sets it once the graph reaches END
    typer.echo(f"state:         {final_state.value}")
    if errors := state.get("errors"):
        for error in errors:
            typer.echo(f"  error:       {error}")
    return final_state


@app.command()
def investigate(
    issue: Annotated[
        str | None, typer.Option(help="Issue URL, owner/repo#n, fixture path, or raw text")
    ] = None,
    snapshot: Annotated[
        Path | None, typer.Option(help="Snapshot dir (from `ingest`) — must already be `index`ed")
    ] = None,
    scope: Annotated[
        list[str] | None, typer.Option(help="Glob(s) the patch must stay within")
    ] = None,
    resume: Annotated[
        str | None, typer.Option(help="Resume an existing run_id instead of starting a new one")
    ] = None,
    decision: Annotated[
        str | None,
        typer.Option(help="Resolve the human gate immediately: approve | reject | revise"),
    ] = None,
    reason: Annotated[str, typer.Option(help="Reason recorded with --decision")] = "",
    reviewer: Annotated[str, typer.Option(help="Name recorded with --decision")] = "human",
    role: Annotated[
        str, typer.Option(help="gatekeeper | auditor | strategist — see safety/permissions.py")
    ] = "gatekeeper",
) -> None:
    """Run the reasoning graph on an issue: investigate, draft a patch, pause for review.

    Uses the same durable (SQLite-file) checkpointer as the API, under
    ``<artifacts_dir>/checkpoints.db`` — a run started here can be resumed by
    a later ``investigate --resume <run_id>``, or by the API, and vice versa.
    Pass ``--decision`` to resolve the human gate immediately; omit it to stop
    at the pending review and inspect it first.
    """
    from issue_to_patch.graph import (
        GraphDependencies,
        HumanDecision,
        build_dependencies,
        load_investigation,
        resume_investigation,
        sqlite_checkpointer,
        start_investigation,
    )
    from issue_to_patch.ingestion.snapshot import load_snapshot
    from issue_to_patch.persistence import Store

    settings = get_settings()
    store = Store(settings.database_url)
    deps: GraphDependencies = build_dependencies(store=store, settings=settings)
    checkpointer = sqlite_checkpointer(settings.artifacts_dir / "checkpoints.db")

    if resume:
        from issue_to_patch.graph import UnknownRun

        try:
            handle = load_investigation(resume, deps, checkpointer=checkpointer)
        except UnknownRun:
            typer.echo(f"no such run: {resume}", err=True)
            raise typer.Exit(2) from None
    else:
        if not issue or not snapshot:
            typer.echo("give --issue and --snapshot, or --resume <run_id>", err=True)
            raise typer.Exit(2)
        snap = load_snapshot(snapshot)
        if not store.indexed_paths(snap.repo or snap.source, snap.commit_sha):
            typer.echo("no chunks indexed for this snapshot — run `index` first", err=True)
            raise typer.Exit(2)
        handle = start_investigation(
            issue_ref=issue,
            repository=snap,
            deps=deps,
            allowed_scope=scope,
            checkpointer=checkpointer,
        )

    if decision and handle.awaiting_human:
        if decision not in {"approve", "reject", "revise"}:
            typer.echo("--decision must be one of approve | reject | revise", err=True)
            raise typer.Exit(2)
        if role not in {"gatekeeper", "auditor", "strategist"}:
            typer.echo("--role must be one of gatekeeper | auditor | strategist", err=True)
            raise typer.Exit(2)
        handle = resume_investigation(
            handle, HumanDecision(decision=decision, reason=reason, reviewer=reviewer, role=role)
        )

    final_state = _print_investigation(handle)
    if final_state is None:
        typer.echo("rerun with --decision to resolve")
        raise typer.Exit(10)
    raise typer.Exit(_EXIT_CODES[final_state])


@app.command()
def auto(
    issue_url: Annotated[
        str | None, typer.Argument(help="GitHub issue URL or owner/repo#n; prompted if omitted")
    ] = None,
    token: Annotated[
        str | None, typer.Option(help="GitHub token (else ITP_GITHUB_TOKEN, else anonymous)")
    ] = None,
    repo_source: Annotated[
        str | None,
        typer.Option(help="Override the clone source (local path or URL); default = clone_url"),
    ] = None,
    ref: Annotated[
        str | None, typer.Option(help="Branch, tag, or SHA to pin; default = repo default branch")
    ] = None,
    scope: Annotated[
        list[str] | None, typer.Option(help="Glob(s) the patch must stay within")
    ] = None,
    max_lines: Annotated[int, typer.Option(help="Max lines before a big class is split")] = 200,
    reviewer: Annotated[str | None, typer.Option(help="Reviewer name; prompted if omitted")] = None,
) -> None:
    """One interactive session: ingest -> index -> investigate -> human
    review -> optional build -> optional push -> optional pull request.

    Calls the same functions ``ingest``/``index``/``investigate`` call
    directly (never shells out to those commands), sharing one run_id across
    every stage. The human-review gate is never skipped: build only runs
    after an explicit approve, push is a *separate* confirmation after that,
    and opening a pull request is a third, separate confirmation after
    push — never implied by any of the others.
    """
    import asyncio
    import uuid

    from issue_to_patch.auto_run import auto_index, auto_ingest, auto_investigate
    from issue_to_patch.graph import (
        GraphDependencies,
        HumanDecision,
        build_dependencies,
        resume_investigation,
        sqlite_checkpointer,
    )
    from issue_to_patch.ingestion.errors import GitHubAPIError, IngestionError
    from issue_to_patch.logging import bind_run_id
    from issue_to_patch.patching import materialize_branch, push_branch
    from issue_to_patch.persistence import Store
    from issue_to_patch.pull_request import ForkRemoteNotGitHub, create_pull_request

    if not issue_url:
        issue_url = typer.prompt("Issue URL")

    settings = get_settings()
    run_id = uuid.uuid4().hex[:16]
    run_dir = settings.artifacts_dir / run_id

    with bind_run_id(run_id):
        typer.echo(f"run_id:        {run_id}")
        typer.echo("==> 1. fetching issue + repo...")
        try:
            ingest_result = asyncio.run(
                auto_ingest(
                    issue_url,
                    run_dir,
                    settings=settings,
                    token=token,
                    repo_source=repo_source,
                    ref=ref,
                )
            )
            typer.echo(f"    repo:      {ingest_result.repository.full_name}")
            typer.echo(f"    commit:    {ingest_result.snapshot.commit_sha}")
        except IngestionError as exc:
            typer.echo(f"ingest failed: {exc}", err=True)
            raise typer.Exit(1) from exc

        typer.echo("==> 2. indexing repository...")
        store = Store(settings.database_url)
        store.create_all()
        try:
            snap, index_result = auto_index(run_dir, store, max_lines=max_lines)
        except Exception as exc:
            typer.echo(f"index failed: {type(exc).__name__}: {exc}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"    chunks:    {index_result.chunks_written}")

        typer.echo("==> 3. investigating...")
        deps: GraphDependencies = build_dependencies(store=store, settings=settings)
        checkpointer = sqlite_checkpointer(settings.artifacts_dir / "checkpoints.db")
        try:
            handle = auto_investigate(
                issue_url, snap, deps, checkpointer, run_id=run_id, scope=scope
            )
        except Exception as exc:
            typer.echo(f"investigation failed: {type(exc).__name__}: {exc}", err=True)
            typer.echo("re-run with ITP_LOG_LEVEL=DEBUG for the full traceback", err=True)
            raise typer.Exit(1) from exc

        while True:
            final_state = _print_investigation(handle)
            if final_state is not None:
                break
            decision = typer.prompt("Decision [approve/reject/revise]")
            if decision not in {"approve", "reject", "revise"}:
                typer.echo("must be approve | reject | revise", err=True)
                continue
            reason = typer.prompt("Reason", default="")
            reviewer_name = reviewer or typer.prompt("Reviewer name", default="human")
            role = typer.prompt("Role [gatekeeper/auditor/strategist]", default="gatekeeper")
            if role not in {"gatekeeper", "auditor", "strategist"}:
                typer.echo("must be gatekeeper | auditor | strategist", err=True)
                continue
            try:
                handle = resume_investigation(
                    handle,
                    HumanDecision(
                        decision=decision, reason=reason, reviewer=reviewer_name, role=role
                    ),
                )
            except Exception as exc:
                typer.echo(f"resume failed: {type(exc).__name__}: {exc}", err=True)
                raise typer.Exit(1) from exc

        if final_state is not RunState.PATCH_VALIDATED:
            raise typer.Exit(_EXIT_CODES[final_state])

        if not typer.confirm(
            "Build this patch onto a persistent branch you can inspect/build/test?",
            default=True,
        ):
            raise typer.Exit(0)

        patch = handle.state["candidate_patch"]
        assert patch is not None  # PATCH_VALIDATED guarantees a candidate_patch
        branch_name = f"itp/{run_id}"
        # Must stay inside the snapshot dir (snap.root_path.parent) —
        # materialize_branch's SafeGit is rooted there, and a sibling of it
        # (e.g. plain run_dir/"branch") would be refused as an escape.
        branch_dir = snap.root_path.parent / "branch"
        try:
            materialize_branch(snap, patch, branch_dir, branch_name)
        except Exception as exc:
            typer.echo(f"build failed: {type(exc).__name__}: {exc}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"branch ready:  {branch_dir}  (git branch: {branch_name})")
        typer.echo(f"  cd {branch_dir}  # then run your own build/tests")

        if not typer.confirm("Push this branch to your configured remote now?", default=False):
            raise typer.Exit(0)

        if settings.push_remote_url is None:
            typer.echo(
                "ITP_PUSH_REMOTE_URL is not configured — set it to a remote you "
                "own (e.g. your own fork) and re-run; nothing was pushed.",
                err=True,
            )
            raise typer.Exit(0)
        remote = settings.push_remote_url.get_secret_value()
        # Cheap, best-effort nudge — no extra API calls: if the configured
        # remote's string looks like it could be the same repo the issue
        # came from, double-check before ever pushing there.
        if (
            snap.repo
            and snap.repo.split("/")[-1] in remote
            and not typer.confirm(
                f"warning: this remote looks like it could be the same repo the "
                f"issue came from ({snap.repo}) — are you sure this is a fork/remote you own?",
                default=False,
            )
        ):
            typer.echo("push cancelled", err=True)
            raise typer.Exit(0)
        result = push_branch(branch_dir, branch_name, remote)
        if result.ok:
            typer.echo(f"pushed {result.branch} to {result.remote_display}")
        else:
            typer.echo(f"push failed: {result.detail}", err=True)
            raise typer.Exit(1)

        if not typer.confirm("Create a pull request now?", default=False):
            raise typer.Exit(0)

        top_hypothesis = handle.state["hypotheses"][0] if handle.state.get("hypotheses") else None
        default_title = f"Fix: {top_hypothesis.summary if top_hypothesis else issue_url}"
        pr_title = typer.prompt("PR title", default=default_title)
        pr_body = typer.prompt("PR description", default=f"Fixes {issue_url}")
        pr_token = token or (
            settings.github_token.get_secret_value() if settings.github_token else None
        )
        if pr_token is None:
            pr_token = typer.prompt(
                "GitHub token for creating the PR (needs 'repo' scope)", hide_input=True
            )
        try:
            pr_result = asyncio.run(
                create_pull_request(
                    run_dir=run_dir,
                    branch_name=branch_name,
                    fork_remote_url=remote,
                    title=pr_title,
                    body=pr_body,
                    base=None,
                    token=pr_token,
                )
            )
        except (ForkRemoteNotGitHub, GitHubAPIError) as exc:
            typer.echo(f"pull request failed: {exc}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"pull request opened: {pr_result.url}")


@app.command()
def audit(
    run_id: Annotated[str, typer.Argument(help="run_id from `investigate` or `POST /runs`")],
) -> None:
    """Print a run's full audit trail: every tool call and human decision, in
    order, with both append-only hash chains verified (Phase 8)."""
    from issue_to_patch.persistence import Store, build_audit_trail, render_audit_trail

    store = Store(get_settings().database_url)
    trail = build_audit_trail(store, run_id)
    typer.echo(render_audit_trail(trail))
    if not trail.found:
        raise typer.Exit(1)
    if not trail.is_tamper_evident_intact:
        raise typer.Exit(3)


@app.command("create-user")
def create_user(
    username: Annotated[str, typer.Argument(help="Unique name for this account")],
    role: Annotated[
        str, typer.Option(help="gatekeeper | auditor | strategist — see safety/permissions.py")
    ] = "gatekeeper",
) -> None:
    """Create a real per-user account with its own API key. The key is
    printed exactly once here — there is no way to recover it afterward;
    disable the user and create a new one if it's lost."""
    from issue_to_patch.persistence import Store

    if role not in {"gatekeeper", "auditor", "strategist"}:
        typer.echo("--role must be one of gatekeeper | auditor | strategist", err=True)
        raise typer.Exit(2)
    store = Store(get_settings().database_url)
    try:
        api_key = store.create_user(username=username, role=role)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"user created: {username} ({role})")
    typer.echo(f"API key (copy now, shown only once): {api_key}")


@app.command("list-users")
def list_users() -> None:
    """Username, role, created_at, disabled — never the key itself; only
    its hash is ever stored, so there is nothing secret to print here."""
    from issue_to_patch.persistence import Store

    store = Store(get_settings().database_url)
    users = store.list_users()
    if not users:
        typer.echo("no users yet — see `create-user`")
        return
    for user in users:
        status = "disabled" if user.disabled_at is not None else "active"
        typer.echo(f"{user.username:<20} {user.role:<12} {status:<10} {user.created_at}")


@app.command("disable-user")
def disable_user(
    username: Annotated[str, typer.Argument(help="Username to revoke access for")],
) -> None:
    """Revokes access without deleting the user or their audit history —
    past decisions they made stay exactly as recorded."""
    from issue_to_patch.persistence import Store

    store = Store(get_settings().database_url)
    try:
        store.disable_user(username)
    except KeyError:
        typer.echo(f"no such user: {username}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"disabled: {username}")


@app.command()
def judge(run_id: Annotated[str, typer.Argument(help="run_id to judge")]) -> None:
    """Score one completed run on the 5 grounded dimensions (Phase 9) — never
    the sole success signal, just an additional comparable one stored
    alongside the run's own deterministic RunState."""
    from issue_to_patch.evaluation import judge_run
    from issue_to_patch.graph import (
        UnknownRun,
        build_dependencies,
        load_investigation,
        sqlite_checkpointer,
    )
    from issue_to_patch.persistence import Store

    settings = get_settings()
    store = Store(settings.database_url)
    deps = build_dependencies(store=store, settings=settings)
    checkpointer = sqlite_checkpointer(settings.artifacts_dir / "checkpoints.db")
    try:
        handle = load_investigation(run_id, deps, checkpointer=checkpointer)
    except UnknownRun:
        typer.echo(f"no such run: {run_id}", err=True)
        raise typer.Exit(2) from None

    record = judge_run(run_id, handle.state, deps)
    typer.echo(record.model_dump_json(indent=2))


@app.command("eval-suite")
def eval_suite(
    labeled: Annotated[
        Path | None, typer.Option(help="Path to labeled_issues.jsonl for retrieval metrics")
    ] = None,
    top_k: Annotated[int, typer.Option(help="Cutoff for retrieval")] = 10,
    run_id: Annotated[
        list[str] | None,
        typer.Option("--run-id", help="Already-completed run_id to include (repeatable)"),
    ] = None,
    judge_runs: Annotated[
        bool,
        typer.Option(
            "--judge", help="Also run the LLM judge on each --run-id (uses ITP_LLM_PROVIDER)"
        ),
    ] = False,
    out_json: Annotated[Path, typer.Option(help="Write the JSON report here")] = Path(
        "evals/report.json"
    ),
    out_html: Annotated[Path, typer.Option(help="Write the HTML report here")] = Path(
        "evals/report.html"
    ),
) -> None:
    """Build the evaluation suite report (Phase 9): retrieval metrics from a
    labeled set and/or deterministic + judge metrics from already-completed
    runs. Give --labeled, --run-id (repeatable), or both — driving brand new
    graph runs from scratch isn't done here; point --run-id at runs you
    already made (with a real provider, or FakeLLM for a plumbing smoke test).
    """
    from issue_to_patch.config.settings import LLMProvider
    from issue_to_patch.evaluation import build_suite_report, render_suite_report_html
    from issue_to_patch.graph import build_dependencies
    from issue_to_patch.persistence import Store
    from issue_to_patch.retrieval.evaluation import render_report

    if not labeled and not run_id:
        typer.echo("give --labeled, --run-id (repeatable), or both", err=True)
        raise typer.Exit(2)

    settings = get_settings()
    store = Store(settings.database_url)
    if judge_runs and settings.llm_provider is LLMProvider.FAKE:
        typer.echo(
            "warning: ITP_LLM_PROVIDER=fake — judge scores will be plumbing-only, not meaningful",
            err=True,
        )
    judge_deps = build_dependencies(store=store, settings=settings) if judge_runs else None

    report = build_suite_report(
        store, labeled_path=labeled, top_k=top_k, run_ids=run_id, judge_deps=judge_deps
    )

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(report.model_dump_json(indent=2), "utf-8")
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render_suite_report_html(report), "utf-8")
    typer.echo(f"wrote {out_json} and {out_html}")

    if report.retrieval is not None:
        typer.echo(render_report(report.retrieval))
    if report.suite_metrics is not None:
        sm = report.suite_metrics
        typer.echo(
            f"runs={sm.run_count} patch_apply_rate={sm.patch_apply_rate:.0%} "
            f"unrelated_file_rate={sm.unrelated_file_change_rate:.0%} "
            f"total_cost=${sm.total_cost_usd:.4f}"
        )


if __name__ == "__main__":
    app()
