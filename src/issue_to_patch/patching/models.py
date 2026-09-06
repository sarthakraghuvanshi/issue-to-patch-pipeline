"""Data shapes for patch generation and validation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from issue_to_patch.run_states import RunState


class FileEdit(BaseModel):
    """One edit to one file.

    * ``old == ""`` and the file does not exist  -> create the file with ``new``.
    * ``new == ""``                              -> delete the matched text.
    * otherwise ``old`` must appear **exactly once** in the file and is replaced.

    ``anchor`` is optional surrounding context recorded for the audit trail; it is
    not used for matching.
    """

    path: str
    old: str = ""
    new: str = ""
    anchor: str = ""


class EditPlan(BaseModel):
    """An ordered list of edits plus the commit message to use."""

    message: str = "fix: apply issue-to-patch edit plan"
    edits: list[FileEdit] = Field(min_length=1)

    def paths(self) -> list[str]:
        return [edit.path for edit in self.edits]

    def content_key(self) -> str:
        rows = [self.message]
        for edit in self.edits:
            rows.append(f"{edit.path}\x1f{edit.old}\x1f{edit.new}")
        return "\n".join(rows)


class PatchArtifact(BaseModel):
    """The generated patch and what it touched."""

    base_sha: str
    commit_sha: str
    changed_files: list[str]
    added_lines: int
    removed_lines: int
    patch_text: str

    def normalized_for_hash(self) -> str:
        """Patch text with volatile headers stripped, for reproducibility checks."""
        keep: list[str] = []
        for line in self.patch_text.splitlines():
            if line.startswith(("From ", "Date: ", "index ")):
                continue
            keep.append(line)
        return "\n".join(keep)


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class CheckResult(BaseModel):
    name: str
    status: CheckStatus
    detail: str = ""


class ValidationReport(BaseModel):
    """The outcome of running every deterministic check against a patch."""

    checks: list[CheckResult]
    run_state: RunState

    @property
    def ok(self) -> bool:
        return self.run_state is RunState.PATCH_VALIDATED

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status is CheckStatus.FAIL]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status is CheckStatus.WARN]
