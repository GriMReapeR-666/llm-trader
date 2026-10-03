"""Module 04: Economic Sanctions, Financial Plumbing & Evasion Forensics.

Implements institutional sanctions screening, financial plumbing forensics, and predictive effectiveness models:
1. OFAC 50% Rule Recursive Beneficial Ownership Graph Traversal:
       Entity blocked if sum_{i in SDN} Share_i >= 50% or BlockedParent ->(>=50%) Subsidiary
2. SWIFT MT103, MT202 COV (Sequence A & B), MT700, and ISO 20022 (pacs.008) Screening Engine
3. Sovereign Reserve Freezes & Custody Chains (Euroclear, Clearstream, FRBNY vs. CIPS/Gold)
4. G7 Oil Price Cap Attestation Mechanics & Inflated Shipping Kickback Evasion Detector
5. Shadow Fleet Vessel AIS Dark Activity & Mid-Ocean STS Forensics
6. Technology Export Controls: EAR vs. ITAR, 6-Tier Semiconductor Evasion Networks & BIS 50% Rule
7. BMA-Weighted Predictive Model of Sanctions Effectiveness:
       P(Success) = sum_i w_i * sigmoid(beta_i^T * x_target)
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import numpy as np


def evaluate_ofac_fifty_percent_beneficial_ownership(
    ownership_graph: Dict[str, Dict[str, float]],
    sdn_list: Sequence[str],
    target_entity: str,
) -> Dict[str, Any]:
    """Evaluates the OFAC 50% Beneficial Ownership Rule using recursive graph traversal.
    An entity is blocked if:
    1. Direct SDN ownership sum >= 50.0%
    2. Indirect: An upstream parent entity that is blocked owns >= 50.0% of the target entity.
    """
    sdn_set = {str(s).strip().upper() for s in sdn_list}
    target = str(target_entity).strip().upper()

    # Cache for memoizing blocked status of entities
    blocked_cache: Dict[str, bool] = {}

    def is_entity_blocked(entity: str, visiting: Optional[Set[str]] = None) -> bool:
        if visiting is None:
            visiting = set()

        ent = entity.strip().upper()
        if ent in sdn_set:
            return True
        if ent in blocked_cache:
            return blocked_cache[ent]
        if ent in visiting:
            return False  # Break cyclic dependency

        visiting.add(ent)
        shareholders = ownership_graph.get(ent, {})

        total_blocked_equity = 0.0
        for owner, share_pct in shareholders.items():
            owner_upper = owner.strip().upper()
            share = float(share_pct)

            if is_entity_blocked(owner_upper, visiting.copy()):
                total_blocked_equity += share

        is_blocked = total_blocked_equity >= 50.0
        blocked_cache[ent] = is_blocked
        return is_blocked

    is_target_blocked = is_entity_blocked(target)

    # Detailed breakdown of direct shareholders
    direct_shareholders = ownership_graph.get(target, {})
    blocked_direct_equity = sum(
        float(share) for owner, share in direct_shareholders.items()
        if is_entity_blocked(owner)
    )

    return {
        "target_entity": target,
        "is_blocked_by_fifty_percent_rule": bool(is_target_blocked),
        "total_blocked_beneficial_equity_pct": round(float(blocked_direct_equity), 2),
        "legal_status": "BLOCKED_SDN_AFFILIATE" if is_target_blocked else "UNBLOCKED_CLEAN",
        "compliance_action": "FREEZE_ASSETS_AND_REJECT_TRANSACTION" if is_target_blocked else "STANDARD_DUE_DILIGENCE",
    }


def screen_swift_financial_message(
    message_type: str,
    raw_message_payload: Dict[str, Any],
    sdn_sanctions_registry: Sequence[str],
) -> Dict[str, Any]:
    """Screens SWIFT financial messages (MT103, MT202 COV, MT700, ISO 20022 pacs.008) against OFAC SDN list."""
    msg_type = str(message_type).strip().upper()
    sdn_set = {str(s).strip().upper() for s in sdn_sanctions_registry}

    flagged_parties = []
    matched_fields = []

    def check_text_against_sdn(text_val: Any, field_name: str):
        if not text_val:
            return
        text_str = str(text_val).upper()
        for sdn in sdn_set:
            if sdn in text_str:
                flagged_parties.append(sdn)
                matched_fields.append(field_name)

    if msg_type == "MT103":
        check_text_against_sdn(raw_message_payload.get("50a_ordering_customer"), ":50a")
        check_text_against_sdn(raw_message_payload.get("59a_beneficiary_customer"), ":59a")
        check_text_against_sdn(raw_message_payload.get("70_remittance_information"), ":70")

    elif msg_type in ("MT202_COV", "MT202COV"):
        # Sequence A (Interbank)
        check_text_against_sdn(raw_message_payload.get("seq_a_ordering_institution"), "SeqA:52")
        check_text_against_sdn(raw_message_payload.get("seq_a_beneficiary_institution"), "SeqA:58")
        # Sequence B (Underlying Customer Transfer - Critical for covert routing)
        check_text_against_sdn(raw_message_payload.get("seq_b_ordering_customer"), "SeqB:50a")
        check_text_against_sdn(raw_message_payload.get("seq_b_beneficiary_customer"), "SeqB:59a")
        check_text_against_sdn(raw_message_payload.get("seq_b_remittance_information"), "SeqB:70")

    elif msg_type == "MT700":
        check_text_against_sdn(raw_message_payload.get("50_applicant"), ":50")
        check_text_against_sdn(raw_message_payload.get("59_beneficiary"), ":59")
        check_text_against_sdn(raw_message_payload.get("45A_description_of_goods"), ":45A")
        check_text_against_sdn(raw_message_payload.get("44E_port_of_loading"), ":44E")
        check_text_against_sdn(raw_message_payload.get("44F_port_of_discharge"), ":44F")

    elif "ISO" in msg_type or "PACS" in msg_type:
        check_text_against_sdn(raw_message_payload.get("dbtr_debtor"), "<Dbtr>")
        check_text_against_sdn(raw_message_payload.get("cdtr_creditor"), "<Cdtr>")
        check_text_against_sdn(raw_message_payload.get("ultmt_cdtr_ultimate_creditor"), "<UltmtCdtr>")
        check_text_against_sdn(raw_message_payload.get("prtry_proprietary_info"), "<Prtry>")

    else:
        for k, v in raw_message_payload.items():
            check_text_against_sdn(v, k)

    is_hit = len(flagged_parties) > 0

    return {
        "message_type": msg_type,
        "is_sanctions_hit": bool(is_hit),
        "flagged_sdn_entities": list(set(flagged_parties)),
        "matched_fields": list(set(matched_fields)),
        "compliance_decision": "BLOCK_AND_FREEZE_PAYMENT" if is_hit else "CLEAN_PROCEED_TO_SETTLEMENT",
        "ofac_reporting_required": bool(is_hit),
    }


def evaluate_sovereign_reserve_freeze_exposure(
    reserve_breakdown_usd: Dict[str, float],
    sovereign_jurisdictions_unfriendly: Sequence[str],
    monthly_import_bill_usd: float = 25.0,
) -> Dict[str, Any]:
    """Calculates custodial sovereign reserve freeze exposure and post-freeze import cover months."""
    unfriendly_set = {str(j).strip().upper() for j in sovereign_jurisdictions_unfriendly}
    monthly_import = max(1e-6, float(monthly_import_bill_usd))

    total_reserves = 0.0
    frozen_reserves = 0.0
    accessible_reserves = 0.0

    custody_breakdown = {}

    for jurisdiction, amount in reserve_breakdown_usd.items():
        jur_upper = str(jurisdiction).strip().upper()
        amt = float(amount)
        total_reserves += amt

        is_frozen = any(unf in jur_upper for unf in unfriendly_set) or "EUROCLEAR" in jur_upper or "CLEARSTREAM" in jur_upper or "FRBNY" in jur_upper
        if is_frozen:
            frozen_reserves += amt
            status = "FROZEN_IMMOBILIZED"
        else:
            accessible_reserves += amt
            status = "ACCESSIBLE_BUFFER"

        custody_breakdown[jur_upper] = {
            "amount_usd_billion": amt,
            "status": status,
        }

    pre_freeze_import_cover = total_reserves / monthly_import
    post_freeze_import_cover = accessible_reserves / monthly_import
    freeze_ratio_pct = (frozen_reserves / max(1e-6, total_reserves)) * 100.0

    return {
        "total_reserves_usd_billion": round(total_reserves, 2),
        "frozen_reserves_usd_billion": round(frozen_reserves, 2),
        "accessible_reserves_usd_billion": round(accessible_reserves, 2),
        "freeze_ratio_pct": round(freeze_ratio_pct, 2),
        "pre_freeze_import_cover_months": round(pre_freeze_import_cover, 2),
        "post_freeze_import_cover_months": round(post_freeze_import_cover, 2),
        "sovereign_liquidity_crisis_risk": "CRITICAL" if post_freeze_import_cover < 3.0 else ("ELEVATED" if post_freeze_import_cover < 6.0 else "MANAGED"),
        "custody_details": custody_breakdown,
    }


def evaluate_g7_oil_price_cap_attestation(
    contract_oil_price_bbl: float,
    price_cap_threshold_bbl: float = 60.0,
    maritime_services_provider_jurisdiction: str = "G7",
    tier_level: int = 1,
    shipping_kickback_markup_bbl: float = 0.0,
) -> Dict[str, Any]:
    """Validates G7 Oil Price Cap Attestation Chain and detects inflated shipping kickback evasion."""
    p_contract = float(contract_oil_price_bbl)
    p_cap = float(price_cap_threshold_bbl)
    p_markup = float(shipping_kickback_markup_bbl)
    is_g7_service = str(maritime_services_provider_jurisdiction).upper() in ("G7", "EU", "UK", "US", "JAPAN")

    p_true_delivered = p_contract + p_markup
    is_evasion = False
    violation_reason = "NONE"

    if is_g7_service:
        if p_contract > p_cap:
            is_evasion = True
            violation_reason = "CONTRACT_PRICE_EXCEEDS_CAP"
        elif p_true_delivered > p_cap and p_markup >= 15.0:
            is_evasion = True
            violation_reason = "INFLATED_SHIPPING_KICKBACK_EVASION"

    return {
        "contract_oil_price_bbl": p_contract,
        "price_cap_threshold_bbl": p_cap,
        "shipping_kickback_markup_bbl": p_markup,
        "true_delivered_price_bbl": round(p_true_delivered, 2),
        "is_g7_maritime_service_used": is_g7_service,
        "attestation_tier": int(tier_level),
        "is_sanctions_violation": bool(is_evasion),
        "violation_reason": violation_reason,
        "compliance_action": "DENY_MARITIME_INSURANCE_AND_REPORT" if is_evasion else "ISSUE_PORT_CLEARANCE",
    }


def detect_shadow_fleet_ais_dark_activity(
    ais_last_ping_hours_ago: float,
    reported_speed_knots: float,
    sar_radar_detected_length_m: Optional[float] = None,
    insurance_provider: str = "Western_P_and_I",
) -> Dict[str, Any]:
    """Detects maritime shadow fleet AIS dark activity, transponder spoofing, and obscure insurance."""
    ping_delay = float(ais_last_ping_hours_ago)
    speed = float(reported_speed_knots)
    insurance = str(insurance_provider).upper()

    is_western_insurance = any(w in insurance for w in ("WESTERN", "IG", "INTERNATIONAL_GROUP", "UK_CLUB", "GARD"))
    is_dark = ping_delay >= 6.0

    spoofing_detected = False
    if sar_radar_detected_length_m is not None and sar_radar_detected_length_m >= 150.0 and is_dark:
        spoofing_detected = True

    risk_score = 0.0
    if is_dark:
        risk_score += 45.0
    if not is_western_insurance:
        risk_score += 35.0
    if spoofing_detected:
        risk_score += 20.0

    return {
        "ais_last_ping_hours_ago": ping_delay,
        "is_ais_transponder_dark": bool(is_dark),
        "sar_radar_hull_detected": sar_radar_detected_length_m is not None,
        "transponder_spoofing_detected": bool(spoofing_detected),
        "insurance_provider": insurance,
        "shadow_fleet_risk_score": float(min(100.0, risk_score)),
        "vessel_classification": "SUSPECTED_SHADOW_FLEET_DARK_TANKER" if risk_score >= 70.0 else ("MONITOR" if risk_score >= 40.0 else "LEGITIMATE_COMMERCIAL_VESSEL"),
    }


def evaluate_semiconductor_evasion_tier_network(
    intermediary_jurisdiction: str,
    entity_chain_depth: int,
    end_use_military_fusion: bool,
    contains_eccn_controlled_item: bool,
) -> Dict[str, Any]:
    """Evaluates 6-Tier semiconductor evasion network and export control liability under EAR and BIS 50% rule."""
    jur = str(intermediary_jurisdiction).upper()
    depth = max(1, int(entity_chain_depth))
    is_mil = bool(end_use_military_fusion)
    is_eccn = bool(contains_eccn_controlled_item)

    high_risk_jurisdictions = {"UAE", "HONG_KONG", "TURKEY", "MALAYSIA", "KAZAKHSTAN", "ARMENIA", "SERBIA"}
    is_high_risk_transit = any(h in jur for h in high_risk_jurisdictions)

    evasion_risk = 0.0
    if is_high_risk_transit:
        evasion_risk += 35.0
    if depth >= 3:
        evasion_risk += 25.0
    if is_mil:
        evasion_risk += 25.0
    if is_eccn:
        evasion_risk += 15.0

    return {
        "intermediary_jurisdiction": jur,
        "entity_chain_depth_tier": depth,
        "is_high_risk_transit_hub": bool(is_high_risk_transit),
        "is_eccn_controlled_item": is_eccn,
        "is_military_end_use_fusion": is_mil,
        "evasion_network_risk_score": float(min(100.0, evasion_risk)),
        "bis_license_required": bool(is_eccn or is_mil or evasion_risk >= 50.0),
        "enforcement_action": "RED_FLAG_SEIZE_AND_INVESTIGATE" if evasion_risk >= 70.0 else "ENHANCED_END_USER_VERIFICATION",
    }


def calculate_bma_sanctions_effectiveness_probability(
    autocracy_score: float,
    alternative_trade_partners_share: float,
    fx_reserve_import_cover_months: float,
    multilateral_un_consensus: bool,
    model_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Calculates Bayesian Model Averaged (BMA) probability P(Success) of sanctions forcing policy change."""
    autoc = max(0.0, min(100.0, float(autocracy_score)))
    alt_trade = max(0.0, min(100.0, float(alternative_trade_partners_share)))
    import_cov = max(0.0, float(fx_reserve_import_cover_months))
    un_cons = bool(multilateral_un_consensus)

    weights = model_weights or {
        "economic_coercion": 0.20,
        "financial_weaponization": 0.25,
        "technology_chokepoints": 0.30,
        "multilateral_legitimacy": 0.15,
        "domestic_regime_type": 0.10,
    }

    # Model A: Economic Coercion (Pain on GDP)
    p_econ = max(0.05, 1.0 - (alt_trade / 100.0))

    # Model B: Financial Weaponization (SWIFT/USD isolation)
    p_fin = 0.85 if import_cov < 3.0 else max(0.10, 1.0 / (1.0 + math.exp(0.5 * (import_cov - 6.0))))

    # Model C: Technology Chokepoints (Degrades industrial/military capacity)
    p_tech = 0.70  # Long-term attrition is consistently potent

    # Model D: Multilateral Legitimacy (Prevents leakage)
    p_multi = 0.90 if un_cons else 0.25

    # Model E: Domestic Regime Type (Autocracies repress and resist; democracies comply)
    p_regime = max(0.05, 1.0 - (autoc / 100.0))

    p_success = (
        weights["economic_coercion"] * p_econ +
        weights["financial_weaponization"] * p_fin +
        weights["technology_chokepoints"] * p_tech +
        weights["multilateral_legitimacy"] * p_multi +
        weights["domestic_regime_type"] * p_regime
    )

    return {
        "composite_sanctions_success_probability": round(float(p_success), 4),
        "model_probabilities": {
            "economic_coercion": round(float(p_econ), 4),
            "financial_weaponization": round(float(p_fin), 4),
            "technology_chokepoints": round(float(p_tech), 4),
            "multilateral_legitimacy": round(float(p_multi), 4),
            "domestic_regime_type": round(float(p_regime), 4),
        },
        "target_adaptation_equilibrium": "SUSTAINABLE_SHADOW_ECONOMY" if p_success < 0.40 else ("VULNERABLE_TO_CONCESSIONS" if p_success >= 0.65 else "ATTRITIONAL_STANDOFF"),
    }
