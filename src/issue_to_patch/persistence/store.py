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
from issue_to_patch.persistence.models import (
    Artifact,
    Base,
    ChunkRow,
    HumanDecisionRow,
    Run,
    ToolCall,
)

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

    # -- append-only human decisions ---------------------------------
    def record_human_decision(
        self, run_id: str, *, role: str, reviewer: str, decision: str, reason: str
    ) -> str:
        """Its own hash chain, same technique as :meth:`record_tool_call` —
        a decision is a different kind of event than a tool call, so it gets
        its own append-only sequence rather than being interleaved into one."""
        with self.session() as session:
            last = session.scalars(
                select(HumanDecisionRow)
                .where(HumanDecisionRow.run_id == run_id)
                .order_by(HumanDecisionRow.seq.desc())
                .limit(1)
            ).first()
            seq = 1 if last is None else last.seq + 1
            prev_hash = _GENESIS_HASH if last is None else last.row_hash
            ts = datetime.now(UTC)
            row_hash = stable_hash(
                prev_hash, run_id, str(seq), role, reviewer, decision, reason, _ts_key(ts)
            )
            session.add(
                HumanDecisionRow(
                    run_id=run_id,
                    seq=seq,
                    role=role,
                    reviewer=reviewer,
                    decision=decision,
                    reason=reason,
                    ts=ts,
                    prev_hash=prev_hash,
                    row_hash=row_hash,
                )
            )
            return row_hash

    def list_human_decisions(self, run_id: str) -> list[HumanDecisionRow]:
        with self.session() as session:
            rows = session.scalars(
                select(HumanDecisionRow)
                .where(HumanDecisionRow.run_id == run_id)
                .order_by(HumanDecisionRow.seq)
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def verify_decision_chain(self, run_id: str) -> bool:
        """Recompute the human-decision hash chain; False if any link is broken."""
        rows = self.list_human_decisions(run_id)
        prev = _GENESIS_HASH
        for row in rows:
            expected = stable_hash(
                prev,
                run_id,
                str(row.seq),
                row.role,
                row.reviewer,
                row.decision,
                row.reason,
                _ts_key(row.ts),
            )
            if row.prev_hash != prev or row.row_hash != expected:
                return False
            prev = row.row_hash
        return True

    def list_tool_calls(self, run_id: str) -> list[ToolCall]:
        with self.session() as session:
            rows = session.scalars(
                select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.seq)
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        with self.session() as session:
            rows = session.scalars(
                select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at)
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

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

    def get_run(self, run_id: str) -> Run | None:
        with self.session() as session:
            run = session.get(Run, run_id)
            if run is not None:
                session.expunge(run)
            return run

    # -- chunks ---------------------------------------------------
    def replace_chunks(
        self, repository: str, commit_sha: str, rows: list[dict[str, object]]
    ) -> int:
        """Wipe any existing chunks for this repo@sha, then insert ``rows``.

        Re-indexing the same commit is therefore idempotent (Phase 2 exit
        criterion), and retrieval never mixes two commits.
        """
        with self.session() as session:
            session.query(ChunkRow).filter(
                ChunkRow.repository == repository, ChunkRow.commit_sha == commit_sha
            ).delete(synchronize_session=False)
            session.bulk_insert_mappings(ChunkRow, rows)
            return len(rows)

    def get_chunk(self, chunk_id: str) -> ChunkRow | None:
        with self.session() as session:
            return session.get(ChunkRow, chunk_id)

    def list_chunks(self, repository: str, commit_sha: str) -> list[ChunkRow]:
        with self.session() as session:
            rows = session.scalars(
                select(ChunkRow)
                .where(ChunkRow.repository == repository, ChunkRow.commit_sha == commit_sha)
                .order_by(ChunkRow.path, ChunkRow.line_start)
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def set_embeddings(self, embeddings: dict[str, list[float]]) -> int:
        with self.session() as session:
            for chunk_id, vector in embeddings.items():
                row = session.get(ChunkRow, chunk_id)
                if row is not None:
                    row.embedding = vector
            return len(embeddings)

    def indexed_paths(self, repository: str, commit_sha: str) -> set[str]:
        """Paths actually indexed for repo@sha, so a dataset builder can filter to them."""
        with self.session() as session:
            rows = session.scalars(
                select(ChunkRow.path)
                .where(ChunkRow.repository == repository, ChunkRow.commit_sha == commit_sha)
                .distinct()
            ).all()
            return set(rows)

    def count_chunks(self, repository: str, commit_sha: str) -> int:
        with self.session() as session:
            return (
                session.query(ChunkRow)
                .filter(ChunkRow.repository == repository, ChunkRow.commit_sha == commit_sha)
                .count()
            )
