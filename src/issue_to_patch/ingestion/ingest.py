"""Orchestrate a full ingest: issue + conversation + repo metadata + snapshot.

No LLM. The only network is the GitHub API and the initial ``git clone``; after
the clone the snapshot's :class:`SafeGit` is offline. Everything fetched is
archived to ``artifacts/<run_id>/raw/`` before it is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from issue_to_patch.ingestion.attachments import (
    AttachmentDescriber,
    NullAttachmentDescriber,
    extract_attachment_urls,
)
from issue_to_patch.ingestion.errors import InvalidIssueReference
from issue_to_patch.ingestion.fetch import (
    collect_related_changes,
    fetch_issue,
    fetch_issue_conversation,
    fetch_repository_metadata,
)
from issue_to_patch.ingestion.github import GitHubClient
from issue_to_patch.ingestion.github_models import (
    IssueConversation,
    RelatedChange,
    RepositoryMetadata,
)
from issue_to_patch.ingestion.models import IssueRequest, RepositorySnapshot
from issue_to_patch.ingestion.normalize import normalize_issue
from issue_to_patch.ingestion.raw_store import RawArtifactRecord, RawArtifactWriter
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.logging import get_logger

_log = get_logger("ingest")


@dataclass
class IngestResult:
    issue_request: IssueRequest
    conversation: IssueConversation
    repository: RepositoryMetadata
    snapshot: RepositorySnapshot
    related_changes: list[RelatedChange] = field(default_factory=list)
    attachment_urls: list[str] = field(default_factory=list)
    raw_records: list[RawArtifactRecord] = field(default_factory=list)


async def ingest_issue(
    issue_ref: str,
    run_dir: Path,
    *,
    client: GitHubClient,
    repo_source: str | None = None,
    ref: str | None = None,
    attachment_describer: AttachmentDescriber | None = None,
) -> IngestResult:
    request = normalize_issue(issue_ref)
    if not request.repo or not request.issue_number:
        raise InvalidIssueReference(f"ingest needs a repo and issue number; got {issue_ref!r}")
    repo, number = request.repo, request.issue_number
    _ = attachment_describer or NullAttachmentDescriber()  # wired in Sprint 5+

    raw = RawArtifactWriter(run_dir)
    api = f"{client.base_url}/repos/{repo}"

    issue = await fetch_issue(client, repo, number)
    raw.write("issue", issue, source_url=f"{api}/issues/{number}")

    conversation = await fetch_issue_conversation(client, repo, issue)
    raw.write("issue_comments", conversation.comments, source_url=f"{api}/issues/{number}/comments")

    metadata = await fetch_repository_metadata(client, repo)
    raw.write("repository", metadata, source_url=api)

    related = await collect_related_changes(client, repo, number)
    raw.write("related_changes", related, source_url=f"{api}/issues/{number}/timeline")

    request = request.model_copy(
        update={"title": issue.title, "body": issue.body, "labels": issue.labels}
    )
    attachment_urls = extract_attachment_urls(conversation.as_text())

    clone_from = repo_source or metadata.clone_url
    checkout_ref = ref or metadata.default_branch
    if not clone_from:
        raise InvalidIssueReference(f"no clone source for {repo} (pass repo_source)")

    snapshot = create_snapshot(clone_from, run_dir / "snapshot", ref=checkout_ref, repo_name=repo)
    raw.write("snapshot_manifest", snapshot.manifest(), source_url=clone_from)

    _log.info(
        "ingest.done",
        repo=repo,
        issue=number,
        comments=len(conversation.comments),
        related=len(related),
        commit_sha=snapshot.commit_sha,
        attachments=len(attachment_urls),
    )
    return IngestResult(
        issue_request=request,
        conversation=conversation,
        repository=metadata,
        snapshot=snapshot,
        related_changes=related,
        attachment_urls=attachment_urls,
        raw_records=raw.records,
    )
