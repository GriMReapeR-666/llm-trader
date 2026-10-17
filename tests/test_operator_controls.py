"""C4: emergency controls must actually do something, and the autonomy gate
must fail closed.

Covers:
- apply_operator_control(): real flatten / real killswitch (store + autonomy
  halt + latch + pending-order cancel), honest applied/partial/queued/failed
  statuses.
- LLMOrchestrator._autonomy_gate(): drains and applies controls, then HOLDs;
  any error inside the gate returns HOLD instead of trading past it.
"""
import pytest

from core.trading_safety import TradingStore
from data.kill_switch_latch import KillSwitchLatch
from operations import autonomy as autonomy_mod
from operations.autonomy import AutonomyManager, apply_operator_control


# ---------------------------------------------------------------- helpers
class FakeRisk:
    def __init__(self):
        self.reason = None
        self.triggered_by = None

    def trigger_emergency_flatten(self, reason):
        self.reason = reason
        self.triggered_by = reason

    def pending_flatten_reason(self):
        return self.reason

    def clear_flatten_request(self):
        self.reason = None


class ClosingTradeManager:
    """Simulates a TradeManager that closes every owned position."""

    def __init__(self, risk):
        self.risk = risk
        self.calls = 0

    def enforce_protective_flatten(self):
        self.calls += 1
        self.risk.clear_flatten_request()
        return True


class StuckTradeManager:
    """Simulates a TradeManager that cannot close (positions remain)."""

    def __init__(self, risk):
        self.risk = risk
        self.calls = 0

    def enforce_protective_flatten(self):
        self.calls += 1
        return True


class OpsStub:
    def __init__(self, intents=None):
        self._intents = intents or []
        self.resolved = []

    def order_intents(self, statuses=("PENDING",)):
        return list(self._intents)

    def resolve_order_intent(self, key, status, *, ticket=None, result=None):
        self.resolved.append((key, status, ticket))


def _autonomy(tmp_path):
    return AutonomyManager(
        str(tmp_path / "autonomy_state.json"),
        str(tmp_path / "control_plane.jsonl"),
    )


# ------------------------------------------------------- apply_operator_control
def test_killswitch_sets_store_halts_autonomy_and_latches(tmp_path):
    store = TradingStore(str(tmp_path / "trading.db"))
    auto = _autonomy(tmp_path)
    latch = KillSwitchLatch(path=str(tmp_path / "latch.json"))
    ops = OpsStub()

    outcome = apply_operator_control(
        {"action": "killswitch", "actor": "deck"},
        store=store, autonomy=auto, operations=ops, latch=latch,
    )

    assert outcome["status"] == "applied", outcome
    assert store.is_kill_switch_active() is True
    allowed, why = auto.trading_allowed()
    assert allowed is False and "killswitch" in why
    assert latch.snapshot()["latched"] is True
    assert outcome["pending_cancelled"] == 0


def test_killswitch_cancels_pending_order_intents(tmp_path):
    store = TradingStore(str(tmp_path / "trading.db"))
    auto = _autonomy(tmp_path)
    latch = KillSwitchLatch(path=str(tmp_path / "latch.json"))
    intents = [
        {"intent_key": "k1", "mt5_ticket": 101, "payload": '{"magic": 8888}'},
        {"intent_key": "k2", "mt5_ticket": 0, "payload": "{}"},
    ]
    ops = OpsStub(intents=intents)

    class _Recorder:
        calls = []

    import core.order_execution as oe

    def _fake_cancel(ticket, paper, magic):
        _Recorder.calls.append((ticket, paper, magic))
        return ticket == 101

    original = oe.cancel_pending_order
    oe.cancel_pending_order = _fake_cancel
    try:
        outcome = apply_operator_control(
            {"action": "killswitch", "actor": "deck"},
            store=store, autonomy=auto, operations=ops, latch=latch,
        )
    finally:
        oe.cancel_pending_order = original

    assert _Recorder.calls == [(101, None, 8888)]
    assert outcome["status"] == "applied", outcome
    assert outcome["pending_cancelled"] == 1
    assert ops.resolved == [("k1", "CANCELLED", 101)]


def test_killswitch_fails_closed_when_store_write_fails(tmp_path):
    auto = _autonomy(tmp_path)
    broken = MagicMockStore()

    outcome = apply_operator_control(
        {"action": "killswitch", "actor": "deck"},
        store=broken, autonomy=auto, operations=OpsStub(),
    )
    assert outcome["status"] == "failed"
    assert any("store kill switch" in e for e in outcome["errors"])


class MagicMockStore:
    def set_kill_switch(self, active, reason=""):
        raise RuntimeError("db unavailable")


def test_flatten_applied_when_positions_close(tmp_path):
    auto = _autonomy(tmp_path)
    risk = FakeRisk()
    tm = ClosingTradeManager(risk)

    outcome = apply_operator_control(
        {"action": "flatten", "actor": "deck"},
        store=None, autonomy=auto, risk=risk, trade_manager=tm,
    )
    assert outcome["status"] == "applied", outcome
    assert tm.calls == 1
    assert risk.triggered_by == "operator:deck"
    assert risk.pending_flatten_reason() is None


def test_flatten_partial_when_positions_remain(tmp_path):
    auto = _autonomy(tmp_path)
    risk = FakeRisk()
    tm = StuckTradeManager(risk)

    outcome = apply_operator_control(
        {"action": "flatten", "actor": "deck"},
        store=None, autonomy=auto, risk=risk, trade_manager=tm,
    )
    assert outcome["status"] == "partial"
    assert outcome["reason"] == "operator:deck"


def test_flatten_queued_without_trade_manager(tmp_path):
    auto = _autonomy(tmp_path)
    risk = FakeRisk()

    outcome = apply_operator_control(
        {"action": "flatten", "actor": "deck"},
        store=None, autonomy=auto, risk=risk, trade_manager=None,
    )
    assert outcome["status"] == "queued"
    # The durable flag is set so TradeManager applies it on its next tick.
    assert risk.pending_flatten_reason() == "operator:deck"


def test_flatten_fails_without_risk_handle(tmp_path):
    auto = _autonomy(tmp_path)
    outcome = apply_operator_control(
        {"action": "flatten", "actor": "deck"},
        store=None, autonomy=auto, risk=None,
    )
    assert outcome["status"] == "failed"


def test_pause_resume_report_applied_by_drain(tmp_path):
    auto = _autonomy(tmp_path)
    for action in ("pause", "resume"):
        outcome = apply_operator_control(
            {"action": action, "actor": "deck"},
            store=None, autonomy=auto,
        )
        assert outcome["status"] == "applied"
        assert outcome["applied_by"] == "drain"


# ------------------------------------------------------------- _autonomy_gate
@pytest.fixture
def gate_orch():
    from intelligence.llm_orchestrator import LLMOrchestrator

    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.store = None
    orch.risk = None
    orch.operations = OpsStub()
    orch.paper_broker = None
    orch.data_provider = type("DP", (), {"trade_manager": None})()
    return orch


@pytest.fixture
def gate_env(monkeypatch, tmp_path):
    """Route the gate to isolated autonomy/latch/event state."""
    auto = _autonomy(tmp_path)
    monkeypatch.setattr(autonomy_mod, "_default_manager", auto)

    latch = KillSwitchLatch(path=str(tmp_path / "latch.json"))
    import data.kill_switch_latch as latch_mod
    monkeypatch.setattr(latch_mod, "get_kill_switch_latch", lambda: latch)

    events = []

    import operations.event_bridge as bridge
    monkeypatch.setattr(bridge, "emit", lambda etype, payload=None, **kw: events.append((etype, payload)))

    store = TradingStore(str(tmp_path / "trading.db"))
    return {"autonomy": auto, "latch": latch, "events": events, "store": store}


def test_gate_passes_when_all_clear(gate_orch, gate_env):
    gate_orch.store = gate_env["store"]
    assert gate_orch._autonomy_gate("EURUSD", "scalp") is None
    assert gate_env["events"] == []


def test_gate_applies_killswitch_then_holds(gate_orch, gate_env, monkeypatch):
    gate_orch.store = gate_env["store"]

    # Simulate a killswitch issued from the deck while the engine cycles.
    gate_env["autonomy"].issue_control("killswitch", actor="deck", source="command-deck")

    result = gate_orch._autonomy_gate("EURUSD", "scalp")
    assert result is not None
    decision, _lat = result
    assert decision["action"] == "HOLD"
    assert "killswitch" in decision["reasoning"]

    # The control really happened:
    assert gate_env["store"].is_kill_switch_active() is True
    assert gate_env["latch"].snapshot()["latched"] is True
    types = [t for t, _ in gate_env["events"]]
    assert "CONTROL_EXECUTED" in types
    executed = [p for t, p in gate_env["events"] if t == "CONTROL_EXECUTED"]
    assert executed[0]["status"] in ("applied", "partial")

    # Queue is drained: a second gate run holds on autonomy, not on re-apply.
    result2 = gate_orch._autonomy_gate("EURUSD", "scalp")
    assert result2[0]["action"] == "HOLD"
    assert "killswitch" in result2[0]["reasoning"]


def test_gate_emits_control_failed_and_holds_when_apply_breaks(
    gate_orch, gate_env, monkeypatch,
):
    gate_orch.store = MagicMockStore()  # set_kill_switch raises
    gate_env["autonomy"].issue_control("killswitch", actor="deck")

    result = gate_orch._autonomy_gate("EURUSD", "scalp")
    assert result is not None and result[0]["action"] == "HOLD"
    types = [t for t, _ in gate_env["events"]]
    assert "CONTROL_FAILED" in types
    assert "CONTROL_EXECUTED" not in types


def test_gate_fails_closed_when_autonomy_unavailable(gate_orch, monkeypatch):
    def _boom():
        raise RuntimeError("control plane unreadable")

    monkeypatch.setattr(autonomy_mod, "get_autonomy", _boom)
    result = gate_orch._autonomy_gate("EURUSD", "scalp")
    assert result is not None
    decision, _lat = result
    assert decision["action"] == "HOLD"
    assert "fail-closed" in decision["reasoning"]


def test_gate_fails_closed_when_trading_allowed_raises(gate_orch, gate_env, monkeypatch):
    gate_orch.store = gate_env["store"]

    def _raise():
        raise RuntimeError("state corrupt")

    monkeypatch.setattr(type(gate_env["autonomy"]), "trading_allowed", lambda self: _raise())
    result = gate_orch._autonomy_gate("EURUSD", "scalp")
    assert result is not None and result[0]["action"] == "HOLD"
    assert "fail-closed" in result[0]["reasoning"]


def test_gate_applies_pause_from_queue(gate_orch, gate_env):
    gate_orch.store = gate_env["store"]
    gate_env["autonomy"].issue_control("pause", actor="deck")

    result = gate_orch._autonomy_gate("EURUSD", "scalp")
    assert result is not None and result[0]["action"] == "HOLD"
    assert "operator:" in result[0]["reasoning"]
    types = [t for t, _ in gate_env["events"]]
    assert "CONTROL_EXECUTED" in types
