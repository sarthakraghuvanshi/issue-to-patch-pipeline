"""Command-line entrypoint.

Only ``version`` and ``config`` are wired up at bootstrap. Feature commands
(``run``, ``ingest``, ``index``, ``search``, ``review``) land in their sprints.
"""

from __future__ import annotations

import json

import typer

from issue_to_patch import __version__
from issue_to_patch.config import get_settings
from issue_to_patch.logging import configure_logging

app = typer.Typer(add_completion=False, help="Issue-to-Patch Automation Pipeline")


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


if __name__ == "__main__":
    app()
