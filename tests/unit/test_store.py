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


def _chunk_row(chunk_id: str, path: str, commit_sha: str = "sha1") -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "repository": "acme/x",
        "commit_sha": commit_sha,
        "path": path,
        "language": "python",
        "kind": "function",
        "symbol": "f",
        "line_start": 1,
        "line_end": 2,
        "content": "def f(): pass",
        "content_hash": "h",
        "parent_chunk_id": None,
        "summary": "",
        "keywords": [],
        "questions": [],
        "reference_paths": [],
    }


def test_indexed_paths_returns_distinct_paths_for_repo_and_sha(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace_chunks(
        "acme/x",
        "sha1",
        [_chunk_row("c1", "a.py"), _chunk_row("c2", "a.py"), _chunk_row("c3", "b.py")],
    )
    # a different commit shouldn't leak into the result
    store.replace_chunks("acme/x", "sha2", [_chunk_row("c4", "other.py", commit_sha="sha2")])

    assert store.indexed_paths("acme/x", "sha1") == {"a.py", "b.py"}
    assert store.indexed_paths("acme/x", "sha2") == {"other.py"}
    assert store.indexed_paths("acme/x", "missing-sha") == set()
