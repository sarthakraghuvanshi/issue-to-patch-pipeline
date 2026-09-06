"""Shared fixtures.

The default test environment uses SQLite, a temp artifacts dir, and the fake
LLM, so ``pytest`` runs offline with zero credentials.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("ITP_ENVIRONMENT", "ci")
os.environ.setdefault("ITP_LLM_PROVIDER", "fake")

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@test.local",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@test.local",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+00:00",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env=_GIT_ENV, check=True, capture_output=True)


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A tiny Git repo with a deliberate bug in ``calculator.py``."""
    repo = tmp_path / "buggy-project"
    repo.mkdir()
    (repo / "calculator.py").write_text(
        "def add(a, b):\n    return a - b  # BUG: should be a + b\n",
        "utf-8",
    )
    (repo / "test_calculator.py").write_text(
        "from calculator import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        "utf-8",
    )
    (repo / "README.md").write_text("# Buggy Project\n", "utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial commit with bug")
    return repo


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from issue_to_patch.config import get_settings
    from issue_to_patch.config.settings import Settings

    monkeypatch.setenv("ITP_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    try:
        yield Settings()
    finally:
        get_settings.cache_clear()


@pytest.fixture
def fake_llm() -> Iterator[object]:
    from issue_to_patch.llm import FakeLLM

    yield FakeLLM()
