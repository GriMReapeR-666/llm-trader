"""Tests for offline local agent memory."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_operations import AgentOperations
from local_agent_memory import (
    LocalAgentMemory,
    LocalAgentMemoryConfig,
    maybe_remember_event,
    redact_payload,
)


@pytest.fixture
def mem_env(tmp_path: Path):
    db = tmp_path / "local_agent_memory.db"
    return {
        "LOCAL_MEMORY_ENABLED": "true",
        "LOCAL_MEMORY_PATH": str(db),
        "LOCAL_MEMORY_MAX_ACTIVE": "100",
        "LOCAL_MEMORY_RETENTION_DAYS": "30",
        "LOCAL_MEMORY_COMPACT_KEEP": "5",
    }


@pytest.fixture
def memory(mem_env):
    return LocalAgentMemory(LocalAgentMemoryConfig.from_env(mem_env))


def test_disabled_is_fail_closed(tmp_path: Path):
    mem = LocalAgentMemory(LocalAgentMemoryConfig(
        enabled=False, db_path=str(tmp_path / "x.db")
    ))
    assert mem.status()["ready"] is False
    assert mem.status()["execution_authority"] is False
    assert mem.status()["network_required"] is False
    out = mem.remember_event(actor="a", event_type="t", payload={"k": 1})
    assert out["stored"] is False
    assert out["execution_authority"] is False
    assert mem.query_memory()["count"] == 0


def test_remember_event_and_query_pit(memory):
    early = memory.remember_event(
        actor="RiskController",
        event_type="proposal_validated",
        payload={"approved": False, "password": "secret-pass", "api_key": "sk-abc"},  # secret-scan: allow
        observed_at="2025-06-01T12:00:00+00:00",
        correlation_id="setup-1",
    )
    late = memory.remember_event(
        actor="MacroAnalyst",
        event_type="macro_event_state",
        payload={"event": "NFP"},
        observed_at="2027-06-01T12:00:00+00:00",
        correlation_id="macro-1",
    )
    assert early["stored"] is True
    assert late["stored"] is True
    assert early["execution_authority"] is False

    pit = memory.query_memory(as_of="2026-01-01T00:00:00+00:00", limit=20)
    assert pit["count"] == 1
    assert pit["items"][0]["payload"]["payload"]["password"] == "[REDACTED]"
    assert pit["items"][0]["payload"]["payload"]["api_key"] == "[REDACTED]"
    assert "NFP" not in str(pit["items"])

    all_items = memory.query_memory(as_of="2028-01-01T00:00:00+00:00", limit=20)
    assert all_items["count"] == 2
    assert all_items["execution_authority"] is False


def test_invalid_as_of_fail_closed(memory):
    memory.remember_event(actor="a", event_type="t", payload={})
    bad = memory.query_memory(as_of="not-a-timestamp")
    assert bad["count"] == 0
    assert bad["mode"] == "INVALID_AS_OF"
    assert bad["execution_authority"] is False


def test_journal_snapshot_and_trade_cycle(memory):
    snap = memory.remember_journal_snapshot(
        kind="proposal_input",
        payload={"symbol": "EURUSD", "token": "Bearer abc.def.ghi"},
        snapshot_id="proposal_input:abc",
        symbol="EURUSD",
        timeframe="M15",
        observed_at="2026-01-01T10:00:00+00:00",
        memory_links={"L0": "raw-1", "L1": ["fact-1"]},
    )
    assert snap["stored"] is True
    assert snap["memory_links"]["L0"] == "raw-1"

    cycle = memory.remember_trade_cycle(
        cycle_key="ticket-99",
        stage="filled",
        payload={"ticket": 99, "fill_price": 1.1},
        ticket=99,
        symbol="EURUSD",
        side="BUY",
        observed_at="2026-01-01T11:00:00+00:00",
        memory_links={"L2": "scenario-eur", "L3": "core-risk"},
    )
    assert cycle["stored"] is True
    assert cycle["doc_type"] == "fill"

    indexed = memory.get_kv("trade_cycle_index", "ticket-99")
    assert indexed["found"] is True
    assert indexed["value"]["latest_stage"] == "filled"
    assert indexed["execution_authority"] is False

    tagged = memory.query_memory(tags={"symbol": "EURUSD"}, as_of="2026-01-02T00:00:00+00:00")
    assert tagged["count"] >= 1


def test_export_for_improvement_redacts_and_bounds(memory):
    memory.remember_event(
        actor="ops",
        event_type="heartbeat",
        payload={"MT5_PASSWORD": "real-secret", "ok": True},
        observed_at="2026-02-01T00:00:00+00:00",
    )
    exported = memory.export_for_improvement(as_of="2026-03-01T00:00:00+00:00", limit=10)
    assert exported["count"] == 1
    assert exported["execution_authority"] is False
    blob = str(exported["records"])
    assert "real-secret" not in blob
    assert "[REDACTED]" in blob or "REDACTED" in blob


def test_compaction_and_maintenance(memory):
    for i in range(12):
        memory.remember_event(
            actor="ops",
            event_type=f"tick_{i}",
            payload={"i": i},
            correlation_id=f"c-{i}",
            observed_at=f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00",
        )
    result = memory.compact_memory()
    assert result["compacted"] >= 1
    assert result["execution_authority"] is False
    active = memory.query_memory(as_of="2027-01-01T00:00:00+00:00", limit=50)
    assert active["count"] <= memory.config.compact_keep_per_collection

    maint = memory.run_maintenance()
    assert "archived" in maint
    assert maint["execution_authority"] is False


def test_redact_payload_helper():
    raw = {
        "password": "x",
        "nested": {"api_key": "y", "ok": 1},
        "note": "OPENROUTER_API_KEY=sk-live-1234567890",
    }
    clean = redact_payload(raw)
    assert clean["password"] == "[REDACTED]"
    assert clean["nested"]["api_key"] == "[REDACTED]"
    assert clean["nested"]["ok"] == 1
    assert "sk-live" not in clean["note"]


def test_maybe_remember_never_raises(tmp_path: Path):
    # Disabled env -> soft no-op
    out = maybe_remember_event(
        actor="a",
        event_type="t",
        payload={},
        env={"LOCAL_MEMORY_ENABLED": "false",
             "LOCAL_MEMORY_PATH": str(tmp_path / "n.db")},
    )
    assert out["stored"] is False
    assert out["execution_authority"] is False


def test_agent_operations_event_wires_local_memory(tmp_path: Path, monkeypatch):
    ops_db = tmp_path / "ops.db"
    mem_db = tmp_path / "local.db"
    monkeypatch.setenv("LOCAL_MEMORY_ENABLED", "true")
    monkeypatch.setenv("LOCAL_MEMORY_PATH", str(mem_db))

    ops = AgentOperations(db_path=str(ops_db))
    ops.event("RiskController", "proposal_validated", {"approved": True, "password": "nope"},
              run_id="run-1", correlation_id="c1")

    mem = LocalAgentMemory(LocalAgentMemoryConfig.from_env({
        "LOCAL_MEMORY_ENABLED": "true",
        "LOCAL_MEMORY_PATH": str(mem_db),
    }))
    found = mem.query_memory(as_of="2099-01-01T00:00:00+00:00", text_contains="proposal_validated")
    assert found["count"] >= 1
    assert "nope" not in str(found["items"])
    assert found["items"][0]["execution_authority"] is False


def test_working_memory_and_snapshot_wire(tmp_path: Path, monkeypatch):
    ops_db = tmp_path / "ops2.db"
    mem_db = tmp_path / "local2.db"
    monkeypatch.setenv("LOCAL_MEMORY_ENABLED", "true")
    monkeypatch.setenv("LOCAL_MEMORY_PATH", str(mem_db))

    ops = AgentOperations(db_path=str(ops_db))
    result = ops.put_working_memory(
        "ns", "risk:1", {"decision": "block", "token": "secret-token"},
        kind="risk_decision", priority=90, source_ids=["src-1"],
    )
    assert result["execution_authority"] is False
    assert result["local_memory"]["execution_authority"] is False
    assert result["local_memory"].get("stored") is True

    snap_id = ops.store_snapshot("market", {"bid": 1.0, "api_key": "hidden"}, run_id="r", symbol="XAUUSD")
    assert snap_id.startswith("market:")

    ops.enqueue_memory("closed_trade", "55", {"ticket": 55, "label": "WIN", "pnl": 10.0})

    mem = LocalAgentMemory(LocalAgentMemoryConfig.from_env({
        "LOCAL_MEMORY_ENABLED": "true",
        "LOCAL_MEMORY_PATH": str(mem_db),
    }))
    wm = mem.query_memory(collection="working_memory", as_of="2099-01-01T00:00:00+00:00")
    assert wm["count"] >= 1
    snaps = mem.query_memory(collection="journal_snapshots", as_of="2099-01-01T00:00:00+00:00")
    assert snaps["count"] >= 1
    cycles = mem.query_memory(collection="trade_cycles", as_of="2099-01-01T00:00:00+00:00")
    assert cycles["count"] >= 1
    assert "hidden" not in str(snaps["items"])
    assert "secret-token" not in str(wm["items"])


def test_status_honest_naming(memory):
    status = memory.status()
    assert status["backend"] == "local_agent_memory"
    assert "cloud" in status["inspired_by"].lower() or "local" in status["inspired_by"].lower()
    assert status["network_required"] is False
    assert status["execution_authority"] is False
