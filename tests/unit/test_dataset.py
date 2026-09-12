"""dataset.build_labeled_issues: mining merged PRs into labeled eval rows."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from issue_to_patch.ingestion.github import GitHubClient
from issue_to_patch.retrieval.dataset import build_labeled_issues, write_jsonl

BASE = "https://api.github.com"
REPO = "acme/x"


async def _no_sleep(_seconds: float) -> None:
    return None


def _pr(number: int, body: str) -> dict[str, object]:
    return {"number": number, "body": body}


def _file(filename: str, patch: str = "") -> dict[str, object]:
    return {"filename": filename, "patch": patch}


@respx.mock
async def test_keeps_pr_with_linked_issue_and_indexed_gold_file() -> None:
    route = respx.get(f"{BASE}/search/issues")
    route.side_effect = [
        httpx.Response(200, json={"items": [_pr(100, "This fixes #10 for real")]}),
        httpx.Response(200, json={"items": []}),
    ]
    respx.get(f"{BASE}/repos/{REPO}/pulls/100/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                _file("src/x.py", patch="@@ -1,2 +1,2 @@ def broken():\n+def broken():\n+    pass")
            ],
        )
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/10").mock(
        return_value=httpx.Response(
            200,
            json={"title": "broken() crashes", "body": "steps to reproduce", "labels": ["bug"]},
        )
    )
    client = GitHubClient(base_url=BASE, sleep=_no_sleep)
    rows = await build_labeled_issues(client, REPO, "sha1", indexed_paths={"src/x.py"}, count=5)
    await client.aclose()

    assert len(rows) == 1
    row = rows[0]
    assert row.issue_id == f"{REPO}#10"
    assert row.repository == REPO
    assert row.commit_sha == "sha1"
    assert row.gold_files == ["src/x.py"]
    assert "broken" in row.gold_symbols
    assert "bug" in row.labels
    assert "broken() crashes" in row.query


@respx.mock
async def test_dedupes_pr_reappearing_across_pages() -> None:
    # GitHub's search index is live; the same PR can resurface on a later page
    # if the result set shifts mid-scan. It must only be counted once.
    route = respx.get(f"{BASE}/search/issues")
    route.side_effect = [
        httpx.Response(200, json={"items": [_pr(100, "fixes #10")]}),
        httpx.Response(200, json={"items": [_pr(100, "fixes #10")]}),
        httpx.Response(200, json={"items": []}),
    ]
    respx.get(f"{BASE}/repos/{REPO}/pulls/100/files").mock(
        return_value=httpx.Response(200, json=[_file("src/x.py")])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/10").mock(
        return_value=httpx.Response(200, json={"title": "t", "body": "b"})
    )
    client = GitHubClient(base_url=BASE, sleep=_no_sleep)
    rows = await build_labeled_issues(client, REPO, "sha1", indexed_paths={"src/x.py"}, count=5)
    await client.aclose()

    assert len(rows) == 1


@respx.mock
async def test_skips_pr_without_a_linked_issue() -> None:
    respx.get(f"{BASE}/search/issues").mock(
        return_value=httpx.Response(200, json={"items": [_pr(101, "just a refactor, no issue")]})
    )
    client = GitHubClient(base_url=BASE, sleep=_no_sleep)
    rows = await build_labeled_issues(client, REPO, "sha1", indexed_paths={"src/x.py"})
    await client.aclose()

    assert rows == []


@respx.mock
async def test_skips_pr_whose_changed_file_is_not_indexed() -> None:
    respx.get(f"{BASE}/search/issues").mock(
        return_value=httpx.Response(200, json={"items": [_pr(100, "fixes #10")]})
    )
    respx.get(f"{BASE}/repos/{REPO}/pulls/100/files").mock(
        return_value=httpx.Response(200, json=[_file("src/not_indexed.py")])
    )
    client = GitHubClient(base_url=BASE, sleep=_no_sleep)
    rows = await build_labeled_issues(client, REPO, "sha1", indexed_paths={"src/x.py"})
    await client.aclose()

    assert rows == []


@respx.mock
async def test_skips_pr_touching_more_than_max_files() -> None:
    respx.get(f"{BASE}/search/issues").mock(
        return_value=httpx.Response(200, json={"items": [_pr(100, "fixes #10")]})
    )
    respx.get(f"{BASE}/repos/{REPO}/pulls/100/files").mock(
        return_value=httpx.Response(200, json=[_file(f"src/f{i}.py") for i in range(5)])
    )
    client = GitHubClient(base_url=BASE, sleep=_no_sleep)
    rows = await build_labeled_issues(
        client, REPO, "sha1", indexed_paths={f"src/f{i}.py" for i in range(5)}, max_files=2
    )
    await client.aclose()

    assert rows == []


@respx.mock
async def test_skips_when_closes_number_points_at_a_pull_request() -> None:
    respx.get(f"{BASE}/search/issues").mock(
        return_value=httpx.Response(200, json={"items": [_pr(100, "fixes #10")]})
    )
    respx.get(f"{BASE}/repos/{REPO}/pulls/100/files").mock(
        return_value=httpx.Response(200, json=[_file("src/x.py")])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/10").mock(
        return_value=httpx.Response(
            200, json={"title": "t", "body": "b", "pull_request": {"url": "..."}}
        )
    )
    client = GitHubClient(base_url=BASE, sleep=_no_sleep)
    rows = await build_labeled_issues(client, REPO, "sha1", indexed_paths={"src/x.py"})
    await client.aclose()

    assert rows == []


def test_write_jsonl_round_trips(tmp_path: Path) -> None:
    from issue_to_patch.retrieval.evaluation import LabeledIssue, load_labeled_issues

    rows = [
        LabeledIssue(
            issue_id="acme/x#1",
            repository="acme/x",
            commit_sha="sha1",
            query="q",
            gold_files=["a.py"],
        )
    ]
    out = tmp_path / "labeled.jsonl"
    write_jsonl(rows, out)
    loaded = load_labeled_issues(out)
    assert loaded == rows
