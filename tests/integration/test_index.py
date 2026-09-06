"""index_snapshot: chunk a real snapshot, deterministically, traceable to source."""

from __future__ import annotations

from pathlib import Path

import pytest

from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.persistence import Store
from issue_to_patch.processing import index_snapshot

pytestmark = pytest.mark.integration


def _snapshot(repo: Path, dest: Path):
    return create_snapshot(str(repo), dest, repo_name="acme/sample")


def _store(tmp_path: Path) -> Store:
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'idx.db'}")
    store.create_all()
    return store


def test_indexes_supported_files_and_writes_chunks(indexable_repo: Path, tmp_path: Path) -> None:
    snap = _snapshot(indexable_repo, tmp_path / "snap")
    store = _store(tmp_path)

    result = index_snapshot(snap, store, out_dir=tmp_path)

    assert result.repository == "acme/sample"
    assert result.chunks_written > 0
    assert store.count_chunks("acme/sample", snap.commit_sha) == result.chunks_written
    assert (tmp_path / "chunks.jsonl").exists()

    # a function chunk exists and points at the right symbol + file
    with store.session() as session:
        from issue_to_patch.persistence import ChunkRow

        rows = session.query(ChunkRow).all()
    by_symbol = {r.symbol: r for r in rows}
    assert "parse_issue_url" in by_symbol
    assert by_symbol["parse_issue_url"].path == "src/sample/parser.py"


def test_chunk_content_matches_the_exact_file_lines(indexable_repo: Path, tmp_path: Path) -> None:
    snap = _snapshot(indexable_repo, tmp_path / "snap")
    store = _store(tmp_path)
    index_snapshot(snap, store)

    with store.session() as session:
        from issue_to_patch.persistence import ChunkRow

        rows = session.query(ChunkRow).all()
    for row in rows:
        file_lines = (snap.root_path / row.path).read_text("utf-8").split("\n")
        expected = "\n".join(file_lines[row.line_start - 1 : row.line_end])
        assert row.content == expected, f"{row.path}:{row.line_start}-{row.line_end}"


def test_reindexing_the_same_commit_is_idempotent(indexable_repo: Path, tmp_path: Path) -> None:
    snap = _snapshot(indexable_repo, tmp_path / "snap")
    store = _store(tmp_path)

    first = index_snapshot(snap, store)
    second = index_snapshot(snap, store)

    assert first.chunks_written == second.chunks_written
    assert store.count_chunks(snap.repo or "", snap.commit_sha) == first.chunks_written

    with store.session() as session:
        from issue_to_patch.persistence import ChunkRow

        ids_a = sorted(r.chunk_id for r in session.query(ChunkRow).all())
    second_again = index_snapshot(snap, store)
    with store.session() as session:
        from issue_to_patch.persistence import ChunkRow

        ids_b = sorted(r.chunk_id for r in session.query(ChunkRow).all())
    assert ids_a == ids_b
    assert second_again.chunks_written == first.chunks_written


def test_test_files_are_recorded_as_references(indexable_repo: Path, tmp_path: Path) -> None:
    snap = _snapshot(indexable_repo, tmp_path / "snap")
    store = _store(tmp_path)
    index_snapshot(snap, store)

    row = None
    with store.session() as session:
        from issue_to_patch.persistence import ChunkRow

        row = session.query(ChunkRow).filter(ChunkRow.symbol == "parse_issue_url").one()
    assert "tests/test_parser.py" in row.reference_paths
