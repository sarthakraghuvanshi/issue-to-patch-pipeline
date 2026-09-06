"""Dense-vector support.

Two swappable embedders:

* :class:`HashingEmbedder` — deterministic, offline, zero-cost. A hashed
  bag-of-tokens projected to a fixed dimension and L2-normalised. Weak, but real
  enough to exercise the hybrid path and the eval harness with no provider.
* a provider-backed embedder is added in Sprint 5 behind ``llm/client.py``; it
  satisfies the same :class:`Embedder` protocol.

Similarity search here is brute-force cosine in Python. In staging/prod this is
replaced by a ``pgvector`` ``<->`` query on the same table — the interface
(``search(vector, allowed, top_k)``) does not change.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

DEFAULT_DIM = 256

# A plain tokeniser — the code-aware one lives in retrieval/ and must not be
# imported here (persistence is a lower layer). Splits words and camelCase.
_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _embed_tokens(text: str) -> list[str]:
    out: list[str] = []
    for raw in _WORD.findall(text or ""):
        lowered = raw.lower()
        out.append(lowered)
        for piece in _CAMEL.split(raw):
            if piece and piece.lower() != lowered:
                out.append(piece.lower())
    return out


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic hashed-bag-of-tokens embedding."""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _embed_tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        return _l2_normalize(vec)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return max(-1.0, min(1.0, dot))  # both sides are unit vectors


def brute_force_search(
    query_vec: list[float],
    corpus: dict[str, list[float]],
    *,
    allowed: set[str] | None = None,
    top_k: int = 10,
) -> list[tuple[str, float]]:
    scored: list[tuple[str, float]] = []
    for doc_id, vec in corpus.items():
        if allowed is not None and doc_id not in allowed:
            continue
        scored.append((doc_id, cosine(query_vec, vec)))
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return scored[:top_k]


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]
