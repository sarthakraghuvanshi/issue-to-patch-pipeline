"""Typed artifacts the six specialist agents (Phase 6) pass between each
other. Reuses ``graph.state.Hypothesis``/``SourceLocation`` and
``patching.models.EditPlan`` rather than duplicating them — an artifact
that already exists in the single-agent path stays exactly the same shape
in the multi-agent one, so the rest of the graph (validation, the human
gate, revise-or-reject) never needs to know which mode produced it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IssueAnalysis(BaseModel):
    """Issue Analyst: what's actually being reported, in the model's own words —
    grounding for every agent downstream, not itself grounded in evidence
    (there's no code yet to cite)."""

    symptoms: list[str] = Field(default_factory=list)
    expected_behavior: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)


class RepoMap(BaseModel):
    """Repository Cartographer: which of the *already-retrieved* chunks are
    actually relevant, versus incidental. Deterministic — grouping and
    filtering what retrieval already found needs judgment about relevance,
    not a fresh guess, so this agent doesn't call the LLM at all."""

    likely_paths: list[str] = Field(default_factory=list)
    likely_symbols: list[str] = Field(default_factory=list)
    related_test_paths: list[str] = Field(default_factory=list)


class TestPlan(BaseModel):
    """Test Strategist: existing tests that likely cover the changed paths.
    Deterministic (path-convention matching, not a fresh LLM guess) — see
    RepoMap's note."""

    __test__ = False  # not a pytest test class; the name just collides with the convention

    existing_tests: list[str] = Field(default_factory=list)


class PatchReview(BaseModel):
    """Patch Reviewer: scope, correctness, security, compatibility — the
    last check before a draft is even attempted against the real repo."""

    approved: bool
    concerns: list[str] = Field(default_factory=list)
