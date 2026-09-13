"""Deterministic metrics computed from stored artifacts (Phase 9).

Nothing here runs anything, calls an LLM, or touches the network — it only
reads rows a run already wrote to the ``Store`` and the patch/validation
files ``PersistRun`` already left on disk (see ``graph/nodes.py``). Run the
same run_ids through this twice and you get the same numbers back.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable
from pathlib import Path

from issue_to_patch.evaluation.models import RunMetrics, SuiteMetrics
from issue_to_patch.persistence import Store


def compute_run_metrics(store: Store, run_id: str) -> RunMetrics:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)

    artifacts = {a.kind: a for a in store.list_artifacts(run_id)}
    patch_applied = False
    unrelated_file_change = False
    validation_artifact = artifacts.get("validation")
    if validation_artifact is not None:
        report = json.loads(Path(validation_artifact.uri).read_text("utf-8"))
        checks = {c["name"]: c["status"] for c in report.get("checks", [])}
        patch_applied = checks.get("applies_to_sha") == "pass"
        unrelated_file_change = checks.get("scope") in {"warn", "fail"}

    latency = None
    if run.finished_at is not None:
        latency = (run.finished_at - run.created_at).total_seconds()

    return RunMetrics(
        run_id=run_id,
        final_state=run.state,
        patch_drafted="patch" in artifacts,
        patch_applied=patch_applied,
        unrelated_file_change=unrelated_file_change,
        latency_seconds=latency,
        cost_usd=run.cost_usd,
    )


def compute_suite_metrics(store: Store, run_ids: list[str]) -> SuiteMetrics:
    runs = [compute_run_metrics(store, run_id) for run_id in run_ids]
    if not runs:
        return SuiteMetrics(
            run_count=0,
            patch_apply_rate=0.0,
            unrelated_file_change_rate=0.0,
            median_latency_seconds=None,
            p95_latency_seconds=None,
            median_cost_usd=0.0,
            total_cost_usd=0.0,
            runs=[],
        )
    latencies = [r.latency_seconds for r in runs if r.latency_seconds is not None]
    costs = [r.cost_usd for r in runs]
    return SuiteMetrics(
        run_count=len(runs),
        patch_apply_rate=_rate(r.patch_applied for r in runs),
        unrelated_file_change_rate=_rate(r.unrelated_file_change for r in runs),
        median_latency_seconds=statistics.median(latencies) if latencies else None,
        p95_latency_seconds=_percentile(latencies, 0.95) if latencies else None,
        median_cost_usd=statistics.median(costs),
        total_cost_usd=sum(costs),
        runs=runs,
    )


def _rate(flags: Iterable[bool]) -> float:
    values = list(flags)
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolation percentile — no numpy dependency for one function."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)
