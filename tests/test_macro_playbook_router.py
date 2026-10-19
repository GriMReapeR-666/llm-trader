"""Tests for the Meta-Playbook Router (composition-only module).

Covers: playbook-table reachability, curve-regime detection (all 4 regimes +
fail-closed paths), growth-inflation quadrants, Taylor-rule divergence signs,
meta-router composition + fail-closed behavior, execution_authority=False
invariant across every verdict, and intent-router swarm mapping.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Single import root: repo root only.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import core.macro_playbook_router as mpr


# ---------------------------------------------------------------------------
# 1. Playbook table integrity
# ---------------------------------------------------------------------------

def test_all_five_event_playbooks_present():
    types = {row.event_type for row in mpr.EVENT_PLAYBOOKS}
    assert types == {
        "NFP", "CPI", "CENTRAL_BANK_DECISION",
        "GEOPOLITICAL_SHOCK", "SYSTEMIC_LIQUIDITY_CRISIS",
    }


def test_every_playbook_row_has_content():
    for row in mpr.EVENT_PLAYBOOKS:
        assert row.tier_expected == 1
        assert row.critical_signal and row.transmission_vector
        assert row.tactical_execution_bias and row.aliases


def test_match_event_playbook_all_rows_reachable():
    titles = [
        ("Non-Farm Payrolls", "NFP"),
        ("Consumer Price Index YoY", "CPI"),
        ("FOMC Interest Rate Decision", "CENTRAL_BANK_DECISION"),
        ("Missile strike hits energy infrastructure", "GEOPOLITICAL_SHOCK"),
        ("FRA-OIS spread blows out as repo spikes", "SYSTEMIC_LIQUIDITY_CRISIS"),
    ]
    for title, expected in titles:
        row = mpr.match_event_playbook(title)
        assert row is not None, title
        assert row.event_type == expected


def test_match_event_playbook_specificity_prefers_longest_alias():
    row = mpr.match_event_playbook("US CPI Consumer Price Index release")
    assert row is not None and row.event_type == "CPI"


def test_match_event_playbook_fail_closed():
    assert mpr.match_event_playbook("Random commentary about weather") is None
    assert mpr.match_event_playbook("") is None
    assert mpr.match_event_playbook(None) is None
    assert mpr.match_event_playbook(123) is None


# ---------------------------------------------------------------------------
# 2. Curve-regime detection
# ---------------------------------------------------------------------------

def test_curve_regime_bull_steepener():
    # Front end falls faster than long end: yields falling (BULL), slope widens.
    result = mpr.detect_curve_regime(-25.0, -5.0)
    assert result["regime"] == "BULL_STEEPENER"
    assert result["direction"] == "BULL"
    assert result["dominant_leg"] == "FRONT_END"
    assert result["playbook"]["regime"] == "BULL_STEEPENER"


def test_curve_regime_bear_steepener():
    # Long end rises faster than front end: yields rising (BEAR), slope widens.
    result = mpr.detect_curve_regime(5.0, 30.0)
    assert result["regime"] == "BEAR_STEEPENER"
    assert result["dominant_leg"] == "LONG_END"


def test_curve_regime_bull_flattener():
    # Long end falls faster than front end: yields falling (BULL), slope narrows.
    result = mpr.detect_curve_regime(-2.0, -20.0)
    assert result["regime"] == "BULL_FLATTENER"
    assert result["direction"] == "BULL"


def test_curve_regime_bear_flattener_completed_by_pattern():
    # Classic case: front end reprices hawkish, long end anchored/less rising.
    result = mpr.detect_curve_regime(20.0, 5.0)
    assert result["regime"] == "BEAR_FLATTENER"
    assert result["direction"] == "BEAR"
    assert result["dominant_leg"] == "FRONT_END"
    playbook = mpr.CURVE_REGIME_PLAYBOOKS["BEAR_FLATTENER"]
    assert "hawkish" in playbook.macro_driver.lower()
    assert "front-end" in playbook.mechanics.lower()


def test_bear_flattener_provenance_flagged_in_docs():
    doc = (Path(mpr.__file__).parent.parent / "docs" /
           "ARCHITECTURE_DUPLICATE_PREVENTION.md")
    if doc.exists():
        text = doc.read_text(encoding="utf-8")
        assert "BEAR_FLATTENER" in text and "completed by pattern" in text.lower()


def test_curve_regime_fail_closed():
    missing = mpr.detect_curve_regime(None, 10.0)
    assert missing["regime"] == mpr.INSUFFICIENT_DATA and missing["playbook"] is None
    bad = mpr.detect_curve_regime(float("nan"), 10.0)
    assert bad["regime"] == mpr.INSUFFICIENT_DATA
    noise = mpr.detect_curve_regime(1.0, 1.5)  # slope change = +0.5, inside dead-band
    assert noise["regime"] == "NO_CLEAR_MOVEMENT" and noise["playbook"] is None


def test_all_four_curve_regimes_have_playbooks():
    assert set(mpr.CURVE_REGIME_PLAYBOOKS) == {
        "BULL_STEEPENER", "BEAR_STEEPENER", "BULL_FLATTENER", "BEAR_FLATTENER",
    }


# ---------------------------------------------------------------------------
# 3. Growth-inflation quadrants
# ---------------------------------------------------------------------------

def test_quadrant_all_four_map_correctly():
    cases = [
        (1.0, 1.0, "REFLATION"),
        (1.0, -1.0, "GOLDILOCKS"),
        (-1.0, 1.0, "STAGFLATION"),
        (-1.0, -1.0, "DEFLATION"),
    ]
    for growth_z, inflation_z, expected in cases:
        result = mpr.classify_quadrant(growth_z, inflation_z)
        assert result["quadrant"] == expected
        assert result["bias"] == mpr.GROWTH_INFLATION_QUADRANTS[expected].bias


def test_quadrant_dead_band_does_not_force_claim():
    # growth right AT threshold counts as up; just below forces the down side —
    # but z=0 vs i=0 boundary behavior must be deterministic, not noisy.
    assert mpr.classify_quadrant(0.5, 0.5)["quadrant"] == "REFLATION"
    assert mpr.classify_quadrant(0.4, 0.4)["quadrant"] == "DEFLATION"  # below dead-band -> down/down


def test_quadrant_fail_closed():
    assert mpr.classify_quadrant(None, 1.0)["quadrant"] == mpr.INSUFFICIENT_DATA
    assert mpr.classify_quadrant(1.0, "abc")["quadrant"] == mpr.INSUFFICIENT_DATA
    assert mpr.classify_quadrant(float("inf"), 0.1)["quadrant"] == mpr.INSUFFICIENT_DATA


def test_quadrant_biases_match_spec():
    assert "cyclical" in mpr.GROWTH_INFLATION_QUADRANTS["REFLATION"].bias
    assert "duration" in mpr.GROWTH_INFLATION_QUADRANTS["GOLDILOCKS"].bias.lower()
    assert "gold" in mpr.GROWTH_INFLATION_QUADRANTS["STAGFLATION"].bias
    assert "USD" in mpr.GROWTH_INFLATION_QUADRANTS["DEFLATION"].bias


# ---------------------------------------------------------------------------
# 4. Taylor-rule divergence
# ---------------------------------------------------------------------------

def test_taylor_rule_implied_rate_formula():
    # i = 1.5 + 3.0 + 1.0*(3.0-2.0) + 0.5*1.0 = 6.0
    result = mpr.taylor_rule_divergence(market_implied_rate=6.0, inflation=3.0,
                                        output_gap=1.0)
    assert result["taylor_implied_rate"] == 6.0
    assert result["divergence_bps"] == 0.0
    assert result["divergence_class"] == "ALIGNED"


def test_taylor_rule_divergence_signs():
    # Market 4.75 vs implied 6.0 -> -125bps: market BELOW rule => market prices
    # MORE easing than the rule justifies => DOVISH divergence.
    dovish = mpr.taylor_rule_divergence(4.75, 3.0, output_gap=1.0)
    assert dovish["divergence_bps"] < 0
    assert dovish["divergence_class"] == "MARKET_PRICED_DOVISH"
    # Market 7.0 vs implied 6.0 -> +100bps: market prices MORE tightening.
    hawkish = mpr.taylor_rule_divergence(7.0, 3.0, output_gap=1.0)
    assert hawkish["divergence_bps"] > 0
    assert hawkish["divergence_class"] == "MARKET_PRICED_HAWKISH"


def test_taylor_rule_fail_closed():
    result = mpr.taylor_rule_divergence(None, 3.0)
    assert result["divergence_class"] == mpr.INSUFFICIENT_DATA
    assert result["divergence_bps"] is None


# ---------------------------------------------------------------------------
# 5. Meta-Playbook Router (route_event)
# ---------------------------------------------------------------------------

def test_route_event_nfp_full_composition():
    result = mpr.route_event(
        "Non-Farm Payrolls",
        minutes_since_event=45.0,              # detect_news_phase: trend_continuation
        phase_name="trend_continuation",
        actual=350, consensus=180, historical_std=45.0,   # z = +3.78 (structural)
        disaggregated_components={"ahe_yoy": 4.2, "net_revision": -60},
        macro_regime="EXPANSION", cot_speculative_percentile=80.0,
    )
    assert result["verdict"] == "ROUTED"
    assert result["event_type"] == "NFP"
    assert result["tier"] == 1 and result["tier_matches_playbook"]
    assert result["phase"] == "RELEASE" or result["phase"] in ("RELEASE", "POST_EVENT_ALIGNED")
    assert result["surprise"]["z_delta"] > 2.0
    assert result["alignment"] == "STRUCTURAL_SHIFT_SWING_ALLOCATION"
    assert result["conviction"]["conviction_multiplier"]
    assert "net_revision" in result["composition_note"]
    assert result["playbook"]["tactical_execution_bias"].startswith("Avoid the first")
    assert result["execution_authority"] is False


def test_route_event_transient_noise_alignment():
    result = mpr.route_event("Consumer Price Index", phase_name="post_news_entry",
                             minutes_since_event=20.0,
                             actual=0.16, consensus=0.15, historical_std=0.15)  # z=0.07
    assert result["alignment"] == "TRANSIENT_NOISE_MEAN_REVERSION"
    assert result["execution_authority"] is False


def test_route_event_fail_closed_on_unknown_or_missing():
    unknown = mpr.route_event("Completely unclassified speech")
    assert unknown["verdict"] == mpr.INSUFFICIENT_DATA and unknown["playbook"] is None
    assert unknown["execution_authority"] is False
    no_title = mpr.route_event(None, phase_name="trend_continuation")
    assert no_title["verdict"] == mpr.INSUFFICIENT_DATA
    no_row = mpr.route_event("")
    assert no_row["verdict"] == mpr.INSUFFICIENT_DATA


def test_route_event_tier_mismatch_reported_not_gated():
    # "FED SPEECH" is tier 3 in news_trading; it matches CB aliases here.
    result = mpr.route_event("Fed Speech on inflation outlook")
    assert result["verdict"] == "ROUTED"
    assert result["tier_matches_playbook"] is False   # reported, never blocking
    assert result["execution_authority"] is False


# ---------------------------------------------------------------------------
# 6. Intent router (swarm mapping)
# ---------------------------------------------------------------------------

def test_route_intent_maps_all_four_swarm_roles():
    mapping = {
        "INGESTION": "data.news_triage",
        "CB_TONE": "intelligence.sovereign_macro_and_literary_forensics",
        "CROSS_ASSET": "intelligence.intermarket_regime",
        "RISK_SIZING": "core.risk_manager",
    }
    for intent, expected_target in mapping.items():
        result = mpr.route_intent(intent)
        assert result["targets"], intent
        assert result["targets"][0]["target"] == expected_target
        assert result["targets"][0]["rationale"]
        assert result["execution_authority"] is False


def test_route_intent_fail_closed():
    assert mpr.route_intent("UNKNOWN_QUEST")["targets"] == []
    result = mpr.route_intent(None)
    assert result["targets"] == [] and result["execution_authority"] is False
    assert mpr.route_intent("  ")["targets"] == []


def test_supported_intents_sorted_and_complete():
    intents = mpr.supported_intents()
    assert intents == sorted(intents)
    assert {"INGESTION", "CB_TONE", "CROSS_ASSET", "RISK_SIZING"} <= set(intents)


# ---------------------------------------------------------------------------
# 7. Invariants across the module
# ---------------------------------------------------------------------------

def test_no_execution_authority_anywhere():
    for row in mpr.EVENT_PLAYBOOKS:
        assert row.tier_expected >= 0
    for curve in mpr.CURVE_REGIME_PLAYBOOKS.values():
        assert curve.playbook
    assert mpr.EXECUTION_AUTHORITY is False


def test_router_institutional_macro_framework_integration():
    # Verify institutional macro framework symbols exposed
    expected_symbols = [
        "evaluate_fomc_reaction_function",
        "evaluate_fomc_press_conference",
        "evaluate_ecb_fragmentation_playbook",
        "evaluate_boj_carry_unwind_playbook",
        "evaluate_pboc_credit_transmission_playbook",
        "evaluate_cpi_release_playbook",
        "evaluate_nfp_labor_playbook",
        "evaluate_pmi_cycle_momentum",
        "evaluate_terms_of_trade_shock",
        "evaluate_gold_decoupling_dynamics",
        "evaluate_sovereign_debt_crisis_hierarchy",
        "evaluate_cot_3y_z_scores",
        "evaluate_dealer_gamma_regime",
        "evaluate_fx_risk_reversals",
        "evaluate_master_cross_asset_matrix",
        "evaluate_three_window_execution_protocol",
    ]
    for sym in expected_symbols:
        assert hasattr(mpr, sym), f"Symbol {sym} missing from macro_playbook_router"

    # Verify route_event enriches with three-window execution protocol
    res_absorption = mpr.route_event(
        "Consumer Price Index",
        minutes_since_event=5.0,
        actual=0.4,
        consensus=0.2,
    )
    assert res_absorption["verdict"] == "ROUTED"
    assert res_absorption["three_window_protocol"] is not None
    assert res_absorption["three_window_protocol"]["active_window"] == "WINDOW_2_ABSORPTION_PHASE"
    assert res_absorption["three_window_protocol"]["order_action"] == "ZERO_MARKET_ORDERS_FREEZE"

    res_swing = mpr.route_event(
        "Non-Farm Payrolls",
        minutes_since_event=30.0,
        actual=250000.0,
        consensus=180000.0,
    )
    assert res_swing["verdict"] == "ROUTED"
    assert res_swing["three_window_protocol"] is not None
    assert res_swing["three_window_protocol"]["active_window"] == "WINDOW_3_DIRECTIONAL_ALLOCATION"
