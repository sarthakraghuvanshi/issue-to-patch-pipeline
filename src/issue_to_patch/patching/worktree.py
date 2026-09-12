"""Apply an :class:`EditPlan` inside an isolated ``git worktree`` and export a patch.

Nothing touches the snapshot's checked-out files. We add a throwaway worktree at
the base commit, edit there, commit with a fixed identity/date (so the patch is
byte-stable), run ``git format-patch``, then remove the worktree.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import uuid
from pathlib import Path

from issue_to_patch.ingestion.errors import UnsafeGitInvocation
from issue_to_patch.ingestion.git_ops import SafeGit
from issue_to_patch.ingestion.models import RepositorySnapshot
from issue_to_patch.patching.models import EditPlan, FileEdit, PatchArtifact


class EditApplicationError(Exception):
    """An edit could not be applied cleanly (missing file, ambiguous match, ...)."""


def generate_patch(snapshot: RepositorySnapshot, plan: EditPlan) -> PatchArtifact:
    """Produce a :class:`PatchArtifact` for ``plan`` applied on top of ``snapshot``."""
    repo = snapshot.root_path
    git = SafeGit(root=repo.parent if repo.parent.exists() else repo)
    git.go_offline()

    work_parent = Path(tempfile.mkdtemp(prefix="itp-worktree-", dir=repo.parent))
    worktree = work_parent / "wt"
    # Unique per call: a revision loop calls generate_patch() more than once
    # against the same snapshot, and a fixed branch name would collide with
    # whatever the previous attempt left behind (worktree removal below does
    # not implicitly delete the branch it was checked out on).
    branch = f"itp/patch-{uuid.uuid4().hex[:12]}"
    try:
        git.run(
            "worktree",
            "add",
            "--quiet",
            "--force",
            "-b",
            branch,
            str(worktree),
            snapshot.commit_sha,
            cwd=repo,
        )

        for edit in plan.edits:
            _apply_one(worktree, edit)

        git.run("add", "-A", cwd=worktree)
        git.run("commit", "--quiet", "-m", plan.message, cwd=worktree)
        commit_sha = git.run("rev-parse", "HEAD", cwd=worktree).stdout.strip()

        patch_text = git.run(
            "format-patch", "-1", "--stdout", "--no-signature", cwd=worktree
        ).stdout
        numstat = git.run("diff", "--numstat", f"{snapshot.commit_sha}..HEAD", cwd=worktree).stdout
        name_only = git.run(
            "diff", "--name-only", f"{snapshot.commit_sha}..HEAD", cwd=worktree
        ).stdout

        added, removed = _sum_numstat(numstat)
        return PatchArtifact(
            base_sha=snapshot.commit_sha,
            commit_sha=commit_sha,
            changed_files=[line for line in name_only.splitlines() if line],
            added_lines=added,
            removed_lines=removed,
            patch_text=patch_text,
        )
    finally:
        with contextlib.suppress(UnsafeGitInvocation):
            git.run("worktree", "remove", "--force", str(worktree), cwd=repo, check=False)
        with contextlib.suppress(UnsafeGitInvocation):
            git.run("branch", "-D", branch, cwd=repo, check=False)
        shutil.rmtree(work_parent, ignore_errors=True)


def _apply_one(worktree: Path, edit: FileEdit) -> None:
    if ".." in Path(edit.path).parts or Path(edit.path).is_absolute():
        raise EditApplicationError(f"unsafe edit path: {edit.path}")
    target = worktree / edit.path

    if edit.old == "" and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edit.new, "utf-8")
        return

    if not target.exists():
        raise EditApplicationError(f"file to edit does not exist: {edit.path}")

    text = target.read_text("utf-8")
    occurrences = text.count(edit.old)
    if edit.old == "":
        raise EditApplicationError(f"empty 'old' on existing file: {edit.path}")
    if occurrences == 0:
        raise EditApplicationError(f"'old' text not found in {edit.path}")
    if occurrences > 1:
        raise EditApplicationError(
            f"'old' text is ambiguous in {edit.path} ({occurrences} matches)"
        )
    target.write_text(text.replace(edit.old, edit.new, 1), "utf-8")


def _sum_numstat(numstat: str) -> tuple[int, int]:
    added = removed = 0
    for line in numstat.splitlines():
        cols = line.split("\t")
        if len(cols) >= 2 and cols[0].isdigit() and cols[1].isdigit():
            added += int(cols[0])
            removed += int(cols[1])
    return added, removed
