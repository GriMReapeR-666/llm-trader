from pathlib import Path

import pytest

from intelligence.trading_memory import TradingMemory
from intelligence.semantic_memory import search as semantic_search
from core.trading_safety import TradeProposal, TradingStore, ValidationResult
from operations.agent_operations import AgentOperations


def _insert_candidate(memory: TradingMemory, key: str = "scalp|EURUSD|BUY|setup|trend"):
    with memory._con() as con:
        con.execute("""INSERT INTO scenario_documents(
            scenario_key,created_at,updated_at,tags,sample_count,win_count,loss_count,
            rejected_count,unresolved_count,content,evidence,status
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            key, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
            '{"symbol":"EURUSD","strategy":"scalp"}', 10, 5, 5, 0, 0,
            '{"balanced":true}', '{"wins":[1],"losses":[2]}', "CANDIDATE",
        ))
    return key


def test_candidate_scenarios_are_not_retrieved_until_reviewed(tmp_path: Path):
    memory = TradingMemory(str(tmp_path / "memory.db"))
    key = _insert_candidate(memory)

    assert memory.retrieve_scenarios({"symbol": "EURUSD", "strategy": "scalp"}) == []
    memory.review_scenario(key, approve=True, reviewer="reviewer")
    records = memory.retrieve_scenarios({"symbol": "EURUSD", "strategy": "scalp"})
    assert len(records) == 1
    assert records[0]["status"] == "APPROVED"
    assert records[0]["reviewed_by"] == "reviewer"


def test_scenario_review_is_one_time_and_rebuild_preserves_approval(tmp_path: Path):
    path = tmp_path / "memory.db"
    memory = TradingMemory(str(path))
    key = _insert_candidate(memory)
    memory.review_scenario(key, approve=True, reviewer="reviewer")
    with pytest.raises(ValueError):
        memory.review_scenario(key, approve=False, reviewer="second")

    # The weekly rebuild's upsert must not silently demote an approved record.
    store = TradingStore(str(path))
    proposal = TradeProposal("scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8,
                             {"setup_id": "setup", "regime": "trend"})
    store.record_open_trade(1, proposal, 1.0, 1.0, {"features": {"regime": "trend"}})
    store.record_close(1, 1.2, 0.1, "target")
    memory.label_closed_trade(1, 0.1, 1.0, "target")
    memory.rebuild_scenarios()
    row = memory.list_scenarios("APPROVED")
    assert row and row[0]["scenario_key"] == key


def test_rebuild_records_rejected_and_unresolved_examples(tmp_path: Path):
    path = tmp_path / "memory.db"
    memory = TradingMemory(str(path))
    store = TradingStore(str(path))
    with store._connect() as con:
        con.execute("""INSERT INTO setup_instances(
            setup_key,setup_id,strategy,symbol,side,state,created_at,updated_at,reason,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
            "scalp|EURUSD|BUY|setup|trend", "setup", "scalp", "EURUSD", "BUY",
            "REJECTED", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
            "spread too wide", '{"features":{"regime":"trend"}}',
        ))
    with memory._con() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS order_intents(
            intent_key TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, run_id TEXT,
            proposal_id TEXT, status TEXT, mt5_ticket INTEGER, payload TEXT, result TEXT, error TEXT
        )""")
        con.execute("""INSERT INTO order_intents(intent_key,status,mt5_ticket,payload,error)
            VALUES(?,?,?,?,?)""", (
            "intent-1", "UNRESOLVED", 77,
            '{"strategy":"scalp","symbol":"EURUSD","side":"BUY","setup_id":"setup","regime":"trend"}',
            "broker history unavailable",
        ))
    memory.rebuild_scenarios()
    records = memory.list_scenarios("CANDIDATE")
    assert len(records) == 1
    assert records[0]["rejected_count"] == 1
    assert records[0]["unresolved_count"] == 1
    assert records[0]["content"]["balanced"] is False
    labels = memory.list_setup_labels()
    assert {item["label"] for item in labels} >= {"REJECTED_VALIDATOR", "UNRESOLVED_BROKER"}


def test_runtime_transitions_write_non_trade_labels(tmp_path: Path):
    path = tmp_path / "runtime-labels.db"
    store = TradingStore(str(path))
    memory = TradingMemory(str(path))
    proposal = TradeProposal("scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8,
                             {"setup_id": "runtime", "regime": "range"})
    store.log_proposal(proposal, ValidationResult(False, ["spread too wide"], 1))
    labels = memory.list_setup_labels("REJECTED_VALIDATOR")
    assert labels and labels[0]["subject_type"] == "setup_instance"

    stale = TradeProposal("scalp", "XAUUSD", "BUY", 2000.0, 1990.0, 2020.0, 0.8,
                           {"setup_id": "stale", "regime": "high_vol"})
    store.log_proposal(stale, ValidationResult(False, ["stale tick"], 1))
    assert memory.list_setup_labels("DATA_QUALITY_BLOCKED")

    operations = AgentOperations(str(path))
    with operations._con() as con:
        con.execute("""INSERT INTO order_intents(
            intent_key,created_at,updated_at,run_id,proposal_id,status,payload
        ) VALUES(?,?,?,?,?,?,?)""", ("runtime-intent", "now", "now", "run", "p", "CREATED", "{}"))
    operations.resolve_order_intent("runtime-intent", "ERROR", error="broker timeout")
    assert memory.list_setup_labels("UNRESOLVED_BROKER")

    with operations._con() as con:
        con.execute("""INSERT INTO order_intents(
            intent_key,created_at,updated_at,run_id,proposal_id,status,payload
        ) VALUES(?,?,?,?,?,?,?)""", ("cancelled-intent", "now", "now", "run", "p", "CREATED", "{}"))
    operations.resolve_order_intent("cancelled-intent", "CANCELLED", error="operator timeout")
    assert memory.list_setup_labels("CANCELLED_UNFILLED")

    subject_key = memory.list_setup_labels("REJECTED_VALIDATOR")[0]["subject_key"]
    memory.review_setup_label(subject_key, approve=True, reviewer="operator")
    reviewed = [item for item in memory.list_setup_labels() if item["subject_key"] == subject_key][0]
    assert reviewed["review_status"] == "APPROVED"
    assert reviewed["reviewed_by"] == "operator"


def test_scenario_retrieval_prefers_balanced_win_loss_evidence(tmp_path: Path):
    memory = TradingMemory(str(tmp_path / "balanced.db"))
    balanced_key = _insert_candidate(memory, "balanced|EURUSD|scalp")
    with memory._con() as con:
        con.execute("""INSERT INTO scenario_documents(
            scenario_key,created_at,updated_at,tags,sample_count,win_count,loss_count,
            rejected_count,unresolved_count,content,evidence,status
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "wins-only|EURUSD|scalp", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
            '{"symbol":"EURUSD","strategy":"scalp"}', 5, 5, 0, 0, 0,
            '{"balanced":false}', '{"wins":[1]}', "CANDIDATE",
        ))
    memory.review_scenario(balanced_key, approve=True, reviewer="reviewer")
    memory.review_scenario("wins-only|EURUSD|scalp", approve=True, reviewer="reviewer")
    records = memory.retrieve_scenarios({"symbol": "EURUSD", "strategy": "scalp"}, limit=2)
    assert records[0]["scenario_key"] == balanced_key
    assert records[0]["retrieval_balance"] == "BALANCED"
    assert any(item["retrieval_balance"] == "UNBALANCED" for item in records)
    semantic = semantic_search(str(tmp_path / "balanced.db"), {"symbol": "EURUSD", "strategy": "scalp"}, limit=2)
    assert semantic and semantic[0]["retrieval_balance"] == "BALANCED"


def test_semantic_search_applies_exact_structured_filters_before_similarity(tmp_path: Path):
    path = tmp_path / "structured.db"
    memory = TradingMemory(str(path))
    good = _insert_candidate(memory, "good|EURUSD|scalp")
    with memory._con() as con:
        con.execute("""INSERT INTO scenario_documents(
            scenario_key,created_at,updated_at,tags,sample_count,win_count,loss_count,
            rejected_count,unresolved_count,content,evidence,status,reviewed_by,reviewed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "wrong-symbol|GBPUSD|scalp", "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00",
            '{"symbol":"GBPUSD","strategy":"scalp"}', 10, 5, 5, 0, 0,
            '{"text":"EURUSD scalp trend evidence"}', '{"wins":[1],"losses":[2]}',
            "APPROVED", "reviewer", "2025-01-02T00:00:00+00:00",
        ))
    memory.review_scenario(good, approve=True, reviewer="reviewer")
    results = semantic_search(str(path), {"symbol": "EURUSD", "strategy": "scalp"}, limit=10,
                              as_of="2027-01-01T00:00:00Z")
    assert results
    assert all(item["tags"].get("symbol") == "EURUSD" for item in results)
