"""Tests for research/full_pipeline_backtest.py — full pipeline backtest engine."""
from __future__ import annotations

import math
import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trending_bars(n=500, trend=0.0003, vol=0.0008, seed=42):
    """Generate synthetic M15 bars with a trend."""
    np.random.seed(seed)
    price = 1.1000
    bars = []
    for i in range(n):
        change = np.random.normal(trend, vol)
        o = price
        c = price * (1 + change)
        h = max(o, c) * (1 + abs(np.random.normal(0, vol * 0.3)))
        l = min(o, c) * (1 - abs(np.random.normal(0, vol * 0.3)))
        bars.append({
            "open": round(o, 5), "high": round(h, 5),
            "low": round(l, 5), "close": round(c, 5),
            "volume": int(np.random.exponential(1000)),
        })
        price = c
    return bars


# ---------------------------------------------------------------------------
# Indicator tests
# ---------------------------------------------------------------------------

class TestIndicators:
    def test_hma_reduces_lag(self):
        from research.full_pipeline_backtest import _hma
        # Step function: price jumps from 100 to 110 at bar 50
        data = np.concatenate([np.full(100, 100.0), np.full(100, 110.0)])
        hma = _hma(data, 21)
        # HMA should eventually reach 110
        assert not np.isnan(hma[-1])
        assert hma[-1] > 105  # close to new level after 100 bars
        # HMA should be faster than a 50-period EMA
        from research.full_pipeline_backtest import _ema
        ema = _ema(data, 50)
        assert hma[-1] >= ema[-1] - 1.0  # HMA should be at least as responsive

    def test_ema_converges(self):
        from research.full_pipeline_backtest import _ema
        data = np.full(100, 100.0)
        ema = _ema(data, 20)
        assert np.all(np.abs(ema[19:] - 100.0) < 0.01)

    def test_rsi_bounds(self):
        from research.full_pipeline_backtest import _rsi
        bars = _make_trending_bars(200)
        closes = np.array([b["close"] for b in bars])
        rsi = _rsi(closes, 14)
        valid = rsi[~np.isnan(rsi)]
        assert all(0 <= v <= 100 for v in valid)

    def test_adx_non_nan_after_warmup(self):
        from research.full_pipeline_backtest import _adx
        bars = _make_trending_bars(200)
        h = np.array([b["high"] for b in bars])
        l = np.array([b["low"] for b in bars])
        c = np.array([b["close"] for b in bars])
        adx = _adx(h, l, c, 14)
        # After warmup, ADX should have values
        assert not np.isnan(adx[60])
        assert adx[60] > 0

    def test_atr_positive(self):
        from research.full_pipeline_backtest import _atr
        bars = _make_trending_bars(100)
        h = np.array([b["high"] for b in bars])
        l = np.array([b["low"] for b in bars])
        c = np.array([b["close"] for b in bars])
        atr = _atr(h, l, c, 14)
        valid = atr[~np.isnan(atr)]
        assert all(v > 0 for v in valid)


# ---------------------------------------------------------------------------
# Signal tests
# ---------------------------------------------------------------------------

class TestDeterministicSignal:
    def test_buy_on_bullish_confluence(self):
        from research.full_pipeline_backtest import deterministic_signal
        sig = deterministic_signal(
            {"close": 1.1050, "hma_21": 1.1020, "ema_50": 1.1000,
             "adx_14": 30, "atr_14": 0.0015, "rsi_14": 60},
            alpha_composite=1.0,
        )
        assert sig["action"] == "BUY"
        assert sig["confidence"] > 0
        assert sig["entry"] > sig["stop_loss"]
        assert sig["take_profit"] > sig["entry"]

    def test_sell_on_bearish_confluence(self):
        from research.full_pipeline_backtest import deterministic_signal
        sig = deterministic_signal(
            {"close": 1.0950, "hma_21": 1.0980, "ema_50": 1.1000,
             "adx_14": 30, "atr_14": 0.0015, "rsi_14": 40},
            alpha_composite=-1.0,
        )
        assert sig["action"] == "SELL"
        assert sig["stop_loss"] > sig["entry"]
        assert sig["take_profit"] < sig["entry"]

    def test_hold_on_insufficient_confluence(self):
        from research.full_pipeline_backtest import deterministic_signal
        sig = deterministic_signal(
            {"close": 1.1000, "hma_21": 1.1001, "ema_50": 1.0999,
             "adx_14": 15, "atr_14": 0.0015, "rsi_14": 50},
            alpha_composite=0.1,
        )
        assert sig["action"] == "HOLD"

    def test_hold_on_zero_data(self):
        from research.full_pipeline_backtest import deterministic_signal
        sig = deterministic_signal(
            {"close": 0, "hma_21": 0, "ema_50": 0,
             "adx_14": 0, "atr_14": 0, "rsi_14": 50},
        )
        assert sig["action"] == "HOLD"

    def test_hold_when_regime_not_tradable(self):
        from research.full_pipeline_backtest import deterministic_signal
        sig = deterministic_signal(
            {"close": 1.1050, "hma_21": 1.1020, "ema_50": 1.1000,
             "adx_14": 30, "atr_14": 0.0015, "rsi_14": 60},
            alpha_composite=1.0,
            regime_tradable=False,
        )
        assert sig["action"] == "HOLD"


# ---------------------------------------------------------------------------
# Backtest engine tests
# ---------------------------------------------------------------------------

class TestFullPipelineBacktest:
    def test_basic_backtest_runs(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500)
        result = run_full_pipeline_backtest(bars)
        assert result.bars_processed == 500
        assert result.initial_equity == 10000
        assert result.equity_curve[0] == 10000

    def test_produces_trades_on_trending_data(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(1000, trend=0.0005, vol=0.0006)
        result = run_full_pipeline_backtest(bars, name="trend_test")
        assert result.total_trades > 0, "Should produce trades on trending data"
        assert result.signals_generated > 0

    def test_gates_reject_low_rr(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500)
        result = run_full_pipeline_backtest(bars, min_rr=3.0)
        # With higher R:R requirement, fewer signals pass
        assert result.signals_rejected_gates > 0

    def test_gates_can_be_disabled(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500, trend=0.001, vol=0.0003)
        r1 = run_full_pipeline_backtest(bars, enable_gates=True)
        r2 = run_full_pipeline_backtest(bars, enable_gates=False)
        # Without gates, more trades should pass
        assert r2.signals_passed_gates >= r1.signals_passed_gates

    def test_costs_are_subtracted(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500, trend=0.001, vol=0.0003)
        result = run_full_pipeline_backtest(bars, spread_pips=5.0, slippage_pips=2.0)
        if result.total_trades > 0:
            assert result.total_spread_cost > 0
            assert result.total_slippage_cost > 0
            assert result.total_commission > 0

    def test_risk_per_trade_controls_sizing(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500, trend=0.001, vol=0.0003)
        r1 = run_full_pipeline_backtest(bars, risk_per_trade_pct=0.01)
        r2 = run_full_pipeline_backtest(bars, risk_per_trade_pct=0.02)
        # Higher risk should produce larger PnL swings
        if r1.total_trades > 0 and r2.total_trades > 0:
            assert abs(r2.max_drawdown_pct) >= abs(r1.max_drawdown_pct) * 0.5

    def test_sharpe_computed(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(1000, trend=0.0003)
        result = run_full_pipeline_backtest(bars)
        assert isinstance(result.sharpe_ratio, float)

    def test_win_rate_is_percentage(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500, trend=0.001, vol=0.0003)
        result = run_full_pipeline_backtest(bars)
        if result.total_trades > 0:
            assert 0 <= result.win_rate_pct <= 100

    def test_to_dict_serializable(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(300)
        result = run_full_pipeline_backtest(bars)
        d = result.to_dict()
        assert isinstance(d, dict)
        assert "sharpe_ratio" in d
        assert "max_drawdown_pct" in d

    def test_short_bars_returns_empty(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(10)
        result = run_full_pipeline_backtest(bars)
        assert result.total_trades == 0
        assert result.equity_curve == []  # early return, no equity curve

    def test_equity_never_negative(self):
        """Equity should not go below zero (risk per trade limits loss)."""
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500, trend=-0.001, vol=0.001)
        result = run_full_pipeline_backtest(bars, risk_per_trade_pct=0.01)
        assert min(result.equity_curve) > 0, f"Equity went negative: {min(result.equity_curve)}"

    def test_no_lookahead_in_indicators(self):
        """Indicators at bar i should not use data from bar i+1."""
        from research.full_pipeline_backtest import _hma, _ema
        data = np.array([1.0] * 50 + [2.0] * 50)
        hma = _hma(data, 21)
        ema = _ema(data, 20)
        # At bar 50 (first bar after jump), HMA/EMA should still be near 1.0
        assert hma[50] < 1.5, f"HMA at bar 50 reacted too fast: {hma[50]}"
        assert ema[50] < 1.3, f"EMA at bar 50 reacted too fast: {ema[50]}"


# ---------------------------------------------------------------------------
# Provenance: a generic ruleset must not read as a per-strategy result
# ---------------------------------------------------------------------------

def test_full_pipeline_backtest_provenance_labels_generic_ruleset():
    """`name` is a label; results must carry the ruleset that produced them.

    Every registry strategy is currently scored by the same
    deterministic_signal() ruleset, so without provenance the identical numbers
    read as if they described that specific strategy.
    """
    from research.full_pipeline_backtest import run_full_pipeline_backtest

    bars = _make_trending_bars()
    r = run_full_pipeline_backtest(
        bars=bars, symbol="EURUSD", timeframe="M15",
        name="alpha_055_orderflow", enable_dsr=False)
    d = r.to_dict()

    assert d["name"] == "alpha_055_orderflow"
    assert d["ruleset"] == "deterministic_signal"
    assert d["strategy_implemented"] is False


def test_full_pipeline_backtest_same_ruleset_gives_same_metrics():
    """Different strategy labels must not change the numbers they report."""
    from research.full_pipeline_backtest import run_full_pipeline_backtest

    bars = _make_trending_bars()
    a = run_full_pipeline_backtest(bars=bars, name="alpha_055_orderflow",
                                   enable_dsr=False).to_dict()
    b = run_full_pipeline_backtest(bars=bars, name="kronos_h1",
                                   enable_dsr=False).to_dict()

    assert a["ruleset"] == b["ruleset"]
    assert a["sharpe_ratio"] == b["sharpe_ratio"]
    assert a["profit_factor"] == b["profit_factor"]
    assert a["total_trades"] == b["total_trades"]
