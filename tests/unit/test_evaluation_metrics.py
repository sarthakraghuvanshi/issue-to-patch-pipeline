"""evaluation/metrics.py: deterministic metrics read from stored artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from issue_to_patch.evaluation.metrics import compute_run_metrics, compute_suite_metrics
from issue_to_patch.persistence import Store


def _store(tmp_path: Path) -> Store:
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'metrics.db'}")
    store.create_all()
    return store


def _validation_file(tmp_path: Path, name: str, checks: list[dict[str, str]]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"checks": checks, "run_state": "PATCH_VALIDATED"}), "utf-8")
    return path


def test_compute_run_metrics_raises_for_an_unknown_run(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        compute_run_metrics(_store(tmp_path), "no-such-run")


def test_compute_run_metrics_reads_patch_applied_and_scope_from_the_validation_file(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")
    validation_path = _validation_file(
        tmp_path,
        "validation.json",
        [
            {"name": "applies_to_sha", "status": "pass", "detail": ""},
            {"name": "scope", "status": "pass", "detail": ""},
        ],
    )
    store.record_artifact("r1", kind="patch", uri="file:///fix.patch", content_hash="ph")
    store.record_artifact("r1", kind="validation", uri=str(validation_path), content_hash="vh")
    store.finish_run("r1", state="PATCH_VALIDATED", cost_usd=0.05)

    metrics = compute_run_metrics(store, "r1")
    assert metrics.final_state == "PATCH_VALIDATED"
    assert metrics.patch_drafted is True
    assert metrics.patch_applied is True
    assert metrics.unrelated_file_change is False
    assert metrics.cost_usd == 0.05
    assert metrics.latency_seconds is not None and metrics.latency_seconds >= 0.0
    assert metrics.test_pass_rate is None  # not measurable without a sandbox (Sprint 9)


def test_compute_run_metrics_flags_an_unrelated_file_change(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")
    validation_path = _validation_file(
        tmp_path,
        "validation.json",
        [
            {"name": "applies_to_sha", "status": "pass", "detail": ""},
            {"name": "scope", "status": "fail", "detail": "touches unrelated.py"},
        ],
    )
    store.record_artifact("r1", kind="validation", uri=str(validation_path), content_hash="vh")

    metrics = compute_run_metrics(store, "r1")
    assert metrics.unrelated_file_change is True
    assert metrics.patch_drafted is False  # no "patch" artifact recorded


def test_compute_run_metrics_without_a_validation_artifact_is_all_false(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")

    metrics = compute_run_metrics(store, "r1")
    assert metrics.patch_drafted is False
    assert metrics.patch_applied is False
    assert metrics.unrelated_file_change is False
    assert metrics.latency_seconds is None  # run never finished


def test_compute_suite_metrics_on_no_runs_is_all_zero() -> None:
    from issue_to_patch.persistence import Store as _Store

    suite = compute_suite_metrics(_Store("sqlite+pysqlite:///:memory:"), [])
    assert suite.run_count == 0
    assert suite.patch_apply_rate == 0.0
    assert suite.median_latency_seconds is None


def test_compute_suite_metrics_aggregates_across_runs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    good_validation = _validation_file(
        tmp_path, "good.json", [{"name": "applies_to_sha", "status": "pass", "detail": ""}]
    )
    bad_validation = _validation_file(
        tmp_path, "bad.json", [{"name": "applies_to_sha", "status": "fail", "detail": "x"}]
    )
    for run_id, validation_path, cost in [
        ("r1", good_validation, 0.10),
        ("r2", good_validation, 0.20),
        ("r3", bad_validation, 0.30),
    ]:
        store.create_run(run_id=run_id, issue_ref="x", repo=None, commit_sha=None, content_hash="h")
        store.record_artifact(run_id, kind="validation", uri=str(validation_path), content_hash="h")
        store.finish_run(run_id, state="PATCH_VALIDATED", cost_usd=cost)

    suite = compute_suite_metrics(store, ["r1", "r2", "r3"])
    assert suite.run_count == 3
    assert suite.patch_apply_rate == pytest.approx(2 / 3)
    assert suite.median_cost_usd == pytest.approx(0.20)
    assert suite.total_cost_usd == pytest.approx(0.60)
    assert suite.median_latency_seconds is not None
    assert suite.p95_latency_seconds is not None
    assert len(suite.runs) == 3
