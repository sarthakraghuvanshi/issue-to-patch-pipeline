"""Freeze a repository at one commit on local disk.

For Sprint 1 the ``source`` is normally a local path to a fixture repository, but
a ``file://`` or ``https://`` URL works too. After the clone we immediately call
:meth:`SafeGit.go_offline` so nothing else in the run can reach the network.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from issue_to_patch.ingestion.errors import RepositoryNotFound
from issue_to_patch.ingestion.git_ops import SafeGit
from issue_to_patch.ingestion.models import RepositorySnapshot

MANIFEST_NAME = "snapshot_manifest.json"


def create_snapshot(
    source: str,
    dest_dir: Path,
    *,
    ref: str | None = None,
    repo_name: str | None = None,
) -> RepositorySnapshot:
    """Clone ``source`` into ``dest_dir/repo`` and return a :class:`RepositorySnapshot`.

    ``dest_dir`` must not already contain a ``repo`` directory; the caller owns
    its lifetime (usually ``artifacts/<run_id>/``).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    repo_path = dest_dir / "repo"
    if repo_path.exists():
        raise RepositoryNotFound(f"destination already populated: {repo_path}")

    local = _as_local_path(source)
    if local is not None and not (local / ".git").exists():
        raise RepositoryNotFound(f"not a git repository: {local}")

    git = SafeGit(root=dest_dir)
    clone_src = str(local) if local is not None else source
    git.run(
        "clone",
        "--no-local" if local is None else "--local",
        clone_src,
        "repo",
        allow_external_paths=True,
    )
    git.go_offline()

    if ref:
        git.run("checkout", ref, cwd=repo_path)

    commit_sha = git.run("rev-parse", "HEAD", cwd=repo_path).stdout.strip()
    tree_hash = git.run("rev-parse", "HEAD^{tree}", cwd=repo_path).stdout.strip()
    tracked = git.run("ls-files", cwd=repo_path).stdout.splitlines()

    snapshot = RepositorySnapshot(
        repo=repo_name,
        source=source,
        commit_sha=commit_sha,
        tree_hash=tree_hash,
        root_path=repo_path,
        file_count=len(tracked),
        created_at=datetime.now(UTC),
    )
    (dest_dir / MANIFEST_NAME).write_text(
        json.dumps(snapshot.manifest(), indent=2, sort_keys=True), "utf-8"
    )
    return snapshot


def _as_local_path(source: str) -> Path | None:
    if source.startswith("file://"):
        return Path(source[len("file://") :]).resolve()
    if "://" in source:
        return None
    candidate = Path(source).expanduser()
    return candidate.resolve() if candidate.exists() else None


def discard_snapshot(snapshot: RepositorySnapshot) -> None:
    """Delete the cloned working copy (keep the manifest next to it)."""
    shutil.rmtree(snapshot.root_path, ignore_errors=True)


def load_snapshot(snapshot_dir: Path) -> RepositorySnapshot:
    """Rebuild a :class:`RepositorySnapshot` from an on-disk snapshot directory."""
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise RepositoryNotFound(f"no {MANIFEST_NAME} in {snapshot_dir}")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    repo_path = snapshot_dir / "repo"
    if not (repo_path / ".git").exists():
        raise RepositoryNotFound(f"no cloned repo at {repo_path}")
    return RepositorySnapshot(
        repo=manifest.get("repo"),
        source=manifest["source"],
        commit_sha=manifest["commit_sha"],
        tree_hash=manifest["tree_hash"],
        root_path=repo_path,
        file_count=int(manifest.get("file_count", 0)),
        created_at=datetime.fromisoformat(manifest["created_at"]),
    )
