"""Deterministic risk classification for a proposed patch.

Not every patch deserves the same level of scrutiny. Changing a docstring and
changing a CI workflow are not the same risk — this module says which changed
paths are "risky" (globs only; no LLM judgment call, so it can't be talked
out of flagging something), and the router uses it to require a specific
review role (Gatekeeper) rather than accepting any human's "approve".
"""

from __future__ import annotations

from fnmatch import fnmatch

# Deliberately glob-based and conservative: a false positive costs one extra
# review; a false negative could ship a change to CI, secrets, or how the
# patch pipeline itself runs. Extend this list — never narrow it — from
# incidents, not speculation.
#
# Each entry is given both at the repo root and nested anywhere (fnmatch has
# no path-aware "**", so "migrations/**" would otherwise miss
# "backend/migrations/0001.py" and "**/migrations/**" would otherwise miss a
# root-level "migrations/0001.py" — a bug this file had until a test caught it).
_RISKY_BASENAMES: tuple[str, ...] = (
    ".github/**",
    "Dockerfile*",
    "docker-compose*.y*ml",
    "*.env",
    ".env",
    ".env.*",
    "secrets/**",
    "secret*",
    "*.pem",
    "*.key",
    "migrations/**",
    "alembic/versions/**",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements*.txt",
    "*.lock",
)

RISKY_GLOBS: tuple[str, ...] = tuple(
    pattern for base in _RISKY_BASENAMES for pattern in (base, f"**/{base}")
)


def risky_paths(changed_files: list[str]) -> list[str]:
    """The subset of ``changed_files`` matching a risky pattern."""
    return [f for f in changed_files if any(fnmatch(f, pat) for pat in RISKY_GLOBS)]


def is_risky(changed_files: list[str]) -> bool:
    return bool(risky_paths(changed_files))
