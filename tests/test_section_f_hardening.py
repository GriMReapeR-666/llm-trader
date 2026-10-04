import pytest
import json

from agent_operations import AgentOperations
from trading_memory import TradingMemory


def test_l0_raw_trace_secret_redaction(tmp_path):
    # The api_key below is SYNTHETIC. This file was tracked, and it used to
    # carry the real TOKENROUTER_KEY copied out of the operator's .env -
    # so every clone received a live-format credential as a test fixture.
    # A redaction fixture does not need a working key; it needs a
    # key-shaped string. If you ever paste a real one here again, rotate it.
    db_file = str(tmp_path / "state.db")
    ops = AgentOperations(db_file)
    
    payload = {
        "headers": {"Authorization": "Bearer rm-1234567890abcdef12345678"},
        "api_key": "sk-TESTFIXTURE0000000000000000000000000000REDACTED",
        "secret_assignment": "ROUTESME_KEY=rm-TESTFIXTURE0000000000000000000000000000000000000000",
        "normal_field": "EURUSD_BUY_PROPOSAL",
    }
    
    trace_id = ops.record_raw_trace("HTTP_PROBE", payload)
    assert trace_id.startswith("HTTP_PROBE:")
    
    with ops._con() as con:
        row = con.execute("SELECT payload FROM raw_traces WHERE trace_id=?", (trace_id,)).fetchone()
    
    redacted_json = row["payload"]
    assert "rm-1234567890abcdef12345678" not in redacted_json
    assert "sk-TESTFIXTURE0000000000000000000000000000REDACTED" not in redacted_json
    assert "rm-TESTFIXTURE0000000000000000000000000000000000000000" not in redacted_json
    assert "EURUSD_BUY_PROPOSAL" in redacted_json


def test_l1_l2_l3_trading_memory_tiers(tmp_path):
    db_file = str(tmp_path / "memory.db")
    mem = TradingMemory(db_file)
    
    # L1: Atomic Fact
    fact_id = mem.record_fact("src_1", "EURUSD_SPREAD_MEDIAN", "1.2", source="microstructure", confidence=0.95)
    assert fact_id > 0
    facts = mem.retrieve_facts(["EURUSD_SPREAD_MEDIAN"])
    assert len(facts) >= 1
    assert facts[0]["fact_value"] == "1.2"
    
    # L3: Core Memory (requires explicit operator review/approval)
    core_id = mem.propose_core_memory(
        namespace="RISK_POLICY", memory_key="max_daily_loss",
        content="Max daily loss hard limit is 3.0%", source_ids=["src_1"], confidence=1.0
    )
    assert core_id > 0
    mem.review_core_memory(core_id, approve=True, reviewer="operator")
    core = mem.retrieve_core_memories(["RISK_POLICY"])
    assert len(core) >= 1
    assert core[0]["content"] == "Max daily loss hard limit is 3.0%"


def test_safety_kinds_retention_during_compaction(tmp_path):
    db_file = str(tmp_path / "state.db")
    ops = AgentOperations(db_file)
    
    ns = "compaction_test"
    # Write entries including safety kinds
    ops.put_working_memory(ns, "err_1", "Critical risk breach payload", kind="error", priority=1)
    ops.put_working_memory(ns, "risk_1", "Kill switch activated", kind="risk_decision", priority=1)
    ops.put_working_memory(ns, "note_1", "Routine indicator calculation", kind="general_note", priority=1)
    ops.put_working_memory(ns, "note_2", "Routine tick update", kind="general_note", priority=1)
    
    summary = ops.compact_working_memory(ns, max_entries=1)
    assert summary["safety_retained"] >= 2
    
    with ops._con() as con:
        active_rows = con.execute("SELECT memory_key, kind FROM working_memory WHERE namespace=? AND status='ACTIVE'", (ns,)).fetchall()
    
    active_keys = {r["memory_key"] for r in active_rows}
    assert "err_1" in active_keys
    assert "risk_1" in active_keys
