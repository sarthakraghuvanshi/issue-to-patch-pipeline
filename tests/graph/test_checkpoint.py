"""The checkpoint serializer allowlist is strict, not decorative.

This module exists because a "cleaner" refactor once made it a no-op: both
``MemorySaver()`` and ``SqliteSaver()`` default to an already-maximally-
permissive allowlist (``True``), and ``.with_allowlist(...)`` only ever
*adds* entries — adding entries to "already allows everything" changes
nothing. The allowlist has to be built into the serializer's constructor
instead (see ``graph/checkpoint.py``); these tests catch a regression back
to the no-op version, and catch a new state model that forgot to register.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from issue_to_patch.graph.checkpoint import (
    _allowlisted_serde,
    default_checkpointer,
    sqlite_checkpointer,
)
from issue_to_patch.graph.deps import GraphDependencies
from issue_to_patch.graph.run import resume_investigation, start_investigation
from issue_to_patch.graph.state import HumanDecision, SourceLocation
from issue_to_patch.ingestion.snapshot import create_snapshot
from issue_to_patch.llm.client import FakeLLM
from issue_to_patch.persistence import Store
from issue_to_patch.processing import index_snapshot
from issue_to_patch.retrieval import RetrievalService
from issue_to_patch.retrieval.models import SearchFilters

pytestmark = pytest.mark.integration

_BLOCKED_OR_WARNED = ("Blocked deserialization", "unregistered type")
_FIX = {
    "message": "fix: correct add()",
    "edits": [
        {
            "path": "calculator.py",
            "old": "return a - b  # BUG: should be a + b",
            "new": "return a + b",
        }
    ],
}


def _has_blocked_or_warned_log(records: list[logging.LogRecord]) -> bool:
    return any(any(s in r.getMessage() for s in _BLOCKED_OR_WARNED) for r in records)


def test_an_allowlisted_state_model_round_trips_as_its_real_type() -> None:
    serde = _allowlisted_serde()
    loc = SourceLocation(chunk_id="c1", path="a.py", line_start=1, line_end=2)
    back = serde.loads_typed(serde.dumps_typed(loc))
    assert isinstance(back, SourceLocation)
    assert back == loc


def test_the_allowlist_actually_blocks_an_unregistered_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Proof the list is enforced, not cosmetic: a real Pydantic model that is
    NOT in graph/checkpoint.py's _STATE_MODELS must not silently round-trip
    as itself — if it did, the allowlist would once again be a no-op."""
    serde = _allowlisted_serde()
    filters = SearchFilters(repository="r", commit_sha="s")
    with caplog.at_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus"):
        back = serde.loads_typed(serde.dumps_typed(filters))
    assert not isinstance(back, SearchFilters)
    assert _has_blocked_or_warned_log(caplog.records)


def test_default_checkpointer_uses_the_strict_allowlist_not_the_permissive_default() -> None:
    saver = default_checkpointer()
    # ._allowed_msgpack_modules is True on the permissive default this
    # regresses to; anything else (our explicit tuple) means it's real.
    assert saver.serde._allowed_msgpack_modules is not True  # type: ignore[attr-defined]


def test_sqlite_checkpointer_uses_the_strict_allowlist_not_the_permissive_default(tmp_path) -> None:
    saver = sqlite_checkpointer(tmp_path / "cp.db")
    assert saver.serde._allowed_msgpack_modules is not True  # type: ignore[attr-defined]


@pytest.mark.parametrize("checkpointer_kind", ["memory", "sqlite"])
def test_a_full_run_never_hits_a_blocked_or_warned_type(
    checkpointer_kind: str, fixture_repo: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Every real type that ends up in InvestigationState during an actual
    investigation must be on the allowlist — this is what would have caught
    the missing IssueSource/CheckStatus/RunState entries."""
    from issue_to_patch.config.settings import Settings

    snap = create_snapshot(str(fixture_repo), tmp_path / "snap", repo_name="acme/calc")
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'g.db'}")
    store.create_all()
    index_snapshot(snap, store)
    llm = FakeLLM()
    llm.queue_structured({"search_queries": ["add returns wrong result"], "focus_areas": []})
    llm.queue_structured(
        {"hypotheses": [{"summary": "subtracts instead of adds", "confidence": 0.9}]}
    )
    llm.queue_structured(_FIX)
    deps = GraphDependencies(
        llm=llm,
        retrieval=RetrievalService(store),
        store=store,
        settings=Settings(artifacts_dir=tmp_path / "artifacts"),
    )
    checkpointer = (
        default_checkpointer()
        if checkpointer_kind == "memory"
        else sqlite_checkpointer(tmp_path / "cp.db")
    )

    with caplog.at_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus"):
        handle = start_investigation(
            issue_ref="add() returns the wrong result",
            repository=snap,
            deps=deps,
            allowed_scope=["calculator.py"],
            checkpointer=checkpointer,
        )
        resume_investigation(handle, HumanDecision(decision="approve"))

    assert not _has_blocked_or_warned_log(caplog.records), [r.getMessage() for r in caplog.records]
