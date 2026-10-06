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
