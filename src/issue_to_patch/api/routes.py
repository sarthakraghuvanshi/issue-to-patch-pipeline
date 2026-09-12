"""Thin HTTP handlers over the graph/retrieval/patching services.

No orchestration logic lives here — every handler is a straight-line
"parse request -> call a service -> shape response". That's deliberate
(Phase 4): the schema follows the graph, not the reverse, and the same
services stay independently testable and CLI-usable.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from langgraph.checkpoint.base import BaseCheckpointSaver

from issue_to_patch.api.deps import (
    CheckpointerDep,
    GraphDepsDep,
    StoreDep,
    rate_limit,
    require_auth,
)
from issue_to_patch.api.schemas import (
    ApproveRequest,
    HypothesisOut,
    PatchResponse,
    RetrievalHitOut,
    RetrievalResponse,
    RunStateResponse,
    SearchRequest,
    SearchResponse,
    SearchResultOut,
    SourceLocationOut,
    StartRunRequest,
    ValidatePatchRequest,
    ValidatePatchResponse,
    ValidationCheckOut,
)
from issue_to_patch.graph import (
    HumanDecision,
    UnknownRun,
    load_investigation,
    resume_investigation,
    start_investigation,
)
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.run import InvestigationHandle
from issue_to_patch.ingestion.errors import RepositoryNotFound
from issue_to_patch.ingestion.models import RepositorySnapshot
from issue_to_patch.ingestion.snapshot import load_snapshot
from issue_to_patch.patching.models import PatchArtifact, ValidationReport
from issue_to_patch.patching.validate import validate_patch
from issue_to_patch.retrieval import RetrievalService, SearchFilters

_AUTHED = [Depends(require_auth), Depends(rate_limit)]

runs_router = APIRouter(prefix="/runs", tags=["runs"], dependencies=_AUTHED)
tools_router = APIRouter(tags=["tools"], dependencies=_AUTHED)

_VALID_DECISIONS = {"approve", "reject", "revise"}


def _load_snapshot_or_400(path: str) -> RepositorySnapshot:
    try:
        return load_snapshot(Path(path))
    except RepositoryNotFound as exc:
        raise HTTPException(400, f"could not load snapshot at {path!r}: {exc}") from exc


def _load_or_404(
    run_id: str, deps: GraphDependencies, checkpointer: BaseCheckpointSaver[str]
) -> InvestigationHandle:
    try:
        return load_investigation(run_id, deps, checkpointer=checkpointer)
    except UnknownRun as exc:
        raise HTTPException(404, f"no such run: {run_id}") from exc


def _checks_out(report: ValidationReport | None) -> list[ValidationCheckOut]:
    if report is None:
        return []
    return [
        ValidationCheckOut(name=c.name, status=c.status, detail=c.detail) for c in report.checks
    ]


def _to_response(handle: InvestigationHandle) -> RunStateResponse:
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
        validation=_checks_out(state.get("validation")),
    )


@runs_router.post("", response_model=RunStateResponse, status_code=201)
def start_run(
    body: StartRunRequest, deps: GraphDepsDep, checkpointer: CheckpointerDep
) -> RunStateResponse:
    snapshot = _load_snapshot_or_400(body.snapshot)
    if not deps.store.indexed_paths(snapshot.repo or snapshot.source, snapshot.commit_sha):
        raise HTTPException(400, "snapshot is not indexed — run `index` first")
    handle = start_investigation(
        issue_ref=body.issue,
        repository=snapshot,
        deps=deps,
        allowed_scope=body.scope,
        checkpointer=checkpointer,
    )
    return _to_response(handle)


@runs_router.get("/{run_id}", response_model=RunStateResponse)
def get_run(run_id: str, deps: GraphDepsDep, checkpointer: CheckpointerDep) -> RunStateResponse:
    return _to_response(_load_or_404(run_id, deps, checkpointer))


@runs_router.post("/{run_id}/approve", response_model=RunStateResponse)
def approve_run(
    run_id: str, body: ApproveRequest, deps: GraphDepsDep, checkpointer: CheckpointerDep
) -> RunStateResponse:
    if body.decision not in _VALID_DECISIONS:
        raise HTTPException(422, f"decision must be one of {sorted(_VALID_DECISIONS)}")
    handle = _load_or_404(run_id, deps, checkpointer)
    if not handle.awaiting_human:
        raise HTTPException(409, "run is not awaiting human review")
    resumed = resume_investigation(
        handle, HumanDecision(decision=body.decision, reason=body.reason)
    )
    return _to_response(resumed)


@runs_router.get("/{run_id}/retrieval", response_model=RetrievalResponse)
def get_retrieval(
    run_id: str, deps: GraphDepsDep, checkpointer: CheckpointerDep
) -> RetrievalResponse:
    handle = _load_or_404(run_id, deps, checkpointer)
    evidence = [
        RetrievalHitOut(
            kind=e.kind,
            score=e.score,
            location=SourceLocationOut(**e.source_location.model_dump())
            if e.source_location
            else None,
        )
        for e in handle.state.get("evidence", [])
    ]
    return RetrievalResponse(run_id=run_id, evidence=evidence)


@runs_router.get("/{run_id}/patch", response_model=PatchResponse)
def get_patch(run_id: str, deps: GraphDepsDep, checkpointer: CheckpointerDep) -> PatchResponse:
    handle = _load_or_404(run_id, deps, checkpointer)
    patch = handle.state.get("candidate_patch")
    if patch is None:
        raise HTTPException(404, "no patch has been drafted for this run yet")
    return PatchResponse(
        run_id=run_id,
        base_sha=patch.base_sha,
        commit_sha=patch.commit_sha,
        changed_files=patch.changed_files,
        patch_text=patch.patch_text,
    )


@tools_router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest, store: StoreDep) -> SearchResponse:
    service = RetrievalService(store)
    filters = SearchFilters(repository=body.repo, commit_sha=body.commit_sha)
    try:
        trace = service.search(body.query, filters, top_k=body.top_k, mode=body.mode)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    results = [
        SearchResultOut(
            rank=r.rank,
            score=r.score,
            path=r.path,
            line_start=r.line_start,
            line_end=r.line_end,
            symbol=r.symbol,
            kind=r.kind,
        )
        for r in trace.results
    ]
    return SearchResponse(
        mode=trace.mode, candidates_considered=trace.candidates_considered, results=results
    )


@tools_router.post("/validate-patch", response_model=ValidatePatchResponse)
def validate_patch_route(body: ValidatePatchRequest) -> ValidatePatchResponse:
    snapshot = _load_snapshot_or_400(body.snapshot)
    patch = PatchArtifact(
        base_sha=body.base_sha,
        commit_sha=body.commit_sha,
        changed_files=body.changed_files,
        added_lines=body.added_lines,
        removed_lines=body.removed_lines,
        patch_text=body.patch_text,
    )
    report = validate_patch(snapshot, patch, allowed_scope=body.allowed_scope)
    return ValidatePatchResponse(state=report.run_state.value, checks=_checks_out(report))
