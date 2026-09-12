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
