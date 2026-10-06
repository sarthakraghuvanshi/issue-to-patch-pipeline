"""issue_to_patch.ingestion.fetch.list_open_issues: filters PRs, sorts, caps."""

from __future__ import annotations

import httpx
import respx

from issue_to_patch.ingestion import GitHubClient
from issue_to_patch.ingestion.fetch import list_open_issues

BASE = "https://api.github.com"


@respx.mock
async def test_list_open_issues_filters_out_pull_requests() -> None:
    respx.get(f"{BASE}/repos/o/r/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 2, "title": "a PR", "pull_request": {"url": "..."}, "state": "open"},
                {"number": 1, "title": "a real issue", "state": "open", "user": {"login": "x"}},
            ],
        )
    )
    client = GitHubClient(base_url=BASE)
    issues = await list_open_issues(client, "o/r")
    assert [i.number for i in issues] == [1]
    await client.aclose()


@respx.mock
async def test_list_open_issues_stops_at_max_items() -> None:
    respx.get(f"{BASE}/repos/o/r/issues").mock(
        return_value=httpx.Response(
            200, json=[{"number": n, "title": f"issue {n}", "state": "open"} for n in range(1, 6)]
        )
    )
    client = GitHubClient(base_url=BASE)
    issues = await list_open_issues(client, "o/r", max_items=3)
    assert [i.number for i in issues] == [1, 2, 3]
    await client.aclose()


@respx.mock
async def test_list_open_issues_requests_open_sorted_by_updated() -> None:
    route = respx.get(f"{BASE}/repos/o/r/issues").mock(return_value=httpx.Response(200, json=[]))
    client = GitHubClient(base_url=BASE)
    await list_open_issues(client, "o/r")
    params = route.calls.last.request.url.params
    assert params["state"] == "open"
    assert params["sort"] == "updated"
    assert params["direction"] == "desc"
    await client.aclose()


@respx.mock
async def test_list_open_issues_returns_empty_list_for_a_repo_with_no_open_issues() -> None:
    respx.get(f"{BASE}/repos/o/r/issues").mock(return_value=httpx.Response(200, json=[]))
    client = GitHubClient(base_url=BASE)
    assert await list_open_issues(client, "o/r") == []
    await client.aclose()
