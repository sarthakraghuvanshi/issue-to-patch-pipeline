"""BM25Index: ranking behaviour and inspectable term contributions."""

from __future__ import annotations

from issue_to_patch.retrieval.bm25 import BM25Index

CORPUS = {
    "d1": ["the", "parser", "reads", "issue", "urls", "and", "strips", "whitespace"],
    "d2": ["the", "http", "client", "retries", "on", "rate", "limit", "errors"],
    "d3": ["parser", "parser", "parser", "tests", "for", "the", "issue", "parser", "module"],
    "d4": ["unrelated", "document", "about", "database", "migrations"],
}


def _index() -> BM25Index:
    idx = BM25Index()
    for doc_id, tokens in CORPUS.items():
        idx.add(doc_id, tokens)
    return idx.finalize()


def test_ranks_the_most_on_topic_document_first() -> None:
    hits = _index().search(["parser", "issue"], top_k=3)
    assert hits[0].doc_id == "d3"
    assert {h.doc_id for h in hits} == {"d1", "d3"}


def test_rare_terms_get_higher_idf_than_common_ones() -> None:
    idx = _index()
    assert idx.idf("migrations") > idx.idf("parser")
    assert idx.idf("the") < idx.idf("parser")  # "the" is in every doc


def test_hit_carries_per_term_contributions_that_sum_to_score() -> None:
    hit = _index().search(["parser", "issue"], top_k=1)[0]
    assert {c.term for c in hit.contributions} <= {"parser", "issue"}
    assert abs(sum(c.contribution for c in hit.contributions) - hit.score) < 1e-6


def test_filter_restricts_candidates() -> None:
    hits = _index().search(["parser"], top_k=5, allowed={"d1"})
    assert [h.doc_id for h in hits] == ["d1"]


def test_no_match_returns_nothing() -> None:
    assert _index().search(["kubernetes"], top_k=5) == []


def test_ranking_is_deterministic() -> None:
    a = [h.doc_id for h in _index().search(["parser", "the"], top_k=4)]
    b = [h.doc_id for h in _index().search(["parser", "the"], top_k=4)]
    assert a == b
