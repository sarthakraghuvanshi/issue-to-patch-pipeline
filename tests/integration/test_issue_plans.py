"""Plan previews are grounded, resumable, read-only, and usable as run guidance."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from issue_to_patch.api.app import app
from issue_to_patch.api.deps import get_graph_dependencies
from issue_to_patch.config import get_settings
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.ingestion.fetch import list_issue_page
from issue_to_patch.ingestion.github import GitHubClient
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.persistence import Store
from issue_to_patch.persistence.models import IssuePlanRow
from issue_to_patch.planning.models import SavedPlan
from issue_to_patch.planning.service import (
    generate_plan,
    interrupt_unfinished,
    read_plan,
    retrieve_sources,
    write_plan,
)
from issue_to_patch.processing import index_snapshot
from issue_to_patch.retrieval import RetrievalService

pytestmark = pytest.mark.integration


def result(refs: list[str] | None = None) -> dict:
    return {
        "understanding": "Addition subtracts instead of adding.",
        "steps": [
            {"text": text, "references": refs or []}
            for text in (
                "Reproduce the incorrect sum.",
                "Inspect the operator.",
                "Add regression coverage.",
            )
        ],
        "verification": ["Run the calculator tests."],
        "open_questions": [],
    }


@pytest.fixture
async def context(tmp_path, fixture_repo, monkeypatch):
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'plans.db'}")
    monkeypatch.setenv("ITP_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.delenv("ITP_API_KEY", raising=False)
    get_settings.cache_clear()
    store = Store(get_settings().database_url)
    store.create_all()
    llm = FakeLLM()
    deps = GraphDependencies(
        llm=llm, store=store, settings=get_settings(), retrieval=RetrievalService(store)
    )
    snapshot = create_snapshot(str(fixture_repo), tmp_path / "snapshot", repo_name="acme/calc")
    index_snapshot(snapshot, store)
    saved = SavedPlan(
        snapshot=str(snapshot.root_path.parent),
        commit_sha=snapshot.commit_sha,
        issue_text="add returns the wrong sum",
    )
    with store.session() as session:
        session.add(
            IssuePlanRow(
                plan_id="saved",
                request_id="saved",
                owner="local",
                issue_url="https://github.com/acme/calc/issues/1",
                status="interrupted",
                payload=saved.model_dump(mode="json"),
                cost_usd=0,
                created_at=datetime.now(UTC),
            )
        )
    app.dependency_overrides[get_graph_dependencies] = lambda: deps
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, deps, llm, snapshot
    app.dependency_overrides.clear()
    get_settings.cache_clear()


async def test_regeneration_source_and_duplicate_submission(context):
    client, deps, llm, snapshot = context
    llm.queue_structured(result())
    response = await client.post("/issue-plans/saved/regenerate")
    assert response.status_code == 202
    data = (await client.get("/issue-plans/saved")).json()
    assert data["status"] == "ready"
    assert data["commit_sha"] == snapshot.commit_sha
    assert data["sources"]
    source = data["sources"][0]
    response = await client.get(
        "/issue-plans/saved/source", params={"chunk_id": source["chunk_id"]}
    )
    assert response.json() == source
    assert (
        await client.get("/issue-plans/saved/source", params={"chunk_id": "../../.env"})
    ).status_code == 404
    assert not deps.store.list_runs()
    assert not list(deps.settings.artifacts_dir.glob("**/fix.patch"))
    # Idempotency: existing request returns the same job without another model call.
    response = await client.post(
        "/issue-plans", json={"issue_url": "acme/calc#1", "request_id": "saved"}
    )
    assert response.json() == {"plan_id": "saved"}
    assert len(llm.calls) == 1
    # Bad model references fail without discarding the previous preview.
    llm.queue_structured(result(["invented-file"]))
    await client.post("/issue-plans/saved/regenerate")
    failed = (await client.get("/issue-plans/saved")).json()
    assert failed["status"] == "failed"
    assert failed["result"] == data["result"]
    assert failed["sources"] == data["sources"]


async def test_auth_and_missing_plans(context, monkeypatch):
    client, _deps, llm, _ = context
    monkeypatch.setenv("ITP_API_KEY", "workspace-secret")
    get_settings.cache_clear()
    assert (await client.get("/issue-plans/saved")).status_code == 401
    headers = {"Authorization": "Bearer workspace-secret"}
    # Shared-key identity cannot read a local identity's plans.
    assert (await client.get("/issue-plans/saved", headers=headers)).status_code == 404
    assert (await client.get("/issue-plans/missing", headers=headers)).status_code == 404
    assert not llm.calls


async def test_invalid_issue_and_unready_handoff(context):
    client, _, _, _ = context
    assert (
        await client.post("/issue-plans", json={"issue_url": "/etc/passwd", "request_id": "bad"})
    ).status_code == 422
    response = await client.post(
        "/runs/auto", json={"issue_url": "acme/calc#1", "plan_id": "saved"}
    )
    assert response.status_code == 409


async def test_handoff_reuses_snapshot_and_guidance(context, monkeypatch):
    client, deps, llm, snapshot = context
    llm.queue_structured(result())
    await generate_plan("saved", deps)
    captured = {}

    def investigate(issue, repo, dependencies, checkpoint, **kwargs):
        captured.update(kwargs)
        captured["sha"] = repo.commit_sha

    monkeypatch.setattr("issue_to_patch.api.routes.auto_investigate", investigate)
    response = await client.post(
        "/runs/auto",
        json={"issue_url": "https://github.com/acme/calc/issues/1", "plan_id": "saved"},
    )
    assert response.status_code == 202
    assert captured["sha"] == snapshot.commit_sha
    assert "Add regression coverage" in captured["provisional_plan"]
    assert "wrong sum" in captured["plan_issue_text"]
    assert (
        await client.post("/runs/auto", json={"issue_url": "acme/other#2", "plan_id": "saved"})
    ).status_code == 409
    assert (
        await client.post(
            "/runs/auto", json={"issue_url": "acme/calc#1", "plan_id": "saved", "ref": "other"}
        )
    ).status_code == 409


async def test_interruption_and_input_budget(context):
    _, deps, llm, snapshot = context
    with deps.store.session() as session:
        row = session.get(IssuePlanRow, "saved")
        row.status = "writing"
    interrupt_unfinished(deps.store)
    assert read_plan(deps.store, "saved").status == "interrupted"
    saved = SavedPlan(issue_text="add returns wrong sum")
    saved.sources = retrieve_sources(snapshot, saved, deps)
    assert len(saved.sources) <= 12
    assert sum(len(s.content) for s in saved.sources) <= 32000
    llm.queue_structured(result())
    empty_result = write_plan(SavedPlan(issue_text="Unknown bug"), deps)
    assert empty_result.open_questions


@respx.mock
async def test_initial_plan_fetch_bounds_and_no_patch(context, fixture_repo, monkeypatch):
    client, deps, llm, _ = context
    respx.get("https://api.github.com/repos/acme/calc/issues/2").mock(
        return_value=httpx.Response(
            200, json={"number": 2, "title": "add", "body": "x" * 14000, "comments": 125}
        )
    )
    comments_route = respx.get("https://api.github.com/repos/acme/calc/issues/2/comments").mock(
        return_value=httpx.Response(200, json=[{"body": "c" * 900} for _ in range(25)])
    )
    respx.get("https://api.github.com/repos/acme/calc").mock(
        return_value=httpx.Response(
            200,
            json={
                "full_name": "acme/calc",
                "default_branch": "main",
                "clone_url": str(fixture_repo),
            },
        )
    )
    # Fixture branch name varies across git installations.
    original = create_snapshot
    monkeypatch.setattr(
        "issue_to_patch.planning.service.create_snapshot",
        lambda source, directory, **kw: original(source, directory, repo_name=kw["repo_name"]),
    )
    llm.queue_structured(result())
    response = await client.post(
        "/issue-plans", json={"issue_url": "acme/calc#2", "request_id": "fresh"}
    )
    assert response.status_code == 202
    plan_id = response.json()["plan_id"]
    data = (await client.get("/issue-plans/" + plan_id)).json()
    assert data["status"] == "ready", data
    saved = SavedPlan.model_validate(read_plan(deps.store, plan_id).payload)
    assert len(saved.issue_text) == 12000
    assert len(saved.discussion) == 12000
    assert saved.context_truncated
    assert comments_route.calls[0].request.url.params["page"] == "2"
    assert not deps.store.list_runs()


@respx.mock
async def test_pagination_across_pr_heavy_pages():
    route = respx.get("https://api.github.com/repos/acme/calc/issues")
    pages = {
        "1": [{"number": i, "title": "pr", "pull_request": {}} for i in range(100)],
        "2": [{"number": i, "title": str(i)} for i in range(100, 140)],
    }

    def respond(request):
        page = request.url.params["page"]
        return httpx.Response(
            200,
            json=pages[page],
            headers={"Link": '<https://api.github.com/repos/acme/calc/issues?page=2>; rel="next"'}
            if page == "1"
            else {},
        )

    route.mock(side_effect=respond)
    async with GitHubClient() as github:
        first, after, before = await list_issue_page(github, "acme/calc")
        assert len(first) == 30 and before is None
        second, end, back = await list_issue_page(github, "acme/calc", after)
        assert [i.number for i in second] == list(range(130, 140))
        assert end is None
        again, _, _ = await list_issue_page(github, "acme/calc", back)
        assert [i.number for i in again] == [i.number for i in first]
        with pytest.raises(ValueError):
            await list_issue_page(github, "acme/calc", "bad cursor")


async def test_invalid_model_output_and_provider_failure_preserve_preview(context):
    client, deps, llm, _ = context
    llm.queue_structured(result())
    await generate_plan("saved", deps)
    before = read_plan(deps.store, "saved").payload
    llm.queue_structured({"understanding": "incomplete output"})
    await client.post("/issue-plans/saved/regenerate")
    assert read_plan(deps.store, "saved").status == "failed"
    assert read_plan(deps.store, "saved").payload == before
    # No FakeLLM response queued: simulate provider failure without a real API request.
    await client.post("/issue-plans/saved/regenerate")
    assert read_plan(deps.store, "saved").status == "failed"
    assert read_plan(deps.store, "saved").payload == before


async def test_active_job_is_not_enqueued_twice_and_missing_snapshot_is_rejected(context):
    client, deps, llm, _ = context
    with deps.store.session() as session:
        row = session.get(IssuePlanRow, "saved")
        row.status = "preparing"
    await client.post("/issue-plans/saved/regenerate")
    assert not llm.calls
    llm.queue_structured(result())
    await generate_plan("saved", deps)
    with deps.store.session() as session:
        row = session.get(IssuePlanRow, "saved")
        data = SavedPlan.model_validate(row.payload)
        data.snapshot = str(deps.settings.artifacts_dir / "missing-snapshot")
        row.payload = data.model_dump(mode="json")
    response = await client.post(
        "/runs/auto", json={"issue_url": "acme/calc#1", "plan_id": "saved"}
    )
    assert response.status_code == 409
    assert not deps.store.list_runs()


@respx.mock
async def test_conditional_github_page_preserves_next_link():
    route = respx.get("https://api.github.com/repos/acme/calc/issues").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[],
                headers={
                    "ETag": "v1",
                    "Link": '<https://api.github.com/repos/acme/calc/issues?page=2>; rel="next"',
                },
            ),
            httpx.Response(304),
        ]
    )
    async with GitHubClient() as github:
        _, next_page = await github.get_page("/repos/acme/calc/issues", params={"page": 1})
        assert next_page
        _, next_page = await github.get_page("/repos/acme/calc/issues", params={"page": 1})
        assert next_page
    assert route.calls[1].request.headers["If-None-Match"] == "v1"


def test_shared_snapshot_build_directories_are_distinct():
    from pathlib import Path
    from types import SimpleNamespace

    from issue_to_patch.api._common import branch_target

    snapshot = SimpleNamespace(root_path=Path("/artifacts/issue-plans/plan/snapshot/repo"))
    first, _ = branch_target("first", snapshot, shared_snapshot=True)
    second, _ = branch_target("second", snapshot, shared_snapshot=True)
    assert first != second
    assert first.name == "first" and second.name == "second"
    legacy, _ = branch_target("legacy", snapshot)
    assert legacy.name == "branch"


async def test_plan_guidance_is_provisional_and_issue_text_is_preserved(context):
    from issue_to_patch.graph import nodes
    from issue_to_patch.graph.state import new_state

    _, deps, llm, _ = context
    state = new_state(run_id="guided", issue_ref="acme/calc#1")
    state["plan_issue_text"] = "Wrong sum\nadd(2, 3) returns -1"
    state["provisional_plan"] = "Check negative inputs before changing the operator."
    state.update(nodes.normalize_request(state, deps))
    assert "returns -1" in state["issue"].body
    llm.queue_structured({"search_queries": ["add negative input"], "focus_areas": []})
    nodes.plan_investigation(state, deps)
    sent = llm.calls[0][-1].content
    assert "verify independently" in sent
    assert "Check negative inputs" in sent


async def test_two_investigations_can_build_from_the_same_plan_snapshot(context):
    from issue_to_patch.api._common import branch_target
    from issue_to_patch.patching.models import EditPlan
    from issue_to_patch.patching.worktree import generate_patch, materialize_branch

    _, _, _, snapshot = context
    patch = generate_patch(
        snapshot,
        EditPlan.model_validate(
            {
                "message": "fix sum",
                "edits": [
                    {
                        "path": "calculator.py",
                        "old": "return a - b  # BUG: should be a + b",
                        "new": "return a + b",
                    }
                ],
            }
        ),
    )
    for run_id in ("plan-run-one", "plan-run-two"):
        directory, branch = branch_target(run_id, snapshot, shared_snapshot=True)
        materialize_branch(snapshot, patch, directory, branch)
        assert "return a + b" in (directory / "calculator.py").read_text()


async def test_exhausted_provider_credits_show_actionable_error(context, monkeypatch):
    import httpx
    from openai import RateLimitError

    _client, deps, llm, _ = context

    def exhausted(*args, **kwargs):
        raise RateLimitError(
            "private provider details",
            response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com")),
            body={"code": "credit_balance_exhausted"},
        )

    monkeypatch.setattr(llm, "structured", exhausted)
    await generate_plan("saved", deps)
    row = read_plan(deps.store, "saved")
    assert row.status == "failed"
    assert "API credits" in row.error
    assert "private provider details" not in row.error


async def test_saved_plan_is_discovered_and_reopened_without_generation(context):
    client, deps, llm, _ = context
    llm.queue_structured(result())
    await generate_plan("saved", deps)
    response = await client.get(
        "/issue-plans", params=[("issue_url", "acme/calc#1"), ("issue_url", "acme/calc#2")]
    )
    assert response.status_code == 200
    assert response.json() == [
        {
            "plan_id": "saved",
            "issue_url": "https://github.com/acme/calc/issues/1",
            "status": "ready",
            "has_result": True,
        }
    ]
    for request_id in ("new-page", "other-tab"):
        response = await client.post(
            "/issue-plans", json={"issue_url": "acme/calc#1", "request_id": request_id}
        )
        assert response.json() == {"plan_id": "saved"}
    assert len(llm.calls) == 1
    assert read_plan(deps.store, "saved").status == "ready"


async def test_lookup_is_owned_bounded_and_never_restarts_failed_plans(context, monkeypatch):
    client, deps, llm, _ = context
    with deps.store.session() as session:
        row = session.get(IssuePlanRow, "saved")
        row.status = "failed"
    response = await client.post("/issue-plans", json={"issue_url": "acme/calc#1"})
    assert response.json() == {"plan_id": "saved"}
    assert read_plan(deps.store, "saved").status == "failed"
    assert not llm.calls
    assert (await client.get("/issue-plans", params={"issue_url": "../bad"})).status_code == 422
    assert (
        await client.get("/issue-plans", params=[("issue_url", "acme/calc#1")] * 31)
    ).status_code == 422
    monkeypatch.setenv("ITP_API_KEY", "secret")
    get_settings.cache_clear()
    assert (
        await client.get("/issue-plans", params={"issue_url": "acme/calc#1"})
    ).status_code == 401
    response = await client.get(
        "/issue-plans",
        params={"issue_url": "acme/calc#1"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.json() == []


def test_concurrent_first_clicks_share_one_job(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi import BackgroundTasks

    from issue_to_patch.api.deps import CurrentUser
    from issue_to_patch.api.issue_plans import create_plan
    from issue_to_patch.graph.deps import build_dependencies
    from issue_to_patch.planning.models import PlanRequest

    store = Store(f"sqlite+pysqlite:///{tmp_path / 'concurrent.db'}")
    store.create_all()
    deps = build_dependencies(store=store, llm=FakeLLM())
    barrier = Barrier(2)

    def click():
        tasks = BackgroundTasks()
        barrier.wait()
        response = create_plan(
            PlanRequest(issue_url="acme/calc#42"),
            tasks,
            deps,
            CurrentUser(username="local", role="gatekeeper", authenticated=False),
        )
        return response.plan_id, len(tasks.tasks)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: click(), range(2)))
    assert results[0][0] == results[1][0]
    assert sum(count for _, count in results) == 1
