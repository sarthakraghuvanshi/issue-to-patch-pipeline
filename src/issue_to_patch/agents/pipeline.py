"""The multi-agent path (Phase 6): six specialists producing typed, cited
artifacts in sequence, instead of one LLM call doing the analysis and one
doing the draft. Enabled via ``ITP_AGENT_MODE=multi``; the single-agent path
(Sprint 5) stays the default, so the two run on the exact same graph and are
directly comparable.

Only three of the six actually call the LLM (Issue Analyst, Root-Cause
Analyst, Patch Author, Patch Reviewer — four, not three: judgment calls).
Repository Cartographer and Test Strategist are deterministic: filtering and
grouping evidence retrieval already found, and matching test-file naming
conventions, don't benefit from a fresh LLM guess — Sprint 4/1's own
machinery (``is_probably_test_path``) already does it reliably.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, Field

from issue_to_patch.agents.models import IssueAnalysis, PatchReview, RepoMap, TestPlan
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.state import Evidence, Hypothesis
from issue_to_patch.ingestion.models import IssueRequest
from issue_to_patch.llm.client import Message
from issue_to_patch.patching.models import EditPlan
from issue_to_patch.processing.parser import is_probably_test_path


class _HypothesesResponse(BaseModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list)


# -- Issue Analyst (LLM) -----------------------------------------------------
def issue_analyst(issue: IssueRequest, deps: GraphDependencies) -> IssueAnalysis:
    messages = [
        Message(
            role="system",
            content=(
                "Extract the symptoms, expected behavior, and acceptance criteria from "
                "this bug report. Don't guess at code causes yet - that's a later step."
            ),
        ),
        Message(role="user", content=f"{issue.title}\n\n{issue.body}"),
    ]
    return deps.llm.structured(messages, IssueAnalysis)


# -- Repository Cartographer (deterministic) --------------------------------
def repository_cartographer(code_evidence: list[Evidence]) -> RepoMap:
    paths: list[str] = []
    symbols: list[str] = []
    tests: list[str] = []
    seen_paths: set[str] = set()
    for e in code_evidence:
        loc = e.source_location
        if loc is None:
            continue
        if loc.path not in seen_paths:
            seen_paths.add(loc.path)
            (tests if is_probably_test_path(loc.path) else paths).append(loc.path)
        if loc.symbol and loc.symbol not in symbols:
            symbols.append(loc.symbol)
    return RepoMap(likely_paths=paths, likely_symbols=symbols, related_test_paths=tests)


# -- Root-Cause Analyst (LLM) ------------------------------------------------
def root_cause_analyst(
    issue: IssueRequest,
    analysis: IssueAnalysis,
    repo_map: RepoMap,
    code_evidence: list[Evidence],
    deps: GraphDependencies,
) -> list[Hypothesis]:
    catalog = "\n\n".join(
        f"[{e.source_location.chunk_id}] {e.source_location.path}:"
        f"{e.source_location.line_start}-{e.source_location.line_end}\n{e.content[:800]}"
        for e in code_evidence
        if e.source_location and e.source_location.path in repo_map.likely_paths
    )
    messages = [
        Message(
            role="system",
            content=(
                "Compare the issue analysis against the retrieved evidence and propose "
                "root-cause hypotheses. Every hypothesis must cite at least one of the "
                "chunk ids given below - never invent a location."
            ),
        ),
        Message(
            role="user",
            content=(
                f"Issue: {issue.title}\n{issue.body}\n\n"
                f"Symptoms: {analysis.symptoms}\n"
                f"Expected behavior: {analysis.expected_behavior}\n\n"
                f"Likely modules: {repo_map.likely_paths}\n"
                f"Likely symbols: {repo_map.likely_symbols}\n\n"
                f"Evidence:\n{catalog}"
            ),
        ),
    ]
    response = deps.llm.structured(messages, _HypothesesResponse)
    return response.hypotheses


# -- Patch Author (LLM) -------------------------------------------------------
def patch_author(
    issue: IssueRequest,
    hypothesis: Hypothesis | None,
    code_evidence: list[Evidence],
    deps: GraphDependencies,
    *,
    revision_note: str = "",
) -> EditPlan:
    # SourceLocation isn't hashable (a plain pydantic model), so this has to
    # stay a list membership check, not a set.
    cited = hypothesis.cites if hypothesis else []
    files_catalog = "\n\n".join(
        f"{e.source_location.path}:{e.source_location.line_start}-{e.source_location.line_end}\n"
        f"{e.content}"
        for e in code_evidence
        if e.source_location and e.source_location in cited
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
                f"Root cause: {hypothesis.summary if hypothesis else 'unknown'}\n\n"
                f"Files:\n{files_catalog}\n\n{revision_note}"
            ),
        ),
    ]
    return deps.llm.structured(messages, EditPlan)


# -- Test Strategist (deterministic) -----------------------------------------
def select_covering_tests(repo_map: RepoMap, edit_plan: EditPlan) -> TestPlan:
    changed_stems = {PurePosixPath(p).stem for p in edit_plan.paths()}
    matched = [t for t in repo_map.related_test_paths if any(stem in t for stem in changed_stems)]
    return TestPlan(existing_tests=matched or repo_map.related_test_paths)


# -- Patch Reviewer (LLM) ------------------------------------------------------
def patch_reviewer(
    issue: IssueRequest,
    edit_plan: EditPlan,
    test_plan: TestPlan,
    allowed_scope: list[str],
    deps: GraphDependencies,
) -> PatchReview:
    messages = [
        Message(
            role="system",
            content=(
                "Review this edit plan for scope, correctness, security, and "
                "compatibility before it's even attempted against the real repo. "
                "Reject it if it looks unrelated to the issue, touches more than "
                "the allowed scope, or looks unsafe."
            ),
        ),
        Message(
            role="user",
            content=(
                f"Issue: {issue.title}\n\n"
                f"Edit plan message: {edit_plan.message}\n"
                f"Changed paths: {edit_plan.paths()}\n"
                f"Allowed scope: {allowed_scope or '(none given)'}\n"
                f"Covering tests: {test_plan.existing_tests}"
            ),
        ),
    ]
    return deps.llm.structured(messages, PatchReview)


# -- The two pipelines the graph nodes call -----------------------------------
def run_analysis_pipeline(
    issue: IssueRequest, code_evidence: list[Evidence], deps: GraphDependencies
) -> list[Hypothesis]:
    analysis = issue_analyst(issue, deps)
    repo_map = repository_cartographer(code_evidence)
    return root_cause_analyst(issue, analysis, repo_map, code_evidence, deps)


def run_draft_pipeline(
    issue: IssueRequest,
    hypothesis: Hypothesis | None,
    code_evidence: list[Evidence],
    allowed_scope: list[str],
    deps: GraphDependencies,
    *,
    revision_note: str = "",
) -> EditPlan | None:
    """None means the Patch Reviewer rejected the draft — the caller treats
    that exactly like an edit plan that failed to apply (revise-or-reject),
    never as a crash."""
    edit_plan = patch_author(issue, hypothesis, code_evidence, deps, revision_note=revision_note)
    repo_map = repository_cartographer(code_evidence)
    test_plan = select_covering_tests(repo_map, edit_plan)
    review = patch_reviewer(issue, edit_plan, test_plan, allowed_scope, deps)
    return edit_plan if review.approved else None
