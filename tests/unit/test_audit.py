"""persistence/audit.py: assembling and verifying a run's full audit trail."""

from __future__ import annotations

from pathlib import Path

from issue_to_patch.persistence import Store, build_audit_trail, render_audit_trail


def _store(tmp_path: Path) -> Store:
    store = Store(f"sqlite+pysqlite:///{tmp_path / 'audit.db'}")
    store.create_all()
    return store


def test_audit_trail_for_an_unknown_run_reports_not_found(tmp_path: Path) -> None:
    trail = build_audit_trail(_store(tmp_path), "no-such-run")
    assert trail.found is False
    assert trail.tool_calls == []
    assert "no such run" in render_audit_trail(trail)


def test_audit_trail_assembles_everything_in_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_run(
        run_id="r1", issue_ref="acme/x#1", repo="acme/x", commit_sha=None, content_hash="h"
    )
    store.record_tool_call("r1", tool="search", args_redacted="{}", result_hash="1")
    store.record_tool_call("r1", tool="validate_patch", args_redacted="{}", result_hash="2")
    store.record_human_decision(
        "r1", role="gatekeeper", reviewer="alice", decision="approve", reason=""
    )
    store.record_artifact("r1", kind="patch", uri="file:///fix.patch", content_hash="h2")
    store.finish_run("r1", state="PATCH_VALIDATED")

    trail = build_audit_trail(store, "r1")

    assert trail.found is True
    assert trail.state == "PATCH_VALIDATED"
    assert trail.issue_ref == "acme/x#1"
    assert [t.tool for t in trail.tool_calls] == ["search", "validate_patch"]
    assert [d.reviewer for d in trail.decisions] == ["alice"]
    assert [a.kind for a in trail.artifacts] == ["patch"]
    assert trail.tool_calls_chain_valid is True
    assert trail.decisions_chain_valid is True
    assert trail.is_tamper_evident_intact is True

    rendered = render_audit_trail(trail)
    assert "tool_calls=OK" in rendered
    assert "decisions=OK" in rendered
    assert "alice" in rendered


def test_audit_trail_flags_a_broken_tool_call_chain_without_hiding_it_behind_decisions(
    tmp_path: Path,
) -> None:
    from sqlalchemy import text

    store = _store(tmp_path)
    store.create_run(run_id="r1", issue_ref="x", repo=None, commit_sha=None, content_hash="h")
    store.record_tool_call("r1", tool="a", args_redacted="{}", result_hash="1")
    store.record_human_decision(
        "r1", role="gatekeeper", reviewer="alice", decision="approve", reason=""
    )
    with store.session() as session:
        session.execute(text("UPDATE tool_calls SET result_hash='tampered' WHERE run_id='r1'"))

    trail = build_audit_trail(store, "r1")
    assert trail.tool_calls_chain_valid is False
    assert trail.decisions_chain_valid is True  # the other chain is unaffected
    assert trail.is_tamper_evident_intact is False
    assert "tool_calls=BROKEN" in render_audit_trail(trail)
    assert "decisions=OK" in render_audit_trail(trail)
