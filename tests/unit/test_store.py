"""Store: the append-only hash chain over tool calls."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from issue_to_patch.persistence import Store


def _store(tmp_path: Path) -> Store:
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'runs.db'}")
    store.create_all()
    return store


def test_chain_is_valid_after_normal_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_run(
        run_id="r1", issue_ref="acme/x#1", repo="acme/x", commit_sha=None, content_hash="h"
    )
    store.record_tool_call("r1", tool="a", args_redacted="{}", result_hash="1")
    store.record_tool_call("r1", tool="b", args_redacted="{}", result_hash="2")
    store.record_tool_call("r1", tool="c", args_redacted="{}", result_hash="3")
    assert store.verify_chain("r1") is True


def test_tampering_breaks_the_chain(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_run(
        run_id="r1", issue_ref="acme/x#1", repo="acme/x", commit_sha=None, content_hash="h"
    )
    store.record_tool_call("r1", tool="a", args_redacted="{}", result_hash="1")
    store.record_tool_call("r1", tool="b", args_redacted='{"secret":"x"}', result_hash="2")

    with store.session() as session:
        session.execute(
            text("UPDATE tool_calls SET args_redacted='{}' WHERE seq=2 AND run_id='r1'")
        )

    assert store.verify_chain("r1") is False


def test_finish_run_sets_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")
    store.finish_run("r1", state="PATCH_VALIDATED")
    assert store.get_run_state("r1") == "PATCH_VALIDATED"
