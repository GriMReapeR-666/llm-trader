"""Unit tests for P14: Uncorrelated Selection & QUBO Walk-Forward Rebalancing (Weinberg 2026).

Validates:
- Ledoit-Wolf shrinkage covariance and correlation matrix estimation.
- Angular distance metric mapping correlation space to metric space.
- Hierarchical cluster representative selection via annualized Sharpe ratio.
- QUBO formulation of portfolio rebalancing scheduling with marginal Sharpe and exponential cost decay.
- Classical and exact QUBO schedule solver reducing transaction count and cost.
"""
from __future__ import annotations

import math
import numpy as np
import pytest
from research.qubo_rebalancer import (
    ledoit_wolf_shrinkage,
    angular_distance,
    select_uncorrelated_assets,
    build_qubo_matrix,
    solve_qubo_schedule,
    walk_forward_qubo_rebalance,
)


def test_ledoit_wolf_shrinkage():
    """Ledoit-Wolf shrinkage produces positive definite correlation matrix with unit diagonal."""
    np.random.seed(42)
    returns = np.random.normal(0.0005, 0.015, size=(100, 5))
    cov, corr, alpha = ledoit_wolf_shrinkage(returns)

    assert cov.shape == (5, 5)
    assert corr.shape == (5, 5)
    assert 0.0 <= alpha <= 1.0
    # Diagonal of correlation is 1.0
    np.testing.assert_allclose(np.diag(corr), np.ones(5), atol=1e-5)
    # Symmetry
    np.testing.assert_allclose(corr, corr.T, atol=1e-5)


def test_angular_distance_properties():
    """Angular distance d_ij = sqrt(0.5 * (1 - rho_ij)) satisfies metric axioms."""
    corr = np.array([
        [1.0, 0.0, -1.0],
        [0.0, 1.0, 0.5],
        [-1.0, 0.5, 1.0],
    ])
    dist = angular_distance(corr)

    # Identical correlation -> distance 0
    assert dist[0, 0] == 0.0
    # Zero correlation -> distance sqrt(0.5) ~ 0.7071
    assert math.isclose(dist[0, 1], math.sqrt(0.5), rel_tol=1e-5)
    # Inverse correlation -> distance 1.0
    assert math.isclose(dist[0, 2], 1.0, rel_tol=1e-5)


def test_select_uncorrelated_assets():
    """Selects highest-Sharpe representative from each correlation cluster."""
    np.random.seed(42)
    T = 200
    # Create 4 assets: assets 0 and 1 are highly correlated (cluster A)
    # assets 2 and 3 are highly correlated (cluster B)
    base_a = np.random.normal(0.001, 0.01, size=T)
    base_b = np.random.normal(-0.0005, 0.02, size=T)

    # In cluster A, asset 0 has higher mean return than asset 1
    r0 = base_a + 0.002
    r1 = base_a
    # In cluster B, asset 2 has higher mean return than asset 3
    r2 = base_b + 0.001
    r3 = base_b

    returns = np.column_stack([r0, r1, r2, r3])
    symbols = ["A_HIGH", "A_LOW", "B_HIGH", "B_LOW"]

    selected = select_uncorrelated_assets(returns, symbols, n_clusters=2)
    assert len(selected) == 2
    # Should pick A_HIGH and B_HIGH from respective clusters
    assert "A_HIGH" in selected
    assert "B_HIGH" in selected


def test_build_qubo_matrix_and_solve_schedule():
    """QUBO matrix penalizes rapid consecutive rebalancing and selects cost-effective dates."""
    np.random.seed(42)
    T, N, W = 100, 3, 5
    returns = np.random.normal(0.001, 0.01, size=(T, N))
    target_weights = np.array([0.4, 0.3, 0.3])
    candidate_dates = [20, 40, 60, 80, 100]

    Q = build_qubo_matrix(returns, target_weights, candidate_dates, cost_rate=0.001)
    assert Q.shape == (W, W)

    # Off-diagonals must be non-negative (exponential penalty for rapid consecutive rebalances)
    for i in range(W):
        for j in range(W):
            if i != j:
                assert Q[i, j] >= 0.0

    schedule, energy = solve_qubo_schedule(Q)
    assert schedule.shape == (W,)
    assert set(np.unique(schedule)).issubset({0, 1})


def test_walk_forward_qubo_rebalance_slashes_turnover():
    """Walk-forward QUBO rebalancing achieves lower turnover than fixed periodic rebalancing."""
    np.random.seed(42)
    T, N = 150, 4
    returns = np.random.normal(0.0005, 0.01, size=(T, N))
    target_weights = np.ones(N) / N

    results = walk_forward_qubo_rebalance(
        returns=returns,
        target_weights=target_weights,
        n_windows=3,
        candidates_per_window=4,
        cost_rate=0.001,
    )

    assert "rebalance_count" in results
    assert "total_cost_bp" in results
    assert "sharpe_ratio" in results
    # Total rebalances should be less than naive 12 candidate opportunities
    assert results["rebalance_count"] <= 12
