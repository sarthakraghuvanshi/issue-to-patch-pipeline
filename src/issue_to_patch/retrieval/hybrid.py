"""Rank fusion for hybrid retrieval."""

from __future__ import annotations

from collections.abc import Sequence

_RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = _RRF_K
) -> list[tuple[str, float]]:
    """Combine several ranked id lists into one. Higher score = better.

    RRF score for a doc = sum over rankings of 1 / (k + rank), rank being
    1-indexed. It needs no score calibration between the input rankers, which is
    why it is the safe default for BM25 + dense.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def weighted_fusion(
    ranked_scores: Sequence[tuple[Sequence[tuple[str, float]], float]],
) -> list[tuple[str, float]]:
    """Weighted sum of min-max-normalised scores from each ranker.

    ``ranked_scores`` is a list of ``(hits, weight)`` where ``hits`` is
    ``[(doc_id, raw_score), ...]``.
    """
    combined: dict[str, float] = {}
    for hits, weight in ranked_scores:
        if not hits:
            continue
        values = [s for _, s in hits]
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        for doc_id, raw in hits:
            combined[doc_id] = combined.get(doc_id, 0.0) + weight * (raw - lo) / span
    return sorted(combined.items(), key=lambda kv: (-kv[1], kv[0]))
