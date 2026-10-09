"""Dense-vector support.

Two swappable embedders:

* :class:`HashingEmbedder` — deterministic, offline, zero-cost. A hashed
  bag-of-tokens projected to a fixed dimension and L2-normalised. Weak, but real
  enough to exercise the hybrid path and the eval harness with no provider.
* :class:`OpenAIEmbedder` — a real provider-backed embedder, same
  :class:`Embedder` protocol. Selected by ``llm/client.py``'s
  ``get_embedder()`` whenever an OpenAI key is configured.

Similarity search here is brute-force cosine in Python. In staging/prod this is
replaced by a ``pgvector`` ``<->`` query on the same table — the interface
(``search(vector, allowed, top_k)``) does not change.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import tiktoken
from openai import OpenAI

DEFAULT_DIM = 256
# text-embedding-3-small's native output dimension.
_OPENAI_EMBEDDING_DIM = 1536
# OpenAI's embeddings endpoint accepts a list input natively; batching keeps
# a large repo's indexing to a handful of requests instead of one per chunk,
# without risking a single oversized request.
_OPENAI_BATCH_SIZE = 100
_OPENAI_INPUT_TOKENS = 8000
_OPENAI_BATCH_TOKENS = 250_000

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
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


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


@dataclass
class OpenAIEmbedder:
    """A real, provider-backed embedding — satisfies the same :class:`Embedder`
    protocol as :class:`HashingEmbedder`, selected instead of it by
    ``llm/client.py``'s ``get_embedder()`` whenever an OpenAI key is configured.
    """

    api_key: str
    model: str = "text-embedding-3-small"
    client: OpenAI | None = None  # injectable, so tests never touch the network
    dim: int = field(init=False, default=_OPENAI_EMBEDDING_DIM)

    _client: OpenAI = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.dim = 3072 if self.model == "text-embedding-3-large" else _OPENAI_EMBEDDING_DIM
        self._client = self.client or OpenAI(api_key=self.api_key)

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            encoding = tiktoken.encoding_for_model(self.model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        # Keep full code excerpts in storage. Only the embedding transport is
        # split; pool segments back into one vector per original chunk.
        vectors: list[list[float]] = [[] for _ in texts]
        segments = [0] * len(texts)
        weights = [0] * len(texts)
        batch: list[list[int]] = []
        owners: list[int] = []
        batch_tokens = 0

        def flush() -> None:
            response = self._client.embeddings.create(model=self.model, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            if len(ordered) != len(batch):
                raise ValueError("Embedding provider returned an incomplete batch.")
            for owner, tokens, item in zip(owners, batch, ordered, strict=True):
                weight = len(tokens)
                if not vectors[owner]:
                    vectors[owner] = [value * weight for value in item.embedding]
                else:
                    vectors[owner] = [
                        old + value * weight
                        for old, value in zip(vectors[owner], item.embedding, strict=True)
                    ]
                weights[owner] += weight
                segments[owner] += 1
            batch.clear()
            owners.clear()

        for owner, text in enumerate(texts):
            tokens = encoding.encode(text, disallowed_special=()) or encoding.encode(" ")
            for offset in range(0, len(tokens), _OPENAI_INPUT_TOKENS):
                part = tokens[offset : offset + _OPENAI_INPUT_TOKENS]
                if batch and (
                    len(batch) >= _OPENAI_BATCH_SIZE
                    or batch_tokens + len(part) > _OPENAI_BATCH_TOKENS
                ):
                    flush()
                    batch_tokens = 0
                batch.append(part)
                owners.append(owner)
                batch_tokens += len(part)
        if batch:
            flush()
        return [
            _l2_normalize(vector) if count > 1 else [value / weight for value in vector]
            for vector, count, weight in zip(vectors, segments, weights, strict=True)
        ]


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
