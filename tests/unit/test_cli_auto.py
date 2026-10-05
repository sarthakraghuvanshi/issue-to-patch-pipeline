"""`auto` CLI: ingest -> index -> investigate -> review -> build -> push, as
one interactive session. No real network/LLM calls — GitHub is mocked via
respx (mirrors tests/integration/test_ingest.py), the repo clone source is
the local `fixture_repo`, and the LLM is `FakeLLM` (mirrors
tests/unit/test_cli_investigate.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from issue_to_patch.cli import app
from issue_to_patch.llm.client import FakeLLM

runner = CliRunner()

BASE = "https://api.github.com"
REPO = "acme/widget"
ISSUE_URL = f"https://github.com/{REPO}/issues/7"

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


def _mock_github() -> None:
    respx.get(f"{BASE}/repos/{REPO}/issues/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 7,
                "title": "add() is wrong",
                "body": "add() returns the wrong result",
                "state": "open",
                "labels": [{"name": "bug"}],
                "user": {"login": "reporter"},
                "comments": 0,
                "html_url": ISSUE_URL,
            },
        )
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/7/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{BASE}/repos/{REPO}").mock(
        return_value=httpx.Response(
            200,
            json={
                "full_name": REPO,
                "default_branch": "main",
                "visibility": "public",
                "language": "Python",
                "topics": [],
                "size": 1,
                "clone_url": f"https://github.com/{REPO}.git",
            },
        )
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/7/timeline").mock(
        return_value=httpx.Response(200, json=[])
    )


def _patch_fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    llm = FakeLLM()
    llm.queue_structured({"search_queries": ["add returns wrong result"], "focus_areas": []})
    llm.queue_structured(
        {"hypotheses": [{"summary": "subtracts instead of adds", "confidence": 0.9}]}
    )
    llm.queue_structured(_FIX)
    monkeypatch.setattr("issue_to_patch.graph.deps.get_llm", lambda settings=None: llm)
    return llm


@pytest.fixture
def auto_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from issue_to_patch.config import get_settings

    monkeypatch.setenv("ITP_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _run_id_from(output: str) -> str:
    return next(
        line.split(":", 1)[1].strip() for line in output.splitlines() if line.startswith("run_id:")
    )


@respx.mock
def test_auto_happy_path_builds_without_pushing(
    fixture_repo: Path, auto_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_github()
    _patch_fake_llm(monkeypatch)

    result = runner.invoke(
        app,
        ["auto", ISSUE_URL, "--repo-source", str(fixture_repo), "--scope", "calculator.py"],
        input="approve\n\nalice\ngatekeeper\ny\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "state:         PATCH_VALIDATED" in result.output
    assert "branch ready:" in result.output
    assert "pushed" not in result.output

    run_id = _run_id_from(result.output)
    branch_dir = auto_env / "artifacts" / run_id / "snapshot" / "branch"
    assert branch_dir.exists()
    content = (branch_dir / "calculator.py").read_text("utf-8")
    assert "a + b" in content


@respx.mock
def test_auto_can_push_to_a_configured_remote(
    fixture_repo: Path, auto_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_github()
    _patch_fake_llm(monkeypatch)

    bare = auto_env / "bare.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
    monkeypatch.setenv("ITP_PUSH_REMOTE_URL", f"file://{bare}")
    from issue_to_patch.config import get_settings

    get_settings.cache_clear()

    result = runner.invoke(
        app,
        ["auto", ISSUE_URL, "--repo-source", str(fixture_repo), "--scope", "calculator.py"],
        # ... / build=y / push=y / create-pr=n (declined — not under test here)
        input="approve\n\nalice\ngatekeeper\ny\ny\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "pushed itp/" in result.output

    run_id = _run_id_from(result.output)
    ls_remote = subprocess.run(
        ["git", "ls-remote", str(bare), f"itp/{run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert f"itp/{run_id}" in ls_remote


@respx.mock
def test_auto_declining_the_pull_request_prompt_stops_cleanly(
    fixture_repo: Path, auto_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_github()
    _patch_fake_llm(monkeypatch)

    bare = auto_env / "bare.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
    monkeypatch.setenv("ITP_PUSH_REMOTE_URL", f"file://{bare}")
    from issue_to_patch.config import get_settings

    get_settings.cache_clear()

    result = runner.invoke(
        app,
        ["auto", ISSUE_URL, "--repo-source", str(fixture_repo), "--scope", "calculator.py"],
        # approve / reason / reviewer / role / build=y / push=y / create-pr=n
        input="approve\n\nalice\ngatekeeper\ny\ny\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "pushed itp/" in result.output
    # "Create a pull request now?" (the prompt text) is expected; the
    # outcomes of actually trying are not.
    assert "pull request opened" not in result.output
    assert "pull request failed" not in result.output


@respx.mock
def test_auto_reports_a_non_github_push_remote_cleanly_when_creating_a_pr(
    fixture_repo: Path, auto_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The push target here is a local bare repo (the realistic, offline way
    to test push itself) — not a github.com URL. Trying to open a PR from
    it must fail with a clear message, not a crash, since there's no
    github.com owner/repo to parse out of it."""
    _mock_github()
    _patch_fake_llm(monkeypatch)

    bare = auto_env / "bare.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
    monkeypatch.setenv("ITP_PUSH_REMOTE_URL", f"file://{bare}")
    from issue_to_patch.config import get_settings

    get_settings.cache_clear()

    result = runner.invoke(
        app,
        ["auto", ISSUE_URL, "--repo-source", str(fixture_repo), "--scope", "calculator.py"],
        # approve / reason / reviewer / role / build=y / push=y / create-pr=y /
        # title=default / body=default / github token
        input="approve\n\nalice\ngatekeeper\ny\ny\ny\n\n\nfaketoken123\n",
    )

    assert result.exit_code == 1, result.output
    assert "pushed itp/" in result.output
    assert "pull request failed" in result.output
    assert "not a github.com remote" in result.output


@respx.mock
def test_auto_can_create_a_pull_request_after_pushing(
    fixture_repo: Path, auto_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path end to end: push to a real-looking github.com fork URL
    (stubbed — an actual `git push` there would need real network/auth,
    which SafeGit itself, not this test, is responsible for), then open a
    real PR against the mocked GitHub API."""
    _mock_github()
    _patch_fake_llm(monkeypatch)

    from issue_to_patch.config import get_settings
    from issue_to_patch.patching.push import PushResult

    # Deliberately NOT named "widget" (the upstream repo name): that would
    # trip auto's own "are you sure this isn't the upstream repo?" nudge —
    # a real, separate confirmation prompt this test isn't about.
    monkeypatch.setenv("ITP_PUSH_REMOTE_URL", "https://github.com/alice/my-fork.git")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "issue_to_patch.patching.push_branch",
        lambda branch_dir, branch_name, remote: PushResult(
            remote_display=remote, branch=branch_name, ok=True, detail="pushed"
        ),
    )

    pr_route = respx.post(f"{BASE}/repos/{REPO}/pulls").mock(
        return_value=httpx.Response(
            201, json={"number": 3, "html_url": f"https://github.com/{REPO}/pull/3"}
        )
    )

    result = runner.invoke(
        app,
        ["auto", ISSUE_URL, "--repo-source", str(fixture_repo), "--scope", "calculator.py"],
        # approve / reason / reviewer / role / build=y / push=y / create-pr=y /
        # title=default / body=default / github token
        input="approve\n\nalice\ngatekeeper\ny\ny\ny\n\n\nfaketoken123\n",
    )

    assert result.exit_code == 0, result.output
    assert "pushed itp/" in result.output
    assert f"pull request opened: https://github.com/{REPO}/pull/3" in result.output
    sent = json.loads(pr_route.calls.last.request.content)
    assert sent["head"] == "alice:itp/" + _run_id_from(result.output)
    assert sent["base"] == "main"


@respx.mock
def test_auto_push_without_a_configured_remote_is_a_clean_noop(
    fixture_repo: Path, auto_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_github()
    _patch_fake_llm(monkeypatch)

    result = runner.invoke(
        app,
        ["auto", ISSUE_URL, "--repo-source", str(fixture_repo), "--scope", "calculator.py"],
        input="approve\n\nalice\ngatekeeper\ny\ny\n",
    )

    assert result.exit_code == 0, result.output
    assert "ITP_PUSH_REMOTE_URL is not configured" in result.output
    assert "pushed itp/" not in result.output


@respx.mock
def test_auto_declining_build_stops_before_any_branch_is_made(
    fixture_repo: Path, auto_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_github()
    _patch_fake_llm(monkeypatch)

    result = runner.invoke(
        app,
        ["auto", ISSUE_URL, "--repo-source", str(fixture_repo), "--scope", "calculator.py"],
        input="approve\n\nalice\ngatekeeper\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "branch ready:" not in result.output
    run_id = _run_id_from(result.output)
    assert not (auto_env / "artifacts" / run_id / "snapshot" / "branch").exists()
