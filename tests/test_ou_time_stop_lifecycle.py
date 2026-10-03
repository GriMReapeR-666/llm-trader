"""Tests for Ornstein-Uhlenbeck dynamic time stop position lifecycle."""
import numpy as np
import pytest

from core.ou_time_stop import (
    OUTimeStopEngine,
    OUTimeStopResult,
    evaluate_scalp_time_stop,
)


def test_evaluate_scalp_time_stop_closes_stagnant_position():
    """Position held past 2.5 * half-life triggers TIME_STOP_CLOSE."""
    np.random.seed(42)
    # Generate an AR(1) mean-reverting series around 100 with theta ~ -0.15 (half-life ~ 4.6 bars)
    prices = [100.0]
    for _ in range(60):
        prev = prices[-1]
        next_p = prev - 0.15 * (prev - 100.0) + np.random.normal(0, 0.5)
        prices.append(next_p)

    # If held for 30 bars (>> 2.5 * half_life), must trigger TIME_STOP_CLOSE
    res = evaluate_scalp_time_stop(prices, bars_held=30, multiplier=2.5)
    assert isinstance(res, OUTimeStopResult)
    assert res.is_stagnant is True
    assert res.recommended_action == "TIME_STOP_CLOSE"
    assert res.time_stop_bars < 30


def test_evaluate_scalp_time_stop_holds_active_position():
    """Position held within half-life window continues to HOLD."""
    np.random.seed(42)
    prices = [100.0]
    for _ in range(60):
        prev = prices[-1]
        next_p = prev - 0.15 * (prev - 100.0) + np.random.normal(0, 0.5)
        prices.append(next_p)

    # If held for only 3 bars (well within 2.5 * half_life), must HOLD
    res = evaluate_scalp_time_stop(prices, bars_held=3, multiplier=2.5)
    assert res.is_stagnant is False
    assert res.recommended_action == "HOLD"


def test_evaluate_scalp_time_stop_fallback_short_history():
    """Gracefully handles short price history (< 20 bars)."""
    res = evaluate_scalp_time_stop([100.0, 101.0, 100.5], bars_held=5)
    assert isinstance(res, OUTimeStopResult)
    assert res.recommended_action == "HOLD"
