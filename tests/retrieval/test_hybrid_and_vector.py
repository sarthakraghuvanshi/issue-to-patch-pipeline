"""Rank fusion + the deterministic hashing embedder."""

from __future__ import annotations

from issue_to_patch.persistence.vector import HashingEmbedder, brute_force_search, cosine
from issue_to_patch.retrieval.expand import expand_query
from issue_to_patch.retrieval.hybrid import reciprocal_rank_fusion, weighted_fusion


def test_rrf_rewards_agreement_between_rankers() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]])
    ids = [doc_id for doc_id, _ in fused]
    assert ids[:2] == ["a", "b"]  # both rank a and b highly
    assert set(ids) == {"a", "b", "c", "d"}


def test_weighted_fusion_respects_weights() -> None:
    strong = [("x", 10.0), ("y", 0.0)]
    weak = [("y", 10.0), ("x", 0.0)]
    fused = weighted_fusion([(strong, 0.9), (weak, 0.1)])
    assert fused[0][0] == "x"


def test_hashing_embedder_is_deterministic_and_unit_length() -> None:
    e = HashingEmbedder(dim=64)
    v1 = e.embed("parse the issue url")
    v2 = e.embed("parse the issue url")
    assert v1 == v2
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-9


def test_similar_text_scores_higher_than_unrelated() -> None:
    e = HashingEmbedder()
    q = e.embed("parser strips whitespace from issue urls")
    near = e.embed("the url parser removes surrounding whitespace")
    far = e.embed("database migration rollback strategy")
    assert cosine(q, near) > cosine(q, far)


def test_brute_force_search_filters_and_ranks() -> None:
    e = HashingEmbedder()
    corpus = {
        "a": e.embed("parser issue url whitespace"),
        "b": e.embed("http retry rate limit"),
        "c": e.embed("parser issue url strip"),
    }
    hits = brute_force_search(e.embed("issue url parser"), corpus, top_k=2)
    assert {doc_id for doc_id, _ in hits} == {"a", "c"}
    assert brute_force_search(e.embed("x"), corpus, allowed={"b"}, top_k=5)[0][0] == "b"


def test_expand_query_pulls_terms_from_labels_and_stacktrace() -> None:
    text = 'crash: File "src/pkg/mod.py", line 3, in do_thing'
    terms = expand_query(text, labels=["needs-triage"])
    assert "src/pkg/mod.py" in terms
    assert "mod" in terms
    assert {"do", "thing"} <= set(terms)
    assert "triage" in terms
