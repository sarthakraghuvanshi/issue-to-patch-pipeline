"""Tokeniser: identifier splitting, path handling, stack-trace extraction."""

from __future__ import annotations

from issue_to_patch.retrieval.tokenize import (
    extract_stacktrace_locations,
    query_terms,
    tokenize,
)


def test_splits_identifiers_but_keeps_the_original() -> None:
    tokens = tokenize("parseIssueURL and read_config")
    assert "parseissueurl" in tokens
    assert {"parse", "issue", "url"} <= set(tokens)
    assert {"read", "config"} <= set(tokens)


def test_keeps_paths_whole_and_also_splits_them() -> None:
    tokens = tokenize("the bug is in src/foo/parser.py")
    assert "src/foo/parser.py" in tokens
    assert "parser" in tokens
    assert "py" in tokens


def test_drops_stopwords_and_short_tokens() -> None:
    assert "the" not in tokenize("the parser")
    assert "a" not in tokenize("a b cd")


def test_extracts_python_traceback_frames() -> None:
    tb = (
        "Traceback (most recent call last):\n"
        '  File "src/app/handler.py", line 42, in handle_request\n'
        "    raise ValueError\n"
    )
    locs = extract_stacktrace_locations(tb)
    assert ("src/app/handler.py", 42, "handle_request") in locs


def test_extracts_path_colon_line_refs() -> None:
    locs = extract_stacktrace_locations("see calc.py:17 for the off-by-one")
    assert ("calc.py", 17, "") in locs


def test_query_terms_dedupe_preserving_order() -> None:
    assert query_terms("parser parser bug parser") == ["parser", "bug"]
