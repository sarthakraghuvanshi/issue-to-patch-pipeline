"""The FastAPI service boundary, end to end: httpx.ASGITransport, no real
server, FakeLLM, a real (fixture) indexed repo.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

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
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'api.db'}")
    get_settings.cache_clear()

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


async def test_openapi_schema_lists_every_endpoint(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert set(paths) == {
        "/runs",
        "/runs/{run_id}",
        "/runs/{run_id}/approve",
        "/runs/{run_id}/retrieval",
        "/runs/{run_id}/patch",
        "/search",
        "/validate-patch",
    }


async def test_full_run_pauses_then_approves_to_patch_validated(client: tuple) -> None:
    async_client, snap_dir, llm, store = client
    _queue_happy_path(llm)

    started = await async_client.post(
        "/runs",
        json={
            "issue": "add() returns the wrong result",
            "snapshot": str(snap_dir),
            "scope": ["calculator.py"],
        },
    )
    assert started.status_code == 201
    body = started.json()
    run_id = body["run_id"]
    assert body["status"] == "AWAITING_HUMAN_REVIEW"
    assert body["hypothesis"]["summary"] == "subtracts instead of adds"
    assert body["hypothesis"]["cites"]

    fetched = await async_client.get(f"/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "AWAITING_HUMAN_REVIEW"

    patch = await async_client.get(f"/runs/{run_id}/patch")
    assert patch.status_code == 200
    assert "calculator.py" in patch.json()["changed_files"]
    assert "a + b" in patch.json()["patch_text"]

    retrieval = await async_client.get(f"/runs/{run_id}/retrieval")
    assert retrieval.status_code == 200
    assert any(e["kind"] == "code" for e in retrieval.json()["evidence"])

    approved = await async_client.post(f"/runs/{run_id}/approve", json={"decision": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "PATCH_VALIDATED"
    assert store.get_run_state(run_id) == "PATCH_VALIDATED"


async def test_approve_before_awaiting_review_is_a_conflict(client: tuple) -> None:
    async_client, snap_dir, llm, _store = client
    _queue_happy_path(llm)
    started = await async_client.post(
        "/runs",
        json={"issue": "add() bug", "snapshot": str(snap_dir), "scope": ["calculator.py"]},
    )
    run_id = started.json()["run_id"]
    await async_client.post(f"/runs/{run_id}/approve", json={"decision": "approve"})

    second = await async_client.post(f"/runs/{run_id}/approve", json={"decision": "approve"})
    assert second.status_code == 409


async def test_approve_with_an_invalid_decision_is_rejected(client: tuple) -> None:
    async_client, snap_dir, llm, _store = client
    _queue_happy_path(llm)
    started = await async_client.post(
        "/runs", json={"issue": "add() bug", "snapshot": str(snap_dir)}
    )
    run_id = started.json()["run_id"]

    response = await async_client.post(f"/runs/{run_id}/approve", json={"decision": "maybe"})
    assert response.status_code == 422


async def test_get_run_404s_for_an_unknown_run_id(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.get("/runs/does-not-exist")
    assert response.status_code == 404


async def test_a_paused_run_survives_a_simulated_process_restart(client: tuple) -> None:
    """The whole point of the durable checkpointer: not just a different
    *request*, but a completely fresh SqliteSaver + sqlite3 connection reading
    the same file back — what actually happens across a real process restart.
    """
    async_client, snap_dir, llm, store = client
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

    # Drop the cached checkpointer (and its open sqlite3.Connection) so the
    # next request has to open a brand-new one from the file on disk.
    api_deps._checkpointer_cache.clear()

    fetched = await async_client.get(f"/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "AWAITING_HUMAN_REVIEW"

    approved = await async_client.post(f"/runs/{run_id}/approve", json={"decision": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "PATCH_VALIDATED"
    assert store.get_run_state(run_id) == "PATCH_VALIDATED"


async def test_start_run_with_an_unindexed_snapshot_is_a_bad_request(
    client: tuple, tmp_path: Path, fixture_repo: Path
) -> None:
    async_client, _snap_dir, _llm, _store = client
    other = create_snapshot(str(fixture_repo), tmp_path / "other-snap", repo_name="acme/other")
    response = await async_client.post(
        "/runs", json={"issue": "x", "snapshot": str(other.root_path.parent)}
    )
    assert response.status_code == 400


async def test_start_run_with_a_bad_snapshot_path_is_a_bad_request(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.post(
        "/runs", json={"issue": "x", "snapshot": "/no/such/snapshot"}
    )
    assert response.status_code == 400


async def test_search_endpoint_returns_ranked_results(client: tuple) -> None:
    async_client, snap_dir, _llm, _store = client
    from issue_to_patch.ingestion.snapshot import load_snapshot

    snap = load_snapshot(snap_dir)
    response = await async_client.post(
        "/search",
        json={
            "query": "add returns wrong result",
            "repo": snap.repo,
            "commit_sha": snap.commit_sha,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["results"]
    assert any(r["path"] == "calculator.py" for r in body["results"])


async def test_search_endpoint_404s_for_an_unindexed_repo(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.post(
        "/search", json={"query": "x", "repo": "no/such", "commit_sha": "0" * 40}
    )
    assert response.status_code == 404


async def test_validate_patch_endpoint_reports_a_syntax_failure(client: tuple) -> None:
    async_client, snap_dir, _llm, _store = client
    from issue_to_patch.ingestion.snapshot import load_snapshot

    snap = load_snapshot(snap_dir)
    response = await async_client.post(
        "/validate-patch",
        json={
            "snapshot": str(snap_dir),
            "base_sha": snap.commit_sha,
            "commit_sha": snap.commit_sha,
            "changed_files": ["calculator.py"],
            "patch_text": "not a real diff",
            "allowed_scope": ["calculator.py"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "PATCH_REJECTED"
    assert any(c["name"] == "syntax" and c["status"] == "fail" for c in body["checks"])
