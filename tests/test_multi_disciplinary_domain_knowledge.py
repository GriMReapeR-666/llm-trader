"""Unit tests for Module 03: Multi-Disciplinary Domain Knowledge Compendium & Causal Case Studies."""
import pytest

from intelligence.multi_disciplinary_domain_knowledge import (
    evaluate_thucydides_trap_parity_risk,
    evaluate_regime_institutional_stability,
    calculate_structural_realism_security_dilemma,
    calculate_military_combat_force_ratio_advantage,
    evaluate_dutch_disease_resource_curse_dynamics,
    calculate_geostrategic_buffer_and_chokepoint_vulnerability,
    calculate_haber_bosch_fertilizer_cost_floor,
    calculate_opec_spare_capacity_price_elasticity,
    evaluate_semiconductor_chokepoint_exposure,
    evaluate_cross_currency_basis_liquidity_stress,
    evaluate_demographic_stability_and_growth_drag,
    synthesize_multi_domain_geopolitical_prediction,
)


def test_thucydides_trap_parity_risk():
    # Parity ratio: 18T / 20T = 0.90 (in 0.80 - 1.20 zone) -> High conflict probability
    res_trap = evaluate_thucydides_trap_parity_risk(gdp_rising_power=18.0, gdp_incumbent_power=20.0)
    assert res_trap["in_thucydides_zone"] is True
    assert res_trap["conflict_probability"] >= 0.75
    assert res_trap["status"] == "HIGH_THUCYDIDES_TRANSITION_RISK"

    # Parity ratio: 5T / 20T = 0.25 (outside zone)
    res_out = evaluate_thucydides_trap_parity_risk(gdp_rising_power=5.0, gdp_incumbent_power=20.0)
    assert res_out["in_thucydides_zone"] is False
    assert res_out["conflict_probability"] < 0.20


def test_regime_institutional_stability():
    # Personalist autocracy, 20 years in power, no defined succession -> Critical succession risk
    res_autocracy = evaluate_regime_institutional_stability(
        regime_type="PERSONALIST_AUTOCRACY",
        years_in_power=20.0,
        institutional_checks_score=15.0,
        succession_mechanism_defined=False,
    )
    assert res_autocracy["succession_cliff_risk"] == "CRITICAL"
    assert res_autocracy["daily_policy_volatility"] == "LOW"

    # Proportional parliamentary democracy -> Coalition friction, low succession risk
    res_parl = evaluate_regime_institutional_stability(
        regime_type="PROPORTIONAL_PARLIAMENTARY",
        years_in_power=3.0,
        institutional_checks_score=85.0,
        succession_mechanism_defined=True,
    )
    assert res_parl["succession_cliff_risk"] == "LOW"
    assert "COALITION_GRIDLOCK" in res_parl["daily_policy_volatility"]


def test_structural_realism_security_dilemma():
    res = calculate_structural_realism_security_dilemma(
        offensive_capabilities_score=90.0,
        defensive_intent_clarity=10.0,  # Opaque / ambiguous intent
        geographical_proximity=85.0,
    )
    assert res["dilemma_score"] >= 70.0
    assert res["security_state"] == "INTENSE_SECURITY_DILEMMA_SPIRAL"


def test_military_combat_force_ratio_advantage():
    # 190k attacker vs 200k defender in urban terrain (requires 6:1 ratio) -> ratio is 0.95 -> prolonged attritional
    res_urban = calculate_military_combat_force_ratio_advantage(
        attacker_troop_strength=190000.0,
        defender_troop_strength=200000.0,
        terrain_type="FORTIFIED_URBAN",
        defender_fortification_level=1.5,
    )
    assert res_urban["is_sufficient_for_offensive"] is False
    assert res_urban["expected_conflict_duration"] == "PROLONGED_ATTRITIONAL_WAR"

    # 350k attacker vs 100k defender in open maneuver (requires 3:1 ratio) -> ratio is 3.5 -> sufficient
    res_maneuver = calculate_military_combat_force_ratio_advantage(
        attacker_troop_strength=350000.0,
        defender_troop_strength=100000.0,
        terrain_type="OPEN_MANEUVER",
        defender_fortification_level=1.0,
    )
    assert res_maneuver["is_sufficient_for_offensive"] is True


def test_dutch_disease_resource_curse_dynamics():
    res = evaluate_dutch_disease_resource_curse_dynamics(
        resource_export_share_gdp=35.0,
        currency_reer_appreciation_pct=15.0,
        manufacturing_pmi=43.0,
        fiscal_oil_revenue_pct=55.0,
    )
    assert res["is_dutch_disease_active"] is True
    assert res["recession_risk_on_commodity_bust"] == "CRITICAL"


def test_geostrategic_buffer_and_chokepoint_vulnerability():
    # State without warm water port, high encirclement
    res = calculate_geostrategic_buffer_and_chokepoint_vulnerability(
        distance_to_open_ocean_nm=1200.0,
        has_warm_water_port=False,
        nato_perimeter_encirclement_pct=70.0,
        chokepoint_transit_dependency_pct=60.0,
    )
    assert res["has_warm_water_port"] is False
    assert res["expansion_compulsion"] == "AGGRESSIVE_BUFFER_SEEKING"
    assert res["structural_defense_gdp_burden_pct"] >= 4.0


def test_haber_bosch_fertilizer_cost_floor():
    # Natural gas at $10.00/MMBtu: Floor = 33.5 * 10 + 65 = $400/ton
    floor_10 = calculate_haber_bosch_fertilizer_cost_floor(natural_gas_price_mmbtu=10.0)
    assert pytest.approx(floor_10, 1e-4) == 400.0

    # Natural gas at $30.00/MMBtu: Floor = 33.5 * 30 + 65 = $1070/ton
    floor_30 = calculate_haber_bosch_fertilizer_cost_floor(natural_gas_price_mmbtu=30.0)
    assert pytest.approx(floor_30, 1e-4) == 1070.0


def test_opec_spare_capacity_price_elasticity():
    # Spare capacity = 1.0 M bbl/d (critical deficit < 2.0) -> exponential surge
    res_tight = calculate_opec_spare_capacity_price_elasticity(spare_capacity_mbpd=1.0)
    assert res_tight["market_regime"] == "CRITICAL_SPARE_CAPACITY_DEFICIT"
    assert res_tight["oil_risk_premium_dollar"] > 30.0

    # Spare capacity = 4.0 M bbl/d -> Ample
    res_ample = calculate_opec_spare_capacity_price_elasticity(spare_capacity_mbpd=4.0)
    assert res_ample["market_regime"] == "AMPLE_SUPPLY_BUFFER"
    assert res_ample["oil_risk_premium_dollar"] == 0.0


def test_semiconductor_chokepoint_exposure():
    res_euv = evaluate_semiconductor_chokepoint_exposure(
        advanced_node_nm=3.0,
        asml_euv_dependency_pct=100.0,
        tsmc_taiwan_fab_share_pct=90.0,
    )
    assert res_euv["is_global_single_point_of_failure"] is True
    assert res_euv["substitution_time_horizon_years"] == 5.0
    assert res_euv["vulnerability_tier"] == "EXTREME_CHOKEPOINT"


def test_cross_currency_basis_liquidity_stress():
    # Widening basis (-60 bps EUR/USD, -70 bps JPY/USD) -> Acute freeze
    res_stress = evaluate_cross_currency_basis_liquidity_stress(
        eur_usd_basis_3m_bps=-60.0,
        jpy_usd_basis_3m_bps=-70.0,
    )
    assert res_stress["liquidity_stress_level"] == "ACUTE_OFFSHORE_USD_LIQUIDITY_FREEZE"
    assert res_stress["strategic_action"] == "ACCUMULATE_USD_CASH_AND_DEFENSIVE_TREASURIES"


def test_demographic_stability_and_growth_drag():
    # Youth bulge: 65% under 30 with 30% unemployment -> Unrest risk
    res_youth = evaluate_demographic_stability_and_growth_drag(
        median_age=22.0,
        youth_under_30_pct=65.0,
        youth_unemployment_pct=30.0,
        old_age_dependency_ratio=10.0,
    )
    assert res_youth["demographic_profile"] == "YOUTH_BULGE_ELEVATED_CIVIL_UNREST_RISK"

    # Aging society: Median age 48, dependency 45 -> Growth drag
    res_aging = evaluate_demographic_stability_and_growth_drag(
        median_age=48.0,
        youth_under_30_pct=25.0,
        youth_unemployment_pct=8.0,
        old_age_dependency_ratio=45.0,
    )
    assert res_aging["demographic_profile"] == "AGING_PYRAMID_STRUCTURAL_LABOR_CONTRACTION"


def test_synthesize_multi_domain_geopolitical_prediction():
    domain_assessments = {
        "history": {"conflict_probability": 0.85},
        "military": {"breach_probability": 0.80},
        "geography": {"geographic_vulnerability_score": 75.0},
        "energy": {"oil_risk_premium_dollar": 35.0},
    }
    synthesis = synthesize_multi_domain_geopolitical_prediction(domain_assessments)
    assert synthesis["composite_conflict_risk"] >= 0.70
    assert synthesis["synthesized_forecast"] == "PROLONGED_HIGH_INTENSITY_ATTRITIONAL_CONFLICT"
    assert "LONG_GOLD_USD_DEFENSE" in synthesis["projected_macro_impacts"]["safe_havens"]
