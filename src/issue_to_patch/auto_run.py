"""Shared "give a URL, get an investigation" orchestration: ingest -> index
-> start_investigation. Used by both the `auto` CLI command and the
``POST /runs/auto`` API route, so the two can never drift — each call site
still owns its own error presentation (typer.Exit vs HTTPException).
"""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver

from issue_to_patch.config import Settings
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.run import InvestigationHandle, start_investigation
from issue_to_patch.ingestion.github import GitHubClient
from issue_to_patch.ingestion.ingest import IngestResult, ingest_issue
from issue_to_patch.ingestion.models import RepositorySnapshot
from issue_to_patch.ingestion.snapshot import load_snapshot
from issue_to_patch.persistence import Store
from issue_to_patch.processing import IndexResult, index_snapshot


async def auto_ingest(
    issue_url: str,
    run_dir: Path,
    *,
    settings: Settings,
    token: str | None = None,
    repo_source: str | None = None,
    ref: str | None = None,
) -> IngestResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    resolved_token = token or (
        settings.github_token.get_secret_value() if settings.github_token else None
    )
    async with GitHubClient(token=resolved_token, base_url=settings.github_api_base) as client:
        return await ingest_issue(
            issue_url, run_dir, client=client, repo_source=repo_source, ref=ref
        )


def auto_index(
    run_dir: Path, store: Store, *, max_lines: int = 200
) -> tuple[RepositorySnapshot, IndexResult]:
    # auto_ingest() (via ingest_issue) clones into run_dir/"snapshot", not
    # run_dir itself.
    snap = load_snapshot(run_dir / "snapshot")
    index_result = index_snapshot(snap, store, max_lines=max_lines, out_dir=run_dir)
    return snap, index_result


def auto_investigate(
    issue_url: str,
    snapshot: RepositorySnapshot,
    deps: GraphDependencies,
    checkpointer: BaseCheckpointSaver[str],
    *,
    run_id: str,
    scope: list[str] | None = None,
    provisional_plan: str | None = None,
    plan_issue_text: str | None = None,
) -> InvestigationHandle:
    return start_investigation(
        issue_ref=issue_url,
        provisional_plan=provisional_plan,
        plan_issue_text=plan_issue_text,
        repository=snapshot,
        deps=deps,
        allowed_scope=scope,
        run_id=run_id,
        checkpointer=checkpointer,
    )
