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

from issue_to_patch.graph.build import InvestigationGraph, build_graph
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.state import HumanDecision, InvestigationState, new_state
from issue_to_patch.ingestion.models import RepositorySnapshot, stable_hash
from issue_to_patch.logging import bind_run_id


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
) -> InvestigationHandle:
    """Create the run row, then drive the graph to its first pause or END."""
    run_id = run_id or uuid.uuid4().hex[:16]
    graph = build_graph(deps)
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


def resume_investigation(
    handle: InvestigationHandle, decision: HumanDecision
) -> InvestigationHandle:
    """Inject the human's decision and drive the graph the rest of the way.

    Only valid within the process that called :func:`start_investigation` —
    the checkpointer is in-memory (see ``graph/checkpoint.py``). Resuming a
    paused run from a *different* process is Sprint 6's durable-checkpointer
    work.
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
