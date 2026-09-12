"""Checkpointer for the reasoning graph.

``MemorySaver`` is process-local: it lets a run pause at the human-review node
and resume later *within the same process* (what Sprint 5's tests and CLI
demo exercise), but it does not survive a process restart. A durable
checkpointer (SQLite locally, Postgres in staging/prod, keyed by the same
``database_url`` the rest of the app already uses) is Sprint 6's
persistence/resumability work, so a run can be approved from a different
process — e.g. an API call hitting a worker that isn't the one that paused it.

The state carries Pydantic models (``Evidence``, ``Hypothesis``, ...) as
values, not just plain dicts, so every claim keeps its type. LangGraph's
default serializer will happily round-trip them but refuses to do so silently
in a future version unless the exact (module, class) pairs are allowlisted —
so we allowlist our own state models explicitly here instead of turning that
check off wholesale.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_STATE_MODELS: list[tuple[str, str]] = [
    ("issue_to_patch.graph.state", "SourceLocation"),
    ("issue_to_patch.graph.state", "Evidence"),
    ("issue_to_patch.graph.state", "Hypothesis"),
    ("issue_to_patch.graph.state", "InvestigationPlan"),
    ("issue_to_patch.graph.state", "HumanDecision"),
    ("issue_to_patch.graph.state", "EvaluationReport"),
    ("issue_to_patch.ingestion.models", "IssueRequest"),
    ("issue_to_patch.ingestion.models", "RepositorySnapshot"),
    ("issue_to_patch.patching.models", "EditPlan"),
    ("issue_to_patch.patching.models", "FileEdit"),
    ("issue_to_patch.patching.models", "PatchArtifact"),
    ("issue_to_patch.patching.models", "ValidationReport"),
    ("issue_to_patch.patching.models", "CheckResult"),
]


def default_checkpointer() -> MemorySaver:
    """A fresh, process-local checkpointer with our state models allowlisted."""
    serde = JsonPlusSerializer(allowed_msgpack_modules=list(_STATE_MODELS))
    return MemorySaver(serde=serde)
