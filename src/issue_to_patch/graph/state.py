"""The reasoning graph's shared state, and the typed models that flow through it.

``InvestigationState`` is a plain :class:`TypedDict` (LangGraph's native state
shape); every value inside it is either a primitive or one of the Pydantic
models below, so a checkpoint round-trips exactly and every claim a node makes
stays traceable back to a real chunk/file/line (never a bare string).

Two fields use an additive reducer (``Annotated[list[X], operator.add]``): a
node that appends evidence or an error returns only the *new* items and
LangGraph concatenates them onto the existing list. Every other field is
replace-on-write, which is what you want for "the current plan" or "the
current patch" — there is only ever one at a time.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field

from issue_to_patch.ingestion.models import IssueRequest, RepositorySnapshot
from issue_to_patch.patching.models import EditPlan, PatchArtifact, ValidationReport
from issue_to_patch.run_states import RunState


class SourceLocation(BaseModel):
    """A specific, citable place in the repository."""

    chunk_id: str
    path: str
    line_start: int
    line_end: int
    symbol: str | None = None


class Evidence(BaseModel):
    """One piece of retrieved context. Always traceable to where it came from.

    ``kind`` is ``"issue"`` for context pulled from the issue/comments, or
    ``"code"`` for a retrieved chunk — ``source_location`` is set for the
    latter so every downstream claim can cite a real chunk/file/line.
    """

    kind: str
    content: str
    source_location: SourceLocation | None = None
    score: float = 0.0
    note: str = ""


class Hypothesis(BaseModel):
    """A candidate root-cause explanation, grounded in evidence."""

    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    cites: list[SourceLocation] = Field(default_factory=list)


class InvestigationPlan(BaseModel):
    """What to look for before drafting a fix."""

    search_queries: list[str] = Field(default_factory=list)
    focus_areas: list[str] = Field(default_factory=list)


class HumanDecision(BaseModel):
    """The outcome of the human review gate."""

    decision: str = Field(description="'approve' | 'reject' | 'revise'")
    reason: str = ""
    reviewer: str = "human"


class EvaluationReport(BaseModel):
    """A cheap, deterministic summary of how the run went (Sprint 8 does more)."""

    revisions_used: int = 0
    evidence_count: int = 0
    top_hypothesis_confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)


class InvestigationState(TypedDict, total=False):
    run_id: str
    issue_ref: str
    allowed_scope: list[str]

    issue: IssueRequest | None
    repository: RepositorySnapshot | None

    plan: InvestigationPlan | None
    evidence: Annotated[list[Evidence], operator.add]
    hypotheses: list[Hypothesis]
    selected_files: list[SourceLocation]

    edit_plan: EditPlan | None
    candidate_patch: PatchArtifact | None
    validation: ValidationReport | None
    revisions_used: Annotated[int, operator.add]
    expanded: bool

    human_decision: HumanDecision | None
    evaluation: EvaluationReport | None

    final_state: RunState | None
    errors: Annotated[list[str], operator.add]


def new_state(
    *, run_id: str, issue_ref: str, allowed_scope: list[str] | None = None
) -> InvestigationState:
    """The empty state a run starts from."""
    return InvestigationState(
        run_id=run_id,
        issue_ref=issue_ref,
        allowed_scope=allowed_scope or [],
        issue=None,
        repository=None,
        plan=None,
        evidence=[],
        hypotheses=[],
        selected_files=[],
        edit_plan=None,
        candidate_patch=None,
        validation=None,
        revisions_used=0,
        expanded=False,
        human_decision=None,
        evaluation=None,
        final_state=None,
        errors=[],
    )
