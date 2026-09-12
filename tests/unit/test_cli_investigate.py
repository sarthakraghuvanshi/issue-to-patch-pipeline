"""`investigate` CLI: the command wiring (snapshot loading, the indexed-check
gate, output, exit codes) around the already-tested graph machinery."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from issue_to_patch.cli import app
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.persistence import Store
from issue_to_patch.processing import index_snapshot

runner = CliRunner()

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
def indexed_snapshot(
    fixture_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    from issue_to_patch.config import get_settings

    monkeypatch.setenv("ITP_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()

    snap = create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/calc")
    store = Store(get_settings().database_url)
    store.create_all()
    index_snapshot(snap, store)
    yield tmp_path / "snap"
    get_settings.cache_clear()


def _patch_fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    llm = FakeLLM()
    llm.queue_structured({"search_queries": ["add returns wrong result"], "focus_areas": []})
    llm.queue_structured(
        {"hypotheses": [{"summary": "subtracts instead of adds", "confidence": 0.9}]}
    )
    llm.queue_structured(_FIX)
    # get_llm is looked up where graph/deps.py imported it, not where it's defined.
    monkeypatch.setattr("issue_to_patch.graph.deps.get_llm", lambda settings=None: llm)
    return llm


def test_investigate_pauses_at_the_human_gate_without_a_decision(
    indexed_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_llm(monkeypatch)
    result = runner.invoke(
        app,
        [
            "investigate",
            "--issue",
            "add() returns the wrong result",
            "--snapshot",
            str(indexed_snapshot),
            "--scope",
            "calculator.py",
        ],
    )
    assert result.exit_code == 10
    assert "AWAITING_HUMAN_REVIEW" in result.output
    assert "root cause:" in result.output


def test_investigate_with_decision_reaches_patch_validated(
    indexed_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_llm(monkeypatch)
    result = runner.invoke(
        app,
        [
            "investigate",
            "--issue",
            "add() returns the wrong result",
            "--snapshot",
            str(indexed_snapshot),
            "--scope",
            "calculator.py",
            "--decision",
            "approve",
        ],
    )
    assert result.exit_code == 0
    assert "state:         PATCH_VALIDATED" in result.output


def test_investigate_resumes_a_previously_started_run_via_resume_flag(
    indexed_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_llm(monkeypatch)
    started = runner.invoke(
        app,
        [
            "investigate",
            "--issue",
            "add() returns the wrong result",
            "--snapshot",
            str(indexed_snapshot),
            "--scope",
            "calculator.py",
        ],
    )
    assert started.exit_code == 10
    run_id = next(
        line.split(":", 1)[1].strip()
        for line in started.output.splitlines()
        if line.startswith("run_id:")
    )

    resumed = runner.invoke(app, ["investigate", "--resume", run_id, "--decision", "approve"])
    assert resumed.exit_code == 0
    assert "state:         PATCH_VALIDATED" in resumed.output


def test_investigate_resume_of_an_unknown_run_id_fails_cleanly(
    indexed_snapshot: Path,
) -> None:
    result = runner.invoke(app, ["investigate", "--resume", "no-such-run"])
    assert result.exit_code == 2
    assert "no such run" in result.output


def test_investigate_requires_issue_and_snapshot_when_not_resuming(
    indexed_snapshot: Path,
) -> None:
    result = runner.invoke(app, ["investigate"])
    assert result.exit_code == 2
    assert "--resume" in result.output


def test_investigate_rejects_an_invalid_decision_value(
    indexed_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_llm(monkeypatch)
    result = runner.invoke(
        app,
        [
            "investigate",
            "--issue",
            "add() returns the wrong result",
            "--snapshot",
            str(indexed_snapshot),
            "--decision",
            "maybe",
        ],
    )
    assert result.exit_code == 2
    assert "approve | reject | revise" in result.output


def test_investigate_rejects_an_invalid_role_value(
    indexed_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_llm(monkeypatch)
    result = runner.invoke(
        app,
        [
            "investigate",
            "--issue",
            "add() returns the wrong result",
            "--snapshot",
            str(indexed_snapshot),
            "--decision",
            "approve",
            "--role",
            "wizard",
        ],
    )
    assert result.exit_code == 2
    assert "gatekeeper | auditor | strategist" in result.output


def test_investigate_refuses_an_unindexed_snapshot(
    fixture_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from issue_to_patch.config import get_settings

    monkeypatch.setenv("ITP_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'empty.db'}")
    get_settings.cache_clear()
    Store(get_settings().database_url).create_all()
    snap = create_snapshot(str(fixture_repo), tmp_path / "snap2", repo_name="acme/calc")

    result = runner.invoke(
        app, ["investigate", "--issue", "x", "--snapshot", str(snap.root_path.parent)]
    )
    get_settings.cache_clear()

    assert result.exit_code == 2
    assert "run `index` first" in result.output
