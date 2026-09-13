"""`eval-suite` and `judge` CLI commands."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from issue_to_patch.cli import app
from issue_to_patch.config import get_settings
from issue_to_patch.persistence import Store

runner = CliRunner()


@pytest.fixture
def store_with_a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    monkeypatch.setenv("ITP_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'eval.db'}")
    get_settings.cache_clear()
    store = Store(get_settings().database_url)
    store.create_all()
    store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")
    store.finish_run("r1", state="PATCH_VALIDATED", cost_usd=0.02)
    yield store
    get_settings.cache_clear()


def test_eval_suite_requires_labeled_or_run_id(store_with_a_run: Store) -> None:
    result = runner.invoke(app, ["eval-suite"])
    assert result.exit_code == 2
    assert "--labeled" in result.output


def test_eval_suite_with_run_id_writes_json_and_html(
    store_with_a_run: Store, tmp_path: Path
) -> None:
    out_json = tmp_path / "report.json"
    out_html = tmp_path / "report.html"
    result = runner.invoke(
        app,
        [
            "eval-suite",
            "--run-id",
            "r1",
            "--out-json",
            str(out_json),
            "--out-html",
            str(out_html),
        ],
    )
    assert result.exit_code == 0
    assert out_json.exists()
    assert out_html.exists()
    payload = json.loads(out_json.read_text("utf-8"))
    assert payload["suite_metrics"]["run_count"] == 1
    assert "Run metrics" in out_html.read_text("utf-8")
    assert "runs=1" in result.output


def test_eval_suite_warns_when_judging_with_the_fake_provider(
    store_with_a_run: Store, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "eval-suite",
            "--run-id",
            "r1",
            "--judge",
            "--out-json",
            str(tmp_path / "report.json"),
            "--out-html",
            str(tmp_path / "report.html"),
        ],
    )
    assert "plumbing-only" in result.output
    # FakeLLM has no queued reply for JudgeScores -> the judge call itself
    # fails loudly rather than fabricating a score.
    assert result.exit_code != 0


def test_judge_reports_an_unknown_run_id(store_with_a_run: Store) -> None:
    result = runner.invoke(app, ["judge", "no-such-run"])
    assert result.exit_code == 2
    assert "no such run" in result.output
