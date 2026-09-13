"""End-to-end: the compiled graph runs on a real (fixture) repo with a fake LLM,
pauses at the human gate, and every terminal path lands on exactly one RunState."""

from __future__ import annotations

from pathlib import Path

import pytest

from issue_to_patch.config.settings import Settings
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.run import resume_investigation, start_investigation
from issue_to_patch.graph.state import HumanDecision, ReviewerRole
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.persistence import Store
from issue_to_patch.processing import index_snapshot
from issue_to_patch.retrieval import RetrievalService
from issue_to_patch.run_states import RunState

pytestmark = pytest.mark.integration

_FIX = {
    "message": "fix: correct add()",
    "edits": [
        {
            "path": "calculator.py",
            "old": "return a - b  # BUG: should be a + b",
            "new": "return a + b",
        }
    ],
}


@pytest.fixture
def deps_and_repo(fixture_repo: Path, tmp_path: Path):
    snap = create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/calc")
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'g.db'}")
    store.create_all()
    index_snapshot(snap, store)
    llm = FakeLLM()
    # artifacts_dir must not default to "artifacts" (relative to cwd) — PersistRun
    # writes a real fix.patch/validation.json per run, and would otherwise leak
    # test output into the actual project's artifacts/ directory.
    deps = GraphDependencies(
        llm=llm,
        retrieval=RetrievalService(store),
        store=store,
        settings=Settings(artifacts_dir=tmp_path / "artifacts"),
    )
    return deps, snap


def _queue_happy_path(llm: FakeLLM) -> None:
    llm.queue_structured({"search_queries": ["add returns wrong result"], "focus_areas": []})
    llm.queue_structured(
        {"hypotheses": [{"summary": "subtracts instead of adds", "confidence": 0.9}]}
    )
    llm.queue_structured(_FIX)


def test_full_run_pauses_at_human_gate_then_approves_to_patch_validated(deps_and_repo) -> None:
    deps, snap = deps_and_repo
    _queue_happy_path(deps.llm)

    handle = start_investigation(
        issue_ref="add() returns the wrong result",
        repository=snap,
        deps=deps,
        allowed_scope=["calculator.py"],
    )
    assert handle.awaiting_human is True
    assert handle.state["candidate_patch"] is not None
    assert handle.state["validation"].run_state is RunState.PATCH_VALIDATED
    # every citation on the way to a patch traces back to a real chunk
    assert handle.state["hypotheses"][0].cites
    for loc in handle.state["hypotheses"][0].cites:
        assert deps.store.get_chunk(loc.chunk_id) is not None

    resumed = resume_investigation(handle, HumanDecision(decision="approve", reviewer="alice"))
    assert resumed.awaiting_human is False
    assert resumed.state["final_state"] is RunState.PATCH_VALIDATED
    assert deps.store.get_run_state(handle.run_id) == "PATCH_VALIDATED"

    from issue_to_patch.persistence.audit import build_audit_trail

    trail = build_audit_trail(deps.store, handle.run_id)
    assert trail.found is True
    assert trail.is_tamper_evident_intact is True
    assert [d.reviewer for d in trail.decisions] == ["alice"]
    validate_call = next(t for t in trail.tool_calls if t.tool == "validate_patch")
    # result_hash must be an actual hash (fixed-length hex), never the raw
    # validation JSON itself - that was a real bug: readable but not a hash.
    assert len(validate_call.result_hash) == 64
    int(validate_call.result_hash, 16)  # raises if it isn't hex

    # PersistRun writes the patch/validation to real files and records them
    # as artifacts, exactly like Sprint 1's deterministic pipeline already
    # did - the graph path was missing this until Sprint 8.
    artifacts = {a.kind: a for a in deps.store.list_artifacts(handle.run_id)}
    assert "patch" in artifacts and "validation" in artifacts
    from pathlib import Path

    assert "diff --git" in Path(artifacts["patch"].uri).read_text("utf-8")
    assert "PATCH_VALIDATED" in Path(artifacts["validation"].uri).read_text("utf-8")


def test_risky_patch_needs_the_gatekeeper_role_specifically(deps_and_repo) -> None:
    """An Auditor's "approve" on a CI-file change isn't enough authority — the
    router (safety/permissions.py + graph/router.py) requires the Gatekeeper
    role for anything matching a risky glob."""
    deps, snap = deps_and_repo
    llm = deps.llm
    llm.queue_structured({"search_queries": [], "focus_areas": []})
    llm.queue_structured({"hypotheses": [{"summary": "ci needs a fix", "confidence": 0.9}]})
    llm.queue_structured(
        {
            "message": "fix: adjust ci",
            "edits": [{"path": ".github/workflows/deploy.yml", "old": "", "new": "name: deploy\n"}],
        }
    )

    handle = start_investigation(
        issue_ref="ci is broken",
        repository=snap,
        deps=deps,
        allowed_scope=[".github/**"],
    )
    assert handle.awaiting_human is True

    resumed = resume_investigation(
        handle, HumanDecision(decision="approve", reviewer="bob", role=ReviewerRole.AUDITOR)
    )
    assert resumed.state["final_state"] is RunState.PATCH_REQUIRES_HUMAN_REVIEW
    assert deps.store.get_run_state(handle.run_id) == "PATCH_REQUIRES_HUMAN_REVIEW"


def test_risky_patch_is_validated_once_the_gatekeeper_approves(deps_and_repo) -> None:
    deps, snap = deps_and_repo
    llm = deps.llm
    llm.queue_structured({"search_queries": [], "focus_areas": []})
    llm.queue_structured({"hypotheses": [{"summary": "ci needs a fix", "confidence": 0.9}]})
    llm.queue_structured(
        {
            "message": "fix: adjust ci",
            "edits": [{"path": ".github/workflows/deploy.yml", "old": "", "new": "name: deploy\n"}],
        }
    )

    handle = start_investigation(
        issue_ref="ci is broken", repository=snap, deps=deps, allowed_scope=[".github/**"]
    )
    resumed = resume_investigation(
        handle,
        HumanDecision(decision="approve", reviewer="alice", role=ReviewerRole.GATEKEEPER),
    )
    assert resumed.state["final_state"] is RunState.PATCH_VALIDATED


def test_human_rejection_ends_the_run_as_patch_rejected(deps_and_repo) -> None:
    deps, snap = deps_and_repo
    _queue_happy_path(deps.llm)

    handle = start_investigation(
        issue_ref="add() returns the wrong result",
        repository=snap,
        deps=deps,
        allowed_scope=["calculator.py"],
    )
    resumed = resume_investigation(handle, HumanDecision(decision="reject", reason="not it"))
    assert resumed.state["final_state"] is RunState.PATCH_REJECTED
    assert deps.store.get_run_state(handle.run_id) == "PATCH_REJECTED"


def test_edit_that_never_applies_is_inconclusive_after_one_revision_without_a_human(
    deps_and_repo,
) -> None:
    deps, snap = deps_and_repo
    deps.llm.queue_structured({"search_queries": [], "focus_areas": []})
    deps.llm.queue_structured({"hypotheses": [{"summary": "bad guess", "confidence": 0.9}]})
    # both attempts reference text that isn't in calculator.py -> EditApplicationError each time
    bad_fix = {"message": "fix", "edits": [{"path": "calculator.py", "old": "nope", "new": "x"}]}
    deps.llm.queue_structured(bad_fix)
    deps.llm.queue_structured(bad_fix)

    handle = start_investigation(
        issue_ref="add() returns the wrong result",
        repository=snap,
        deps=deps,
        allowed_scope=["calculator.py"],
    )
    assert handle.awaiting_human is False  # no patch ever applied -> straight to PersistRun
    assert handle.state["final_state"] is RunState.INVESTIGATION_INCONCLUSIVE
    assert deps.store.get_run_state(handle.run_id) == "INVESTIGATION_INCONCLUSIVE"


def test_bad_issue_reference_never_reaches_the_llm(deps_and_repo) -> None:
    deps, snap = deps_and_repo  # LLM queue stays empty -> any call would raise
    handle = start_investigation(issue_ref="", repository=snap, deps=deps)
    assert handle.awaiting_human is False
    assert handle.state["final_state"] is RunState.INVESTIGATION_INCONCLUSIVE
