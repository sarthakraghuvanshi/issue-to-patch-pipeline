"""Metadata generators: identifier splitting, keywords, summaries, questions."""

from __future__ import annotations

from issue_to_patch.processing import (
    Language,
    StructureAnalyzer,
    StructureAwareChunker,
    split_identifier,
)
from issue_to_patch.processing.metadata import MetadataEnricher

SRC = "def parse_issue_url(raw_url):\n    return raw_url.strip()\n"


def _enriched():
    structure = StructureAnalyzer().analyze("src/norm.py", SRC, Language.PYTHON)
    chunk = StructureAwareChunker().chunk(structure, SRC, repository="o/r", commit_sha="a" * 40)[0]
    return MetadataEnricher().enrich(
        chunk, structure, test_index={"parse_issue_url": ["tests/t.py"]}
    )


def test_split_identifier_handles_snake_and_camel() -> None:
    assert split_identifier("parse_issue_url") == ["parse", "issue", "url"]
    assert split_identifier("parseIssueURL") == ["parse", "issue", "url"]
    assert split_identifier("HTTPServer") == ["http", "server"]


def test_keywords_favour_the_symbol_name() -> None:
    enriched = _enriched()
    assert "parse" in enriched.keywords[:5]
    assert "issue" in enriched.keywords[:5]


def test_summary_names_the_symbol_and_location() -> None:
    enriched = _enriched()
    assert "parse_issue_url" in enriched.summary
    assert "src/norm.py:1-2" in enriched.summary


def test_questions_are_templated_and_reference_the_symbol() -> None:
    enriched = _enriched()
    assert any("parse_issue_url" in q for q in enriched.questions)


def test_references_come_from_the_test_index() -> None:
    assert _enriched().references == ["tests/t.py"]


def test_enrichment_is_deterministic() -> None:
    assert _enriched().model_dump() == _enriched().model_dump()
