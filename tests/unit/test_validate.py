"""validate_patch: which check failures map to which run state."""

from __future__ import annotations

from pathlib import Path

from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.patching import EditPlan, FileEdit, generate_patch, validate_patch
from issue_to_patch.run_states import RunState


def _snap(fixture_repo: Path, tmp_path: Path):
    return create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/x")


def test_clean_fix_is_validated(fixture_repo: Path, tmp_path: Path) -> None:
    snap = _snap(fixture_repo, tmp_path)
    patch = generate_patch(
        snap, EditPlan(edits=[FileEdit(path="calculator.py", old="a - b", new="a + b")])
    )
    report = validate_patch(snap, patch, allowed_scope=["*.py"])
    assert report.run_state is RunState.PATCH_VALIDATED
    assert report.ok


def test_added_secret_is_rejected(fixture_repo: Path, tmp_path: Path) -> None:
    snap = _snap(fixture_repo, tmp_path)
    leak = 'API_KEY = "AKIAIOSFODNN7EXAMPLE"'
    patch = generate_patch(
        snap,
        EditPlan(edits=[FileEdit(path="calculator.py", old="a - b", new=f"a + b\n# {leak}")]),
    )
    report = validate_patch(snap, patch, allowed_scope=["*.py"])
    assert report.run_state is RunState.PATCH_REJECTED
    assert any(c.name == "no_secrets" and c.status.value == "fail" for c in report.checks)


def test_no_scope_given_is_a_warning_not_a_pass(fixture_repo: Path, tmp_path: Path) -> None:
    snap = _snap(fixture_repo, tmp_path)
    patch = generate_patch(
        snap, EditPlan(edits=[FileEdit(path="calculator.py", old="a - b", new="a + b")])
    )
    report = validate_patch(snap, patch, allowed_scope=[])
    assert report.run_state is RunState.PATCH_REQUIRES_HUMAN_REVIEW
