"""The 12 graph nodes.

Each node is a pure ``(state, deps) -> partial_state`` function: it reads
``InvestigationState``, does its one job through :class:`GraphDependencies`
(never anything else), and returns only the keys it changed — LangGraph merges
that back into the state (concatenating for the ``evidence``/``errors``/
``revisions_used`` fields, replacing for everything else; see
``graph/state.py``).

Only three nodes call the LLM (`PlanInvestigation`, `AnalyzeRootCause`,
`DraftPatch`/`RevisePatch`) and only ever to fill in a *structured field*
(a plan, a hypothesis, an edit plan) — never to choose which tool to run.
Every other node is as deterministic as Sprints 1-4 already made it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.state import (
    EvaluationReport,
    Evidence,
    Hypothesis,
    InvestigationPlan,
    InvestigationState,
    SourceLocation,
)
from issue_to_patch.ingestion.normalize import normalize_issue
from issue_to_patch.llm.client import Message
from issue_to_patch.logging import get_logger
from issue_to_patch.patching.models import EditPlan
from issue_to_patch.patching.validate import validate_patch
from issue_to_patch.patching.worktree import EditApplicationError, generate_patch
from issue_to_patch.retrieval.models import RetrievalMode, SearchFilters
from issue_to_patch.run_states import RunState

_log = get_logger("graph.nodes")

_EVIDENCE_TOP_K = 8
_EXPANDED_TOP_K = 16

# retrieve_code_context/select_additional_evidence always search in HYBRID
# mode, whose score is a reciprocal-rank-fusion sum (1/(k+rank) per ranker) —
# not a 0-1 confidence. Two rankers (bm25 + dense) at k=60 means "ranked #1 by
# both" tops out at 2/61 =~ 0.033, nowhere near ``retrieval_confidence_floor``
# (0.35 by default). We normalize against that ceiling so a chunk's Evidence
# score is a comparable 0-1 "how close to a top-of-both-rankers hit is this".
_RRF_K = 60
_RRF_RANKERS = 2
_RRF_CEILING = _RRF_RANKERS / (_RRF_K + 1)


def _confidence(raw_rrf_score: float) -> float:
    return min(1.0, raw_rrf_score / _RRF_CEILING)


# -- 1. NormalizeRequest -----------------------------------------------
def normalize_request(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    try:
        issue = normalize_issue(state["issue_ref"])
    except Exception as exc:  # a bad reference is data, not a crash
        return {"errors": [f"normalize_request: {exc}"]}
    return {"issue": issue}


# -- 2. PlanInvestigation -----------------------------------------------
def plan_investigation(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    issue = state["issue"]
    assert issue is not None  # router guarantees this before we're reached
    messages = [
        Message(
            role="system",
            content=(
                "You are investigating a software bug report. Propose 2-4 short search "
                "queries that would find the code responsible, and the areas of the "
                "codebase most likely involved."
            ),
        ),
        Message(role="user", content=f"Title: {issue.title}\n\n{issue.body}"),
    ]
    plan = deps.llm.structured(messages, InvestigationPlan)
    return {"plan": plan}


# -- 3. RetrieveIssueContext ---------------------------------------------
def retrieve_issue_context(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    """Turn the issue's own text into citable evidence (no LLM, no network)."""
    issue = state["issue"]
    assert issue is not None
    evidence = [Evidence(kind="issue", content=f"{issue.title}\n\n{issue.body}", note="issue body")]
    if issue.labels:
        evidence.append(
            Evidence(kind="issue", content=", ".join(issue.labels), note="issue labels")
        )
    return {"evidence": evidence}


# -- 4. RetrieveCodeContext -----------------------------------------------
def retrieve_code_context(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    repo = state["repository"]
    issue = state["issue"]
    assert repo is not None and issue is not None
    plan = state.get("plan")
    queries = (plan.search_queries if plan and plan.search_queries else None) or [
        f"{issue.title}\n{issue.body}"
    ]
    filters = SearchFilters(repository=repo.repo or repo.source, commit_sha=repo.commit_sha)

    seen_chunks: set[str] = set()
    evidence: list[Evidence] = []
    for query in queries:
        try:
            trace = deps.retrieval.search(
                query,
                filters,
                top_k=_EVIDENCE_TOP_K,
                mode=RetrievalMode.HYBRID,
                labels=issue.labels,
            )
        except LookupError as exc:  # nothing indexed for this repo@sha
            return {"errors": [f"retrieve_code_context: {exc}"]}
        for hit in trace.results:
            if hit.chunk_id in seen_chunks:
                continue
            seen_chunks.add(hit.chunk_id)
            row = deps.store.get_chunk(hit.chunk_id)
            content = row.content if row is not None else ""
            evidence.append(
                Evidence(
                    kind="code",
                    content=content,
                    source_location=SourceLocation(
                        chunk_id=hit.chunk_id,
                        path=hit.path,
                        line_start=hit.line_start,
                        line_end=hit.line_end,
                        symbol=hit.symbol,
                    ),
                    score=_confidence(hit.score),
                )
            )
    return {"evidence": evidence}


# -- 5. AnalyzeRootCause ---------------------------------------------------
class _HypothesesResponse(BaseModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list)


def analyze_root_cause(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    issue = state["issue"]
    assert issue is not None
    code_evidence = [e for e in state["evidence"] if e.kind == "code" and e.source_location]

    catalog = "\n\n".join(
        f"[{e.source_location.chunk_id}] {e.source_location.path}:"
        f"{e.source_location.line_start}-{e.source_location.line_end}\n{e.content[:800]}"
        for e in code_evidence
        if e.source_location
    )
    messages = [
        Message(
            role="system",
            content=(
                "Propose root-cause hypotheses for this bug. Every hypothesis must cite "
                "at least one of the chunk ids given below — never invent a location."
            ),
        ),
        Message(role="user", content=f"Issue: {issue.title}\n{issue.body}\n\nEvidence:\n{catalog}"),
    ]
    response = deps.llm.structured(messages, _HypothesesResponse)
    hypotheses = _backfill_citations(response.hypotheses, code_evidence)
    selected = hypotheses[0].cites if hypotheses else []
    return {"hypotheses": hypotheses, "selected_files": selected}


def _backfill_citations(
    hypotheses: list[Hypothesis], code_evidence: list[Evidence]
) -> list[Hypothesis]:
    """Never let a hypothesis with no citation reach a patch draft."""
    fallback = [e.source_location for e in code_evidence if e.source_location][:3]
    fixed: list[Hypothesis] = []
    for h in hypotheses:
        fixed.append(h if h.cites else h.model_copy(update={"cites": fallback}))
    return fixed


# -- 6. SelectAdditionalEvidence -------------------------------------------
def select_additional_evidence(
    state: InvestigationState, deps: GraphDependencies
) -> dict[str, object]:
    """Retrieval confidence was low (or evidence conflicted) — cast a wider net."""
    repo = state["repository"]
    issue = state["issue"]
    assert repo is not None and issue is not None
    filters = SearchFilters(repository=repo.repo or repo.source, commit_sha=repo.commit_sha)
    extra_terms = [h.summary for h in state["hypotheses"]]
    query = f"{issue.title}\n{issue.body}\n{' '.join(extra_terms)}"

    seen = {e.source_location.chunk_id for e in state["evidence"] if e.source_location}
    try:
        trace = deps.retrieval.search(
            query, filters, top_k=_EXPANDED_TOP_K, mode=RetrievalMode.HYBRID, labels=issue.labels
        )
    except LookupError as exc:  # nothing indexed for this repo@sha
        return {"errors": [f"select_additional_evidence: {exc}"], "expanded": True}
    evidence: list[Evidence] = []
    for hit in trace.results:
        if hit.chunk_id in seen:
            continue
        seen.add(hit.chunk_id)
        row = deps.store.get_chunk(hit.chunk_id)
        evidence.append(
            Evidence(
                kind="code",
                content=row.content if row is not None else "",
                source_location=SourceLocation(
                    chunk_id=hit.chunk_id,
                    path=hit.path,
                    line_start=hit.line_start,
                    line_end=hit.line_end,
                    symbol=hit.symbol,
                ),
                score=hit.score,
            )
        )
    return {"evidence": evidence, "expanded": True}


# -- 7. DraftPatch ----------------------------------------------------------
def draft_patch(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    edit_plan = _propose_edit_plan(state, deps, revision_note="")
    return _apply_edit_plan(state, edit_plan)


# -- 10. RevisePatch (shares DraftPatch's LLM call, with failure context) --
def revise_patch(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    validation = state.get("validation")
    failures = "; ".join(c.detail or c.name for c in validation.failures) if validation else ""
    edit_plan = _propose_edit_plan(
        state, deps, revision_note=f"Previous attempt failed: {failures}"
    )
    result = _apply_edit_plan(state, edit_plan)
    result["revisions_used"] = 1  # additive reducer -> total attempts so far
    return result


def _propose_edit_plan(
    state: InvestigationState, deps: GraphDependencies, *, revision_note: str
) -> EditPlan:
    issue = state["issue"]
    assert issue is not None
    top = state["hypotheses"][0] if state["hypotheses"] else None
    files_catalog = "\n\n".join(
        f"{e.source_location.path}:{e.source_location.line_start}-{e.source_location.line_end}\n"
        f"{e.content}"
        for e in state["evidence"]
        if e.kind == "code"
        and e.source_location
        and e.source_location in (top.cites if top else [])
    )
    messages = [
        Message(
            role="system",
            content=(
                "Draft the smallest edit plan that fixes the bug. Each edit's 'old' text "
                "must match the file content exactly and appear only once."
            ),
        ),
        Message(
            role="user",
            content=(
                f"Issue: {issue.title}\n{issue.body}\n\n"
                f"Root cause: {top.summary if top else 'unknown'}\n\n"
                f"Files:\n{files_catalog}\n\n{revision_note}"
            ),
        ),
    ]
    return deps.llm.structured(messages, EditPlan)


def _apply_edit_plan(state: InvestigationState, edit_plan: EditPlan) -> dict[str, object]:
    repo = state["repository"]
    assert repo is not None
    try:
        patch = generate_patch(repo, edit_plan)
    except EditApplicationError as exc:
        return {"edit_plan": edit_plan, "candidate_patch": None, "errors": [f"draft_patch: {exc}"]}
    return {"edit_plan": edit_plan, "candidate_patch": patch}


# -- 8. RunPatchValidation ---------------------------------------------------
def run_patch_validation(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    repo = state["repository"]
    patch = state["candidate_patch"]
    assert repo is not None and patch is not None
    scope = state.get("allowed_scope") or [loc.path for loc in state["selected_files"]] or ["**"]
    report = validate_patch(repo, patch, allowed_scope=scope)
    return {"validation": report}


# -- 9. RequestHumanValidation -----------------------------------------------
def request_human_validation(
    state: InvestigationState, deps: GraphDependencies
) -> dict[str, object]:
    """The interrupt point. The graph pauses *before* this node runs; by the time
    it actually executes (on resume) ``human_decision`` has already been written
    into state by the caller via ``graph.update_state``."""
    if state.get("human_decision") is None:
        return {"errors": ["request_human_validation: resumed with no human_decision set"]}
    return {}


# -- 11. EvaluateRun ----------------------------------------------------------
def evaluate_run(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    top_conf = max((h.confidence for h in state["hypotheses"]), default=0.0)
    report = EvaluationReport(
        revisions_used=state.get("revisions_used", 0),
        evidence_count=len(state["evidence"]),
        top_hypothesis_confidence=top_conf,
    )
    return {"evaluation": report}


# -- 12. PersistRun -----------------------------------------------------------
def persist_run(state: InvestigationState, deps: GraphDependencies) -> dict[str, object]:
    final_state = _determine_final_state(state)
    validation = state.get("validation")
    deps.store.finish_run(state["run_id"], state=final_state.value)
    if validation is not None:
        deps.store.record_tool_call(
            state["run_id"],
            tool="validate_patch",
            args_redacted="{}",
            result_hash=validation.model_dump_json(),
        )
    _log.info("graph.persist_run", run_id=state["run_id"], final_state=final_state.value)
    return {"final_state": final_state}


def _determine_final_state(state: InvestigationState) -> RunState:
    if state.get("errors"):
        return RunState.INVESTIGATION_INCONCLUSIVE

    decision = state.get("human_decision")
    if decision is not None:
        if decision.decision == "approve":
            return RunState.PATCH_VALIDATED
        if decision.decision == "reject":
            return RunState.PATCH_REJECTED
        return RunState.PATCH_REQUIRES_HUMAN_REVIEW  # "revise" with no budget left

    validation = state.get("validation")
    if validation is not None and validation.run_state is RunState.PATCH_REJECTED:
        return RunState.PATCH_REJECTED

    return RunState.INVESTIGATION_INCONCLUSIVE
