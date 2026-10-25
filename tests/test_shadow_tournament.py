"""Unit tests for research/shadow_tournament.py: Parallel shadow branch A/B tournament."""
import pytest
from research.shadow_tournament import ShadowTournamentArena


def test_shadow_tournament_requires_min_trades():
    arena = ShadowTournamentArena(min_shadow_trades=10)
    for i in range(5):
        arena.record_shadow_trade("strat_1", "branch_a", 0.5)
    res = arena.evaluate_tournament("strat_1", ["branch_a"])
    assert res["status"] == "AWAITING_SHADOW_SAMPLES"
    assert res["winner_branch"] is None


def test_shadow_tournament_prunes_negative_expectancy():
    arena = ShadowTournamentArena(min_shadow_trades=10)
    for i in range(12):
        arena.record_shadow_trade("strat_1", "losing_branch", -0.4)
    res = arena.evaluate_tournament("strat_1", ["losing_branch"])
    assert "losing_branch" in res["pruned_branches"]
    assert res["winner_branch"] is None


def test_shadow_tournament_picks_dominant_winner():
    arena = ShadowTournamentArena(min_shadow_trades=15)
    # branch_a: strong positive expectancy +0.8R with low variance
    for i in range(20):
        arena.record_shadow_trade("strat_1", "branch_a", 0.8 + (0.1 if i % 2 == 0 else -0.1))
    # branch_b: weak positive expectancy +0.1R with high variance
    for i in range(20):
        arena.record_shadow_trade("strat_1", "branch_b", 0.1 + (0.5 if i % 2 == 0 else -0.5))

    res = arena.evaluate_tournament("strat_1", ["branch_a", "branch_b"])
    assert res["status"] == "TOURNAMENT_WINNER_DECIDED"
    assert res["winner_branch"] == "branch_a"
    assert res["win_probability"] >= 0.70
