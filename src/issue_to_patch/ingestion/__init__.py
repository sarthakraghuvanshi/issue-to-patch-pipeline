"""Data Sources: GitHub API client, issue normalization, repository snapshots."""

from issue_to_patch.ingestion.errors import (
    IngestionError,
    InvalidIssueNumber,
    InvalidIssueReference,
    RepositoryNotFound,
    UnsafeGitInvocation,
)
from issue_to_patch.ingestion.git_ops import GitInvocation, SafeGit
from issue_to_patch.ingestion.models import (
    IssueRequest,
    IssueSource,
    RepositorySnapshot,
    RunOutcome,
    stable_hash,
)
from issue_to_patch.ingestion.normalize import normalize_issue
from issue_to_patch.ingestion.snapshot import create_snapshot, discard_snapshot

__all__ = [
    "GitInvocation",
    "IngestionError",
    "InvalidIssueNumber",
    "InvalidIssueReference",
    "IssueRequest",
    "IssueSource",
    "RepositoryNotFound",
    "RepositorySnapshot",
    "RunOutcome",
    "SafeGit",
    "UnsafeGitInvocation",
    "create_snapshot",
    "discard_snapshot",
    "normalize_issue",
    "stable_hash",
]
