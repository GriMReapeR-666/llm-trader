import pytest
from core.quant_gates_and_pipeline import (
    QuantGate,
    QuantGateVerdict,
    QuantGateStack,
    calculate_composite_score,
)

def test_quant_gate_taxonomy():
    assert len(QuantGate) == 9
    assert QuantGate.DATA_QUALITY_GATE.value == "DATA_QUALITY_GATE"
    assert QuantGate.LIVE_SHADOW_GATE.value == "LIVE_SHADOW_GATE"

def test_composite_scoring_formula():
    metrics = {
        "sharpe": 2.1,                    # return (25%)
        "max_drawdown": 0.08,             # dd control (20%)
        "regime_consistency_pct": 80.0,   # regime (15%)
        "slippage_degradation_pct": 10.0, # exec (15%)
        "probability_of_ruin": 0.01,      # mc (10%)
        "max_correlation": 0.20,          # div (10%)
        "capacity_usd": 800000.0,         # cap (5%)
    }
    score = calculate_composite_score(metrics)
    assert 0.0 <= score <= 1.0
    assert score > 0.70  # Solid candidate

def test_quant_gate_stack_passing_and_failing():
    stack = QuantGateStack()
    
    # Passing candidate
    passing_metrics = {
        "is_data_clean": True,
        "oos_sharpe": 1.2,
        "win_rate": 0.52,
        "max_drawdown": 0.12,
        "profit_factor": 1.45,
        "slippage_degradation_pct": 20.0,
        "max_correlation": 0.35,
        "walk_forward_efficiency": 0.65,
        "probability_of_ruin": 0.01,
        "shock_regime_return": 0.02,
        "regime_consistency_pct": 75.0,
        "capacity_usd": 500000.0,
    }
    verdict = stack.evaluate_candidate(passing_metrics)
    assert isinstance(verdict, QuantGateVerdict)
    assert verdict.passed is True
    assert len(verdict.passed_gates) == 9
    assert len(verdict.failed_gates) == 0
    assert verdict.execution_authority is False
    assert "DATA_QUALITY_GATE" in verdict.gate_scores
    assert verdict.to_dict()["passed"] is True

    # Failing candidate (High Max Drawdown & Severe Slippage)
    failing_metrics = dict(passing_metrics)
    failing_metrics["max_drawdown"] = 0.28
    failing_metrics["slippage_degradation_pct"] = 65.0
    verdict_fail = stack.evaluate_candidate(failing_metrics)
    assert verdict_fail.passed is False
    assert "RISK_GATE" in verdict_fail.failed_gates
    assert "EXECUTION_GATE" in verdict_fail.failed_gates

def test_execution_order_manager():
    from core.quant_gates_and_pipeline import (
        ExecutionOrderManager,
        OrderType,
        OrderRequest,
    )

    om = ExecutionOrderManager()

    # Standard Limit Order
    ord_limit = om.create_order(
        symbol="EURUSD",
        side="BUY",
        quantity=1.0,
        order_type=OrderType.LIMIT,
        price=1.1050,
    )
    assert isinstance(ord_limit, OrderRequest)
    assert len(ord_limit.child_orders) == 1
    assert ord_limit.child_orders[0]["quantity"] == 1.0

    # TWAP Sliced Order (5 slices)
    ord_twap = om.create_order(
        symbol="EURUSD",
        side="BUY",
        quantity=5.0,
        order_type=OrderType.TWAP,
        slices=5,
    )
    assert len(ord_twap.child_orders) == 5
    assert ord_twap.child_orders[0]["quantity"] == 1.0

    # Iceberg Sliced Order (Display 0.5 lot)
    ord_iceberg = om.create_order(
        symbol="BTCUSD",
        side="SELL",
        quantity=2.0,
        order_type=OrderType.ICEBERG,
        iceberg_display_qty=0.5,
    )
    assert len(ord_iceberg.child_orders) == 4
    assert ord_iceberg.child_orders[0]["quantity"] == 0.5

    # Dynamic Leverage calculation
    lev_normal = om.calculate_dynamic_leverage(base_leverage=3.0, current_drawdown=0.02, kill_dd=0.15)
    assert lev_normal > 2.5
    # Near kill switch -> leverage steps down
    lev_stressed = om.calculate_dynamic_leverage(base_leverage=3.0, current_drawdown=0.12, kill_dd=0.15)
    assert lev_stressed <= 1.0

def test_autonomous_quant_operating_pipeline():
    import numpy as np
    from core.strategy_universe import load_market_dataset_polars
    from core.quant_gates_and_pipeline import (
        AutonomousQuantOperatingPipeline,
        PipelineDeploymentPackage,
    )

    # 1,000 bars synthetic data
    np.random.seed(42)
    n = 1000
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0004, 0.007, n)))
    df = load_market_dataset_polars({"close": prices})

    pipeline = AutonomousQuantOperatingPipeline(emit_telemetry=False)
    package = pipeline.run_pipeline(df, symbols=["EURUSD"])

    assert isinstance(package, PipelineDeploymentPackage)
    assert package.execution_authority is False
    assert package.total_candidates >= 15
    assert len(package.ranked_finalists) > 0
    # Top finalist must have highest composite score
    if len(package.ranked_finalists) > 1:
        assert package.ranked_finalists[0]["composite_score"] >= package.ranked_finalists[1]["composite_score"]

def test_pipeline_telemetry_event_dispatch():
    from unittest.mock import MagicMock
    import core.agent_event_bus as aeb
    from core.strategy_universe import load_market_dataset_polars
    from core.quant_gates_and_pipeline import AutonomousQuantOperatingPipeline

    mock_emit = MagicMock()
    orig_emit = getattr(aeb, "emit_agent_event", None)
    try:
        aeb.emit_agent_event = mock_emit
        pipeline = AutonomousQuantOperatingPipeline(emit_telemetry=True)
        df = load_market_dataset_polars({"close": [100.0 + i * 0.1 for i in range(200)]})
        pkg = pipeline.run_pipeline(df, symbols=["EURUSD"])
        assert pkg.total_candidates > 0
        assert mock_emit.call_count >= 1
        event_types = [call.kwargs.get("event_type") for call in mock_emit.call_args_list]
        assert "DEPLOYMENT_PIPELINE_COMPLETED" in event_types
    finally:
        if orig_emit is not None:
            aeb.emit_agent_event = orig_emit
        elif hasattr(aeb, "emit_agent_event"):
            delattr(aeb, "emit_agent_event")


