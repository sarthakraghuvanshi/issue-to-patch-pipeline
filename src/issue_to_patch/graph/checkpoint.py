"""Checkpointers for the reasoning graph.

Two flavors, same allowlist:

* :func:`default_checkpointer` — process-local (``MemorySaver``). A run can
  pause at the human-review node and resume later *within the same process*
  (Sprint 5's tests and the `investigate` CLI demo). Gone once the process
  exits.
* :func:`sqlite_checkpointer` — a file on disk. The API (Sprint 6) uses this
  one so a run started by one HTTP request can be approved by a completely
  different request — even a different worker process — because the paused
  state lives in the file, not in a Python object only that first request held.
  A Postgres-backed equivalent for staging/prod is a drop-in swap behind the
  same ``BaseCheckpointSaver`` interface once that infra is actually running.

The state carries Pydantic models (``Evidence``, ``Hypothesis``, ...) as
values, not just plain dicts, so every claim keeps its type. LangGraph's
default serializer will happily round-trip them but refuses to do so silently
in a future version unless the exact (module, class) pairs are allowlisted —
so we allowlist our own state models explicitly here instead of turning that
check off wholesale.

That allowlist has to be built into the serializer's *constructor*, not
added after the fact with ``.with_allowlist(...)``: both ``MemorySaver()``
and ``SqliteSaver()`` default to an already-maximally-permissive allowlist
(``True`` — allow anything, just warn), and ``.with_allowlist()`` only ever
*adds* entries to whatever allowlist is already there. Adding entries to
"already allows everything" is a no-op, so calling it on a fresh saver
silently keeps the permissive default instead of tightening it — which is
exactly what this module did for most of Sprint 5 and 6 before this was
caught.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

_STATE_MODELS: list[tuple[str, str]] = [
    ("issue_to_patch.graph.state", "SourceLocation"),
    ("issue_to_patch.graph.state", "Evidence"),
    ("issue_to_patch.graph.state", "Hypothesis"),
    ("issue_to_patch.graph.state", "InvestigationPlan"),
    ("issue_to_patch.graph.state", "HumanDecision"),
    ("issue_to_patch.graph.state", "EvaluationReport"),
    ("issue_to_patch.ingestion.models", "IssueSource"),
    ("issue_to_patch.ingestion.models", "IssueRequest"),
    ("issue_to_patch.ingestion.models", "RepositorySnapshot"),
    ("issue_to_patch.patching.models", "EditPlan"),
    ("issue_to_patch.patching.models", "FileEdit"),
    ("issue_to_patch.patching.models", "PatchArtifact"),
    ("issue_to_patch.patching.models", "CheckStatus"),
    ("issue_to_patch.patching.models", "CheckResult"),
    ("issue_to_patch.patching.models", "ValidationReport"),
    ("issue_to_patch.run_states", "RunState"),
]


def _allowlisted_serde() -> JsonPlusSerializer:
    """A serializer that is *strict by allowlist*: a (module, class) pair not
    in the list above comes back as the constructor's raw positional args
    instead of the real typed object — not an immediate error, but a
    guaranteed one the moment downstream code touches an attribute on what it
    expected to be, say, a ``SourceLocation``. If a new Pydantic model gets
    added to InvestigationState, register it here; ``tests/graph/test_checkpoint.py``
    fails loudly (via ``caplog``) if a run's checkpoint ever needed a type
    this list doesn't have.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=list(_STATE_MODELS))


def default_checkpointer() -> BaseCheckpointSaver[str]:
    """A fresh, process-local checkpointer with our state models allowlisted."""
    return MemorySaver(serde=_allowlisted_serde())


def sqlite_checkpointer(db_path: str | Path) -> BaseCheckpointSaver[str]:
    """A checkpointer backed by a SQLite file at ``db_path``, allowlisted the
    same way. Creates its tables (idempotent) if they don't exist yet.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may run a sync dependency in a
    # worker thread different from the one that opened the connection.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn, serde=_allowlisted_serde())
    saver.setup()
    return saver
