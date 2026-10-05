"""push_branch: redaction and a real push over a local, networkless transport."""

from __future__ import annotations

import subprocess
from pathlib import Path

from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.patching import EditPlan, FileEdit, generate_patch, materialize_branch
from issue_to_patch.patching.push import (
    PushResult,
    _redact_remote,
    parse_github_owner_repo,
    push_branch,
)


def test_redact_remote_strips_userinfo_from_https() -> None:
    url = "https://user:ghp_abcXYZ0123456789@github.com/user/fork.git"
    redacted = _redact_remote(url)
    assert "ghp_abcXYZ0123456789" not in redacted
    assert "@" not in redacted  # no credential delimiter left at all
    assert redacted == "https://github.com/user/fork.git"


def test_parse_github_owner_repo_from_https() -> None:
    assert parse_github_owner_repo("https://github.com/alice/fork.git") == ("alice", "fork")
    assert parse_github_owner_repo("https://github.com/alice/fork") == ("alice", "fork")


def test_parse_github_owner_repo_from_scp_style() -> None:
    assert parse_github_owner_repo("git@github.com:alice/fork.git") == ("alice", "fork")


def test_parse_github_owner_repo_strips_embedded_credentials() -> None:
    url = "https://ghp_abc123@github.com/alice/fork.git"
    assert parse_github_owner_repo(url) == ("alice", "fork")


def test_parse_github_owner_repo_rejects_a_non_github_host() -> None:
    assert parse_github_owner_repo("https://gitlab.com/alice/fork.git") is None
    assert parse_github_owner_repo("git@gitlab.com:alice/fork.git") is None


def test_parse_github_owner_repo_rejects_a_malformed_path() -> None:
    assert parse_github_owner_repo("https://github.com/alice") is None
    assert parse_github_owner_repo("https://github.com/alice/fork/extra") is None
    assert parse_github_owner_repo("not-a-url-at-all") is None


def test_redact_remote_leaves_scp_syntax_alone() -> None:
    url = "git@github.com:me/fork.git"
    assert _redact_remote(url) == url


def test_redact_remote_leaves_url_without_userinfo_alone() -> None:
    url = "https://github.com/me/fork.git"
    assert _redact_remote(url) == url


def test_push_branch_reaches_a_local_bare_remote(fixture_repo: Path, tmp_path: Path) -> None:
    """A real `git push`, over a file:// transport instead of a mocked
    subprocess — a local bare repo is a completely realistic, networkless
    remote, so this exercises the real SafeGit.run("push", ...) call."""
    snap = create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/x")
    plan = EditPlan(edits=[FileEdit(path="calculator.py", old="a - b", new="a + b")])
    patch = generate_patch(snap, plan)
    branch_dir = tmp_path / "snap" / "branch"
    materialize_branch(snap, patch, branch_dir, "itp/test-push")

    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)

    result = push_branch(branch_dir, "itp/test-push", f"file://{bare}")

    assert result.ok, result.detail
    assert result.branch == "itp/test-push"
    ls_remote = subprocess.run(
        ["git", "ls-remote", str(bare), "itp/test-push"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "itp/test-push" in ls_remote


def test_push_branch_reports_failure_without_raising(fixture_repo: Path, tmp_path: Path) -> None:
    snap = create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/x")
    plan = EditPlan(edits=[FileEdit(path="calculator.py", old="a - b", new="a + b")])
    patch = generate_patch(snap, plan)
    branch_dir = tmp_path / "snap" / "branch"
    materialize_branch(snap, patch, branch_dir, "itp/test-push")

    result = push_branch(branch_dir, "itp/test-push", "file:///no/such/bare/repo")

    # the point of this test: a git-level failure comes back as data, not an
    # exception — the caller never needs a try/except around push_branch.
    assert isinstance(result, PushResult)
    assert not result.ok
    assert result.detail
