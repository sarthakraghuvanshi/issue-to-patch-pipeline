"""`audit` CLI: prints a run's trail and exits non-zero on trouble."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from issue_to_patch.cli import app
from issue_to_patch.config import get_settings
from issue_to_patch.persistence import Store

runner = CliRunner()


@pytest.fixture
def store_with_a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'audit.db'}")
    get_settings.cache_clear()
    store = Store(get_settings().database_url)
    store.create_all()
    store.create_run(
        run_id="r1", issue_ref="acme/x#1", repo="acme/x", commit_sha=None, content_hash="h"
    )
    store.record_tool_call("r1", tool="search", args_redacted="{}", result_hash="1")
    store.record_human_decision(
        "r1", role="gatekeeper", reviewer="alice", decision="approve", reason=""
    )
    store.finish_run("r1", state="PATCH_VALIDATED")
    yield store
    get_settings.cache_clear()


def test_audit_prints_the_trail_and_exits_zero_when_intact(store_with_a_run: Store) -> None:
    result = runner.invoke(app, ["audit", "r1"])
    assert result.exit_code == 0
    assert "run_id:  r1" in result.output
    assert "tool_calls=OK" in result.output
    assert "decisions=OK" in result.output
    assert "alice" in result.output


def test_audit_exits_1_for_an_unknown_run(store_with_a_run: Store) -> None:
    result = runner.invoke(app, ["audit", "does-not-exist"])
    assert result.exit_code == 1
    assert "no such run" in result.output


def test_audit_exits_3_when_a_chain_is_broken(store_with_a_run: Store) -> None:
    from sqlalchemy import text

    with store_with_a_run.session() as session:
        session.execute(text("UPDATE tool_calls SET result_hash='tampered' WHERE run_id='r1'"))

    result = runner.invoke(app, ["audit", "r1"])
    assert result.exit_code == 3
    assert "tool_calls=BROKEN" in result.output
