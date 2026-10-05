"""Push an already-built branch to a remote the user owns.

Never pushes to the repository the issue came from — the caller always
supplies a remote the user has configured themselves (``ITP_PUSH_REMOTE_URL``).
No ``git remote add`` is ever run: the remote is passed as a literal URL
straight to ``git push <url> <refspec>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from issue_to_patch.ingestion.errors import UnsafeGitInvocation
from issue_to_patch.ingestion.git_ops import SafeGit
from issue_to_patch.ingestion.raw_store import redact_secrets


@dataclass(frozen=True)
class PushResult:
    remote_display: str  # redacted — safe to print/log
    branch: str
    ok: bool
    detail: str  # redacted — safe to print/log


def push_branch(branch_dir: Path, branch_name: str, remote_url: str) -> PushResult:
    """Push ``branch_name`` (already checked out at ``branch_dir``, e.g. via
    :func:`issue_to_patch.patching.worktree.materialize_branch`) to
    ``remote_url``.

    Uses a fresh :class:`SafeGit` instance rooted at ``branch_dir`` that
    never calls ``go_offline()`` — the one used to build the branch may
    already be offline, and reusing it would simply refuse the push.

    Never raises: a git-level failure comes back as
    ``PushResult(ok=False, detail=<redacted>)`` so the caller can print it
    directly without special-casing exceptions.
    """
    git = SafeGit(root=branch_dir)
    display = _redact_remote(remote_url)
    try:
        git.run("push", remote_url, f"{branch_name}:{branch_name}", cwd=branch_dir)
    except UnsafeGitInvocation as exc:
        return PushResult(
            remote_display=display,
            branch=branch_name,
            ok=False,
            detail=_redact(str(exc), remote_url),
        )
    return PushResult(remote_display=display, branch=branch_name, ok=True, detail="pushed")


def with_embedded_token(url: str, token: str) -> str:
    """Embed ``token`` as the userinfo of an ``http(s)://`` remote, so a
    visitor who has no git credentials configured on *this server* (the
    common case for anyone other than its operator) can still push, using
    nothing but their own token — no SSH key or server-side credential
    helper involved at all.

    A no-op for any other URL shape (scp-style ``git@host:...``, or a URL
    that already carries its own userinfo) — those already carry their own
    credential, or don't support one in the URL at all.
    """
    if "://" not in url:
        return url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or "@" in parts.netloc:
        return url
    return urlunsplit(
        (parts.scheme, f"{token}@{parts.netloc}", parts.path, parts.query, parts.fragment)
    )


def parse_github_owner_repo(url: str) -> tuple[str, str] | None:
    """``(owner, repo)`` from a github.com remote URL — ``https://`` or
    scp-style ``git@host:owner/repo.git``. ``None`` if it doesn't look like
    a github.com URL at all (a different host, or a malformed path) — the
    REST API used for pull-request creation only ever targets github.com,
    not GitHub Enterprise."""
    if "://" in url:
        parts = urlsplit(url)
        host = parts.netloc.rsplit("@", 1)[-1]
        path = parts.path
    else:
        if ":" not in url:
            return None
        host_part, path = url.split(":", 1)
        host = host_part.rsplit("@", 1)[-1]

    if host.lower() != "github.com":
        return None
    segments = path.strip("/").removesuffix(".git").split("/")
    if len(segments) != 2 or not all(segments):
        return None
    return segments[0], segments[1]


def _redact_remote(url: str) -> str:
    """Strip embedded userinfo (``user:pass@``) from an ``http(s)://`` or
    ``ssh://`` URL before it's ever printed/logged — independent of what
    shape the credential takes, unlike a regex over known token prefixes."""
    if "://" not in url:
        return url  # scp-like git@host:path — "git" there is not a secret
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _redact(text: str, remote_url: str) -> str:
    """Belt-and-suspenders: replace the exact known ``remote_url`` substring
    wherever it appears (git's own stderr commonly echoes the raw URL back
    on an auth failure), then run the shared token-shape regex as a second
    pass for anything not verbatim."""
    text = text.replace(remote_url, _redact_remote(remote_url))
    redacted = redact_secrets(text)
    assert isinstance(redacted, str)
    return redacted
