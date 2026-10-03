"""Unit tests for Module 06 Robustness Playbook and Compounding Systems."""
import numpy as np
import pytest

from core.robustness_playbook import (
    calculate_adf_cointegration,
    evaluate_50pct_haircut_rule,
    apply_carver_trade_buffering,
    calculate_portfolio_decorrelation_volatility,
)


def test_adf_cointegration():
    np.random.seed(42)
    x = np.cumsum(np.random.randn(100))
    y = 2.0 * x + np.random.normal(0, 0.5, 100)  # Cointegrated series
    res = calculate_adf_cointegration(y, x)
    assert res["is_cointegrated"] is True
    assert pytest.approx(res["hedge_ratio"], abs=0.2) == 2.0


def test_50pct_haircut_rule():
    # Strong backtest: Sharpe 2.4, CAGR 30%, MaxDD 10% -> Passes haircut
    res_pass = evaluate_50pct_haircut_rule(backtest_sharpe=2.4, backtest_cagr=0.30, backtest_max_dd=0.10)
    assert res_pass["is_promotion_approved"] is True
    assert res_pass["expected_live_sharpe"] == 1.20

    # Fragile backtest: Sharpe 1.5 -> Live Sharpe 0.75 < 1.0 -> Rejected
    res_fail = evaluate_50pct_haircut_rule(backtest_sharpe=1.5, backtest_cagr=0.15, backtest_max_dd=0.12)
    assert res_fail["is_promotion_approved"] is False


def test_carver_trade_buffering():
    # Position change of 5% (inside +/- 10% buffer) -> Suppress rebalance
    executed_pos, is_executed = apply_carver_trade_buffering(target_position=1.05, current_position=1.00, buffer_pct=0.10)
    assert is_executed is False
    assert executed_pos == 1.00

    # Position change of 20% (outside +/- 10% buffer) -> Execute full rebalance
    executed_pos, is_executed = apply_carver_trade_buffering(target_position=1.20, current_position=1.00, buffer_pct=0.10)
    assert is_executed is True
    assert executed_pos == 1.20


def test_portfolio_decorrelation():
    sleeve_vols = [0.15, 0.15, 0.15, 0.15]
    corr_matrix = np.array([
        [1.00, 0.10, 0.10, 0.10],
        [0.10, 1.00, 0.10, 0.10],
        [0.10, 0.10, 1.00, 0.10],
        [0.10, 0.10, 0.10, 1.00],
    ])
    res = calculate_portfolio_decorrelation_volatility(sleeve_vols, corr_matrix)
    # Diversification reduces portfolio volatility from 15% to ~8%
    assert res["portfolio_volatility"] < 0.10
    assert res["volatility_reduction_percent"] > 30.0
