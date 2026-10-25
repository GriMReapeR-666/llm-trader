"""Unit tests for core/math_auditor.py: Time-Machine test, finite domain, and accounting identities."""
import numpy as np
import pytest

from core.math_auditor import verify_strategy_math_and_causality


def test_clean_causal_strategy_passes():
    """A strictly backward-looking moving average cross must pass the time machine test."""
    np.random.seed(42)
    n = 200
    prices = 100.0 + np.cumsum(np.random.normal(0.05, 1.0, n))
    # Fake OHLCV: Open, High, Low, Close, Volume
    ohlcv = np.column_stack([prices, prices + 0.5, prices - 0.5, prices, np.ones(n) * 1000])

    def causal_signal_fn(bars):
        # 10-bar SMA crossover
        c = bars[:, 3]
        if len(c) < 10:
            return np.zeros(len(c))
        sma10 = np.convolve(c, np.ones(10)/10, mode='valid')
        sig = np.zeros(len(c))
        sig[9:] = np.where(c[9:] > sma10, 1.0, -1.0)
        return sig

    trades = [
        {"net_pnl": 15.0, "risk_dollars": 10.0, "r_multiple": 1.5},
        {"net_pnl": -10.0, "risk_dollars": 10.0, "r_multiple": -1.0},
        {"net_pnl": 20.0, "risk_dollars": 10.0, "r_multiple": 2.0},
    ]
    res = verify_strategy_math_and_causality(
        strategy_signal_fn=causal_signal_fn,
        ohlcv_bars=ohlcv,
        trades_ledger=trades,
        initial_equity=100.0,
        terminal_equity=125.0,
    )
    assert res["passed"] is True
    assert res["reason"] == "ALL_CAUSALITY_AND_MATH_INVARIANTS_VERIFIED"


def test_lookahead_signal_fails_time_machine():
    """A signal that peeks into the future must be caught by future corruption."""
    np.random.seed(42)
    n = 200
    prices = 100.0 + np.cumsum(np.random.normal(0.05, 1.0, n))
    ohlcv = np.column_stack([prices, prices + 0.5, prices - 0.5, prices, np.ones(n) * 1000])

    def lookahead_signal_fn(bars):
        # Future peek: signal at t uses close at min(t+1, len-1)
        c = bars[:, 3]
        sig = np.zeros(len(c))
        for t in range(len(c) - 1):
            sig[t] = 1.0 if c[t+1] > c[t] else -1.0
        return sig

    res = verify_strategy_math_and_causality(
        strategy_signal_fn=lookahead_signal_fn,
        ohlcv_bars=ohlcv,
        trades_ledger=[],
    )
    assert res["passed"] is False
    assert "LOOKAHEAD_BIAS_DETECTED" in res["reason"]


def test_nan_or_inf_signal_fails_finite_domain():
    """Signals emitting NaN or Inf must be rejected."""
    np.random.seed(42)
    n = 200
    ohlcv = np.ones((n, 5))

    def nan_signal_fn(bars):
        sig = np.zeros(len(bars))
        sig[50] = np.nan
        return sig

    res = verify_strategy_math_and_causality(
        strategy_signal_fn=nan_signal_fn,
        ohlcv_bars=ohlcv,
        trades_ledger=[],
    )
    assert res["passed"] is False
    assert "MATH_ERROR" in res["reason"]


def test_accounting_mismatch_fails():
    """Accounting discrepancies in net PnL vs equity delta must fail."""
    np.random.seed(42)
    n = 200
    ohlcv = np.ones((n, 5))
    causal_sig = lambda b: np.zeros(len(b))

    trades = [
        {"net_pnl": 10.0, "risk_dollars": 10.0, "r_multiple": 1.0},
    ]
    # Stated terminal equity 120.0 but trades only earned 10.0
    res = verify_strategy_math_and_causality(
        strategy_signal_fn=causal_sig,
        ohlcv_bars=ohlcv,
        trades_ledger=trades,
        initial_equity=100.0,
        terminal_equity=120.0,
    )
    assert res["passed"] is False
    assert "ACCOUNTING_MISMATCH" in res["reason"]
