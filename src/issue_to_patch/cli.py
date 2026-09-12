"""Command-line entrypoint.

``version`` / ``config`` (bootstrap) · ``run`` (Sprint 1, deterministic) ·
``ingest`` (Sprint 2) · ``index`` / ``show-chunk`` (Sprint 3) ·
``search`` / ``eval-retrieval`` / ``build-eval-set`` (Sprint 4) ·
``investigate`` (Sprint 5, the LangGraph reasoning engine).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from issue_to_patch import __version__
from issue_to_patch.config import get_settings
from issue_to_patch.logging import configure_logging
from issue_to_patch.run_states import RunState

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


@app.command()
def investigate(
    issue: Annotated[str, typer.Option(help="Issue URL, owner/repo#n, fixture path, or raw text")],
    snapshot: Annotated[
        Path, typer.Option(help="Snapshot dir (from `ingest`) — must already be `index`ed")
    ],
    scope: Annotated[
        list[str] | None, typer.Option(help="Glob(s) the patch must stay within")
    ] = None,
    decision: Annotated[
        str | None,
        typer.Option(help="Resolve the human gate immediately: approve | reject | revise"),
    ] = None,
    reason: Annotated[str, typer.Option(help="Reason recorded with --decision")] = "",
) -> None:
    """Run the reasoning graph on an issue: investigate, draft a patch, pause for review.

    Runs entirely within this process — the checkpointer is in-memory (see
    ``graph/checkpoint.py``), so resuming a paused run from a *separate*
    ``investigate`` invocation isn't possible yet. Pass ``--decision`` to
    resolve the human gate immediately and see the run through to a final
    state in one command; omit it to stop at the pending review and inspect it.
    """
    from issue_to_patch.graph import (
        GraphDependencies,
        HumanDecision,
        build_dependencies,
        resume_investigation,
        start_investigation,
    )
    from issue_to_patch.ingestion.snapshot import load_snapshot
    from issue_to_patch.persistence import Store

    settings = get_settings()
    snap = load_snapshot(snapshot)
    store = Store(settings.database_url)
    if not store.indexed_paths(snap.repo or snap.source, snap.commit_sha):
        typer.echo("no chunks indexed for this snapshot — run `index` first", err=True)
        raise typer.Exit(2)

    deps: GraphDependencies = build_dependencies(store=store, settings=settings)
    handle = start_investigation(issue_ref=issue, repository=snap, deps=deps, allowed_scope=scope)

    if decision and handle.awaiting_human:
        handle = resume_investigation(handle, HumanDecision(decision=decision, reason=reason))

    state = handle.state
    typer.echo(f"run_id:        {handle.run_id}")
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
    if (validation := state.get("validation")) is not None:
        for check in validation.checks:
            typer.echo(f"  [{check.status.value:>4}] {check.name} {check.detail}".rstrip())
    if handle.awaiting_human:
        typer.echo("status:        AWAITING_HUMAN_REVIEW — rerun with --decision to resolve")
        raise typer.Exit(10)
    final_state = state["final_state"]
    assert final_state is not None  # PersistRun always sets it once the graph reaches END
    typer.echo(f"state:         {final_state.value}")
    raise typer.Exit(_EXIT_CODES[final_state])


if __name__ == "__main__":
    app()
