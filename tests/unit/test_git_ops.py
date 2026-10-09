"""SafeGit: the allowlist, the offline switch, and path-escape guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from issue_to_patch.ingestion import SafeGit, UnsafeGitInvocation


def test_disallowed_subcommand_is_refused(tmp_path: Path) -> None:
    git = SafeGit(root=tmp_path)
    # "remote" is deliberately never allowlisted: a push target is always a
    # literal URL passed straight to `git push <url> <refspec>`, so
    # `git remote add` is never needed.
    with pytest.raises(UnsafeGitInvocation, match="not allowed"):
        git.run("remote", "-v")


def test_offline_blocks_network_subcommands(tmp_path: Path) -> None:
    git = SafeGit(root=tmp_path)
    git.go_offline()
    with pytest.raises(UnsafeGitInvocation, match="offline"):
        git.run("clone", "https://example.com/x.git", "x")


def test_offline_blocks_push_too(tmp_path: Path) -> None:
    """Defense in depth: an instance that already went offline (e.g. the one
    generate_patch uses for verification) must refuse push even though push
    is now allowlisted — callers must construct a fresh SafeGit for push."""
    git = SafeGit(root=tmp_path)
    git.go_offline()
    with pytest.raises(UnsafeGitInvocation, match="offline"):
        git.run("push", "https://example.com/x.git", "main:main")


def test_push_is_allowlisted_and_reaches_the_subprocess(tmp_path: Path) -> None:
    """push must pass the allowlist check (unlike remote) — it just fails
    for an ordinary reason (no git repo here), not "not allowed"."""
    git = SafeGit(root=tmp_path)
    with pytest.raises(UnsafeGitInvocation) as exc_info:
        git.run("push", "https://example.com/x.git", "main:main")
    assert "not allowed" not in str(exc_info.value)


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


def test_push_passes_through_git_ssh_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """push needs real env (SSH_AUTH_SOCK/HOME/GIT_SSH_COMMAND) to actually
    authenticate — every other subcommand runs under a fully-pinned,
    byte-stable env with none of that. Prove the passthrough really reaches
    the subprocess by pointing GIT_SSH_COMMAND at a fake ssh that just
    touches a marker file, instead of mocking subprocess.run."""
    marker = tmp_path / "ssh-was-called"
    fake_ssh = tmp_path / "fake-ssh.sh"
    fake_ssh.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n", "utf-8")
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(fake_ssh))

    repo = tmp_path / "repo"
    repo.mkdir()
    git = SafeGit(root=repo)
    git.run("init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x", "utf-8")
    git.run("add", "-A")
    git.run("commit", "-q", "-m", "init")

    with pytest.raises(UnsafeGitInvocation):
        git.run("push", "git@fake-host-does-not-exist:x/y.git", "main:main")
    assert marker.exists()  # our fake GIT_SSH_COMMAND really did run


def test_non_push_subcommands_do_not_get_the_real_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The passthrough is narrow: only push gets it. A GIT_SSH_COMMAND set on
    the real process must not leak into an ordinary commit's environment."""
    marker = tmp_path / "ssh-was-called"
    fake_ssh = tmp_path / "fake-ssh.sh"
    fake_ssh.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n", "utf-8")
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(fake_ssh))

    git = SafeGit(root=tmp_path)
    git.run("init", "-q", "-b", "main")
    git.run("config", "user.email", "x@y.z")
    assert not marker.exists()


def test_timeout_terminates_git_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import signal
    import subprocess
    from unittest.mock import MagicMock

    process = MagicMock()
    process.pid = 987654
    process.communicate.side_effect = [subprocess.TimeoutExpired("git", 1), ("", "")]
    popen = MagicMock()
    popen.return_value.__enter__.return_value = process
    killpg = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr("issue_to_patch.ingestion.git_ops.os.killpg", killpg)
    with pytest.raises(TimeoutError, match="Repository preparation timed out"):
        SafeGit(root=tmp_path).run(
            "clone", "https://example.com/repo.git", "repo", timeout_seconds=1
        )
    killpg.assert_called_once_with(process.pid, signal.SIGKILL)
    assert process.communicate.call_count == 2
    assert popen.call_args.kwargs["start_new_session"] is True
