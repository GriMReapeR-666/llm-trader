"""Module 03: Multi-Disciplinary Domain Knowledge Compendium & Causal Case Studies.

Implements the 12 Foundational Knowledge Pillars and Causal Dynamics:
1. History & Patterns: Thucydides Trap Parity Risk
2. Political Systems: Regime Institutional Stability & Succession Cliff
3. International Relations: Structural Realism Security Dilemma
4. Military & Security: Doctrinal Force Ratios (3:1 maneuver, 6:1 urban) & A2/AD
5. Macroeconomic Structures: Dutch Disease & Resource Curse Causal Cascade
6. Geography & Geostrategy: Warm-Water Port Constraints & Buffer Geopolitics
7. Energy & Commodities: Haber-Bosch Stoichiometry & OPEC+ Spare Capacity
8. Technology & Semis: ASML Lithography & Sub-5nm Fabrication Chokepoints
9. Financial Plumbing: Cross-Currency Basis Swaps & Offshore USD Liquidity
10. Demographics: Youth Bulge Unrest vs. Aging Pyramid Fiscal Drag
11. Culture & Identity: Nationalist Grievance Escalation Multiplier
12. Multi-Domain Pre-Invasion Prediction Synthesis Engine
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np


def evaluate_thucydides_trap_parity_risk(
    gdp_rising_power: float,
    gdp_incumbent_power: float,
    historical_conflict_base_rate: float = 0.75,
) -> Dict[str, Any]:
    """Evaluates Thucydides Trap power transition friction:
    Parity Ratio R = GDP_rising / GDP_incumbent
    Transition friction occurs when 0.80 <= R <= 1.20 (12 of 16 historical cases ended in war).
    """
    gdp_rise = max(1e-6, float(gdp_rising_power))
    gdp_inc = max(1e-6, float(gdp_incumbent_power))
    base_rate = max(0.0, min(1.0, float(historical_conflict_base_rate)))

    parity_ratio = gdp_rise / gdp_inc
    in_trap_zone = 0.80 <= parity_ratio <= 1.20

    if in_trap_zone:
        # Distance to perfect parity (1.0) determines intensity peak
        distance_to_parity = abs(parity_ratio - 1.0)
        conflict_prob = base_rate + (0.90 - base_rate) * (1.0 - (distance_to_parity / 0.20))
        status = "HIGH_THUCYDIDES_TRANSITION_RISK"
    elif parity_ratio < 0.80:
        conflict_prob = 0.25 * (parity_ratio / 0.80)
        status = "RISING_POWER_SUBORDINATE"
    else:  # parity_ratio > 1.20
        conflict_prob = 0.30
        status = "POWER_TRANSITION_SUBSTANTIALLY_COMPLETE"

    return {
        "parity_ratio": round(float(parity_ratio), 4),
        "in_thucydides_zone": bool(in_trap_zone),
        "conflict_probability": round(float(conflict_prob), 4),
        "status": status,
        "historical_base_rate": base_rate,
    }


def evaluate_regime_institutional_stability(
    regime_type: str,
    years_in_power: float,
    institutional_checks_score: float,
    succession_mechanism_defined: bool,
) -> Dict[str, Any]:
    """Evaluates regime stability, policy continuity, and succession risk.
    - Personalist Autocracy: Low daily policy volatility, extreme structural succession cliff.
    - Proportional Democracy: High coalition friction, low tail succession risk.
    """
    reg_norm = str(regime_type).upper()
    years = max(0.0, float(years_in_power))
    checks = max(0.0, min(100.0, float(institutional_checks_score)))
    has_succession = bool(succession_mechanism_defined)

    if "PERSONALIST" in reg_norm or "AUTOCRACY" in reg_norm:
        daily_volatility = "LOW"
        succession_risk = "CRITICAL" if not has_succession or years >= 15 else "ELEVATED"
        policy_reversibility = "VERY_LOW"
        composite_tail_risk = 85.0 if not has_succession else 60.0
    elif "PROPORTIONAL" in reg_norm or "PARLIAMENTARY" in reg_norm:
        daily_volatility = "ELEVATED_COALITION_GRIDLOCK"
        succession_risk = "LOW"
        policy_reversibility = "HIGH"
        composite_tail_risk = 25.0
    else:  # Presidential Democracy / Hybrid
        daily_volatility = "MODERATE"
        succession_risk = "LOW" if has_succession else "MODERATE"
        policy_reversibility = "MODERATE"
        composite_tail_risk = 40.0

    return {
        "regime_type": reg_norm,
        "years_in_power": years,
        "institutional_checks": checks,
        "has_defined_succession": has_succession,
        "daily_policy_volatility": daily_volatility,
        "succession_cliff_risk": succession_risk,
        "policy_reversibility": policy_reversibility,
        "composite_tail_risk_score": float(composite_tail_risk),
    }


def calculate_structural_realism_security_dilemma(
    offensive_capabilities_score: float,
    defensive_intent_clarity: float,
    geographical_proximity: float,
) -> Dict[str, Any]:
    """Calculates Security Dilemma escalation metric under Structural Realism:
    S_dilemma = (Capabilities * Proximity) / max(0.1, Intent Clarity)
    """
    cap = max(0.0, min(100.0, float(offensive_capabilities_score)))
    intent = max(0.1, min(100.0, float(defensive_intent_clarity)))
    prox = max(0.0, min(100.0, float(geographical_proximity)))

    raw_score = (cap * prox) / intent
    normalized_score = float(min(100.0, raw_score / 10.0))

    if normalized_score >= 70.0:
        state = "INTENSE_SECURITY_DILEMMA_SPIRAL"
        action = "ACCELERATING_ARMS_RACE_AND_ALLIANCE_FORMATION"
    elif normalized_score >= 40.0:
        state = "MODERATE_SECURITY_FRICTION"
        action = "HEDGED_OFFSHORE_BALANCING"
    else:
        state = "BENIGN_SECURITY_ENVIRONMENT"
        action = "COOPERATIVE_TRADE_INTEGRATION"

    return {
        "dilemma_score": round(normalized_score, 2),
        "security_state": state,
        "strategic_implication": action,
    }


def calculate_military_combat_force_ratio_advantage(
    attacker_troop_strength: float,
    defender_troop_strength: float,
    terrain_type: str = "OPEN_MANEUVER",
    defender_fortification_level: float = 1.0,
) -> Dict[str, Any]:
    """Calculates doctrinal military force ratio and combat breach probability:
    - Open Maneuver: Requires >= 3.0 : 1 attacker advantage.
    - Fortified Urban Attrition: Requires >= 6.0 : 1 attacker advantage.
    """
    att = max(1.0, float(attacker_troop_strength))
    dfn = max(1.0, float(defender_troop_strength))
    fort = max(0.5, min(3.0, float(defender_fortification_level)))
    terrain = str(terrain_type).upper()

    effective_defender = dfn * fort
    force_ratio = att / effective_defender

    if "URBAN" in terrain or "FORTIFIED" in terrain:
        req_ratio = 6.0
    elif "RIVER" in terrain or "SWAMP" in terrain or "MOUNTAIN" in terrain:
        req_ratio = 4.5
    else:  # OPEN_MANEUVER
        req_ratio = 3.0

    breach_prob = float(min(0.98, max(0.02, 1.0 / (1.0 + math.exp(-1.5 * (force_ratio - req_ratio))))))

    return {
        "attacker_strength": att,
        "effective_defender_strength": effective_defender,
        "realized_force_ratio": round(float(force_ratio), 2),
        "doctrinal_required_ratio": req_ratio,
        "breach_probability": round(breach_prob, 4),
        "is_sufficient_for_offensive": force_ratio >= req_ratio,
        "expected_conflict_duration": "RAPID_MANEUVER" if force_ratio >= req_ratio * 1.5 else "PROLONGED_ATTRITIONAL_WAR",
    }


def evaluate_dutch_disease_resource_curse_dynamics(
    resource_export_share_gdp: float,
    currency_reer_appreciation_pct: float,
    manufacturing_pmi: float,
    fiscal_oil_revenue_pct: float,
) -> Dict[str, Any]:
    """Evaluates the Dutch Disease and Resource Curse causal transmission cascade."""
    res_share = float(resource_export_share_gdp)
    reer_apprec = float(currency_reer_appreciation_pct)
    mfg_pmi = float(manufacturing_pmi)
    fiscal_oil = float(fiscal_oil_revenue_pct)

    is_dutch_disease = (res_share >= 20.0 or fiscal_oil >= 40.0) and (reer_apprec >= 10.0 or mfg_pmi < 48.0)

    vulnerability_score = (res_share * 0.35) + (fiscal_oil * 0.45) + max(0.0, (50.0 - mfg_pmi) * 2.0)
    vulnerability_score = float(min(100.0, max(0.0, vulnerability_score)))

    return {
        "is_dutch_disease_active": bool(is_dutch_disease),
        "resource_vulnerability_score": round(vulnerability_score, 2),
        "transmission_stage": "ACUTE_DE_INDUSTRIALIZATION" if is_dutch_disease and mfg_pmi < 45.0 else ("BALANCED" if vulnerability_score < 40.0 else "FISCAL_COMMODITY_DEPENDENCE"),
        "recession_risk_on_commodity_bust": "CRITICAL" if fiscal_oil >= 50.0 else ("ELEVATED" if fiscal_oil >= 30.0 else "LOW"),
    }


def calculate_geostrategic_buffer_and_chokepoint_vulnerability(
    distance_to_open_ocean_nm: float,
    has_warm_water_port: bool,
    nato_perimeter_encirclement_pct: float,
    chokepoint_transit_dependency_pct: float,
) -> Dict[str, Any]:
    """Models permanent geographic constraints, expansion compulsion, and defense crowding-out."""
    dist = max(0.0, float(distance_to_open_ocean_nm))
    has_port = bool(has_warm_water_port)
    encirclement = max(0.0, min(100.0, float(nato_perimeter_encirclement_pct)))
    choke_dep = max(0.0, min(100.0, float(chokepoint_transit_dependency_pct)))

    geographic_vulnerability = (0.40 * encirclement) + (0.35 * choke_dep) + (25.0 if not has_port else 0.0)
    geographic_vulnerability = float(min(100.0, geographic_vulnerability))

    expansion_compulsion = "AGGRESSIVE_BUFFER_SEEKING" if not has_port or encirclement >= 60.0 else "STATUS_QUO"

    return {
        "geographic_vulnerability_score": round(geographic_vulnerability, 2),
        "has_warm_water_port": has_port,
        "expansion_compulsion": expansion_compulsion,
        "structural_defense_gdp_burden_pct": round(4.5 if not has_port else 2.0, 1),
    }


def calculate_haber_bosch_fertilizer_cost_floor(
    natural_gas_price_mmbtu: float,
    base_operating_cost_per_ton: float = 65.0,
    mmbtu_per_ton_nh3: float = 33.5,
) -> float:
    """Calculates synthetic nitrogen ammonia fertilizer price floor:
    P_Ammonia = (33.5 * P_NatGas) + $65.00/ton
    """
    gas_p = max(0.1, float(natural_gas_price_mmbtu))
    base_op = float(base_operating_cost_per_ton)
    stoich = float(mmbtu_per_ton_nh3)

    return float(stoich * gas_p + base_op)


def calculate_opec_spare_capacity_price_elasticity(
    spare_capacity_mbpd: float,
    global_demand_mbpd: float = 102.0,
) -> Dict[str, Any]:
    """Calculates oil price risk premium based on OPEC+ spare capacity buffer.
    When spare capacity < 2.0M bbl/d (~2% of demand), risk premium surges exponentially.
    """
    spare = max(0.0, float(spare_capacity_mbpd))
    demand = max(1.0, float(global_demand_mbpd))
    buffer_pct = (spare / demand) * 100.0

    if spare < 2.0:
        risk_premium_dollar = float(math.exp((2.0 - spare) * 1.5) * 15.0)
        regime = "CRITICAL_SPARE_CAPACITY_DEFICIT"
    elif spare < 3.5:
        risk_premium_dollar = float((3.5 - spare) * 5.0)
        regime = "NORMAL_TIGHT_BUFFER"
    else:
        risk_premium_dollar = 0.0
        regime = "AMPLE_SUPPLY_BUFFER"

    return {
        "spare_capacity_mbpd": spare,
        "buffer_pct_of_demand": round(buffer_pct, 2),
        "oil_risk_premium_dollar": round(risk_premium_dollar, 2),
        "market_regime": regime,
    }


def evaluate_semiconductor_chokepoint_exposure(
    advanced_node_nm: float,
    asml_euv_dependency_pct: float = 100.0,
    tsmc_taiwan_fab_share_pct: float = 90.0,
) -> Dict[str, Any]:
    """Evaluates sub-5nm semiconductor lithography single-point-of-failure risk."""
    node = float(advanced_node_nm)
    asml_dep = max(0.0, min(100.0, float(asml_euv_dependency_pct)))
    tsmc_share = max(0.0, min(100.0, float(tsmc_taiwan_fab_share_pct)))

    is_chokepoint = node <= 7.0 and (asml_dep >= 80.0 or tsmc_share >= 80.0)
    tau_substitution_years = 5.0 if node <= 3.0 else (3.0 if node <= 7.0 else 1.0)

    return {
        "advanced_node_nm": node,
        "is_global_single_point_of_failure": bool(is_chokepoint),
        "asml_euv_dependency": asml_dep,
        "tsmc_taiwan_fab_share": tsmc_share,
        "substitution_time_horizon_years": tau_substitution_years,
        "vulnerability_tier": "EXTREME_CHOKEPOINT" if is_chokepoint else "COMMODITIZED_LEGACY_NODE",
    }


def evaluate_cross_currency_basis_liquidity_stress(
    eur_usd_basis_3m_bps: float,
    jpy_usd_basis_3m_bps: float,
    fed_repo_facility_usage_billion: float = 0.0,
) -> Dict[str, Any]:
    """Evaluates global offshore USD funding stress via 3M Cross-Currency Basis Swaps.
    Widening negative basis points indicates severe institutional USD shortage.
    """
    eur_basis = float(eur_usd_basis_3m_bps)
    jpy_basis = float(jpy_usd_basis_3m_bps)
    repo_use = float(fed_repo_facility_usage_billion)

    mean_basis = (eur_basis + jpy_basis) / 2.0

    if mean_basis <= -45.0 or repo_use >= 50.0:
        stress_level = "ACUTE_OFFSHORE_USD_LIQUIDITY_FREEZE"
        action = "ACCUMULATE_USD_CASH_AND_DEFENSIVE_TREASURIES"
    elif mean_basis <= -25.0:
        stress_level = "MODERATE_USD_FUNDING_PRESSURE"
        action = "MONITOR_CROSS_BORDER_INTERBANK_SPREADS"
    else:
        stress_level = "BENIGN_DOLLAR_LIQUIDITY"
        action = "NORMAL_LIQUIDITY"

    return {
        "eur_usd_basis_3m_bps": eur_basis,
        "jpy_usd_basis_3m_bps": jpy_basis,
        "mean_basis_bps": round(mean_basis, 2),
        "fed_repo_usage_billion": repo_use,
        "liquidity_stress_level": stress_level,
        "strategic_action": action,
    }


def evaluate_demographic_stability_and_growth_drag(
    median_age: float,
    youth_under_30_pct: float,
    youth_unemployment_pct: float,
    old_age_dependency_ratio: float,
) -> Dict[str, Any]:
    """Evaluates youth bulge civil unrest risk vs. aging pyramid economic drag."""
    age = float(median_age)
    youth_share = max(0.0, min(100.0, float(youth_under_30_pct)))
    youth_unemp = max(0.0, min(100.0, float(youth_unemployment_pct)))
    old_dep = max(0.0, float(old_age_dependency_ratio))

    youth_unrest_risk = (youth_share >= 60.0 and youth_unemp >= 25.0)
    aging_drag = (age >= 42.0 or old_dep >= 35.0)

    if youth_unrest_risk:
        profile = "YOUTH_BULGE_ELEVATED_CIVIL_UNREST_RISK"
        growth_outlook = "VOLATILE_DOMESTIC_INSTABILITY"
    elif aging_drag:
        profile = "AGING_PYRAMID_STRUCTURAL_LABOR_CONTRACTION"
        growth_outlook = "SUBDUED_LONG_TERM_POTENTIAL_GDP"
    else:
        profile = "DEMOGRAPHIC_DIVIDEND_EQUILIBRIUM"
        growth_outlook = "HEALTHY_EXPANSION"

    return {
        "median_age": age,
        "youth_under_30_pct": youth_share,
        "youth_unemployment_pct": youth_unemp,
        "old_age_dependency_ratio": old_dep,
        "demographic_profile": profile,
        "macro_growth_impact": growth_outlook,
    }


def synthesize_multi_domain_geopolitical_prediction(
    domain_assessments: Dict[str, Any],
) -> Dict[str, Any]:
    """Synthesizes multi-domain findings (History, Military, Geography, Energy, Economics, Politics)
    into an integrated, non-linear macroeconomic forecast.
    """
    hist = domain_assessments.get("history", {})
    mil = domain_assessments.get("military", {})
    geo = domain_assessments.get("geography", {})
    energy = domain_assessments.get("energy", {})
    econ = domain_assessments.get("economics", {})

    conflict_prob = float(hist.get("conflict_probability", 0.50))
    breach_prob = float(mil.get("breach_probability", 0.50))
    geo_vuln = float(geo.get("geographic_vulnerability_score", 50.0))
    risk_prem = float(energy.get("oil_risk_premium_dollar", 0.0))

    composite_conflict_risk = (conflict_prob * 0.30) + (breach_prob * 0.40) + ((geo_vuln / 100.0) * 0.30)
    composite_conflict_risk = float(min(1.0, max(0.0, composite_conflict_risk)))

    if composite_conflict_risk >= 0.70:
        forecast = "PROLONGED_HIGH_INTENSITY_ATTRITIONAL_CONFLICT"
        macro_impact = {
            "crude_oil_bias": f"+${round(risk_prem + 25.0, 1)}/bbl",
            "fertilizer_bias": "SHARP_INCREASE_CORN_TO_SOY_SHIFT",
            "safe_havens": "LONG_GOLD_USD_DEFENSE",
            "equities": "RISK_OFF_DOWNWARD_REVALUATION",
        }
    elif composite_conflict_risk >= 0.40:
        forecast = "STANDOFF_REGIONAL_PROXY_FRICTION"
        macro_impact = {
            "crude_oil_bias": f"+${round(risk_prem, 1)}/bbl",
            "fertilizer_bias": "MODERATE_VOLATILITY",
            "safe_havens": "BALANCED_HEDGED",
            "equities": "SECTOR_ROTATION_DEFENSIVE",
        }
    else:
        forecast = "DIPLOMATIC_CONTAINMENT_STATUS_QUO"
        macro_impact = {
            "crude_oil_bias": "RANGE_BOUND",
            "fertilizer_bias": "STABLE",
            "safe_havens": "NEUTRAL",
            "equities": "NORMAL_BETA",
        }

    return {
        "composite_conflict_risk": round(composite_conflict_risk, 4),
        "synthesized_forecast": forecast,
        "projected_macro_impacts": macro_impact,
    }
