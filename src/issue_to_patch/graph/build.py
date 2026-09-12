"""Assemble the reasoning graph: 12 nodes, the deterministic router between
them, and a checkpointer that pauses the run at the human-review gate."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from issue_to_patch.graph import nodes, router
from issue_to_patch.graph.checkpoint import default_checkpointer
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.state import InvestigationState

InvestigationGraph = CompiledStateGraph[
    InvestigationState, None, InvestigationState, InvestigationState
]
_NodeFn = Callable[[InvestigationState, GraphDependencies], dict[str, object]]

_NORMALIZE_REQUEST = "NormalizeRequest"
_PLAN_INVESTIGATION = router.PLAN_INVESTIGATION
_RETRIEVE_ISSUE_CONTEXT = "RetrieveIssueContext"
_RETRIEVE_CODE_CONTEXT = "RetrieveCodeContext"
_ANALYZE_ROOT_CAUSE = router.ANALYZE_ROOT_CAUSE
_SELECT_ADDITIONAL_EVIDENCE = router.SELECT_ADDITIONAL_EVIDENCE
_DRAFT_PATCH = router.DRAFT_PATCH
_RUN_PATCH_VALIDATION = router.RUN_PATCH_VALIDATION
_REQUEST_HUMAN_VALIDATION = router.REQUEST_HUMAN_VALIDATION
_REVISE_PATCH = router.REVISE_PATCH
_EVALUATE_RUN = router.EVALUATE_RUN
_PERSIST_RUN = router.PERSIST_RUN


def build_graph(
    deps: GraphDependencies, *, checkpointer: BaseCheckpointSaver[str] | None = None
) -> InvestigationGraph:
    settings = deps.settings
    graph = StateGraph(InvestigationState)

    def node(fn: _NodeFn) -> Any:
        # LangGraph's add_node overloads resolve NodeInputT against a Union of
        # Protocols; mypy cannot solve that for a plain closure even though the
        # closure's own signature (checked via _NodeFn above) is exactly right.
        # Typing the boundary as Any keeps the real contract — fn matches
        # _NodeFn — checked, without fighting a library-typing limitation.
        def run(state: InvestigationState) -> dict[str, object]:
            return fn(state, deps)

        return run

    graph.add_node(_NORMALIZE_REQUEST, node(nodes.normalize_request))
    graph.add_node(_PLAN_INVESTIGATION, node(nodes.plan_investigation))
    graph.add_node(_RETRIEVE_ISSUE_CONTEXT, node(nodes.retrieve_issue_context))
    graph.add_node(_RETRIEVE_CODE_CONTEXT, node(nodes.retrieve_code_context))
    graph.add_node(_ANALYZE_ROOT_CAUSE, node(nodes.analyze_root_cause))
    graph.add_node(_SELECT_ADDITIONAL_EVIDENCE, node(nodes.select_additional_evidence))
    graph.add_node(_DRAFT_PATCH, node(nodes.draft_patch))
    graph.add_node(_RUN_PATCH_VALIDATION, node(nodes.run_patch_validation))
    graph.add_node(_REQUEST_HUMAN_VALIDATION, node(nodes.request_human_validation))
    graph.add_node(_REVISE_PATCH, node(nodes.revise_patch))
    graph.add_node(_EVALUATE_RUN, node(nodes.evaluate_run))
    graph.add_node(_PERSIST_RUN, node(nodes.persist_run))

    graph.set_entry_point(_NORMALIZE_REQUEST)
    graph.add_conditional_edges(_NORMALIZE_REQUEST, router.route_after_normalize)
    graph.add_edge(_PLAN_INVESTIGATION, _RETRIEVE_ISSUE_CONTEXT)
    graph.add_edge(_RETRIEVE_ISSUE_CONTEXT, _RETRIEVE_CODE_CONTEXT)
    graph.add_conditional_edges(
        _RETRIEVE_CODE_CONTEXT, partial(router.route_after_retrieve_code, settings=settings)
    )
    graph.add_edge(_SELECT_ADDITIONAL_EVIDENCE, _ANALYZE_ROOT_CAUSE)
    graph.add_conditional_edges(
        _ANALYZE_ROOT_CAUSE, partial(router.route_after_analyze, settings=settings)
    )
    route_after_draft = partial(router.route_after_draft, settings=settings)
    graph.add_conditional_edges(_DRAFT_PATCH, route_after_draft)
    graph.add_conditional_edges(
        _RUN_PATCH_VALIDATION, partial(router.route_after_validation, settings=settings)
    )
    graph.add_conditional_edges(
        _REQUEST_HUMAN_VALIDATION, partial(router.route_after_human, settings=settings)
    )
    # RevisePatch's own attempt can also fail to apply, so it needs the same
    # candidate_patch-is-None check DraftPatch does, not a fixed edge.
    graph.add_conditional_edges(_REVISE_PATCH, route_after_draft)
    graph.add_edge(_EVALUATE_RUN, _PERSIST_RUN)
    graph.add_edge(_PERSIST_RUN, END)

    return graph.compile(
        checkpointer=checkpointer or default_checkpointer(),
        interrupt_before=[_REQUEST_HUMAN_VALIDATION],
    )
