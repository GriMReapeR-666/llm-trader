"""Unit tests for the Multi-Horizon Temporal Planning Engine.

Tests:
- Tier 1: Yearly liquidity seasonality (Q1-Q4)
- Tier 2: Monthly 4-week catalyst cycle
- Tier 3: Day-of-week structural templates (Mon-Fri)
- Tier 4: Session Killzone detection (Asian, London, NY, London Close)
- Weekend de-risking policy check at Friday 16:00 UTC
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from core.temporal_planning_engine import (
    classify_yearly_regime,
    classify_monthly_cycle,
    classify_day_of_week_template,
    detect_session_killzone,
    evaluate_temporal_context,
    TemporalPlanningEngine,
)


def test_yearly_regime_classification():
    # Feb 15 -> Q1 Fresh capital
    q1_dt = datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc)
    res_q1 = classify_yearly_regime(q1_dt)
    assert res_q1["quarter"] == "Q1"
    assert "FRESH_CAPITAL_DEPLOYMENT" in res_q1["regime_name"]

    # May 10 -> Q2 Tax drain
    q2_dt = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    res_q2 = classify_yearly_regime(q2_dt)
    assert res_q2["quarter"] == "Q2"
    assert "TAX_RECEIPTS_DRAIN" in res_q2["regime_name"]

    # Dec 20 -> Q4 Year-end turn / balance sheet freeze
    dec_dt = datetime(2026, 12, 20, 12, 0, tzinfo=timezone.utc)
    res_dec = classify_yearly_regime(dec_dt)
    assert res_dec["quarter"] == "Q4"
    assert res_dec["year_end_freeze"] is True


def test_monthly_cycle_windows():
    # Day 4 -> Week 1 Growth & Labor Baseline (NFP / PMIs)
    w1_dt = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    res_w1 = classify_monthly_cycle(w1_dt)
    assert res_w1["monthly_week"] == 1
    assert "GROWTH_LABOR_BASELINE" in res_w1["window_name"]

    # Day 11 -> Week 2 Inflation & Yields (CPI)
    w2_dt = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    res_w2 = classify_monthly_cycle(w2_dt)
    assert res_w2["monthly_week"] == 2
    assert "INFLATION_REAL_YIELDS" in res_w2["window_name"]


def test_day_of_week_templates():
    # Tuesday 13:00 UTC -> Judas Swing, Trade 1 deployment window
    tue_dt = datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)
    res_tue = classify_day_of_week_template(tue_dt)
    assert res_tue["day_name"] == "TUESDAY"
    assert res_tue["is_trade_1_window"] is True

    # Friday 16:30 UTC -> Weekend de-risking active
    fri_dt = datetime(2026, 10, 16, 16, 30, tzinfo=timezone.utc)
    res_fri = classify_day_of_week_template(fri_dt)
    assert res_fri["day_name"] == "FRIDAY"
    assert res_fri["weekend_derisking_active"] is True


def test_session_killzone_detection():
    # 03:00 UTC -> Asian Accumulation
    asian_dt = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)
    assert detect_session_killzone(asian_dt)["killzone"] == "ASIAN_ACCUMULATION"

    # 08:30 UTC -> London Open Manipulation
    ldn_dt = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)
    assert detect_session_killzone(ldn_dt)["killzone"] == "LONDON_OPEN"

    # 13:30 UTC -> NY Open Macro Expansion
    ny_dt = datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc)
    assert detect_session_killzone(ny_dt)["killzone"] == "NY_OPEN"

    # 22:00 UTC -> Dead zone
    dead_dt = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)
    assert detect_session_killzone(dead_dt)["killzone"] == "OFF_HOURS_DORMANT"


def test_unified_temporal_context():
    dt = datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc)  # Wednesday NY Open
    ctx = evaluate_temporal_context(dt)
    assert ctx["status"] == "READY_FOR_REVIEW"
    assert ctx["tier_1_yearly"]["quarter"] == "Q4"
    assert ctx["tier_3_day"]["day_name"] == "WEDNESDAY"
    assert ctx["tier_4_killzone"]["killzone"] == "NY_OPEN"
    assert ctx["is_high_probability_execution_window"] is True
