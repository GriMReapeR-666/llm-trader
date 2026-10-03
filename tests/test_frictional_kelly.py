"""Tests for Frictional Kelly Criterion and Analytical Kill-Switch (Masoumian, 2026)."""
import math
import pytest
from core.metrics import (
    calculate_cost_adjusted_payoff_ratio,
    calculate_cost_adjusted_kelly,
    calculate_cost_adjusted_quarter_kelly,
    calculate_statistical_kill_switch,
)


def test_statistical_kill_switch_paper_values():
    """Verify analytical streak threshold n_loss^(alpha) = ceil(ln(alpha)/ln(1-WR)).
    
    From Masoumian (2026): For a strategy with WR = 40.54% (0.4054):
    alpha = 0.05 (95% confidence) -> 6 consecutive losses
    alpha = 0.01 (99% confidence) -> 9 consecutive losses
    """
    wr = 0.4054
    assert calculate_statistical_kill_switch(wr, alpha=0.05) == 6
    assert calculate_statistical_kill_switch(wr, alpha=0.01) == 9

    # 50% win rate
    # ln(0.01) / ln(0.5) = -4.605 / -0.6931 = 6.64 -> ceil = 7
    assert calculate_statistical_kill_switch(0.50, alpha=0.01) == 7
    # ln(0.05) / ln(0.5) = -2.9957 / -0.6931 = 4.32 -> ceil = 5
    assert calculate_statistical_kill_switch(0.50, alpha=0.05) == 5


def test_statistical_kill_switch_edge_cases():
    """Verify robust fallback behavior under degenerate inputs."""
    assert calculate_statistical_kill_switch(0.0, alpha=0.01) == 10
    assert calculate_statistical_kill_switch(1.0, alpha=0.01) == 10
    assert calculate_statistical_kill_switch(-0.5, alpha=0.01) == 10
    assert calculate_statistical_kill_switch(0.5, alpha=0.0) == 10
    assert calculate_statistical_kill_switch(0.5, alpha=1.0) == 10
    assert calculate_statistical_kill_switch(float("nan"), alpha=0.01) == 10


def test_cost_adjusted_payoff_ratio():
    """Verify friction impact on payoff ratio rho = W_unit / L_unit."""
    # Zero friction: rho == reward_risk_ratio
    rho_zero = calculate_cost_adjusted_payoff_ratio(
        reward_risk_ratio=2.0,
        stop_distance_points=10.0,
        spread_points=0.0,
        commission_per_lot_dollar=0.0,
        point_value_dollar=1.0,
    )
    assert pytest.approx(rho_zero, abs=1e-6) == 2.0

    # With friction: D_stop=10, RR=2, Spread=1, Comm=1, V=1
    # C_unit = 1*1 + 1 = 2
    # L_unit = 10*1 + 2 = 12
    # W_unit = 10*2*1 - 2 = 18
    # rho = 18 / 12 = 1.5
    rho_fric = calculate_cost_adjusted_payoff_ratio(
        reward_risk_ratio=2.0,
        stop_distance_points=10.0,
        spread_points=1.0,
        commission_per_lot_dollar=1.0,
        point_value_dollar=1.0,
    )
    assert pytest.approx(rho_fric, abs=1e-6) == 1.5


def test_cost_adjusted_payoff_ratio_extreme_frictions():
    """Verify payoff ratio drops to zero if frictions wipe out gross win."""
    # D_stop=2, RR=1, Spread=3 (C_unit = 3 > D_stop*RR = 2) -> W_unit < 0
    rho_negative_gain = calculate_cost_adjusted_payoff_ratio(
        reward_risk_ratio=1.0,
        stop_distance_points=2.0,
        spread_points=3.0,
        commission_per_lot_dollar=0.0,
        point_value_dollar=1.0,
    )
    assert rho_negative_gain == 0.0

    # Non-positive stop distance or point value
    assert calculate_cost_adjusted_payoff_ratio(2.0, stop_distance_points=0.0) == 0.0
    assert calculate_cost_adjusted_payoff_ratio(2.0, stop_distance_points=10.0, point_value_dollar=-1.0) == 0.0


def test_cost_adjusted_kelly_and_quarter_kelly():
    """Verify Kelly fraction f* = WR - (1 - WR)/rho and quarter-Kelly sizing."""
    # WR = 0.5, rho = 1.5 -> f* = 0.5 - 0.5/1.5 = 0.5 - 0.33333 = 0.166667
    f_star = calculate_cost_adjusted_kelly(
        win_rate=0.5,
        reward_risk_ratio=2.0,
        stop_distance_points=10.0,
        spread_points=1.0,
        commission_per_lot_dollar=1.0,
        point_value_dollar=1.0,
    )
    assert pytest.approx(f_star, abs=1e-5) == 0.166667

    # Quarter-Kelly with default cap = 0.02
    # 0.25 * 0.166667 = 0.041667 -> capped at 0.02
    qk_capped = calculate_cost_adjusted_quarter_kelly(
        win_rate=0.5,
        reward_risk_ratio=2.0,
        stop_distance_points=10.0,
        spread_points=1.0,
        commission_per_lot_dollar=1.0,
        point_value_dollar=1.0,
        max_cap=0.02,
    )
    assert qk_capped == 0.02

    # Quarter-Kelly with high cap
    qk_uncapped = calculate_cost_adjusted_quarter_kelly(
        win_rate=0.5,
        reward_risk_ratio=2.0,
        stop_distance_points=10.0,
        spread_points=1.0,
        commission_per_lot_dollar=1.0,
        point_value_dollar=1.0,
        max_cap=0.10,
    )
    assert pytest.approx(qk_uncapped, abs=1e-5) == 0.041667

    # Negative expectancy under high friction -> 0.0 sizing
    f_star_loss = calculate_cost_adjusted_kelly(
        win_rate=0.3,
        reward_risk_ratio=1.0,
        stop_distance_points=10.0,
        spread_points=2.0,
        commission_per_lot_dollar=2.0,
        point_value_dollar=1.0,
    )
    assert f_star_loss == 0.0
