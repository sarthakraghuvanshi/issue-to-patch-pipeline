"""Query expansion.

Deterministic. Add terms the retriever is likely to need but that a terse issue
title omits: label words, symbols and paths pulled from stack traces, and
CamelCase / snake_case identifiers mentioned in the body.
"""

from __future__ import annotations

import re

from issue_to_patch.processing.metadata import split_identifier
from issue_to_patch.retrieval.tokenize import extract_stacktrace_locations, query_terms

_IDENTIFIERISH = re.compile(r"\b(?:[a-z]+_[a-z_]+|[A-Za-z]+[A-Z][A-Za-z]*)\b")


def expand_query(
    text: str, *, labels: list[str] | None = None, extra: list[str] | None = None
) -> list[str]:
    """Return the ordered, de-duplicated term list to actually search with."""
    seen: dict[str, None] = {}

    def add(term: str) -> None:
        term = term.lower().strip()
        if len(term) >= 2:
            seen.setdefault(term, None)

    for term in query_terms(text):
        add(term)
    for label in labels or []:
        for piece in re.split(r"[\s:/_-]+", label):
            add(piece)
    for token in extra or []:
        add(token)

    for path, _line, func in extract_stacktrace_locations(text):
        add(path)
        for piece in re.split(r"[./]", path):
            add(piece)
        if func:
            add(func)
            for piece in split_identifier(func):
                add(piece)

    for match in _IDENTIFIERISH.findall(text):
        add(match)
        for piece in split_identifier(match):
            add(piece)

    return list(seen)
