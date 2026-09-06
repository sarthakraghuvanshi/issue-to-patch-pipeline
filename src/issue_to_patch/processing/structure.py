"""Language-aware structure extraction.

Python is parsed with tree-sitter into functions, classes, methods, imports, and
line ranges. Markdown is split into heading sections. Other languages get a
minimal single-symbol structure so downstream code has a uniform shape.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from issue_to_patch.processing.models import FileStructure, Language, SourceSymbol, SymbolKind
from issue_to_patch.processing.parser import is_probably_test_path

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@lru_cache(maxsize=1)
def _python_parser() -> Any:
    import tree_sitter_python
    from tree_sitter import Language as TSLanguage
    from tree_sitter import Parser

    return Parser(TSLanguage(tree_sitter_python.language()))


class StructureAnalyzer:
    def analyze(self, path: str, content: str, language: Language) -> FileStructure:
        is_test = is_probably_test_path(path)
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        if language is Language.PYTHON:
            return self._analyze_python(path, content, is_test, line_count)
        if language is Language.MARKDOWN:
            return self._analyze_markdown(path, content, line_count)
        return FileStructure(
            path=path,
            language=language,
            line_count=line_count,
            is_test_file=is_test,
        )

    # -- python ------------------------------------------------------
    def _analyze_python(
        self, path: str, content: str, is_test: bool, line_count: int
    ) -> FileStructure:
        source = content.encode("utf-8")
        try:
            tree = _python_parser().parse(source)
        except Exception as exc:
            return FileStructure(
                path=path,
                language=Language.PYTHON,
                line_count=line_count,
                is_test_file=is_test,
                parse_ok=False,
                parse_error=str(exc),
            )

        imports: list[str] = []
        symbols: list[SourceSymbol] = []
        self._walk_python(tree.root_node, source, parent=None, imports=imports, symbols=symbols)
        for symbol in symbols:
            if symbol.name.startswith("test_") or (is_test and symbol.kind is SymbolKind.FUNCTION):
                symbol.is_test = True
        return FileStructure(
            path=path,
            language=Language.PYTHON,
            line_count=line_count,
            imports=sorted(set(imports)),
            symbols=symbols,
            is_test_file=is_test,
        )

    def _walk_python(
        self,
        node: Any,
        source: bytes,
        *,
        parent: str | None,
        imports: list[str],
        symbols: list[SourceSymbol],
    ) -> None:
        for child in node.children:
            kind = child.type
            if kind == "import_statement":
                imports.extend(_import_names(child, source))
            elif kind == "import_from_statement":
                imports.extend(_import_from_module(child, source))
            elif kind in {"function_definition", "class_definition", "decorated_definition"}:
                definition = _unwrap_decorated(child)
                if definition is None:
                    continue
                name = _field_text(definition, "name", source)
                is_class = definition.type == "class_definition"
                qualified = f"{parent}.{name}" if parent else name
                symbol = SourceSymbol(
                    kind=(
                        SymbolKind.CLASS
                        if is_class
                        else (SymbolKind.METHOD if parent else SymbolKind.FUNCTION)
                    ),
                    name=name,
                    qualified_name=qualified,
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    parent=parent,
                    decorators=_decorator_names(child, source),
                )
                symbols.append(symbol)
                if is_class:
                    body = definition.child_by_field_name("body")
                    if body is not None:
                        self._walk_python(
                            body, source, parent=qualified, imports=imports, symbols=symbols
                        )

    # -- markdown --------------------------------------------------
    def _analyze_markdown(self, path: str, content: str, line_count: int) -> FileStructure:
        symbols: list[SourceSymbol] = []
        lines = content.split("\n")
        open_headings: list[tuple[int, int, str]] = []  # (level, start_line, title)

        def close(down_to_level: int, end_line: int) -> None:
            while open_headings and open_headings[-1][0] >= down_to_level:
                _level, start, title = open_headings.pop()
                symbols.append(
                    SourceSymbol(
                        kind=SymbolKind.SECTION,
                        name=title or f"section@{start}",
                        qualified_name=title or f"section@{start}",
                        line_start=start,
                        line_end=end_line,
                    )
                )

        for i, line in enumerate(lines, start=1):
            match = _MD_HEADING.match(line)
            if match:
                close(len(match.group(1)), i - 1)
                open_headings.append((len(match.group(1)), i, match.group(2).strip()))
        close(1, len(lines))
        symbols.sort(key=lambda s: s.line_start)
        return FileStructure(
            path=path, language=Language.MARKDOWN, line_count=line_count, symbols=symbols
        )


# -- tree-sitter helpers ------------------------------------------
def _field_text(node: Any, field: str, source: bytes) -> str:
    child = node.child_by_field_name(field)
    if child is None:
        return ""
    return source[child.start_byte : child.end_byte].decode("utf-8", "replace")


def _unwrap_decorated(node: Any) -> Any | None:
    if node.type != "decorated_definition":
        return node
    for child in node.children:
        if child.type in {"function_definition", "class_definition"}:
            return child
    return None


def _decorator_names(node: Any, source: bytes) -> list[str]:
    if node.type != "decorated_definition":
        return []
    names: list[str] = []
    for child in node.children:
        if child.type == "decorator":
            text = source[child.start_byte : child.end_byte].decode("utf-8", "replace")
            names.append(text.lstrip("@").split("(")[0].strip())
    return names


def _import_names(node: Any, source: bytes) -> list[str]:
    text = source[node.start_byte : node.end_byte].decode("utf-8", "replace")
    body = text[len("import ") :] if text.startswith("import ") else text
    return [part.strip().split(" as ")[0].split(".")[0] for part in body.split(",") if part.strip()]


def _import_from_module(node: Any, source: bytes) -> list[str]:
    module = node.child_by_field_name("module_name")
    if module is None:
        return []
    name = source[module.start_byte : module.end_byte].decode("utf-8", "replace").strip()
    return [name.split(".")[0]] if name else []
