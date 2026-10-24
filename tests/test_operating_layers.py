import numpy as np
import polars as pl
import pytest

from core.operating_layers import (
    DataLayer,
    Layer1DataOutput,
    SignalLayer,
    Layer2SignalOutput,
    StrategyLayer,
    Layer3StrategyOutput,
    RiskLayer,
    Layer4RiskOutput,
    ExecutionLayer,
    Layer5ExecutionOutput,
    PortfolioLayer,
    Layer6PortfolioOutput,
    MonitoringLayer,
    Layer7MonitoringOutput,
    GovernanceLayer,
    Layer8GovernanceOutput,
    DecoupledOperatingPipeline,
    DecoupledOperatingReport,
)


def test_layer1_data_and_layer2_signal():
    np.random.seed(42)
    n = 200
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = pl.DataFrame({
        "close": prices,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "volume": np.full(n, 1000.0),
    })

    # Layer 1: Data
    data_layer = DataLayer()
    l1_out = data_layer.process(df)
    assert isinstance(l1_out, Layer1DataOutput)
    assert l1_out.is_clean is True
    assert l1_out.total_bars == n

    # Layer 2: Signal
    signal_layer = SignalLayer()
    l2_out = signal_layer.process(l1_out.clean_df)
    assert isinstance(l2_out, Layer2SignalOutput)
    assert l2_out.feature_count >= 5
    assert "ema_fast" in l2_out.signals_dict
    assert "rsi" in l2_out.signals_dict


def test_layer3_strategy_and_layer4_risk():
    np.random.seed(42)
    n = 200
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = pl.DataFrame({"close": prices, "high": prices * 1.002, "low": prices * 0.998, "volume": np.full(n, 1000.0)})

    # Layer 3: Strategy
    strategy_layer = StrategyLayer()
    l3_out = strategy_layer.process(symbols=["EURUSD"], timeframes=["H1"])
    assert isinstance(l3_out, Layer3StrategyOutput)
    assert l3_out.candidate_count >= 15

    # Layer 4: Risk Tiered Drawdown Defense
    risk_layer = RiskLayer()
    # 1. Normal state (DD = 4%)
    r1 = risk_layer.evaluate_drawdown(current_drawdown=0.04)
    assert r1.risk_tier == "NORMAL"
    assert r1.exposure_scale == 1.0
    assert r1.is_halted is False

    # 2. Tier 1 defense: Halve (DD = 11%)
    r2 = risk_layer.evaluate_drawdown(current_drawdown=0.11)
    assert r2.risk_tier == "DEFENSE_HALVE"
    assert r2.exposure_scale == 0.50
    assert r2.is_halted is False

    # 3. Tier 2 defense: Freeze (DD = 16%)
    r3 = risk_layer.evaluate_drawdown(current_drawdown=0.16)
    assert r3.risk_tier == "DEFENSE_FREEZE"
    assert r3.exposure_scale == 0.0
    assert r3.is_halted is False

    # 4. Tier 3 defense: Halt & Liquidate (DD = 21%)
    r4 = risk_layer.evaluate_drawdown(current_drawdown=0.21)
    assert r4.risk_tier == "EMERGENCY_HALT"
    assert r4.exposure_scale == 0.0
    assert r4.is_halted is True


def test_layer5_execution_and_layer6_portfolio():
    # Layer 5: Execution
    exec_layer = ExecutionLayer()
    l5_out = exec_layer.route_orders(symbol="EURUSD", target_exposure=0.5, current_exposure=0.0, price=1.1000)
    assert isinstance(l5_out, Layer5ExecutionOutput)
    assert l5_out.routed_orders_count >= 1
    assert l5_out.execution_algo in ["TWAP", "VWAP", "ICEBERG", "DIRECT"]
    assert l5_out.total_cost_bps > 0.0

    # Layer 6: Portfolio
    port_layer = PortfolioLayer()
    strat_weights = {"strat_1": 0.3, "strat_2": 0.4, "strat_3": 0.3}
    returns_matrix = np.random.normal(0, 0.01, size=(50, 3))
    l6_out = port_layer.audit_portfolio(strat_weights, returns_matrix)
    assert isinstance(l6_out, Layer6PortfolioOutput)
    assert l6_out.hhi_concentration <= 0.40
    assert l6_out.effective_bets >= 2.0


def test_layer7_monitoring_and_layer8_governance():
    # Layer 7: Monitoring
    mon_layer = MonitoringLayer(emit_telemetry=False)
    l7_out = mon_layer.record_cycle(n_trades=10, last_sharpe=1.2)
    assert isinstance(l7_out, Layer7MonitoringOutput)

    # Layer 8: Governance
    gov_layer = GovernanceLayer()
    l8_out = gov_layer.evaluate_governance(gate_pass_ratio=0.85, kill_switch_active=False)
    assert isinstance(l8_out, Layer8GovernanceOutput)
    assert l8_out.execution_authority is False
    assert l8_out.gate_verdict in ["APPROVED_FOR_SHADOW", "REJECTED", "GATE_FAILED"]


def test_decoupled_operating_pipeline_end_to_end():
    np.random.seed(42)
    n = 300
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = pl.DataFrame({"close": prices, "high": prices * 1.002, "low": prices * 0.998, "volume": np.full(n, 1000.0)})

    pipeline = DecoupledOperatingPipeline(emit_telemetry=True)
    report = pipeline.tick(df, symbol="EURUSD", timeframe="H1")

    assert isinstance(report, DecoupledOperatingReport)
    assert report.symbol == "EURUSD"
    assert report.timeframe == "H1"
    assert report.execution_authority is False

    # Check all 8 layers are cleanly instantiated and populated
    assert report.layer1_data.is_clean is True
    assert report.layer2_signal.feature_count >= 5
    assert report.layer3_strategy.candidate_count >= 15
    assert report.layer4_risk.risk_tier in ["NORMAL", "DEFENSE_HALVE", "DEFENSE_FREEZE", "EMERGENCY_HALT"]
    assert report.layer5_execution.total_cost_bps > 0.0
    assert report.layer6_portfolio.hhi_concentration > 0.0
    assert isinstance(report.layer7_monitoring.events_emitted, int)
    assert report.layer8_governance.execution_authority is False
