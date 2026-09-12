"""api/deps.py: auth + rate limiting, exercised directly (no ASGI app needed)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from issue_to_patch.api.deps import rate_limit, require_auth
from issue_to_patch.config.settings import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


async def test_require_auth_passes_when_no_key_is_configured() -> None:
    await require_auth(_settings(), authorization=None)  # no exception raised


async def test_require_auth_rejects_a_missing_header() -> None:
    settings = _settings(api_key=SecretStr("secret"))
    with pytest.raises(HTTPException) as exc:
        await require_auth(settings, authorization=None)
    assert exc.value.status_code == 401


async def test_require_auth_rejects_the_wrong_token() -> None:
    settings = _settings(api_key=SecretStr("secret"))
    with pytest.raises(HTTPException) as exc:
        await require_auth(settings, authorization="Bearer wrong")
    assert exc.value.status_code == 401


async def test_require_auth_accepts_the_right_token() -> None:
    settings = _settings(api_key=SecretStr("secret"))
    await require_auth(settings, authorization="Bearer secret")  # no exception raised


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
