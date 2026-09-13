"""Typed shapes for evaluation (Phase 9): deterministic per-run metrics, the
suite-level report ``make eval`` produces, and the LLM judge's scores.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from issue_to_patch.retrieval.evaluation import RetrievalReport


class RunMetrics(BaseModel):
    """Deterministic, per-run metrics read from already-stored artifacts —
    nothing here re-runs anything or calls an LLM.

    ``test_pass_rate``/``regression_rate`` stay ``None`` until Sprint 9's
    sandbox can safely execute a target repo's own test suite; faking a
    number here would be worse than admitting it isn't measured yet.
    """

    run_id: str
    final_state: str | None
    patch_drafted: bool
    patch_applied: bool
    unrelated_file_change: bool
    latency_seconds: float | None
    cost_usd: float
    test_pass_rate: float | None = None
    regression_rate: float | None = None


class SuiteMetrics(BaseModel):
    """Aggregated across a batch of runs — the numbers ``make eval`` prints."""

    run_count: int
    patch_apply_rate: float
    unrelated_file_change_rate: float
    median_latency_seconds: float | None
    p95_latency_seconds: float | None
    median_cost_usd: float
    total_cost_usd: float
    runs: list[RunMetrics] = Field(default_factory=list)


class JudgeScores(BaseModel):
    """The 5 grounded dimensions from Phase 9. Never the sole success signal —
    RunState (deterministic) stays authoritative; this is stored alongside it
    as an additional, comparable signal."""

    root_cause_correctness: float = Field(ge=0.0, le=1.0)
    evidence_sufficiency: float = Field(ge=0.0, le=1.0)
    patch_relevance: float = Field(ge=0.0, le=1.0)
    patch_minimality: float = Field(ge=0.0, le=1.0)
    explanation_faithfulness: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class JudgeRecord(BaseModel):
    """What actually gets persisted: the scores plus enough context (model,
    prompt version) that two judge runs can be told apart later."""

    run_id: str
    model: str
    prompt_version: str
    scores: JudgeScores


class SuiteReport(BaseModel):
    """The combined output of ``make eval`` / ``eval-suite``. Any section can
    be absent — retrieval metrics need only a labeled set; suite metrics and
    judge records need a list of already-completed run_ids."""

    retrieval: RetrievalReport | None = None
    suite_metrics: SuiteMetrics | None = None
    judge_records: list[JudgeRecord] = Field(default_factory=list)
