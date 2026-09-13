"""evaluation/run_suite.py: combining retrieval eval, run metrics, and the
judge into one report — and rendering it as HTML."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from issue_to_patch.config.settings import Settings
from issue_to_patch.evaluation.models import JudgeRecord, JudgeScores, SuiteMetrics, SuiteReport
from issue_to_patch.evaluation.run_suite import build_suite_report, render_suite_report_html
from issue_to_patch.graph.checkpoint import sqlite_checkpointer
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.run import start_investigation
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.persistence import Store
from issue_to_patch.processing import index_snapshot
from issue_to_patch.retrieval import RetrievalService

pytestmark = pytest.mark.integration


def _labeled_jsonl(path: Path, *, repository: str, commit_sha: str, gold_file: str) -> Path:
    row = {
        "issue_id": "acme/x#1",
        "repository": repository,
        "commit_sha": commit_sha,
        "query": "parse_issue_url does not strip whitespace",
        "labels": [],
        "gold_files": [gold_file],
        "gold_symbols": [],
    }
    path.write_text(json.dumps(row) + "\n", "utf-8")
    return path


def test_build_suite_report_with_only_a_labeled_set_computes_retrieval(
    indexable_repo: Path, tmp_path: Path
) -> None:
    snap = create_snapshot(str(indexable_repo), tmp_path / "snap", repo_name="acme/sample")
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'g.db'}")
    store.create_all()
    index_snapshot(snap, store)
    labeled = _labeled_jsonl(
        tmp_path / "labeled.jsonl",
        repository=snap.repo,
        commit_sha=snap.commit_sha,
        gold_file="src/sample/parser.py",
    )

    report = build_suite_report(store, labeled_path=labeled)

    assert report.retrieval is not None
    assert report.suite_metrics is None
    assert report.judge_records == []


def test_build_suite_report_with_only_run_ids_computes_metrics_but_no_judge(
    tmp_path: Path,
) -> None:
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'g.db'}")
    store.create_all()
    store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")
    store.finish_run("r1", state="PATCH_VALIDATED", cost_usd=0.01)

    report = build_suite_report(store, run_ids=["r1"])

    assert report.retrieval is None
    assert report.suite_metrics is not None
    assert report.suite_metrics.run_count == 1
    assert report.judge_records == []


def test_build_suite_report_with_judge_deps_judges_each_run(
    fixture_repo: Path, tmp_path: Path
) -> None:
    snap = create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/calc")
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'g.db'}")
    store.create_all()
    index_snapshot(snap, store)

    graph_llm = FakeLLM()
    graph_llm.queue_structured({"search_queries": [], "focus_areas": []})
    graph_llm.queue_structured(
        {"hypotheses": [{"summary": "subtracts instead of adds", "confidence": 0.9}]}
    )
    graph_llm.queue_structured(
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
    graph_deps = GraphDependencies(
        llm=graph_llm,
        retrieval=RetrievalService(store),
        store=store,
        settings=Settings(artifacts_dir=tmp_path / "artifacts"),
    )
    # a real CLI/API-originated run always uses the durable checkpointer, not
    # the process-local default - build_suite_report's judge path looks
    # there, so the test has to put the run there too.
    checkpointer = sqlite_checkpointer(graph_deps.settings.artifacts_dir / "checkpoints.db")
    handle = start_investigation(
        issue_ref="add() bug",
        repository=snap,
        deps=graph_deps,
        allowed_scope=["calculator.py"],
        checkpointer=checkpointer,
    )

    judge_llm = FakeLLM()
    judge_llm.queue_structured(
        {
            "root_cause_correctness": 0.9,
            "evidence_sufficiency": 0.8,
            "patch_relevance": 0.9,
            "patch_minimality": 0.7,
            "explanation_faithfulness": 0.85,
            "rationale": "matches the cited chunk",
        }
    )
    judge_deps = GraphDependencies(
        llm=judge_llm,
        retrieval=RetrievalService(store),
        store=store,
        settings=graph_deps.settings,
    )

    report = build_suite_report(store, run_ids=[handle.run_id], judge_deps=judge_deps)

    assert report.suite_metrics is not None
    assert len(report.judge_records) == 1
    assert report.judge_records[0].run_id == handle.run_id
    assert report.judge_records[0].scores.rationale == "matches the cited chunk"


def test_render_html_handles_a_completely_empty_report() -> None:
    html = render_suite_report_html(SuiteReport())
    assert "Nothing to report" in html
    assert "<html>" in html


def test_render_html_includes_every_present_section() -> None:
    from issue_to_patch.retrieval import RetrievalMode
    from issue_to_patch.retrieval.evaluation import ModeScores, RetrievalReport

    retrieval = RetrievalReport(
        ks=[1, 5],
        per_mode=[
            ModeScores(
                mode=RetrievalMode.HYBRID,
                n=1,
                file_recall_at={1: 0.5, 5: 1.0},
                symbol_recall_at={1: 0.5, 5: 1.0},
                mrr=0.75,
                ndcg_at_10=0.8,
            )
        ],
    )
    metrics = SuiteMetrics(
        run_count=1,
        patch_apply_rate=1.0,
        unrelated_file_change_rate=0.0,
        median_latency_seconds=1.5,
        p95_latency_seconds=2.0,
        median_cost_usd=0.01,
        total_cost_usd=0.01,
        runs=[],
    )
    report = SuiteReport(
        retrieval=retrieval,
        suite_metrics=metrics,
        judge_records=[
            JudgeRecord(
                run_id="r1",
                model="fake",
                prompt_version="judge-v1",
                scores=JudgeScores(
                    root_cause_correctness=0.5,
                    evidence_sufficiency=0.5,
                    patch_relevance=0.5,
                    patch_minimality=0.5,
                    explanation_faithfulness=0.5,
                    rationale="test rationale",
                ),
            )
        ],
    )
    html = render_suite_report_html(report)
    assert "Retrieval" in html
    assert "Run metrics" in html
    assert "LLM judge" in html
    assert "test rationale" in html
