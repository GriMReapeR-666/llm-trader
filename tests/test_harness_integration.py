"""End-to-End Integration Tests for Trading Harness Plugins, Agent Loop, and Eval Harness."""
import pytest
from datetime import datetime, timezone
import os

from core.harness import (
    TradingContext,
    EventStream,
    HarnessEvent,
    HarnessPlugin,
    SessionForkEngine,
)
from core.harness.plugins import (
    RiskManagementPlugin,
    TradingMemoryPlugin,
    BrokerPlugin,
    CausalIntelligencePlugin,
    DataHubPlugin,
    DeliberativeGraphPlugin,
)
from intelligence.harness_agent_loop import HarnessAgentLoop, CycleExecutionSummary
from research.eval_harness import TradingEvalHarness, EvalBenchmarkResult


def test_harness_plugins_mounting_and_lifecycle(tmp_path):
    db_path = str(tmp_path / "test_state.db")
    ctx = TradingContext(name="IntegrationTestCtx")

    ctx.plugin(TradingMemoryPlugin, db_path=db_path)
    ctx.plugin(RiskManagementPlugin, db_path=db_path, balance=10000.0, max_risk_pct=0.01)
    ctx.plugin(BrokerPlugin, paper_mode=True, db_path=db_path)
    ctx.plugin(CausalIntelligencePlugin, db_path=db_path)
    ctx.plugin(DataHubPlugin)
    ctx.plugin(DeliberativeGraphPlugin, db_path=db_path)

    assert ctx.has("memory")
    assert ctx.has("risk_manager")
    assert ctx.has("deterministic_validator")
    assert ctx.has("broker")
    assert ctx.has("trade_manager")
    assert ctx.has("macro_graph")
    assert ctx.has("causal_validator")
    assert ctx.has("market_data")
    assert ctx.has("llm_router")

    ctx.dispose()
    assert ctx.is_disposed


class _StubLiveMarketData:
    """Minimal provider satisfying the fail-closed cycle's live-data gate."""

    def get_latest_price(self, symbol):
        return 1.0850

    def get_atr(self, symbol):
        return 0.0015


class _StubLiveMarketDataPlugin(HarnessPlugin):
    name = "market_data"
    version = "1.0.0"
    dependencies = ["broker", "risk_manager"]

    def apply(self, ctx: TradingContext) -> None:
        ctx.provide("market_data", _StubLiveMarketData())


def test_harness_agent_loop_cycle(tmp_path):
    db_path = str(tmp_path / "test_loop.db")
    ctx = TradingContext(name="AgentLoopCtx")

    ctx.plugin(TradingMemoryPlugin, db_path=db_path)
    ctx.plugin(RiskManagementPlugin, db_path=db_path, balance=10000.0, max_risk_pct=0.01)
    ctx.plugin(BrokerPlugin, paper_mode=True, db_path=db_path)
    ctx.plugin(CausalIntelligencePlugin, db_path=db_path)
    ctx.plugin(_StubLiveMarketDataPlugin)
    # Permissive environment so a validated branch may proceed past gates.
    ctx.provide("market_env", {
        "market_open": True,
        "symbol_tradable": True,
        "tick_age_ms": 100.0,
        "max_tick_age_ms": 5000.0,
        "spread": 1.2,
        "max_spread": 2.5,
        "session_allowed": True,
        "liquidity_ok": True,
        "microstructure_ok": True,
        "clock_ok": True,
    })

    event_stream = EventStream(db_path=db_path)
    events_caught = []
    event_stream.subscribe_all(lambda e: events_caught.append(e.event_type))

    agent_loop = HarnessAgentLoop(root_ctx=ctx, event_stream=event_stream)
    summary = agent_loop.run_cycle(symbol="EURUSD", timeframe="M15")

    assert isinstance(summary, CycleExecutionSummary)
    assert summary.symbol == "EURUSD"
    assert summary.decision in ("EXECUTE", "HOLD")
    assert summary.duration_ms > 0

    assert "CYCLE_STARTED" in events_caught
    if summary.decision == "EXECUTE":
        assert "ORDER_ROUTED" in events_caught

    ctx.dispose()


def test_harness_agent_loop_fails_closed_without_live_market_data(tmp_path):
    """No live price/ATR from the provider must reject the cycle (no fabricated inputs)."""
    db_path = str(tmp_path / "test_loop_no_data.db")
    ctx = TradingContext(name="AgentLoopNoDataCtx")

    ctx.plugin(TradingMemoryPlugin, db_path=db_path)
    ctx.plugin(RiskManagementPlugin, db_path=db_path, balance=10000.0, max_risk_pct=0.01)
    ctx.plugin(BrokerPlugin, paper_mode=True, db_path=db_path)
    ctx.plugin(CausalIntelligencePlugin, db_path=db_path)
    ctx.plugin(DataHubPlugin)

    event_stream = EventStream(db_path=db_path)
    events_caught = []
    event_stream.subscribe_all(lambda e: events_caught.append((e.event_type, e.payload)))

    agent_loop = HarnessAgentLoop(root_ctx=ctx, event_stream=event_stream)
    summary = agent_loop.run_cycle(symbol="EURUSD", timeframe="M15")

    assert summary.decision == "HOLD"
    rejected = [payload for ev, payload in events_caught if ev == "CYCLE_REJECTED"]
    assert rejected and rejected[0].get("reason") == "insufficient_live_market_data"

    ctx.dispose()


def test_eval_harness_benchmark(tmp_path):
    db_path = str(tmp_path / "test_eval.db")
    harness = TradingEvalHarness(db_path=db_path)

    # Generate synthetic bar sequence
    sample_dataset = [
        {"close": 1.0850, "high": 1.0870, "low": 1.0830, "atr": 0.0015, "rsi": 35.0, "future_close": 1.0890},
        {"close": 1.0890, "high": 1.0910, "low": 1.0870, "atr": 0.0016, "rsi": 65.0, "future_close": 1.0860},
        {"close": 1.0860, "high": 1.0880, "low": 1.0840, "atr": 0.0014, "rsi": 45.0, "future_close": 1.0880},
    ]

    result = harness.run_benchmark(dataset=sample_dataset, symbol="EURUSD")

    assert isinstance(result, EvalBenchmarkResult)
    assert result.total_bars_evaluated == 3
    assert result.total_proposals_generated > 0
    assert result.proposals_approved_by_risk >= 0
    assert result.avg_latency_ms >= 0
    assert result.session_id.startswith("eval_")
