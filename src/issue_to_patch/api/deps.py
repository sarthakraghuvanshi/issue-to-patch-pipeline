"""FastAPI dependencies: settings, store, graph wiring, auth, rate limiting.

Handlers ask for these by type via ``Depends(...)`` — none of them reach for
a provider SDK, the database, or a global directly, which is what keeps
``api/routes.py`` thin and testable with overridden dependencies.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from langgraph.checkpoint.base import BaseCheckpointSaver

from issue_to_patch.config import Settings, get_settings
from issue_to_patch.graph import GraphDependencies, build_dependencies, sqlite_checkpointer
from issue_to_patch.persistence import Store


def get_app_settings() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_store(settings: SettingsDep) -> Store:
    store = Store(settings.database_url)
    store.create_all()  # idempotent; cheap enough to call per-request
    return store


StoreDep = Annotated[Store, Depends(get_store)]

# Keyed by resolved path, not by request: a sqlite3.Connection is a real
# resource (an open file descriptor) that should not be opened fresh on
# every request the way the plain SQLAlchemy Store above can be.
_checkpointer_cache: dict[str, BaseCheckpointSaver[str]] = {}


def get_checkpointer(settings: SettingsDep) -> BaseCheckpointSaver[str]:
    path = str(settings.artifacts_dir / "checkpoints.db")
    if path not in _checkpointer_cache:
        _checkpointer_cache[path] = sqlite_checkpointer(path)
    return _checkpointer_cache[path]


CheckpointerDep = Annotated[BaseCheckpointSaver[str], Depends(get_checkpointer)]


def get_graph_dependencies(settings: SettingsDep, store: StoreDep) -> GraphDependencies:
    return build_dependencies(store=store, settings=settings)


GraphDepsDep = Annotated[GraphDependencies, Depends(get_graph_dependencies)]


@dataclass(frozen=True)
class CurrentUser:
    """Who's making this request — a real per-user account, or one of the
    two synthetic fallbacks that keep today's single-shared-key / local-dev
    behavior working unchanged for anyone who hasn't created real accounts.
    ``authenticated`` is the one field that distinguishes "a verified
    identity" from "whoever had the shared key or is running this locally"
    — only the former may be trusted to claim a role like gatekeeper."""

    username: str
    role: str
    authenticated: bool


async def get_current_user(
    settings: SettingsDep, store: StoreDep, authorization: Annotated[str | None, Header()] = None
) -> CurrentUser:
    """Resolves, in order: a real per-user API key; the single shared
    ``ITP_API_KEY``; or — only when no key is configured at all (local/ci,
    today's no-auth-needed case) — a synthetic local user. Raises 401 only
    when a key *is* configured and nothing presented matches it, exactly
    like the ``require_auth`` this replaces."""
    token = authorization.removeprefix("Bearer ") if authorization else None

    if token:
        user = store.get_user_by_api_key(token)
        if user is not None:
            return CurrentUser(username=user.username, role=user.role, authenticated=True)

    if settings.api_key is None:
        return CurrentUser(username="local", role="gatekeeper", authenticated=False)

    if token == settings.api_key.get_secret_value():
        return CurrentUser(username="shared-key", role="gatekeeper", authenticated=False)

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid bearer token")


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


# A fixed-window limiter keyed by client host, in-memory and single-process —
# fine until real infra (Redis) is running, the same placeholder-now/
# real-later pattern as the hashing embedder and the in-memory checkpointer.
_rate_windows: dict[str, list[float]] = defaultdict(list)


async def rate_limit(request: Request, settings: SettingsDep) -> None:
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _rate_windows[key]
    window[:] = [t for t in window if now - t < 60.0]
    if len(window) >= settings.api_rate_limit_per_minute:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")
    window.append(now)
