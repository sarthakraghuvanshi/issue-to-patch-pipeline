"""The retrieval service: one ``search()`` over a repo@sha, three modes.

    bm25    lexical only (inspectable, the baseline)
    dense   cosine over embeddings only
    hybrid  reciprocal-rank fusion of the two

Adds: metadata filters, deterministic query expansion, and parent-context
expansion (pull in a split class's header when one of its methods is retrieved).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from issue_to_patch.logging import get_logger
from issue_to_patch.persistence import ChunkRow, HashingEmbedder, Store, brute_force_search
from issue_to_patch.persistence.vector import Embedder
from issue_to_patch.retrieval.bm25 import BM25Hit, BM25Index
from issue_to_patch.retrieval.expand import expand_query
from issue_to_patch.retrieval.hybrid import reciprocal_rank_fusion
from issue_to_patch.retrieval.models import (
    RetrievalMode,
    RetrievalTrace,
    ScoredChunk,
    SearchFilters,
)
from issue_to_patch.retrieval.tokenize import tokenize

_log = get_logger("retrieval")
_FETCH_MULTIPLIER = 5  # over-fetch before filtering / fusion / truncation


@dataclass
class _RepoIndex:
    chunks: dict[str, ChunkRow]
    bm25: BM25Index
    vectors: dict[str, list[float]]
    doc_tokens: dict[str, list[str]] = field(default_factory=dict)


class RetrievalService:
    def __init__(self, store: Store, embedder: Embedder | None = None) -> None:
        self._store = store
        self._embedder = embedder or HashingEmbedder()
        self._cache: dict[tuple[str, str], _RepoIndex] = {}

    # -- indexing --------------------------------------------------
    def ensure_embeddings(self, repository: str, commit_sha: str) -> int:
        rows = self._store.list_chunks(repository, commit_sha)
        missing = {r.chunk_id: self._embed_row(r) for r in rows if not r.embedding}
        if missing:
            self._store.set_embeddings(missing)
            self._cache.pop((repository, commit_sha), None)
        return len(missing)

    def _load(self, repository: str, commit_sha: str) -> _RepoIndex:
        key = (repository, commit_sha)
        if key in self._cache:
            return self._cache[key]
        rows = self._store.list_chunks(repository, commit_sha)
        if not rows:
            raise LookupError(f"no chunks indexed for {repository}@{commit_sha[:12]}")

        bm25 = BM25Index()
        vectors: dict[str, list[float]] = {}
        chunks: dict[str, ChunkRow] = {}
        doc_tokens: dict[str, list[str]] = {}
        for row in rows:
            chunks[row.chunk_id] = row
            tokens = _document_tokens(row)
            doc_tokens[row.chunk_id] = tokens
            bm25.add(row.chunk_id, tokens)
            vectors[row.chunk_id] = row.embedding or self._embed_row(row)
        bm25.finalize()
        index = _RepoIndex(chunks=chunks, bm25=bm25, vectors=vectors, doc_tokens=doc_tokens)
        self._cache[key] = index
        return index

    def _embed_row(self, row: ChunkRow) -> list[float]:
        return self._embedder.embed("\n".join(_document_tokens(row)))

    # -- search ---------------------------------------------------
    def search(
        self,
        query: str,
        filters: SearchFilters,
        *,
        top_k: int = 10,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        expand: bool = True,
        parent_context: bool = True,
        labels: list[str] | None = None,
    ) -> RetrievalTrace:
        index = self._load(filters.repository, filters.commit_sha)
        allowed = {
            cid
            for cid, row in index.chunks.items()
            if filters.accepts(language=row.language, path=row.path, kind=row.kind)
        }
        terms = expand_query(query, labels=labels) if expand else tokenize(query)
        fetch_k = top_k * _FETCH_MULTIPLIER

        bm25_hits = index.bm25.search(terms, top_k=fetch_k, allowed=allowed)
        bm25_order = [h.doc_id for h in bm25_hits]
        contributions = {h.doc_id: h.contributions for h in bm25_hits}

        query_vec = self._embedder.embed(query)
        dense_hits = brute_force_search(query_vec, index.vectors, allowed=allowed, top_k=fetch_k)
        dense_order = [doc_id for doc_id, _ in dense_hits]

        ordered = self._fuse(mode, bm25_hits, bm25_order, dense_hits, dense_order)[:top_k]

        results: list[ScoredChunk] = []
        for rank, (doc_id, score) in enumerate(ordered, start=1):
            row = index.chunks[doc_id]
            results.append(
                ScoredChunk(
                    chunk_id=doc_id,
                    rank=rank,
                    score=round(score, 4),
                    path=row.path,
                    symbol=row.symbol,
                    line_start=row.line_start,
                    line_end=row.line_end,
                    kind=row.kind,
                )
            )
        if parent_context:
            results = self._add_parent_context(index, results, allowed)

        trace = RetrievalTrace(
            query=query,
            mode=mode,
            expanded_terms=terms,
            filters=filters,
            candidates_considered=len(allowed),
            results=results,
            term_contributions={
                cid: contributions.get(cid, [])
                for cid in [r.chunk_id for r in results]
                if contributions.get(cid)
            },
        )
        _log.info(
            "retrieval.search",
            mode=mode.value,
            terms=len(terms),
            candidates=len(allowed),
            results=len(results),
        )
        return trace

    def _fuse(
        self,
        mode: RetrievalMode,
        bm25_hits: list[BM25Hit],
        bm25_order: list[str],
        dense_hits: list[tuple[str, float]],
        dense_order: list[str],
    ) -> list[tuple[str, float]]:
        if mode is RetrievalMode.BM25:
            return [(h.doc_id, h.score) for h in bm25_hits]
        if mode is RetrievalMode.DENSE:
            return list(dense_hits)
        return reciprocal_rank_fusion([bm25_order, dense_order])

    def _add_parent_context(
        self, index: _RepoIndex, results: list[ScoredChunk], allowed: set[str]
    ) -> list[ScoredChunk]:
        present = {r.chunk_id for r in results}
        extra: list[ScoredChunk] = []
        for result in results:
            parent_id = index.chunks[result.chunk_id].parent_chunk_id
            if parent_id and parent_id not in present and parent_id in allowed:
                parent = index.chunks.get(parent_id)
                if parent is None:
                    continue
                present.add(parent_id)
                extra.append(
                    ScoredChunk(
                        chunk_id=parent_id,
                        rank=result.rank,
                        score=result.score,
                        path=parent.path,
                        symbol=parent.symbol,
                        line_start=parent.line_start,
                        line_end=parent.line_end,
                        kind=parent.kind,
                        added_as_parent_context=True,
                    )
                )
        return results + extra


def _document_tokens(row: ChunkRow) -> list[str]:
    """Text used to index a chunk. Path and symbol are repeated to boost exact matches."""
    parts = [
        row.path,
        row.path,
        (row.symbol or "") + " " + (row.symbol or "") + " " + (row.symbol or ""),
        row.summary,
        " ".join(row.keywords),
        " ".join(row.questions),
        row.content,
    ]
    return tokenize("\n".join(p for p in parts if p))
