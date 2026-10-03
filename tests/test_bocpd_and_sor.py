"""Unit tests for Module 03: BOCPD, Smart Order Routing, Copula Tail Risk, EWC, and Stealth Execution."""
import numpy as np
import pytest

from core.bocpd import (
    BayesianOnlineChangepointDetector,
    detect_changepoints_offline,
    detect_changepoints_offline_series,
)
from core.copula_tail_risk import (
    compute_lower_tail_dependence,
    compute_bivariate_lower_tail_dependence,
    clayton_copula_tail_risk,
    evaluate_copula_extreme_value_tail_risk,
)
from core.order_execution import (
    calculate_alpha_urgency,
    determine_smart_order_routing,
)
from core.urgency_sor import (
    calculate_alpha_decay_urgency_score,
    UrgencySmartOrderRouter,
    SyntheticStealthExecutionDaemon,
)
from core.ewc_forgetting_prevention import ElasticWeightConsolidationRegularizer


def test_bocpd_online_detection():
    detector = BayesianOnlineChangepointDetector(hazard_lambda=30.0)
    # Feed stationary series
    for _ in range(25):
        cp_prob = detector.update(np.random.normal(0.0, 0.1))
        assert 0.0 <= cp_prob <= 1.0

    # Inject sudden shock
    shock_cp = detector.update(10.0)
    assert shock_cp > 0.10  # Changepoint probability spikes on structural shock


def test_bocpd_detect_changepoints_offline():
    series = np.concatenate([
        np.random.normal(0.0, 0.2, 30),
        np.random.normal(5.0, 0.2, 30),  # Sudden jump at index 30
    ])
    cps = detect_changepoints_offline_series(series, hazard_lambda=25.0, threshold=0.20)
    assert isinstance(cps, list)
    assert len(cps) > 0


def test_copula_tail_dependence():
    np.random.seed(42)
    # Perfectly co-moving tail returns
    x = np.random.randn(100)
    y = x + np.random.normal(0, 0.05, 100)
    td = compute_bivariate_lower_tail_dependence(x, y, quantile=0.10)
    assert td > 0.40  # High lower tail dependence


def test_clayton_copula_tail_risk_scaling():
    np.random.seed(42)
    x = np.random.randn(80)
    y = x + np.random.normal(0, 0.05, 80)
    returns_matrix = np.column_stack([x, y])
    res = evaluate_copula_extreme_value_tail_risk(returns_matrix, normal_heat_budget_r=4.0, crisis_heat_budget_r=1.0)
    assert "adjusted_heat_budget_r" in res
    assert res["adjusted_heat_budget_r"] <= 4.0
    assert res["tail_risk_state"] in ("NORMAL", "ELEVATED_TAIL_RISK", "CRISIS_TAIL_CONTAGION")


def test_alpha_urgency_and_sor_routing():
    # Fast momentum breakout: high vol + high imbalance -> Aggressive Taker
    u_high = calculate_alpha_urgency(instantaneous_volatility=1.5, order_flow_imbalance=20.0, depth=5.0)
    assert u_high > 0.70
    sor_high = determine_smart_order_routing(u_high)
    assert sor_high["execution_style"] == "AGGRESSIVE_TAKER"
    assert sor_high["filling_type"] == "IOC"

    # Slow mean reversion: low vol + low imbalance -> Passive Maker
    u_low = calculate_alpha_urgency(instantaneous_volatility=0.1, order_flow_imbalance=0.2, depth=20.0)
    assert u_low < 0.30
    sor_low = determine_smart_order_routing(u_low)
    assert sor_low["execution_style"] == "PASSIVE_MAKER"
    assert sor_low["fade_quote"] is True


def test_urgency_smart_order_router_engine():
    sor = UrgencySmartOrderRouter(high_urgency_threshold=0.70, low_urgency_threshold=0.30)
    # High urgency order -> Taker IOC
    routed_taker = sor.route_execution_order(
        symbol="EURUSD",
        side="BUY",
        volume=1.0,
        price=1.1000,
        urgency_score=0.85,
        best_bid=1.0999,
        best_ask=1.1001,
    )
    assert routed_taker["is_taker"] is True
    assert routed_taker["order_type"] == "MARKET_IOC"

    # Low urgency order -> Maker Limit
    routed_maker = sor.route_execution_order(
        symbol="EURUSD",
        side="BUY",
        volume=1.0,
        price=1.1000,
        urgency_score=0.20,
        best_bid=1.0999,
        best_ask=1.1001,
        bid_depth=200.0,
        ask_depth=100.0,
    )
    assert routed_maker["is_taker"] is False
    assert routed_maker["order_type"] == "LIMIT_MAKER"


def test_elastic_weight_consolidation_loss():
    theta_curr = np.array([1.2, 0.5, -0.3])
    theta_old = np.array([1.0, 0.5, 0.0])
    fisher_diag = np.array([10.0, 5.0, 8.0])

    penalty = ElasticWeightConsolidationRegularizer.compute_ewc_loss_penalty(
        current_parameters=theta_curr,
        previous_optimal_parameters=theta_old,
        fisher_information_diagonal=fisher_diag,
        regularizer_lambda=50.0,
    )
    # diff = [0.2, 0.0, -0.3], diff^2 = [0.04, 0.0, 0.09]
    # sum(F * diff^2) = 10*0.04 + 0 + 8*0.09 = 0.40 + 0.72 = 1.12
    # penalty = 0.5 * 50 * 1.12 = 28.0
    assert pytest.approx(penalty, 1e-4) == 28.0


def test_synthetic_stealth_execution_daemon():
    # Long position with SL at 95.0, TP at 110.0
    # Price reaches 94.5 on high volume (150 > 50) -> Triggers SL
    res_sl = SyntheticStealthExecutionDaemon.evaluate_synthetic_trigger(
        current_price=94.5,
        stop_loss_price=95.0,
        take_profit_price=110.0,
        side="BUY",
        current_bar_volume=150.0,
        min_bar_volume_confirmation=50.0,
    )
    assert res_sl["should_execute"] is True
    assert res_sl["sl_triggered"] is True
    assert res_sl["reason"] == "SYNTHETIC_STOP_LOSS_CONFIRMED"

    # Price reaches 94.5 on low volume (20 < 50) -> Filtered out (not confirmed)
    res_unconfirmed = SyntheticStealthExecutionDaemon.evaluate_synthetic_trigger(
        current_price=94.5,
        stop_loss_price=95.0,
        take_profit_price=110.0,
        side="BUY",
        current_bar_volume=20.0,
        min_bar_volume_confirmation=50.0,
    )
    assert res_unconfirmed["should_execute"] is False
    assert res_unconfirmed["is_volume_confirmed"] is False
