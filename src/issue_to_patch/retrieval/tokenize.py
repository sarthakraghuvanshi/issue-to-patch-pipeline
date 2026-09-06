"""Tokenisation for natural language *and* source code.

Design choices, all inspectable:

* lowercase everything;
* keep dotted / slashed paths whole (``src/mod.py``, ``a.b.c``) **and** also emit
  their pieces, so a query for ``parser.py`` matches ``src/foo/parser.py``;
* split identifiers on ``_`` and camelCase (``parseIssueURL`` -> parse, issue,
  url) and keep the original too;
* pull file+line references out of stack traces and boost them elsewhere.
"""

from __future__ import annotations

import re

from issue_to_patch.processing.metadata import split_identifier

_WORD = re.compile(r"[A-Za-z0-9_]+(?:[./][A-Za-z0-9_]+)*")
_STACK_FRAME_PY = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\w+))?')
_PATHY = re.compile(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]+):(\d+)")

_STOPWORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "to",
    "of",
    "in",
    "on",
    "for",
    "and",
    "or",
    "not",
    "it",
    "this",
    "that",
    "with",
    "as",
    "at",
    "by",
    "from",
    "i",
    "we",
    "you",
    "should",
    "would",
    "when",
    "then",
    "if",
}
_MIN_LEN = 2


def tokenize(text: str) -> list[str]:
    """Return the ordered token list for a document or query."""
    tokens: list[str] = []
    for match in _WORD.finditer(text or ""):
        original = match.group(0)  # keep case so camelCase can be split
        lowered = original.lower()
        tokens.append(lowered)
        if any(sep in original for sep in "./"):
            for piece in re.split(r"[./]", original):
                if piece:
                    tokens.append(piece.lower())
                    tokens.extend(split_identifier(piece))
        else:
            parts = split_identifier(original)
            if parts != [lowered]:
                tokens.extend(parts)
    return [t for t in tokens if len(t) >= _MIN_LEN and t not in _STOPWORDS]


def extract_stacktrace_locations(text: str) -> list[tuple[str, int, str]]:
    """``(file, line, function)`` triples found in Python tracebacks / ``path:line`` refs."""
    found: list[tuple[str, int, str]] = []
    for path, line, func in _STACK_FRAME_PY.findall(text or ""):
        found.append((path, int(line), func or ""))
    for path, line in _PATHY.findall(text or ""):
        found.append((path, int(line), ""))
    return found


def query_terms(text: str) -> list[str]:
    """De-duplicated tokens for a query, preserving first-seen order."""
    seen: dict[str, None] = {}
    for token in tokenize(text):
        seen.setdefault(token, None)
    return list(seen)
