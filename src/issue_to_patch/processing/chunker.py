"""Turn a parsed file into retrieval chunks that respect its structure.

Rules:

* a function or small class -> one chunk;
* a class larger than ``max_lines`` -> a ``class_header`` parent chunk plus one
  child chunk per method (``parent_chunk_id`` links them);
* code before the first symbol (imports, module docstring, constants) ->
  a ``module_prelude`` chunk;
* a single function larger than ``max_lines`` is **still emitted whole** — we
  never split a function body mid-statement;
* Markdown -> one chunk per heading section;
* other languages / no symbols -> the whole file as one chunk (or size-sliced on
  blank lines if very large).
"""

from __future__ import annotations

from issue_to_patch.ingestion.models import stable_hash
from issue_to_patch.processing.models import (
    Chunk,
    ChunkKind,
    FileStructure,
    Language,
    SourceSymbol,
    SymbolKind,
)

DEFAULT_MAX_LINES = 200
_HARD_SLICE_LINES = 400  # only ever used for structureless giant text files


class BoundaryDetector:
    """Report the 1-indexed line numbers where a new logical unit begins."""

    def detect(self, structure: FileStructure) -> list[int]:
        if structure.language is Language.MARKDOWN:
            return sorted(s.line_start for s in structure.symbols)
        starts = sorted(s.line_start for s in structure.top_level_symbols())
        return [1, *starts] if starts and starts[0] != 1 else starts or [1]


class StructureAwareChunker:
    def __init__(self, max_lines: int = DEFAULT_MAX_LINES) -> None:
        self.max_lines = max_lines

    def chunk(
        self, structure: FileStructure, content: str, *, repository: str, commit_sha: str
    ) -> list[Chunk]:
        lines = content.split("\n")
        ctx = _Ctx(repository=repository, commit_sha=commit_sha, path=structure.path, lines=lines)

        if structure.language is Language.MARKDOWN and structure.symbols:
            return [self._section_chunk(ctx, s) for s in structure.symbols]

        top = structure.top_level_symbols()
        if not top:
            return self._whole_file(ctx, structure.language)

        chunks: list[Chunk] = []
        first_symbol_line = min(s.line_start for s in top)
        if first_symbol_line > 1:
            chunks.append(self._prelude_chunk(ctx, structure.language, end=first_symbol_line - 1))

        for symbol in sorted(top, key=lambda s: s.line_start):
            chunks.extend(self._symbol_chunks(ctx, structure, symbol))
        return chunks

    # -- per-kind builders ---------------------------------------
    def _symbol_chunks(
        self, ctx: _Ctx, structure: FileStructure, symbol: SourceSymbol
    ) -> list[Chunk]:
        span = symbol.line_end - symbol.line_start + 1
        methods = [s for s in structure.symbols if s.parent == symbol.qualified_name]
        if symbol.kind is SymbolKind.CLASS and span > self.max_lines and methods:
            header_end = min(m.line_start for m in methods) - 1
            parent = self._chunk(
                ctx,
                kind=ChunkKind.CLASS_HEADER,
                symbol=symbol.qualified_name,
                start=symbol.line_start,
                end=max(header_end, symbol.line_start),
                language=structure.language,
            )
            out = [parent]
            for method in sorted(methods, key=lambda s: s.line_start):
                out.append(
                    self._chunk(
                        ctx,
                        kind=ChunkKind.METHOD,
                        symbol=method.qualified_name,
                        start=method.line_start,
                        end=method.line_end,
                        language=structure.language,
                        parent_chunk_id=parent.chunk_id,
                    )
                )
            return out

        # function, small class, or oversized-but-indivisible function: one whole chunk
        return [
            self._chunk(
                ctx,
                kind=ChunkKind.SYMBOL,
                symbol=symbol.qualified_name,
                start=symbol.line_start,
                end=symbol.line_end,
                language=structure.language,
            )
        ]

    def _prelude_chunk(self, ctx: _Ctx, language: Language, *, end: int) -> Chunk:
        return self._chunk(
            ctx, kind=ChunkKind.MODULE_PRELUDE, symbol=None, start=1, end=end, language=language
        )

    def _section_chunk(self, ctx: _Ctx, symbol: SourceSymbol) -> Chunk:
        return self._chunk(
            ctx,
            kind=ChunkKind.SECTION,
            symbol=symbol.name,
            start=symbol.line_start,
            end=symbol.line_end,
            language=Language.MARKDOWN,
        )

    def _whole_file(self, ctx: _Ctx, language: Language) -> list[Chunk]:
        total = len(ctx.lines)
        if total <= _HARD_SLICE_LINES:
            return [
                self._chunk(
                    ctx,
                    kind=ChunkKind.FILE,
                    symbol=None,
                    start=1,
                    end=max(total, 1),
                    language=language,
                )
            ]
        chunks: list[Chunk] = []
        start = 1
        while start <= total:
            end = min(start + _HARD_SLICE_LINES - 1, total)
            chunks.append(
                self._chunk(
                    ctx, kind=ChunkKind.FILE, symbol=None, start=start, end=end, language=language
                )
            )
            start = end + 1
        return chunks

    def _chunk(
        self,
        ctx: _Ctx,
        *,
        kind: ChunkKind,
        symbol: str | None,
        start: int,
        end: int,
        language: Language,
        parent_chunk_id: str | None = None,
    ) -> Chunk:
        body = "\n".join(ctx.lines[start - 1 : end])
        return Chunk(
            chunk_id=Chunk.make_id(ctx.repository, ctx.commit_sha, ctx.path, start, end),
            repository=ctx.repository,
            commit_sha=ctx.commit_sha,
            path=ctx.path,
            language=language,
            kind=kind,
            symbol=symbol,
            line_start=start,
            line_end=end,
            content=body,
            content_hash=stable_hash(body),
            parent_chunk_id=parent_chunk_id,
        )


class _Ctx:
    __slots__ = ("commit_sha", "lines", "path", "repository")

    def __init__(self, *, repository: str, commit_sha: str, path: str, lines: list[str]) -> None:
        self.repository = repository
        self.commit_sha = commit_sha
        self.path = path
        self.lines = lines
