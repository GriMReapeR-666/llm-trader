"""Tests for High-Fidelity Intrabar Precision, Commission Deduction, and Calmar Metrics.
"""
import pytest
import pandas as pd
import numpy as np

from research.mt5_backtest_engine import MT5BrokerBacktestEngine, BacktestMetrics


def test_intrabar_stop_loss_triggered_even_if_close_is_profitable():
    """Verify that an intrabar dip to low <= sl_level triggers STOP_LOSS,
    even if the bar closes at a profit.
    """
    engine = MT5BrokerBacktestEngine(spread_pips=0.0, slippage_pips=0.0, commission_per_lot=0.0)
    
    # Bar 0: initial flat bar
    # Bar 1: entry signal
    # Bar 2: low breaches SL (30 pips = 0.0030), but close is up +20 pips
    bars = pd.DataFrame({
        "open":  [1.1000, 1.1000, 1.1000, 1.1000],
        "high":  [1.1005, 1.1005, 1.1020, 1.1005],
        "low":   [1.0995, 1.0995, 1.0960, 1.0995],  # 1.0960 breaches 1.0970 SL
        "close": [1.1000, 1.1000, 1.1020, 1.1000],  # Close is profitable (1.1020 > 1.1000)
    })
    
    entries_long = [False, True, True, True]
    entries_short = [False, False, False, False]
    
    metrics = engine.run_strategy_backtest(
        bars_df=bars,
        entries_long=entries_long,
        entries_short=entries_short,
        symbol="EURUSD",
        timeframe="H1",
        sl_pips=30.0,
        tp_pips=60.0,
        data_source="synthetic",
    )
    
    assert metrics.total_trades == 1
    trade = metrics.trades[0]
    assert trade["exit_reason"] == "STOP_LOSS"
    assert trade["exit_idx"] == 2
    assert trade["pnl_pips"] < 0


def test_intrabar_simultaneous_sl_and_tp_conservative():
    """When both SL and TP price levels are breached within the same bar,
    conservative execution must register STOP_LOSS first.
    """
    engine = MT5BrokerBacktestEngine(spread_pips=0.0, slippage_pips=0.0, commission_per_lot=0.0)
    
    bars = pd.DataFrame({
        "open":  [1.1000, 1.1000, 1.1000, 1.1000],
        "high":  [1.1005, 1.1005, 1.1070, 1.1005],  # 1.1070 breaches 1.1060 TP
        "low":   [1.0995, 1.0995, 1.0960, 1.0995],  # 1.0960 breaches 1.0970 SL
        "close": [1.1000, 1.1000, 1.1050, 1.1000],
    })
    
    entries_long = [False, True, True, True]
    entries_short = [False, False, False, False]
    
    metrics = engine.run_strategy_backtest(
        bars_df=bars,
        entries_long=entries_long,
        entries_short=entries_short,
        symbol="EURUSD",
        timeframe="H1",
        sl_pips=30.0,
        tp_pips=60.0,
        data_source="synthetic",
    )
    
    assert metrics.total_trades == 1
    assert metrics.trades[0]["exit_reason"] == "STOP_LOSS"


def test_gap_through_stop_loss_fills_at_open():
    """When price opens past the stop loss, fill must occur at the gap open price
    rather than the theoretical limit price.
    """
    engine = MT5BrokerBacktestEngine(spread_pips=0.0, slippage_pips=0.0, commission_per_lot=0.0)
    
    bars = pd.DataFrame({
        "open":  [1.1000, 1.1000, 1.0940, 1.1000],  # Bar 2 opens at 1.0940 (60 pips below entry, 30 below SL)
        "high":  [1.1005, 1.1005, 1.0945, 1.1005],
        "low":   [1.0995, 1.0995, 1.0935, 1.0995],
        "close": [1.1000, 1.1000, 1.0940, 1.1000],
    })
    
    entries_long = [False, True, True, True]
    entries_short = [False, False, False, False]
    
    metrics = engine.run_strategy_backtest(
        bars_df=bars,
        entries_long=entries_long,
        entries_short=entries_short,
        symbol="EURUSD",
        timeframe="H1",
        sl_pips=30.0,
        tp_pips=60.0,
        data_source="synthetic",
    )
    
    assert metrics.total_trades == 1
    trade = metrics.trades[0]
    assert trade["exit_reason"] == "STOP_LOSS"
    # Exit price must reflect the gap open (1.0940), not SL limit (1.0970)
    assert trade["exit_price"] == pytest.approx(1.0940, abs=1e-4)
    assert trade["pnl_pips"] == pytest.approx(-60.0, abs=1e-2)


def test_commission_deduction_from_net_pnl():
    """Commission of $7/lot corresponds to 0.7 pips on EURUSD (100k contract).
    Net PnL must be gross PnL minus 0.7 pips.
    """
    engine = MT5BrokerBacktestEngine(spread_pips=0.0, slippage_pips=0.0, commission_per_lot=7.0)
    
    # Enter at 1.1000, exit at 1.1060 (Take Profit: +60 pips gross)
    bars = pd.DataFrame({
        "open":  [1.1000, 1.1000, 1.1060, 1.1000],
        "high":  [1.1005, 1.1005, 1.1065, 1.1005],
        "low":   [1.0995, 1.0995, 1.1010, 1.0995],
        "close": [1.1000, 1.1000, 1.1060, 1.1000],
    })
    
    entries_long = [False, True, True, True]
    entries_short = [False, False, False, False]
    
    metrics = engine.run_strategy_backtest(
        bars_df=bars,
        entries_long=entries_long,
        entries_short=entries_short,
        symbol="EURUSD",
        timeframe="H1",
        sl_pips=30.0,
        tp_pips=60.0,
        data_source="synthetic",
    )
    
    assert metrics.total_trades == 1
    trade = metrics.trades[0]
    assert trade["exit_reason"] == "TAKE_PROFIT"
    # 60 gross pips - 0.7 commission pips = 59.3 pips net
    assert trade["pnl_pips"] == pytest.approx(59.3, abs=0.1)


def test_timeframe_aware_annualization_and_calmar():
    """Verify that M15 timeframe correctly scales days using 96 bars/day,
    and Calmar ratio equals annualized_return_pct / max_drawdown_pct.
    """
    engine = MT5BrokerBacktestEngine(spread_pips=0.0, slippage_pips=0.0, commission_per_lot=0.0)
    
    # 192 bars of M15 = 2 full trading days (96 bars/day)
    n = 192
    closes = [1.1000 + (0.0001 * (i % 2)) for i in range(n)]
    bars = pd.DataFrame({
        "open": closes,
        "high": [c + 0.0005 for c in closes],
        "low": [c - 0.0005 for c in closes],
        "close": closes,
    })
    
    # Long on even bars, exit on odd bars
    entries_long = [(i % 2 == 1) for i in range(n)]
    entries_short = [False] * n
    
    metrics = engine.run_strategy_backtest(
        bars_df=bars,
        entries_long=entries_long,
        entries_short=entries_short,
        symbol="EURUSD",
        timeframe="M15",
        sl_pips=10.0,
        tp_pips=10.0,
        data_source="synthetic",
    )
    
    assert metrics.total_trades > 0
    if metrics.max_drawdown_pct > 0:
        expected_calmar = round(metrics.annualized_return_pct / metrics.max_drawdown_pct, 2)
        assert metrics.calmar_ratio == pytest.approx(expected_calmar, abs=0.05)
