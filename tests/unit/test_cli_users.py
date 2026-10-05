"""`create-user` / `list-users` / `disable-user` CLI commands."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from issue_to_patch.cli import app
from issue_to_patch.config import get_settings
from issue_to_patch.persistence import Store

runner = CliRunner()


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'users.db'}")
    get_settings.cache_clear()
    store = Store(get_settings().database_url)
    store.create_all()
    yield store
    get_settings.cache_clear()


def test_create_user_prints_the_key_exactly_once(db: Store) -> None:
    result = runner.invoke(app, ["create-user", "alice", "--role", "auditor"])
    assert result.exit_code == 0
    assert "user created: alice (auditor)" in result.output
    assert "itp_" in result.output

    user = db.get_user_by_api_key(_extract_key(result.output))
    assert user is not None
    assert user.username == "alice"
    assert user.role == "auditor"


def test_create_user_rejects_an_invalid_role(db: Store) -> None:
    result = runner.invoke(app, ["create-user", "alice", "--role", "wizard"])
    assert result.exit_code == 2
    assert "gatekeeper | auditor | strategist" in result.output


def test_create_user_rejects_a_duplicate_username(db: Store) -> None:
    runner.invoke(app, ["create-user", "alice"])
    result = runner.invoke(app, ["create-user", "alice"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_list_users_shows_role_and_status_but_never_the_key(db: Store) -> None:
    created = runner.invoke(app, ["create-user", "alice", "--role", "gatekeeper"])
    api_key = _extract_key(created.output)

    result = runner.invoke(app, ["list-users"])
    assert result.exit_code == 0
    assert "alice" in result.output
    assert "gatekeeper" in result.output
    assert "active" in result.output
    assert api_key not in result.output


def test_list_users_with_none_created_yet(db: Store) -> None:
    result = runner.invoke(app, ["list-users"])
    assert result.exit_code == 0
    assert "no users yet" in result.output


def test_disable_user_revokes_the_key(db: Store) -> None:
    created = runner.invoke(app, ["create-user", "alice"])
    api_key = _extract_key(created.output)
    assert db.get_user_by_api_key(api_key) is not None

    result = runner.invoke(app, ["disable-user", "alice"])
    assert result.exit_code == 0
    assert "disabled: alice" in result.output
    assert db.get_user_by_api_key(api_key) is None

    listed = runner.invoke(app, ["list-users"])
    assert "disabled" in listed.output


def test_disable_user_reports_an_unknown_username(db: Store) -> None:
    result = runner.invoke(app, ["disable-user", "no-such-user"])
    assert result.exit_code == 1
    assert "no such user" in result.output


def _extract_key(output: str) -> str:
    line = next(line for line in output.splitlines() if line.startswith("API key"))
    return line.rsplit(" ", 1)[-1]
