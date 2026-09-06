"""Data Sources: GitHub API client, issue normalization, repository snapshots."""

from issue_to_patch.ingestion.errors import (
    GitHubAPIError,
    GitHubNotFound,
    GitHubRateLimited,
    IngestionError,
    InvalidIssueNumber,
    InvalidIssueReference,
    RepositoryNotFound,
    UnsafeGitInvocation,
)
from issue_to_patch.ingestion.fetch import (
    collect_related_changes,
    fetch_issue,
    fetch_issue_conversation,
    fetch_repository_metadata,
)
from issue_to_patch.ingestion.git_ops import GitInvocation, SafeGit
from issue_to_patch.ingestion.github import GitHubClient
from issue_to_patch.ingestion.github_models import (
    GitHubComment,
    GitHubIssue,
    IssueConversation,
    RelatedChange,
    RepositoryMetadata,
)
from issue_to_patch.ingestion.ingest import IngestResult, ingest_issue
from issue_to_patch.ingestion.models import (
    IssueRequest,
    IssueSource,
    RepositorySnapshot,
    RunOutcome,
    stable_hash,
)
from issue_to_patch.ingestion.normalize import normalize_issue
from issue_to_patch.ingestion.raw_store import RawArtifactRecord, RawArtifactWriter, redact_secrets
from issue_to_patch.ingestion.snapshot import create_snapshot, discard_snapshot

__all__ = [
    "GitHubAPIError",
    "GitHubClient",
    "GitHubComment",
    "GitHubIssue",
    "GitHubNotFound",
    "GitHubRateLimited",
    "GitInvocation",
    "IngestResult",
    "IngestionError",
    "InvalidIssueNumber",
    "InvalidIssueReference",
    "IssueConversation",
    "IssueRequest",
    "IssueSource",
    "RawArtifactRecord",
    "RawArtifactWriter",
    "RelatedChange",
    "RepositoryMetadata",
    "RepositoryNotFound",
    "RepositorySnapshot",
    "RunOutcome",
    "SafeGit",
    "UnsafeGitInvocation",
    "collect_related_changes",
    "create_snapshot",
    "discard_snapshot",
    "fetch_issue",
    "fetch_issue_conversation",
    "fetch_repository_metadata",
    "ingest_issue",
    "normalize_issue",
    "redact_secrets",
    "stable_hash",
]
