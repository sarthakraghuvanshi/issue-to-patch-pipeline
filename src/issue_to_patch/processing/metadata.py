"""Retrieval metadata for a chunk: summary, keywords, questions, references.

All rule-based and deterministic — Principle 1. An LLM variant can be added
later behind a flag; the deterministic version is the baseline to measure it
against.
"""

from __future__ import annotations

import keyword
import re

from issue_to_patch.processing.models import Chunk, ChunkKind, FileStructure

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_STOPWORDS = {
    "self",
    "cls",
    "return",
    "import",
    "from",
    "none",
    "true",
    "false",
    "def",
    "class",
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    *keyword.kwlist,
}
_MIN_KEYWORD_LEN = 3
_MAX_KEYWORDS = 15


def split_identifier(identifier: str) -> list[str]:
    """``parseIssueURL`` / ``parse_issue_url`` -> ['parse', 'issue', 'url']."""
    pieces: list[str] = []
    for part in identifier.split("_"):
        pieces.extend(p for p in _CAMEL_BOUNDARY.split(part) if p)
    return [p.lower() for p in pieces if p]


class KeywordExtractor:
    def extract(self, chunk: Chunk) -> list[str]:
        counts: dict[str, int] = {}
        for token in _IDENTIFIER.findall(chunk.content):
            for piece in split_identifier(token):
                if len(piece) >= _MIN_KEYWORD_LEN and piece not in _STOPWORDS:
                    counts[piece] = counts.get(piece, 0) + 1
        if chunk.symbol:
            for piece in split_identifier(chunk.symbol.replace(".", "_")):
                counts[piece] = counts.get(piece, 0) + 5  # weight the symbol name
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [word for word, _ in ranked[:_MAX_KEYWORDS]]


class SummaryGenerator:
    def summarize(self, chunk: Chunk, structure: FileStructure) -> str:
        loc = f"{chunk.path}:{chunk.line_start}-{chunk.line_end}"
        if chunk.kind is ChunkKind.MODULE_PRELUDE:
            imports = ", ".join(structure.imports[:8]) or "none"
            return f"Module prelude of {chunk.path} (imports: {imports})."
        if chunk.kind is ChunkKind.SECTION:
            return f"Documentation section '{chunk.symbol}' ({loc})."
        if chunk.kind is ChunkKind.FILE:
            return f"Full contents of {chunk.path} ({chunk.language}, {loc})."

        symbol_meta = next((s for s in structure.symbols if s.qualified_name == chunk.symbol), None)
        kind_word = symbol_meta.kind.value if symbol_meta else "symbol"
        decorators = (
            f" decorated with {', '.join(symbol_meta.decorators)}"
            if symbol_meta and symbol_meta.decorators
            else ""
        )
        test_note = " (a test)" if symbol_meta and symbol_meta.is_test else ""
        return f"{kind_word.capitalize()} `{chunk.symbol}`{decorators}{test_note} at {loc}."


class QuestionGenerator:
    def generate(self, chunk: Chunk) -> list[str]:
        if chunk.symbol and chunk.kind in {ChunkKind.SYMBOL, ChunkKind.METHOD}:
            name = chunk.symbol
            return [
                f"What does {name} do?",
                f"How is {name} used?",
                f"Where is {name} defined?",
                f"What calls {name}?",
            ]
        if chunk.kind is ChunkKind.SECTION:
            return [f"What does the '{chunk.symbol}' section explain?"]
        if chunk.kind is ChunkKind.MODULE_PRELUDE:
            return [
                f"What does {chunk.path} import?",
                f"What are the module-level names in {chunk.path}?",
            ]
        return [f"What is in {chunk.path}?"]


class MetadataEnricher:
    """Runs the three generators + fills ``references`` (tests that name the symbol)."""

    def __init__(self) -> None:
        self.keywords = KeywordExtractor()
        self.summaries = SummaryGenerator()
        self.questions = QuestionGenerator()

    def enrich(
        self, chunk: Chunk, structure: FileStructure, *, test_index: dict[str, list[str]]
    ) -> Chunk:
        references: list[str] = []
        if chunk.symbol:
            bare = chunk.symbol.split(".")[-1]
            references = sorted(set(test_index.get(bare, [])))
        return chunk.model_copy(
            update={
                "keywords": self.keywords.extract(chunk),
                "summary": self.summaries.summarize(chunk, structure),
                "questions": self.questions.generate(chunk),
                "references": references,
            }
        )
