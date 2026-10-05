"""Open a real GitHub Pull Request from an already-pushed branch.

A deliberately separate, explicit action from push — never automatic, never
implied. Reuses the upstream repo's metadata already fetched and saved
during ingest (``artifacts/<run_id>/raw/repository.json``) rather than
calling GitHub again just to look up a default branch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from issue_to_patch.ingestion.github import GitHubClient
from issue_to_patch.patching.push import parse_github_owner_repo


class ForkRemoteNotGitHub(Exception):
    """The given fork remote doesn't look like a github.com URL."""


@dataclass(frozen=True)
class PullRequestResult:
    number: int
    url: str


async def create_pull_request(
    *,
    run_dir: Path,
    branch_name: str,
    fork_remote_url: str,
    title: str,
    body: str,
    base: str | None,
    token: str,
) -> PullRequestResult:
    """``head``/``base`` follow GitHub's own convention: ``head`` is just the
    branch name when the fork IS the upstream repo (the user has write
    access there directly), or ``"<fork-owner>:<branch>"`` for the more
    common cross-repository case. ``base`` defaults to the upstream repo's
    real default branch — not a guess like ``"main"``."""
    fork_owner_repo = parse_github_owner_repo(fork_remote_url)
    if fork_owner_repo is None:
        raise ForkRemoteNotGitHub(f"not a github.com remote: {fork_remote_url}")
    fork_owner, _fork_repo = fork_owner_repo

    repository = json.loads((run_dir / "raw" / "repository.json").read_text("utf-8"))["data"]
    upstream_full_name: str = repository["full_name"]
    upstream_owner = upstream_full_name.split("/", 1)[0]
    resolved_base = base or repository["default_branch"]

    head = branch_name if fork_owner == upstream_owner else f"{fork_owner}:{branch_name}"

    async with GitHubClient(token=token) as client:
        response = await client.post_json(
            f"/repos/{upstream_full_name}/pulls",
            json={"title": title, "body": body, "head": head, "base": resolved_base},
        )
    return PullRequestResult(number=response["number"], url=response["html_url"])
