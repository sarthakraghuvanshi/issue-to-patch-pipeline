"""Response-shaping helpers shared by the JSON API (routes.py) and the HTML
UI (ui.py) — both need the same "load a run, shape its state" logic."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from langgraph.checkpoint.base import BaseCheckpointSaver

from issue_to_patch.api.schemas import (
    HypothesisOut,
    RunStateResponse,
    SourceLocationOut,
    ValidationCheckOut,
)
from issue_to_patch.graph import UnknownRun, load_investigation
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.run import InvestigationHandle
from issue_to_patch.ingestion.models import RepositorySnapshot
from issue_to_patch.patching.models import ValidationReport


def branch_target(run_id: str, snapshot: RepositorySnapshot) -> tuple[Path, str]:
    """Same convention the `auto` CLI command uses — a run started via the
    API, the CLI, or the web UI ends up buildable/pushable the same way."""
    return snapshot.root_path.parent / "branch", f"itp/{run_id}"


def load_or_404(
    run_id: str, deps: GraphDependencies, checkpointer: BaseCheckpointSaver[str]
) -> InvestigationHandle:
    try:
        return load_investigation(run_id, deps, checkpointer=checkpointer)
    except UnknownRun as exc:
        raise HTTPException(404, f"no such run: {run_id}") from exc


def checks_out(report: ValidationReport | None) -> list[ValidationCheckOut]:
    if report is None:
        return []
    return [
        ValidationCheckOut(name=c.name, status=c.status, detail=c.detail) for c in report.checks
    ]


def to_response(handle: InvestigationHandle) -> RunStateResponse:
    state = handle.state
    issue = state.get("issue")
    hypothesis = None
    if hypotheses := state.get("hypotheses"):
        top = hypotheses[0]
        hypothesis = HypothesisOut(
            summary=top.summary,
            confidence=top.confidence,
            cites=[SourceLocationOut(**loc.model_dump()) for loc in top.cites],
        )
    patch = state.get("candidate_patch")
    final_state = state.get("final_state")
    status = (
        "AWAITING_HUMAN_REVIEW"
        if handle.awaiting_human
        else (final_state.value if final_state is not None else "IN_PROGRESS")
    )
    return RunStateResponse(
        run_id=handle.run_id,
        issue_ref=issue.reference if issue is not None else None,
        status=status,
        hypothesis=hypothesis,
        changed_files=patch.changed_files if patch is not None else [],
        validation=checks_out(state.get("validation")),
    )
