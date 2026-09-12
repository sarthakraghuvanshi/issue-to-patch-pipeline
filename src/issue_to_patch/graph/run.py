"""End-to-end orchestration: build the initial state, run the graph to its
first pause (or straight to completion), and resume it once a human decision
arrives.

Kept separate from ``graph/build.py`` because this is where a run's ``Store``
row and the graph's checkpoint thread get tied together — both keyed by the
same ``run_id`` — while the graph itself has no idea a database exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from issue_to_patch.graph.build import InvestigationGraph, build_graph
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.state import HumanDecision, InvestigationState, new_state
from issue_to_patch.ingestion.models import RepositorySnapshot, stable_hash
from issue_to_patch.logging import bind_run_id


class UnknownRun(KeyError):
    """No checkpoint exists for this run_id (never started, or a different store)."""


@dataclass
class InvestigationHandle:
    run_id: str
    graph: InvestigationGraph
    state: InvestigationState
    awaiting_human: bool


def _config(run_id: str) -> RunnableConfig:
    return RunnableConfig(configurable={"thread_id": run_id})


def start_investigation(
    *,
    issue_ref: str,
    repository: RepositorySnapshot,
    deps: GraphDependencies,
    allowed_scope: list[str] | None = None,
    run_id: str | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> InvestigationHandle:
    """Create the run row, then drive the graph to its first pause or END.

    ``checkpointer`` defaults to the process-local one (fine for the CLI and
    tests); pass a durable one (``graph.checkpoint.sqlite_checkpointer``) so a
    *different* request/process can later inspect or resume this same run —
    that's what the API does.
    """
    run_id = run_id or uuid.uuid4().hex[:16]
    graph = build_graph(deps, checkpointer=checkpointer)
    initial = new_state(run_id=run_id, issue_ref=issue_ref, allowed_scope=allowed_scope)
    initial["repository"] = repository

    deps.store.create_run(
        run_id=run_id,
        issue_ref=issue_ref,
        repo=repository.repo,
        commit_sha=repository.commit_sha,
        content_hash=stable_hash(issue_ref, repository.commit_sha),
    )

    with bind_run_id(run_id):
        result = cast(InvestigationState, graph.invoke(initial, config=_config(run_id)))
    return _handle(run_id, graph, result)


def load_investigation(
    run_id: str, deps: GraphDependencies, *, checkpointer: BaseCheckpointSaver[str]
) -> InvestigationHandle:
    """Rebuild a handle for a run this process never itself started — the
    read path for a fresh HTTP request (``GET /runs/{id}``) or the first half
    of resuming one (``POST /runs/{id}/approve``) from a durable checkpoint.
    """
    graph = build_graph(deps, checkpointer=checkpointer)
    snapshot = graph.get_state(_config(run_id))
    if not snapshot.values:
        raise UnknownRun(run_id)
    state = cast(InvestigationState, snapshot.values)
    return InvestigationHandle(
        run_id=run_id, graph=graph, state=state, awaiting_human=bool(snapshot.next)
    )


def resume_investigation(
    handle: InvestigationHandle, decision: HumanDecision
) -> InvestigationHandle:
    """Inject the human's decision and drive the graph the rest of the way.

    Works on a handle from either :func:`start_investigation` or
    :func:`load_investigation` — only ``handle.graph`` (already bound to
    whichever checkpointer built it) and the run_id matter here.
    """
    config = _config(handle.run_id)
    with bind_run_id(handle.run_id):
        handle.graph.update_state(config, {"human_decision": decision})
        result = cast(InvestigationState, handle.graph.invoke(None, config=config))
    return _handle(handle.run_id, handle.graph, result)


def _handle(
    run_id: str, graph: InvestigationGraph, result: InvestigationState
) -> InvestigationHandle:
    pending = graph.get_state(_config(run_id))
    return InvestigationHandle(
        run_id=run_id, graph=graph, state=result, awaiting_human=bool(pending.next)
    )
