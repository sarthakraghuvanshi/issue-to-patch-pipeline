"""Language detection + a light parse that validates structured formats.

Deep structure (symbols, imports) is :mod:`issue_to_patch.processing.structure`.
This module just says *what* a file is and whether it is well-formed.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from pydantic import BaseModel

from issue_to_patch.processing.models import Language

_EXTENSION_LANGUAGE = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
    ".json": Language.JSON,
    ".yaml": Language.YAML,
    ".yml": Language.YAML,
    ".txt": Language.TEXT,
    ".rst": Language.TEXT,
    ".cfg": Language.TEXT,
    ".ini": Language.TEXT,
    ".toml": Language.TEXT,
}


def detect_language(path: str) -> Language:
    suffix = PurePosixPath(path).suffix.lower()
    return _EXTENSION_LANGUAGE.get(suffix, Language.UNKNOWN)


def is_probably_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = PurePosixPath(path).name
    return (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or name.endswith(("_test.py", "_tests.py"))
        or name == "conftest.py"
    )


class ParsedDocument(BaseModel):
    path: str
    language: Language
    text: str
    line_count: int
    parse_ok: bool = True
    parse_error: str = ""


class DocumentParser:
    """Normalise newlines, detect the language, validate JSON/YAML."""

    def parse(self, path: str, content: str) -> ParsedDocument:
        text = content.replace("\r\n", "\n").replace("\r", "\n")
        language = detect_language(path)
        ok, error = self._validate(language, text)
        return ParsedDocument(
            path=path,
            language=language,
            text=text,
            line_count=text.count("\n") + (1 if text and not text.endswith("\n") else 0),
            parse_ok=ok,
            parse_error=error,
        )

    @staticmethod
    def _validate(language: Language, text: str) -> tuple[bool, str]:
        if language is Language.JSON:
            try:
                json.loads(text or "null")
            except json.JSONDecodeError as exc:
                return False, f"invalid JSON: {exc}"
        if language is Language.YAML:
            try:
                import yaml

                yaml.safe_load(text)
            except Exception as exc:
                return False, f"invalid YAML: {exc}"
        return True, ""
