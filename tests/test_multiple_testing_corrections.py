"""Tests for Advanced Multiple Testing & Snooping Corrections.

Covers:
- Benjamini-Hochberg False Discovery Rate (FDR) control
- Bonferroni Family-Wise Error Rate (FWER)
- Politis & Romano Stationary Bootstrap index generator
- White's Reality Check (White 2000)
- Hansen's Superior Predictive Ability (Hansen 2005)
- screen_candidate_setups_fdr orchestrator helper
- Exact canonical aliases
"""
from __future__ import annotations

import numpy as np
import pytest

from core.metrics import (
    calculate_benjamini_hochberg_fdr,
    calculate_bonferroni_fwer,
    calculate_data_snooping_reality_check_white,
    calculate_false_discovery_rate_benjamini_hochberg,
    calculate_hansens_spa,
    calculate_superior_predictive_ability_hansen,
    calculate_whites_reality_check,
    generate_stationary_bootstrap_indices,
    screen_candidate_setups_fdr,
)


def test_benjamini_hochberg_fdr_basic_case():
    # Known 10-hypothesis test case
    # p-values: 3 strong signals, 7 noise
    raw_p = [0.001, 0.008, 0.015, 0.12, 0.25, 0.40, 0.55, 0.70, 0.85, 0.95]
    q_vals, is_sig = calculate_benjamini_hochberg_fdr(raw_p, fdr_target=0.05)

    assert len(q_vals) == 10
    assert len(is_sig) == 10
    # First 3 should be discoveries at 5% FDR
    assert bool(is_sig[0]) is True
    assert bool(is_sig[1]) is True
    assert bool(is_sig[2]) is True
    # Noise should not be discoveries
    assert bool(is_sig[3]) is False
    assert bool(is_sig[9]) is False

    # Monotonicity check on sorted p-values
    sorted_order = np.argsort(raw_p)
    q_sorted = q_vals[sorted_order]
    for i in range(len(q_sorted) - 1):
        assert q_sorted[i] <= q_sorted[i + 1] + 1e-12


def test_benjamini_hochberg_all_noise_and_all_signals():
    # All noise: p-values > 0.30
    noise_p = [0.35, 0.45, 0.60, 0.75, 0.90]
    q_noise, sig_noise = calculate_benjamini_hochberg_fdr(noise_p, fdr_target=0.05)
    assert not np.any(sig_noise)
    assert np.all(q_noise >= 0.35)

    # All signals: very small p-values
    sig_p = [0.0001, 0.0002, 0.0005, 0.001]
    q_sig, sig_sig = calculate_benjamini_hochberg_fdr(sig_p, fdr_target=0.05)
    assert np.all(sig_sig)
    assert np.all(q_sig < 0.05)


def test_bonferroni_fwer_adjustment():
    raw_p = [0.01, 0.04, 0.20, 0.60]
    m = len(raw_p)
    adj_p = calculate_bonferroni_fwer(raw_p)

    assert len(adj_p) == m
    assert pytest.approx(adj_p[0], abs=1e-6) == 0.04  # 0.01 * 4
    assert pytest.approx(adj_p[1], abs=1e-6) == 0.16  # 0.04 * 4
    assert pytest.approx(adj_p[2], abs=1e-6) == 0.80  # 0.20 * 4
    assert pytest.approx(adj_p[3], abs=1e-6) == 1.00  # clamped at 1.0


def test_stationary_bootstrap_generator_properties():
    n_samples = 100
    n_boot = 50
    indices = generate_stationary_bootstrap_indices(
        n_samples=n_samples, mean_block_length=10.0, num_bootstraps=n_boot, seed=123
    )
    assert indices.shape == (n_boot, n_samples)
    assert np.all(indices >= 0)
    assert np.all(indices < n_samples)

    # Verify that blocks exists (consecutive indices mod N)
    block_runs = 0
    for b in range(n_boot):
        diffs = (indices[b, 1:] - indices[b, :-1]) % n_samples
        block_runs += np.sum(diffs == 1)
    # Expected proportion of consecutive steps is roughly 1 - 1/mean_block = 0.90
    observed_rate = block_runs / (n_boot * (n_samples - 1))
    assert 0.80 <= observed_rate <= 0.95


def test_whites_reality_check_noise_strategies():
    # 10 pure white noise strategies against zero benchmark
    rng = np.random.default_rng(42)
    noise_returns = rng.normal(loc=0.0, scale=0.01, size=(200, 10))

    res = calculate_whites_reality_check(
        strategy_returns=noise_returns,
        mean_block_length=5.0,
        num_bootstraps=300,
        seed=42,
    )
    assert res["num_strategies"] == 10
    assert res["sample_size"] == 200
    # Because returns are zero-mean noise, White's p-value should not reject H0
    assert res["p_value"] > 0.05
    assert res["is_significant"] is False


def test_whites_reality_check_genuine_alpha():
    # 9 noise strategies, 1 genuine winning strategy with solid positive drift
    rng = np.random.default_rng(42)
    t_obs = 300
    strategies = rng.normal(loc=0.0, scale=0.01, size=(t_obs, 10))
    # Add strong positive drift to strategy #3 (Sharpe ~ 3)
    strategies[:, 3] += 0.003

    res = calculate_whites_reality_check(
        strategy_returns=strategies,
        mean_block_length=5.0,
        num_bootstraps=400,
        seed=42,
    )
    assert res["best_strategy_index"] == 3
    assert res["best_strategy_mean"] > 0.002
    assert res["p_value"] < 0.05
    assert res["is_significant"] is True


def test_hansens_spa_robustness_to_poor_models():
    # Hansen's SPA should detect the winning strategy even when a disastrous model is included
    rng = np.random.default_rng(99)
    t_obs = 250
    strategies = rng.normal(loc=0.0, scale=0.01, size=(t_obs, 5))
    # Strategy #1 has real positive edge
    strategies[:, 1] += 0.0025
    # Strategy #4 is a catastrophic loser
    strategies[:, 4] -= 0.05

    res = calculate_hansens_spa(
        strategy_returns=strategies,
        mean_block_length=5.0,
        num_bootstraps=300,
        seed=99,
    )
    assert res["best_strategy_index"] == 1
    assert res["spa_stat"] > 0.0
    assert res["p_value_spa"] < 0.05
    assert res["is_significant"] is True


def test_screen_candidate_setups_fdr_helper():
    setups = {
        "trend_pullback_v1": 0.002,
        "vpoc_rejection_v2": 0.008,
        "cvd_absorption_v1": 0.019,
        "bollinger_squeeze_v3": 0.18,
        "macd_crossover_v1": 0.65,
    }
    screen = screen_candidate_setups_fdr(setups, fdr_target=0.05)
    assert screen["total_setups"] == 5
    assert screen["discoveries_count"] == 3
    assert "trend_pullback_v1" in screen["discoveries"]
    assert "vpoc_rejection_v2" in screen["discoveries"]
    assert "cvd_absorption_v1" in screen["discoveries"]
    assert "macd_crossover_v1" not in screen["discoveries"]


def test_exact_canonical_aliases():
    assert calculate_false_discovery_rate_benjamini_hochberg == calculate_benjamini_hochberg_fdr
    assert calculate_data_snooping_reality_check_white == calculate_whites_reality_check
    assert calculate_superior_predictive_ability_hansen == calculate_hansens_spa
