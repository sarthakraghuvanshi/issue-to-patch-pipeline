"""Data shapes for retrieval."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RetrievalMode(StrEnum):
    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"


class SearchFilters(BaseModel):
    repository: str
    commit_sha: str
    language: str | None = None
    path_prefix: str | None = None
    kinds: list[str] = Field(default_factory=list)

    def accepts(self, *, language: str, path: str, kind: str) -> bool:
        if self.language and language != self.language:
            return False
        if self.path_prefix and not path.startswith(self.path_prefix):
            return False
        return not (self.kinds and kind not in self.kinds)


class TermContribution(BaseModel):
    term: str
    tf: int
    idf: float
    contribution: float


class ScoredChunk(BaseModel):
    chunk_id: str
    rank: int
    score: float
    path: str
    symbol: str | None
    line_start: int
    line_end: int
    kind: str
    added_as_parent_context: bool = False


class RetrievalTrace(BaseModel):
    query: str
    mode: RetrievalMode
    expanded_terms: list[str] = Field(default_factory=list)
    filters: SearchFilters
    candidates_considered: int = 0
    results: list[ScoredChunk] = Field(default_factory=list)
    term_contributions: dict[str, list[TermContribution]] = Field(default_factory=dict)

    def result_paths(self) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.results:
            seen.setdefault(r.path, None)
        return list(seen)

    def result_symbols(self) -> list[str]:
        return [r.symbol for r in self.results if r.symbol]
