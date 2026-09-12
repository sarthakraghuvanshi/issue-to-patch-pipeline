"""Router: each rule from IMPLEMENTATION_PLAN.md's Sprint 5 table, in isolation."""

from __future__ import annotations

from issue_to_patch.config.settings import Settings
from issue_to_patch.graph import router
from issue_to_patch.graph.state import (
    Evidence,
    HumanDecision,
    Hypothesis,
    ReviewerRole,
    SourceLocation,
    new_state,
)
from issue_to_patch.patching.models import CheckResult, CheckStatus, PatchArtifact, ValidationReport
from issue_to_patch.run_states import RunState

_SETTINGS = Settings(retrieval_confidence_floor=0.35, max_patch_revisions=1)


def _loc(chunk_id: str = "c1") -> SourceLocation:
    return SourceLocation(chunk_id=chunk_id, path="a.py", line_start=1, line_end=2)


def _patch(changed_files: list[str]) -> PatchArtifact:
    return PatchArtifact(
        base_sha="a",
        commit_sha="b",
        changed_files=changed_files,
        added_lines=1,
        removed_lines=0,
        patch_text="diff --git",
    )


def test_missing_issue_or_repository_goes_to_persist_run() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    assert router.route_after_normalize(state) == router.PERSIST_RUN


def test_normalize_error_goes_to_persist_run() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["errors"] = ["bad ref"]
    assert router.route_after_normalize(state) == router.PERSIST_RUN


def test_valid_issue_and_repository_proceeds_to_plan() -> None:
    from datetime import UTC, datetime
    from pathlib import Path

    from issue_to_patch.ingestion.models import IssueRequest, IssueSource, RepositorySnapshot

    state = new_state(run_id="r1", issue_ref="x")
    state["issue"] = IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", body="x")
    state["repository"] = RepositorySnapshot(
        source="local",
        commit_sha="0" * 40,
        tree_hash="t",
        root_path=Path("/tmp/x"),
        file_count=1,
        created_at=datetime.now(UTC),
    )
    assert router.route_after_normalize(state) == router.PLAN_INVESTIGATION


def test_low_retrieval_confidence_expands_once() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["evidence"] = [Evidence(kind="code", content="c", score=0.1, source_location=_loc())]
    assert router.route_after_retrieve_code(state, _SETTINGS) == router.SELECT_ADDITIONAL_EVIDENCE


def test_no_code_evidence_expands_once() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["evidence"] = [Evidence(kind="issue", content="c")]
    assert router.route_after_retrieve_code(state, _SETTINGS) == router.SELECT_ADDITIONAL_EVIDENCE


def test_low_confidence_after_expansion_proceeds_anyway() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["evidence"] = [Evidence(kind="code", content="c", score=0.1, source_location=_loc())]
    state["expanded"] = True
    assert router.route_after_retrieve_code(state, _SETTINGS) == router.ANALYZE_ROOT_CAUSE


def test_high_retrieval_confidence_proceeds_to_analysis() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["evidence"] = [Evidence(kind="code", content="c", score=0.9, source_location=_loc())]
    assert router.route_after_retrieve_code(state, _SETTINGS) == router.ANALYZE_ROOT_CAUSE


def test_no_hypotheses_expands_once_then_gives_up() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    assert router.route_after_analyze(state, _SETTINGS) == router.SELECT_ADDITIONAL_EVIDENCE
    state["expanded"] = True
    assert router.route_after_analyze(state, _SETTINGS) == router.PERSIST_RUN


def test_low_confidence_hypothesis_expands_once_then_gives_up() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["hypotheses"] = [Hypothesis(summary="maybe", confidence=0.1)]
    assert router.route_after_analyze(state, _SETTINGS) == router.SELECT_ADDITIONAL_EVIDENCE
    state["expanded"] = True
    assert router.route_after_analyze(state, _SETTINGS) == router.PERSIST_RUN


def test_conflicting_top_hypotheses_trigger_additional_retrieval() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["hypotheses"] = [
        Hypothesis(summary="a", confidence=0.6),
        Hypothesis(summary="b", confidence=0.58),
    ]
    assert router.route_after_analyze(state, _SETTINGS) == router.SELECT_ADDITIONAL_EVIDENCE


def test_conflicting_hypotheses_after_expansion_proceed_to_draft_anyway() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["hypotheses"] = [
        Hypothesis(summary="a", confidence=0.6),
        Hypothesis(summary="b", confidence=0.58),
    ]
    state["expanded"] = True
    assert router.route_after_analyze(state, _SETTINGS) == router.DRAFT_PATCH


def test_confident_clear_winner_proceeds_to_draft() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["hypotheses"] = [
        Hypothesis(summary="a", confidence=0.9),
        Hypothesis(summary="b", confidence=0.2),
    ]
    assert router.route_after_analyze(state, _SETTINGS) == router.DRAFT_PATCH


def test_failed_edit_application_revises_when_budget_remains() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    assert router.route_after_draft(state, _SETTINGS) == router.REVISE_PATCH


def test_failed_edit_application_gives_up_once_budget_is_exhausted() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["revisions_used"] = 1
    assert router.route_after_draft(state, _SETTINGS) == router.PERSIST_RUN


def test_applied_patch_proceeds_to_validation() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["candidate_patch"] = PatchArtifact(
        base_sha="a",
        commit_sha="b",
        changed_files=["a.py"],
        added_lines=1,
        removed_lines=0,
        patch_text="diff --git",
    )
    assert router.route_after_draft(state, _SETTINGS) == router.RUN_PATCH_VALIDATION


def _validation(state_: RunState) -> ValidationReport:
    status = CheckStatus.FAIL if state_ is RunState.PATCH_REJECTED else CheckStatus.PASS
    return ValidationReport(checks=[CheckResult(name="x", status=status)], run_state=state_)


def test_rejected_patch_revises_when_budget_remains() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["validation"] = _validation(RunState.PATCH_REJECTED)
    assert router.route_after_validation(state, _SETTINGS) == router.REVISE_PATCH


def test_rejected_patch_gives_up_once_budget_is_exhausted() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["validation"] = _validation(RunState.PATCH_REJECTED)
    state["revisions_used"] = 1
    assert router.route_after_validation(state, _SETTINGS) == router.PERSIST_RUN


def test_validated_patch_goes_to_human_gate() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["validation"] = _validation(RunState.PATCH_VALIDATED)
    assert router.route_after_validation(state, _SETTINGS) == router.REQUEST_HUMAN_VALIDATION


def test_patch_requiring_review_also_goes_to_human_gate() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["validation"] = _validation(RunState.PATCH_REQUIRES_HUMAN_REVIEW)
    assert router.route_after_validation(state, _SETTINGS) == router.REQUEST_HUMAN_VALIDATION


def test_human_approval_proceeds_to_evaluation() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["human_decision"] = HumanDecision(decision="approve")
    assert router.route_after_human(state, _SETTINGS) == router.EVALUATE_RUN


def test_human_rejection_ends_the_run() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["human_decision"] = HumanDecision(decision="reject")
    assert router.route_after_human(state, _SETTINGS) == router.PERSIST_RUN


def test_human_revise_request_consumes_budget() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["human_decision"] = HumanDecision(decision="revise")
    assert router.route_after_human(state, _SETTINGS) == router.REVISE_PATCH


def test_human_revise_request_gives_up_once_budget_is_exhausted() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["human_decision"] = HumanDecision(decision="revise")
    state["revisions_used"] = 1
    assert router.route_after_human(state, _SETTINGS) == router.PERSIST_RUN


def test_gatekeeper_approval_of_a_risky_patch_proceeds_to_evaluation() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["candidate_patch"] = _patch([".github/workflows/deploy.yml"])
    state["human_decision"] = HumanDecision(decision="approve", role=ReviewerRole.GATEKEEPER)
    assert router.approval_is_authorized(state) is True
    assert router.route_after_human(state, _SETTINGS) == router.EVALUATE_RUN


def test_non_gatekeeper_approval_of_a_risky_patch_is_not_authorized() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["candidate_patch"] = _patch([".github/workflows/deploy.yml"])
    state["human_decision"] = HumanDecision(decision="approve", role=ReviewerRole.AUDITOR)
    assert router.approval_is_authorized(state) is False
    assert router.route_after_human(state, _SETTINGS) == router.PERSIST_RUN


def test_any_role_can_approve_a_non_risky_patch() -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["candidate_patch"] = _patch(["src/calculator.py"])
    state["human_decision"] = HumanDecision(decision="approve", role=ReviewerRole.STRATEGIST)
    assert router.approval_is_authorized(state) is True
    assert router.route_after_human(state, _SETTINGS) == router.EVALUATE_RUN
