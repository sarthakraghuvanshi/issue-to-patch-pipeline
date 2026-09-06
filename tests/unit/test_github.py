"""GitHubClient: retries, rate-limit handling, pagination, ETag caching."""

from __future__ import annotations

import httpx
import pytest
import respx

from issue_to_patch.ingestion import GitHubClient, GitHubNotFound, GitHubRateLimited

BASE = "https://api.github.com"


async def _client() -> GitHubClient:
    # sleep is a no-op so retry tests do not actually wait
    return GitHubClient(token="t", base_url=BASE, sleep=_no_sleep)


async def _no_sleep(_seconds: float) -> None:
    return None


@respx.mock
async def test_get_json_returns_body() -> None:
    respx.get(f"{BASE}/repos/o/r/issues/1").mock(
        return_value=httpx.Response(200, json={"number": 1, "title": "hi"})
    )
    client = await _client()
    body = await client.get_json("/repos/o/r/issues/1")
    assert body["title"] == "hi"
    await client.aclose()


@respx.mock
async def test_404_raises_not_found() -> None:
    respx.get(f"{BASE}/repos/o/missing").mock(return_value=httpx.Response(404, json={}))
    client = await _client()
    with pytest.raises(GitHubNotFound):
        await client.get_json("/repos/o/missing")
    await client.aclose()


@respx.mock
async def test_retries_on_500_then_succeeds() -> None:
    route = respx.get(f"{BASE}/repos/o/r")
    route.side_effect = [
        httpx.Response(500, text="boom"),
        httpx.Response(502, text="boom"),
        httpx.Response(200, json={"full_name": "o/r"}),
    ]
    client = await _client()
    body = await client.get_json("/repos/o/r")
    assert body["full_name"] == "o/r"
    assert route.call_count == 3
    await client.aclose()


@respx.mock
async def test_secondary_rate_limit_403_is_retried() -> None:
    route = respx.get(f"{BASE}/repos/o/r/issues/1")
    route.side_effect = [
        httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0", "Retry-After": "1"},
            text="You have exceeded a secondary rate limit",
        ),
        httpx.Response(200, json={"number": 1, "title": "ok"}),
    ]
    client = await _client()
    body = await client.get_json("/repos/o/r/issues/1")
    assert body["title"] == "ok"
    await client.aclose()


@respx.mock
async def test_rate_limit_gives_up_after_budget() -> None:
    respx.get(f"{BASE}/repos/o/r").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "1"}, text="rate limit")
    )
    client = GitHubClient(base_url=BASE, max_retries=2, sleep=_no_sleep)
    with pytest.raises(GitHubRateLimited):
        await client.get_json("/repos/o/r")
    await client.aclose()


@respx.mock
async def test_pagination_follows_link_header() -> None:
    route = respx.get(f"{BASE}/repos/o/r/issues/1/comments")
    route.side_effect = [
        httpx.Response(
            200,
            json=[{"id": 1, "body": "a", "user": {"login": "x"}}],
            headers={"Link": f'<{BASE}/repos/o/r/issues/1/comments?page=2>; rel="next"'},
        ),
        httpx.Response(200, json=[{"id": 2, "body": "b", "user": {"login": "y"}}]),
    ]
    client = await _client()
    items = [item async for item in client.paginate("/repos/o/r/issues/1/comments")]
    assert [i["id"] for i in items] == [1, 2]
    assert route.call_count == 2
    await client.aclose()


@respx.mock
async def test_etag_cache_turns_repeat_into_304() -> None:
    route = respx.get(f"{BASE}/repos/o/r")
    route.side_effect = [
        httpx.Response(200, json={"full_name": "o/r"}, headers={"ETag": '"abc"'}),
        httpx.Response(304, headers={"ETag": '"abc"'}),
    ]
    client = await _client()
    first = await client.get_json("/repos/o/r")
    second = await client.get_json("/repos/o/r")
    assert first == second == {"full_name": "o/r"}
    assert route.call_count == 2
    await client.aclose()
