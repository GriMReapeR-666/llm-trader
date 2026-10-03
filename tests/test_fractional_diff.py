"""Unit Test Suite for Module 02: Fractional Differentiation & Inefficiency Discovery Engine."""
import math
import numpy as np
import pandas as pd
import pytest

from core.fractional_diff import (
    compute_binomial_fractional_weights,
    apply_fractional_differentiation,
    estimate_augmented_dickey_fuller_stationarity,
    optimize_stationarity_memory_order_d,
    estimate_ornstein_uhlenbeck_half_life,
    calculate_bouchaud_square_root_capacity,
    # Aliases
    get_fractional_weights,
    fractional_diff,
    adf_stationarity_score,
    find_optimal_d,
)


def test_binomial_fractional_weights():
    # d = 0.50 binomial expansion weights
    weights = compute_binomial_fractional_weights(order_d=0.50, window_size=10, convergence_threshold=1e-4)
    assert len(weights) > 0
    # Last weight should be 1.0 (w_0)
    assert pytest.approx(weights[-1], 1e-6) == 1.0
    # w_1 = -d = -0.50
    assert pytest.approx(weights[-2], 1e-6) == -0.50


def test_fractional_differentiation_memory_and_stationarity():
    np.random.seed(42)
    # Random walk price series I(1)
    prices = 100.0 + np.cumsum(np.random.normal(0, 1, 200))
    s = pd.Series(prices)

    # d = 0.0 -> identical series
    fd_0 = apply_fractional_differentiation(s, order_d=0.0)
    assert len(fd_0) == len(s)

    # d = 0.40 -> fractional differentiation preserves high correlation (>0.60)
    fd_04 = apply_fractional_differentiation(s, order_d=0.40, max_window=30)
    assert len(fd_04) > 100
    corr = float(s.iloc[-len(fd_04):].corr(fd_04))
    assert corr > 0.60



def test_optimize_stationarity_memory_order_d():
    np.random.seed(42)
    prices = 100.0 + np.cumsum(np.random.normal(0, 1, 150))
    res = optimize_stationarity_memory_order_d(prices, d_search_range=(0.1, 1.0), step_size=0.10)
    assert "optimal_d" in res
    assert 0.0 < res["optimal_d"] <= 1.0
    assert len(res["evaluations"]) > 0


def test_estimate_ornstein_uhlenbeck_half_life():
    # Mean-reverting AR(1) process: e_t = 0.7 * e_{t-1} + noise
    # theta = 0.7 - 1.0 = -0.30 -> half_life = -ln(2) / -0.30 ~ 2.31 bars
    np.random.seed(42)
    e = np.zeros(200)
    for t in range(1, 200):
        e[t] = 0.7 * e[t - 1] + np.random.normal(0, 0.2)

    hl = estimate_ornstein_uhlenbeck_half_life(e)
    assert 1.5 <= hl <= 4.0


def test_calculate_bouchaud_square_root_capacity():
    # 2% daily vol, order 10,000 shares, ADV 1,000,000 shares -> Participation = 1%
    # Sqrt(0.01) = 0.10 -> Impact = 1.0 * 0.02 * 0.10 = 0.0020 (20 bps)
    impact = calculate_bouchaud_square_root_capacity(
        daily_volatility=0.02,
        order_size_shares=10000.0,
        average_daily_volume_shares=1000000.0,
        impact_constant_y=1.0,
    )
    assert pytest.approx(impact, 1e-6) == 0.0020
