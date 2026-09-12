"""Assemble and verify a run's full audit trail (Phase 8).

"Every tool call and decision must be append-only in the run audit log" —
this is the read side of that: pull every tool call, every human decision,
and every artifact recorded for a run, in order, and verify both hash chains
(tool calls and human decisions are separate chains; see
``persistence/store.py``). A single ``chain_valid`` flag would hide *which*
chain someone tampered with, so the two are reported separately.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from issue_to_patch.persistence.store import Store


class ToolCallEntry(BaseModel):
    seq: int
    tool: str
    args_redacted: str
    result_hash: str
    ts: datetime
    row_hash: str


class HumanDecisionEntry(BaseModel):
    seq: int
    role: str
    reviewer: str
    decision: str
    reason: str
    ts: datetime
    row_hash: str


class ArtifactEntry(BaseModel):
    kind: str
    uri: str
    hash: str
    created_at: datetime


class AuditTrail(BaseModel):
    run_id: str
    found: bool
    state: str | None = None
    issue_ref: str | None = None
    tool_calls_chain_valid: bool
    decisions_chain_valid: bool
    tool_calls: list[ToolCallEntry]
    decisions: list[HumanDecisionEntry]
    artifacts: list[ArtifactEntry]

    @property
    def is_tamper_evident_intact(self) -> bool:
        """True only if BOTH chains verify — a broken chain in either one
        means the recorded history cannot be trusted as-is."""
        return self.tool_calls_chain_valid and self.decisions_chain_valid


def build_audit_trail(store: Store, run_id: str) -> AuditTrail:
    run = store.get_run(run_id)
    tool_calls = store.list_tool_calls(run_id)
    decisions = store.list_human_decisions(run_id)
    artifacts = store.list_artifacts(run_id)
    return AuditTrail(
        run_id=run_id,
        found=run is not None,
        state=run.state if run is not None else None,
        issue_ref=run.issue_ref if run is not None else None,
        tool_calls_chain_valid=store.verify_chain(run_id),
        decisions_chain_valid=store.verify_decision_chain(run_id),
        tool_calls=[
            ToolCallEntry(
                seq=t.seq,
                tool=t.tool,
                args_redacted=t.args_redacted,
                result_hash=t.result_hash,
                ts=t.ts,
                row_hash=t.row_hash,
            )
            for t in tool_calls
        ],
        decisions=[
            HumanDecisionEntry(
                seq=d.seq,
                role=d.role,
                reviewer=d.reviewer,
                decision=d.decision,
                reason=d.reason,
                ts=d.ts,
                row_hash=d.row_hash,
            )
            for d in decisions
        ],
        artifacts=[
            ArtifactEntry(kind=a.kind, uri=a.uri, hash=a.hash, created_at=a.created_at)
            for a in artifacts
        ],
    )


def render_audit_trail(trail: AuditTrail) -> str:
    if not trail.found:
        return f"no such run: {trail.run_id}"
    lines = [
        f"run_id:  {trail.run_id}",
        f"issue:   {trail.issue_ref}",
        f"state:   {trail.state}",
        f"chains:  tool_calls={'OK' if trail.tool_calls_chain_valid else 'BROKEN'}  "
        f"decisions={'OK' if trail.decisions_chain_valid else 'BROKEN'}",
        "",
        "tool calls:",
    ]
    for t in trail.tool_calls:
        lines.append(f"  [{t.seq}] {t.ts.isoformat()}  {t.tool}  args={t.args_redacted}")
    lines.append("")
    lines.append("human decisions:")
    for d in trail.decisions:
        lines.append(
            f"  [{d.seq}] {d.ts.isoformat()}  {d.reviewer} ({d.role}): "
            f"{d.decision} — {d.reason or '(no reason given)'}"
        )
    lines.append("")
    lines.append("artifacts:")
    for a in trail.artifacts:
        lines.append(f"  {a.kind}: {a.uri}  ({a.hash[:12]}...)")
    return "\n".join(lines)
