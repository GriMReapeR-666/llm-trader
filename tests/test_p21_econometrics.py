"""Unit tests for P21: Econometric Modelling and Algorithmic Trading of Bitcoin (2026).

Validates:
1. Augmented Dickey-Fuller (ADF) stationarity testing on prices vs returns.
2. GARCH(1,1) conditional volatility estimation and parameter persistence (alpha + beta < 1).
3. Multi-step forward volatility forecasting towards unconditional variance.
4. Volatility clustering detection (autocorrelation of squared returns, excess kurtosis).
5. Robust failsafes on short, degenerate, or zero-variance return series.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.econometric_features import (
    ADFTestResult,
    GARCHResult,
    calculate_adf_test,
    detect_volatility_clustering,
    fit_garch11,
    forecast_garch_volatility,
)


def test_adf_stationarity_returns_vs_prices():
    """Verify ADF test correctly identifies non-stationary prices (unit root)
    and stationary log-returns (no unit root).
    """
    rng = np.random.default_rng(42)
    # Stationary Gaussian white noise returns
    returns = rng.normal(0.0005, 0.015, size=500)
    # Non-stationary cumulative price process (random walk)
    prices = 50000.0 * np.exp(np.cumsum(returns))

    # Price test: Fail to reject null of unit root -> non-stationary
    res_price = calculate_adf_test(prices)
    assert res_price.is_stationary is False
    assert res_price.p_value > 0.10

    # Returns test: Reject null of unit root -> stationary
    res_ret = calculate_adf_test(returns)
    assert res_ret.is_stationary is True
    assert res_ret.p_value < 0.01
    assert res_ret.test_statistic < res_ret.critical_values["1%"]


def test_garch11_estimation_and_persistence():
    """Verify GARCH(1,1) fitting accurately captures volatility persistence (alpha + beta)."""
    rng = np.random.default_rng(42)
    n = 1500
    omega_true = 1e-5
    alpha_true = 0.10
    beta_true = 0.85  # persistence = 0.95
    sigma2 = np.zeros(n)
    returns = np.zeros(n)
    sigma2[0] = omega_true / (1.0 - alpha_true - beta_true)

    for t in range(1, n):
        sigma2[t] = omega_true + alpha_true * (returns[t - 1] ** 2) + beta_true * sigma2[t - 1]
        returns[t] = rng.normal(0.0, np.sqrt(sigma2[t]))

    res = fit_garch11(returns)
    assert res.is_covariance_stationary is True
    assert res.persistence < 1.0
    assert res.persistence == pytest.approx(0.95, abs=0.08)
    assert res.alpha == pytest.approx(alpha_true, abs=0.06)
    assert res.beta == pytest.approx(beta_true, abs=0.08)
    assert res.unconditional_volatility > 0.0
    assert len(res.conditional_volatilities) == n


def test_garch_volatility_forecasting():
    """Verify multi-step forward volatility forecast mean-reverts toward unconditional volatility."""
    rng = np.random.default_rng(42)
    # Create volatile episode
    returns = rng.normal(0.0, 0.03, size=300)
    res = fit_garch11(returns)

    # 1-bar forecast vs 20-bar forecast
    vol_1 = forecast_garch_volatility(res, horizon_bars=1)
    vol_20 = forecast_garch_volatility(res, horizon_bars=20)

    assert vol_1 > 0.0
    assert vol_20 > 0.0
    # Over long horizons, volatility reverts closer to the unconditional mean
    diff_20 = abs(vol_20 - res.unconditional_volatility)
    diff_1 = abs(vol_1 - res.unconditional_volatility)
    assert diff_20 <= diff_1 + 1e-5


def test_volatility_clustering_detection():
    """Verify detection of Mandelbrot volatility clustering and stylized heavy tails."""
    rng = np.random.default_rng(42)
    # Create clustered volatility regime (alternating low and high vol regimes)
    vols = np.repeat([0.005, 0.03, 0.008, 0.035], 100)
    returns = rng.normal(0.0, vols)

    clustering = detect_volatility_clustering(returns, lags=5)
    assert clustering["has_volatility_clustering"] is True
    assert clustering["kurtosis"] > 0.0  # Leptokurtic (fat tails)
    assert clustering["squared_return_autocorr_lag1"] > 0.10


def test_garch_failsafe_on_flat_or_short_series():
    """Verify fail-safe behavior on short or zero-variance inputs."""
    # Short series (< 20 elements)
    short_ret = np.array([0.01, -0.01, 0.02])
    res_short = fit_garch11(short_ret)
    assert res_short.is_covariance_stationary is True
    assert res_short.persistence == 0.90  # Default robust prior

    # Flat series (zeros)
    flat_ret = np.zeros(100)
    res_flat = fit_garch11(flat_ret)
    assert res_flat.unconditional_volatility == pytest.approx(0.0)
