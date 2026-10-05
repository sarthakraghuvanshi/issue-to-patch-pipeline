"""load_snapshot must always hand back an absolute root_path.

Regression: a relative --snapshot path used to survive into
RepositorySnapshot.root_path unresolved. That's fine for most of the graph,
but patch validation re-clones the repo into a *different* temp working
directory — a relative path there resolves against the wrong cwd and the
clone fails with a confusing "repository does not exist" error, even though
the snapshot is perfectly valid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from issue_to_patch.ingestion.snapshot import create_snapshot, load_snapshot


def test_load_snapshot_resolves_a_relative_path_to_absolute(
    fixture_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest_dir = tmp_path / "run" / "snapshot"
    create_snapshot(str(fixture_repo), dest_dir)

    monkeypatch.chdir(tmp_path)
    loaded = load_snapshot(Path("run/snapshot"))

    assert loaded.root_path.is_absolute()
    assert loaded.root_path == (dest_dir / "repo").resolve()
    assert (loaded.root_path / ".git").exists()
