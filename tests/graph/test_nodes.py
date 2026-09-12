"""Each graph node in isolation: one job, through GraphDependencies only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from issue_to_patch.graph import nodes
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.state import (
    Evidence,
    HumanDecision,
    Hypothesis,
    ReviewerRole,
    SourceLocation,
    new_state,
)
from issue_to_patch.ingestion.models import IssueRequest, IssueSource, RepositorySnapshot
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.patching.models import CheckResult, CheckStatus, PatchArtifact, ValidationReport
from issue_to_patch.persistence import Store
from issue_to_patch.processing import index_snapshot
from issue_to_patch.retrieval import RetrievalService
from issue_to_patch.run_states import RunState


def _deps(tmp_path: Path, llm: FakeLLM | None = None) -> GraphDependencies:
    from issue_to_patch.config.settings import Settings

    store = Store(f"sqlite+pysqlite:///{tmp_path / 'g.db'}")
    store.create_all()
    return GraphDependencies(
        llm=llm or FakeLLM(),
        retrieval=RetrievalService(store),
        store=store,
        settings=Settings(),
    )


def _repo(tmp_path: Path) -> RepositorySnapshot:
    return RepositorySnapshot(
        source="local",
        commit_sha="0" * 40,
        tree_hash="t",
        root_path=tmp_path,
        file_count=1,
        created_at=datetime.now(UTC),
    )


# -- NormalizeRequest -----------------------------------------------------
def test_normalize_request_parses_a_valid_reference(tmp_path: Path) -> None:
    state = new_state(run_id="r1", issue_ref="the add function is broken")
    out = nodes.normalize_request(state, _deps(tmp_path))
    assert out["issue"].body == "the add function is broken"


def test_normalize_request_records_an_error_for_a_bad_reference(tmp_path: Path) -> None:
    state = new_state(run_id="r1", issue_ref="")
    out = nodes.normalize_request(state, _deps(tmp_path))
    assert out["errors"] and "empty" in out["errors"][0].lower()


# -- RetrieveIssueContext ---------------------------------------------------
def test_retrieve_issue_context_cites_the_issue_body_and_labels(tmp_path: Path) -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["issue"] = IssueRequest(
        source=IssueSource.RAW_TEXT, source_ref="x", title="t", body="b", labels=["bug"]
    )
    out = nodes.retrieve_issue_context(state, _deps(tmp_path))
    evidence = out["evidence"]
    assert any(e.kind == "issue" and "b" in e.content for e in evidence)
    assert any(e.note == "issue labels" for e in evidence)


# -- RetrieveCodeContext / SelectAdditionalEvidence -----------------------
@pytest.fixture
def indexed(indexable_repo: Path, tmp_path: Path) -> tuple[GraphDependencies, RepositorySnapshot]:
    snap = create_snapshot(str(indexable_repo), tmp_path / "snap", repo_name="acme/sample")
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'idx.db'}")
    store.create_all()
    index_snapshot(snap, store)
    from issue_to_patch.config.settings import Settings

    deps = GraphDependencies(
        llm=FakeLLM(), retrieval=RetrievalService(store), store=store, settings=Settings()
    )
    return deps, snap


def test_retrieve_code_context_returns_grounded_evidence(indexed) -> None:
    deps, snap = indexed
    state = new_state(run_id="r1", issue_ref="x")
    state["repository"] = snap
    state["issue"] = IssueRequest(
        source=IssueSource.RAW_TEXT,
        source_ref="x",
        title="parse_issue_url does not strip whitespace",
        body="",
    )
    out = nodes.retrieve_code_context(state, deps)
    evidence = out["evidence"]
    assert evidence
    assert all(e.kind == "code" and e.source_location is not None for e in evidence)
    assert any(e.source_location.path == "src/sample/parser.py" for e in evidence)
    assert all(0.0 <= e.score <= 1.0 for e in evidence)  # normalized, not a raw RRF score


def test_retrieve_code_context_reports_an_error_for_an_unindexed_repo(tmp_path: Path) -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["repository"] = _repo(tmp_path)
    state["issue"] = IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", title="t", body="b")
    out = nodes.retrieve_code_context(state, _deps(tmp_path))
    assert out["errors"]


def test_select_additional_evidence_marks_expanded_and_skips_duplicates(indexed) -> None:
    deps, snap = indexed
    state = new_state(run_id="r1", issue_ref="x")
    state["repository"] = snap
    state["issue"] = IssueRequest(
        source=IssueSource.RAW_TEXT, source_ref="x", title="parse_issue_url whitespace", body=""
    )
    first = nodes.retrieve_code_context(state, deps)
    state["evidence"] = first["evidence"]

    out = nodes.select_additional_evidence(state, deps)
    assert out["expanded"] is True
    seen_before = {e.source_location.chunk_id for e in state["evidence"]}
    assert all(e.source_location.chunk_id not in seen_before for e in out["evidence"])


# -- AnalyzeRootCause -------------------------------------------------------
def test_analyze_root_cause_returns_llm_hypotheses(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(
        {
            "hypotheses": [
                {
                    "summary": "off by one",
                    "confidence": 0.8,
                    "cites": [{"chunk_id": "c1", "path": "a.py", "line_start": 1, "line_end": 2}],
                }
            ]
        }
    )
    state = new_state(run_id="r1", issue_ref="x")
    state["issue"] = IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", title="t", body="b")
    state["evidence"] = [
        Evidence(
            kind="code",
            content="def add(a, b): return a - b",
            source_location=SourceLocation(chunk_id="c1", path="a.py", line_start=1, line_end=2),
            score=0.9,
        )
    ]
    out = nodes.analyze_root_cause(state, _deps(tmp_path, llm))
    assert out["hypotheses"][0].summary == "off by one"
    assert out["selected_files"] == out["hypotheses"][0].cites


def test_analyze_root_cause_backfills_missing_citations(tmp_path: Path) -> None:
    """A hypothesis with no citation must not reach a patch draft ungrounded."""
    llm = FakeLLM()
    llm.queue_structured({"hypotheses": [{"summary": "no citation given", "confidence": 0.7}]})
    loc = SourceLocation(chunk_id="c1", path="a.py", line_start=1, line_end=2)
    state = new_state(run_id="r1", issue_ref="x")
    state["issue"] = IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", title="t", body="b")
    state["evidence"] = [Evidence(kind="code", content="c", source_location=loc, score=0.9)]
    out = nodes.analyze_root_cause(state, _deps(tmp_path, llm))
    assert out["hypotheses"][0].cites == [loc]


# -- DraftPatch / RevisePatch -----------------------------------------------
def test_draft_patch_generates_a_real_patch(fixture_repo: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(
        {
            "message": "fix: correct add()",
            "edits": [
                {
                    "path": "calculator.py",
                    "old": "return a - b  # BUG: should be a + b",
                    "new": "return a + b",
                }
            ],
        }
    )
    snap = create_snapshot(str(fixture_repo), fixture_repo.parent / "snap", repo_name="acme/calc")
    state = new_state(run_id="r1", issue_ref="x")
    state["repository"] = snap
    state["issue"] = IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", title="t", body="b")
    out = nodes.draft_patch(state, _deps(fixture_repo.parent, llm))
    assert out["candidate_patch"] is not None
    assert "calculator.py" in out["candidate_patch"].changed_files
    assert "a + b" in out["candidate_patch"].patch_text


def test_draft_patch_records_an_error_when_the_edit_does_not_apply(fixture_repo: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(
        {"message": "fix", "edits": [{"path": "calculator.py", "old": "not present", "new": "x"}]}
    )
    snap = create_snapshot(str(fixture_repo), fixture_repo.parent / "snap", repo_name="acme/calc")
    state = new_state(run_id="r1", issue_ref="x")
    state["repository"] = snap
    state["issue"] = IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", title="t", body="b")
    out = nodes.draft_patch(state, _deps(fixture_repo.parent, llm))
    assert out["candidate_patch"] is None
    assert out["errors"]


def test_revise_patch_bumps_the_revision_counter(fixture_repo: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(
        {
            "message": "fix",
            "edits": [
                {
                    "path": "calculator.py",
                    "old": "return a - b  # BUG: should be a + b",
                    "new": "return a + b",
                }
            ],
        }
    )
    snap = create_snapshot(str(fixture_repo), fixture_repo.parent / "snap", repo_name="acme/calc")
    state = new_state(run_id="r1", issue_ref="x")
    state["repository"] = snap
    state["issue"] = IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", title="t", body="b")
    state["validation"] = ValidationReport(
        checks=[CheckResult(name="scope", status=CheckStatus.FAIL, detail="oops")],
        run_state=RunState.PATCH_REJECTED,
    )
    out = nodes.revise_patch(state, _deps(fixture_repo.parent, llm))
    assert out["revisions_used"] == 1
    assert out["candidate_patch"] is not None


# -- RunPatchValidation ------------------------------------------------------
def test_run_patch_validation_delegates_to_validate_patch(
    fixture_repo: Path, tmp_path: Path
) -> None:
    snap = create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/calc")
    patch = PatchArtifact(
        base_sha=snap.commit_sha,
        commit_sha=snap.commit_sha,
        changed_files=["calculator.py"],
        added_lines=1,
        removed_lines=1,
        patch_text="not a real diff",
    )
    state = new_state(run_id="r1", issue_ref="x", allowed_scope=["calculator.py"])
    state["repository"] = snap
    state["candidate_patch"] = patch
    out = nodes.run_patch_validation(state, _deps(tmp_path))
    assert out["validation"].run_state is RunState.PATCH_REJECTED  # syntax check fails


# -- RequestHumanValidation ---------------------------------------------------
def test_request_human_validation_is_a_noop_once_decision_is_set(tmp_path: Path) -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["human_decision"] = HumanDecision(decision="approve")
    assert nodes.request_human_validation(state, _deps(tmp_path)) == {}


def test_request_human_validation_persists_the_decision_for_the_audit_trail(
    tmp_path: Path,
) -> None:
    deps = _deps(tmp_path)
    state = new_state(run_id="r1", issue_ref="x")
    state["human_decision"] = HumanDecision(
        decision="approve", reason="looks right", reviewer="alice", role=ReviewerRole.GATEKEEPER
    )
    nodes.request_human_validation(state, deps)

    recorded = deps.store.list_human_decisions("r1")
    assert len(recorded) == 1
    assert recorded[0].reviewer == "alice"
    assert recorded[0].role == "gatekeeper"
    assert recorded[0].decision == "approve"
    assert deps.store.verify_decision_chain("r1") is True


def test_request_human_validation_errors_if_resumed_without_a_decision(tmp_path: Path) -> None:
    state = new_state(run_id="r1", issue_ref="x")
    out = nodes.request_human_validation(state, _deps(tmp_path))
    assert out["errors"]


# -- EvaluateRun ---------------------------------------------------------------
def test_evaluate_run_summarizes_the_state(tmp_path: Path) -> None:
    state = new_state(run_id="r1", issue_ref="x")
    state["evidence"] = [Evidence(kind="issue", content="x")]
    state["hypotheses"] = [Hypothesis(summary="a", confidence=0.7)]
    state["revisions_used"] = 1
    out = nodes.evaluate_run(state, _deps(tmp_path))
    report = out["evaluation"]
    assert report.evidence_count == 1
    assert report.top_hypothesis_confidence == 0.7
    assert report.revisions_used == 1


# -- PersistRun ------------------------------------------------------------
@pytest.mark.parametrize(
    ("build_state", "expected"),
    [
        (lambda s: s.__setitem__("errors", ["boom"]), RunState.INVESTIGATION_INCONCLUSIVE),
        (
            lambda s: s.__setitem__("human_decision", HumanDecision(decision="approve")),
            RunState.PATCH_VALIDATED,
        ),
        (
            lambda s: s.__setitem__("human_decision", HumanDecision(decision="reject")),
            RunState.PATCH_REJECTED,
        ),
        (
            lambda s: s.__setitem__("human_decision", HumanDecision(decision="revise")),
            RunState.PATCH_REQUIRES_HUMAN_REVIEW,
        ),
        (
            lambda s: s.__setitem__(
                "validation",
                ValidationReport(
                    checks=[CheckResult(name="x", status=CheckStatus.FAIL)],
                    run_state=RunState.PATCH_REJECTED,
                ),
            ),
            RunState.PATCH_REJECTED,
        ),
        (lambda s: None, RunState.INVESTIGATION_INCONCLUSIVE),
        (
            lambda s: (
                s.__setitem__(
                    "candidate_patch",
                    PatchArtifact(
                        base_sha="a",
                        commit_sha="b",
                        changed_files=[".github/workflows/deploy.yml"],
                        added_lines=1,
                        removed_lines=0,
                        patch_text="diff --git",
                    ),
                ),
                s.__setitem__(
                    "human_decision",
                    HumanDecision(decision="approve", role=ReviewerRole.GATEKEEPER),
                ),
            ),
            RunState.PATCH_VALIDATED,
        ),
        (
            lambda s: (
                s.__setitem__(
                    "candidate_patch",
                    PatchArtifact(
                        base_sha="a",
                        commit_sha="b",
                        changed_files=[".github/workflows/deploy.yml"],
                        added_lines=1,
                        removed_lines=0,
                        patch_text="diff --git",
                    ),
                ),
                s.__setitem__(
                    "human_decision",
                    HumanDecision(decision="approve", role=ReviewerRole.AUDITOR),
                ),
            ),
            RunState.PATCH_REQUIRES_HUMAN_REVIEW,
        ),
    ],
)
def test_persist_run_determines_the_right_final_state(tmp_path, build_state, expected) -> None:
    deps = _deps(tmp_path)
    deps.store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")
    state = new_state(run_id="r1", issue_ref="x")
    build_state(state)
    out = nodes.persist_run(state, deps)
    assert out["final_state"] is expected
    assert deps.store.get_run_state("r1") == expected.value
