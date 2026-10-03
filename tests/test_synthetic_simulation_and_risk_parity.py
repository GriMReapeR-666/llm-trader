"""Tests for Synthetic Path Simulation, Regime Monte Carlo, and Risk Parity.

Covers:
- AR(1) model fitting and half-life calculation
- Vectorized 10,000-path synthetic ensemble generation
- Ensemble parameter optimization (Methods A and B)
- Regime-Switching Monte Carlo with Markov transition matrices
- Equal Risk Contribution (ERC) Risk Parity with turnover limits
"""
from __future__ import annotations

import numpy as np
import pytest

from research.synthetic_path_simulator import (
    fit_ar1_model,
    generate_synthetic_paths,
    default_mean_reversion_strategy,
    optimize_strategy_ensemble,
    AR1ModelFit,
)
from research.regime_monte_carlo import (
    RegimeSwitchingMonteCarlo,
    RegimeMonteCarloResult,
)
from research.risk_parity_engine import (
    RiskParityOptimizer,
    RiskParityResult,
)


def test_ar1_fitting_and_synthetic_generation():
    np.random.seed(42)
    # Generate synthetic mean-reverting series
    n = 300
    prices = np.zeros(n)
    prices[0] = 100.0
    for t in range(1, n):
        prices[t] = 100.0 + 0.85 * (prices[t - 1] - 100.0) + np.random.normal(0, 1.0)

    model = fit_ar1_model(prices)
    assert isinstance(model, AR1ModelFit)
    assert 0.0 < model.phi < 1.0
    assert model.is_mean_reverting is True
    assert model.half_life > 0.0

    # Generate 1000 synthetic paths of length 150
    paths = generate_synthetic_paths(model, num_paths=1000, path_length=150, initial_price=100.0)
    assert paths.shape == (1000, 150)
    assert np.all(paths > 0)


def test_ensemble_strategy_optimization():
    np.random.seed(42)
    prices = 100.0 + np.cumsum(np.random.normal(0, 0.5, 300))
    model = fit_ar1_model(prices)
    paths = generate_synthetic_paths(model, num_paths=200, path_length=100)

    param_grid = [
        {"window": 10, "threshold": 1.0},
        {"window": 20, "threshold": 1.5},
        {"window": 30, "threshold": 2.0},
    ]

    opt_res = optimize_strategy_ensemble(default_mean_reversion_strategy, param_grid, paths)
    assert opt_res["status"] == "SUCCESS"
    assert "method_a_optimal_params" in opt_res
    assert "method_b_optimal_params" in opt_res
    assert "sharpe_percentiles_method_a" in opt_res


def test_regime_switching_monte_carlo():
    mc = RegimeSwitchingMonteCarlo(seed=42)

    trades_by_regime = {
        "LOW_VOL": np.array([0.012, -0.006, 0.015, -0.008, 0.010]),
        "HIGH_VOL_STRESS": np.array([0.025, -0.022, 0.030, -0.028, -0.015]),
    }
    trans_matrix = np.array([[0.85, 0.15], [0.40, 0.60]])

    res = mc.run_simulation(trades_by_regime, trans_matrix, n_simulations=1000, n_trades=50)
    assert isinstance(res, RegimeMonteCarloResult)
    assert res.median_final_equity > 0.0
    assert -1.0 <= res.median_max_dd <= 0.0
    assert 0.0 <= res.prob_ruin <= 1.0


def test_risk_parity_optimizer():
    opt = RiskParityOptimizer()

    # 3-asset covariance matrix
    cov = np.array([
        [0.04, 0.01, 0.00],
        [0.01, 0.09, 0.02],
        [0.00, 0.02, 0.02],
    ])
    assets = ["EURUSD", "XAUUSD", "USOIL"]

    res = opt.solve(cov, asset_names=assets, max_turnover=0.40)
    assert isinstance(res, RiskParityResult)
    assert len(res.weights) == 3
    assert np.isclose(sum(res.weights), 1.0, atol=1e-4)
    assert res.portfolio_volatility > 0.0
    assert len(res.risk_contributions_pct) == 3
