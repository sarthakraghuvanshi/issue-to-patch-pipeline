"""normalize_issue: every accepted form, and every rejection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from issue_to_patch.ingestion import (
    InvalidIssueNumber,
    InvalidIssueReference,
    IssueSource,
    normalize_issue,
)


def test_github_issue_url() -> None:
    issue = normalize_issue("https://github.com/pallets/flask/issues/42")
    assert issue.repo == "pallets/flask"
    assert issue.issue_number == 42
    assert issue.source is IssueSource.URL
    assert issue.reference == "pallets/flask#42"


def test_short_ref() -> None:
    issue = normalize_issue("psf/requests#1000")
    assert issue.repo == "psf/requests"
    assert issue.issue_number == 1000
    assert issue.source is IssueSource.SHORT_REF


def test_fixture_file(tmp_path: Path) -> None:
    path = tmp_path / "issue.json"
    path.write_text(
        json.dumps({"repo": "acme/widget", "number": 7, "title": "boom", "body": "stack..."}),
        "utf-8",
    )
    issue = normalize_issue(str(path))
    assert issue.repo == "acme/widget"
    assert issue.issue_number == 7
    assert issue.title == "boom"
    assert issue.source is IssueSource.FIXTURE


def test_raw_text_becomes_body() -> None:
    issue = normalize_issue("the login button does nothing on Safari")
    assert issue.source is IssueSource.RAW_TEXT
    assert "Safari" in issue.body
    assert issue.repo is None


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "https://gitlab.com/x/y/issues/1",
        "https://github.com/only/repo",
        "https://github.com/o/r/pull/3",
    ],
)
def test_malformed_reference_rejected(bad: str) -> None:
    with pytest.raises(InvalidIssueReference):
        normalize_issue(bad)


@pytest.mark.parametrize("bad", ["owner/repo#0", "owner/repo#-3", "owner/repo#abc"])
def test_bad_issue_number_rejected(bad: str) -> None:
    with pytest.raises(InvalidIssueNumber):
        normalize_issue(bad)


def test_missing_fixture_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidIssueReference):
        normalize_issue(str(tmp_path / "nope.json"))


def test_content_key_is_stable() -> None:
    a = normalize_issue("acme/x#1")
    b = normalize_issue("acme/x#1")
    assert a.content_key() == b.content_key()
