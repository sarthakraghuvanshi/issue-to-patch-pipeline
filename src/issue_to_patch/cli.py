"""Command-line entrypoint.

Only ``version`` and ``config`` are wired up at bootstrap. Feature commands
(``run``, ``ingest``, ``index``, ``search``, ``review``) land in their sprints.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from issue_to_patch import __version__
from issue_to_patch.config import get_settings
from issue_to_patch.logging import configure_logging
from issue_to_patch.run_states import RunState

app = typer.Typer(add_completion=False, help="Issue-to-Patch Automation Pipeline")

# Exit code per terminal state, so shell / CI can branch on the outcome.
_EXIT_CODES = {
    RunState.PATCH_VALIDATED: 0,
    RunState.PATCH_REQUIRES_HUMAN_REVIEW: 10,
    RunState.PATCH_REJECTED: 20,
    RunState.INVESTIGATION_INCONCLUSIVE: 30,
}


@app.callback()
def _main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def config() -> None:
    """Print the effective configuration with secrets redacted."""
    settings = get_settings()
    typer.echo(json.dumps(json.loads(settings.model_dump_json()), indent=2, default=str))


@app.command()
def run(
    issue: Annotated[str, typer.Option(help="Issue URL, owner/repo#n, fixture path, or raw text")],
    repo: Annotated[str, typer.Option(help="Local path or URL of the repository to snapshot")],
    edit_plan: Annotated[Path, typer.Option(help="Path to an EditPlan JSON file")],
    scope: Annotated[
        list[str] | None,
        typer.Option(help="Glob(s) the patch must stay within; defaults to the plan's own paths"),
    ] = None,
) -> None:
    """Deterministically turn an issue + repo + edit plan into a validated .patch (no LLM)."""
    from issue_to_patch.pipeline import run_deterministic

    result = run_deterministic(
        issue_ref=issue,
        repo_source=repo,
        edit_plan_path=edit_plan,
        allowed_scope=scope,
    )

    typer.echo(f"run_id:        {result.run_id}")
    typer.echo(f"issue:         {result.issue.reference}")
    typer.echo(f"base_sha:      {result.snapshot.commit_sha if result.snapshot else '-'}")
    typer.echo(f"changed_files: {result.patch.changed_files if result.patch else []}")
    typer.echo(f"content_hash:  {result.content_hash}")
    typer.echo(f"state:         {result.state.value}")
    if result.validation:
        for check in result.validation.checks:
            typer.echo(f"  [{check.status.value:>4}] {check.name} {check.detail}".rstrip())
    typer.echo(f"artifacts:     {result.run_dir}")
    raise typer.Exit(_EXIT_CODES[result.state])


if __name__ == "__main__":
    app()
