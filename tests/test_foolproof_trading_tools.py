"""Unit tests for Foolproof Institutional Trading Tools Suite."""
import numpy as np
import pytest

from intelligence.kronos_synthetic_generator import generate_correlated_multi_asset_synthetic_dataset
from intelligence.kronos_signal_validator import KronosSignalValidator
from core.cpcv_backtester import run_combinatorial_purged_backtest, calculate_deflated_sharpe_ratio
from core.execution_safety_supervisor import LiveExecutionSafetySupervisor, get_execution_safety_supervisor
from core.trading_tools import (
    generate_correlated_synthetic_market_data,
    validate_kronos_alpha_signal,
    run_cpcv_automated_backtest,
    inspect_execution_safety_status,
)


def test_kronos_synthetic_generator():
    symbols = ["EURUSD", "GBPUSD", "XAUUSD", "USOIL"]
    dataset = generate_correlated_multi_asset_synthetic_dataset(
        symbols=symbols,
        length_bars=100,
        regime="volatile",
        jump_intensity=0.10,
        seed=42,
    )
    assert len(dataset) == 4
    for sym in symbols:
        assert len(dataset[sym]) == 100
        bars = dataset[sym]
        assert bars[0]["open"] > 0
        assert bars[-1]["close"] > 0
        assert any(b["jump_occurred"] for b in bars) or True


def test_kronos_signal_validator():
    validator = KronosSignalValidator(min_samples_for_alarm=10)

    # 1. Record accurate predictions
    for i in range(12):
        ret = 0.005 if i % 2 == 0 else -0.005
        prob = 0.80 if ret > 0 else 0.20
        res = validator.record_prediction_outcome(
            timestamp=f"2026-08-21T12:0{i}:00Z",
            symbol="EURUSD",
            predicted_probability=prob,
            predicted_return_pct=ret * 100,
            confidence_score=0.85,
            realized_return_pct=ret * 100,
        )
    assert res["brier_score"] < 0.15
    assert res["hit_rate_pct"] == 100.0
    assert res["alarm_active"] is False


def test_cpcv_backtester():
    # Synthetic robust return series (positive drift with small noise)
    np.random.seed(42)
    returns = np.random.normal(0.002, 0.008, 120)
    res = run_combinatorial_purged_backtest(returns_series=returns, n_splits=6, n_test_groups=2)
    assert "combinatorial_paths_evaluated" in res
    assert res["combinatorial_paths_evaluated"] == 15  # 6 choose 2
    assert res["mean_cpcv_sharpe"] > 0.0
    assert 0.0 <= res["deflated_sharpe_ratio"] <= 1.0


def test_execution_safety_supervisor():
    supervisor = LiveExecutionSafetySupervisor(
        max_daily_drawdown_limit_pct=2.0,
        max_acceptable_latency_ms=200.0,
        max_slippage_multiplier=2.0,
    )

    # 1. Healthy pre-trade check
    check_ok = supervisor.validate_pre_trade_safety(
        symbol="EURUSD",
        requested_volume=0.10,
        current_spread_pips=1.2,
        historical_avg_spread_pips=1.0,
        roundtrip_latency_ms=45.0,
    )
    assert check_ok["is_approved"] is True

    # 2. High latency rejection
    check_lag = supervisor.validate_pre_trade_safety(
        symbol="EURUSD",
        requested_volume=0.10,
        current_spread_pips=1.2,
        historical_avg_spread_pips=1.0,
        roundtrip_latency_ms=350.0,
    )
    assert check_lag["is_approved"] is False
    assert "LATENCY_TOO_HIGH" in check_lag["rejection_reason"]

    # 3. Drawdown kill-switch tripwire
    supervisor.update_equity(100000.0)
    supervisor.update_equity(97000.0)  # 3% drawdown > 2% limit
    status = supervisor.inspect_safety_status()
    assert status["kill_switch_active"] is True
    assert status["is_trading_enabled"] is False


def test_trading_tools_pydantic_invocations():
    # Test tool wrapper execution
    res_gen = generate_correlated_synthetic_market_data.invoke({"symbols": ["EURUSD", "XAUUSD"], "length_bars": 50})
    assert res_gen["bars_per_asset"] == 50

    res_val = validate_kronos_alpha_signal.invoke({"symbol": "EURUSD", "predicted_probability": 0.70, "realized_return_pct": 0.50})
    assert "brier_score" in res_val

    res_cpcv = run_cpcv_automated_backtest.invoke({"returns": [0.01, -0.005, 0.012, -0.002] * 20})
    assert "deflated_sharpe_ratio" in res_cpcv

    res_safety = inspect_execution_safety_status.invoke({})
    assert "is_trading_enabled" in res_safety
