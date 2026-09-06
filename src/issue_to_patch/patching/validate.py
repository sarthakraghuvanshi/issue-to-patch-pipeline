"""Deterministic checks against a generated patch.

None of this uses an LLM. Each check appends a :class:`CheckResult`; the overall
:class:`ValidationReport.run_state` is derived from them:

* any hard failure (scope, secret, binary, does-not-apply)  -> ``PATCH_REJECTED``
* only soft warnings (large diff, many files)               -> ``PATCH_REQUIRES_HUMAN_REVIEW``
* everything passes                                          -> ``PATCH_VALIDATED``
"""

from __future__ import annotations

import re
import shutil
import tempfile
from fnmatch import fnmatch
from pathlib import Path

from issue_to_patch.ingestion.git_ops import SafeGit
from issue_to_patch.ingestion.models import RepositorySnapshot
from issue_to_patch.patching.models import (
    CheckResult,
    CheckStatus,
    PatchArtifact,
    ValidationReport,
)
from issue_to_patch.run_states import RunState

_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)aws_secret_access_key\s*=\s*\S+"),
    re.compile(r"(?i)(?:api|secret|access)[_-]?(?:key|token)\s*[:=]\s*['\"]?[A-Za-z0-9/+_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
]

# Soft thresholds -> human review, not rejection.
_MAX_FILES_BEFORE_REVIEW = 5
_MAX_CHANGED_LINES_BEFORE_REVIEW = 150


def validate_patch(
    snapshot: RepositorySnapshot,
    patch: PatchArtifact,
    *,
    allowed_scope: list[str],
) -> ValidationReport:
    checks: list[CheckResult] = []

    checks.append(_check_syntax(patch))
    checks.append(_check_applies(snapshot, patch))
    checks.append(_check_scope(patch, allowed_scope))
    checks.append(_check_no_binaries(patch))
    checks.append(_check_no_secrets(patch))
    checks.append(_check_minimality(patch))

    has_failure = any(c.status is CheckStatus.FAIL for c in checks)
    has_warning = any(c.status is CheckStatus.WARN for c in checks)
    if has_failure:
        state = RunState.PATCH_REJECTED
    elif has_warning:
        state = RunState.PATCH_REQUIRES_HUMAN_REVIEW
    else:
        state = RunState.PATCH_VALIDATED
    return ValidationReport(checks=checks, run_state=state)


def _check_syntax(patch: PatchArtifact) -> CheckResult:
    if not patch.patch_text.strip():
        return CheckResult(name="syntax", status=CheckStatus.FAIL, detail="empty patch")
    if "diff --git" not in patch.patch_text:
        return CheckResult(
            name="syntax", status=CheckStatus.FAIL, detail="no 'diff --git' hunk header"
        )
    return CheckResult(name="syntax", status=CheckStatus.PASS)


def _check_applies(snapshot: RepositorySnapshot, patch: PatchArtifact) -> CheckResult:
    """Re-clone the base commit into a temp dir and dry-run ``git apply --check``."""
    parent = Path(tempfile.mkdtemp(prefix="itp-verify-", dir=snapshot.root_path.parent))
    git = SafeGit(root=parent)
    try:
        git.run(
            "clone",
            "--local",
            "--quiet",
            str(snapshot.root_path),
            "base",
            allow_external_paths=True,
        )
        git.go_offline()
        base = parent / "base"
        git.run("checkout", "--quiet", snapshot.commit_sha, cwd=base)
        result = git.run(
            "apply", "--check", "-p1", "-", cwd=base, check=False, input_text=patch.patch_text
        )
        if result.returncode == 0:
            return CheckResult(name="applies_to_sha", status=CheckStatus.PASS)
        return CheckResult(
            name="applies_to_sha",
            status=CheckStatus.FAIL,
            detail=result.stderr.strip() or "git apply --check failed",
        )
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def _check_scope(patch: PatchArtifact, allowed_scope: list[str]) -> CheckResult:
    if not allowed_scope:
        return CheckResult(
            name="scope",
            status=CheckStatus.WARN,
            detail="no allowed_scope given; cannot confirm the patch stays in bounds",
        )
    outside = [f for f in patch.changed_files if not any(fnmatch(f, pat) for pat in allowed_scope)]
    if outside:
        return CheckResult(
            name="scope",
            status=CheckStatus.FAIL,
            detail=f"changes files outside allowed scope: {', '.join(outside)}",
        )
    return CheckResult(name="scope", status=CheckStatus.PASS)


def _check_no_binaries(patch: PatchArtifact) -> CheckResult:
    if re.search(r"^Binary files .* differ$", patch.patch_text, re.MULTILINE) or (
        "GIT binary patch" in patch.patch_text
    ):
        return CheckResult(
            name="no_binaries", status=CheckStatus.FAIL, detail="patch changes a binary file"
        )
    return CheckResult(name="no_binaries", status=CheckStatus.PASS)


def _check_no_secrets(patch: PatchArtifact) -> CheckResult:
    added = [
        line[1:]
        for line in patch.patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    body = "\n".join(added)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(body):
            return CheckResult(
                name="no_secrets",
                status=CheckStatus.FAIL,
                detail=f"added line matches a secret pattern: {pattern.pattern[:40]}",
            )
    return CheckResult(name="no_secrets", status=CheckStatus.PASS)


def _check_minimality(patch: PatchArtifact) -> CheckResult:
    changed_lines = patch.added_lines + patch.removed_lines
    if len(patch.changed_files) > _MAX_FILES_BEFORE_REVIEW:
        return CheckResult(
            name="minimality",
            status=CheckStatus.WARN,
            detail=f"{len(patch.changed_files)} files changed (> {_MAX_FILES_BEFORE_REVIEW})",
        )
    if changed_lines > _MAX_CHANGED_LINES_BEFORE_REVIEW:
        return CheckResult(
            name="minimality",
            status=CheckStatus.WARN,
            detail=f"{changed_lines} lines changed (> {_MAX_CHANGED_LINES_BEFORE_REVIEW})",
        )
    return CheckResult(name="minimality", status=CheckStatus.PASS)
