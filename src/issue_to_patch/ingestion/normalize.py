"""Turn whatever the user gives us into an :class:`IssueRequest`.

Accepted forms:

* a GitHub issue URL      ``https://github.com/owner/repo/issues/123``
* a short reference       ``owner/repo#123``
* a fixture file path     ``fixtures/issue_1.json`` (a JSON object)
* raw text                any other non-empty string becomes the issue body

No network calls happen here. Fetching the real issue body from GitHub is
Sprint 2; this is the deterministic front door.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from issue_to_patch.ingestion.errors import InvalidIssueNumber, InvalidIssueReference
from issue_to_patch.ingestion.models import IssueRequest, IssueSource

_GITHUB_HOSTS = {"github.com", "www.github.com"}


def normalize_issue(reference: str) -> IssueRequest:
    """Parse ``reference`` into an :class:`IssueRequest` or raise a typed error."""
    ref = reference.strip()
    if not ref:
        raise InvalidIssueReference("empty issue reference")

    if ref.startswith(("http://", "https://")):
        return _from_url(ref)

    fixture_path = Path(ref)
    if fixture_path.suffix == ".json" or fixture_path.exists():
        return _from_fixture(fixture_path)

    if "#" in ref and "/" in ref.split("#", 1)[0]:
        return _from_short_ref(ref)

    return IssueRequest(source=IssueSource.RAW_TEXT, source_ref=ref, body=ref)


def _from_url(url: str) -> IssueRequest:
    parsed = urlparse(url)
    if parsed.hostname not in _GITHUB_HOSTS:
        raise InvalidIssueReference(f"not a github.com URL: {url}")
    parts = [p for p in parsed.path.split("/") if p]
    # expected: owner / repo / "issues" / number
    if len(parts) != 4 or parts[2] != "issues":
        raise InvalidIssueReference(f"not a github issue URL: {url}")
    owner, repo, _, number_str = parts
    return IssueRequest(
        repo=f"{owner}/{repo}",
        issue_number=_parse_number(number_str),
        source=IssueSource.URL,
        source_ref=url,
    )


def _from_short_ref(ref: str) -> IssueRequest:
    repo_part, number_str = ref.split("#", 1)
    return IssueRequest(
        repo=repo_part,
        issue_number=_parse_number(number_str),
        source=IssueSource.SHORT_REF,
        source_ref=ref,
    )


def _from_fixture(path: Path) -> IssueRequest:
    if not path.exists():
        raise InvalidIssueReference(f"fixture file not found: {path}")
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidIssueReference(f"fixture is not valid JSON: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise InvalidIssueReference(f"fixture must be a JSON object: {path}")

    raw_number = data.get("number") or data.get("issue_number")
    return IssueRequest(
        repo=data.get("repo"),
        issue_number=_parse_number(raw_number) if raw_number is not None else None,
        title=str(data.get("title", "")),
        body=str(data.get("body", "")),
        labels=[str(label) for label in data.get("labels", [])],
        source=IssueSource.FIXTURE,
        source_ref=str(path),
    )


def _parse_number(value: object) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError) as exc:
        raise InvalidIssueNumber(f"issue number is not an integer: {value!r}") from exc
    if number < 1:
        raise InvalidIssueNumber(f"issue number must be positive: {number}")
    return number
