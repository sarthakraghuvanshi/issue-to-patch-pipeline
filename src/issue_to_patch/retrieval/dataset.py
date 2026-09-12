"""Build a labeled retrieval-eval set from a repo's merged bug-fix pull requests.

For a bug-fix PR we already know the ground truth: the *issue* it closes is the
query a user would type, and the *files that PR changed* are the files a good
retriever should surface. Mining GitHub for "merged PR that says fixes/closes
#N" turns real project history into free eval labels — no manual annotation.

Kept deliberately narrow (Milestone 2 gate, not a general dataset tool):

* only PRs that link an issue via "fixes/closes/resolves #N" in the body;
* only PRs whose changed files are a subset of what we've actually indexed for
  the pinned commit (a label pointing at a file our index doesn't have would
  make every mode's recall wrong, not just show retrieval doing badly);
* skip PRs that touch more than ``max_files`` code files — those are usually
  refactors with no single "this file fixed it" answer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from issue_to_patch.ingestion.github import GitHubClient
from issue_to_patch.logging import get_logger
from issue_to_patch.retrieval.evaluation import LabeledIssue

_log = get_logger("dataset")

_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
_HUNK_SYMBOL_RE = re.compile(
    r"^@@ .*@@\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.MULTILINE
)
_DEF_IN_PATCH_RE = re.compile(r"^\+\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.MULTILINE)
_INDEXABLE_SUFFIXES = (".py", ".md", ".pyi")


async def build_labeled_issues(
    client: GitHubClient,
    repo: str,
    commit_sha: str,
    *,
    indexed_paths: set[str],
    count: int = 20,
    max_files: int = 4,
    extra_query: str = "",
) -> list[LabeledIssue]:
    """Scan merged PRs for ``repo``, oldest-search-first, and label ``count`` of them."""
    query = f"repo:{repo} type:pr is:merged{f' {extra_query}' if extra_query else ''}"
    rows: list[LabeledIssue] = []
    scanned = 0
    seen_prs: set[int] = set()

    async for pr in client.search_issues(query, sort="updated", order="desc", max_items=count * 15):
        if len(rows) >= count:
            break
        pr_number = int(pr["number"])
        if pr_number in seen_prs:
            # GitHub's search index is live and sorted by "updated"; a PR can
            # legitimately reappear across pages if the result set shifts mid-scan.
            continue
        seen_prs.add(pr_number)
        scanned += 1
        body = str(pr.get("body") or "")
        issue_ids = [int(m) for m in _CLOSES_RE.findall(body)]
        if not issue_ids:
            continue

        try:
            files_payload = await _pr_files(client, repo, pr_number)
        except Exception as exc:  # a flaky PR shouldn't kill the whole scan
            _log.warning("dataset.pr_files_failed", pr=pr_number, error=str(exc))
            continue

        code_files = [
            str(f["filename"])
            for f in files_payload
            if str(f.get("filename", "")).endswith(_INDEXABLE_SUFFIXES)
        ]
        if not code_files or len(code_files) > max_files:
            continue
        gold_files = sorted({f for f in code_files if f in indexed_paths})
        if not gold_files:
            continue

        symbols: set[str] = set()
        for f in files_payload:
            patch = str(f.get("patch") or "")
            symbols.update(_HUNK_SYMBOL_RE.findall(patch))
            symbols.update(_DEF_IN_PATCH_RE.findall(patch))

        row = await _label_one(
            client, repo, commit_sha, issue_ids[0], pr_number, gold_files, sorted(symbols)
        )
        if row is not None:
            rows.append(row)
            _log.info("dataset.row", issue=issue_ids[0], pr=pr_number, gold_files=len(gold_files))

    _log.info("dataset.done", scanned=scanned, kept=len(rows))
    return rows


async def _label_one(
    client: GitHubClient,
    repo: str,
    commit_sha: str,
    issue_id: int,
    pr_number: int,
    gold_files: list[str],
    gold_symbols: list[str],
) -> LabeledIssue | None:
    try:
        issue: dict[str, Any] = await client.get_json(f"/repos/{repo}/issues/{issue_id}")
    except Exception as exc:  # a deleted/inaccessible issue shouldn't kill the whole scan
        _log.warning("dataset.issue_fetch_failed", issue=issue_id, pr=pr_number, error=str(exc))
        return None
    if "pull_request" in issue:  # "fixes #N" pointed at another PR, not an issue
        return None

    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")[:2000]
    labels = [lb["name"] if isinstance(lb, dict) else str(lb) for lb in issue.get("labels", [])]
    return LabeledIssue(
        issue_id=f"{repo}#{issue_id}",
        repository=repo,
        commit_sha=commit_sha,
        query=f"{title}\n\n{body}".strip(),
        labels=labels,
        gold_files=gold_files,
        gold_symbols=gold_symbols,
    )


async def _pr_files(client: GitHubClient, repo: str, pr_number: int) -> list[dict[str, Any]]:
    return [
        f async for f in client.paginate(f"/repos/{repo}/pulls/{pr_number}/files", per_page=100)
    ]


def write_jsonl(rows: list[LabeledIssue], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(r.model_dump_json() for r in rows) + "\n", encoding="utf-8")
