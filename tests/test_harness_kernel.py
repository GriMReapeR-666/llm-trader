"""Comprehensive Unit & Integration Tests for Trading Harness Microkernel."""
import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import tempfile
import os

from core.harness import (
    TradingContext,
    HarnessPlugin,
    Disposable,
    CompositeDisposable,
    EventStream,
    HarnessEvent,
    SessionForkEngine,
    Hypothesis,
    BranchResult,
    CapabilitySandbox,
    CapabilityToken,
    PermissionLevel,
)


class DummyRiskService:
    def __init__(self, max_risk=0.01):
        self.max_risk = max_risk


class DummyBrokerService:
    def __init__(self):
        self.orders = []
        self.connected = True

    def close(self):
        self.connected = False


class SamplePlugin(HarnessPlugin):
    name = "sample_plugin"
    version = "1.2.0"
    dependencies = ["risk_service"]

    def apply(self, ctx: TradingContext) -> None:
        broker = DummyBrokerService()
        ctx.provide("broker_service", broker)
        # Reversible effect: clean disconnect on context disposal
        ctx.effect(lambda: broker.close(), name="broker_disconnect")


def test_disposable_and_composite():
    events = []

    d1 = Disposable(lambda: events.append("d1"), name="disp1")
    d2 = Disposable(lambda: events.append("d2"), name="disp2")

    composite = CompositeDisposable(d1, d2)
    assert len(composite) == 2
    assert not composite.is_disposed

    composite.dispose()
    assert composite.is_disposed
    # LIFO order
    assert events == ["d2", "d1"]

    # Idempotent
    composite.dispose()
    assert events == ["d2", "d1"]


def test_trading_context_services():
    root = TradingContext(name="Root")
    risk = DummyRiskService()
    root.provide("risk_service", risk)

    assert root.has("risk_service")
    assert root.get("risk_service") is risk
    assert root.get("non_existent", "default_val") == "default_val"

    with pytest.raises(KeyError):
        root.get("non_existent")

    child = root.create_child("ChildScope")
    assert child.has("risk_service")
    assert child.get("risk_service") is risk

    # Child override does not mutate parent
    child_risk = DummyRiskService(max_risk=0.02)
    child.provide("risk_service", child_risk)
    assert child.get("risk_service") is child_risk
    assert root.get("risk_service") is risk

    root.dispose()
    assert root.is_disposed
    assert child.is_disposed


def test_plugin_mounting_and_dependencies():
    ctx = TradingContext(name="PluginTestCtx")

    # Mounting without dependency raises error
    with pytest.raises(RuntimeError, match="requires service 'risk_service'"):
        ctx.plugin(SamplePlugin)

    # Provide dependency
    ctx.provide("risk_service", DummyRiskService())
    ctx.plugin(SamplePlugin)

    assert ctx.has("broker_service")
    broker = ctx.get("broker_service")
    assert broker.connected is True

    # Disposal triggers reversible cleanup
    ctx.dispose()
    assert broker.connected is False


def test_event_stream_and_replay(tmp_path):
    db_path = str(tmp_path / "test_events.db")
    stream = EventStream(db_path=db_path)

    received_ticks = []
    received_all = []

    sub_tick = stream.subscribe("TICK", lambda e: received_ticks.append(e.payload))
    sub_all = stream.subscribe_all(lambda e: received_all.append(e))

    stream.emit("TICK", {"symbol": "EURUSD", "bid": 1.0850}, session_id="s1")
    stream.emit("NEWS", {"headline": "CPI releases inline"}, session_id="s1")

    assert len(received_ticks) == 1
    assert received_ticks[0]["symbol"] == "EURUSD"
    assert len(received_all) == 2

    # Test reversible unsubscription
    sub_tick.dispose()
    stream.emit("TICK", {"symbol": "GBPUSD", "bid": 1.2700}, session_id="s1")
    assert len(received_ticks) == 1  # Unsubscribed, count remains 1
    assert len(received_all) == 3

    # Test replay from SQLite store
    replayed = stream.replay_events(session_id="s1")
    assert len(replayed) == 3
    assert replayed[0].event_type == "TICK"
    assert replayed[1].event_type == "NEWS"
    assert replayed[2].payload["symbol"] == "GBPUSD"


def test_session_forking_and_counterfactual_arbitration():
    root = TradingContext(name="MainMarketContext")
    root.provide("risk_service", DummyRiskService())
    root.set_state("market_regime", "TRENDING_BULLISH")

    fork_engine = SessionForkEngine()

    hypotheses = [
        Hypothesis(name="BreakoutLong", prompt_override="Buy breakout", weight=1.0),
        Hypothesis(name="FadeShort", prompt_override="Fade resistance", weight=0.8),
    ]

    snapshot = {"symbol": "EURUSD", "price": 1.0850, "atr": 0.0015}

    def branch_evaluator(branch_ctx: TradingContext, hyp: Hypothesis, snap: Dict[str, Any]) -> BranchResult:
        # Verify isolation
        assert branch_ctx.has("risk_service")
        assert branch_ctx.get_state("hypothesis") == hyp

        if hyp.name == "BreakoutLong":
            return BranchResult(
                hypothesis_name=hyp.name,
                proposal={"side": "BUY", "sl": 1.0830, "tp": 1.0900},
                confidence_score=0.88,
                passed_risk=True,
            )
        else:
            return BranchResult(
                hypothesis_name=hyp.name,
                proposal={"side": "SELL", "sl": 1.0870, "tp": 1.0800},
                confidence_score=0.55,
                passed_risk=False,  # fails risk
            )

    results = fork_engine.evaluate_branches(root, snapshot, hypotheses, branch_evaluator)
    assert len(results) == 2

    winner = fork_engine.arbitrate_best_branch(results)
    assert winner is not None
    assert winner.hypothesis_name == "BreakoutLong"
    assert winner.confidence_score == 0.88
    assert winner.passed_risk is True


def test_capability_sandbox():
    sandbox = CapabilitySandbox(default_level=PermissionLevel.READ_ONLY)

    token_read = sandbox.issue_token(PermissionLevel.READ_ONLY, scopes=["market_data"])
    token_exec = sandbox.issue_token(PermissionLevel.ORDER_WRITE, scopes=["broker"])

    assert sandbox.verify_call(token_read, PermissionLevel.READ_ONLY, scope="market_data") is True
    assert sandbox.verify_call(token_read, PermissionLevel.ORDER_WRITE, scope="broker") is False
    assert sandbox.verify_call(token_exec, PermissionLevel.ORDER_WRITE, scope="broker") is True

    @sandbox.guard(PermissionLevel.ORDER_WRITE, scope="broker")
    def send_order(symbol: str, token: Optional[CapabilityToken] = None):
        return f"ORDER_{symbol}"

    # Read token fails guard
    with pytest.raises(PermissionError, match="Capability authorization failed"):
        send_order("EURUSD", token=token_read)

    # Exec token passes guard
    assert send_order("EURUSD", token=token_exec) == "ORDER_EURUSD"
