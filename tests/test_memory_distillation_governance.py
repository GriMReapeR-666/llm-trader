from pathlib import Path

from agent_operations import AgentOperations
from health_alerts import evaluate as evaluate_alerts
from local_llm_memory import process_outbox
from trading_memory import TradingMemory
from trading_safety import TradeProposal, TradingStore


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def generate(self, messages, model=""):
        self.calls += 1
        return '{"tags":{"kind":"closed_trade"},"summary":"verified result","lesson":"retain costs"}'


def test_unverified_memory_sources_are_blocked_before_llm(tmp_path: Path):
    path = str(tmp_path / "memory.db")
    operations = AgentOperations(path)
    memory = TradingMemory(path)
    operations.enqueue_memory("closed_trade", "999", {"label": "WIN_TARGET"})
    llm = FakeLLM()

    result = process_outbox(operations, memory, llm, "test-model")

    assert result["blocked"] == 1
    assert llm.calls == 0
    with operations._con() as con:
        assert con.execute("SELECT status FROM memory_outbox").fetchone()["status"] == "BLOCKED"
    alert = evaluate_alerts(path)
    assert any(item["code"] == "UNVERIFIED_MEMORY_BLOCKED" for item in alert["alerts"])


def test_only_closed_trade_with_matching_label_is_distilled(tmp_path: Path):
    path = str(tmp_path / "verified.db")
    store = TradingStore(path)
    memory = TradingMemory(path)
    proposal = TradeProposal("scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8,
                             {"setup_id": "memory", "regime": "trend"})
    store.record_open_trade(101, proposal, 1.0, 1.0, {"features": {"regime": "trend"}})
    store.record_close(101, 1.3, 0.3, "target")
    label = memory.label_closed_trade(101, 0.3, 1.0, "target")
    operations = AgentOperations(path)
    operations.enqueue_memory("closed_trade", "101", {"ticket": 101, "label": label["label"], "r_multiple": label["r_multiple"]})
    llm = FakeLLM()

    result = process_outbox(operations, memory, llm, "test-model")

    assert result["processed"] == 1
    assert result["blocked"] == 0
    assert result["attempt_count"] == 1
    assert result["parse_status"] == "BATCH_SUCCESS"
    assert result["prompt_hash"] and result["response_hash"]
    assert result["response_chars"] > 0
    assert llm.calls == 1
    assert memory.list_narratives("CANDIDATE")


def test_recorded_event_reaction_is_an_allow_listed_memory_source(tmp_path: Path):
    path = str(tmp_path / "event.db")
    operations = AgentOperations(path)
    memory = TradingMemory(path)
    with memory._con() as con:
        con.execute("CREATE TABLE event_reactions (id INTEGER PRIMARY KEY, event_source_id TEXT, symbol TEXT)")
        con.execute("INSERT INTO event_reactions(id,event_source_id,symbol) VALUES(?,?,?)", (7, "macro:source", "XAUUSD"))
    operations.enqueue_memory("event_reaction", "7", {"reaction_id": 7, "symbol": "XAUUSD"})
    result = process_outbox(operations, memory, FakeLLM(), "test-model")
    assert result["processed"] == 1
