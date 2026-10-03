"""Unit tests for Module 02: Geopolitical Macro Foresight, Scenario Planning & Early Warning Systems."""
import pytest

from intelligence.geopolitical_macro_foresight import (
    GeopoliticalRegime,
    GeopoliticalSignal,
    COMPASSScore,
    calculate_compass_strategic_bearings,
    calculate_caldara_iacoviello_gpr_divergence,
    evaluate_sipri_eight_domain_early_warning_system,
    evaluate_yield_curve_recession_transmission,
    construct_two_by_two_scenario_matrix,
    BayesianGeopoliticalRegimeClassifier,
    GeopoliticalMacroPredictor,
)


def test_compass_strategic_bearings():
    res = calculate_compass_strategic_bearings(
        culture=80.0,
        organization=50.0,
        military=90.0,
        production=40.0,
        alliances=30.0,
        spatial=60.0,
        sustainability=85.0,
    )
    assert res["composite_risk"] == pytest.approx(62.14, abs=0.1)
    assert res["critical_vulnerability_bearing"] == "military"
    assert res["critical_vulnerability_score"] == 90.0
    assert res["strategic_status"] == "ELEVATED"


def test_caldara_iacoviello_gpr_divergence():
    # Threat index spikes to 160 (Z = 4.0), Action index remains at 100 (Z = 0.0) -> Accumulate convexity
    res_underpriced = calculate_caldara_iacoviello_gpr_divergence(
        gpr_threat_index=160.0,
        gpr_action_index=100.0,
    )
    assert res_underpriced["regime"] == "UNDERPRICED_THREAT_ACCUMULATE_CONVEXITY"
    assert res_underpriced["allocation_bias"] == "LONG_VOL_GOLD_DEFENSE"

    # Action index spikes to 160 (Z = 4.0), Threat index at 130 -> Event Realized / Sell News
    res_realized = calculate_caldara_iacoviello_gpr_divergence(
        gpr_threat_index=130.0,
        gpr_action_index=160.0,
    )
    assert res_realized["regime"] == "EVENT_REALIZED_MEAN_REVERSION_RISK_ON"
    assert res_realized["allocation_bias"] == "RISK_ON_EQUITIES_SELL_NEWS"


def test_sipri_eight_domain_early_warning_system():
    domain_scores = {
        "justice_rule_of_law": 40.0,
        "socio_cultural": 50.0,
        "internal_security": 75.0,      # Triggered
        "geopolitical_setting": 80.0,   # Triggered
        "military_security": 85.0,      # Triggered
        "environment_resources": 30.0,
        "governance_capacity": 45.0,
        "socio_economic": 72.0,         # Triggered
    }
    res = evaluate_sipri_eight_domain_early_warning_system(domain_scores, warning_threshold=70.0)
    assert res["num_tripwires_triggered"] == 4
    assert res["escalation_tier"] == "CRITICAL"
    assert "military_security" in res["triggered_tripwires"]


def test_yield_curve_recession_transmission():
    # Inverted curve (10Y = 4.0%, 2Y = 4.5% -> Spread = -50 bps), SLOOS tightening = 45%, months = 14 -> Stage 3
    res = evaluate_yield_curve_recession_transmission(
        yield_10y=4.0,
        yield_2y=4.5,
        sloos_tightening_net_pct=45.0,
        months_since_inversion=14,
    )
    assert res["is_inverted"] is True
    assert res["spread_bps"] == -50.0
    assert res["transmission_stage"] == "STAGE_3_REAL_ACTIVITY_CONTRACTION_IMMOBILIZATION"
    assert res["estimated_recession_probability"] >= 0.80


def test_two_by_two_scenario_matrix():
    matrix = construct_two_by_two_scenario_matrix(
        axis_x_driver="Geopolitical Tension",
        axis_x_val=75.0,  # High tension
        axis_y_driver="Economic Integration",
        axis_y_val=80.0,  # High integration
    )
    assert matrix["active_quadrant"] == "QUADRANT_2"
    assert matrix["active_scenario"] == "Decoupled Rivalry"


def test_bayesian_geopolitical_regime_classifier():
    classifier = BayesianGeopoliticalRegimeClassifier(memory_decay=0.99)
    assert pytest.approx(sum(classifier.regime_weights.values()), 1e-4) == 1.0

    # Feed escalating military signals
    signals = [
        GeopoliticalSignal(
            timestamp="2026-08-20T00:00:00Z",
            domain="military",
            indicator="Naval Carrier Strike Mobilization",
            value=85.0,
            direction="escalating",
            confidence=0.90,
            source="Satellite_SAR",
        ),
        GeopoliticalSignal(
            timestamp="2026-08-20T00:00:00Z",
            domain="resource",
            indicator="Critical Mineral Export Ban",
            value=90.0,
            direction="escalating",
            confidence=0.85,
            source="Customs_Ministry",
        ),
    ]

    updated_weights = classifier.update_with_signals(signals)
    assert "hegemonic_transition" in updated_weights
    assert "fragmentation" in updated_weights
    assert pytest.approx(sum(classifier.regime_weights.values()), 1e-4) == 1.0

    certainty = classifier.evaluate_entropy_confidence()
    assert 0.0 <= certainty <= 1.0
