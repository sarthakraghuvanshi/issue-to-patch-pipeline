"""LLM judge (Phase 9): five grounded dimensions, scored only against a run's
own stored evidence and patch — never invented. Never the sole success
signal either: RunState (deterministic, decided by the graph's own router
and validation checks) stays authoritative, this is an additional signal
recorded alongside it so runs can be compared later.
"""

from __future__ import annotations

from issue_to_patch.evaluation.models import JudgeRecord, JudgeScores
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.state import InvestigationState
from issue_to_patch.ingestion.models import stable_hash
from issue_to_patch.llm.client import Message

PROMPT_VERSION = "judge-v1"


def judge_run(run_id: str, state: InvestigationState, deps: GraphDependencies) -> JudgeRecord:
    issue = state.get("issue")
    hypotheses = state.get("hypotheses") or []
    top = hypotheses[0] if hypotheses else None
    patch = state.get("candidate_patch")
    validation = state.get("validation")

    cited_content = "\n\n".join(
        f"{loc.path}:{loc.line_start}-{loc.line_end}\n"
        f"{(row.content if (row := deps.store.get_chunk(loc.chunk_id)) is not None else '')}"
        for loc in (top.cites if top else [])
    )
    messages = [
        Message(
            role="system",
            content=(
                "Score this completed bug-fix investigation on five dimensions, each "
                "0.0-1.0: root_cause_correctness, evidence_sufficiency, patch_relevance, "
                "patch_minimality, explanation_faithfulness. Ground every score ONLY in "
                "the evidence and patch shown below - never assume anything about the "
                "repository that isn't shown. Give a short rationale."
            ),
        ),
        Message(
            role="user",
            content=(
                f"Issue: {issue.title if issue else ''}\n{issue.body if issue else ''}\n\n"
                f"Hypothesis: {top.summary if top else '(none)'}\n"
                f"Cited evidence:\n{cited_content or '(none)'}\n\n"
                f"Patch:\n{patch.patch_text if patch else '(none)'}\n\n"
                f"Validation: {validation.model_dump_json() if validation else '(none)'}"
            ),
        ),
    ]
    scores = deps.llm.structured(messages, JudgeScores)
    record = JudgeRecord(
        run_id=run_id,
        model=getattr(deps.llm, "model", "unknown"),
        prompt_version=PROMPT_VERSION,
        scores=scores,
    )

    payload = record.model_dump_json(indent=2)
    path = deps.settings.artifacts_dir / run_id / "judge.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, "utf-8")
    deps.store.record_artifact(
        run_id, kind="eval_judge", uri=str(path), content_hash=stable_hash(payload)
    )
    return record
