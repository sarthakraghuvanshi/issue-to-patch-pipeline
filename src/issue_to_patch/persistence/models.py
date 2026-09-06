"""Relational storage schema (SQLAlchemy 2.0).

Sprint 1 tables: ``runs``, ``tool_calls``, ``artifacts``. Later sprints add
``evidence``, ``human_decisions``, ``chunks``, ``eval_results`` (see
IMPLEMENTATION_PLAN.md section 4). A run is never deleted, only superseded.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issue_ref: Mapped[str] = mapped_column(String(512))
    repo: Mapped[str | None] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(48))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    tool_calls: Mapped[list[ToolCall]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ToolCall.seq"
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ToolCall(Base):
    """Append-only, hash-chained record of every side-effecting call in a run."""

    __tablename__ = "tool_calls"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_toolcall_run_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(128))
    args_redacted: Mapped[str] = mapped_column(Text, default="")
    result_hash: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column()
    prev_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64))

    run: Mapped[Run] = relationship(back_populates="tool_calls")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # patch | validation | raw | eval | run
    uri: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column()

    run: Mapped[Run] = relationship(back_populates="artifacts")
