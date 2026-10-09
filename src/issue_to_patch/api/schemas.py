"""Request/response models for the OpenAPI service boundary (Phase 4).

Kept separate from the graph's own state models: the API's shape should
follow what a client needs to see, not leak the graph's internal fields —
they happen to overlap a lot right now, but that's a starting point, not a
guarantee.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from issue_to_patch.patching.models import CheckStatus
from issue_to_patch.retrieval.models import RetrievalMode


class SourceLocationOut(BaseModel):
    chunk_id: str
    path: str
    line_start: int
    line_end: int
    symbol: str | None = None


class HypothesisOut(BaseModel):
    summary: str
    confidence: float
    cites: list[SourceLocationOut] = Field(default_factory=list)


class ValidationCheckOut(BaseModel):
    name: str
    status: CheckStatus
    detail: str = ""


class StartRunRequest(BaseModel):
    issue: str = Field(description="Issue URL, owner/repo#n, fixture path, or raw text")
    snapshot: str = Field(description="Path to an already-`index`ed snapshot directory")
    scope: list[str] | None = Field(default=None, description="Glob(s) the patch must stay within")


class AutoRunRequest(BaseModel):
    """Like StartRunRequest, but does the ingest+index itself from a bare
    issue URL instead of requiring an already-prepared snapshot."""

    issue_url: str = Field(description="GitHub issue URL or owner/repo#n")
    token: str | None = Field(default=None, description="GitHub token; else anonymous")
    repo_source: str | None = Field(default=None, description="Override the clone source")
    ref: str | None = Field(default=None, description="Branch/tag/SHA to pin")
    scope: list[str] | None = Field(default=None, description="Glob(s) the patch must stay within")
    plan_id: str | None = Field(default=None, description="Reuse a saved resolution plan snapshot")
    max_lines: int = Field(default=200, description="Max lines before a big class is split")


class AutoRunAcceptedResponse(BaseModel):
    """What ``POST /runs/auto`` returns immediately — the run has been
    scheduled, not completed. Poll ``GET /runs/{run_id}`` (JSON, 404 until
    the first checkpoint exists) or ``GET /ui/runs/{run_id}`` (HTML, shows
    progress in the meantime) for the eventual result."""

    run_id: str


class RunStateResponse(BaseModel):
    run_id: str
    issue_ref: str | None = None
    status: str = Field(description="AWAITING_HUMAN_REVIEW, or a terminal RunState")
    hypothesis: HypothesisOut | None = None
    changed_files: list[str] = Field(default_factory=list)
    validation: list[ValidationCheckOut] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    decision: str = Field(description="approve | reject | revise")
    reason: str = ""
    reviewer: str = "human"
    role: str = Field(
        default="gatekeeper",
        description="gatekeeper | auditor | strategist — only a gatekeeper can "
        "clear a patch touching a risky path (safety/permissions.py)",
    )


class RetrievalHitOut(BaseModel):
    kind: str
    score: float
    location: SourceLocationOut | None = None


class RetrievalResponse(BaseModel):
    run_id: str
    evidence: list[RetrievalHitOut]


class PatchResponse(BaseModel):
    run_id: str
    base_sha: str
    commit_sha: str
    changed_files: list[str]
    patch_text: str


class BuildResponse(BaseModel):
    run_id: str
    branch_name: str
    branch_dir: str
    sha: str = Field(description="HEAD commit sha of the new, persistent branch")


class PushRequest(BaseModel):
    remote_url: str | None = Field(
        default=None,
        description="Remote you own (e.g. your own fork). Overrides "
        "ITP_PUSH_REMOTE_URL for this call if given.",
    )
    token: str | None = Field(
        default=None,
        description="Your GitHub personal access token (needs 'repo' scope). Lets "
        "anyone push over https:// using only their own token - no SSH key or git "
        "credentials need to exist on the server at all. Ignored for an ssh/scp-style "
        "remote_url, which carries its own credential instead.",
    )


class PushResponse(BaseModel):
    run_id: str
    ok: bool
    remote_display: str = Field(description="The remote URL, with any credential redacted")
    detail: str


class UserOut(BaseModel):
    username: str
    role: str
    authenticated: bool = Field(
        description="True only for a real per-user account — false for the "
        "shared ITP_API_KEY or local no-auth fallback"
    )


class PullRequestRequest(BaseModel):
    fork_remote_url: str = Field(description="The same remote you pushed the branch to")
    title: str
    body: str = ""
    base: str | None = Field(default=None, description="Defaults to the upstream default branch")
    github_token: str | None = Field(
        default=None,
        description="Needs 'repo' scope; overrides ITP_GITHUB_TOKEN for this call",
    )


class PullRequestResponse(BaseModel):
    number: int
    url: str


class SearchRequest(BaseModel):
    query: str
    repo: str
    commit_sha: str
    mode: RetrievalMode = RetrievalMode.HYBRID
    top_k: int = Field(default=10, ge=1, le=100)


class SearchResultOut(BaseModel):
    rank: int
    score: float
    path: str
    line_start: int
    line_end: int
    symbol: str | None
    kind: str


class SearchResponse(BaseModel):
    mode: RetrievalMode
    candidates_considered: int
    results: list[SearchResultOut]


class ValidatePatchRequest(BaseModel):
    snapshot: str = Field(description="Path to the snapshot the patch was generated against")
    base_sha: str
    commit_sha: str
    changed_files: list[str]
    added_lines: int = 0
    removed_lines: int = 0
    patch_text: str
    allowed_scope: list[str] = Field(default_factory=list)


class ValidatePatchResponse(BaseModel):
    state: str
    checks: list[ValidationCheckOut]
