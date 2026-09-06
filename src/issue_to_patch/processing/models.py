"""Data shapes for parsing, structure analysis, and chunking."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from issue_to_patch.ingestion.models import stable_hash


class Language(StrEnum):
    PYTHON = "python"
    MARKDOWN = "markdown"
    JSON = "json"
    YAML = "yaml"
    TEXT = "text"
    UNKNOWN = "unknown"


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    SECTION = "section"  # a markdown heading section


class ChunkKind(StrEnum):
    MODULE_PRELUDE = "module_prelude"  # imports / module docstring / top-level constants
    SYMBOL = "symbol"  # a whole function or class
    METHOD = "method"  # one method of a split class
    CLASS_HEADER = "class_header"  # a large class's signature + docstring (parent of methods)
    SECTION = "section"  # a markdown section
    FILE = "file"  # the whole file (small configs, plain text)


class SourceSymbol(BaseModel):
    kind: SymbolKind
    name: str
    qualified_name: str  # e.g. "Foo.bar"
    line_start: int  # 1-indexed, inclusive
    line_end: int  # 1-indexed, inclusive
    parent: str | None = None  # qualified name of the enclosing class, if any
    decorators: list[str] = Field(default_factory=list)
    is_test: bool = False


class FileStructure(BaseModel):
    path: str
    language: Language
    line_count: int
    imports: list[str] = Field(default_factory=list)
    symbols: list[SourceSymbol] = Field(default_factory=list)
    is_test_file: bool = False
    parse_ok: bool = True
    parse_error: str = ""

    def top_level_symbols(self) -> list[SourceSymbol]:
        return [s for s in self.symbols if s.parent is None and s.kind is not SymbolKind.MODULE]


class Chunk(BaseModel):
    chunk_id: str
    repository: str
    commit_sha: str
    path: str
    language: Language
    kind: ChunkKind
    symbol: str | None = None
    line_start: int
    line_end: int
    content: str
    content_hash: str
    parent_chunk_id: str | None = None
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)

    @staticmethod
    def make_id(repository: str, commit_sha: str, path: str, line_start: int, line_end: int) -> str:
        return stable_hash(repository, commit_sha, path, str(line_start), str(line_end))[:24]


class IndexResult(BaseModel):
    repository: str
    commit_sha: str
    files_seen: int
    files_indexed: int
    files_skipped: int
    chunks_written: int
