"""Unit tests for Multi-Domain Quantitative Scoring & 2-Trade-Per-Week Selection Funnel.

Tests:
- Bounded net conviction score S_{i,t} in [-100, +100]
- Hurdle rate filtering (+45 / -45)
- Cross-asset alignment checks
- De-correlation filter (|r| < 0.35)
- 5 Master Playbook triggers
"""
from __future__ import annotations

import pytest
import numpy as np
from core.macro_conviction_funnel import (
    calculate_net_conviction_score,
    evaluate_selection_funnel,
    classify_master_playbook,
    MacroConvictionEngine,
)


def test_net_conviction_score_bounds_and_weights():
    # Long conviction: strong macro surprise (+2.0), hawkish CB (+1.5), positive ToT energy exporter (+1.0), balanced COT (Z=0.2)
    res_long = calculate_net_conviction_score(
        macro_surprise_score=2.0,
        cb_divergence_score=1.5,
        geopolitical_shock_score=1.0,
        cot_3y_z_score=0.2,
    )
    assert res_long["status"] == "READY_FOR_REVIEW"
    assert 45.0 <= res_long["net_conviction_score"] <= 100.0
    assert res_long["actionable_zone"] == "ACTIVE_LONG"

    # Extreme crowding penalty: COT Z = +2.5 -> P_{i,t} = -1.0 (penalizes long continuation)
    res_crowded = calculate_net_conviction_score(
        macro_surprise_score=1.0,
        cb_divergence_score=1.0,
        geopolitical_shock_score=0.0,
        cot_3y_z_score=2.5,
    )
    assert res_crowded["p_component"] < 0
    assert res_crowded["net_conviction_score"] < res_long["net_conviction_score"]


def test_selection_funnel_decorrelation_top2():
    engine = MacroConvictionEngine()
    candidate_scores = {
        "USDJPY": 68.0,   # Strong long
        "USDCHF": 65.0,   # Strong long, but highly correlated with USDJPY (r = 0.75)
        "EURAUD": -58.0,  # Strong short, uncorrelated with USDJPY (r = -0.10)
        "EURUSD": -30.0,  # Below hurdle |S| < 45 -> Discarded
        "GBPUSD": 20.0,   # Below hurdle -> Discarded
    }
    # Synthetic correlation matrix
    corr_matrix = {
        ("USDJPY", "USDCHF"): 0.75,
        ("USDJPY", "EURAUD"): -0.10,
        ("USDCHF", "EURAUD"): 0.05,
    }

    selected = engine.run_weekly_selection_funnel(candidate_scores, corr_matrix)
    assert selected["status"] == "READY_FOR_REVIEW"
    assert len(selected["selected_trades"]) == 2
    # USDCHF must be rejected by the de-correlation gate (|r| < 0.35)
    selected_assets = [t["asset"] for t in selected["selected_trades"]]
    assert "USDJPY" in selected_assets
    assert "EURAUD" in selected_assets
    assert "USDCHF" not in selected_assets


def test_master_playbook_classification():
    # 1. CB-DIV
    pb_cb = classify_master_playbook({
        "rate_spread_expanding_bps": 18.0,
        "cb_guidance_divergence": True,
    })
    assert pb_cb["playbook_code"] == "CB-DIV"

    # 2. STAG-SHOCK
    pb_stag = classify_master_playbook({
        "core_cpi_surprise_pct": 0.25,
        "employment_contracting": True,
    })
    assert pb_stag["playbook_code"] == "STAG-SHOCK"

    # 3. SUPPLY-DISRUPT
    pb_supply = classify_master_playbook({
        "chokepoint_compromised": True,
        "crude_backwardation_steepening": True,
    })
    assert pb_supply["playbook_code"] == "SUPPLY-DISRUPT"

    # 4. LIQUIDITY-SQUEEZE
    pb_liq = classify_master_playbook({
        "tga_balance_building_billions": 120.0,
        "sofr_above_iorb": True,
    })
    assert pb_liq["playbook_code"] == "LIQUIDITY-SQUEEZE"

    # 5. CARRY-UNWIND
    pb_carry = classify_master_playbook({
        "vix_level": 24.5,
        "leveraged_carry_percentile": 88.0,
    })
    assert pb_carry["playbook_code"] == "CARRY-UNWIND"
