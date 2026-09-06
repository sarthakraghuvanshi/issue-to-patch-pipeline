"""StructureAnalyzer: symbols, imports, methods, markdown sections."""

from __future__ import annotations

from issue_to_patch.processing import Language, StructureAnalyzer, SymbolKind

PY = '''\
"""module doc."""

import os
from a.b import thing


CONST = 1


@decorator
def top_level(x):
    return x + 1


class Widget:
    """A widget."""

    def method_one(self):
        return top_level(1)

    def method_two(self):
        return 2


def test_widget():
    assert Widget().method_two() == 2
'''


def _structure(path: str = "src/widget.py", content: str = PY):
    return StructureAnalyzer().analyze(path, content, Language.PYTHON)


def test_extracts_imports_at_module_root() -> None:
    s = _structure()
    assert s.imports == ["a", "os"]


def test_extracts_functions_classes_and_methods_with_line_ranges() -> None:
    s = _structure()
    names = {sym.qualified_name: sym for sym in s.symbols}
    assert names["top_level"].kind is SymbolKind.FUNCTION
    assert names["top_level"].decorators == ["decorator"]
    assert names["Widget"].kind is SymbolKind.CLASS
    assert names["Widget.method_one"].kind is SymbolKind.METHOD
    assert names["Widget.method_one"].parent == "Widget"
    m1 = names["Widget.method_one"]
    assert PY.split("\n")[m1.line_start - 1].strip().startswith("def method_one")


def test_flags_test_functions() -> None:
    s = _structure()
    assert next(sym for sym in s.symbols if sym.name == "test_widget").is_test


def test_top_level_symbols_excludes_methods() -> None:
    s = _structure()
    top = {sym.qualified_name for sym in s.top_level_symbols()}
    assert top == {"top_level", "Widget", "test_widget"}


def test_syntactically_broken_python_still_returns_a_structure() -> None:
    s = StructureAnalyzer().analyze("bad.py", "def oops(:\n    pass\n", Language.PYTHON)
    assert s.language is Language.PYTHON
    assert s.parse_ok  # tree-sitter is error-tolerant; it just yields fewer symbols


def test_markdown_splits_into_heading_sections() -> None:
    md = "# Title\n\nintro\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
    s = StructureAnalyzer().analyze("README.md", md, Language.MARKDOWN)
    titles = [sym.name for sym in s.symbols]
    assert titles == ["Title", "A", "B"]
    title = next(sym for sym in s.symbols if sym.name == "Title")
    assert title.line_start == 1
