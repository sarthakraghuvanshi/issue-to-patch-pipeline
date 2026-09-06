"""Full ingest: mocked GitHub API + a local repo -> raw artifacts + pinned snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from issue_to_patch.ingestion import GitHubClient, InvalidIssueReference
from issue_to_patch.ingestion.ingest import ingest_issue

pytestmark = pytest.mark.integration

BASE = "https://api.github.com"
REPO = "acme/widget"


def _mock_github(issue_body: str = "The parser crashes on empty input.") -> None:
    respx.get(f"{BASE}/repos/{REPO}/issues/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 7,
                "title": "parser crashes",
                "body": issue_body,
                "state": "open",
                "labels": [{"name": "bug"}, {"name": "parser"}],
                "user": {"login": "reporter"},
                "comments": 2,
                "html_url": f"https://github.com/{REPO}/issues/7",
            },
        )
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/7/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "body": "I can repro on 3.12", "user": {"login": "reporter"}},
                {"id": 2, "body": "PR incoming", "user": {"login": "maintainer"}},
            ],
        )
    )
    respx.get(f"{BASE}/repos/{REPO}").mock(
        return_value=httpx.Response(
            200,
            json={
                "full_name": REPO,
                "default_branch": "main",
                "visibility": "public",
                "language": "Python",
                "topics": ["cli"],
                "size": 42,
                "clone_url": f"https://github.com/{REPO}.git",
            },
        )
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/7/timeline").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "event": "cross-referenced",
                    "source": {
                        "issue": {
                            "number": 9,
                            "title": "fix parser",
                            "html_url": f"https://github.com/{REPO}/pull/9",
                            "pull_request": {"url": "..."},
                        }
                    },
                },
                {"event": "labeled"},
            ],
        )
    )


@respx.mock
async def test_ingest_writes_raw_artifacts_and_pins_a_snapshot(
    fixture_repo: Path, tmp_path: Path
) -> None:
    _mock_github()
    run_dir = tmp_path / "run"
    async with GitHubClient(base_url=BASE, sleep=_no_sleep) as client:
        result = await ingest_issue(
            f"https://github.com/{REPO}/issues/7",
            run_dir,
            client=client,
            repo_source=str(fixture_repo),
        )

    assert result.issue_request.repo == REPO
    assert result.issue_request.labels == ["bug", "parser"]
    assert result.issue_request.body.startswith("The parser crashes")
    assert len(result.conversation.comments) == 2
    assert result.repository.primary_language == "Python"
    assert [c.ref for c in result.related_changes] == ["9"]
    assert len(result.snapshot.commit_sha) == 40

    names = {r.name for r in result.raw_records}
    assert names == {
        "issue",
        "issue_comments",
        "repository",
        "related_changes",
        "snapshot_manifest",
    }
    issue_doc = json.loads((run_dir / "raw" / "issue.json").read_text())
    assert issue_doc["data"]["number"] == 7
    assert issue_doc["_meta"]["source_url"].endswith("/issues/7")


@respx.mock
async def test_ingest_redacts_a_token_pasted_in_the_issue_body(
    fixture_repo: Path, tmp_path: Path
) -> None:
    _mock_github(issue_body="my CI uses ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789xx and it broke")
    run_dir = tmp_path / "run"
    async with GitHubClient(base_url=BASE, sleep=_no_sleep) as client:
        await ingest_issue(f"{REPO}#7", run_dir, client=client, repo_source=str(fixture_repo))
    raw_text = (run_dir / "raw" / "issue.json").read_text()
    assert "ghp_" not in raw_text
    assert "[REDACTED]" in raw_text


async def test_ingest_rejects_a_reference_without_an_issue_number(tmp_path: Path) -> None:
    async with GitHubClient(base_url=BASE) as client:
        with pytest.raises(InvalidIssueReference):
            await ingest_issue("just some prose", tmp_path / "run", client=client)


async def _no_sleep(_seconds: float) -> None:
    return None
