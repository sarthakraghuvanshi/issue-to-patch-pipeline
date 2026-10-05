"""The FastAPI service boundary, end to end: httpx.ASGITransport, no real
server, FakeLLM, a real (fixture) indexed repo.
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
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'api.db'}")
    get_settings.cache_clear()
    # ASGITransport test requests have no real client address, so rate_limit
    # (api/deps.py) buckets every one of them under the same "unknown" key —
    # a module-level dict shared across the whole pytest session. Without
    # clearing it, enough tests in one run eventually trip the 60/minute
    # default and a later, unrelated test starts failing with 429.
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


async def test_openapi_schema_lists_every_endpoint(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert set(paths) == {
        "/runs",
        "/runs/auto",
        "/runs/{run_id}",
        "/runs/{run_id}/approve",
        "/runs/{run_id}/retrieval",
        "/runs/{run_id}/patch",
        "/runs/{run_id}/audit",
        "/runs/{run_id}/build",
        "/runs/{run_id}/push",
        "/users/me",
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

    approved = await async_client.post(
        f"/runs/{run_id}/approve", json={"decision": "approve", "reviewer": "alice"}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "PATCH_VALIDATED"
    assert store.get_run_state(run_id) == "PATCH_VALIDATED"

    audit = await async_client.get(f"/runs/{run_id}/audit")
    assert audit.status_code == 200
    trail = audit.json()
    assert trail["tool_calls_chain_valid"] is True
    assert trail["decisions_chain_valid"] is True
    assert [d["reviewer"] for d in trail["decisions"]] == ["alice"]
    assert any(t["tool"] == "validate_patch" for t in trail["tool_calls"])


async def test_build_after_approval_creates_a_persistent_branch(client: tuple) -> None:
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

    built = await async_client.post(f"/runs/{run_id}/build")
    assert built.status_code == 200, built.text
    body = built.json()
    assert body["branch_name"] == f"itp/{run_id}"
    branch_dir = Path(body["branch_dir"])
    assert branch_dir.exists()
    assert "a + b" in (branch_dir / "calculator.py").read_text("utf-8")

    # idempotent: calling it again reports the same branch, doesn't error
    built_again = await async_client.post(f"/runs/{run_id}/build")
    assert built_again.status_code == 200
    assert built_again.json()["sha"] == body["sha"]


async def test_build_before_patch_validated_is_a_conflict(client: tuple) -> None:
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
    response = await async_client.post(f"/runs/{run_id}/build")
    assert response.status_code == 409


async def test_push_before_build_is_a_conflict(client: tuple) -> None:
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

    response = await async_client.post(f"/runs/{run_id}/push")
    assert response.status_code == 409


async def test_push_without_a_configured_remote_is_a_bad_request(client: tuple) -> None:
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

    response = await async_client.post(f"/runs/{run_id}/push")
    assert response.status_code == 400
    assert "ITP_PUSH_REMOTE_URL" in response.text


async def test_push_to_a_configured_local_bare_remote_succeeds(
    client: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

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

    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
    monkeypatch.setenv("ITP_PUSH_REMOTE_URL", f"file://{bare}")
    get_settings.cache_clear()

    pushed = await async_client.post(f"/runs/{run_id}/push")
    assert pushed.status_code == 200, pushed.text
    body = pushed.json()
    assert body["ok"] is True

    ls_remote = subprocess.run(
        ["git", "ls-remote", str(bare), f"itp/{run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert f"itp/{run_id}" in ls_remote


async def test_push_with_a_remote_given_in_the_request_needs_no_server_config(
    client: tuple, tmp_path: Path
) -> None:
    """The UI lets a user type a remote directly (remembered in their own
    browser) instead of requiring ITP_PUSH_REMOTE_URL on the server at all."""
    import subprocess

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

    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)

    # Deliberately NOT setting ITP_PUSH_REMOTE_URL anywhere for this test.
    pushed = await async_client.post(f"/runs/{run_id}/push", json={"remote_url": f"file://{bare}"})
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["ok"] is True

    ls_remote = subprocess.run(
        ["git", "ls-remote", str(bare), f"itp/{run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert f"itp/{run_id}" in ls_remote


async def test_audit_endpoint_404s_for_an_unknown_run(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.get("/runs/does-not-exist/audit")
    assert response.status_code == 404


async def test_approve_rejects_an_invalid_role(client: tuple) -> None:
    async_client, snap_dir, llm, _store = client
    _queue_happy_path(llm)
    started = await async_client.post(
        "/runs", json={"issue": "add() bug", "snapshot": str(snap_dir)}
    )
    run_id = started.json()["run_id"]

    response = await async_client.post(
        f"/runs/{run_id}/approve", json={"decision": "approve", "role": "wizard"}
    )
    assert response.status_code == 422


async def test_a_risky_patch_needs_the_gatekeeper_role_via_the_api(client: tuple) -> None:
    async_client, snap_dir, llm, store = client
    llm.queue_structured({"search_queries": [], "focus_areas": []})
    llm.queue_structured({"hypotheses": [{"summary": "ci needs a fix", "confidence": 0.9}]})
    llm.queue_structured(
        {
            "message": "fix: adjust ci",
            "edits": [{"path": ".github/workflows/deploy.yml", "old": "", "new": "name: deploy\n"}],
        }
    )
    started = await async_client.post(
        "/runs",
        json={"issue": "ci is broken", "snapshot": str(snap_dir), "scope": [".github/**"]},
    )
    run_id = started.json()["run_id"]

    denied = await async_client.post(
        f"/runs/{run_id}/approve",
        json={"decision": "approve", "reviewer": "bob", "role": "auditor"},
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == "PATCH_REQUIRES_HUMAN_REVIEW"
    assert store.get_run_state(run_id) == "PATCH_REQUIRES_HUMAN_REVIEW"


async def test_a_real_auditor_account_cannot_lie_its_way_to_gatekeeper(client: tuple) -> None:
    """The actual security fix: before per-user accounts, anyone holding the
    one shared key could just type role=gatekeeper in the request body and
    clear a risky patch. A real account's role must win regardless of what
    the body claims."""
    async_client, snap_dir, llm, store = client
    llm.queue_structured({"search_queries": [], "focus_areas": []})
    llm.queue_structured({"hypotheses": [{"summary": "ci needs a fix", "confidence": 0.9}]})
    llm.queue_structured(
        {
            "message": "fix: adjust ci",
            "edits": [{"path": ".github/workflows/deploy.yml", "old": "", "new": "name: deploy\n"}],
        }
    )
    started = await async_client.post(
        "/runs",
        json={"issue": "ci is broken", "snapshot": str(snap_dir), "scope": [".github/**"]},
    )
    run_id = started.json()["run_id"]

    auditor_key = store.create_user(username="eve", role="auditor")
    denied = await async_client.post(
        f"/runs/{run_id}/approve",
        json={"decision": "approve", "reviewer": "someone-else", "role": "gatekeeper"},
        headers={"Authorization": f"Bearer {auditor_key}"},
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == "PATCH_REQUIRES_HUMAN_REVIEW"
    assert store.get_run_state(run_id) == "PATCH_REQUIRES_HUMAN_REVIEW"

    decisions = store.list_human_decisions(run_id)
    assert decisions[-1].reviewer == "eve"  # the real identity, not the body's lie
    assert decisions[-1].role == "auditor"
    assert decisions[-1].authenticated is True


async def test_a_real_gatekeeper_account_clears_a_risky_patch(client: tuple) -> None:
    async_client, snap_dir, llm, store = client
    llm.queue_structured({"search_queries": [], "focus_areas": []})
    llm.queue_structured({"hypotheses": [{"summary": "ci needs a fix", "confidence": 0.9}]})
    llm.queue_structured(
        {
            "message": "fix: adjust ci",
            "edits": [{"path": ".github/workflows/deploy.yml", "old": "", "new": "name: deploy\n"}],
        }
    )
    started = await async_client.post(
        "/runs",
        json={"issue": "ci is broken", "snapshot": str(snap_dir), "scope": [".github/**"]},
    )
    run_id = started.json()["run_id"]

    gatekeeper_key = store.create_user(username="alice", role="gatekeeper")
    approved = await async_client.post(
        f"/runs/{run_id}/approve",
        json={"decision": "approve"},
        headers={"Authorization": f"Bearer {gatekeeper_key}"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "PATCH_VALIDATED"

    decisions = store.list_human_decisions(run_id)
    assert decisions[-1].reviewer == "alice"
    assert decisions[-1].authenticated is True


async def test_whoami_reports_a_real_account(client: tuple) -> None:
    async_client, _snap_dir, _llm, store = client
    api_key = store.create_user(username="alice", role="gatekeeper")
    response = await async_client.get("/users/me", headers={"Authorization": f"Bearer {api_key}"})
    assert response.status_code == 200
    body = response.json()
    assert body == {"username": "alice", "role": "gatekeeper", "authenticated": True}


async def test_whoami_reports_the_local_fallback_when_no_key_is_sent(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.get("/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False


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
async def test_auto_run_ingests_indexes_and_investigates_from_a_bare_url(
    client: tuple, fixture_repo: Path
) -> None:
    async_client, _snap_dir, llm, store = client
    _mock_github_for_auto()
    _queue_happy_path(llm)

    response = await async_client.post(
        "/runs/auto",
        json={
            "issue_url": f"https://github.com/{_AUTO_REPO}/issues/7",
            "repo_source": str(fixture_repo),
            "scope": ["calculator.py"],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "AWAITING_HUMAN_REVIEW"
    assert body["hypothesis"]["summary"] == "subtracts instead of adds"
    assert store.get_run_state(body["run_id"]) is not None


@respx.mock
async def test_auto_run_with_a_bad_issue_reference_is_a_bad_request(client: tuple) -> None:
    async_client, _snap_dir, _llm, _store = client
    response = await async_client.post("/runs/auto", json={"issue_url": "not a real reference"})
    assert response.status_code == 400


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
