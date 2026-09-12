"""Deterministic routing between nodes.

Every function here reads ``InvestigationState`` (plus the two tunable
thresholds in :class:`Settings`) and returns the *name* of the next node —
never a decision made by the LLM. This is what "the router is deterministic
where possible" (IMPLEMENTATION_PLAN.md, Sprint 5) means in practice: the
model gets to fill in a hypothesis or a patch, never to pick what happens
next.
"""

from __future__ import annotations

from issue_to_patch.config import Settings
from issue_to_patch.graph.state import InvestigationState
from issue_to_patch.run_states import RunState

PERSIST_RUN = "PersistRun"
PLAN_INVESTIGATION = "PlanInvestigation"
SELECT_ADDITIONAL_EVIDENCE = "SelectAdditionalEvidence"
ANALYZE_ROOT_CAUSE = "AnalyzeRootCause"
DRAFT_PATCH = "DraftPatch"
RUN_PATCH_VALIDATION = "RunPatchValidation"
REQUEST_HUMAN_VALIDATION = "RequestHumanValidation"
REVISE_PATCH = "RevisePatch"
EVALUATE_RUN = "EvaluateRun"

# Two hypotheses within this margin of each other count as "conflicting
# evidence" (Phase 5's router rule), and are worth one more retrieval pass
# rather than betting on whichever one happened to be listed first.
_CONFLICT_MARGIN = 0.10


def route_after_normalize(state: InvestigationState) -> str:
    """Missing repository or issue data -> ingestion retry/error."""
    if state.get("errors") or state.get("issue") is None or state.get("repository") is None:
        return PERSIST_RUN
    return PLAN_INVESTIGATION


def route_after_retrieve_code(state: InvestigationState, settings: Settings) -> str:
    """Low retrieval confidence -> query expansion (once), then proceed anyway."""
    code_evidence = [e for e in state["evidence"] if e.kind == "code"]
    best_score = max((e.score for e in code_evidence), default=0.0)
    if (not code_evidence or best_score < settings.retrieval_confidence_floor) and not state.get(
        "expanded"
    ):
        return SELECT_ADDITIONAL_EVIDENCE
    return ANALYZE_ROOT_CAUSE


def route_after_analyze(state: InvestigationState, settings: Settings) -> str:
    """Low confidence or conflicting top hypotheses -> one more retrieval pass;
    still inconclusive after that -> stop rather than draft on a weak guess."""
    confidences = sorted((h.confidence for h in state["hypotheses"]), reverse=True)
    top = confidences[0] if confidences else 0.0

    if not confidences or top < settings.retrieval_confidence_floor:
        return PERSIST_RUN if state.get("expanded") else SELECT_ADDITIONAL_EVIDENCE

    conflicting = len(confidences) > 1 and (confidences[0] - confidences[1]) < _CONFLICT_MARGIN
    if conflicting and not state.get("expanded"):
        return SELECT_ADDITIONAL_EVIDENCE

    return DRAFT_PATCH


def route_after_draft(state: InvestigationState, settings: Settings) -> str:
    """An edit plan that didn't apply cleanly has nothing to validate yet — that's
    the same "revise once, then give up" budget as a patch that fails validation
    (this also covers RevisePatch's own attempt failing to apply, so the graph
    always has somewhere safe to go instead of validating a patch that doesn't
    exist)."""
    if state.get("candidate_patch") is None:
        if state.get("revisions_used", 0) < settings.max_patch_revisions:
            return REVISE_PATCH
        return PERSIST_RUN
    return RUN_PATCH_VALIDATION


def route_after_validation(state: InvestigationState, settings: Settings) -> str:
    """Patch changes unrelated files / doesn't apply -> revise once, then reject.
    Anything that passed (even with warnings) still goes through the human gate —
    this project never claims success without a human or an exhausted budget."""
    validation = state["validation"]
    assert validation is not None
    if validation.run_state is RunState.PATCH_REJECTED:
        if state.get("revisions_used", 0) < settings.max_patch_revisions:
            return REVISE_PATCH
        return PERSIST_RUN
    return REQUEST_HUMAN_VALIDATION


def route_after_human(state: InvestigationState, settings: Settings) -> str:
    """Gatekeeper's decision: approve completes the run, reject ends it, and a
    request to revise consumes one more revision if the budget allows it."""
    decision = state["human_decision"]
    assert decision is not None
    if decision.decision == "approve":
        return EVALUATE_RUN
    if decision.decision == "reject":
        return PERSIST_RUN
    if state.get("revisions_used", 0) < settings.max_patch_revisions:
        return REVISE_PATCH
    return PERSIST_RUN
