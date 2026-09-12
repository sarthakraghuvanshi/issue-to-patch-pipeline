"""safety/permissions.py: deterministic, glob-based risk classification."""

from __future__ import annotations

import pytest

from issue_to_patch.safety.permissions import is_risky, risky_paths


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "Dockerfile",
        "backend/Dockerfile.prod",
        "docker-compose.yml",
        ".env",
        ".env.production",
        "config/secrets/prod.json",
        "certs/server.pem",
        "certs/server.key",
        "migrations/0001_initial.py",
        "alembic/versions/abcd1234_add_table.py",
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
        "requirements-dev.txt",
        "uv.lock",
    ],
)
def test_flags_known_risky_paths(path: str) -> None:
    assert is_risky([path]) is True
    assert path in risky_paths([path])


@pytest.mark.parametrize(
    "path",
    [
        "src/issue_to_patch/graph/nodes.py",
        "README.md",
        "tests/unit/test_nodes.py",
        "docs/architecture.md",
    ],
)
def test_does_not_flag_ordinary_source_paths(path: str) -> None:
    assert is_risky([path]) is False
    assert risky_paths([path]) == []


def test_one_risky_file_among_many_is_enough() -> None:
    files = ["src/a.py", "src/b.py", ".github/workflows/deploy.yml"]
    assert is_risky(files) is True
    assert risky_paths(files) == [".github/workflows/deploy.yml"]


def test_empty_change_set_is_not_risky() -> None:
    assert is_risky([]) is False
    assert risky_paths([]) == []
