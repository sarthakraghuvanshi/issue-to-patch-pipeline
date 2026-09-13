"""ITP_AGENT_MODE=multi through the real, compiled graph: the six-specialist
pipeline slots into exactly the same AnalyzeRootCause/DraftPatch/RevisePatch
nodes the single-agent path uses, so validation, the human gate, and
revise-or-reject all behave identically regardless of which mode produced
the hypothesis and the edit plan."""

from __future__ import annotations

from pathlib import Path

import pytest

from issue_to_patch.config.settings import AgentMode, Settings
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.run import resume_investigation, start_investigation
from issue_to_patch.graph.state import HumanDecision
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.persistence import Store
from issue_to_patch.processing import index_snapshot
from issue_to_patch.retrieval import RetrievalService
from issue_to_patch.run_states import RunState

pytestmark = pytest.mark.integration


@pytest.fixture
def multi_agent_deps(fixture_repo: Path, tmp_path: Path):
    snap = create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/calc")
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'g.db'}")
    store.create_all()
    index_snapshot(snap, store)
    llm = FakeLLM()
    deps = GraphDependencies(
        llm=llm,
        retrieval=RetrievalService(store),
        store=store,
        settings=Settings(agent_mode=AgentMode.MULTI, artifacts_dir=tmp_path / "artifacts"),
    )
    return deps, snap


def _queue_happy_multi_agent_path(llm: FakeLLM) -> None:
    # PlanInvestigation (single-agent node, unaffected by agent_mode)
    llm.queue_structured({"search_queries": ["add returns wrong result"], "focus_areas": []})
    # Issue Analyst
    llm.queue_structured(
        {
            "symptoms": ["add() returns a smaller number than expected"],
            "expected_behavior": "a + b",
            "acceptance_criteria": ["add(2, 3) == 5"],
        }
    )
    # Root-Cause Analyst
    llm.queue_structured(
        {"hypotheses": [{"summary": "subtracts instead of adds", "confidence": 0.9}]}
    )
    # Patch Author
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
    # Patch Reviewer
    llm.queue_structured({"approved": True})


def test_multi_agent_mode_reaches_patch_validated(multi_agent_deps) -> None:
    deps, snap = multi_agent_deps
    _queue_happy_multi_agent_path(deps.llm)

    handle = start_investigation(
        issue_ref="add() returns the wrong result",
        repository=snap,
        deps=deps,
        allowed_scope=["calculator.py"],
    )
    assert handle.awaiting_human is True
    assert handle.state["candidate_patch"] is not None
    assert handle.state["validation"].run_state is RunState.PATCH_VALIDATED
    assert handle.state["hypotheses"][0].summary == "subtracts instead of adds"
    # citations still trace back to a real chunk, exactly like single-agent mode
    for loc in handle.state["hypotheses"][0].cites:
        assert deps.store.get_chunk(loc.chunk_id) is not None

    resumed = resume_investigation(handle, HumanDecision(decision="approve"))
    assert resumed.state["final_state"] is RunState.PATCH_VALIDATED


def test_multi_agent_mode_treats_a_reviewer_rejection_like_a_failed_apply(
    multi_agent_deps,
) -> None:
    """The Patch Reviewer rejecting a draft must be handled exactly like an
    edit plan that failed to apply: revise once (consuming the same budget),
    then give up - never a crash, never a silent success."""
    deps, snap = multi_agent_deps
    llm = deps.llm
    llm.queue_structured({"search_queries": [], "focus_areas": []})
    llm.queue_structured({"symptoms": [], "expected_behavior": ""})
    llm.queue_structured({"hypotheses": [{"summary": "bad guess", "confidence": 0.9}]})
    bad_edit = {
        "message": "fix",
        "edits": [
            {"path": "calculator.py", "old": "return a - b  # BUG: should be a + b", "new": "x"}
        ],
    }
    # Attempt 1: drafted, then rejected by the reviewer
    llm.queue_structured(bad_edit)
    llm.queue_structured({"approved": False, "concerns": ["doesn't look right"]})
    # Attempt 2 (RevisePatch): drafted again, rejected again
    llm.queue_structured(bad_edit)
    llm.queue_structured({"approved": False, "concerns": ["still wrong"]})

    handle = start_investigation(
        issue_ref="add() returns the wrong result",
        repository=snap,
        deps=deps,
        allowed_scope=["calculator.py"],
    )
    assert handle.awaiting_human is False
    assert handle.state["final_state"] is RunState.INVESTIGATION_INCONCLUSIVE


def test_single_agent_mode_is_still_the_default(tmp_path: Path) -> None:
    assert Settings().agent_mode is AgentMode.SINGLE
