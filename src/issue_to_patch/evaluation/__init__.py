"""Evaluation: deterministic metrics, grounded LLM judge, traces, cost accounting."""

from issue_to_patch.evaluation.judge import judge_run
from issue_to_patch.evaluation.metrics import compute_run_metrics, compute_suite_metrics
from issue_to_patch.evaluation.models import (
    JudgeRecord,
    JudgeScores,
    RunMetrics,
    SuiteMetrics,
    SuiteReport,
)
from issue_to_patch.evaluation.run_suite import build_suite_report, render_suite_report_html

__all__ = [
    "JudgeRecord",
    "JudgeScores",
    "RunMetrics",
    "SuiteMetrics",
    "SuiteReport",
    "build_suite_report",
    "compute_run_metrics",
    "compute_suite_metrics",
    "judge_run",
    "render_suite_report_html",
]
