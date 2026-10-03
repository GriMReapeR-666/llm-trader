"""Unit tests for Module 04: Economic Sanctions, Financial Plumbing & Evasion Forensics."""
import pytest

from intelligence.sanctions_financial_plumbing_and_evasion import (
    evaluate_ofac_fifty_percent_beneficial_ownership,
    screen_swift_financial_message,
    evaluate_sovereign_reserve_freeze_exposure,
    evaluate_g7_oil_price_cap_attestation,
    detect_shadow_fleet_ais_dark_activity,
    evaluate_semiconductor_evasion_tier_network,
    calculate_bma_sanctions_effectiveness_probability,
)


def test_ofac_fifty_percent_beneficial_ownership_direct_and_indirect():
    sdn_list = ["SDN_OLIGARCH_A", "SDN_CORP_B"]

    # Graph with direct, aggregate, and indirect layers:
    # 1. Company_X: 60% owned by SDN_OLIGARCH_A -> Blocked
    # 2. Company_Y: 30% owned by SDN_OLIGARCH_A, 25% owned by SDN_CORP_B -> Total 55% -> Blocked
    # 3. Holding_Z: 100% owned by Company_X -> Blocked (controlled by blocked parent)
    # 4. Clean_Co: 20% owned by SDN_OLIGARCH_A -> 20% < 50% -> Clean
    ownership_graph = {
        "COMPANY_X": {"SDN_OLIGARCH_A": 60.0, "CLEAN_INVESTOR": 40.0},
        "COMPANY_Y": {"SDN_OLIGARCH_A": 30.0, "SDN_CORP_B": 25.0, "CLEAN_INVESTOR": 45.0},
        "HOLDING_Z": {"COMPANY_X": 100.0},
        "CLEAN_CO": {"SDN_OLIGARCH_A": 20.0, "CLEAN_INVESTOR": 80.0},
    }

    res_x = evaluate_ofac_fifty_percent_beneficial_ownership(ownership_graph, sdn_list, "COMPANY_X")
    assert res_x["is_blocked_by_fifty_percent_rule"] is True
    assert res_x["legal_status"] == "BLOCKED_SDN_AFFILIATE"

    res_y = evaluate_ofac_fifty_percent_beneficial_ownership(ownership_graph, sdn_list, "COMPANY_Y")
    assert res_y["is_blocked_by_fifty_percent_rule"] is True

    res_z = evaluate_ofac_fifty_percent_beneficial_ownership(ownership_graph, sdn_list, "HOLDING_Z")
    assert res_z["is_blocked_by_fifty_percent_rule"] is True

    res_clean = evaluate_ofac_fifty_percent_beneficial_ownership(ownership_graph, sdn_list, "CLEAN_CO")
    assert res_clean["is_blocked_by_fifty_percent_rule"] is False
    assert res_clean["legal_status"] == "UNBLOCKED_CLEAN"


def test_screen_swift_financial_message():
    sdn_registry = ["VTB_BANK", "SOVCOMFLOT", "ALROSA"]

    # MT103 clean transfer
    msg_clean = {
        "50a_ordering_customer": "SIEMENS_AG_BERLIN",
        "59a_beneficiary_customer": "TOYOTA_MOTOR_CORP_TOKYO",
        "70_remittance_information": "INDUSTRIAL_PUMP_INVOICE_123",
    }
    res_clean = screen_swift_financial_message("MT103", msg_clean, sdn_registry)
    assert res_clean["is_sanctions_hit"] is False
    assert res_clean["compliance_decision"] == "CLEAN_PROCEED_TO_SETTLEMENT"

    # MT202 COV with hidden SDN in Sequence B (Underlying Customer)
    msg_cov = {
        "seq_a_ordering_institution": "DEUTSCHE_BANK_FRANKFURT",
        "seq_a_beneficiary_institution": "JPMORGAN_CHASE_NY",
        "seq_b_ordering_customer": "TRADING_HOUSE_DUBAI",
        "seq_b_beneficiary_customer": "SOVCOMFLOT_LOGISTICS",  # Hit!
        "seq_b_remittance_information": "CRUDE_FREIGHT_CHARTER",
    }
    res_cov = screen_swift_financial_message("MT202_COV", msg_cov, sdn_registry)
    assert res_cov["is_sanctions_hit"] is True
    assert "SOVCOMFLOT" in res_cov["flagged_sdn_entities"]
    assert res_cov["compliance_decision"] == "BLOCK_AND_FREEZE_PAYMENT"


def test_evaluate_sovereign_reserve_freeze_exposure():
    # $600B total reserves: $300B frozen in Euroclear/FRBNY, $300B in Moscow Gold and PBOC Yuan
    reserves = {
        "EUROCLEAR_BELGIUM": 180.0,
        "CLEARSTREAM_LUXEMBOURG": 40.0,
        "FRBNY_NEW_YORK": 80.0,
        "MOSCOW_PHYSICAL_GOLD": 140.0,
        "PBOC_YUAN_BONDS": 160.0,
    }
    unfriendly = ["BELGIUM", "LUXEMBOURG", "NEW_YORK", "FRBNY"]

    res = evaluate_sovereign_reserve_freeze_exposure(
        reserve_breakdown_usd=reserves,
        sovereign_jurisdictions_unfriendly=unfriendly,
        monthly_import_bill_usd=25.0,
    )
    assert pytest.approx(res["total_reserves_usd_billion"], 1e-2) == 600.0
    assert pytest.approx(res["frozen_reserves_usd_billion"], 1e-2) == 300.0
    assert pytest.approx(res["accessible_reserves_usd_billion"], 1e-2) == 300.0
    assert pytest.approx(res["freeze_ratio_pct"], 1e-2) == 50.0
    assert res["post_freeze_import_cover_months"] == 12.0


def test_evaluate_g7_oil_price_cap_attestation():
    # Contract at $58/bbl (under $60 cap) but with $20/bbl kickback markup -> True price $78 > $60
    res_evasion = evaluate_g7_oil_price_cap_attestation(
        contract_oil_price_bbl=58.0,
        price_cap_threshold_bbl=60.0,
        maritime_services_provider_jurisdiction="G7",
        shipping_kickback_markup_bbl=20.0,
    )
    assert res_evasion["is_sanctions_violation"] is True
    assert res_evasion["violation_reason"] == "INFLATED_SHIPPING_KICKBACK_EVASION"
    assert res_evasion["compliance_action"] == "DENY_MARITIME_INSURANCE_AND_REPORT"

    # Clean transaction at $55/bbl with $3/bbl normal shipping
    res_clean = evaluate_g7_oil_price_cap_attestation(
        contract_oil_price_bbl=55.0,
        price_cap_threshold_bbl=60.0,
        maritime_services_provider_jurisdiction="G7",
        shipping_kickback_markup_bbl=3.0,
    )
    assert res_clean["is_sanctions_violation"] is False
    assert res_clean["compliance_action"] == "ISSUE_PORT_CLEARANCE"


def test_detect_shadow_fleet_ais_dark_activity():
    # Tanker dark for 12 hours with SAR radar detecting 250m hull and non-Western insurance -> Shadow fleet
    res_dark = detect_shadow_fleet_ais_dark_activity(
        ais_last_ping_hours_ago=12.0,
        reported_speed_knots=0.5,
        sar_radar_detected_length_m=250.0,
        insurance_provider="Ingosstrakh_Moscow",
    )
    assert res_dark["is_ais_transponder_dark"] is True
    assert res_dark["transponder_spoofing_detected"] is True
    assert res_dark["vessel_classification"] == "SUSPECTED_SHADOW_FLEET_DARK_TANKER"
    assert res_dark["shadow_fleet_risk_score"] >= 70.0


def test_evaluate_semiconductor_evasion_tier_network():
    # 4-tier chain via UAE free zone with ECCN item and military end-use fusion -> Red flag
    res = evaluate_semiconductor_evasion_tier_network(
        intermediary_jurisdiction="UAE_JEBEL_ALI",
        entity_chain_depth=4,
        end_use_military_fusion=True,
        contains_eccn_controlled_item=True,
    )
    assert res["is_high_risk_transit_hub"] is True
    assert res["bis_license_required"] is True
    assert res["enforcement_action"] == "RED_FLAG_SEIZE_AND_INVESTIGATE"


def test_calculate_bma_sanctions_effectiveness_probability():
    # Target is high autocracy (80), has 70% alternative trade with China/India, 12 months import cover, no UN consensus
    res_resilient = calculate_bma_sanctions_effectiveness_probability(
        autocracy_score=80.0,
        alternative_trade_partners_share=70.0,
        fx_reserve_import_cover_months=12.0,
        multilateral_un_consensus=False,
    )
    assert res_resilient["composite_sanctions_success_probability"] < 0.45
    assert res_resilient["target_adaptation_equilibrium"] == "SUSTAINABLE_SHADOW_ECONOMY"
