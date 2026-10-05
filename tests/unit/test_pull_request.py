"""create_pull_request: head/base construction, reusing the repository.json
already written by ingest instead of calling GitHub again to look it up."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from issue_to_patch.ingestion.errors import GitHubAPIError
from issue_to_patch.pull_request import ForkRemoteNotGitHub, create_pull_request

BASE = "https://api.github.com"


def _write_repository_json(run_dir: Path, *, full_name: str, default_branch: str) -> None:
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "repository.json").write_text(
        json.dumps(
            {"_meta": {}, "data": {"full_name": full_name, "default_branch": default_branch}}
        ),
        "utf-8",
    )


@respx.mock
async def test_cross_repo_pr_uses_owner_prefixed_head(tmp_path: Path) -> None:
    _write_repository_json(tmp_path, full_name="upstream-owner/widget", default_branch="main")
    route = respx.post(f"{BASE}/repos/upstream-owner/widget/pulls").mock(
        return_value=httpx.Response(201, json={"number": 5, "html_url": "https://x/5"})
    )

    result = await create_pull_request(
        run_dir=tmp_path,
        branch_name="itp/abc123",
        fork_remote_url="https://github.com/alice/widget.git",
        title="Fix the bug",
        body="Description",
        base=None,
        token="tok",
    )

    assert result.number == 5
    assert result.url == "https://x/5"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "title": "Fix the bug",
        "body": "Description",
        "head": "alice:itp/abc123",
        "base": "main",
    }


@respx.mock
async def test_same_repo_pr_omits_the_owner_prefix(tmp_path: Path) -> None:
    _write_repository_json(tmp_path, full_name="alice/widget", default_branch="main")
    route = respx.post(f"{BASE}/repos/alice/widget/pulls").mock(
        return_value=httpx.Response(201, json={"number": 1, "html_url": "https://x/1"})
    )

    await create_pull_request(
        run_dir=tmp_path,
        branch_name="itp/abc123",
        fork_remote_url="https://github.com/alice/widget.git",
        title="t",
        body="",
        base=None,
        token="tok",
    )

    sent = json.loads(route.calls.last.request.content)
    assert sent["head"] == "itp/abc123"


@respx.mock
async def test_an_explicit_base_overrides_the_default_branch(tmp_path: Path) -> None:
    _write_repository_json(tmp_path, full_name="upstream/widget", default_branch="main")
    route = respx.post(f"{BASE}/repos/upstream/widget/pulls").mock(
        return_value=httpx.Response(201, json={"number": 1, "html_url": "https://x/1"})
    )

    await create_pull_request(
        run_dir=tmp_path,
        branch_name="itp/abc123",
        fork_remote_url="https://github.com/alice/widget.git",
        title="t",
        body="",
        base="develop",
        token="tok",
    )

    sent = json.loads(route.calls.last.request.content)
    assert sent["base"] == "develop"


async def test_a_non_github_fork_remote_is_rejected_before_any_network_call(
    tmp_path: Path,
) -> None:
    _write_repository_json(tmp_path, full_name="upstream/widget", default_branch="main")
    with pytest.raises(ForkRemoteNotGitHub):
        await create_pull_request(
            run_dir=tmp_path,
            branch_name="itp/abc123",
            fork_remote_url="https://gitlab.com/alice/widget.git",
            title="t",
            body="",
            base=None,
            token="tok",
        )


@respx.mock
async def test_github_rejecting_the_pr_propagates_its_real_error(tmp_path: Path) -> None:
    _write_repository_json(tmp_path, full_name="upstream/widget", default_branch="main")
    respx.post(f"{BASE}/repos/upstream/widget/pulls").mock(
        return_value=httpx.Response(422, json={"message": "A pull request already exists"})
    )

    with pytest.raises(GitHubAPIError, match="already exists"):
        await create_pull_request(
            run_dir=tmp_path,
            branch_name="itp/abc123",
            fork_remote_url="https://github.com/alice/widget.git",
            title="t",
            body="",
            base=None,
            token="tok",
        )
