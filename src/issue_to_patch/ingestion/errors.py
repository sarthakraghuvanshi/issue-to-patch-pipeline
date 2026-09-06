"""Typed errors for ingestion.

Every failure mode the user can trigger with bad input has its own exception, so
callers (and tests) can react precisely instead of matching on message strings.
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for anything that goes wrong while ingesting an issue or repo."""


class InvalidIssueReference(IngestionError):
    """The issue reference was not a URL, an ``owner/repo#n`` ref, or a valid fixture."""


class RepositoryNotFound(IngestionError):
    """The repository path or URL does not point at a Git repository."""


class InvalidIssueNumber(IngestionError):
    """The issue number was present but not a positive integer."""


class UnsafeGitInvocation(IngestionError):
    """A Git command or path was rejected by the safety wrapper."""


class GitHubAPIError(IngestionError):
    """The GitHub API returned an error we did not recover from."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubNotFound(GitHubAPIError):
    """A repository, issue, or resource does not exist (HTTP 404)."""


class GitHubRateLimited(GitHubAPIError):
    """Rate limit hit and not cleared within the retry budget."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after_seconds = retry_after_seconds
