"""Typed shapes for the GitHub data we ingest.

Each model keeps only the fields the pipeline actually uses. The *complete*
untouched payload is still written to ``artifacts/<run_id>/raw/`` — these models
are the working view, not the archive.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

Payload = dict[str, Any]


def _str(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, dict) and "name" in item:
            out.append(str(item["name"]))
        else:
            out.append(str(item))
    return out


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GitHubUser(BaseModel):
    login: str
    html_url: str = ""


class GitHubIssue(BaseModel):
    number: int
    title: str
    body: str = ""
    state: str = "open"
    labels: list[str] = Field(default_factory=list)
    author: str = ""
    comments: int = 0
    html_url: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_api(cls, payload: Payload) -> GitHubIssue:
        user = payload.get("user")
        return cls(
            number=_int(payload.get("number")),
            title=_str(payload.get("title")),
            body=_str(payload.get("body")),
            state=_str(payload.get("state"), "open"),
            labels=_str_list(payload.get("labels")),
            author=_str(user.get("login")) if isinstance(user, dict) else "",
            comments=_int(payload.get("comments")),
            html_url=_str(payload.get("html_url")),
            created_at=_dt(payload.get("created_at")),
            updated_at=_dt(payload.get("updated_at")),
        )


class GitHubComment(BaseModel):
    id: int
    body: str = ""
    author: str = ""
    created_at: datetime | None = None

    @classmethod
    def from_api(cls, payload: Payload) -> GitHubComment:
        user = payload.get("user")
        return cls(
            id=_int(payload.get("id")),
            body=_str(payload.get("body")),
            author=_str(user.get("login")) if isinstance(user, dict) else "",
            created_at=_dt(payload.get("created_at")),
        )


class IssueConversation(BaseModel):
    issue: GitHubIssue
    comments: list[GitHubComment] = Field(default_factory=list)

    def as_text(self) -> str:
        """Flattened issue + comments, for retrieval queries and prompts later."""
        parts = [f"# {self.issue.title}", self.issue.body]
        for comment in self.comments:
            parts.append(f"\n--- comment by {comment.author} ---\n{comment.body}")
        return "\n".join(p for p in parts if p)


class RepositoryMetadata(BaseModel):
    full_name: str
    default_branch: str = "main"
    visibility: str = "public"
    is_archived: bool = False
    is_disabled: bool = False
    size_kb: int = 0
    primary_language: str | None = None
    topics: list[str] = Field(default_factory=list)
    pushed_at: datetime | None = None
    clone_url: str = ""

    @classmethod
    def from_api(cls, payload: Payload) -> RepositoryMetadata:
        language = payload.get("language")
        topics = payload.get("topics")
        return cls(
            full_name=_str(payload.get("full_name")),
            default_branch=_str(payload.get("default_branch"), "main"),
            visibility=_str(payload.get("visibility"), "public"),
            is_archived=bool(payload.get("archived", False)),
            is_disabled=bool(payload.get("disabled", False)),
            size_kb=_int(payload.get("size")),
            primary_language=_str(language) if language is not None else None,
            topics=[str(t) for t in topics] if isinstance(topics, list) else [],
            pushed_at=_dt(payload.get("pushed_at")),
            clone_url=_str(payload.get("clone_url")),
        )


class RelatedChange(BaseModel):
    """A pull request or commit that references the issue (from the timeline)."""

    kind: str  # "cross-referenced" | "referenced" | "connected" | "closed"
    ref: str  # PR number, commit SHA, or URL
    title: str = ""
    url: str = ""
