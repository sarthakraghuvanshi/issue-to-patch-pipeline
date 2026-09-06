"""Shared fixtures.

The default test environment uses SQLite, a temp artifacts dir, and the fake
LLM, so ``pytest`` runs offline with zero credentials.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("ITP_ENVIRONMENT", "ci")
os.environ.setdefault("ITP_LLM_PROVIDER", "fake")


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
