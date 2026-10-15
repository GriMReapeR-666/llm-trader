"""Tests for Institutional Macro Architecture Framework.

Covers:
1. Central Bank Reaction Function Playbooks (FOMC, ECB, BOJ, PBOC)
2. Economic Data Release Playbooks (CPI, NFP, PMI)
3. Geopolitical, Crisis & Shock Playbooks (Terms of Trade, Gold decoupling, Sovereign Debt)
4. Positioning & Market Structure Analysis (COT 3Y Z-score, Dealer Gamma, FX Risk Reversals)
5. Master Cross-Asset Reference Matrix & Three-Window Protocol (6 Master Scenarios, Execution Windows)
"""
from __future__ import annotations

import pytest
import numpy as np

from core.institutional_macro_framework import (
    evaluate_fomc_reaction_function,
    evaluate_fomc_press_conference,
    evaluate_ecb_fragmentation_playbook,
    evaluate_boj_carry_unwind_playbook,
    evaluate_pboc_credit_transmission_playbook,
)


# ==============================================================================
# 1. Central Bank Reaction Functions
# ==============================================================================

def test_fomc_reaction_function_hawkish_hiking_bias():
    # Inflation high (Core PCE 3.5% vs 2% target), unemployment low (3.6% vs 4.0% NAIRU),
    # FCI loosening (-0.5 -> negative delta = loosening, which prompts hawkish tightening)
    res = evaluate_fomc_reaction_function(
        core_pce_inflation=3.5,
        unemployment_rate=3.6,
        fci_delta=-0.5,
        nairu=4.0,
        inflation_target=2.0,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["policy_rate_delta_implied_bps"] > 50.0
    assert res["stance"] == "HAWKISH"
    assert res["execution_authority"] is False


def test_fomc_reaction_function_dovish_easing_bias():
    # Inflation at target (2.0%), unemployment rising (4.8% vs 4.0% NAIRU), FCI tightening (+1.0)
    res = evaluate_fomc_reaction_function(
        core_pce_inflation=2.0,
        unemployment_rate=4.8,
        fci_delta=1.0,
        nairu=4.0,
        inflation_target=2.0,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["policy_rate_delta_implied_bps"] < -25.0
    assert res["stance"] == "DOVISH"
    assert res["execution_authority"] is False


def test_fomc_press_conference_conditioning_and_fade():
    # 2:00 PM release had hawkish spike, but 2:30 PM press conference introduces dovish conditioning:
    # "two-sided risks", "sufficiently restrictive", "data-dependent"
    speech_text = (
        "We are now in a position where risks are two-sided. Policy is sufficiently restrictive "
        "and we remain strictly data-dependent as economic conditions moderate."
    )
    res = evaluate_fomc_press_conference(
        speech_text=speech_text,
        initial_2pm_direction="HAWKISH_YIELDS_UP",
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["dovish_conditioning_detected"] is True
    assert "two-sided risks" in res["matched_cues"]
    assert "sufficiently restrictive" in res["matched_cues"]
    assert res["contradicts_initial_move"] is True
    assert res["actionable_guidance"] == "FADE_INITIAL_2PM_MOVE"


def test_ecb_fragmentation_playbook_btp_bund_blowout():
    # BTP/Bund spread at 260 bps (> 250 bps threshold) with Italian fiscal expansion
    res = evaluate_ecb_fragmentation_playbook(
        btp_bund_spread_bps=260.0,
        ecb_stance="HAWKISH",
        italian_fiscal_expansion=True,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["fragmentation_risk"] == "CRITICAL"
    assert res["tpi_activation_expected"] is True
    assert "SHORT_EUR_CHF" in res["recommended_trades"]
    assert "WIDEN_EUROPEAN_FINANCIALS_CDS" in res["recommended_trades"]


def test_boj_carry_unwind_playbook_shunto_and_mof_intervention():
    # Tokyo CPI 2.8% (> 2.0%), Shunto wage growth 4.5% (> 3.5%), USD/JPY 160.5 with high realized vol
    res = evaluate_boj_carry_unwind_playbook(
        tokyo_cpi=2.8,
        shunto_wage_growth=4.5,
        usdjpy_spot=160.5,
        usdjpy_realized_vol_pct=18.5,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["wage_price_spiral_confirmed"] is True
    assert res["mof_intervention_risk"] == "CRITICAL"
    assert res["global_carry_unwind_triggered"] is True
    assert "SHORT_USD_JPY" in res["recommended_trades"]
    assert "SHORT_EM_CARRY_MXN_BRL" in res["recommended_trades"]


def test_pboc_credit_transmission_and_counter_cyclical_fixing():
    # Daily fixing at 7.1800 vs consensus 7.2200 (delta -400 pips -> strong official resistance)
    # Chinese credit impulse expanding (+2.5 Z-score)
    res = evaluate_pboc_credit_transmission_playbook(
        daily_fixing_midpoint=7.1800,
        consensus_model_fixing=7.2200,
        credit_impulse_z=2.5,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["yuan_defense_active"] is True
    assert res["commodity_demand_expansion_lag_months"] == [3, 6]
    assert "LONG_AUD_USD" in res["recommended_trades"]
    assert "LONG_COPPER" in res["recommended_trades"]


# ==============================================================================
# 2. Economic Data Release Playbooks
# ==============================================================================

def test_cpi_release_playbook_core_beat_matrix_and_15min_rule():
    from core.institutional_macro_framework import evaluate_cpi_release_playbook
    # Hot Core CPI: Core MoM 0.4% vs 0.2% consensus, Supercore 0.5%
    # Case A: T=2 mins (within absorption blackout)
    res_early = evaluate_cpi_release_playbook(
        headline_cpi_mom=0.4,
        consensus_headline_mom=0.2,
        core_cpi_mom=0.4,
        consensus_core_mom=0.2,
        shelter_cpi_mom=0.35,
        supercore_services_mom=0.50,
        minutes_since_release=2.0,
    )
    assert res_early["status"] == "READY_FOR_REVIEW"
    assert res_early["surprise_direction"] == "SURPRISE_BEAT"
    assert res_early["tactical_action"] == "WAIT_ABSORPTION_PHASE"
    assert "US_2Y_10Y_REAL_YIELD" in res_early["execution_matrix"]["long"]
    assert "GOLD_XAU_USD" in res_early["execution_matrix"]["short"]

    # Case B: T=20 mins (post-absorption directional swing phase)
    res_mature = evaluate_cpi_release_playbook(
        headline_cpi_mom=0.4,
        consensus_headline_mom=0.2,
        core_cpi_mom=0.4,
        consensus_core_mom=0.2,
        shelter_cpi_mom=0.35,
        supercore_services_mom=0.50,
        minutes_since_release=20.0,
    )
    assert res_mature["tactical_action"] == "TRADE_DIRECTIONAL_SWING"


def test_cpi_release_playbook_core_miss_matrix():
    from core.institutional_macro_framework import evaluate_cpi_release_playbook
    # Cool Core CPI: Core MoM 0.1% vs 0.3% consensus
    res = evaluate_cpi_release_playbook(
        headline_cpi_mom=0.1,
        consensus_headline_mom=0.3,
        core_cpi_mom=0.1,
        consensus_core_mom=0.3,
        shelter_cpi_mom=0.20,
        supercore_services_mom=0.10,
        minutes_since_release=15.0,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["surprise_direction"] == "SURPRISE_MISS"
    assert "SP500_NASDAQ100" in res["execution_matrix"]["long"]
    assert "GOLD_XAU_USD" in res["execution_matrix"]["long"]
    assert "US_DOLLAR_INDEX" in res["execution_matrix"]["short"]
    assert "US_2Y_YIELDS" in res["execution_matrix"]["short"]


def test_nfp_labor_divergence_trap_and_scenarios():
    from core.institutional_macro_framework import evaluate_nfp_labor_playbook
    # Scenario A: Goldilocks (Headline beats 200k vs 150k, AHE moderates +0.2%, Unemployment ticks up to 4.1%)
    res_goldilocks = evaluate_nfp_labor_playbook(
        headline_nfp_k=200.0,
        consensus_nfp_k=150.0,
        household_survey_emp_delta_k=120.0,
        unemployment_rate=4.1,
        average_hourly_earnings_mom=0.2,
        nairu=4.0,
    )
    assert res_goldilocks["status"] == "READY_FOR_REVIEW"
    assert res_goldilocks["scenario"] == "GOLDILOCKS"
    assert res_goldilocks["risk_environment"] == "RISK_ON"
    assert res_goldilocks["wage_push_signal"] is False

    # Scenario B: Stagflationary Labor Shock (Headline misses 80k vs 150k, AHE spikes +0.5%, Unemployment 3.8% <= NAIRU)
    res_stagflation = evaluate_nfp_labor_playbook(
        headline_nfp_k=80.0,
        consensus_nfp_k=150.0,
        household_survey_emp_delta_k=-50.0,
        unemployment_rate=3.8,
        average_hourly_earnings_mom=0.5,
        nairu=4.0,
    )
    assert res_stagflation["scenario"] == "STAGFLATIONARY_LABOR_SHOCK"
    assert res_stagflation["wage_push_signal"] is True
    assert res_stagflation["divergence_trap_detected"] is True
    assert "BEAR_FLATTENER" in res_stagflation["curve_implication"]


def test_pmi_new_orders_inventory_ratio_cycle_momentum():
    from core.institutional_macro_framework import evaluate_pmi_cycle_momentum
    # Case A: Expansionary momentum (New Orders 54.0 / Inventories 46.0 = 1.17 > 1.0)
    res_exp = evaluate_pmi_cycle_momentum(
        ism_manufacturing_pmi=52.5,
        new_orders_index=54.0,
        inventories_index=46.0,
    )
    assert res_exp["status"] == "READY_FOR_REVIEW"
    assert res_exp["ratio"] > 1.0
    assert res_exp["cycle_momentum"] == "EXPANSIONARY_CYCLE_MOMENTUM"
    assert "LONG_COPPER_OIL_CYCLICALS" in res_exp["recommended_playbook"]

    # Case B: Destocking recession (New Orders 41.0 / Inventories 52.0 = 0.788 < 0.85)
    res_destock = evaluate_pmi_cycle_momentum(
        ism_manufacturing_pmi=44.0,
        new_orders_index=41.0,
        inventories_index=52.0,
    )
    assert res_destock["ratio"] < 0.85
    assert res_destock["cycle_momentum"] == "RECESSIONARY_DESTOCKING"
    assert "LONG_SOVEREIGN_BONDS_SHORT_BASE_METALS" in res_destock["recommended_playbook"]


# ==============================================================================
# 3. Geopolitical, Crisis & Shock Playbooks
# ==============================================================================

def test_terms_of_trade_energy_supply_shock_playbook():
    from core.institutional_macro_framework import evaluate_terms_of_trade_shock
    res = evaluate_terms_of_trade_shock(
        shock_type="SUPPLY_SIDE_ENERGY_SHOCK",
        energy_price_spike_pct=35.0,
        chokepoint_compromised=True,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["stagflationary_dynamics"] is True
    assert "LONG_CAD_JPY" in res["primary_fx_expressions"]
    assert "LONG_USD_EUR" in res["primary_fx_expressions"]
    assert "LONG_PHYSICAL_COMMODITIES" in res["cross_asset_playbook"]
    assert "SHORT_SOVEREIGN_BONDS" in res["cross_asset_playbook"]


def test_terms_of_trade_growth_destructive_shock_playbook():
    from core.institutional_macro_framework import evaluate_terms_of_trade_shock
    res = evaluate_terms_of_trade_shock(
        shock_type="GROWTH_DESTRUCTIVE_SHOCK",
        energy_price_spike_pct=-20.0,
        chokepoint_compromised=False,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["stagflationary_dynamics"] is False
    assert "LONG_SOVEREIGNS_AND_TECH" in res["cross_asset_playbook"]
    assert "SHORT_INDUSTRIAL_COMMODITIES" in res["cross_asset_playbook"]


def test_gold_decoupling_geopolitical_anchor():
    from core.institutional_macro_framework import evaluate_gold_decoupling_dynamics
    # US 10Y real yields rise +40 bps (traditionally bearish for gold),
    # but geopolitical conflict is active and central banks buy 120 tonnes of gold (de-dollarization).
    res = evaluate_gold_decoupling_dynamics(
        real_yield_10y_change_bps=40.0,
        central_bank_gold_buying_tonnes=120.0,
        geopolitical_conflict_active=True,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["traditional_model_bias"] == "BEARISH_GOLD_ON_RATES"
    assert res["geopolitical_anchor_active"] is True
    assert res["effective_gold_bias"] == "BULLISH_STRUCTURAL_DECOUPLING"


def test_sovereign_debt_crisis_flight_to_quality_hierarchy():
    from core.institutional_macro_framework import evaluate_sovereign_debt_crisis_hierarchy
    # Simultaneous liquidation: Equities -4.5%, HY credit spreads blow out +85 bps,
    # and 10Y sovereign yields SPIKE +35 bps (bonds falling).
    res = evaluate_sovereign_debt_crisis_hierarchy(
        equities_delta_pct=-4.5,
        credit_spread_delta_bps=85.0,
        sovereign_bond_yield_delta_bps=35.0,
        fiscal_dominance_detected=True,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["balance_sheet_constrained_unwind"] is True
    assert res["flight_to_quality_hierarchy"][0] == "TIER_1_US_T_BILLS_CASH_EQUIVALENTS"
    assert res["flight_to_quality_hierarchy"][1] == "TIER_2_PHYSICAL_USD_SETTLEMENT"
    assert res["flight_to_quality_hierarchy"][2] == "TIER_3_GOLD"
    assert "SHORT_HIGH_YIELD_CDX" in res["actionable_execution"]
    assert "PAY_FIXED_INTEREST_RATE_SWAPS" in res["actionable_execution"]
    assert "BUY_OTM_PUTS_EQUITY_INDICES" in res["actionable_execution"]


# ==============================================================================
# 4. Positioning & Market Structure Analysis
# ==============================================================================

def test_cot_3y_z_score_extreme_crowding_and_liquidation():
    from core.institutional_macro_framework import evaluate_cot_3y_z_scores
    # Leveraged funds net long 85,000 contracts vs 3-year mean 20,000 and std 25,000 (Z = +2.60 > +2.0)
    # Historical series: 156 weekly net positions (3 years)
    np.random.seed(42)
    history = list(np.random.normal(loc=20000.0, scale=25000.0, size=156))
    res = evaluate_cot_3y_z_scores(
        current_net_position=85000.0,
        historical_3y_net_series=history,
        participant_category="LEVERAGED_FUNDS",
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["z_score"] >= 2.0
    assert res["crowding_status"] == "EXTREME_CROWDING"
    assert res["asymmetry_risk"] == "ASYMMETRIC_LIQUIDATION_SQUEEZE_RISK"
    assert res["guidance"] == "PREPARE_FOR_COUNTER_TREND_SQUEEZE"


def test_cot_3y_z_score_balanced_positioning():
    from core.institutional_macro_framework import evaluate_cot_3y_z_scores
    # Current net position close to 3-year mean (Z = 0.35 < 1.0)
    history = list(np.random.normal(loc=10000.0, scale=20000.0, size=156))
    res = evaluate_cot_3y_z_scores(
        current_net_position=17000.0,
        historical_3y_net_series=history,
        participant_category="ASSET_MANAGERS",
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert abs(res["z_score"]) < 1.0
    assert res["crowding_status"] == "BALANCED_POSITIONING"
    assert res["guidance"] == "FUNDAMENTALS_DRIVE_PRICE_DIRECTLY_TRADE_CONTINUATION"


def test_dealer_gamma_regime_classification():
    from core.institutional_macro_framework import evaluate_dealer_gamma_regime
    # Case A: Long gamma (+3.5 billion / +$ strike buffer) -> Volatility dampener, mean reversion
    res_long = evaluate_dealer_gamma_regime(
        net_dealer_gamma_billions=3.5,
        spot_price=5850.0,
        gamma_flip_strike=5800.0,
    )
    assert res_long["status"] == "READY_FOR_REVIEW"
    assert res_long["gamma_regime"] == "LONG_GAMMA_VOLATILITY_DAMPENER"
    assert res_long["market_maker_behavior"] == "BUY_DIPS_SELL_RALLIES"
    assert "FAVOR_MEAN_REVERSION" in res_long["tactical_playbook"]

    # Case B: Short gamma (-2.8 billion / spot below flip) -> Volatility accelerator, trending
    res_short = evaluate_dealer_gamma_regime(
        net_dealer_gamma_billions=-2.8,
        spot_price=5750.0,
        gamma_flip_strike=5800.0,
    )
    assert res_short["gamma_regime"] == "SHORT_GAMMA_VOLATILITY_ACCELERATOR"
    assert res_short["market_maker_behavior"] == "SELL_DROPS_BUY_RALLIES"
    assert "FAVOR_OUTSIZED_TRENDS" in res_short["tactical_playbook"]


def test_fx_risk_reversals_asymmetric_crash_hedging():
    from core.institutional_macro_framework import evaluate_fx_risk_reversals
    # USD/JPY 25-delta call IV 10.5% vs put IV 13.0% -> RR = 10.5 - 13.0 = -2.5 vol pts
    # Spot USD/JPY remains near multi-decade highs (159.50)
    res = evaluate_fx_risk_reversals(
        call_25d_iv=10.5,
        put_25d_iv=13.0,
        spot_near_cyclical_highs=True,
        pair="USDJPY",
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["risk_reversal_25d_vol_pts"] == -2.5
    assert res["skew_bias"] == "DEEP_PUT_PROTECTION"
    assert res["asymmetric_short_setup"] is True
    assert "HIGH_PROBABILITY_ASYMMETRIC_SHORT" in res["tactical_recommendation"]


# ==============================================================================
# 5. Master Cross-Asset Reference Matrix & Three-Window Protocol
# ==============================================================================

def test_master_cross_asset_matrix_all_six_scenarios():
    from core.institutional_macro_framework import evaluate_master_cross_asset_matrix

    # 1. US Core Inflation Re-acceleration
    res1 = evaluate_master_cross_asset_matrix({
        "scenario_name": "US_CORE_INFLATION_REACCELERATION",
        "core_cpi_surprise_pct": 0.25,
        "supercore_strong": True,
    })
    assert res1["status"] == "READY_FOR_REVIEW"
    assert res1["scenario_identified"] == "US_CORE_INFLATION_REACCELERATION"
    assert res1["trigger_condition_met"] is True
    assert "USDJPY" in res1["high_probability_trades"]["long"]
    assert "US_2Y_YIELD" in res1["high_probability_trades"]["long"]
    assert "NASDAQ_100" in res1["high_probability_trades"]["short"]
    assert "GOLD" in res1["high_probability_trades"]["short"]
    assert "EURUSD" in res1["high_probability_trades"]["short"]

    # 2. Global Recessionary Hard Landing (Sahm Rule)
    res2 = evaluate_master_cross_asset_matrix({
        "scenario_name": "GLOBAL_RECESSIONARY_HARD_LANDING",
        "sahm_rule_breach": True,
        "ism_manufacturing": 43.5,
    })
    assert res2["status"] == "READY_FOR_REVIEW"
    assert res2["scenario_identified"] == "GLOBAL_RECESSIONARY_HARD_LANDING"
    assert res2["trigger_condition_met"] is True
    assert "US_2Y_TREASURIES" in res2["high_probability_trades"]["long"]
    assert "GOLD" in res2["high_probability_trades"]["long"]
    assert "USDCHF" in res2["high_probability_trades"]["long"]
    assert "HYG_HIGH_YIELD" in res2["high_probability_trades"]["short"]
    assert "BRENT_CRUDE" in res2["high_probability_trades"]["short"]
    assert "AUDUSD" in res2["high_probability_trades"]["short"]

    # 3. China Credit & Fiscal Reflation
    res3 = evaluate_master_cross_asset_matrix({
        "scenario_name": "CHINA_CREDIT_REFLATION",
        "credit_impulse_accelerating": True,
        "fiscal_bond_quota_expanded": True,
    })
    assert res3["status"] == "READY_FOR_REVIEW"
    assert res3["scenario_identified"] == "CHINA_CREDIT_REFLATION"
    assert res3["trigger_condition_met"] is True
    assert "AUDUSD" in res3["high_probability_trades"]["long"]
    assert "COPPER" in res3["high_probability_trades"]["long"]
    assert "EMERGING_MARKET_EQUITIES" in res3["high_probability_trades"]["long"]
    assert "DXY_USD_INDEX" in res3["high_probability_trades"]["short"]
    assert "DEFENSIVE_UTILITIES" in res3["high_probability_trades"]["short"]

    # 4. BOJ Yield Policy Normalization
    res4 = evaluate_master_cross_asset_matrix({
        "scenario_name": "BOJ_POLICY_NORMALIZATION",
        "core_tokyo_cpi": 2.8,
        "wage_gains_broadening": True,
    })
    assert res4["status"] == "READY_FOR_REVIEW"
    assert res4["scenario_identified"] == "BOJ_POLICY_NORMALIZATION"
    assert res4["trigger_condition_met"] is True
    assert "SHORT_EURJPY" in res4["high_probability_trades"]["long_jpy_crosses"]
    assert "SHORT_AUDJPY" in res4["high_probability_trades"]["long_jpy_crosses"]
    assert "SOVEREIGN_BONDS_GLOBAL" in res4["high_probability_trades"]["short"]

    # 5. Geopolitical Maritime / Energy Shock
    res5 = evaluate_master_cross_asset_matrix({
        "scenario_name": "MARITIME_ENERGY_SHOCK",
        "chokepoint_compromised": True,
        "energy_price_spike_pct": 22.0,
    })
    assert res5["status"] == "READY_FOR_REVIEW"
    assert res5["scenario_identified"] == "MARITIME_ENERGY_SHOCK"
    assert res5["trigger_condition_met"] is True
    assert "BRENT_CRUDE" in res5["high_probability_trades"]["long"]
    assert "US_NATURAL_GAS" in res5["high_probability_trades"]["long"]
    assert "CAD_EUR" in res5["high_probability_trades"]["long"]
    assert "DAX_EUROPEAN_EQUITIES" in res5["high_probability_trades"]["short"]
    assert "KRW_KOREAN_WON" in res5["high_probability_trades"]["short"]

    # 6. Fiscal Debt Supply Indigestion
    res6 = evaluate_master_cross_asset_matrix({
        "scenario_name": "FISCAL_DEBT_SUPPLY_INDIGESTION",
        "treasury_supply_expansion": True,
        "foreign_cb_demand_waning": True,
    })
    assert res6["status"] == "READY_FOR_REVIEW"
    assert res6["scenario_identified"] == "FISCAL_DEBT_SUPPLY_INDIGESTION"
    assert res6["trigger_condition_met"] is True
    assert "CURVE_STEEPENER_2S30S" in res6["high_probability_trades"]["long"]
    assert "TLT_LONG_TREASURIES" in res6["high_probability_trades"]["short"]
    assert "REITS" in res6["high_probability_trades"]["short"]


def test_master_cross_asset_matrix_auto_detection():
    from core.institutional_macro_framework import evaluate_master_cross_asset_matrix
    # Auto-detect Sahm rule breach when unemployment 12M delta >= 0.50
    res = evaluate_master_cross_asset_matrix({
        "unemployment_12m_delta_pct": 0.55,
        "ism_manufacturing": 42.0,
    })
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["scenario_identified"] == "GLOBAL_RECESSIONARY_HARD_LANDING"
    assert res["trigger_condition_met"] is True
    assert "US_2Y_TREASURIES" in res["high_probability_trades"]["long"]


def test_three_window_execution_protocol_window1_setup():
    from core.institutional_macro_framework import evaluate_three_window_execution_protocol
    # T = -120 mins: Window 1 (Pre-Event Setup, T-24h to T-15m)
    res = evaluate_three_window_execution_protocol(
        minutes_from_event=-120.0,
        spread_multiple=1.0,
        pre_event_positioning_z_score=2.4,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["active_window"] == "WINDOW_1_PRE_EVENT_SETUP"
    assert res["order_action"] == "AUDIT_ONLY_SKEW_ADJUSTED_LIMITS"
    assert res["risk_reduction_required"] is True
    assert res["execution_authority"] is False


def test_three_window_execution_protocol_window2_absorption():
    from core.institutional_macro_framework import evaluate_three_window_execution_protocol
    # T = +4 mins: Window 2 (Absorption Phase, T+0 to T+15m)
    res = evaluate_three_window_execution_protocol(
        minutes_from_event=4.0,
        spread_multiple=8.5,
    )
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["active_window"] == "WINDOW_2_ABSORPTION_PHASE"
    assert res["order_action"] == "ZERO_MARKET_ORDERS_FREEZE"
    assert res["spread_freeze_active"] is True
    assert res["spread_spike_detected"] is True
    assert res["execution_authority"] is False


def test_three_window_execution_protocol_window3_directional_swing():
    from core.institutional_macro_framework import evaluate_three_window_execution_protocol
    # Case A: T = +30 mins, Cross-asset confirmed
    res_confirmed = evaluate_three_window_execution_protocol(
        minutes_from_event=30.0,
        spread_multiple=1.2,
        cross_asset_confirmation=True,
    )
    assert res_confirmed["status"] == "READY_FOR_REVIEW"
    assert res_confirmed["active_window"] == "WINDOW_3_DIRECTIONAL_ALLOCATION"
    assert res_confirmed["order_action"] == "ENTER_SWING_PULLBACK"
    assert res_confirmed["fade_signal"] is False

    # Case B: T = +30 mins, Cross-asset failed to confirm / exhausted
    res_fade = evaluate_three_window_execution_protocol(
        minutes_from_event=30.0,
        spread_multiple=1.2,
        cross_asset_confirmation=False,
    )
    assert res_fade["status"] == "READY_FOR_REVIEW"
    assert res_fade["active_window"] == "WINDOW_3_DIRECTIONAL_ALLOCATION"
    assert res_fade["order_action"] == "FADE_OVERSHOOT"
    assert res_fade["fade_signal"] is True

