import gc
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
import pytest

from intelligence.trading_memory import TradingMemory
from intelligence.local_agent_memory import LocalAgentMemory, LocalAgentMemoryConfig


@pytest.fixture
def temp_memory_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    gc.collect()
    try:
        if os.path.exists(path):
            os.remove(path)
    except (PermissionError, OSError):
        pass


def test_retire_core_memory_approved_and_candidate(temp_memory_db):
    tm = TradingMemory(temp_memory_db)

    # 1. Propose and approve memory 1
    mem1 = tm.propose_core_memory(
        namespace="RISK_POLICY",
        memory_key="max_drawdown_limit",
        content="Halt execution at 5% daily drawdown",
        source_ids=["rule:risk_v1"],
        confidence=1.0,
    )
    tm.review_core_memory(mem1, approve=True, reviewer="operator:alice")

    # Verify mem1 is retrieved
    retrieved = tm.retrieve_core_memories(["RISK_POLICY"])
    assert any(m["id"] == mem1 for m in retrieved)

    # Retire approved mem1
    res1 = tm.retire_core_memory(mem1, reviewer="operator:bob", reason="superseded by 4% drawdown rule")
    assert res1["id"] == mem1
    assert res1["status"] == "RETIRED"
    assert res1["previous_status"] == "APPROVED"
    assert res1["reason"] == "superseded by 4% drawdown rule"
    assert res1["execution_authority"] is False

    # Verify mem1 is no longer retrieved
    retrieved_after = tm.retrieve_core_memories(["RISK_POLICY"])
    assert not any(m["id"] == mem1 for m in retrieved_after)

    # 2. Propose candidate memory 2 and retire directly
    mem2 = tm.propose_core_memory(
        namespace="STRATEGY_RULE",
        memory_key="asia_session_breakout",
        content="Trade London open breakout",
        source_ids=["rule:session_v1"],
        confidence=0.8,
    )
    res2 = tm.retire_core_memory(mem2, reviewer="operator:bob", reason="deprecated setup")
    assert res2["id"] == mem2
    assert res2["status"] == "RETIRED"
    assert res2["previous_status"] == "CANDIDATE"


def test_prune_stale_working_contexts_preserves_protected(temp_memory_db):
    tm = TradingMemory(temp_memory_db)
    session_key = "session:2026-08-01:alpha"

    # Add 5 normal items
    for i in range(5):
        tm.append_working_context(
            session_key,
            kind="observation",
            payload={"msg": f"tick observation {i}"},
            importance="NORMAL",
        )

    # Add 2 protected items (risk decision and kill switch)
    tm.append_working_context(
        session_key,
        kind="risk_decision",
        payload={"action": "REJECT", "rule": "daily_loss_limit"},
        importance="CRITICAL",
    )
    tm.append_working_context(
        session_key,
        kind="kill_switch",
        payload={"reason": "drawdown threshold breached"},
        importance="HIGH",
    )

    # Artificially age all records by 72 hours
    old_time = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    with tm._con() as con:
        con.execute("UPDATE working_context_items SET created_at=?", (old_time,))

    # Prune stale items older than 48 hours, keeping max 2 per session
    report = tm.prune_stale_working_contexts(max_age_hours=48, keep_per_session=2)
    assert report["scanned_sessions_count"] == 1
    # 5 normal items - 2 kept = 3 archived
    assert report["archived_count"] == 3
    # Both protected items must be preserved
    assert report["preserved_protected_count"] >= 2
    assert report["execution_authority"] is False

    # Check database state directly
    with tm._con() as con:
        active = con.execute(
            "SELECT kind, status FROM working_context_items WHERE session_key=? AND status='ACTIVE'",
            (session_key,),
        ).fetchall()
        active_kinds = [r["kind"] for r in active]
        assert "risk_decision" in active_kinds
        assert "kill_switch" in active_kinds
        # Only 2 normal items remain active
        assert active_kinds.count("observation") == 2

        archived = con.execute(
            "SELECT COUNT(*) FROM working_context_items WHERE session_key=? AND status='ARCHIVED'",
            (session_key,),
        ).fetchone()[0]
        assert archived == 3


def test_local_agent_memory_deduplication(temp_memory_db):
    cfg = LocalAgentMemoryConfig(
        db_path=temp_memory_db,
        enabled=True,
    )
    lam = LocalAgentMemory(cfg)

    # Insert initial operational event
    payload = {"status": "HEALTHY", "broker_connected": True}
    res1 = lam.remember_event(
        actor="health_monitor",
        event_type="heartbeat",
        payload=payload,
    )
    assert res1["stored"] is True
    doc_id1 = res1["doc_id"]

    # Insert duplicate identical event immediately
    res2 = lam.remember_event(
        actor="health_monitor",
        event_type="heartbeat",
        payload=payload,
    )
    assert res2["stored"] is True
    assert res2.get("deduplicated") is True
    assert res2["doc_id"] == doc_id1

    # Verify only 1 row exists in SQLite table
    with lam._con() as con:
        count = con.execute("SELECT COUNT(*) FROM memory_documents").fetchone()[0]
        assert count == 1


def test_cli_memory_core_retire(temp_memory_db):
    tm = TradingMemory(temp_memory_db)
    mem_id = tm.propose_core_memory(
        namespace="ACCESS_POLICY",
        memory_key="read_only_mode",
        content="System operating in read-only audit mode",
        source_ids=["audit:2026"],
        confidence=1.0,
    )
    tm.review_core_memory(mem_id, approve=True, reviewer="operator:admin")

    del tm
    gc.collect()

    cmd = [
        sys.executable,
        "mt5_trader.py",
        "memory-core",
        "retire",
        "--journal-path",
        temp_memory_db,
        "--id",
        str(mem_id),
        "--note",
        "operator retirement test",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out["id"] == mem_id
    assert out["status"] == "RETIRED"
    assert out["previous_status"] == "APPROVED"
    assert out["reason"] == "operator retirement test"
