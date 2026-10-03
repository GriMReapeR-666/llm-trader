"""Unit tests for P11: Frictional Growth, Compound Heat & Opportunity Cost Hurdle (Masoumian 2026).

Validates:
- Compound growth rate calculation under geometric compounding and frictions.
- Opportunity cost hurdle r_opp gating (trades must exceed opportunity cost of capital).
- Integration into RiskManager sizing pipeline.
"""
from __future__ import annotations

import math
import pytest
from core.metrics import (
    calculate_compound_growth_rate,
    check_opportunity_cost_hurdle,
    calculate_cost_adjusted_kelly,
    calculate_statistical_kill_switch,
)
from core.risk_manager import RiskManager


def test_compound_growth_rate_positive_and_negative():
    """Compound growth rate is positive for edge, drops negative for bad edge or over-leverage."""
    # Good strategy: WR=0.50, RR=2.0, risk=2%
    g_good = calculate_compound_growth_rate(
        win_rate=0.50,
        reward_risk_ratio=2.0,
        risk_fraction=0.02,
        friction_r=0.02,
    )
    assert g_good > 0.0

    # Sub-break-even strategy: WR=0.30, RR=1.5, friction=0.05
    g_bad = calculate_compound_growth_rate(
        win_rate=0.30,
        reward_risk_ratio=1.5,
        risk_fraction=0.02,
        friction_r=0.05,
    )
    assert g_bad < 0.0


def test_check_opportunity_cost_hurdle_gate():
    """Trades must clear the opportunity cost hurdle of capital (r_opp)."""
    # Marginal edge (0.02R) below 0.05R hurdle
    ok_marginal, ev_net, reason = check_opportunity_cost_hurdle(
        win_rate=0.45,
        reward_risk_ratio=1.4,
        friction_r=0.08,
        r_opp=0.05,
    )
    assert ok_marginal is False
    assert "below_opportunity_hurdle" in reason

    # Strong edge (0.45R) well above 0.05R hurdle
    ok_strong, ev_strong, reason_strong = check_opportunity_cost_hurdle(
        win_rate=0.55,
        reward_risk_ratio=1.8,
        friction_r=0.04,
        r_opp=0.05,
    )
    assert ok_strong is True
    assert reason_strong == "admissible"


def test_risk_manager_opportunity_cost_scalar():
    """RiskManager applies opportunity cost hurdle scalar to prevent deploying capital into sub-hurdle setups."""
    rm = RiskManager(balance=10000.0, risk_per_trade=0.01)
    rm.opportunity_cost_hurdle_r = 0.05

    # Neutral when fewer than min_trades history exists
    scalar_neutral = rm.opportunity_cost_scalar(min_trades=20)
    assert scalar_neutral == 1.0

    # Populate 25 losing/marginal trades (net expectancy negative or < 0.05R)
    rm.trade_r_history = [-1.0, 1.0, -1.0, -1.0, 1.05] * 5
    scalar_rejected = rm.opportunity_cost_scalar(min_trades=20)
    assert scalar_rejected == 0.0

    # Populate 25 strong winning trades (net expectancy >> 0.05R)
    rm.trade_r_history = [2.0, 1.5, -1.0, 2.5, -1.0] * 5
    scalar_accepted = rm.opportunity_cost_scalar(min_trades=20)
    assert scalar_accepted == 1.0


def test_risk_manager_size_zero_when_opportunity_cost_breached():
    """calculate_position_size returns 0.0 when opportunity cost hurdle is not met."""
    rm = RiskManager(balance=10000.0, risk_per_trade=0.01)
    rm.opportunity_cost_hurdle_r = 0.10
    # 25 trades with 0.02R mean expectancy (< 0.10R hurdle)
    rm.trade_r_history = [-1.0, 1.04] * 15

    lots = rm.calc_position_size(
        symbol="EURUSD",
        entry=1.1000,
        sl=1.0950,
        confidence=0.85,
    )
    assert lots == 0.0
