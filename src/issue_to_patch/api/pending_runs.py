"""In-memory tracking for an auto-run's progress before its first LangGraph
checkpoint exists — i.e. during the ingest+index window ``start_investigation``
hasn't reached yet. Single-process, lost on restart: same "placeholder now,
real infra later" convention as ``api/deps.py``'s rate limiter and
checkpointer cache. Deliberately NOT in ``auto_run.py`` (shared with the
CLI, which has no use for this) and NOT a DB row (the project keeps
``create_run``'s first-checkpoint pairing atomic by design; see
``run_states.py``).

Only ever mutated from the event-loop thread (``routes.py``, between the
``await run_in_threadpool(...)`` calls in ``_run_auto_pipeline``) — never
from inside ``auto_index``/``auto_investigate`` themselves, which run on a
worker thread. That invariant is what lets this stay a plain dict with no
lock.
"""

from __future__ import annotations

from dataclasses import dataclass

Stage = str  # "ingesting" | "indexing" | "investigating" | "failed"


@dataclass(frozen=True)
class PendingAutoRun:
    stage: Stage
    error: str | None = None


_pending: dict[str, PendingAutoRun] = {}


def mark_stage(run_id: str, stage: Stage) -> None:
    _pending[run_id] = PendingAutoRun(stage=stage)


def mark_failed(run_id: str, error: str) -> None:
    _pending[run_id] = PendingAutoRun(stage="failed", error=error)


def get(run_id: str) -> PendingAutoRun | None:
    return _pending.get(run_id)


def clear(run_id: str) -> None:
    _pending.pop(run_id, None)
