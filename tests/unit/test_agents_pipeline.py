"""agents/pipeline.py: each of the six specialists in isolation, then the two
pipelines (analysis, draft) that chain them together."""

from __future__ import annotations

from pathlib import Path

from issue_to_patch.agents.models import RepoMap, TestPlan
from issue_to_patch.agents.pipeline import (
    issue_analyst,
    patch_author,
    patch_reviewer,
    repository_cartographer,
    root_cause_analyst,
    run_analysis_pipeline,
    run_draft_pipeline,
    select_covering_tests,
)
from issue_to_patch.config.settings import Settings
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.state import Evidence, Hypothesis, SourceLocation
from issue_to_patch.ingestion.models import IssueRequest, IssueSource
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.patching.models import EditPlan
from issue_to_patch.persistence import Store
from issue_to_patch.retrieval import RetrievalService


def _deps(tmp_path: Path, llm: FakeLLM | None = None) -> GraphDependencies:
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'g.db'}")
    store.create_all()
    return GraphDependencies(
        llm=llm or FakeLLM(),
        retrieval=RetrievalService(store),
        store=store,
        settings=Settings(artifacts_dir=tmp_path / "artifacts"),
    )


def _issue(title: str = "add() is wrong", body: str = "returns a-b instead of a+b") -> IssueRequest:
    return IssueRequest(source=IssueSource.RAW_TEXT, source_ref="x", title=title, body=body)


def _loc(chunk_id: str, path: str, symbol: str | None = None) -> SourceLocation:
    return SourceLocation(chunk_id=chunk_id, path=path, line_start=1, line_end=2, symbol=symbol)


def _evidence(
    chunk_id: str, path: str, symbol: str | None = None, content: str = "code"
) -> Evidence:
    return Evidence(
        kind="code", content=content, source_location=_loc(chunk_id, path, symbol), score=0.8
    )


# -- Issue Analyst -----------------------------------------------------------
def test_issue_analyst_returns_the_llms_structured_analysis(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(
        {
            "symptoms": ["wrong sum"],
            "expected_behavior": "a+b",
            "acceptance_criteria": ["add(2,3)==5"],
        }
    )
    analysis = issue_analyst(_issue(), _deps(tmp_path, llm))
    assert analysis.symptoms == ["wrong sum"]
    assert analysis.expected_behavior == "a+b"


# -- Repository Cartographer --------------------------------------------------
def test_repository_cartographer_separates_tests_from_source_and_dedupes() -> None:
    evidence = [
        _evidence("c1", "src/calculator.py", symbol="add"),
        _evidence("c2", "src/calculator.py", symbol="subtract"),  # same path again
        _evidence("c3", "tests/test_calculator.py", symbol="test_add"),
        Evidence(kind="issue", content="no location"),  # no source_location -> skipped
    ]
    repo_map = repository_cartographer(evidence)
    assert repo_map.likely_paths == ["src/calculator.py"]
    assert repo_map.related_test_paths == ["tests/test_calculator.py"]
    assert repo_map.likely_symbols == ["add", "subtract", "test_add"]


def test_repository_cartographer_on_empty_evidence_is_empty() -> None:
    repo_map = repository_cartographer([])
    assert repo_map == RepoMap()


# -- Root-Cause Analyst --------------------------------------------------------
def test_root_cause_analyst_only_shows_the_llm_evidence_from_likely_paths(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(
        {"hypotheses": [{"summary": "off by sign", "confidence": 0.9, "cites": []}]}
    )
    evidence = [
        _evidence("c1", "src/calculator.py"),
        _evidence("c2", "README.md"),  # not in likely_paths
    ]
    repo_map = RepoMap(likely_paths=["src/calculator.py"], likely_symbols=[], related_test_paths=[])
    hypotheses = root_cause_analyst(_issue(), _analysis(), repo_map, evidence, _deps(tmp_path, llm))
    assert hypotheses[0].summary == "off by sign"
    # the catalog shown to the LLM only mentions the likely path
    sent = llm.calls[0][-1].content
    assert "src/calculator.py" in sent
    assert "README.md" not in sent


def _analysis():
    from issue_to_patch.agents.models import IssueAnalysis

    return IssueAnalysis(symptoms=["wrong sum"], expected_behavior="a+b")


# -- Patch Author --------------------------------------------------------------
def test_patch_author_only_shows_the_llm_cited_evidence(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured(
        {"message": "fix", "edits": [{"path": "src/calculator.py", "old": "a - b", "new": "a + b"}]}
    )
    cited_loc = _loc("c1", "src/calculator.py")
    hypothesis = Hypothesis(summary="off by sign", confidence=0.9, cites=[cited_loc])
    evidence = [
        Evidence(kind="code", content="return a - b", source_location=cited_loc, score=0.8),
        _evidence("c2", "README.md"),  # not cited
    ]
    edit_plan = patch_author(_issue(), hypothesis, evidence, _deps(tmp_path, llm))
    assert isinstance(edit_plan, EditPlan)
    sent = llm.calls[0][-1].content
    assert "return a - b" in sent
    assert "README.md" not in sent


def test_patch_author_handles_no_hypothesis(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured({"message": "fix", "edits": [{"path": "x.py", "old": "a", "new": "b"}]})
    edit_plan = patch_author(_issue(), None, [], _deps(tmp_path, llm))
    assert isinstance(edit_plan, EditPlan)


# -- Test Strategist -----------------------------------------------------------
def test_strategist_matches_tests_by_filename_stem() -> None:
    repo_map = RepoMap(
        likely_paths=["src/calculator.py"],
        likely_symbols=[],
        related_test_paths=["tests/test_calculator.py", "tests/test_unrelated.py"],
    )
    edit_plan = EditPlan(
        message="fix", edits=[{"path": "src/calculator.py", "old": "a", "new": "b"}]
    )
    plan = select_covering_tests(repo_map, edit_plan)
    assert plan.existing_tests == ["tests/test_calculator.py"]


def test_strategist_falls_back_to_all_related_tests_when_nothing_matches() -> None:
    repo_map = RepoMap(
        likely_paths=[], likely_symbols=[], related_test_paths=["tests/test_other.py"]
    )
    edit_plan = EditPlan(
        message="fix", edits=[{"path": "src/calculator.py", "old": "a", "new": "b"}]
    )
    plan = select_covering_tests(repo_map, edit_plan)
    assert plan.existing_tests == ["tests/test_other.py"]


# -- Patch Reviewer -------------------------------------------------------------
def test_patch_reviewer_returns_the_llms_verdict(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured({"approved": False, "concerns": ["touches unrelated file"]})
    edit_plan = EditPlan(message="fix", edits=[{"path": "x.py", "old": "a", "new": "b"}])
    review = patch_reviewer(_issue(), edit_plan, TestPlan(), ["x.py"], _deps(tmp_path, llm))
    assert review.approved is False
    assert review.concerns == ["touches unrelated file"]


# -- run_analysis_pipeline / run_draft_pipeline --------------------------------
def test_run_analysis_pipeline_chains_issue_analyst_and_root_cause_analyst(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured({"symptoms": ["wrong sum"], "expected_behavior": "a+b"})
    llm.queue_structured({"hypotheses": [{"summary": "off by sign", "confidence": 0.9}]})
    evidence = [_evidence("c1", "src/calculator.py")]
    hypotheses = run_analysis_pipeline(_issue(), evidence, _deps(tmp_path, llm))
    assert hypotheses[0].summary == "off by sign"


def test_run_draft_pipeline_returns_none_when_the_reviewer_rejects(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured({"message": "fix", "edits": [{"path": "x.py", "old": "a", "new": "b"}]})
    llm.queue_structured({"approved": False, "concerns": ["too risky"]})
    result = run_draft_pipeline(_issue(), None, [], [], _deps(tmp_path, llm))
    assert result is None


def test_run_draft_pipeline_returns_the_edit_plan_when_approved(tmp_path: Path) -> None:
    llm = FakeLLM()
    llm.queue_structured({"message": "fix", "edits": [{"path": "x.py", "old": "a", "new": "b"}]})
    llm.queue_structured({"approved": True})
    result = run_draft_pipeline(_issue(), None, [], [], _deps(tmp_path, llm))
    assert result is not None
    assert result.message == "fix"
