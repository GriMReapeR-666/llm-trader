"""Test Suite for Institutional Quantitative & Algorithmic Trading Workflows."""
import pytest
import os
import numpy as np

from core.harness import TradingContext
from core.harness.plugins import (
    RiskManagementPlugin,
    TradingMemoryPlugin,
    BrokerPlugin,
    CausalIntelligencePlugin,
    DataHubPlugin,
    AnalyticalFrameworksPlugin,
    KronosIntelligencePlugin,
    ExecutionSafetyPlugin,
    CointegrationGovernancePlugin,
    QuantWorkflowsPlugin,
)
from core.harness.workflows import (
    QuantWorkflowEngine,
    StatisticalArbitrageWorkflow,
    MeanReversionWorkflow,
    MacroCausalMomentumWorkflow,
    MicrostructureBreakoutWorkflow,
    NeuralFoundationWorkflow,
)


@pytest.fixture
def workflow_context(tmp_path):
    db_path = str(tmp_path / "workflow_test.db")
    ctx = TradingContext(name="WorkflowTestCtx")
    ctx.plugin(TradingMemoryPlugin, db_path=db_path)
    ctx.plugin(RiskManagementPlugin, db_path=db_path, balance=10000.0, max_risk_pct=0.01)
    ctx.plugin(BrokerPlugin, paper_mode=True, db_path=db_path)
    ctx.plugin(CausalIntelligencePlugin, db_path=db_path)
    ctx.plugin(DataHubPlugin)
    ctx.plugin(AnalyticalFrameworksPlugin)
    ctx.plugin(KronosIntelligencePlugin)
    ctx.plugin(ExecutionSafetyPlugin)
    ctx.plugin(CointegrationGovernancePlugin)
    ctx.plugin(QuantWorkflowsPlugin)
    yield ctx
    ctx.dispose()


def test_quant_workflow_engine_discovery(workflow_context):
    engine: QuantWorkflowEngine = workflow_context.get("quant_workflow_engine")
    workflows = engine.list_workflows()
    names = [w["name"] for w in workflows]
    assert "statistical_arbitrage" in names
    assert "mean_reversion" in names
    assert "macro_causal_momentum" in names
    assert "microstructure_breakout" in names
    assert "neural_kronos_foundation" in names


def test_statistical_arbitrage_workflow(workflow_context):
    engine: QuantWorkflowEngine = workflow_context.get("quant_workflow_engine")
    snapshot = {
        "symbol": "EURUSD",
        "pair_asset": "GBPUSD",
        "prices_y": [1.0850 + np.sin(i / 5.0) * 0.0020 for i in range(60)],
        "prices_x": [1.2650 + np.sin(i / 5.0) * 0.0022 for i in range(60)],
        "atr": 0.0015,
    }
    res = engine.execute_workflow("statarb", ctx=workflow_context, symbol="EURUSD", market_snapshot=snapshot)
    assert res.workflow_name == "statistical_arbitrage"
    assert res.decision in ("EXECUTE", "HOLD")
    assert len(res.steps) >= 3


def test_mean_reversion_workflow(workflow_context):
    engine: QuantWorkflowEngine = workflow_context.get("quant_workflow_engine")
    snapshot = {
        "symbol": "EURUSD",
        "prices": [1.0850 + np.sin(i / 4.0) * 0.0030 for i in range(80)],
        "atr": 0.0015,
    }
    res = engine.execute_workflow("mean_reversion", ctx=workflow_context, symbol="EURUSD", market_snapshot=snapshot)
    assert res.workflow_name == "mean_reversion"
    assert res.decision in ("EXECUTE", "HOLD")
    assert len(res.steps) >= 3


def test_macro_causal_momentum_workflow(workflow_context):
    engine: QuantWorkflowEngine = workflow_context.get("quant_workflow_engine")
    snapshot = {
        "symbol": "EURUSD",
        "price": 1.0850,
        "atr": 0.0015,
    }
    res = engine.execute_workflow("macro_momentum", ctx=workflow_context, symbol="EURUSD", market_snapshot=snapshot)
    assert res.workflow_name == "macro_causal_momentum"
    assert res.decision == "EXECUTE"
    assert res.proposal is not None
    assert res.proposal["strategy"] == "macro_causal_momentum"


def test_microstructure_breakout_workflow(workflow_context):
    engine: QuantWorkflowEngine = workflow_context.get("quant_workflow_engine")
    snapshot = {
        "symbol": "EURUSD",
        "prices": [1.0800 + (i ** 1.1) * 0.0002 for i in range(80)],
        "bid": 1.0850,
        "ask": 1.0852,
        "atr": 0.0015,
    }
    res = engine.execute_workflow("microstructure", ctx=workflow_context, symbol="EURUSD", market_snapshot=snapshot)
    assert res.workflow_name == "microstructure_breakout"
    assert res.decision in ("EXECUTE", "HOLD")


def test_neural_kronos_foundation_workflow(workflow_context):
    engine: QuantWorkflowEngine = workflow_context.get("quant_workflow_engine")
    snapshot = {
        "symbol": "EURUSD",
        "predicted_prob": 0.68,
        "predicted_return": 0.75,
        "confidence": 0.82,
        "price": 1.0850,
        "atr": 0.0015,
    }
    res = engine.execute_workflow("neural_kronos", ctx=workflow_context, symbol="EURUSD", market_snapshot=snapshot)
    assert res.workflow_name == "neural_kronos_foundation"
    assert res.decision == "EXECUTE"
    assert res.execution_ticket == 9505
