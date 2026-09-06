"""StructureAwareChunker: boundaries, prelude, class splitting, no mid-body splits."""

from __future__ import annotations

from issue_to_patch.processing import (
    BoundaryDetector,
    Language,
    StructureAnalyzer,
    StructureAwareChunker,
)
from issue_to_patch.processing.models import ChunkKind


def _chunks(content: str, *, max_lines: int = 200, path: str = "m.py"):
    structure = StructureAnalyzer().analyze(path, content, Language.PYTHON)
    return StructureAwareChunker(max_lines=max_lines).chunk(
        structure, content, repository="o/r", commit_sha="a" * 40
    )


def test_prelude_then_one_chunk_per_symbol() -> None:
    src = "import os\n\n\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n"
    chunks = _chunks(src)
    kinds = [c.kind for c in chunks]
    assert kinds == [ChunkKind.MODULE_PRELUDE, ChunkKind.SYMBOL, ChunkKind.SYMBOL]
    assert chunks[0].line_start == 1 and chunks[0].line_end == 3
    assert [c.symbol for c in chunks[1:]] == ["a", "b"]


def test_chunk_content_is_byte_identical_to_the_source_lines() -> None:
    src = "def a():\n    return 1\n\n\ndef b():\n    x = 2\n    return x\n"
    lines = src.split("\n")
    for chunk in _chunks(src):
        assert chunk.content == "\n".join(lines[chunk.line_start - 1 : chunk.line_end])


def test_a_function_longer_than_max_lines_is_not_split() -> None:
    body = "\n".join(f"    x{i} = {i}" for i in range(30))
    src = f"def big():\n{body}\n    return 0\n"
    chunks = _chunks(src, max_lines=5)
    fn_chunks = [c for c in chunks if c.symbol == "big"]
    assert len(fn_chunks) == 1
    assert fn_chunks[0].content.count("\n") >= 30  # whole body kept


def test_large_class_becomes_header_plus_method_chunks() -> None:
    filler = "\n".join("        pass" for _ in range(10))
    methods = "\n\n".join(f"    def m{i}(self):\n{filler}" for i in range(6))
    src = f"class Big:\n    attr = 1\n\n{methods}\n"
    chunks = _chunks(src, max_lines=10)
    kinds = [c.kind for c in chunks]
    assert ChunkKind.CLASS_HEADER in kinds
    method_chunks = [c for c in chunks if c.kind is ChunkKind.METHOD]
    assert len(method_chunks) == 6
    assert all(c.parent_chunk_id == chunks[0].chunk_id for c in method_chunks)


def test_small_class_stays_whole() -> None:
    src = "class Small:\n    def a(self):\n        return 1\n"
    chunks = _chunks(src, max_lines=50)
    assert [c.kind for c in chunks] == [ChunkKind.SYMBOL]
    assert chunks[0].symbol == "Small"


def test_chunk_ids_are_deterministic() -> None:
    src = "def a():\n    return 1\n"
    assert [c.chunk_id for c in _chunks(src)] == [c.chunk_id for c in _chunks(src)]


def test_boundary_detector_reports_symbol_starts() -> None:
    src = "import os\n\n\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n"
    structure = StructureAnalyzer().analyze("m.py", src, Language.PYTHON)
    assert BoundaryDetector().detect(structure) == [1, 4, 8]

    md = "# A\n\nx\n\n## B\n\ny\n"
    md_structure = StructureAnalyzer().analyze("d.md", md, Language.MARKDOWN)
    assert BoundaryDetector().detect(md_structure) == [1, 5]
