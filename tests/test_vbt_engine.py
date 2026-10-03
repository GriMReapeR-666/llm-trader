"""Unit tests for the VectorBT-compatible vectorized backtest engine."""
import pytest
import pandas as pd
import numpy as np
from research.vbt_engine import VectorizedBacktestEngine, BacktestMetrics


def test_vbt_engine_synthetic_crossover():
    # Create synthetic trending price series (100 bars)
    np.random.seed(42)
    t = np.linspace(0, 10, 100)
    prices = 100.0 + np.sin(t) * 5.0 + np.linspace(0, 10, 100)
    
    df = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=100, freq="15min"),
        "open": prices,
        "high": prices + 0.5,
        "low": prices - 0.5,
        "close": prices,
        "volume": np.random.randint(100, 1000, 100),
    })

    engine = VectorizedBacktestEngine(
        initial_cash=10_000.0,
        commission_pct=0.0001,
        annual_risk_free_rate=0.04,
    )

    metrics = engine.run_crossover_strategy(
        df=df,
        fast_period=5,
        slow_period=15,
        symbol="EURUSD",
        timeframe="M15",
    )

    assert isinstance(metrics, BacktestMetrics)
    assert metrics.symbol == "EURUSD"
    assert metrics.timeframe == "M15"
    assert len(metrics.equity_series) == 100
    assert len(metrics.drawdown_series) == 100
    assert metrics.total_trades >= 1
    assert metrics.initial_cash == 10_000.0
    assert metrics.final_cash > 0.0

    # Test dictionary export
    metrics_dict = metrics.to_dict()
    assert "sharpe_ratio" in metrics_dict
    assert "sortino_ratio" in metrics_dict
    assert "max_drawdown_pct" in metrics_dict
    assert "win_rate_pct" in metrics_dict


def test_vbt_engine_explicit_signals():
    df = pd.DataFrame({
        "close": [100.0, 102.0, 105.0, 103.0, 99.0, 98.0, 101.0, 104.0, 106.0, 105.0]
    })

    entries = [True, False, False, False, False, True, False, False, False, False]
    exits =   [False, False, True, False, False, False, False, True, False, False]

    engine = VectorizedBacktestEngine(initial_cash=1000.0, commission_pct=0.0)
    metrics = engine.run_backtest(
        df=df,
        entries=entries,
        exits=exits,
        symbol="BTCUSD",
        timeframe="H1",
    )

    assert metrics.total_trades == 2
    assert metrics.winning_trades == 2
    assert metrics.win_rate_pct == 100.0
    assert metrics.final_cash > metrics.initial_cash
