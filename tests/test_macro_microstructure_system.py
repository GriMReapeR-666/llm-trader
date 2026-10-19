"""End-to-end integration test suite for the institutional macro-microstructure trading system.

Validates:
1. End-to-end cadence: Sunday scan -> Temporal check -> Microstructure sweep -> A+ scoring -> WFO statistical gates.
2. Swarm intent routing across all newly integrated subsystems.
3. Strict invariants: zero lookahead, execution_authority strictly False.
"""
from __future__ import annotations

import pytest
import pandas as pd
from datetime import datetime, timezone
import core.macro_playbook_router as mpr


def test_swarm_intent_routing_across_new_roles():
    new_roles = [
        "MACRO_CONVICTION",
        "TEMPORAL_PLANNER",
        "MICROSTRUCTURE_GATEKEEPER",
        "PURGED_WFO",
        "OPEN_INTELLIGENCE",
    ]
    intents = mpr.supported_intents()
    for role in new_roles:
        assert role in intents
        routed = mpr.route_intent(role)
        assert len(routed["targets"]) == 1
        assert routed["targets"][0]["symbol"]
        assert routed["execution_authority"] is False


def test_route_event_enrichment_with_temporal_context():
    res = mpr.route_event("Consumer Price Index", minutes_since_event=10.0, actual=0.4, consensus=0.2)
    assert res["verdict"] == "ROUTED"
    assert res["three_window_protocol"] is not None
    assert res["temporal_context"] is not None
    assert "tier_1_yearly" in res["temporal_context"]
    assert "tier_3_day" in res["temporal_context"]
    assert "tier_4_killzone" in res["temporal_context"]
    assert res["execution_authority"] is False


def test_end_to_end_institutional_macro_microstructure_pipeline():
    # 1. Ingest Open Intelligence Telemetry
    aggregator = mpr.OpenIntelligenceAggregator()
    telemetry = aggregator.get_geopolitical_telemetry_vector(
        gdelt_records=[{"goldstein": -6.0, "quad_class": 3}],
        acled_incidents=[{"location": "Bab el-Mandeb", "latitude": 12.5, "longitude": 43.3}],
    )
    assert telemetry["supply_shock_detected"] is True

    # 2. Compute Multi-Domain Conviction Score S_{i,t}
    score_res = mpr.calculate_net_conviction_score(
        macro_surprise_score=2.2,
        cb_divergence_score=1.8,
        geopolitical_shock_score=telemetry["goldstein_conflict_intensity"],
        cot_3y_z_score=0.4,
    )
    assert score_res["actionable_zone"] == "ACTIVE_LONG"
    assert score_res["net_conviction_score"] >= 45.0

    # 3. Sunday Selection Funnel (isolate Top 2 uncorrelated pairs)
    funnel = mpr.evaluate_selection_funnel(
        candidate_scores={"USDJPY": score_res["net_conviction_score"], "EURUSD": -52.0, "USDCHF": 60.0},
        correlations={("USDJPY", "EURUSD"): -0.20, ("USDJPY", "USDCHF"): 0.80},
    )
    assert len(funnel["selected_trades"]) == 2
    selected_symbols = [t["asset"] for t in funnel["selected_trades"]]
    assert "USDJPY" in selected_symbols
    assert "EURUSD" in selected_symbols

    # 4. Temporal Planning Check
    tue_ldn_dt = datetime(2026, 10, 13, 8, 30, tzinfo=timezone.utc)  # Tuesday London Open
    temporal_ctx = mpr.evaluate_temporal_context(tue_ldn_dt)
    assert temporal_ctx["is_high_probability_execution_window"] is True

    # 5. Microstructure Sweep & Impact Candle & A+ Scorecard
    sweep = mpr.detect_liquidity_sweep(
        bar_low=1.0505, bar_high=1.0530, bar_close=1.0525, target_pool_level=1.0520, pool_type="PDL", macro_bias="LONG"
    )
    assert sweep["sweep_detected"] is True

    cvd = mpr.evaluate_cvd_divergence(
        price_low_prior=1.0520, price_low_current=1.0505, cvd_prior=-500.0, cvd_current=-300.0, direction="LONG"
    )
    assert cvd["divergence_detected"] is True

    impact = mpr.evaluate_impact_candle(
        bar_open=1.0520, bar_close=1.0560, bar_high=1.0562, bar_low=1.0518, bar_volume=2000.0,
        hist_vol_mean=900.0, hist_vol_std=400.0, prior_bar_high=1.0525, next_bar_low=1.0535,
        swing_high_to_break=1.0545,
    )
    assert impact["is_impact_candle"] is True

    scorecard = mpr.grade_a_plus_confluence(
        macro_conviction_score=score_res["net_conviction_score"],
        is_high_prob_temporal=temporal_ctx["is_high_probability_execution_window"],
        pool_type="PDL",
        in_discount_or_premium=True,
        cvd_divergence=cvd["divergence_detected"],
        impact_candle_valid=impact["is_impact_candle"],
        risk_reward_ratio=3.2,
    )
    assert scorecard["composite_score"] >= 85.0
    assert scorecard["a_plus_qualified"] is True
    assert scorecard["execution_order_type"] == "LIMIT_ORDER_AT_FVG"

    # 6. Bi-Temporal Purged WFO Gates
    mock_trades = [
        mpr.TradeRecord("USDJPY", 1, pd.Timestamp("2026-10-01", tz="UTC"), pd.Timestamp("2026-10-03", tz="UTC"),
                        150.0, 149.0, 151.5, 153.0, 2.5 if i % 3 != 0 else -1.0)
        for i in range(35)
    ]
    survival = mpr.evaluate_statistical_survival_gates(mock_trades, is_annual_return=0.20, oos_annual_return=0.14)
    assert survival["wfe_pass"] is True
    assert survival["leave_one_out_pass"] is True
    assert survival["all_gates_passed"] is True
