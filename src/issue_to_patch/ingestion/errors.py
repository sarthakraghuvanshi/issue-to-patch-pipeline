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
