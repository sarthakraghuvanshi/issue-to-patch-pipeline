"""A small, inspectable Okapi BM25 index.

Every hit carries its per-term contribution so ranking behaviour can be
explained and tested — that is the whole reason to start with BM25 rather than
a black-box embedding search.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from issue_to_patch.retrieval.models import TermContribution

_K1 = 1.5
_B = 0.75


@dataclass
class BM25Hit:
    doc_id: str
    score: float
    contributions: list[TermContribution]


@dataclass
class BM25Index:
    k1: float = _K1
    b: float = _B

    _doc_len: dict[str, int] = field(default_factory=dict)
    _tf: dict[str, dict[str, int]] = field(default_factory=dict)  # doc_id -> term -> count
    _df: dict[str, int] = field(default_factory=dict)  # term -> #docs
    _postings: dict[str, set[str]] = field(default_factory=dict)  # term -> doc_ids
    _avg_len: float = 0.0
    _finalized: bool = False

    def add(self, doc_id: str, tokens: list[str]) -> None:
        if self._finalized:
            raise RuntimeError("index already finalized")
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        self._tf[doc_id] = counts
        self._doc_len[doc_id] = len(tokens)
        for term in counts:
            self._df[term] = self._df.get(term, 0) + 1
            self._postings.setdefault(term, set()).add(doc_id)

    def finalize(self) -> BM25Index:
        n = len(self._doc_len)
        self._avg_len = (sum(self._doc_len.values()) / n) if n else 0.0
        self._finalized = True
        return self

    @property
    def num_docs(self) -> int:
        return len(self._doc_len)

    def idf(self, term: str) -> float:
        n = len(self._doc_len)
        df = self._df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(
        self, query_tokens: list[str], *, top_k: int = 10, allowed: set[str] | None = None
    ) -> list[BM25Hit]:
        if not self._finalized:
            self.finalize()
        candidate_docs: set[str] = set()
        for term in query_tokens:
            candidate_docs |= self._postings.get(term, set())
        if allowed is not None:
            candidate_docs &= allowed

        hits: list[BM25Hit] = []
        for doc_id in candidate_docs:
            contributions = self._score_doc(doc_id, query_tokens)
            score = sum(c.contribution for c in contributions)
            if score > 0:
                hits.append(BM25Hit(doc_id=doc_id, score=score, contributions=contributions))
        hits.sort(key=lambda h: (-h.score, h.doc_id))
        return hits[:top_k]

    def _score_doc(self, doc_id: str, query_tokens: list[str]) -> list[TermContribution]:
        doc_len = self._doc_len[doc_id]
        tf_map = self._tf[doc_id]
        norm = self.k1 * (1 - self.b + self.b * doc_len / (self._avg_len or 1))
        out: list[TermContribution] = []
        for term in dict.fromkeys(query_tokens):
            tf = tf_map.get(term, 0)
            if tf == 0:
                continue
            idf = self.idf(term)
            contribution = idf * (tf * (self.k1 + 1)) / (tf + norm)
            out.append(
                TermContribution(
                    term=term, tf=tf, idf=round(idf, 4), contribution=round(contribution, 4)
                )
            )
        out.sort(key=lambda c: -c.contribution)
        return out
