"""The FastAPI app. ``make serve`` (or ``uvicorn issue_to_patch.api.app:app``)."""

from __future__ import annotations

from fastapi import FastAPI

from issue_to_patch import __version__
from issue_to_patch.api.routes import runs_router, tools_router

app = FastAPI(
    title="Issue-to-Patch Automation Pipeline",
    description="Turns a GitHub issue into a validated .patch file.",
    version=__version__,
)
app.include_router(runs_router)
app.include_router(tools_router)
