"""A small, inspectable async GitHub REST client.

Deliberately hand-rolled rather than pulling in a full SDK, so the retry, rate
limit, pagination, and caching behaviour is visible and unit-testable.

Handled here:

* bearer auth + the GitHub API version header;
* retry on 5xx and on rate-limit responses, honouring ``Retry-After`` and
  ``X-RateLimit-Reset``, with a bounded budget;
* ``Link``-header pagination as an async iterator;
* conditional requests: an ``ETag`` cache turns a repeated GET into a cheap 304.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from issue_to_patch.ingestion.errors import GitHubAPIError, GitHubNotFound, GitHubRateLimited
from issue_to_patch.logging import get_logger

_log = get_logger("github")

_RETRYABLE_STATUS = {500, 502, 503, 504}
_DEFAULT_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@dataclass
class _CacheEntry:
    etag: str
    payload: Any


@dataclass
class GitHubClient:
    token: str | None = None
    base_url: str = "https://api.github.com"
    max_retries: int = 4
    max_wait_seconds: float = 60.0
    transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    _client: httpx.AsyncClient = field(init=False)
    _etags: dict[str, _CacheEntry] = field(init=False, default_factory=dict)
    call_log: list[dict[str, object]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        headers = dict(_DEFAULT_HEADERS)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(10.0, read=30.0),
            transport=self.transport,  # type: ignore[arg-type]
            follow_redirects=True,
        )

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- core request with retry + conditional caching -----------------
    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = await self._request("GET", path, params=params)
        return response.json()

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        cache_key = self._cache_key(path, params)
        attempt = 0
        while True:
            attempt += 1
            headers: dict[str, str] = {}
            cached = self._etags.get(cache_key)
            if cached is not None:
                headers["If-None-Match"] = cached.etag

            response = await self._client.request(method, path, params=params, headers=headers)
            self.call_log.append(
                {"method": method, "path": path, "status": response.status_code, "attempt": attempt}
            )
            _log.info(
                "github.call",
                method=method,
                path=path,
                status=response.status_code,
                attempt=attempt,
            )

            if response.status_code == 304 and cached is not None:
                return httpx.Response(
                    200, json=cached.payload, headers=response.headers, request=response.request
                )

            if response.status_code == 404:
                raise GitHubNotFound(f"{method} {path} -> 404", status_code=404)

            if self._is_rate_limited(response):
                wait = self._rate_limit_wait(response)
                if attempt > self.max_retries:
                    raise GitHubRateLimited(
                        f"{method} {path}: rate limited after {attempt} attempts",
                        retry_after_seconds=wait,
                    )
                _log.warning("github.rate_limited", path=path, wait_seconds=wait, attempt=attempt)
                await self.sleep(wait)
                continue

            if response.status_code in _RETRYABLE_STATUS:
                if attempt > self.max_retries:
                    raise GitHubAPIError(
                        f"{method} {path}: {response.status_code} after {attempt} attempts",
                        status_code=response.status_code,
                    )
                await self.sleep(self._backoff(attempt))
                continue

            if response.status_code >= 400:
                raise GitHubAPIError(
                    f"{method} {path} -> {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                )

            etag = response.headers.get("ETag")
            if etag:
                self._etags[cache_key] = _CacheEntry(etag=etag, payload=response.json())
            return response

    # -- pagination ---------------------------------------------------
    async def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        per_page: int = 100,
        max_pages: int = 100,
    ) -> AsyncIterator[dict[str, Any]]:
        query: dict[str, Any] = {"per_page": per_page, **(params or {})}
        next_path: str | None = path
        next_params: dict[str, Any] | None = query
        seen: set[str] = set()
        pages = 0
        while next_path is not None:
            if next_path in seen or pages >= max_pages:
                _log.warning("github.pagination_stopped", path=path, pages=pages)
                break
            seen.add(next_path)
            pages += 1
            response = await self._request("GET", next_path, params=next_params)
            body = response.json()
            if not isinstance(body, list):
                raise GitHubAPIError(f"expected a list from {path}, got {type(body).__name__}")
            for item in body:
                yield item
            next_path = _next_link(response.headers.get("Link", ""))
            next_params = None  # the next link already carries the query string

    # -- search ---------------------------------------------------
    async def search_issues(
        self, query: str, *, sort: str = "updated", order: str = "desc", max_items: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate ``/search/issues`` results (the payload is ``{items: [...]}``, not a list)."""
        per_page = 50
        page = 1
        yielded = 0
        while yielded < max_items:
            body = await self.get_json(
                "/search/issues",
                params={
                    "q": query,
                    "sort": sort,
                    "order": order,
                    "per_page": per_page,
                    "page": page,
                },
            )
            items = body.get("items", []) if isinstance(body, dict) else []
            if not items:
                return
            for item in items:
                yield item
                yielded += 1
                if yielded >= max_items:
                    return
            page += 1
            if page > 20:  # GitHub search caps at 1000 results (20 pages * 50)
                return

    # -- rate-limit helpers -----------------------------------------
    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        if response.status_code == 403:
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                return True
            if "rate limit" in response.text.lower() or "secondary rate" in response.text.lower():
                return True
        return False

    def _rate_limit_wait(self, response: httpx.Response) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), self.max_wait_seconds)
        reset = response.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            delta = float(reset) - time.time()
            return max(0.0, min(delta, self.max_wait_seconds))
        return min(self._backoff(1), self.max_wait_seconds)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0 ** (attempt - 1), 30.0)

    @staticmethod
    def _cache_key(path: str, params: dict[str, Any] | None) -> str:
        if not params:
            return path
        rendered = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{path}?{rendered}"


def _next_link(link_header: str) -> str | None:
    """Extract the ``rel="next"`` URL from a GitHub ``Link`` header."""
    for part in link_header.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url = segments[0].strip().strip("<>")
        if any(seg.strip() == 'rel="next"' for seg in segments[1:]):
            return url
    return None
