"""evaluation/judge.py: the LLM judge, grounded in a run's own stored state."""

from __future__ import annotations

import json
from pathlib import Path

from issue_to_patch.config.settings import Settings
from issue_to_patch.evaluation.judge import PROMPT_VERSION, judge_run
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.state import Hypothesis, SourceLocation, new_state
from issue_to_patch.ingestion.models import IssueRequest, IssueSource
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.patching.models import PatchArtifact
from issue_to_patch.persistence import Store
from issue_to_patch.retrieval import RetrievalService


def _deps(tmp_path: Path, llm: FakeLLM) -> GraphDependencies:
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'g.db'}")
    store.create_all()
    return GraphDependencies(
        llm=llm,
        retrieval=RetrievalService(store),
        store=store,
        settings=Settings(artifacts_dir=tmp_path / "artifacts"),
    )


def _score_payload(**overrides: float | str) -> dict[str, float | str]:
    base: dict[str, float | str] = {
        "root_cause_correctness": 0.9,
        "evidence_sufficiency": 0.8,
        "patch_relevance": 0.85,
        "patch_minimality": 0.7,
        "explanation_faithfulness": 0.9,
        "rationale": "grounded in the cited chunk",
    }
    base.update(overrides)
    return base


def test_judge_run_returns_scores_and_persists_them(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(_score_payload())
    deps = _deps(tmp_path, llm)
    deps.store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")

    state = new_state(run_id="r1", issue_ref="x")
    state["issue"] = IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", title="t", body="b")

    record = judge_run("r1", state, deps)

    assert record.run_id == "r1"
    assert record.prompt_version == PROMPT_VERSION
    assert record.scores.root_cause_correctness == 0.9
    assert record.scores.rationale == "grounded in the cited chunk"

    artifacts = deps.store.list_artifacts("r1")
    assert any(a.kind == "eval_judge" for a in artifacts)
    judge_artifact = next(a for a in artifacts if a.kind == "eval_judge")
    on_disk = json.loads(Path(judge_artifact.uri).read_text("utf-8"))
    assert on_disk["scores"]["root_cause_correctness"] == 0.9


def test_judge_run_includes_the_content_of_cited_chunks_in_the_prompt(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(_score_payload())
    deps = _deps(tmp_path, llm)
    deps.store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")

    chunk_row = {
        "chunk_id": "c" * 24,
        "repository": "acme/x",
        "commit_sha": "0" * 40,
        "path": "calculator.py",
        "language": "python",
        "kind": "function",
        "symbol": "add",
        "line_start": 1,
        "line_end": 2,
        "content": "def add(a, b):\n    return a - b",
        "content_hash": "h",
        "parent_chunk_id": None,
        "summary": "",
        "keywords": [],
        "questions": [],
        "reference_paths": [],
    }
    deps.store.replace_chunks("acme/x", "0" * 40, [chunk_row])

    loc = SourceLocation(chunk_id="c" * 24, path="calculator.py", line_start=1, line_end=2)
    state = new_state(run_id="r1", issue_ref="x")
    state["issue"] = IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", title="t", body="b")
    state["hypotheses"] = [Hypothesis(summary="off by sign", confidence=0.9, cites=[loc])]
    state["candidate_patch"] = PatchArtifact(
        base_sha="a",
        commit_sha="b",
        changed_files=["calculator.py"],
        added_lines=1,
        removed_lines=1,
        patch_text="diff --git a/calculator.py b/calculator.py",
    )

    judge_run("r1", state, deps)

    sent = llm.calls[0][-1].content
    assert "return a - b" in sent
    assert "off by sign" in sent
    assert "diff --git" in sent


def test_judge_run_handles_a_run_with_no_hypothesis_or_patch_yet(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(_score_payload(root_cause_correctness=0.0, rationale="inconclusive"))
    deps = _deps(tmp_path, llm)
    deps.store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")
    state = new_state(run_id="r1", issue_ref="x")

    record = judge_run("r1", state, deps)
    assert record.scores.rationale == "inconclusive"
