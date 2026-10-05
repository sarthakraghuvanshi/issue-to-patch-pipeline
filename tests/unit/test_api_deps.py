"""api/deps.py: auth + rate limiting, exercised directly (no ASGI app needed)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from issue_to_patch.api.deps import CurrentUser, get_current_user, rate_limit
from issue_to_patch.config.settings import Settings
from issue_to_patch.persistence import Store


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _store(tmp_path: Path) -> Store:
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'deps.db'}")
    store.create_all()
    return store


async def test_get_current_user_falls_back_to_local_when_no_key_is_configured(
    tmp_path: Path,
) -> None:
    user = await get_current_user(_settings(), _store(tmp_path), authorization=None)
    assert user.authenticated is False
    assert user.role == "gatekeeper"


async def test_get_current_user_rejects_a_missing_header_when_a_key_is_configured(
    tmp_path: Path,
) -> None:
    settings = _settings(api_key=SecretStr("secret"))
    with pytest.raises(HTTPException) as exc:
        await get_current_user(settings, _store(tmp_path), authorization=None)
    assert exc.value.status_code == 401


async def test_get_current_user_rejects_the_wrong_token(tmp_path: Path) -> None:
    settings = _settings(api_key=SecretStr("secret"))
    with pytest.raises(HTTPException) as exc:
        await get_current_user(settings, _store(tmp_path), authorization="Bearer wrong")
    assert exc.value.status_code == 401


async def test_get_current_user_accepts_the_shared_key(tmp_path: Path) -> None:
    settings = _settings(api_key=SecretStr("secret"))
    user = await get_current_user(settings, _store(tmp_path), authorization="Bearer secret")
    assert user.authenticated is False
    assert user.role == "gatekeeper"


async def test_get_current_user_resolves_a_real_per_user_account(tmp_path: Path) -> None:
    store = _store(tmp_path)
    api_key = store.create_user(username="alice", role="auditor")
    user = await get_current_user(_settings(), store, authorization=f"Bearer {api_key}")
    assert user == CurrentUser(username="alice", role="auditor", authenticated=True)


async def test_get_current_user_ignores_a_disabled_users_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    api_key = store.create_user(username="alice", role="auditor")
    store.disable_user("alice")
    user = await get_current_user(_settings(), store, authorization=f"Bearer {api_key}")
    assert user.authenticated is False  # falls back to local, same as no key at all


async def test_rate_limit_allows_requests_under_the_limit() -> None:
    settings = _settings(api_rate_limit_per_minute=2)
    request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4-under-limit"))
    await rate_limit(request, settings)  # type: ignore[arg-type]
    await rate_limit(request, settings)  # type: ignore[arg-type]


async def test_rate_limit_rejects_once_the_limit_is_exceeded() -> None:
    settings = _settings(api_rate_limit_per_minute=2)
    request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4-over-limit"))
    await rate_limit(request, settings)  # type: ignore[arg-type]
    await rate_limit(request, settings)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        await rate_limit(request, settings)  # type: ignore[arg-type]
    assert exc.value.status_code == 429


async def test_rate_limit_treats_a_missing_client_as_one_shared_bucket() -> None:
    settings = _settings(api_rate_limit_per_minute=100)
    request = SimpleNamespace(client=None)
    await rate_limit(request, settings)  # type: ignore[arg-type]
