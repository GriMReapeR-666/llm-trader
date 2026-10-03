"""Test Suite for Module 06: Econometric Cointegration Testing, Backtest Deflation & Compounding Governance Engine."""
import numpy as np
import pytest

from core.cointegration_and_compounding_governance import (
    calculate_engle_granger_adf_cointegration,
    evaluate_out_of_sample_deflation_hurdle,
    apply_position_band_inertia_filter,
    calculate_multi_asset_portfolio_variance,
    calculate_fractional_growth_optimal_kelly,
    evaluate_multi_horizon_drawdown_circuit_breakers,
    evaluate_alpha_decay_decommission_thresholds,
    # Aliases
    calculate_adf_cointegration,
    evaluate_50pct_haircut_rule,
    apply_carver_trade_buffering,
    calculate_portfolio_decorrelation_volatility,
    calculate_fractional_kelly_sizing,
    evaluate_3tier_circuit_breakers,
    evaluate_model_decommission_gate,
)


def test_engle_granger_adf_cointegration_synthetic_pairs():
    np.random.seed(42)
    # Generate stationary synthetic cointegrated pair: y_t = 2.0 * x_t + e_t, e_t ~ AR(1) mean reverting
    n = 200
    x = 100.0 + np.cumsum(np.random.normal(0, 1, n))
    e = np.zeros(n)
    for t in range(1, n):
        e[t] = 0.5 * e[t - 1] + np.random.normal(0, 0.5)
    y = 2.0 * x + e

    res = calculate_engle_granger_adf_cointegration(y, x)
    assert res["is_cointegrated"] is True
    assert res["t_stat"] < -3.34
    assert pytest.approx(res["hedge_ratio"], 0.1) == 2.0


def test_out_of_sample_deflation_hurdle():
    # Strategy with IS Sharpe = 2.50, CAGR = 40%, MaxDD = 12%
    # Live expected: Sharpe = 1.25 >= 1.0, MaxDD = 18% <= 20% -> Pass
    res_pass = evaluate_out_of_sample_deflation_hurdle(
        in_sample_sharpe=2.50,
        in_sample_cagr=0.40,
        in_sample_max_dd=0.12,
    )
    assert res_pass["is_promotion_approved"] is True
    assert res_pass["expected_live_sharpe"] == 1.25
    assert res_pass["expected_live_max_dd"] == 0.18

    # Strategy with IS Sharpe = 1.50 -> Live expected = 0.75 < 1.0 -> Fail
    res_fail = evaluate_out_of_sample_deflation_hurdle(
        in_sample_sharpe=1.50,
        in_sample_cagr=0.20,
        in_sample_max_dd=0.15,
    )
    assert res_fail["is_promotion_approved"] is False


def test_position_band_inertia_filter():
    # Current = 100 shares, Target = 108 shares (8% change <= 10% deadband buffer) -> Suppress order
    pos_suppressed, executed_flag = apply_position_band_inertia_filter(
        target_position=108.0,
        current_position=100.0,
        deadband_buffer_fraction=0.10,
    )
    assert executed_flag is False
    assert pos_suppressed == 100.0

    # Current = 100 shares, Target = 125 shares (25% change > 10% deadband buffer) -> Execute order
    pos_executed, executed_flag2 = apply_position_band_inertia_filter(
        target_position=125.0,
        current_position=100.0,
        deadband_buffer_fraction=0.10,
    )
    assert executed_flag2 is True
    assert pos_executed == 125.0


def test_multi_asset_portfolio_variance_decorrelation():
    # 4 sleeves with individual volatilities of 15% (0.15) and correlation r = 0.10
    vols = [0.15, 0.15, 0.15, 0.15]
    corr = np.array([
        [1.0, 0.10, 0.10, 0.10],
        [0.10, 1.0, 0.10, 0.10],
        [0.10, 0.10, 1.0, 0.10],
        [0.10, 0.10, 0.10, 1.0],
    ])
    res = calculate_multi_asset_portfolio_variance(vols, corr)
    # Theoretical: port_var = (0.15^2 / 4) * (1 + 3 * 0.10) = 0.005625 * 1.30 = 0.0073125 -> port_vol ~ 8.55%
    assert res["portfolio_volatility"] < 0.10
    assert res["volatility_reduction_percent"] > 40.0


def test_fractional_growth_optimal_kelly():
    # 55% win rate, 1.5 payoff -> full K* = 25%, quarter-Kelly = 6.25% -> capped at 2%
    k_applied = calculate_fractional_growth_optimal_kelly(0.55, 1.5, fractional_multiplier=0.25, max_risk_cap=0.02)
    assert pytest.approx(k_applied, 1e-6) == 0.02


def test_multi_horizon_drawdown_circuit_breakers():
    # Normal state
    res_normal = evaluate_multi_horizon_drawdown_circuit_breakers(
        session_realized_r=-1.0,
        weekly_realized_r=-2.0,
        peak_to_trough_drawdown_fraction=0.04,
    )
    assert res_normal["state"] == "NORMAL_EXECUTION"
    assert res_normal["sizing_multiplier"] == 1.0

    # Level 1 Session Halt (-3.0R)
    res_l1 = evaluate_multi_horizon_drawdown_circuit_breakers(
        session_realized_r=-3.2,
        weekly_realized_r=-3.2,
        peak_to_trough_drawdown_fraction=0.05,
    )
    assert res_l1["state"] == "LEVEL_1_SESSION_HALT"
    assert res_l1["sizing_multiplier"] == 0.0

    # Level 2 Weekly Scale Down (-6.0R)
    res_l2 = evaluate_multi_horizon_drawdown_circuit_breakers(
        session_realized_r=-1.0,
        weekly_realized_r=-6.5,
        peak_to_trough_drawdown_fraction=0.08,
    )
    assert res_l2["state"] == "LEVEL_2_WEEKLY_SCALE_DOWN"
    assert res_l2["sizing_multiplier"] == 0.50

    # Level 3 Portfolio Cash Liquidation (>= 15% DD)
    res_l3 = evaluate_multi_horizon_drawdown_circuit_breakers(
        session_realized_r=0.0,
        weekly_realized_r=-2.0,
        peak_to_trough_drawdown_fraction=0.16,
    )
    assert res_l3["state"] == "LEVEL_3_PORTFOLIO_CASH_LIQUIDATION"
    assert res_l3["sizing_multiplier"] == 0.0


def test_alpha_decay_decommission_thresholds():
    # Healthy model: 60d Sharpe = 1.20, DD duration = 5 bars <= 1.5 * 8.0 = 12 bars
    res_healthy = evaluate_alpha_decay_decommission_thresholds(
        rolling_sharpe_60d=1.20,
        current_drawdown_duration_bars=5,
        expected_max_losing_streak_l_max=8.0,
    )
    assert res_healthy["should_decommission"] is False

    # Decayed Sharpe: 60d Sharpe = 0.40 < 0.50 floor
    res_decay_sharpe = evaluate_alpha_decay_decommission_thresholds(
        rolling_sharpe_60d=0.40,
        current_drawdown_duration_bars=5,
        expected_max_losing_streak_l_max=8.0,
    )
    assert res_decay_sharpe["should_decommission"] is True
    assert res_decay_sharpe["sharpe_breached"] is True

    # Decayed Duration: DD duration = 15 bars > 1.5 * 8.0 = 12 bars
    res_decay_duration = evaluate_alpha_decay_decommission_thresholds(
        rolling_sharpe_60d=1.10,
        current_drawdown_duration_bars=15,
        expected_max_losing_streak_l_max=8.0,
    )
    assert res_decay_duration["should_decommission"] is True
    assert res_decay_duration["duration_breached"] is True
