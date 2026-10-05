"""Thin HTTP handlers over the graph/retrieval/patching services.

No orchestration logic lives here — every handler is a straight-line
"parse request -> call a service -> shape response". That's deliberate
(Phase 4): the schema follows the graph, not the reverse, and the same
services stay independently testable and CLI-usable.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from issue_to_patch.api._common import branch_target as _branch_target
from issue_to_patch.api._common import checks_out as _checks_out
from issue_to_patch.api._common import load_or_404 as _load_or_404
from issue_to_patch.api._common import to_response as _to_response
from issue_to_patch.api.deps import (
    CheckpointerDep,
    CurrentUserDep,
    GraphDepsDep,
    SettingsDep,
    StoreDep,
    get_current_user,
    rate_limit,
)
from issue_to_patch.api.schemas import (
    ApproveRequest,
    AutoRunRequest,
    BuildResponse,
    PatchResponse,
    PullRequestRequest,
    PullRequestResponse,
    PushRequest,
    PushResponse,
    RetrievalHitOut,
    RetrievalResponse,
    RunStateResponse,
    SearchRequest,
    SearchResponse,
    SearchResultOut,
    SourceLocationOut,
    StartRunRequest,
    UserOut,
    ValidatePatchRequest,
    ValidatePatchResponse,
)
from issue_to_patch.auto_run import auto_index, auto_ingest, auto_investigate
from issue_to_patch.graph import HumanDecision, resume_investigation, start_investigation
from issue_to_patch.ingestion.errors import GitHubAPIError, IngestionError, RepositoryNotFound
from issue_to_patch.ingestion.git_ops import SafeGit
from issue_to_patch.ingestion.models import RepositorySnapshot
from issue_to_patch.ingestion.snapshot import load_snapshot
from issue_to_patch.patching import EditApplicationError, materialize_branch, push_branch
from issue_to_patch.patching.models import PatchArtifact
from issue_to_patch.patching.validate import validate_patch
from issue_to_patch.persistence.audit import AuditTrail, build_audit_trail
from issue_to_patch.pull_request import ForkRemoteNotGitHub, create_pull_request
from issue_to_patch.retrieval import RetrievalService, SearchFilters
from issue_to_patch.run_states import RunState

_AUTHED = [Depends(get_current_user), Depends(rate_limit)]

runs_router = APIRouter(prefix="/runs", tags=["runs"], dependencies=_AUTHED)
tools_router = APIRouter(tags=["tools"], dependencies=_AUTHED)
users_router = APIRouter(prefix="/users", tags=["users"], dependencies=_AUTHED)

_VALID_DECISIONS = {"approve", "reject", "revise"}
_VALID_ROLES = {"gatekeeper", "auditor", "strategist"}


def _load_snapshot_or_400(path: str) -> RepositorySnapshot:
    try:
        return load_snapshot(Path(path))
    except RepositoryNotFound as exc:
        raise HTTPException(400, f"could not load snapshot at {path!r}: {exc}") from exc


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


@runs_router.post("/auto", response_model=RunStateResponse, status_code=201)
async def start_auto_run(
    body: AutoRunRequest, deps: GraphDepsDep, checkpointer: CheckpointerDep, settings: SettingsDep
) -> RunStateResponse:
    """Like ``POST /runs``, but does the ingest+index itself from a bare
    issue URL — the same ``auto_ingest``/``auto_index``/``auto_investigate``
    the `auto` CLI command calls, so the two can never drift."""
    run_id = uuid.uuid4().hex[:16]
    run_dir = settings.artifacts_dir / run_id
    try:
        await auto_ingest(
            body.issue_url,
            run_dir,
            settings=settings,
            token=body.token,
            repo_source=body.repo_source,
            ref=body.ref,
        )
    except IngestionError as exc:
        raise HTTPException(400, f"ingest failed: {exc}") from exc
    try:
        snapshot, _ = auto_index(run_dir, deps.store, max_lines=body.max_lines)
    except Exception as exc:
        raise HTTPException(400, f"index failed: {exc}") from exc
    try:
        handle = auto_investigate(
            body.issue_url, snapshot, deps, checkpointer, run_id=run_id, scope=body.scope
        )
    except Exception as exc:
        raise HTTPException(500, f"investigation failed: {exc}") from exc
    return _to_response(handle)


@runs_router.get("/{run_id}", response_model=RunStateResponse)
def get_run(run_id: str, deps: GraphDepsDep, checkpointer: CheckpointerDep) -> RunStateResponse:
    return _to_response(_load_or_404(run_id, deps, checkpointer))


@runs_router.post("/{run_id}/approve", response_model=RunStateResponse)
def approve_run(
    run_id: str,
    body: ApproveRequest,
    deps: GraphDepsDep,
    checkpointer: CheckpointerDep,
    current_user: CurrentUserDep,
) -> RunStateResponse:
    if body.decision not in _VALID_DECISIONS:
        raise HTTPException(422, f"decision must be one of {sorted(_VALID_DECISIONS)}")
    # A real per-user account's role wins, full stop — the request body's
    # role/reviewer are only ever trusted for the legacy shared-key/local
    # fallback, where there's no real identity to defer to in the first
    # place (same free-text behavior this project has always had).
    role = current_user.role if current_user.authenticated else body.role
    reviewer = current_user.username if current_user.authenticated else body.reviewer
    if role not in _VALID_ROLES:
        raise HTTPException(422, f"role must be one of {sorted(_VALID_ROLES)}")
    handle = _load_or_404(run_id, deps, checkpointer)
    if not handle.awaiting_human:
        raise HTTPException(409, "run is not awaiting human review")
    resumed = resume_investigation(
        handle,
        HumanDecision(
            decision=body.decision,
            reason=body.reason,
            reviewer=reviewer,
            role=role,
            authenticated=current_user.authenticated,
        ),
    )
    return _to_response(resumed)


@users_router.get("/me", response_model=UserOut)
def whoami(current_user: CurrentUserDep) -> UserOut:
    """Lets the web UI ask "who does the server think I am" after a page
    has already loaded — a plain page navigation can't carry a bearer
    header, so this is the only way the browser finds out."""
    return UserOut(
        username=current_user.username,
        role=current_user.role,
        authenticated=current_user.authenticated,
    )


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


@runs_router.post("/{run_id}/build", response_model=BuildResponse)
def build_run(run_id: str, deps: GraphDepsDep, checkpointer: CheckpointerDep) -> BuildResponse:
    """Materialize the validated patch onto a real, persistent branch —
    idempotent: a second call after the branch already exists just reports
    its current HEAD instead of erroring."""
    handle = _load_or_404(run_id, deps, checkpointer)
    if handle.state.get("final_state") is not RunState.PATCH_VALIDATED:
        raise HTTPException(409, "run must be PATCH_VALIDATED to build")
    snapshot = handle.state["repository"]
    patch = handle.state["candidate_patch"]
    assert snapshot is not None and patch is not None  # PATCH_VALIDATED guarantees both
    branch_dir, branch_name = _branch_target(run_id, snapshot)
    if branch_dir.exists():
        sha = SafeGit(root=branch_dir).run("rev-parse", "HEAD", cwd=branch_dir).stdout.strip()
    else:
        try:
            sha = materialize_branch(snapshot, patch, branch_dir, branch_name)
        except EditApplicationError as exc:
            raise HTTPException(500, f"build failed: {exc}") from exc
    return BuildResponse(
        run_id=run_id, branch_name=branch_name, branch_dir=str(branch_dir), sha=sha
    )


@runs_router.post("/{run_id}/push", response_model=PushResponse)
def push_run(
    run_id: str,
    deps: GraphDepsDep,
    checkpointer: CheckpointerDep,
    settings: SettingsDep,
    body: PushRequest | None = None,
) -> PushResponse:
    handle = _load_or_404(run_id, deps, checkpointer)
    snapshot = handle.state.get("repository")
    if snapshot is None:
        raise HTTPException(404, f"no such run: {run_id}")
    branch_dir, branch_name = _branch_target(run_id, snapshot)
    if not branch_dir.exists():
        raise HTTPException(409, "build the branch before pushing")
    # A remote given in the request (e.g. typed into the UI, remembered in
    # that browser) wins for this one call; ITP_PUSH_REMOTE_URL is only the
    # server-wide fallback default, never required.
    remote_url = (body.remote_url if body else None) or (
        settings.push_remote_url.get_secret_value() if settings.push_remote_url else None
    )
    if remote_url is None:
        raise HTTPException(
            400,
            "No push remote given — pass remote_url, or set ITP_PUSH_REMOTE_URL on the "
            "server, to a remote you own (e.g. your own fork), never the upstream repository",
        )
    result = push_branch(branch_dir, branch_name, remote_url)
    return PushResponse(
        run_id=run_id, ok=result.ok, remote_display=result.remote_display, detail=result.detail
    )


@runs_router.post("/{run_id}/pull-request", response_model=PullRequestResponse)
async def create_pull_request_route(
    run_id: str,
    body: PullRequestRequest,
    deps: GraphDepsDep,
    checkpointer: CheckpointerDep,
    settings: SettingsDep,
) -> PullRequestResponse:
    """Opens a real GitHub Pull Request from the already-pushed branch — a
    separate, explicit action from push, never implied by it."""
    handle = _load_or_404(run_id, deps, checkpointer)
    snapshot = handle.state.get("repository")
    if snapshot is None:
        raise HTTPException(404, f"no such run: {run_id}")
    branch_dir, branch_name = _branch_target(run_id, snapshot)
    if not branch_dir.exists():
        raise HTTPException(409, "build the branch before creating a pull request")
    token = body.github_token or (
        settings.github_token.get_secret_value() if settings.github_token else None
    )
    if token is None:
        raise HTTPException(
            400,
            "No GitHub token given — pass github_token (needs 'repo' scope), "
            "or set ITP_GITHUB_TOKEN on the server",
        )
    try:
        result = await create_pull_request(
            run_dir=settings.artifacts_dir / run_id,
            branch_name=branch_name,
            fork_remote_url=body.fork_remote_url,
            title=body.title,
            body=body.body,
            base=body.base,
            token=token,
        )
    except ForkRemoteNotGitHub as exc:
        raise HTTPException(400, str(exc)) from exc
    except GitHubAPIError as exc:
        raise HTTPException(400, str(exc)) from exc
    return PullRequestResponse(number=result.number, url=result.url)


@runs_router.get("/{run_id}/audit", response_model=AuditTrail)
def get_audit_trail(run_id: str, store: StoreDep) -> AuditTrail:
    """Every tool call and human decision for this run, in order, with both
    append-only hash chains verified (Phase 8's Auditor role)."""
    trail = build_audit_trail(store, run_id)
    if not trail.found:
        raise HTTPException(404, f"no such run: {run_id}")
    return trail


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
