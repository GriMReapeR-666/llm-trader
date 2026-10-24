"""Tests for P1-2: Causal Alpha Pruning via 23x23 Correlation Hierarchical Clustering in intelligence/autonomous_causal_engine.py."""
import pytest
import numpy as np
import pandas as pd

from intelligence.autonomous_causal_engine import (
    HierarchicalCausalAlphaPruner,
    CausalPruneResult,
)


def test_hierarchical_clustering_groups_collinear_alphas():
    np.random.seed(42)
    n = 200
    base_signal = np.random.randn(n)
    
    # alpha_036 and alpha_037 are highly correlated (same cluster)
    alpha_036 = base_signal + np.random.randn(n) * 0.1
    alpha_037 = base_signal + np.random.randn(n) * 0.1
    # alpha_001 is independent
    alpha_001 = np.random.randn(n)

    df = pd.DataFrame({
        "alpha_036": alpha_036,
        "alpha_037": alpha_037,
        "alpha_001": alpha_001,
    })

    pruner = HierarchicalCausalAlphaPruner(max_correlation=0.70)
    clusters = pruner.cluster_alphas(df)
    
    # alpha_036 and alpha_037 must be in the same cluster
    cluster_036 = [c for c, members in clusters.items() if "alpha_036" in members][0]
    assert "alpha_037" in clusters[cluster_036]
    # alpha_001 must not be in that cluster
    assert "alpha_001" not in clusters[cluster_036]


def test_causal_pruning_selects_higher_ic_representative():
    np.random.seed(42)
    n = 300
    fwd_ret = np.random.randn(n)

    # alpha_A has high correlation with fwd_ret (IC ~ 0.5)
    alpha_A = fwd_ret * 0.6 + np.random.randn(n) * 0.4
    # alpha_B is collinear with alpha_A (rho ~ 0.85) but noisier (IC ~ 0.25)
    alpha_B = alpha_A * 0.8 + np.random.randn(n) * 0.5
    # alpha_C is independent (IC ~ 0.3)
    alpha_C = fwd_ret * 0.3 + np.random.randn(n) * 0.7

    df = pd.DataFrame({"alpha_A": alpha_A, "alpha_B": alpha_B, "alpha_C": alpha_C})
    fwd_s = pd.Series(fwd_ret)

    pruner = HierarchicalCausalAlphaPruner(max_correlation=0.60)
    result = pruner.prune(df, fwd_s)

    assert isinstance(result, CausalPruneResult)
    # alpha_A must be kept over alpha_B because its IC is higher
    assert "alpha_A" in result.pruned_alphas
    assert "alpha_B" not in result.pruned_alphas
    assert result.dropped_alphas["alpha_B"]["representative"] == "alpha_A"
    # alpha_C should be kept as it is independent
    assert "alpha_C" in result.pruned_alphas


def test_prune_guarantees_max_correlation_ceiling():
    np.random.seed(42)
    n = 250
    # Create 6 factors with some collinear pairs
    f1 = np.random.randn(n)
    f2 = f1 * 0.95 + np.random.randn(n) * 0.05  # corr ~ 0.95
    f3 = np.random.randn(n)
    f4 = f3 * 0.85 + np.random.randn(n) * 0.15  # corr ~ 0.85
    f5 = np.random.randn(n)
    f6 = np.random.randn(n)

    df = pd.DataFrame({"f1": f1, "f2": f2, "f3": f3, "f4": f4, "f5": f5, "f6": f6})
    fwd_s = pd.Series(np.random.randn(n))

    pruner = HierarchicalCausalAlphaPruner(max_correlation=0.70)
    result = pruner.prune(df, fwd_s)

    # Ensure max pairwise correlation among pruned alphas is strictly <= 0.70
    pruned_df = df[result.pruned_alphas]
    corr_matrix = pruned_df.corr().abs()
    np.fill_diagonal(corr_matrix.values, 0.0)
    max_observed_corr = corr_matrix.values.max()

    assert max_observed_corr <= 0.70
    assert result.max_pairwise_correlation <= 0.70
