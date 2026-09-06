"""End-to-end Sprint 1: issue + repo + edit plan -> validated .patch, no network, no LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from issue_to_patch.config.settings import Settings
from issue_to_patch.patching import EditPlan, FileEdit
from issue_to_patch.pipeline import run_deterministic
from issue_to_patch.run_states import RunState


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    from issue_to_patch.config import get_settings

    monkeypatch.setenv("ITP_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ITP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'runs.db'}")
    get_settings.cache_clear()
    return Settings()


def _fix_plan(tmp_path: Path) -> Path:
    plan = EditPlan(
        message="fix: add() should sum its arguments",
        edits=[FileEdit(path="calculator.py", old="return a - b", new="return a + b")],
    )
    path = tmp_path / "plan.json"
    path.write_text(plan.model_dump_json(indent=2), "utf-8")
    return path


def _issue(tmp_path: Path) -> Path:
    path = tmp_path / "issue.json"
    path.write_text(
        json.dumps({"repo": "acme/calc", "number": 1, "title": "add() subtracts", "body": "wrong"}),
        "utf-8",
    )
    return path


def test_happy_path_produces_validated_patch(
    fixture_repo: Path, tmp_path: Path, settings: Settings
) -> None:
    result = run_deterministic(
        issue_ref=str(_issue(tmp_path)),
        repo_source=str(fixture_repo),
        edit_plan_path=str(_fix_plan(tmp_path)),
        settings=settings,
    )

    assert result.state is RunState.PATCH_VALIDATED
    assert result.patch is not None
    assert result.patch.changed_files == ["calculator.py"]
    assert "return a + b" in result.patch.patch_text

    patch_file = result.run_dir / "fix.patch"
    assert patch_file.read_text("utf-8") == result.patch.patch_text
    assert (result.run_dir / "validation.json").exists()
    assert json.loads((result.run_dir / "run.json").read_text())["state"] == "PATCH_VALIDATED"


def test_same_inputs_give_same_content_hash_and_byte_stable_patch(
    fixture_repo: Path, tmp_path: Path, settings: Settings
) -> None:
    plan, issue = _fix_plan(tmp_path), _issue(tmp_path)
    r1 = run_deterministic(
        issue_ref=str(issue),
        repo_source=str(fixture_repo),
        edit_plan_path=str(plan),
        settings=settings,
    )
    r2 = run_deterministic(
        issue_ref=str(issue),
        repo_source=str(fixture_repo),
        edit_plan_path=str(plan),
        settings=settings,
    )
    assert r1.run_id != r2.run_id
    assert r1.content_hash == r2.content_hash
    assert r1.patch is not None and r2.patch is not None
    assert r1.patch.patch_text == r2.patch.patch_text


def test_scope_violation_is_rejected(
    fixture_repo: Path, tmp_path: Path, settings: Settings
) -> None:
    plan = EditPlan(
        edits=[
            FileEdit(path="calculator.py", old="return a - b", new="return a + b"),
            FileEdit(path="README.md", old="# Buggy Project", new="# Fixed Project"),
        ]
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.model_dump_json(), "utf-8")

    result = run_deterministic(
        issue_ref=str(_issue(tmp_path)),
        repo_source=str(fixture_repo),
        edit_plan_path=str(plan_path),
        allowed_scope=["*.py"],
        settings=settings,
    )
    assert result.state is RunState.PATCH_REJECTED
    assert result.validation is not None
    assert any(c.name == "scope" and c.status.value == "fail" for c in result.validation.checks)


def test_run_is_persisted_with_intact_audit_chain(
    fixture_repo: Path, tmp_path: Path, settings: Settings
) -> None:
    from issue_to_patch.persistence import Store

    result = run_deterministic(
        issue_ref=str(_issue(tmp_path)),
        repo_source=str(fixture_repo),
        edit_plan_path=str(_fix_plan(tmp_path)),
        settings=settings,
    )
    store = Store(settings.database_url)
    assert store.get_run_state(result.run_id) == "PATCH_VALIDATED"
    assert store.verify_chain(result.run_id) is True
