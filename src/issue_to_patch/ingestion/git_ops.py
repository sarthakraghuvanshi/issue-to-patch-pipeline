"""A safety wrapper around the ``git`` command-line tool.

Rules enforced here:

* only an allowlisted set of subcommands may run;
* every invocation is logged and appended to an in-memory command log;
* once :meth:`SafeGit.go_offline` is called, network subcommands are refused;
* any path argument must stay inside the wrapper's root directory (no ``..``
  escape, no absolute path pointing elsewhere).

Repository content is untrusted (Principle 3), so we never pass it to a shell —
``subprocess.run`` is called with a list, ``shell=False``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from issue_to_patch.ingestion.errors import UnsafeGitInvocation
from issue_to_patch.logging import get_logger

_log = get_logger("git")

# Subcommands the pipeline is ever allowed to run.
_ALLOWED_SUBCOMMANDS = frozenset(
    {
        "version",
        "init",
        "clone",
        "config",
        "rev-parse",
        "ls-files",
        "cat-file",
        "log",
        "show",
        "status",
        "diff",
        "add",
        "commit",
        "checkout",
        "branch",
        "worktree",
        "apply",
        "format-patch",
        "am",
    }
)

# Subcommands that touch the network; blocked after go_offline().
_NETWORK_SUBCOMMANDS = frozenset({"clone", "fetch", "pull", "push", "remote"})

# A fixed identity + timestamp so commits (and therefore patches) are byte-stable.
_DETERMINISTIC_ENV = {
    "GIT_AUTHOR_NAME": "issue-to-patch",
    "GIT_AUTHOR_EMAIL": "bot@issue-to-patch.local",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "issue-to-patch",
    "GIT_COMMITTER_EMAIL": "bot@issue-to-patch.local",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+00:00",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


@dataclass(frozen=True)
class GitInvocation:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    ts: datetime


@dataclass
class SafeGit:
    """Run ``git`` commands under an allowlist, rooted at ``root``."""

    root: Path
    _offline: bool = False
    command_log: list[GitInvocation] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())

    def go_offline(self) -> None:
        """Block all network subcommands from here on."""
        self._offline = True

    # -- the single choke point ------------------------------------------
    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
        input_text: str | None = None,
        allow_external_paths: bool = False,
    ) -> GitInvocation:
        """Run ``git <args>``.

        ``allow_external_paths`` must be set explicitly by the caller for the few
        commands that legitimately reference a path outside the root — cloning
        *from* an external source, or adding a worktree *at* a path we chose.
        Everything else is confined to ``root``.
        """
        if not args:
            raise UnsafeGitInvocation("no git subcommand given")
        subcommand = args[0]
        if subcommand not in _ALLOWED_SUBCOMMANDS:
            raise UnsafeGitInvocation(f"git subcommand not allowed: {subcommand}")
        if self._offline and subcommand in _NETWORK_SUBCOMMANDS:
            raise UnsafeGitInvocation(f"network subcommand refused while offline: {subcommand}")

        workdir = self._safe_cwd(cwd)
        self._check_path_args(args, allow_external_paths=allow_external_paths)

        completed = subprocess.run(
            ["git", *args],
            cwd=workdir,
            capture_output=True,
            text=True,
            input=input_text,
            env={**_DETERMINISTIC_ENV},
            check=False,
        )
        invocation = GitInvocation(
            args=tuple(args),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            ts=datetime.now(UTC),
        )
        self.command_log.append(invocation)
        _log.info(
            "git",
            subcommand=subcommand,
            returncode=completed.returncode,
            cwd=str(workdir),
        )
        if check and completed.returncode != 0:
            raise UnsafeGitInvocation(
                f"git {subcommand} failed ({completed.returncode}): {completed.stderr.strip()}"
            )
        return invocation

    # -- guards ---------------------------------------------------------
    def _safe_cwd(self, cwd: Path | None) -> Path:
        if cwd is None:
            return self.root
        resolved = Path(cwd).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise UnsafeGitInvocation(f"cwd escapes the git root: {resolved}")
        return resolved

    def _check_path_args(self, args: tuple[str, ...], *, allow_external_paths: bool) -> None:
        for arg in args[1:]:
            if arg.startswith("-"):
                continue
            if ".." in Path(arg).parts:
                raise UnsafeGitInvocation(f"path argument contains '..': {arg}")
            if allow_external_paths:
                continue
            if Path(arg).is_absolute():
                resolved = Path(arg).resolve()
                if resolved != self.root and self.root not in resolved.parents:
                    raise UnsafeGitInvocation(f"absolute path escapes the git root: {arg}")


def git_version() -> str:
    """Best-effort ``git --version`` for diagnostics."""
    out = subprocess.run(["git", "version"], capture_output=True, text=True, check=False)
    return out.stdout.strip()
