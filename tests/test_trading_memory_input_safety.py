import math

import pytest

from trading_memory import TradingMemory


def test_record_fact_rejects_nonfinite_confidence(tmp_path):
    memory = TradingMemory(str(tmp_path / "facts.db"))
    with pytest.raises(ValueError, match="confidence"):
        memory.record_fact("source", "key", {"value": 1}, confidence=math.nan)


def test_verified_and_core_memory_reject_nonfinite_confidence(tmp_path):
    memory = TradingMemory(str(tmp_path / "confidence.db"))
    with pytest.raises(ValueError, match="confidence"):
        memory.save_verified_memory("source", "lesson", {}, "summary", "lesson", {}, confidence=math.inf)
    with pytest.raises(ValueError, match="confidence"):
        memory.propose_core_memory("SKILL", "key", "content", ["source"], confidence=math.nan)


def test_record_events_marks_malformed_importance_without_crashing(tmp_path):
    memory = TradingMemory(str(tmp_path / "events.db"))
    memory.record_events([
        {"time": "2026-08-06T00:00:00Z", "currency": "USD", "event": "CPI", "importance": "unknown"},
        "not-an-event",
    ])
    with memory._con() as con:
        row = con.execute("SELECT importance,payload FROM macro_events").fetchone()
    assert row["importance"] == 0
    assert "MALFORMED_IMPORTANCE" in row["payload"]


def test_playbook_ignores_malformed_event_importance(tmp_path):
    memory = TradingMemory(str(tmp_path / "playbook.db"))
    playbook = memory.current_playbook(
        "EURUSD",
        [{"event": "bad", "importance": "NaN"}, {"event": "valid", "importance": 3}],
        {"primary_trend": "RANGE", "key_levels": []},
    )
    assert [item["event"] for item in playbook["no_trade_events"]] == ["valid"]
