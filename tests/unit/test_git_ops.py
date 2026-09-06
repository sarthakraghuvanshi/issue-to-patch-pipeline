"""SafeGit: the allowlist, the offline switch, and path-escape guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from issue_to_patch.ingestion import SafeGit, UnsafeGitInvocation


def test_disallowed_subcommand_is_refused(tmp_path: Path) -> None:
    git = SafeGit(root=tmp_path)
    with pytest.raises(UnsafeGitInvocation, match="not allowed"):
        git.run("push")


def test_offline_blocks_network_subcommands(tmp_path: Path) -> None:
    git = SafeGit(root=tmp_path)
    git.go_offline()
    with pytest.raises(UnsafeGitInvocation, match="offline"):
        git.run("clone", "https://example.com/x.git", "x")


def test_path_argument_with_dotdot_is_refused(tmp_path: Path) -> None:
    git = SafeGit(root=tmp_path)
    with pytest.raises(UnsafeGitInvocation, match=r"\.\."):
        git.run("add", "../outside.txt")


def test_cwd_outside_root_is_refused(tmp_path: Path) -> None:
    git = SafeGit(root=tmp_path / "inside")
    (tmp_path / "inside").mkdir()
    with pytest.raises(UnsafeGitInvocation, match="escapes"):
        git.run("status", cwd=tmp_path)


def test_every_call_is_logged(tmp_path: Path) -> None:
    git = SafeGit(root=tmp_path)
    git.run("init", "-q")
    git.run("config", "user.email", "x@y.z")
    assert [c.args[0] for c in git.command_log] == ["init", "config"]
    assert all(c.returncode == 0 for c in git.command_log)


def test_failed_command_raises_when_checked(tmp_path: Path) -> None:
    git = SafeGit(root=tmp_path)
    with pytest.raises(UnsafeGitInvocation, match="failed"):
        git.run("rev-parse", "HEAD")  # not a repo yet


def test_failed_command_returns_when_unchecked(tmp_path: Path) -> None:
    git = SafeGit(root=tmp_path)
    result = git.run("rev-parse", "HEAD", check=False)
    assert result.returncode != 0
