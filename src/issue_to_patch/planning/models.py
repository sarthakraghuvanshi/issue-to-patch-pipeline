"""Bounded model outputs and saved planning state."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    text: str = Field(min_length=1, max_length=1200)
    references: list[str] = Field(default_factory=list, max_length=12)


class ResolutionPlan(BaseModel):
    understanding: str = Field(min_length=1, max_length=2000)
    steps: list[PlanStep] = Field(min_length=3, max_length=5)
    verification: list[str] = Field(max_length=6)
    open_questions: list[str] = Field(default_factory=list, max_length=8)


class PlanSource(BaseModel):
    chunk_id: str
    path: str
    line_start: int
    line_end: int
    content: str


class SavedPlan(BaseModel):
    snapshot: str | None = None
    commit_sha: str | None = None
    issue_text: str = ""
    discussion: str = ""
    context_truncated: bool = False
    sources: list[PlanSource] = Field(default_factory=list)
    result: ResolutionPlan | None = None


PlanStatus = Literal[
    "fetching", "preparing", "retrieving", "writing", "ready", "failed", "interrupted"
]
ACTIVE = ("fetching", "preparing", "retrieving", "writing")


class PlanRequest(BaseModel):
    issue_url: str = Field(max_length=512)
    request_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-zA-Z0-9_-]{1,64}$")


class PlanAccepted(BaseModel):
    plan_id: str


class PlanResponse(BaseModel):
    plan_id: str
    issue_url: str
    status: PlanStatus
    commit_sha: str | None
    result: ResolutionPlan | None
    sources: list[PlanSource]
    context_truncated: bool
    cost_usd: float
    error: str | None


class PlanSummary(BaseModel):
    plan_id: str
    issue_url: str
    status: PlanStatus
    has_result: bool
