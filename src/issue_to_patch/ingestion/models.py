"""Domain models for a run's inputs.

These are plain data objects (Pydantic) shared across the pipeline. Storage rows
live in ``persistence.models``; these are the in-memory shapes.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class IssueSource(StrEnum):
    URL = "url"
    SHORT_REF = "short_ref"
    FIXTURE = "fixture"
    RAW_TEXT = "raw_text"


class IssueRequest(BaseModel):
    """A GitHub issue normalized into the shape the pipeline works with."""

    repo: str | None = Field(default=None, description="owner/name, when known from the reference")
    issue_number: int | None = Field(default=None, ge=1)
    title: str = ""
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    source: IssueSource
    source_ref: str = Field(description="the original string or file path the user supplied")

    @field_validator("repo")
    @classmethod
    def _check_repo(cls, value: str | None) -> str | None:
        if value is not None and not _REPO_RE.match(value):
            raise ValueError(f"repo must look like 'owner/name', got {value!r}")
        return value

    @property
    def reference(self) -> str:
        """A short human-readable identifier for logs and the run record."""
        if self.repo and self.issue_number:
            return f"{self.repo}#{self.issue_number}"
        if self.repo:
            return self.repo
        return self.source_ref

    def content_key(self) -> str:
        """Stable string used when hashing a run for reproducibility."""
        return "|".join(
            [
                self.repo or "",
                str(self.issue_number or ""),
                self.title.strip(),
                self.body.strip(),
                ",".join(sorted(self.labels)),
            ]
        )


class RepositorySnapshot(BaseModel):
    """A repository frozen at one commit on the local disk."""

    repo: str | None = None
    source: str = Field(description="clone URL or local path the snapshot came from")
    commit_sha: str = Field(min_length=7)
    tree_hash: str
    root_path: Path
    file_count: int = Field(ge=0)
    created_at: datetime

    def manifest(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "source": self.source,
            "commit_sha": self.commit_sha,
            "tree_hash": self.tree_hash,
            "file_count": self.file_count,
            "created_at": self.created_at.isoformat(),
        }


class RunOutcome(BaseModel):
    """The persisted summary of a completed run."""

    run_id: str
    issue_ref: str
    repo: str | None
    commit_sha: str | None
    state: str
    content_hash: str
    created_at: datetime
    finished_at: datetime | None = None


def stable_hash(*parts: str) -> str:
    """SHA-256 hex digest of the parts joined by NUL — used for content hashes."""
    digest = hashlib.sha256()
    digest.update(b"\x00".join(p.encode("utf-8") for p in parts))
    return digest.hexdigest()
