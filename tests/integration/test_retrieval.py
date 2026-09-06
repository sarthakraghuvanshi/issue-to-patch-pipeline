"""RetrievalService + eval harness over a real indexed snapshot."""

from __future__ import annotations

from pathlib import Path

import pytest

from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.persistence import Store
from issue_to_patch.processing import index_snapshot
from issue_to_patch.retrieval import (
    LabeledIssue,
    RetrievalMode,
    RetrievalService,
    SearchFilters,
    evaluate,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def indexed(indexable_repo: Path, tmp_path: Path):
    snap = create_snapshot(str(indexable_repo), tmp_path / "snap", repo_name="acme/sample")
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'r.db'}")
    store.create_all()
    index_snapshot(snap, store)
    return store, snap


def _filters(snap) -> SearchFilters:
    return SearchFilters(repository=snap.repo, commit_sha=snap.commit_sha)


def test_bm25_finds_the_function_named_in_the_query(indexed) -> None:
    store, snap = indexed
    service = RetrievalService(store)
    trace = service.search(
        "parse_issue_url does not strip whitespace",
        _filters(snap),
        mode=RetrievalMode.BM25,
    )
    assert "src/sample/parser.py" in trace.result_paths()
    assert "parse_issue_url" in trace.result_symbols()
    top = trace.results[0]
    assert top.chunk_id in trace.term_contributions  # BM25 explains its top hit


def test_all_three_modes_return_results(indexed) -> None:
    store, snap = indexed
    service = RetrievalService(store)
    for mode in RetrievalMode:
        trace = service.search("parser strips whitespace", _filters(snap), mode=mode)
        assert trace.results, mode


def test_language_filter_excludes_markdown(indexed) -> None:
    store, snap = indexed
    service = RetrievalService(store)
    trace = service.search(
        "usage",
        SearchFilters(repository=snap.repo, commit_sha=snap.commit_sha, language="python"),
    )
    assert all(not r.path.endswith(".md") for r in trace.results)


def test_path_prefix_filter(indexed) -> None:
    store, snap = indexed
    service = RetrievalService(store)
    trace = service.search(
        "parser",
        SearchFilters(repository=snap.repo, commit_sha=snap.commit_sha, path_prefix="src/"),
    )
    assert all(r.path.startswith("src/") for r in trace.results)


def test_search_is_deterministic(indexed) -> None:
    store, snap = indexed
    service = RetrievalService(store)
    a = service.search("parser whitespace", _filters(snap))
    b = service.search("parser whitespace", _filters(snap))
    assert [r.chunk_id for r in a.results] == [r.chunk_id for r in b.results]


def test_eval_harness_reports_metrics_per_mode(indexed) -> None:
    store, snap = indexed
    service = RetrievalService(store)
    issues = [
        LabeledIssue(
            issue_id="i1",
            repository=snap.repo,
            commit_sha=snap.commit_sha,
            query="parse_issue_url should strip whitespace",
            gold_files=["src/sample/parser.py"],
            gold_symbols=["parse_issue_url"],
        )
    ]
    report = evaluate(service, issues, ks=[1, 5])
    modes = {m.mode for m in report.per_mode}
    assert modes == set(RetrievalMode)
    bm25 = next(m for m in report.per_mode if m.mode is RetrievalMode.BM25)
    assert bm25.file_recall_at[5] == 1.0
    assert 0.0 <= bm25.mrr <= 1.0
