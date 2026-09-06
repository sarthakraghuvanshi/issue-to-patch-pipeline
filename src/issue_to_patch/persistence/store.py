"""The write path for run metadata.

Every side effect a run performs goes through :meth:`Store.record_tool_call`,
which chains rows by hash: ``row_hash = sha256(prev_hash + payload)``. A broken
chain later means someone edited history — that is the tamper-evidence property
the audit story (Sprint 7) builds on.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from issue_to_patch.ingestion.models import stable_hash
from issue_to_patch.persistence.models import Artifact, Base, Run, ToolCall

_GENESIS_HASH = "0" * 64


def _ts_key(dt: datetime) -> str:
    """Canonical UTC-naive ISO string, so the hash survives a DB round-trip.

    SQLite drops tzinfo on the way in; this makes the write-side and read-side
    representations identical regardless.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.isoformat()


class Store:
    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, future=True)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)

    def create_all(self) -> None:
        """Create tables directly (used in tests; production uses Alembic)."""
        Base.metadata.create_all(self._engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # -- runs ---------------------------------------------------------
    def create_run(
        self,
        *,
        run_id: str,
        issue_ref: str,
        repo: str | None,
        commit_sha: str | None,
        content_hash: str,
        state: str = "INVESTIGATION_INCONCLUSIVE",
    ) -> None:
        with self.session() as session:
            session.add(
                Run(
                    run_id=run_id,
                    issue_ref=issue_ref,
                    repo=repo,
                    commit_sha=commit_sha,
                    state=state,
                    content_hash=content_hash,
                    created_at=datetime.now(UTC),
                )
            )

    def finish_run(self, run_id: str, *, state: str, cost_usd: float = 0.0) -> None:
        with self.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            run.state = state
            run.cost_usd = cost_usd
            run.finished_at = datetime.now(UTC)

    # -- append-only tool calls ------------------------------------
    def record_tool_call(
        self, run_id: str, *, tool: str, args_redacted: str, result_hash: str
    ) -> str:
        with self.session() as session:
            last = session.scalars(
                select(ToolCall)
                .where(ToolCall.run_id == run_id)
                .order_by(ToolCall.seq.desc())
                .limit(1)
            ).first()
            seq = 1 if last is None else last.seq + 1
            prev_hash = _GENESIS_HASH if last is None else last.row_hash
            ts = datetime.now(UTC)
            row_hash = stable_hash(
                prev_hash, run_id, str(seq), tool, args_redacted, result_hash, _ts_key(ts)
            )
            session.add(
                ToolCall(
                    run_id=run_id,
                    seq=seq,
                    tool=tool,
                    args_redacted=args_redacted,
                    result_hash=result_hash,
                    ts=ts,
                    prev_hash=prev_hash,
                    row_hash=row_hash,
                )
            )
            return row_hash

    def verify_chain(self, run_id: str) -> bool:
        """Recompute the hash chain; return False if any link is broken."""
        with self.session() as session:
            rows = session.scalars(
                select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.seq)
            ).all()
        prev = _GENESIS_HASH
        for row in rows:
            expected = stable_hash(
                prev,
                run_id,
                str(row.seq),
                row.tool,
                row.args_redacted,
                row.result_hash,
                _ts_key(row.ts),
            )
            if row.prev_hash != prev or row.row_hash != expected:
                return False
            prev = row.row_hash
        return True

    # -- artifacts --------------------------------------------------
    def record_artifact(self, run_id: str, *, kind: str, uri: str, content_hash: str) -> None:
        with self.session() as session:
            session.add(
                Artifact(
                    run_id=run_id,
                    kind=kind,
                    uri=uri,
                    hash=content_hash,
                    created_at=datetime.now(UTC),
                )
            )

    # -- reads ----------------------------------------------------
    def get_run_state(self, run_id: str) -> str | None:
        with self.session() as session:
            run = session.get(Run, run_id)
            return None if run is None else run.state
