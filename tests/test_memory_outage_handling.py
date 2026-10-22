"""Memory-distillation outage handling (recurring 'memory batch contained
failed items' root-cause fix).

The router's fail-closed contract returns a deterministic-HOLD *string* when
all providers are down. process_outbox used to parse that string as a model
reply, fail validation with 'memory response missing required fields', and
retry forever (source_ids 37-39 re-failed on five consecutive boots). These
tests pin the corrected behavior:

* a deterministic HOLD / 'Error:' string is classified as a provider outage
  (parse_status PROVIDER_OUTAGE), never counted as a parse failure;
* response accounting is not polluted by outage strings;
* complete_memory returns the row's new status so callers can distinguish
  retry from permanent parking;
* at retry exhaustion the documented deterministic fallback narrative is
  saved (distillation_fallback_used) instead of parking a dead row.
"""
import json
from pathlib import Path

from operations.agent_operations import AgentOperations
from intelligence.local_llm_memory import _fallback, process_outbox
from intelligence.trading_memory import TradingMemory
from core.trading_safety import TradeProposal, TradingStore


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def generate(self, messages, model=""):
        self.calls += 1
        return self.reply


HOLD_REPLY = json.dumps({
    "action": "HOLD",
    "decision": "HOLD",
    "confidence": 0.0,
    "deterministic_fallback": True,
    "reasoning": "Deterministic HOLD fallback: all LLM providers failed for agent 'trader'.",
    "provider_errors": ["nvidia_nim: paused until quota reset (~21590s remaining); skipped"],
})


def _verified_setup(tmp_path: Path):
    """One closed_trade with a matching label = a verified outbox item."""
    path = str(tmp_path / "memory.db")
    store = TradingStore(path)
    memory = TradingMemory(path)
    proposal = TradeProposal("scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8,
                             {"setup_id": "memory", "regime": "trend"})
    store.record_open_trade(201, proposal, 1.0, 1.0, {"features": {"regime": "trend"}}, provenance="mt5_broker")
    store.record_close(201, 1.3, 0.3, "target")
    label = memory.label_closed_trade(201, 0.3, 1.0, "target")
    operations = AgentOperations(path)
    operations.enqueue_memory("closed_trade", "201",
                              {"ticket": 201, "label": label["label"],
                               "r_multiple": label["r_multiple"]})
    return operations, memory


def test_deterministic_hold_reply_is_classified_as_provider_outage(tmp_path: Path):
    operations, memory = _verified_setup(tmp_path)
    llm = FakeLLM(HOLD_REPLY)

    result = process_outbox(operations, memory, llm, "test-model")

    assert result["outage_failed"] == 1
    assert result["failed"] == 0
    assert result["parse_status"] == "PROVIDER_OUTAGE"
    assert result["response_chars"] == 0          # HOLD text is not model output
    assert result["response_hash"] == ""          # and must not be hashed as one
    assert llm.calls == 1
    # The item stays queued for retry (not parked) after the first outage.
    with operations._con() as con:
        row = con.execute("SELECT status, error FROM memory_outbox").fetchone()
    assert row["status"] == "PENDING"
    assert "provider outage" in row["error"]
    # The failure event carries the truthful outage reason.
    with operations._con() as con:
        payload = con.execute(
            "SELECT payload FROM operational_events "
            "WHERE event_type='distillation_failed'").fetchone()["payload"]
    assert "provider outage" in payload
    assert "missing required fields" not in payload


def test_provider_error_string_is_classified_as_outage(tmp_path: Path):
    operations, memory = _verified_setup(tmp_path)
    llm = FakeLLM("Error: openrouter quota exhausted (429); paused until quota reset")

    result = process_outbox(operations, memory, llm, "test-model")

    assert result["outage_failed"] == 1
    assert result["failed"] == 0
    assert result["parse_status"] == "PROVIDER_OUTAGE"
    with operations._con() as con:
        assert con.execute("SELECT status FROM memory_outbox").fetchone()["status"] == "PENDING"


def test_complete_memory_returns_sent_for_success(tmp_path: Path):
    operations, _ = _verified_setup(tmp_path)
    with operations._con() as con:
        row = con.execute("SELECT id FROM memory_outbox").fetchone()
    assert operations.complete_memory(row["id"]) == "SENT"


def test_complete_memory_returns_pending_while_retry_budget_remains(tmp_path: Path):
    operations, _ = _verified_setup(tmp_path)
    with operations._con() as con:
        row = con.execute("SELECT id FROM memory_outbox").fetchone()
    assert operations.complete_memory(row["id"], "still failing") == "PENDING"


def test_retry_exhaustion_saves_deterministic_fallback_narrative(tmp_path: Path):
    operations, memory = _verified_setup(tmp_path)
    with operations._con() as con:
        row = con.execute("SELECT id, kind, source_id, payload FROM memory_outbox").fetchone()
    # Drive the outbox row to its last permitted attempt.
    with operations._con() as con:
        con.execute("UPDATE memory_outbox SET attempts=7 WHERE id=?", (row["id"],))

    llm = FakeLLM(HOLD_REPLY)
    result = process_outbox(operations, memory, llm, "test-model")

    assert result["fallback_saved"] == 1
    assert result["failed"] == 0
    with operations._con() as con:
        assert con.execute("SELECT status FROM memory_outbox").fetchone()["status"] == "FAILED"
        events = [r["event_type"] for r in con.execute(
            "SELECT event_type FROM operational_events "
            "WHERE event_type IN ('distillation_failed','distillation_fallback_used')")]
    assert events.count("distillation_fallback_used") == 1
    assert events.count("distillation_failed") == 1
    # The fallback narrative is auditable and carries the same verified payload.
    narratives = memory.list_narratives("CANDIDATE")
    assert narratives
    fallback = _fallback(row["kind"], row["source_id"], json.loads(row["payload"]))
    assert "Verified" in fallback["summary"]


def test_partial_outage_with_success_is_partial_failure(tmp_path: Path):
    operations, memory = _verified_setup(tmp_path)
    # A second verified item (its own closed trade + matching label).
    store = TradingStore(str(tmp_path / "memory.db"))
    proposal = TradeProposal("scalp", "EURUSD", "SELL", 1.1, 1.2, 1.0, 0.8,
                             {"setup_id": "memory", "regime": "trend"})
    store.record_open_trade(202, proposal, 1.2, 1.2, {"features": {"regime": "trend"}}, provenance="mt5_broker")
    store.record_close(202, 1.0, 0.2, "stop")
    label = memory.label_closed_trade(202, 0.2, 1.2, "stop")
    operations.enqueue_memory("closed_trade", "202",
                              {"ticket": 202, "label": label["label"],
                               "r_multiple": label["r_multiple"]})

    class SwitchingLLM:
        def __init__(self, ok_reply):
            self.ok_reply = ok_reply
            self.calls = 0

        def generate(self, messages, model=""):
            self.calls += 1
            return self.ok_reply if self.calls == 1 else HOLD_REPLY

    llm = SwitchingLLM(json.dumps({"tags": {"kind": "closed_trade"},
                                   "summary": "verified result", "lesson": "retain costs"}))
    result = process_outbox(operations, memory, llm, "test-model")

    assert result["processed"] == 1
    assert result["outage_failed"] == 1
    assert result["parse_status"] == "PARTIAL_FAILURE"
    assert result["response_chars"] > 0   # from the one good reply
