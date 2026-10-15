"""Test suite for QuantWorkflowEngine dynamic ensemble evaluation."""
import pytest
from core.harness.kernel import TradingContext
from core.harness.plugins import (
    RiskManagementPlugin,
    ExecutionSafetyPlugin,
    CointegrationGovernancePlugin,
    QuantWorkflowsPlugin,
    MacroEventAlphaPlugin,
)
from core.harness.workflows import QuantWorkflowEngine


def test_quant_workflow_ensemble_evaluation_and_selection():
    ctx = TradingContext(name="TestQuantWorkflowEnsemble")
    ctx.plugin(RiskManagementPlugin, balance=100000.0, max_risk_pct=0.01)
    ctx.plugin(ExecutionSafetyPlugin)
    ctx.plugin(CointegrationGovernancePlugin)
    ctx.plugin(MacroEventAlphaPlugin)
    ctx.plugin(QuantWorkflowsPlugin)

    engine = QuantWorkflowEngine()
    snapshot = {
        "symbol": "EURUSD",
        "price": 1.0850,
        "bid": 1.0849,
        "ask": 1.0851,
        "atr": 0.0015,
        "quote_spread": 0.0002,
        "macro_regime": "EXPANSION",
        "z_score": 0.2,
        "vpin": 0.25,
        "confidence": 0.85,
    }

    res = engine.evaluate_and_select_best_workflow(ctx=ctx, symbol="EURUSD", market_snapshot=snapshot)
    
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["evaluated_count"] >= 5
    assert res["selected_workflow_name"] in [
        "statistical_arbitrage", "mean_reversion", "macro_momentum",
        "microstructure", "neural_kronos", "macro_event_alpha", "retail_quant"
    ]
    assert res["best_composite_score"] is not None
    assert res["best_decision"] in ("EXECUTE", "HOLD", "REJECT", "CIRCUIT_BREAKER")
    assert len(res["all_evaluations"]) == res["evaluated_count"]
    assert res["execution_authority"] is False
