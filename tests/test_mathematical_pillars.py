"""Comprehensive Test Suite for Module 01: Mathematical Pillars & Metrics Optimization."""
import math
import numpy as np
import pytest

from core.metrics import (
    calculate_stop_distance,
    calculate_1r_dollar_risk,
    calculate_normalized_lot_size,
    calculate_realized_r_multiple,
    calculate_expected_value,
    calculate_sqn,
    calculate_wfe,
    calculate_deflated_sharpe_ratio,
    calculate_information_coefficient,
    calculate_risk_of_ruin,
    calculate_asymmetric_loss_recovery_fraction,
    calculate_discrete_lottery_expectancy,
    calculate_streak_probability,
    calculate_expected_max_losing_streak,
    calculate_calmar_ratio,
    calculate_kelly_criterion,
    calculate_quarter_kelly_sizing,
    calculate_volatility_parity_size,
    calculate_hurst_exponent,
    calculate_atr_percentile,
    calculate_master_expectancy,
    calculate_cagr,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    QuantitativeMetricsEngine,
    MetricsTracker,
)



# ---------------------------------------------------------------------------
# Stage 1 Tests: Micro Standardization
# ---------------------------------------------------------------------------

def test_stop_distance_and_1r_dollar_risk():
    dist = calculate_stop_distance(1.1000, 1.0950)
    assert pytest.approx(dist, 1e-6) == 0.0050

    dollar_risk = calculate_1r_dollar_risk(10000.0, 0.01)
    assert pytest.approx(dollar_risk, 1e-6) == 100.0


def test_normalized_lot_size_discretization():
    # $100 risk, 50 pip stop, standard forex pip value
    size = calculate_normalized_lot_size(
        risk_usd=100.0,
        stop_distance=0.0050,
        tick_size=0.00001,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=10.0,
        volume_step=0.01,
    )
    assert size > 0.0
    # Exact step quantization
    assert round(size % 0.01, 4) in (0.0, 0.01)


def test_realized_r_multiple_buy_and_sell():
    # Long trade: entry=100, stop=90 (1R=10), exit=130 -> +3.0R
    r_buy = calculate_realized_r_multiple(100.0, 130.0, 90.0, side="BUY")
    assert pytest.approx(r_buy, 1e-6) == 3.0

    # Long trade loss: exit=90 -> -1.0R
    r_buy_loss = calculate_realized_r_multiple(100.0, 90.0, 90.0, side="BUY")
    assert pytest.approx(r_buy_loss, 1e-6) == -1.0

    # Short trade: entry=100, stop=110 (1R=10), exit=80 -> +2.0R
    r_sell = calculate_realized_r_multiple(100.0, 80.0, 110.0, side="SELL")
    assert pytest.approx(r_sell, 1e-6) == 2.0


# ---------------------------------------------------------------------------
# Stage 2 Tests: Strategy Validation & Proof Engine
# ---------------------------------------------------------------------------

def test_expected_value():
    # 50% win rate, 2.5R win, 1.0R loss -> EV = 0.5*2.5 - 0.5*1.0 = +0.75R
    ev = calculate_expected_value(0.50, 2.5, 1.0)
    assert pytest.approx(ev, 1e-6) == 0.75

    # 40% win rate, 3.0R win, 1.0R loss, 0.05R friction -> EV = 0.4*3 - 0.6*1 - 0.05 = +0.55R
    ev_friction = calculate_expected_value(0.40, 3.0, 1.0, friction_r=0.05)
    assert pytest.approx(ev_friction, 1e-6) == 0.55


def test_sqn_calculation():
    rs = [2.0, -1.0, 3.0, -1.0, 2.5, -0.5, 4.0, -1.0, 1.5, -1.0, 2.0, 3.0]
    sqn = calculate_sqn(rs)
    assert sqn > 0.0


def test_wfe_calculation():
    # OOS Return = 15%, IS Return = 20% -> WFE = 75% >= 60%
    wfe = calculate_wfe(0.15, 0.20)
    assert pytest.approx(wfe, 1e-6) == 0.75


def test_deflated_sharpe_ratio():
    p_val = calculate_deflated_sharpe_ratio(
        estimated_sharpe=2.0,
        num_trials=10,
        sample_size=250,
        skewness=-0.2,
        kurtosis=3.5,
    )
    assert 0.0 <= p_val <= 1.0


def test_information_coefficient():
    forecasts = [0.01, 0.02, -0.01, 0.03, -0.02, 0.015]
    realized = [0.008, 0.025, -0.009, 0.028, -0.018, 0.012]
    ic = calculate_information_coefficient(forecasts, realized)
    assert ic > 0.85


# ---------------------------------------------------------------------------
# Stage 3 Tests: Survival, Run Theory & Drawdown Physics
# ---------------------------------------------------------------------------

def test_risk_of_ruin():
    # Profitable system with 1% risk per trade -> near 0.0% ruin probability
    ror_positive = calculate_risk_of_ruin(0.50, 2.0, 1.0, risk_fraction=0.01)
    assert ror_positive < 0.01

    # Negative expectancy system -> 100% ruin probability
    ror_negative = calculate_risk_of_ruin(0.30, 1.0, 1.0, risk_fraction=0.01)
    assert ror_negative == 1.0


def test_asymmetric_loss_recovery_fraction():
    # -10% -> +11.11%
    assert pytest.approx(calculate_asymmetric_loss_recovery_fraction(0.10), abs=1e-3) == 0.1111
    # -30% -> +42.86%
    assert pytest.approx(calculate_asymmetric_loss_recovery_fraction(0.30), abs=1e-3) == 0.4286
    # -50% -> +100%
    assert pytest.approx(calculate_asymmetric_loss_recovery_fraction(0.50), abs=1e-3) == 1.0000
    # -80% -> +400%
    assert pytest.approx(calculate_asymmetric_loss_recovery_fraction(0.80), abs=1e-3) == 4.0000
    # -90% -> +900%
    assert pytest.approx(calculate_asymmetric_loss_recovery_fraction(0.90), abs=1e-3) == 9.0000



def test_discrete_lottery_expectancy_proof():
    # Mega millions calculation: $43M jackpot / 175M odds - $3.00 ticket = -$2.7543
    ev = calculate_discrete_lottery_expectancy(prize_payout=43_000_000, probability_odds=175_000_000, ticket_cost=3.0)
    assert pytest.approx(ev, 1e-3) == -2.754
    assert ev < 0.0


def test_streak_probability_and_expected_max_loss_streak():
    # Probability of 5 consecutive losses with 50% loss rate: (0.5)^5 = 0.03125 (3.125%)
    p_streak = calculate_streak_probability(0.50, 5)
    assert pytest.approx(p_streak, 1e-6) == 0.03125

    # Expected max losing streak for 200 trades at 50% win rate: ln(200)/ln(2) ~ 7.64
    l_max = calculate_expected_max_losing_streak(0.50, 200)
    assert 7.0 <= l_max <= 8.0


def test_calmar_ratio():
    # 30% CAGR, 15% MaxDD -> Calmar = 2.0
    calmar = calculate_calmar_ratio(0.30, 0.15)
    assert pytest.approx(calmar, 1e-6) == 2.0



# ---------------------------------------------------------------------------
# Stage 4 Tests: Capital Allocation & Sizing
# ---------------------------------------------------------------------------

def test_kelly_and_quarter_kelly():
    # 55% win rate, 1.5:1 payoff -> K* = (0.55*1.5 - 0.45)/1.5 = 0.375 / 1.5 = 0.25 (25%)
    # Quarter Kelly = 0.25 * 0.25 = 0.0625 -> capped at max_cap (e.g. 0.02)
    k = calculate_kelly_criterion(0.55, 1.5)
    assert pytest.approx(k, 1e-6) == 0.25
    qk = calculate_quarter_kelly_sizing(0.55, 1.5, max_cap=0.02)
    assert pytest.approx(qk, 1e-6) == 0.02


def test_volatility_parity_sizing():
    size = calculate_volatility_parity_size(
        account_equity=10000.0,
        target_volatility=0.01,
        atr_20=0.0050,
        price=1.1000,
        contract_multiplier=100000.0,
    )
    assert size > 0.0


# ---------------------------------------------------------------------------
# Stage 5 Tests: Hurst Exponent & Master Expectancy
# ---------------------------------------------------------------------------

def test_hurst_exponent_trending_and_random():
    np.random.seed(42)
    # Synthetic trending series
    trend_prices = 100.0 + np.cumsum(np.random.normal(0.2, 0.5, 200))
    h_trend = calculate_hurst_exponent(trend_prices)
    assert h_trend > 0.50

    # Short neutral series
    short_series = [100.0, 101.0, 100.5]
    assert calculate_hurst_exponent(short_series) == 0.50


def test_atr_percentile():
    history = list(range(1, 101))
    pct = calculate_atr_percentile(history, 85.0)
    assert pytest.approx(pct, 1.0) == 84.0


def test_master_expectancy_aggregation():
    regimes = {"LOW_VOL_TREND": 0.70, "LOW_VOL_RANGE": 0.30}
    weights = {
        "LOW_VOL_TREND": {"MOMENTUM": 0.80, "MEAN_REV": 0.20},
        "LOW_VOL_RANGE": {"MOMENTUM": 0.10, "MEAN_REV": 0.90},
    }
    expectancies = {
        "LOW_VOL_TREND": {"MOMENTUM": 0.60, "MEAN_REV": -0.20},
        "LOW_VOL_RANGE": {"MOMENTUM": -0.30, "MEAN_REV": 0.50},
    }
    master_ev = calculate_master_expectancy(regimes, weights, expectancies)
    assert master_ev > 0.30


# ---------------------------------------------------------------------------
# Stage 6 Tests: Compounding & Risk-Adjusted Returns
# ---------------------------------------------------------------------------

def test_cagr_and_sharpe_sortino():
    # $10,000 to $14,400 over 2 years -> CAGR = sqrt(1.44) - 1 = 0.20 (20%)
    cagr = calculate_cagr(10000.0, 14400.0, 2.0)
    assert pytest.approx(cagr, 1e-4) == 0.20

    # Sharpe & Sortino on positive returns
    rets = [0.01, -0.005, 0.012, -0.002, 0.015, 0.008, -0.004, 0.02]
    sharpe = calculate_sharpe_ratio(rets)
    sortino = calculate_sortino_ratio(rets)
    assert sharpe > 0.0
    assert sortino > sharpe  # Sortino penalizes only downside, so it should be higher for upside-skewed series


# ---------------------------------------------------------------------------
# Master Engine End-to-End Test
# ---------------------------------------------------------------------------

def test_quantitative_metrics_engine_lifecycle():
    engine = QuantitativeMetricsEngine(initial_balance=10000.0)
    
    # Simulate trades
    engine.record_trade(pnl=250.0, r_multiple=2.5)
    engine.record_trade(pnl=-100.0, r_multiple=-1.0)
    engine.record_trade(pnl=300.0, r_multiple=3.0)
    engine.record_trade(pnl=-100.0, r_multiple=-1.0)
    engine.record_trade(pnl=200.0, r_multiple=2.0)

    engine.update_equity(10250.0)
    engine.update_equity(10150.0)
    engine.update_equity(10450.0)
    engine.update_equity(10350.0)
    engine.update_equity(10550.0)

    summary = engine.compute_summary()
    assert summary["total_trades"] == 5
    assert summary["win_rate"] == 0.60
    assert summary["expected_value_r"] > 0.50
    assert summary["total_pnl"] == 550.0
    assert summary["current_equity"] == 10550.0


def test_exact_mathematical_aliases():
    from core.mathematical_pillars import (
        calculate_structural_invalidation_distance,
        calculate_fixed_fractional_dollar_risk,
        calculate_discrete_lot_allocation,
        calculate_dimensionless_realized_r_multiple,
        calculate_mathematical_expectancy,
        calculate_system_quality_number,
        calculate_walk_forward_efficiency,
        calculate_ruin_probability_balsara,
        calculate_asymmetric_loss_recovery,
        calculate_lottery_expected_value,
        calculate_run_theory_streak_probability,
        calculate_run_theory_expected_max_streak,
        calculate_calmar_drawdown_ratio,
        calculate_kelly_optimal_fraction,
        calculate_fractional_kelly_allocation,
        calculate_volatility_parity_allocation,
        calculate_rescaled_range_hurst_exponent,
        calculate_empirical_atr_percentile,
        calculate_regime_conditioned_master_expectancy,
        calculate_compound_annual_growth_rate,
        calculate_annualized_sharpe_ratio,
        calculate_downside_deviation_sortino_ratio,
    )
    assert calculate_structural_invalidation_distance(100.0, 95.0) == 5.0
    assert calculate_fixed_fractional_dollar_risk(10000.0, 0.01) == 100.0
    assert calculate_discrete_lot_allocation(100.0, 5.0, 1.0, 1.0) == 20.0
    assert calculate_dimensionless_realized_r_multiple(100.0, 110.0, 95.0, side="BUY") == 2.0
    assert calculate_mathematical_expectancy(0.5, 2.0, 1.0) == 0.5
    assert calculate_system_quality_number([1.0] * 15 + [-1.0] * 5) > 0.0
    assert calculate_walk_forward_efficiency(0.12, 0.15) == 0.8
    assert calculate_ruin_probability_balsara(0.5, 2.0, 1.0, 0.01) < 0.01
    assert calculate_asymmetric_loss_recovery(0.50) == 1.0
    assert calculate_lottery_expected_value(43_000_000, 175_000_000, 3.0) < 0.0
    assert calculate_run_theory_streak_probability(0.5, 3) == 0.125
    assert calculate_run_theory_expected_max_streak(0.5, 100) > 0.0
    assert calculate_calmar_drawdown_ratio(0.20, 0.10) == 2.0
    assert pytest.approx(calculate_kelly_optimal_fraction(0.55, 1.5), 1e-6) == 0.25
    assert calculate_fractional_kelly_allocation(0.55, 1.5, max_cap=0.05) > 0.0
    assert calculate_volatility_parity_allocation(10000.0, 0.01, 10.0, 100.0) == 10.0
    assert 0.0 <= calculate_rescaled_range_hurst_exponent(list(range(100, 160))) <= 1.0

    assert 0.0 <= calculate_empirical_atr_percentile(list(range(50)), 25.0) <= 100.0
    assert calculate_regime_conditioned_master_expectancy({"TREND": 1.0}, {"TREND": {"M": 1.0}}, {"TREND": {"M": 0.5}}) == 0.5
    assert pytest.approx(calculate_compound_annual_growth_rate(100.0, 121.0, 2.0), 1e-6) == 0.10
    assert calculate_annualized_sharpe_ratio([0.01, 0.02, 0.015, -0.005, 0.03]) > 0.0
    assert calculate_downside_deviation_sortino_ratio([0.01, 0.02, 0.015, -0.005, 0.03]) > 0.0


