"""The FastAPI app. ``make serve`` (or ``uvicorn issue_to_patch.api.app:app``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from issue_to_patch import __version__
from issue_to_patch.api.issue_plans import router as plans_router
from issue_to_patch.api.routes import runs_router, tools_router, users_router
from issue_to_patch.api.ui import ui_router
from issue_to_patch.config import get_settings
from issue_to_patch.persistence import Store
from issue_to_patch.planning.service import interrupt_unfinished


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.database_url)
    store.create_all()
    interrupt_unfinished(store)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Issue-to-Patch Automation Pipeline",
    description="Turns a GitHub issue into a validated .patch file.",
    version=__version__,
)
app.include_router(runs_router)
app.include_router(tools_router)
app.include_router(users_router)
app.include_router(ui_router)

app.include_router(plans_router)
