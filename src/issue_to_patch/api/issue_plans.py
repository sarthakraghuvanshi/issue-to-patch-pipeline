"""Authenticated read-only planning jobs and snapshot evidence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError

from issue_to_patch.api.deps import (
    CurrentUserDep,
    GraphDepsDep,
    StoreDep,
    get_current_user,
    rate_limit,
)
from issue_to_patch.persistence.models import IssuePlanRow
from issue_to_patch.planning.models import (
    ACTIVE,
    PlanAccepted,
    PlanRequest,
    PlanResponse,
    PlanSource,
    PlanStatus,
    PlanSummary,
    SavedPlan,
)
from issue_to_patch.planning.service import canonical_issue, generate_plan, read_plan

router = APIRouter(
    prefix="/issue-plans",
    tags=["issue-plans"],
    dependencies=[Depends(get_current_user), Depends(rate_limit)],
)


_creation_lock = Lock()  # Background planning uses a single application worker.


def owned_plan(plan_id: str, store: StoreDep, user: CurrentUserDep) -> IssuePlanRow:
    try:
        row = read_plan(store, plan_id)
    except LookupError as exc:
        raise HTTPException(404, "Plan not found.") from exc
    if row.owner != user.username:
        raise HTTPException(404, "Plan not found.")
    return row


@router.get("", response_model=list[PlanSummary])
def list_plans(
    store: StoreDep,
    user: CurrentUserDep,
    issue_url: Annotated[list[str], Query(max_length=30)],
) -> list[PlanSummary]:
    try:
        urls = [canonical_issue(url) for url in issue_url]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with store.session() as session:
        rows = session.scalars(
            select(IssuePlanRow)
            .where(IssuePlanRow.owner == user.username, IssuePlanRow.issue_url.in_(urls))
            .order_by(IssuePlanRow.created_at.desc(), IssuePlanRow.plan_id.desc())
        ).all()
        summaries: dict[str, PlanSummary] = {}
        for row in rows:
            if row.issue_url not in summaries:
                summaries[row.issue_url] = PlanSummary(
                    plan_id=row.plan_id,
                    issue_url=row.issue_url,
                    status=cast(PlanStatus, row.status),
                    has_result=bool(row.payload.get("result")),
                )
        return list(summaries.values())


@router.post("", response_model=PlanAccepted, status_code=202)
def create_plan(
    body: PlanRequest, tasks: BackgroundTasks, deps: GraphDepsDep, user: CurrentUserDep
) -> PlanAccepted:
    with _creation_lock:
        return _get_or_create_plan(body, tasks, deps, user)


def _get_or_create_plan(
    body: PlanRequest, tasks: BackgroundTasks, deps: GraphDepsDep, user: CurrentUserDep
) -> PlanAccepted:
    try:
        issue_url = canonical_issue(body.issue_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with deps.store.session() as session:
        existing = session.scalar(
            select(IssuePlanRow)
            .where(IssuePlanRow.owner == user.username, IssuePlanRow.issue_url == issue_url)
            .order_by(IssuePlanRow.created_at.desc(), IssuePlanRow.plan_id.desc())
            .limit(1)
        )
        if existing is not None:
            return PlanAccepted(plan_id=existing.plan_id)
    plan_id = uuid.uuid4().hex
    try:
        with deps.store.session() as session:
            session.add(
                IssuePlanRow(
                    plan_id=plan_id,
                    owner=user.username,
                    request_id=body.request_id,
                    issue_url=issue_url,
                    status="fetching",
                    payload=SavedPlan().model_dump(mode="json"),
                    cost_usd=0,
                    created_at=datetime.now(UTC),
                )
            )
    except IntegrityError:
        with deps.store.session() as session:
            existing = session.scalar(
                select(IssuePlanRow).where(
                    IssuePlanRow.request_id == body.request_id,
                    IssuePlanRow.owner == user.username,
                    IssuePlanRow.issue_url == issue_url,
                )
            )
            if existing is None:
                raise HTTPException(409, "Request identifier is already in use.") from None
            return PlanAccepted(plan_id=existing.plan_id)
    tasks.add_task(generate_plan, plan_id, deps)
    return PlanAccepted(plan_id=plan_id)


@router.get("/{plan_id}", response_model=PlanResponse)
def get_plan(plan_id: str, store: StoreDep, user: CurrentUserDep) -> PlanResponse:
    row = owned_plan(plan_id, store, user)
    saved = SavedPlan.model_validate(row.payload)
    return PlanResponse(
        plan_id=plan_id,
        issue_url=row.issue_url,
        status=cast(PlanStatus, row.status),
        commit_sha=saved.commit_sha,
        result=saved.result,
        sources=saved.sources,
        context_truncated=saved.context_truncated,
        cost_usd=row.cost_usd,
        error=row.error,
    )


@router.post("/{plan_id}/regenerate", response_model=PlanAccepted, status_code=202)
def regenerate(
    plan_id: str, tasks: BackgroundTasks, deps: GraphDepsDep, user: CurrentUserDep
) -> PlanAccepted:
    row = owned_plan(plan_id, deps.store, user)
    saved = SavedPlan.model_validate(row.payload)
    with deps.store.session() as session:
        updated = session.execute(
            update(IssuePlanRow)
            .where(IssuePlanRow.plan_id == plan_id, IssuePlanRow.status.not_in(ACTIVE))
            .values(status="retrieving" if saved.snapshot else "fetching", error=None)
        )
        if isinstance(updated, CursorResult) and updated.rowcount:
            tasks.add_task(generate_plan, plan_id, deps)
    return PlanAccepted(plan_id=plan_id)


@router.get("/{plan_id}/source", response_model=PlanSource)
def source(plan_id: str, chunk_id: str, store: StoreDep, user: CurrentUserDep) -> PlanSource:
    row = owned_plan(plan_id, store, user)
    saved = SavedPlan.model_validate(row.payload)
    for item in saved.sources:
        if item.chunk_id == chunk_id:
            return item
    raise HTTPException(404, "Source was not included in this plan's evidence.")
