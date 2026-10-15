"""Tests for newly implemented institutional validation and robustness gap fixes.

Covers:
1. calculate_time_under_water in core.metrics
2. stress_remove_best_trades in research.research_stress
3. run_null_hypothesis_trade_permutation_test in research.baseline_research
4. MAPElitesArchive in core.evolutionary_optimizer
"""
from __future__ import annotations

import math
import numpy as np
import pytest

from core.metrics import calculate_time_under_water


def test_calculate_time_under_water_known_path():
    # Peak at 100, drops for 3 bars (95, 90, 98), reaches new peak at 105 (recovery length = 3)
    # Drops for 2 bars (102, 101), end of series (current underwater = 2)
    equity = [100.0, 95.0, 90.0, 98.0, 105.0, 102.0, 101.0]
    result = calculate_time_under_water(equity)
    assert result["max_underwater_bars"] == 3
    assert result["current_underwater_bars"] == 2
    assert result["avg_underwater_bars"] == 3.0
    assert result["underwater_periods"] == [3]


def test_calculate_time_under_water_never_drops():
    equity = [100.0, 101.0, 102.0, 105.0, 110.0]
    result = calculate_time_under_water(equity)
    assert result["max_underwater_bars"] == 0
    assert result["avg_underwater_bars"] == 0.0
    assert result["current_underwater_bars"] == 0
    assert result["underwater_periods"] == []


def test_calculate_time_under_water_invalid_inputs():
    assert calculate_time_under_water([])["max_underwater_bars"] == 0
    assert calculate_time_under_water([100.0])["max_underwater_bars"] == 0
    assert calculate_time_under_water([100.0, float("nan")])["max_underwater_bars"] == 0


def test_stress_remove_best_trades_fragile_edge():
    from research.research_stress import stress_remove_best_trades
    # 20 trades: 1 outlier winner of 10.0 R, others modest winners/losers
    trades = [10.0] + [0.5] * 10 + [-1.0] * 9  # 20 trades
    # Top 5% = 1 trade (10.0 R). Remaining 19 trades: 10 * 0.5 - 9 * 1.0 = 5 - 9 = -4.0 (loss)
    res = stress_remove_best_trades(trades, drop_pct=0.05, minimum_trades=20)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["trades_dropped"] == 1
    assert res["dropped_values"] == [10.0]
    assert res["post_trim_expectancy_r"] < 0
    assert res["is_robust_to_trim"] is False


def test_stress_remove_best_trades_robust_edge():
    from research.research_stress import stress_remove_best_trades
    # 20 trades: consistent positive expectancy
    trades = [3.0] + [1.5] * 10 + [-0.8] * 9  # 20 trades
    res = stress_remove_best_trades(trades, drop_pct=0.05, minimum_trades=20)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["trades_dropped"] == 1
    assert res["post_trim_expectancy_r"] > 0
    assert res["is_robust_to_trim"] is True


def test_null_hypothesis_permutation_strong_edge():
    from research.baseline_research import run_null_hypothesis_trade_permutation_test
    trades = [2.0] * 50
    res = run_null_hypothesis_trade_permutation_test(trades, n_permutations=500, seed=42, minimum_trades=20)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["trade_count"] == 50
    assert res["observed_expectancy_r"] == 2.0
    assert res["p_value"] < 0.01
    assert res["is_statistically_significant"] is True
    assert res["execution_authority"] is False


def test_null_hypothesis_permutation_pure_noise():
    from research.baseline_research import run_null_hypothesis_trade_permutation_test
    trades = [1.0, -1.0] * 25
    res = run_null_hypothesis_trade_permutation_test(trades, n_permutations=500, seed=42, minimum_trades=20)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["observed_expectancy_r"] == 0.0
    assert res["p_value"] > 0.1
    assert res["is_statistically_significant"] is False
    assert res["execution_authority"] is False


def test_null_hypothesis_permutation_insufficient_or_invalid():
    from research.baseline_research import run_null_hypothesis_trade_permutation_test
    res_short = run_null_hypothesis_trade_permutation_test([1.0] * 5, minimum_trades=20)
    assert res_short["status"] == "INSUFFICIENT_DATA"
    assert res_short["execution_authority"] is False

    res_invalid = run_null_hypothesis_trade_permutation_test("not_a_list")
    assert res_invalid["status"] == "INVALID_INPUT"
    assert res_invalid["execution_authority"] is False


def test_map_elites_archive_niche_placement_and_replacement():
    from core.evolutionary_optimizer import MAPElitesArchive, StrategyGene

    archive = MAPElitesArchive(
        dim1_bounds=(10, 50),
        dim1_bins=5,
        dim2_bounds=(1.0, 3.0),
        dim2_bins=5,
    )
    assert archive.total_niches() == 25
    assert archive.occupied_niches() == 0
    assert archive.coverage() == 0.0

    gene1 = StrategyGene(gene_id="g1")
    gene2 = StrategyGene(gene_id="g2")
    gene3 = StrategyGene(gene_id="g3")

    added = archive.add_candidate(gene1, fitness=1.2, features=(20.0, 1.5))
    assert added is True
    assert archive.occupied_niches() == 1
    assert archive.coverage() == 1 / 25

    # Gene 2 maps to same niche (21, 1.52) but with LOWER fitness 0.9 -> should NOT replace
    added_lower = archive.add_candidate(gene2, fitness=0.9, features=(21.0, 1.52))
    assert added_lower is False
    assert archive.occupied_niches() == 1
    elites = archive.get_elites()
    assert len(elites) == 1
    assert elites[0][0].gene_id == "g1"

    # Gene 3 maps to same niche (22, 1.55) with HIGHER fitness 2.5 -> SHOULD replace
    added_higher = archive.add_candidate(gene3, fitness=2.5, features=(22.0, 1.55))
    assert added_higher is True
    assert archive.occupied_niches() == 1
    elites = archive.get_elites()
    assert len(elites) == 1
    assert elites[0][0].gene_id == "g3"
    assert elites[0][1] == 2.5

    # Another candidate in a different niche (e.g. 45, 2.8) -> new niche occupied
    gene4 = StrategyGene(gene_id="g4")
    added_diff = archive.add_candidate(gene4, fitness=1.8, features=(45.0, 2.8))
    assert added_diff is True
    assert archive.occupied_niches() == 2
    assert archive.coverage() == 2 / 25

