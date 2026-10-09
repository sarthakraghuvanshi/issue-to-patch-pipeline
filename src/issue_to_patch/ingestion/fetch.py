"""The five ingestion steps, as plain async functions.

They become graph nodes in Sprint 5; for now they are called directly by
``ingest_issue``. Each returns a typed model and never writes to disk — the raw
archiving is the caller's job (``RawArtifactWriter``).
"""

from __future__ import annotations

from issue_to_patch.ingestion.github import GitHubClient
from issue_to_patch.ingestion.github_models import (
    GitHubComment,
    GitHubIssue,
    IssueConversation,
    RelatedChange,
    RepositoryMetadata,
)


async def fetch_issue(client: GitHubClient, repo: str, number: int) -> GitHubIssue:
    payload = await client.get_json(f"/repos/{repo}/issues/{number}")
    return GitHubIssue.from_api(payload)


async def fetch_issue_conversation(
    client: GitHubClient, repo: str, issue: GitHubIssue
) -> IssueConversation:
    comments = [
        GitHubComment.from_api(item)
        async for item in client.paginate(f"/repos/{repo}/issues/{issue.number}/comments")
    ]
    return IssueConversation(issue=issue, comments=comments)


async def fetch_repository_metadata(client: GitHubClient, repo: str) -> RepositoryMetadata:
    payload = await client.get_json(f"/repos/{repo}")
    return RepositoryMetadata.from_api(payload)


async def list_open_issues(
    client: GitHubClient, repo: str, *, max_items: int = 30
) -> list[GitHubIssue]:
    """Open issues for ``repo`` ("owner/repo"), most-recently-updated first.

    The ``/repos/{repo}/issues`` endpoint also returns pull requests — each
    PR-as-issue payload carries a ``"pull_request"`` key — which are
    filtered out here so callers never have to special-case them.
    """
    issues: list[GitHubIssue] = []
    async for payload in client.paginate(
        f"/repos/{repo}/issues",
        params={"state": "open", "sort": "updated", "direction": "desc"},
        per_page=min(max_items, 100),
    ):
        if "pull_request" in payload:
            continue
        issues.append(GitHubIssue.from_api(payload))
        if len(issues) >= max_items:
            break
    return issues


async def collect_related_changes(
    client: GitHubClient, repo: str, number: int
) -> list[RelatedChange]:
    """Pull PRs / commits that reference the issue, from its timeline."""
    related: list[RelatedChange] = []
    async for event in client.paginate(f"/repos/{repo}/issues/{number}/timeline"):
        kind = str(event.get("event", ""))
        if kind == "cross-referenced":
            source = event.get("source", {}) or {}
            issue_like = source.get("issue", {}) if isinstance(source, dict) else {}
            if isinstance(issue_like, dict) and issue_like.get("pull_request"):
                related.append(
                    RelatedChange(
                        kind="cross-referenced",
                        ref=str(issue_like.get("number", "")),
                        title=str(issue_like.get("title", "")),
                        url=str(issue_like.get("html_url", "")),
                    )
                )
        elif kind in {"referenced", "closed"} and event.get("commit_id"):
            related.append(
                RelatedChange(
                    kind=kind,
                    ref=str(event["commit_id"]),
                    url=str(event.get("commit_url", "")),
                )
            )
    return related


async def list_issue_page(
    client: GitHubClient, repo: str, cursor: str | None = None
) -> tuple[list[GitHubIssue], str | None, str | None]:
    """A fixed-size cursor tracks GitHub page/offset without an ever-growing history."""
    import base64
    import json
    from typing import Any

    def encode(page: int, offset: int) -> str:
        return base64.urlsafe_b64encode(json.dumps([page, offset]).encode()).decode()

    try:
        if cursor and len(cursor) > 128:
            raise ValueError("Cursor too long")
        decoded = json.loads(base64.urlsafe_b64decode(cursor)) if cursor else [1, 0]
        if (
            not isinstance(decoded, list)
            or len(decoded) != 2
            or any(type(n) is not int for n in decoded)
            or decoded[0] < 1
            or not 0 <= decoded[1] < 100
        ):
            raise ValueError("Invalid cursor")
        page, offset = int(decoded[0]), int(decoded[1])
    except Exception as exc:
        raise ValueError("Invalid issue page cursor.") from exc

    cache: dict[int, tuple[list[dict[str, Any]], bool]] = {}

    async def fetch(number: int) -> tuple[list[dict[str, Any]], bool]:
        if number not in cache:
            cache[number] = await client.get_page(
                f"/repos/{repo}/issues",
                params={
                    "state": "open",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": 100,
                    "page": number,
                },
            )
        return cache[number]

    previous = None
    back_page, back_offset = page, offset
    found = 0
    for _ in range(20):
        if back_offset == 0:
            back_page -= 1
            back_offset = 100
        if back_page < 1:
            break
        entries, _more = await fetch(back_page)
        for index in range(min(back_offset, len(entries)) - 1, -1, -1):
            if "pull_request" not in entries[index]:
                previous = encode(back_page, index)
                found += 1
                if found == 30:
                    break
        if found == 30:
            break
        back_offset = 0
    else:
        # A very PR-heavy stretch can be traversed without unbounded requests.
        previous = encode(back_page, 0)

    issues: list[GitHubIssue] = []
    for _ in range(20):
        payload, more = await fetch(page)
        while offset < len(payload):
            item = payload[offset]
            offset += 1
            if "pull_request" not in item:
                issues.append(GitHubIssue.from_api(item))
            if len(issues) == 30:
                if offset < len(payload):
                    return issues, encode(page, offset), previous
                return issues, encode(page + 1, 0) if more else None, previous
        if not more:
            return issues, None, previous
        page, offset = page + 1, 0
    return issues, encode(page, offset), previous
