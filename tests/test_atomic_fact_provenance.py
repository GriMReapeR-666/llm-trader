import pytest

from trading_memory import TradingMemory


def test_atomic_fact_requires_source_and_key(tmp_path):
    memory = TradingMemory(str(tmp_path / "journal.db"))
    with pytest.raises(ValueError, match="source_id"):
        memory.record_fact("", "macro.fact", {"value": 1})
    with pytest.raises(ValueError, match="fact_key"):
        memory.record_fact("source-1", "", {"value": 1})


def test_atomic_fact_preserves_source_identifier(tmp_path):
    memory = TradingMemory(str(tmp_path / "journal.db"))
    fact_id = memory.record_fact("event:123", "macro.fact", {"value": 1})
    with memory._con() as con:
        row = con.execute("SELECT source_id, fact_key FROM atomic_facts WHERE id=?", (fact_id,)).fetchone()
    assert row[0] == "event:123"
    assert row[1] == "macro.fact"
