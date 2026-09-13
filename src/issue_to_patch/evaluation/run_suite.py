"""Runs the evaluation suite (Phase 9): retrieval metrics from a labeled set,
and/or deterministic + judge metrics from a batch of already-completed runs.

Two separate inputs, deliberately not one "drive N new investigations from
scratch" flow: retrieval evaluation needs a labeled set (issue -> gold
files, see ``evals/README.md``); everything else needs real run_ids from
runs that already happened (via ``investigate`` or the API). Driving fresh
graph runs here would need a real LLM provider and real money — this repo
never spends that without being explicitly asked, so point ``run_ids`` at
runs you already made (with a real provider, or FakeLLM for a
plumbing-only smoke test) instead of generating new ones.
"""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver

from issue_to_patch.evaluation.judge import judge_run
from issue_to_patch.evaluation.metrics import compute_suite_metrics
from issue_to_patch.evaluation.models import JudgeRecord, SuiteReport
from issue_to_patch.graph.checkpoint import sqlite_checkpointer
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.run import load_investigation
from issue_to_patch.persistence import Store
from issue_to_patch.retrieval import RetrievalService, evaluate, load_labeled_issues


def build_suite_report(
    store: Store,
    *,
    labeled_path: str | Path | None = None,
    top_k: int = 10,
    run_ids: list[str] | None = None,
    judge_deps: GraphDependencies | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> SuiteReport:
    retrieval_report = None
    if labeled_path is not None:
        issues = load_labeled_issues(labeled_path)
        retrieval_report = evaluate(RetrievalService(store), issues, top_k=top_k)

    suite_metrics = None
    judge_records: list[JudgeRecord] = []
    if run_ids:
        suite_metrics = compute_suite_metrics(store, run_ids)
        if judge_deps is not None:
            cp = checkpointer or sqlite_checkpointer(
                judge_deps.settings.artifacts_dir / "checkpoints.db"
            )
            for run_id in run_ids:
                handle = load_investigation(run_id, judge_deps, checkpointer=cp)
                judge_records.append(judge_run(run_id, handle.state, judge_deps))

    return SuiteReport(
        retrieval=retrieval_report, suite_metrics=suite_metrics, judge_records=judge_records
    )


def _fmt_seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}s"


def render_suite_report_html(report: SuiteReport) -> str:
    sections = []

    if report.retrieval is not None:
        rows = "".join(
            f"<tr><td>{m.mode.value}</td>"
            + "".join(f"<td>{m.file_recall_at.get(k, 0.0):.3f}</td>" for k in report.retrieval.ks)
            + f"<td>{m.mrr:.3f}</td><td>{m.ndcg_at_10:.3f}</td></tr>"
            for m in report.retrieval.per_mode
        )
        headers = "".join(f"<th>R@{k}</th>" for k in report.retrieval.ks)
        sections.append(
            "<h2>Retrieval</h2>"
            f"<table><tr><th>mode</th>{headers}<th>MRR</th><th>nDCG@10</th></tr>{rows}</table>"
            f"<p>best by file recall@10: <b>{report.retrieval.best_mode().value}</b></p>"
        )

    if report.suite_metrics is not None:
        sm = report.suite_metrics
        run_rows = "".join(
            f"<tr><td>{r.run_id}</td><td>{r.final_state}</td>"
            f"<td>{'yes' if r.patch_applied else 'no'}</td>"
            f"<td>{'yes' if r.unrelated_file_change else 'no'}</td>"
            f"<td>{'' if r.latency_seconds is None else f'{r.latency_seconds:.2f}'}</td>"
            f"<td>${r.cost_usd:.4f}</td></tr>"
            for r in sm.runs
        )
        median_latency = _fmt_seconds(sm.median_latency_seconds)
        p95_latency = _fmt_seconds(sm.p95_latency_seconds)
        sections.append(
            "<h2>Run metrics</h2>"
            f"<p>{sm.run_count} runs · patch apply rate "
            f"<b>{sm.patch_apply_rate:.0%}</b> · unrelated-file rate "
            f"<b>{sm.unrelated_file_change_rate:.0%}</b> · median latency {median_latency} "
            f"· p95 {p95_latency} · total cost ${sm.total_cost_usd:.4f}</p>"
            "<table><tr><th>run_id</th><th>final state</th><th>applied</th>"
            f"<th>unrelated file</th><th>latency (s)</th><th>cost</th></tr>{run_rows}</table>"
        )

    if report.judge_records:
        judge_rows = "".join(
            f"<tr><td>{j.run_id}</td><td>{j.scores.root_cause_correctness:.2f}</td>"
            f"<td>{j.scores.evidence_sufficiency:.2f}</td><td>{j.scores.patch_relevance:.2f}</td>"
            f"<td>{j.scores.patch_minimality:.2f}</td><td>{j.scores.explanation_faithfulness:.2f}</td>"
            f"<td>{j.scores.rationale}</td></tr>"
            for j in report.judge_records
        )
        sections.append(
            "<h2>LLM judge (grounded, never the sole success signal)</h2>"
            "<table><tr><th>run_id</th><th>root cause</th><th>evidence</th>"
            "<th>relevance</th><th>minimality</th><th>faithfulness</th>"
            f"<th>rationale</th></tr>{judge_rows}</table>"
        )

    body = "\n".join(sections) or "<p>Nothing to report — no labeled set or run_ids given.</p>"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Evaluation report</title><style>"
        "body{font-family:system-ui,sans-serif;margin:2rem;color:#1a1a1a}"
        "table{border-collapse:collapse;margin:0.5rem 0 1.5rem}"
        "th,td{border:1px solid #ccc;padding:0.35rem 0.6rem;text-align:left;font-size:0.9rem}"
        "th{background:#f2f2f2}"
        "</style></head><body>"
        "<h1>Issue-to-Patch: evaluation report</h1>"
        f"{body}</body></html>"
    )
