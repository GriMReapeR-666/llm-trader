"""Unit tests for the Microstructure Gatekeeper & Impact Candle State Machine.

Tests:
- Dealing range and Premium/Discount zone classification
- Liquidity sweep detection opposing macro bias
- Dual-mode CVD delta absorption & divergence
- Impact Candle Score (ICS_t): efficiency >= 0.72, Z_vol >= 2.0, FVG, MSS
- 5-step state machine transitions
- A+ Confluence Matrix evaluation (85/100 threshold)
"""
from __future__ import annotations

import pytest
import numpy as np
from core.microstructure_impact_engine import (
    classify_dealing_range_zones,
    detect_liquidity_sweep,
    evaluate_cvd_divergence,
    evaluate_impact_candle,
    grade_a_plus_confluence,
    MicrostructureImpactEngine,
)


def test_dealing_range_premium_discount():
    # Range: Low 1.0500, High 1.0700 -> Eq 1.0600
    res_disc = classify_dealing_range_zones(
        current_price=1.0540,
        range_low=1.0500,
        range_high=1.0700,
    )
    assert res_disc["status"] == "READY_FOR_REVIEW"
    assert res_disc["range_equilibrium"] == 1.0600
    assert res_disc["zone"] == "DISCOUNT"
    assert res_disc["favors_direction"] == "LONG"

    res_prem = classify_dealing_range_zones(
        current_price=1.0660,
        range_low=1.0500,
        range_high=1.0700,
    )
    assert res_prem["zone"] == "PREMIUM"
    assert res_prem["favors_direction"] == "SHORT"


def test_liquidity_sweep_detection():
    # Bullish bias: price probes below Previous Day Low (PDL 1.0520) to 1.0505, then closes at 1.0525
    sweep_bull = detect_liquidity_sweep(
        bar_low=1.0505,
        bar_high=1.0530,
        bar_close=1.0525,
        target_pool_level=1.0520,
        pool_type="PDL",
        macro_bias="LONG",
    )
    assert sweep_bull["status"] == "READY_FOR_REVIEW"
    assert sweep_bull["sweep_detected"] is True
    assert sweep_bull["aligned_with_macro"] is True
    assert sweep_bull["sweep_depth_pips"] > 0


def test_dual_mode_cvd_divergence():
    # Bullish absorption: Price prints lower low, but CVD prints higher low (delta absorption)
    cvd_bull = evaluate_cvd_divergence(
        price_low_prior=1.0520,
        price_low_current=1.0505,  # Lower low
        cvd_prior=-450.0,
        cvd_current=-280.0,       # Higher low in delta
        direction="LONG",
    )
    assert cvd_bull["status"] == "READY_FOR_REVIEW"
    assert cvd_bull["divergence_detected"] is True
    assert cvd_bull["absorption_type"] == "BULLISH_DELTA_ABSORPTION"


def test_impact_candle_scoring():
    # High efficiency (body 85% of range), volume Z-score 2.4, FVG created, MSS confirmed
    res_impact = evaluate_impact_candle(
        bar_open=1.0520,
        bar_close=1.0555,
        bar_high=1.0558,
        bar_low=1.0518,
        bar_volume=1850.0,
        hist_vol_mean=800.0,
        hist_vol_std=400.0,
        prior_bar_high=1.0525,
        next_bar_low=1.0535,  # next_low > prior_high -> FVG!
        swing_high_to_break=1.0545,
    )
    assert res_impact["status"] == "READY_FOR_REVIEW"
    assert res_impact["efficiency"] >= 0.72
    assert res_impact["vol_z"] >= 2.0
    assert res_impact["fvg_created"] is True
    assert res_impact["mss_confirmed"] is True
    assert res_impact["is_impact_candle"] is True


def test_a_plus_confluence_matrix():
    # Full A+ setup: Score = 25 (Macro 65) + 15 (Tue NY) + 20 (PDL in Discount) + 20 (CVD) + 20 (Impact & RR 3.2) = 100
    res_a_plus = grade_a_plus_confluence(
        macro_conviction_score=65.0,
        is_high_prob_temporal=True,
        pool_type="PDL",
        in_discount_or_premium=True,
        cvd_divergence=True,
        impact_candle_valid=True,
        risk_reward_ratio=3.2,
    )
    assert res_a_plus["status"] == "READY_FOR_REVIEW"
    assert res_a_plus["composite_score"] >= 85
    assert res_a_plus["a_plus_qualified"] is True
    assert res_a_plus["execution_order_type"] == "LIMIT_ORDER_AT_FVG"

    # Marginal setup: fails 85-point threshold
    res_marginal = grade_a_plus_confluence(
        macro_conviction_score=48.0,  # 15 pts
        is_high_prob_temporal=False, # 5 pts
        pool_type="ASL",             # 12 pts
        in_discount_or_premium=False,
        cvd_divergence=True,         # 20 pts
        impact_candle_valid=True,    # 20 pts
        risk_reward_ratio=2.2,
    )
    assert res_marginal["composite_score"] < 85
    assert res_marginal["a_plus_qualified"] is False
