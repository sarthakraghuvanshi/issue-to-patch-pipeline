"""Sprint 1 pipeline: a straight line, no graph, no LLM.

    normalize issue -> snapshot repo -> apply edit plan -> validate -> persist

Later sprints replace the middle with the LangGraph reasoning engine, but the
inputs, outputs, and persistence contract stay the same.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from issue_to_patch.config import Settings, get_settings
from issue_to_patch.ingestion import (
    IssueRequest,
    RepositorySnapshot,
    normalize_issue,
    stable_hash,
)
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.logging import bind_run_id, get_logger
from issue_to_patch.patching import EditPlan, PatchArtifact, ValidationReport, generate_patch
from issue_to_patch.patching.validate import validate_patch
from issue_to_patch.persistence import Store
from issue_to_patch.run_states import RunState

_log = get_logger("pipeline")


@dataclass
class RunResult:
    run_id: str
    state: RunState
    content_hash: str
    run_dir: Path
    issue: IssueRequest
    snapshot: RepositorySnapshot | None
    patch: PatchArtifact | None
    validation: ValidationReport | None


def run_deterministic(
    *,
    issue_ref: str,
    repo_source: str,
    edit_plan_path: str | Path,
    allowed_scope: list[str] | None = None,
    settings: Settings | None = None,
) -> RunResult:
    settings = settings or get_settings()
    run_id = uuid.uuid4().hex[:16]
    run_dir = settings.artifacts_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    store = Store(settings.database_url)
    store.create_all()

    with bind_run_id(run_id):
        issue = normalize_issue(issue_ref)
        plan = EditPlan.model_validate_json(Path(edit_plan_path).read_text("utf-8"))
        scope = allowed_scope or plan.paths()

        content_hash = stable_hash(issue.content_key(), plan.content_key(), ",".join(sorted(scope)))
        store.create_run(
            run_id=run_id,
            issue_ref=issue.reference,
            repo=issue.repo,
            commit_sha=None,
            content_hash=content_hash,
        )
        _log.info("run.start", issue_ref=issue.reference, content_hash=content_hash)

        snapshot = create_snapshot(repo_source, run_dir / "snapshot", repo_name=issue.repo)
        store.record_tool_call(
            run_id,
            tool="create_snapshot",
            args_redacted=json.dumps({"source": repo_source}),
            result_hash=stable_hash(snapshot.commit_sha, snapshot.tree_hash),
        )

        patch = generate_patch(snapshot, plan)
        _write(run_dir / "fix.patch", patch.patch_text)
        store.record_tool_call(
            run_id,
            tool="generate_patch",
            args_redacted=json.dumps({"edits": len(plan.edits)}),
            result_hash=stable_hash(patch.normalized_for_hash()),
        )
        store.record_artifact(
            run_id,
            kind="patch",
            uri=str(run_dir / "fix.patch"),
            content_hash=stable_hash(patch.normalized_for_hash()),
        )

        validation = validate_patch(snapshot, patch, allowed_scope=scope)
        _write(run_dir / "validation.json", validation.model_dump_json(indent=2))
        store.record_tool_call(
            run_id,
            tool="validate_patch",
            args_redacted=json.dumps({"scope": scope}),
            result_hash=stable_hash(validation.model_dump_json()),
        )
        store.record_artifact(
            run_id,
            kind="validation",
            uri=str(run_dir / "validation.json"),
            content_hash=stable_hash(validation.model_dump_json()),
        )

        final_hash = stable_hash(
            content_hash,
            snapshot.commit_sha,
            patch.normalized_for_hash(),
            validation.run_state.value,
        )
        store.finish_run(run_id, state=validation.run_state.value)

        summary = {
            "run_id": run_id,
            "issue_ref": issue.reference,
            "repo": issue.repo,
            "base_sha": snapshot.commit_sha,
            "state": validation.run_state.value,
            "content_hash": final_hash,
            "changed_files": patch.changed_files,
            "created_at": datetime.now(UTC).isoformat(),
        }
        _write(run_dir / "run.json", json.dumps(summary, indent=2, sort_keys=True))
        store.record_artifact(
            run_id, kind="run", uri=str(run_dir / "run.json"), content_hash=final_hash
        )
        _log.info("run.finish", state=validation.run_state.value, content_hash=final_hash)

        return RunResult(
            run_id=run_id,
            state=validation.run_state,
            content_hash=final_hash,
            run_dir=run_dir,
            issue=issue,
            snapshot=snapshot,
            patch=patch,
            validation=validation,
        )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
