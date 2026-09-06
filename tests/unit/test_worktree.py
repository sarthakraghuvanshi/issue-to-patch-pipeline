"""generate_patch: edit application rules and byte-stability."""

from __future__ import annotations

from pathlib import Path

import pytest

from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.patching import EditPlan, FileEdit, generate_patch
from issue_to_patch.patching.worktree import EditApplicationError


def _snapshot(fixture_repo: Path, tmp_path: Path):
    return create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/x")


def test_new_file_is_created(fixture_repo: Path, tmp_path: Path) -> None:
    snap = _snapshot(fixture_repo, tmp_path)
    plan = EditPlan(edits=[FileEdit(path="NOTES.md", old="", new="notes\n")])
    patch = generate_patch(snap, plan)
    assert "NOTES.md" in patch.changed_files
    assert "+notes" in patch.patch_text


def test_ambiguous_old_text_is_rejected(fixture_repo: Path, tmp_path: Path) -> None:
    snap = _snapshot(fixture_repo, tmp_path)
    # "a" appears many times in calculator.py
    plan = EditPlan(edits=[FileEdit(path="calculator.py", old="a", new="x")])
    with pytest.raises(EditApplicationError, match="ambiguous"):
        generate_patch(snap, plan)


def test_missing_old_text_is_rejected(fixture_repo: Path, tmp_path: Path) -> None:
    snap = _snapshot(fixture_repo, tmp_path)
    plan = EditPlan(edits=[FileEdit(path="calculator.py", old="not-present", new="x")])
    with pytest.raises(EditApplicationError, match="not found"):
        generate_patch(snap, plan)


def test_patch_is_byte_stable(fixture_repo: Path, tmp_path: Path) -> None:
    plan = EditPlan(edits=[FileEdit(path="calculator.py", old="a - b", new="a + b")])
    p1 = generate_patch(_snapshot(fixture_repo, tmp_path / "1"), plan)
    p2 = generate_patch(_snapshot(fixture_repo, tmp_path / "2"), plan)
    assert p1.patch_text == p2.patch_text
