"""Comprehensive Integration Test Suite for all 4 DeepSeek Harness Runtime Modes."""
import pytest
import os
import json

from core.harness import TradingContext, EventStream
from core.harness.plugins import (
    RiskManagementPlugin,
    TradingMemoryPlugin,
    BrokerPlugin,
    CausalIntelligencePlugin,
    DataHubPlugin,
    DeliberativeGraphPlugin,
    SkillsPlugin,
    JournalAuditPlugin,
)
from core.harness.sandbox import CapabilitySandbox, PermissionLevel
from intelligence.harness_agent_loop import HarnessAgentLoop
from research.eval_harness import TradingEvalHarness
from research.creator_engine import CreatorEngine


def test_harness_full_context_mounting(tmp_path):
    db_path = str(tmp_path / "full_state.db")
    ctx = TradingContext(name="FullArchitectureCtx")

    ctx.plugin(TradingMemoryPlugin, db_path=db_path)
    ctx.plugin(RiskManagementPlugin, db_path=db_path, balance=10000.0, max_risk_pct=0.01)
    ctx.plugin(BrokerPlugin, paper_mode=True, db_path=db_path)
    ctx.plugin(CausalIntelligencePlugin, db_path=db_path)
    ctx.plugin(DataHubPlugin)
    ctx.plugin(DeliberativeGraphPlugin, db_path=db_path)
    ctx.plugin(SkillsPlugin, db_path=db_path)
    ctx.plugin(JournalAuditPlugin, db_path=db_path)

    expected_services = [
        "memory", "trading_store", "risk_manager", "deterministic_validator",
        "broker", "trade_manager", "macro_graph", "causal_validator",
        "market_data", "llm_router", "skill_registry", "code_interpreter",
        "creator_engine", "audit_fn", "audit_metrics_fn",
        "fred_fetch_fn", "cot_provider", "news_ingest_fn", "openbb_bridge",
        "broker_token", "sandbox"
    ]

    for s in expected_services:
        assert ctx.has(s), f"Missing service: {s}"

    ctx.dispose()
    assert ctx.is_disposed


def test_code_mode_execution(tmp_path):
    db_path = str(tmp_path / "code_mode.db")
    ctx = TradingContext(name="CodeModeCtx")
    ctx.plugin(SkillsPlugin, db_path=db_path)

    interpreter = ctx.get("code_interpreter")
    code = """
import numpy as np
arr = np.array([10.0, 20.0, 30.0])
result = {"mean": float(np.mean(arr)), "sum": float(np.sum(arr))}
"""
    exec_res = interpreter.execute_code(code)
    assert exec_res.success is True
    actual_result = exec_res.result.get("result") if isinstance(exec_res.result, dict) and "result" in exec_res.result else exec_res.result
    assert actual_result == {"mean": 20.0, "sum": 60.0}
    assert exec_res.execution_time_ms >= 0

    ctx.dispose()


def test_creator_mode_skill_synthesis_and_hot_mount(tmp_path):
    db_path = str(tmp_path / "creator_mode.db")
    ctx = TradingContext(name="CreatorModeCtx")
    ctx.plugin(SkillsPlugin, db_path=db_path)

    creator: CreatorEngine = ctx.get("creator_engine")
    registry = ctx.get("skill_registry")
    initial_skills_count = len(registry._skills)

    skill_code = """
def execute_skill(**kwargs):
    symbol = kwargs.get("symbol", "EURUSD")
    return {"status": "SUCCESS", "calculated_metric": 99.5, "symbol": symbol}
"""

    synth_res = creator.synthesize_skill(
        skill_name="synthetic_momentum_audit",
        description="Autonomous dynamic momentum calculator",
        category="DYNAMIC",
        code_str=skill_code,
    )

    assert synth_res.validation_passed is True
    assert synth_res.status == "VERIFIED"

    # Hot mount into registry
    disp = creator.hot_mount_skill(registry, synth_res, ctx=ctx)
    assert "synthetic_momentum_audit" in registry._skills
    assert len(registry._skills) == initial_skills_count + 1

    # Execute mounted skill via registry
    exec_out = registry.execute_skill("synthetic_momentum_audit", symbol="GBPUSD")
    assert exec_out["status"] == "SUCCESS"
    assert exec_out["output"]["symbol"] == "GBPUSD"
    assert exec_out["output"]["calculated_metric"] == 99.5

    # Reversible unmount
    disp.dispose()
    assert "synthetic_momentum_audit" not in registry._skills
    assert len(registry._skills) == initial_skills_count

    ctx.dispose()


def test_minimal_mode_eval_harness(tmp_path):
    db_path = str(tmp_path / "eval_mode.db")
    harness = TradingEvalHarness(db_path=db_path)

    dataset = [
        {"close": 1.0850, "high": 1.0870, "low": 1.0830, "atr": 0.0015, "rsi": 32.0, "future_close": 1.0880},
        {"close": 1.0880, "high": 1.0900, "low": 1.0860, "atr": 0.0014, "rsi": 68.0, "future_close": 1.0850},
    ]

    res = harness.run_benchmark(dataset=dataset, symbol="EURUSD")
    assert res.total_bars_evaluated == 2
    assert res.total_proposals_generated == 4
    assert res.session_id.startswith("eval_")


def test_harness_continuous_loop(tmp_path):
    db_path = str(tmp_path / "cont_loop.db")
    ctx = TradingContext(name="ContinuousLoopCtx")
    ctx.plugin(TradingMemoryPlugin, db_path=db_path)
    ctx.plugin(RiskManagementPlugin, db_path=db_path, balance=10000.0, max_risk_pct=0.01)
    ctx.plugin(BrokerPlugin, paper_mode=True, db_path=db_path)
    ctx.plugin(CausalIntelligencePlugin, db_path=db_path)

    stream = EventStream(db_path=db_path)
    loop = HarnessAgentLoop(root_ctx=ctx, event_stream=stream)

    # Run exactly 2 cycles
    summaries = loop.run_continuous(
        symbols=["EURUSD"],
        timeframe="M15",
        interval_seconds=0.01,
        max_cycles=2,
    )

    assert len(summaries) == 2
    assert summaries[0].decision in ("EXECUTE", "HOLD", "REJECT")
    ctx.dispose()
