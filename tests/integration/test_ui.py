"""The HTML UI routes: /ui/runs (list) and /ui/runs/{run_id} (review page).

Same httpx.ASGITransport + FakeLLM pattern as test_api.py — these are a
presentation layer over the same graph/store, so the fixture setup mirrors
that file exactly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from issue_to_patch.api import deps as api_deps
from issue_to_patch.api.app import app
from issue_to_patch.api.deps import get_graph_dependencies
from issue_to_patch.config import get_settings
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.persistence import Store
from issue_to_patch.processing import index_snapshot
from issue_to_patch.retrieval import RetrievalService

pytestmark = pytest.mark.integration

_FIX = {
    "message": "fix: correct add()",
    "edits": [
        {
            "path": "calculator.py",
            "old": "return a - b  # BUG: should be a + b",
            "new": "return a + b",
        }
    ],
}


def _queue_happy_path(llm: FakeLLM) -> None:
    llm.queue_structured({"search_queries": ["add returns wrong result"], "focus_areas": []})
    llm.queue_structured(
        {"hypotheses": [{"summary": "subtracts instead of adds", "confidence": 0.9}]}
    )
    llm.queue_structured(_FIX)


@pytest.fixture
async def client(
    fixture_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, Path, FakeLLM, Store]]:
    monkeypatch.setenv("ITP_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'ui.db'}")
    get_settings.cache_clear()
    # See test_api.py's client fixture: ASGITransport requests share one
    # rate-limit bucket across the whole pytest session; clear it so an
    # earlier test file can't trip a later test's 429.
    api_deps._rate_windows.clear()

    snap = create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/calc")
    store = Store(get_settings().database_url)
    store.create_all()
    index_snapshot(snap, store)

    llm = FakeLLM()

    def _override_deps() -> GraphDependencies:
        return GraphDependencies(
            llm=llm, retrieval=RetrievalService(store), store=store, settings=get_settings()
        )

    app.dependency_overrides[get_graph_dependencies] = _override_deps
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client, snap.root_path.parent, llm, store

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    api_deps._rate_windows.clear()


async def test_ui_routes_are_excluded_from_the_openapi_schema(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.get("/openapi.json")
    paths = response.json()["paths"]
    assert not any(p.startswith("/ui") for p in paths)


async def test_runs_list_page_shows_a_seeded_run(client: tuple) -> None:
    async_client, snap_dir, llm, _store = client
    _queue_happy_path(llm)
    started = await async_client.post(
        "/runs",
        json={
            "issue": "add() returns the wrong result",
            "snapshot": str(snap_dir),
            "scope": ["calculator.py"],
        },
    )
    run_id = started.json()["run_id"]

    page = await async_client.get("/ui/runs")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert run_id in page.text
    assert "AWAITING_HUMAN_REVIEW" in page.text
    # the "start a new investigation" form, wired to the real /runs/auto route
    assert 'id="new-issue-url"' in page.text
    assert 'id="start-auto-btn"' in page.text
    assert "fetch('/runs/auto'" in page.text


async def test_run_detail_page_shows_diagnosis_diff_and_decision_buttons(client: tuple) -> None:
    async_client, snap_dir, llm, _store = client
    _queue_happy_path(llm)
    started = await async_client.post(
        "/runs",
        json={
            "issue": "add() returns the wrong result",
            "snapshot": str(snap_dir),
            "scope": ["calculator.py"],
        },
    )
    run_id = started.json()["run_id"]

    page = await async_client.get(f"/ui/runs/{run_id}")
    assert page.status_code == 200
    body = page.text
    assert "subtracts instead of adds" in body
    assert "class='code add'" in body  # the colored diff rendered, not raw text
    assert "a + b" in body
    assert 'id="decide-approve"' in body  # awaiting human -> decision buttons present
    # decide() builds the real API route (/runs/<id>/approve) from
    # location.pathname at click-time rather than a baked-in per-page URL.
    assert "'/runs/' + runId + '/approve'" in body
    # the legacy free-text fields are present by default (no way to know
    # who's signed in on a plain page load) but wired to be hidden once a
    # real account is confirmed client-side.
    assert 'id="legacy-reviewer-fields"' in body
    assert 'id="whoami-status"' in body
    assert "refreshWhoami" in body


async def test_run_detail_page_after_approval_shows_final_state_not_buttons(
    client: tuple,
) -> None:
    async_client, snap_dir, llm, _store = client
    _queue_happy_path(llm)
    started = await async_client.post(
        "/runs",
        json={
            "issue": "add() returns the wrong result",
            "snapshot": str(snap_dir),
            "scope": ["calculator.py"],
        },
    )
    run_id = started.json()["run_id"]
    await async_client.post(f"/runs/{run_id}/approve", json={"decision": "approve"})

    page = await async_client.get(f"/ui/runs/{run_id}")
    assert page.status_code == 200
    assert "PATCH_VALIDATED" in page.text
    # id="decide-approve" is the actual button element; the bare string
    # "decide-approve" also appears in the page's <style> block (an ID
    # selector) even when the button itself isn't rendered.
    assert 'id="decide-approve"' not in page.text
    # the gap this was added to close: PATCH_VALIDATED must offer a next
    # step (build), not just dead-end on a status line.
    assert 'id="build-btn"' in page.text
    assert "buildRun()" in page.text


async def test_run_detail_page_shows_push_once_already_built(client: tuple) -> None:
    async_client, snap_dir, llm, _store = client
    _queue_happy_path(llm)
    started = await async_client.post(
        "/runs",
        json={
            "issue": "add() returns the wrong result",
            "snapshot": str(snap_dir),
            "scope": ["calculator.py"],
        },
    )
    run_id = started.json()["run_id"]
    await async_client.post(f"/runs/{run_id}/approve", json={"decision": "approve"})
    await async_client.post(f"/runs/{run_id}/build")

    page = await async_client.get(f"/ui/runs/{run_id}")
    assert page.status_code == 200
    assert 'id="push-btn"' in page.text
    assert 'id="build-btn"' not in page.text
    # the field to type a push remote directly, no server config required
    assert 'id="push-remote-url"' in page.text
    assert "remote_url: remoteUrl" in page.text
    # the "Create a Pull Request" section, present once a branch is built —
    # it never requires a push to have actually happened server-side
    # (that's never tracked), only that there's a branch to open a PR from.
    assert 'id="create-pr-btn"' in page.text
    assert 'id="pr-title"' in page.text
    assert 'id="github-token"' in page.text
    assert "fork_remote_url" in page.text


_AUTO_BASE = "https://api.github.com"
_AUTO_REPO = "acme/widget"


def _mock_github_for_auto() -> None:
    respx.get(f"{_AUTO_BASE}/repos/{_AUTO_REPO}/issues/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 7,
                "title": "add() is wrong",
                "body": "add() returns the wrong result",
                "state": "open",
                "labels": [],
                "user": {"login": "reporter"},
                "comments": 0,
                "html_url": f"https://github.com/{_AUTO_REPO}/issues/7",
            },
        )
    )
    respx.get(f"{_AUTO_BASE}/repos/{_AUTO_REPO}/issues/7/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{_AUTO_BASE}/repos/{_AUTO_REPO}").mock(
        return_value=httpx.Response(
            200,
            json={
                "full_name": _AUTO_REPO,
                "default_branch": "main",
                "visibility": "public",
                "language": "Python",
                "topics": [],
                "size": 1,
                "clone_url": f"https://github.com/{_AUTO_REPO}.git",
            },
        )
    )
    respx.get(f"{_AUTO_BASE}/repos/{_AUTO_REPO}/issues/7/timeline").mock(
        return_value=httpx.Response(200, json=[])
    )


@respx.mock
async def test_a_run_started_via_the_auto_route_is_visible_in_the_ui(
    client: tuple, fixture_repo: Path
) -> None:
    """Proves the UI's "start a new investigation" form (which calls
    POST /runs/auto) and the review page are wired to the same backend: a
    run started the way the browser would start it is immediately visible
    and reviewable, with no separate CLI step involved."""
    async_client, _snap_dir, llm, _store = client
    _mock_github_for_auto()
    _queue_happy_path(llm)

    started = await async_client.post(
        "/runs/auto",
        json={
            "issue_url": f"https://github.com/{_AUTO_REPO}/issues/7",
            "repo_source": str(fixture_repo),
            "scope": ["calculator.py"],
        },
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]

    list_page = await async_client.get("/ui/runs")
    assert run_id in list_page.text

    detail_page = await async_client.get(f"/ui/runs/{run_id}")
    assert detail_page.status_code == 200
    assert "subtracts instead of adds" in detail_page.text
    assert 'id="decide-approve"' in detail_page.text


async def test_run_detail_page_404s_for_an_unknown_run(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.get("/ui/runs/does-not-exist")
    assert response.status_code == 404


async def test_run_detail_page_shows_a_waiting_page_while_pending(client: tuple) -> None:
    """The gap this closes: ingest+index can take tens of seconds before
    the first LangGraph checkpoint exists - a pending run_id must show a
    friendly waiting page here, not a 404, and must auto-refresh so the
    human never has to manually reload."""
    from issue_to_patch.api import pending_runs

    async_client, _snap_dir, _llm, _store = client
    pending_runs.mark_stage("pending-run-1", "indexing")
    try:
        response = await async_client.get("/ui/runs/pending-run-1")
        assert response.status_code == 200
        assert "Indexing the codebase" in response.text
        assert '<meta http-equiv="refresh" content="3">' in response.text
    finally:
        pending_runs.clear("pending-run-1")


async def test_run_detail_page_shows_the_error_for_a_failed_pending_run(client: tuple) -> None:
    from issue_to_patch.api import pending_runs

    async_client, _snap_dir, _llm, _store = client
    pending_runs.mark_failed("pending-run-2", "not a github.com URL: bogus")
    try:
        response = await async_client.get("/ui/runs/pending-run-2")
        assert response.status_code == 200
        assert "Investigation failed" in response.text
        assert "not a github.com URL: bogus" in response.text
        assert "meta http-equiv=" not in response.text  # a failure stops auto-refreshing
    finally:
        pending_runs.clear("pending-run-2")


async def test_diff_with_angle_brackets_renders_without_breaking_the_page(
    client: tuple,
) -> None:
    """A real escaping check, not just the unit test's synthetic string:
    drive a full run where the actual proposed code contains '<'/'&'."""
    async_client, snap_dir, llm, _store = client
    llm.queue_structured({"search_queries": [], "focus_areas": []})
    llm.queue_structured({"hypotheses": [{"summary": "needs a guard", "confidence": 0.9}]})
    llm.queue_structured(
        {
            "message": "fix: add a guard",
            "edits": [
                {
                    "path": "calculator.py",
                    "old": "return a - b  # BUG: should be a + b",
                    "new": "return a + b if a < b else a & b",
                }
            ],
        }
    )
    started = await async_client.post(
        "/runs",
        json={"issue": "x", "snapshot": str(snap_dir), "scope": ["calculator.py"]},
    )
    run_id = started.json()["run_id"]

    page = await async_client.get(f"/ui/runs/{run_id}")
    assert page.status_code == 200
    assert "a &lt; b" in page.text
    assert "a &amp; b" in page.text
    assert "a < b" not in page.text  # never raw/unescaped in the HTML


async def test_runs_list_page_has_the_repo_browse_form(client: tuple) -> None:
    async_client, *_ = client
    page = await async_client.get("/ui/runs")
    assert 'id="repo-url"' in page.text
    assert 'action="/ui/repos/issues"' in page.text


async def test_resolve_repo_issues_redirects_to_the_canonical_path(client: tuple) -> None:
    async_client, *_ = client
    resp = await async_client.get(
        "/ui/repos/issues",
        params={"repo": "https://github.com/acme/widget"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/repos/acme/widget/issues"


async def test_resolve_repo_issues_also_accepts_an_scp_style_url(client: tuple) -> None:
    async_client, *_ = client
    resp = await async_client.get(
        "/ui/repos/issues",
        params={"repo": "git@github.com:acme/widget.git"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/repos/acme/widget/issues"


async def test_resolve_repo_issues_shows_a_friendly_error_for_a_non_github_url(
    client: tuple,
) -> None:
    async_client, *_ = client
    resp = await async_client.get("/ui/repos/issues", params={"repo": "not a url"})
    assert resp.status_code == 200
    assert "doesn't look like a github.com repository URL" in resp.text


@respx.mock
async def test_repo_issues_page_lists_open_issues_and_filters_prs(client: tuple) -> None:
    async_client, *_ = client
    respx.get("https://api.github.com/repos/acme/widget/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 9, "title": "a pull request", "pull_request": {}, "state": "open"},
                {
                    "number": 5,
                    "title": "null pointer on save",
                    "state": "open",
                    "labels": [{"name": "bug"}],
                    "user": {"login": "dev"},
                    "html_url": "https://github.com/acme/widget/issues/5",
                },
            ],
        )
    )
    page = await async_client.get("/ui/repos/acme/widget/issues")
    assert page.status_code == 200
    assert "null pointer on save" in page.text
    assert "a pull request" not in page.text
    assert "data-issue-url='https://github.com/acme/widget/issues/5'" in page.text
    assert "run-issue-btn" in page.text


@respx.mock
async def test_repo_issues_page_shows_an_empty_state_for_no_open_issues(client: tuple) -> None:
    async_client, *_ = client
    respx.get("https://api.github.com/repos/acme/quiet/issues").mock(
        return_value=httpx.Response(200, json=[])
    )
    page = await async_client.get("/ui/repos/acme/quiet/issues")
    assert page.status_code == 200
    assert "No open issues" in page.text


@respx.mock
async def test_repo_issues_page_shows_a_friendly_error_for_a_nonexistent_repo(
    client: tuple,
) -> None:
    async_client, *_ = client
    respx.get("https://api.github.com/repos/acme/ghost/issues").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    page = await async_client.get("/ui/repos/acme/ghost/issues")
    assert page.status_code == 200
    assert "Could not list issues" in page.text
