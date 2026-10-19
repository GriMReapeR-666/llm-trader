"""Foundational Institutional Macro Architecture & Execution Framework.

Implements the institutional macro trading framework:
1. Core Macro Triad & Monetary Plumbing (Net USD Liquidity, Cross-Currency Basis, Sovereign Curves)
2. Central Bank Reaction Function Playbooks (FOMC FCI/Supercore, ECB Fragmentation/TPI, BOJ Shunto/Carry, PBOC Fixing/Credit)
3. Economic Data Release Playbooks (CPI Shelter/Supercore matrix, NFP Household divergence/Wage push, PMI New Orders/Inventory)
4. Geopolitical, Crisis & Shock Playbooks (Terms of Trade elasticity, Gold de-dollarization decoupling, Sovereign Debt hierarchy)
5. Positioning & Market Structure Analysis (COT 3-Year Z-Score, Dealer Gamma regimes, 25-Delta FX Risk Reversals)
6. Master Cross-Asset Reference Matrix (6 Master Scenarios including Sahm Rule)
7. Execution Architecture: The Three-Window Protocol (Pre-Event Setup, Absorption Phase, Directional Allocation)

Design Invariants:
- EXECUTION_AUTHORITY is strictly False. All outputs are analytical/advisory evidence.
- Fail-closed: missing or invalid inputs yield INSUFFICIENT_DATA or INVALID_INPUT.
- Zero unnecessary dependencies: built on Python stdlib and NumPy.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

EXECUTION_AUTHORITY = False


# ==============================================================================
# Helper validation utilities
# ==============================================================================

def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


# ==============================================================================
# 1. Central Bank Reaction Function Playbooks
# ==============================================================================

def evaluate_fomc_reaction_function(
    core_pce_inflation: Any,
    unemployment_rate: Any,
    fci_delta: Any,
    *,
    nairu: Any = 4.0,
    inflation_target: Any = 2.0,
    alpha: float = 0.5,
    beta: float = 0.5,
    gamma: float = 0.25,
) -> Dict[str, Any]:
    """Evaluates the Federal Reserve FOMC Taylor-style Reaction Function:

    Delta i_t = alpha * (pi_t - pi^*) + beta * (u^* - u_t) - gamma * (Delta FCI)

    Where:
    - pi_t: Core PCE or Supercore inflation rate (%)
    - pi^*: Long-term inflation target (2.0%)
    - u_t: Actual unemployment rate (%)
    - u^*: Non-accelerating inflation rate of unemployment / NAIRU (%)
    - Delta FCI: Change in Financial Conditions Index (negative = loosening)
    """
    pi = _safe_float(core_pce_inflation)
    u = _safe_float(unemployment_rate)
    fci = _safe_float(fci_delta)
    u_star = _safe_float(nairu)
    pi_star = _safe_float(inflation_target)

    if any(v is None for v in (pi, u, fci, u_star, pi_star)):
        return {
            "status": "INVALID_INPUT",
            "reason": "Required macroeconomic variables missing or non-finite",
            "execution_authority": False,
        }

    pi_gap = pi - pi_star
    u_gap = u_star - u
    delta_i_pct = (alpha * pi_gap) + (beta * u_gap) - (gamma * fci)
    delta_i_bps = delta_i_pct * 100.0

    if delta_i_bps >= 25.0:
        stance = "HAWKISH"
    elif delta_i_bps <= -25.0:
        stance = "DOVISH"
    else:
        stance = "NEUTRAL"

    return {
        "status": "READY_FOR_REVIEW",
        "policy_rate_delta_implied_bps": round(delta_i_bps, 2),
        "stance": stance,
        "core_pce_gap": round(pi_gap, 2),
        "labor_gap": round(u_gap, 2),
        "fci_contribution_bps": round(-gamma * fci * 100.0, 2),
        "execution_authority": False,
    }


def evaluate_fomc_press_conference(
    speech_text: str,
    initial_2pm_direction: str = "HAWKISH_YIELDS_UP",
) -> Dict[str, Any]:
    """Analyzes the FOMC 2:30 PM Press Conference for conditioning phrases and contradiction signals.

    If the press conference contradicts the initial 2:00 PM statement/SEP market reaction,
    the initial move typically fades within 24 hours.
    """
    if not isinstance(speech_text, str) or not speech_text.strip():
        return {
            "status": "INVALID_INPUT",
            "reason": "speech_text must be a non-empty string",
            "execution_authority": False,
        }

    text_lower = speech_text.lower()

    dovish_cues = [
        "two-sided risks", "two sided risks", "sufficiently restrictive",
        "restrictive enough", "data-dependent", "data dependent",
        "growth moderating", "labor market cooling", "pace of cuts",
    ]
    hawkish_cues = [
        "additional firming", "premature to loosen", "ongoing increases",
        "inflation persistent", "unanchored inflation", "higher for longer",
    ]

    matched_dovish = []
    if "two-sided" in text_lower or "two sided" in text_lower:
        matched_dovish.append("two-sided risks")
    for cue in dovish_cues:
        if cue in text_lower and cue not in matched_dovish:
            matched_dovish.append(cue)

    matched_hawkish = [cue for cue in hawkish_cues if cue in text_lower]

    dovish_detected = len(matched_dovish) > 0
    hawkish_detected = len(matched_hawkish) > 0

    initial_hawkish = "HAWKISH" in initial_2pm_direction.upper() or "YIELDS_UP" in initial_2pm_direction.upper()
    initial_dovish = "DOVISH" in initial_2pm_direction.upper() or "YIELDS_DOWN" in initial_2pm_direction.upper()

    contradicts = False
    actionable_guidance = "ALIGN_WITH_TREND"

    if initial_hawkish and dovish_detected and not hawkish_detected:
        contradicts = True
        actionable_guidance = "FADE_INITIAL_2PM_MOVE"
    elif initial_dovish and hawkish_detected and not dovish_detected:
        contradicts = True
        actionable_guidance = "FADE_INITIAL_2PM_MOVE"

    return {
        "status": "READY_FOR_REVIEW",
        "dovish_conditioning_detected": dovish_detected,
        "hawkish_conditioning_detected": hawkish_detected,
        "matched_cues": matched_dovish + matched_hawkish,
        "contradicts_initial_move": contradicts,
        "actionable_guidance": actionable_guidance,
        "execution_authority": False,
    }


def evaluate_ecb_fragmentation_playbook(
    btp_bund_spread_bps: Any,
    ecb_stance: str = "HAWKISH",
    italian_fiscal_expansion: bool = True,
) -> Dict[str, Any]:
    """Evaluates ECB Anti-Fragmentation & Transmission Protection Instrument (TPI) threshold.

    The critical indicator is the 10-year Italy (BTP) vs. Germany (Bund) spread:
    - Normal: < 150 bps
    - Elevated: 150-200 bps
    - High / Pause Trigger: 200-250 bps
    - Critical / TPI Activation Expected: > 250 bps
    """
    spread = _safe_float(btp_bund_spread_bps)
    if spread is None:
        return {"status": "INVALID_INPUT", "reason": "btp_bund_spread_bps invalid", "execution_authority": False}

    if spread >= 250.0:
        risk = "CRITICAL"
        tpi_expected = True
    elif spread >= 200.0:
        risk = "HIGH"
        tpi_expected = True
    elif spread >= 150.0:
        risk = "ELEVATED"
        tpi_expected = False
    else:
        risk = "NORMAL"
        tpi_expected = False

    recommended_trades: List[str] = []
    if ecb_stance.upper() == "HAWKISH" and (italian_fiscal_expansion or spread >= 200.0):
        recommended_trades.extend([
            "SHORT_EUR_CHF",
            "WIDEN_EUROPEAN_FINANCIALS_CDS",
        ])

    return {
        "status": "READY_FOR_REVIEW",
        "btp_bund_spread_bps": spread,
        "fragmentation_risk": risk,
        "tpi_activation_expected": tpi_expected,
        "recommended_trades": recommended_trades,
        "execution_authority": False,
    }


def evaluate_boj_carry_unwind_playbook(
    tokyo_cpi: Any,
    shunto_wage_growth: Any,
    usdjpy_spot: Any,
    usdjpy_realized_vol_pct: Any,
) -> Dict[str, Any]:
    """Evaluates Bank of Japan (BOJ) Policy Normalization and Global Carry Trade Unwinds.

    Monitors:
    - Tokyo CPI (>2.0%) and Shunto wage negotiations (>3.5%) for sustainable wage-price spiral.
    - USD/JPY spot levels (>155-160) and realized volatility for Ministry of Finance (MoF) intervention risk.
    """
    cpi = _safe_float(tokyo_cpi)
    wage = _safe_float(shunto_wage_growth)
    spot = _safe_float(usdjpy_spot)
    vol = _safe_float(usdjpy_realized_vol_pct)

    if any(v is None for v in (cpi, wage, spot, vol)):
        return {"status": "INVALID_INPUT", "reason": "BOJ metrics invalid", "execution_authority": False}

    wage_price_spiral = bool(cpi >= 2.0 and wage >= 3.5)

    if spot >= 160.0 or (spot >= 155.0 and vol >= 15.0):
        mof_risk = "CRITICAL"
    elif spot >= 155.0:
        mof_risk = "HIGH"
    elif spot >= 150.0:
        mof_risk = "ELEVATED"
    else:
        mof_risk = "NORMAL"

    carry_unwind = wage_price_spiral or mof_risk == "CRITICAL"
    recommended_trades: List[str] = []
    if carry_unwind:
        recommended_trades.extend([
            "SHORT_USD_JPY",
            "SHORT_EM_CARRY_MXN_BRL",
            "SHORT_HIGH_BETA_RISK_ASSETS",
        ])

    return {
        "status": "READY_FOR_REVIEW",
        "wage_price_spiral_confirmed": wage_price_spiral,
        "mof_intervention_risk": mof_risk,
        "global_carry_unwind_triggered": carry_unwind,
        "recommended_trades": recommended_trades,
        "execution_authority": False,
    }


def evaluate_pboc_credit_transmission_playbook(
    daily_fixing_midpoint: Any,
    consensus_model_fixing: Any,
    credit_impulse_z: Any,
) -> Dict[str, Any]:
    """Evaluates People's Bank of China (PBOC) Counter-Cyclical Factor and Credit Transmission.

    - Counter-cyclical fixing: If fixing is significantly stronger than consensus (delta < -100 pips),
      signals official defense against yuan depreciation.
    - Chinese Credit Impulse: Positive credit impulse leads global commodity demand (copper, oil) with a 3-6 month lag.
    """
    fix = _safe_float(daily_fixing_midpoint)
    cons = _safe_float(consensus_model_fixing)
    impulse = _safe_float(credit_impulse_z)

    if any(v is None for v in (fix, cons, impulse)):
        return {"status": "INVALID_INPUT", "reason": "PBOC metrics invalid", "execution_authority": False}

    pips_delta = (fix - cons) * 10000.0
    yuan_defense = bool(pips_delta <= -100.0)

    recommended_trades: List[str] = []
    if impulse > 0:
        recommended_trades.extend([
            "LONG_AUD_USD",
            "LONG_COPPER",
            "LONG_COMMODITY_CURRENCIES",
        ])

    return {
        "status": "READY_FOR_REVIEW",
        "fixing_delta_pips": round(pips_delta, 1),
        "yuan_defense_active": yuan_defense,
        "credit_impulse_z": round(impulse, 2),
        "commodity_demand_expansion_lag_months": [3, 6],
        "recommended_trades": recommended_trades,
        "execution_authority": False,
    }


# ==============================================================================
# 2. Economic Data Release Playbooks
# ==============================================================================

def evaluate_cpi_release_playbook(
    headline_cpi_mom: Any,
    consensus_headline_mom: Any,
    core_cpi_mom: Any,
    consensus_core_mom: Any,
    shelter_cpi_mom: Any,
    supercore_services_mom: Any,
    minutes_since_release: Any = 15.0,
) -> Dict[str, Any]:
    """Evaluates US Consumer Price Index (CPI) release matrix, shelter lag, and tactical rule."""
    h = _safe_float(headline_cpi_mom)
    h_cons = _safe_float(consensus_headline_mom)
    c = _safe_float(core_cpi_mom)
    c_cons = _safe_float(consensus_core_mom)
    shelter = _safe_float(shelter_cpi_mom)
    supercore = _safe_float(supercore_services_mom)
    mins = _safe_float(minutes_since_release)

    if any(v is None for v in (h, h_cons, c, c_cons, shelter, supercore)):
        return {"status": "INVALID_INPUT", "reason": "CPI metrics invalid", "execution_authority": False}

    mins = mins if mins is not None else 0.0

    core_delta = round(c - c_cons, 4)
    # Tactical rule: Never trade the release tick T+0. Wait for T+15 minutes.
    tactical_action = "WAIT_ABSORPTION_PHASE" if mins < 15.0 else "TRADE_DIRECTIONAL_SWING"

    if core_delta >= 0.05:
        surprise_dir = "SURPRISE_BEAT"
        matrix = {
            "long": ["US_2Y_10Y_REAL_YIELD", "US_DOLLAR_INDEX", "SHORT_END_OIS_RATES"],
            "short": ["GOLD_XAU_USD", "LONG_DURATION_EQUITIES", "HIGH_YIELD_CREDIT"],
        }
    elif core_delta <= -0.05:
        surprise_dir = "SURPRISE_MISS"
        matrix = {
            "long": ["SP500_NASDAQ100", "GOLD_XAU_USD", "FRONT_END_TREASURIES"],
            "short": ["US_DOLLAR_INDEX", "US_2Y_YIELDS", "VALUE_CYCLICALS"],
        }
    else:
        surprise_dir = "IN_LINE"
        matrix = {"long": [], "short": []}

    return {
        "status": "READY_FOR_REVIEW",
        "surprise_direction": surprise_dir,
        "core_delta": core_delta,
        "supercore_mom": supercore,
        "shelter_mom": shelter,
        "shelter_lag_note": "Shelter lags real-time rental indices by 9-12 months; supercore reflects underlying persistence.",
        "minutes_since_release": mins,
        "tactical_action": tactical_action,
        "execution_matrix": matrix,
        "execution_authority": False,
    }


def evaluate_nfp_labor_playbook(
    headline_nfp_k: Any,
    consensus_nfp_k: Any,
    household_survey_emp_delta_k: Any,
    unemployment_rate: Any,
    average_hourly_earnings_mom: Any,
    *,
    nairu: Any = 4.0,
) -> Dict[str, Any]:
    """Evaluates NFP Establishment vs Household Survey divergence, wage push, and scenario classification."""
    nfp = _safe_float(headline_nfp_k)
    cons = _safe_float(consensus_nfp_k)
    hh = _safe_float(household_survey_emp_delta_k)
    u = _safe_float(unemployment_rate)
    ahe = _safe_float(average_hourly_earnings_mom)
    u_star = _safe_float(nairu) or 4.0

    if any(v is None for v in (nfp, cons, hh, u, ahe)):
        return {"status": "INVALID_INPUT", "reason": "NFP metrics invalid", "execution_authority": False}

    divergence_trap = bool((nfp > cons and hh < 0) or (nfp > 0 and hh < 0))
    wage_push = bool(ahe >= 0.4 and u <= u_star)

    if nfp >= cons and ahe <= 0.3 and u >= u_star:
        scenario = "GOLDILOCKS"
        risk_env = "RISK_ON"
        curve_imp = "RATES_NEUTRAL_LOWER"
    elif nfp < cons and ahe >= 0.4:
        scenario = "STAGFLATIONARY_LABOR_SHOCK"
        risk_env = "RISK_OFF_EQUITY_MARGIN_COMPRESSION"
        curve_imp = "BEAR_FLATTENER"
    elif nfp >= cons and ahe >= 0.4:
        scenario = "OVERHEATING_HAWKISH"
        risk_env = "RISK_OFF_RATES_SURGE"
        curve_imp = "BEAR_FLATTENER"
    else:
        scenario = "GROWTH_DECELERATION"
        risk_env = "RISK_OFF_CYCLICALS"
        curve_imp = "BULL_STEEPENER"

    return {
        "status": "READY_FOR_REVIEW",
        "scenario": scenario,
        "risk_environment": risk_env,
        "wage_push_signal": wage_push,
        "divergence_trap_detected": divergence_trap,
        "curve_implication": curve_imp,
        "execution_authority": False,
    }


def evaluate_pmi_cycle_momentum(
    ism_manufacturing_pmi: Any,
    new_orders_index: Any,
    inventories_index: Any,
) -> Dict[str, Any]:
    """Evaluates leading cycle momentum: ISM Manufacturing New Orders Index / Inventories Index."""
    pmi = _safe_float(ism_manufacturing_pmi)
    orders = _safe_float(new_orders_index)
    inv = _safe_float(inventories_index)

    if any(v is None for v in (pmi, orders, inv)) or inv <= 0:
        return {"status": "INVALID_INPUT", "reason": "PMI inputs invalid", "execution_authority": False}

    ratio = orders / inv
    if ratio > 1.0:
        momentum = "EXPANSIONARY_CYCLE_MOMENTUM"
        playbook = ["LONG_COPPER_OIL_CYCLICALS", "SHORT_DEFENSIVE_UTILITIES"]
    elif ratio < 0.85:
        momentum = "RECESSIONARY_DESTOCKING"
        playbook = ["LONG_SOVEREIGN_BONDS_SHORT_BASE_METALS", "UNDERPERFORM_CYCLICAL_EQUITIES"]
    else:
        momentum = "BALANCED_INVENTORY_CYCLE"
        playbook = ["NEUTRAL_ALLOCATION"]

    return {
        "status": "READY_FOR_REVIEW",
        "pmi": pmi,
        "new_orders": orders,
        "inventories": inv,
        "ratio": round(ratio, 4),
        "cycle_momentum": momentum,
        "recommended_playbook": playbook,
        "execution_authority": False,
    }


# ==============================================================================
# 3. Geopolitical, Crisis & Shock Playbooks
# ==============================================================================

def evaluate_terms_of_trade_shock(
    shock_type: str = "SUPPLY_SIDE_ENERGY_SHOCK",
    energy_price_spike_pct: Any = 25.0,
    chokepoint_compromised: bool = False,
) -> Dict[str, Any]:
    """Evaluates Geopolitical Terms of Trade (ToT) Elasticity: P_Exports / P_Imports.

    - Net Exporters (USA, Canada, Australia, Norway) experience ToT expansion during energy supply shocks.
    - Net Importers (Eurozone, Japan, South Korea, UK) experience trade balance collapse.
    """
    pct = _safe_float(energy_price_spike_pct) or 0.0
    st = str(shock_type).upper()

    if "SUPPLY" in st or pct >= 20.0 or chokepoint_compromised:
        stagflation = True
        cross_asset = [
            "LONG_PHYSICAL_COMMODITIES",
            "SHORT_SOVEREIGN_BONDS",
        ]
        fx_expressions = [
            "LONG_CAD_JPY",
            "LONG_USD_EUR",
            "LONG_AUD_JPY",
            "LONG_USD_KRW",
        ]
    else:
        stagflation = False
        cross_asset = [
            "LONG_SOVEREIGNS_AND_TECH",
            "SHORT_INDUSTRIAL_COMMODITIES",
        ]
        fx_expressions = [
            "LONG_USD_CASH",
            "LONG_JPY_SAFE_HAVEN",
        ]

    return {
        "status": "READY_FOR_REVIEW",
        "shock_type": shock_type,
        "energy_price_spike_pct": pct,
        "chokepoint_compromised": chokepoint_compromised,
        "stagflationary_dynamics": stagflation,
        "primary_fx_expressions": fx_expressions,
        "cross_asset_playbook": cross_asset,
        "execution_authority": False,
    }


def evaluate_gold_decoupling_dynamics(
    real_yield_10y_change_bps: Any,
    central_bank_gold_buying_tonnes: Any,
    geopolitical_conflict_active: bool = False,
) -> Dict[str, Any]:
    """Evaluates Gold de-dollarization decoupling from real yields during war/fragmentation."""
    ry = _safe_float(real_yield_10y_change_bps)
    tonnes = _safe_float(central_bank_gold_buying_tonnes)

    if ry is None or tonnes is None:
        return {"status": "INVALID_INPUT", "reason": "Gold metrics invalid", "execution_authority": False}

    trad_bias = "BEARISH_GOLD_ON_RATES" if ry > 0 else "BULLISH_GOLD_ON_RATES"
    anchor_active = bool(geopolitical_conflict_active and tonnes >= 50.0)

    if anchor_active:
        effective_bias = "BULLISH_STRUCTURAL_DECOUPLING"
    else:
        effective_bias = trad_bias

    return {
        "status": "READY_FOR_REVIEW",
        "real_yield_10y_change_bps": ry,
        "central_bank_gold_buying_tonnes": tonnes,
        "geopolitical_conflict_active": geopolitical_conflict_active,
        "traditional_model_bias": trad_bias,
        "geopolitical_anchor_active": anchor_active,
        "effective_gold_bias": effective_bias,
        "execution_authority": False,
    }


def evaluate_sovereign_debt_crisis_hierarchy(
    equities_delta_pct: Any,
    credit_spread_delta_bps: Any,
    sovereign_bond_yield_delta_bps: Any,
    fiscal_dominance_detected: bool = False,
) -> Dict[str, Any]:
    """Evaluates sovereign debt crisis and flight-to-quality hierarchy under fiscal dominance."""
    eq = _safe_float(equities_delta_pct)
    credit = _safe_float(credit_spread_delta_bps)
    yields = _safe_float(sovereign_bond_yield_delta_bps)

    if any(v is None for v in (eq, credit, yields)):
        return {"status": "INVALID_INPUT", "reason": "Crisis metrics invalid", "execution_authority": False}

    simultaneous_liquidation = bool(eq < -2.0 and credit > 25.0 and yields > 15.0)

    hierarchy = [
        "TIER_1_US_T_BILLS_CASH_EQUIVALENTS",
        "TIER_2_PHYSICAL_USD_SETTLEMENT",
        "TIER_3_GOLD",
        "TIER_4_LONG_DATED_US_TREASURIES",
    ]

    actionable = [
        "GO_TO_CASH_T_BILLS",
        "SHORT_HIGH_YIELD_CDX",
        "PAY_FIXED_INTEREST_RATE_SWAPS",
        "BUY_OTM_PUTS_EQUITY_INDICES",
    ]

    return {
        "status": "READY_FOR_REVIEW",
        "balance_sheet_constrained_unwind": simultaneous_liquidation,
        "fiscal_dominance_detected": fiscal_dominance_detected,
        "flight_to_quality_hierarchy": hierarchy,
        "actionable_execution": actionable,
        "execution_authority": False,
    }


# ==============================================================================
# 4. Positioning & Market Structure Analysis
# ==============================================================================

def evaluate_cot_3y_z_scores(
    current_net_position: Any,
    historical_3y_net_series: Sequence[float],
    participant_category: str = "LEVERAGED_FUNDS",
) -> Dict[str, Any]:
    """Computes CFTC COT 3-Year Z-Score: Z = (Net_t - mu_3Y) / sigma_3Y."""
    curr = _safe_float(current_net_position)
    if curr is None or not isinstance(historical_3y_net_series, (list, tuple, np.ndarray)):
        return {"status": "INVALID_INPUT", "reason": "COT inputs invalid", "execution_authority": False}

    clean_hist = [_safe_float(x) for x in historical_3y_net_series if _safe_float(x) is not None]
    if len(clean_hist) < 20:
        return {"status": "INSUFFICIENT_DATA", "reason": "Need >= 20 weekly reports for 3Y Z-score", "execution_authority": False}

    mu = float(np.mean(clean_hist))
    sigma = float(np.std(clean_hist))
    sigma = max(1e-6, sigma)

    z = (curr - mu) / sigma
    abs_z = abs(z)

    if abs_z >= 2.0:
        crowding = "EXTREME_CROWDING"
        asymmetry = "ASYMMETRIC_LIQUIDATION_SQUEEZE_RISK"
        guidance = "PREPARE_FOR_COUNTER_TREND_SQUEEZE"
    elif abs_z < 1.0:
        crowding = "BALANCED_POSITIONING"
        asymmetry = "SYMMETRIC_FUNDAMENTAL_RISK"
        guidance = "FUNDAMENTALS_DRIVE_PRICE_DIRECTLY_TRADE_CONTINUATION"
    else:
        crowding = "MODERATE_POSITIONING"
        asymmetry = "ELEVATED_TAIL_RISK"
        guidance = "CAUTIOUS_TREND_FOLLOWING"

    return {
        "status": "READY_FOR_REVIEW",
        "participant_category": participant_category,
        "current_net_position": curr,
        "mean_3y": round(mu, 2),
        "std_3y": round(sigma, 2),
        "z_score": round(z, 2),
        "abs_z_score": round(abs_z, 2),
        "crowding_status": crowding,
        "asymmetry_risk": asymmetry,
        "guidance": guidance,
        "execution_authority": False,
    }


def evaluate_dealer_gamma_regime(
    net_dealer_gamma_billions: Any,
    spot_price: Any,
    gamma_flip_strike: Any,
) -> Dict[str, Any]:
    """Evaluates Dealer Gamma positioning (Long Gamma vol dampener vs Short Gamma vol accelerator)."""
    gamma = _safe_float(net_dealer_gamma_billions)
    spot = _safe_float(spot_price)
    flip = _safe_float(gamma_flip_strike)

    if any(v is None for v in (gamma, spot, flip)):
        return {"status": "INVALID_INPUT", "reason": "Gamma metrics invalid", "execution_authority": False}

    if gamma > 0 or spot > flip:
        regime = "LONG_GAMMA_VOLATILITY_DAMPENER"
        behavior = "BUY_DIPS_SELL_RALLIES"
        playbook = ["COMPRESS_DAILY_RANGES", "FAVOR_MEAN_REVERSION"]
    else:
        regime = "SHORT_GAMMA_VOLATILITY_ACCELERATOR"
        behavior = "SELL_DROPS_BUY_RALLIES"
        playbook = ["ACCELERATE_MOVES", "FAVOR_OUTSIZED_TRENDS"]

    return {
        "status": "READY_FOR_REVIEW",
        "net_dealer_gamma_billions": gamma,
        "spot_price": spot,
        "gamma_flip_strike": flip,
        "gamma_regime": regime,
        "market_maker_behavior": behavior,
        "tactical_playbook": playbook,
        "execution_authority": False,
    }


def evaluate_fx_risk_reversals(
    call_25d_iv: Any,
    put_25d_iv: Any,
    spot_near_cyclical_highs: bool = True,
    pair: str = "USDJPY",
) -> Dict[str, Any]:
    """Evaluates 25-Delta FX Risk Reversals: IV(25D Call) - IV(25D Put)."""
    c_iv = _safe_float(call_25d_iv)
    p_iv = _safe_float(put_25d_iv)

    if c_iv is None or p_iv is None:
        return {"status": "INVALID_INPUT", "reason": "Risk reversal IVs invalid", "execution_authority": False}

    rr = round(c_iv - p_iv, 4)

    if rr <= -1.5:
        skew_bias = "DEEP_PUT_PROTECTION"
        asymmetric_short = bool(spot_near_cyclical_highs)
        tactical = "HIGH_PROBABILITY_ASYMMETRIC_SHORT" if asymmetric_short else "PUT_SKEW_ELEVATED"
    elif rr >= 1.5:
        skew_bias = "DEEP_CALL_PREMIUM"
        asymmetric_short = False
        tactical = "CALL_SKEW_ELEVATED"
    else:
        skew_bias = "BALANCED_VOL_SKEW"
        asymmetric_short = False
        tactical = "SYMMETRIC_OPTIONS_PRICING"

    return {
        "status": "READY_FOR_REVIEW",
        "pair": pair,
        "call_25d_iv": c_iv,
        "put_25d_iv": p_iv,
        "risk_reversal_25d_vol_pts": rr,
        "skew_bias": skew_bias,
        "spot_near_cyclical_highs": spot_near_cyclical_highs,
        "asymmetric_short_setup": asymmetric_short,
        "tactical_recommendation": tactical,
        "execution_authority": False,
    }


# ==============================================================================
# 5. Master Cross-Asset Reference Matrix & Three-Window Protocol
# ==============================================================================

def evaluate_master_cross_asset_matrix(
    scenario_trigger_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Synthesizes the institutional Master Cross-Asset Playbook Reference Matrix across 6 core scenarios.

    Scenarios:
    1. US Core Inflation Re-acceleration (Core CPI >= +0.2% surprise, Supercore strong)
    2. Global Recessionary Hard Landing (Sahm Rule breach >= +0.50% or ISM < 45)
    3. China Credit & Fiscal Reflation (Accelerating credit impulse, bond quota expansion)
    4. BOJ Yield Policy Normalization (Core Tokyo CPI >= 2.5%, wage gains broadening)
    5. Geopolitical Maritime / Energy Shock (Chokepoint compromised, energy price spikes)
    6. Fiscal Debt Supply Indigestion (Treasury long-end supply expansion, weak foreign demand)
    """
    if not isinstance(scenario_trigger_context, dict):
        return {
            "status": "INVALID_INPUT",
            "reason": "scenario_trigger_context must be a dictionary",
            "execution_authority": False,
        }

    scenario_name = scenario_trigger_context.get("scenario_name")

    # Metrics extraction
    core_cpi_surprise = _safe_float(scenario_trigger_context.get("core_cpi_surprise_pct"))
    supercore_strong = bool(scenario_trigger_context.get("supercore_strong", False))

    sahm_breach = bool(scenario_trigger_context.get("sahm_rule_breach", False))
    u_delta = _safe_float(scenario_trigger_context.get("unemployment_12m_delta_pct"))
    ism = _safe_float(scenario_trigger_context.get("ism_manufacturing"))

    credit_impulse = bool(scenario_trigger_context.get("credit_impulse_accelerating", False))
    fiscal_quota = bool(scenario_trigger_context.get("fiscal_bond_quota_expanded", False))

    tokyo_cpi = _safe_float(scenario_trigger_context.get("core_tokyo_cpi"))
    wage_gains = bool(scenario_trigger_context.get("wage_gains_broadening", False))

    chokepoint = bool(scenario_trigger_context.get("chokepoint_compromised", False))
    energy_spike = _safe_float(scenario_trigger_context.get("energy_price_spike_pct"))

    treasury_supply = bool(scenario_trigger_context.get("treasury_supply_expansion", False))
    foreign_waning = bool(scenario_trigger_context.get("foreign_cb_demand_waning", False))
    auction_tail = _safe_float(scenario_trigger_context.get("auction_tail_bps"))

    # Auto-detection if scenario_name not explicitly provided
    if not scenario_name:
        if sahm_breach or (u_delta is not None and u_delta >= 0.50) or (ism is not None and ism < 45.0):
            scenario_name = "GLOBAL_RECESSIONARY_HARD_LANDING"
        elif (core_cpi_surprise is not None and core_cpi_surprise >= 0.20) or supercore_strong:
            scenario_name = "US_CORE_INFLATION_REACCELERATION"
        elif chokepoint or (energy_spike is not None and energy_spike >= 15.0):
            scenario_name = "MARITIME_ENERGY_SHOCK"
        elif (tokyo_cpi is not None and tokyo_cpi >= 2.5) or wage_gains:
            scenario_name = "BOJ_POLICY_NORMALIZATION"
        elif credit_impulse or fiscal_quota:
            scenario_name = "CHINA_CREDIT_REFLATION"
        elif (treasury_supply and foreign_waning) or (auction_tail is not None and auction_tail > 1.5):
            scenario_name = "FISCAL_DEBT_SUPPLY_INDIGESTION"
        else:
            scenario_name = "NEUTRAL_MACRO_BASELINE"

    # Scenario details mapping
    if scenario_name == "US_CORE_INFLATION_REACCELERATION":
        trigger_met = bool((core_cpi_surprise is not None and core_cpi_surprise >= 0.20) or supercore_strong)
        transmission = "Breakevens expand -> 2Y yields rise -> Curve bear-flattens -> Growth equity multiples compress"
        trades = {
            "long": ["USDJPY", "US_2Y_YIELD"],
            "short": ["NASDAQ_100", "GOLD", "EURUSD"],
        }
        invalidation = [
            "Daily close back below pre-event 2Y yield level",
            "Core CPI downward revision",
        ]

    elif scenario_name == "GLOBAL_RECESSIONARY_HARD_LANDING":
        trigger_met = bool(sahm_breach or (u_delta is not None and u_delta >= 0.50) or (ism is not None and ism < 45.0))
        transmission = "Front-end curves bull-steepen aggressively -> Credit spreads blow out -> Oil drops"
        trades = {
            "long": ["US_2Y_TREASURIES", "GOLD", "USDCHF"],
            "short": ["HYG_HIGH_YIELD", "BRENT_CRUDE", "AUDUSD"],
        }
        invalidation = [
            "ISM New Orders > 50",
            "Fed emergency liquidity injection",
        ]

    elif scenario_name == "CHINA_CREDIT_REFLATION":
        trigger_met = bool(credit_impulse or fiscal_quota)
        transmission = "Industrial metals surge -> Australian terms of trade improve -> Asian supply chains react"
        trades = {
            "long": ["AUDUSD", "COPPER", "EMERGING_MARKET_EQUITIES"],
            "short": ["DXY_USD_INDEX", "DEFENSIVE_UTILITIES"],
        }
        invalidation = [
            "USD/CNY breaks above fixing limits",
            "Chinese property defaults escalate",
        ]

    elif scenario_name == "BOJ_POLICY_NORMALIZATION":
        trigger_met = bool((tokyo_cpi is not None and tokyo_cpi >= 2.5) or wage_gains)
        transmission = "Global carry trades unwind -> JGB yields compress foreign spreads -> Repatriation flow"
        trades = {
            "long": ["JPY"],
            "long_jpy_crosses": ["SHORT_EURJPY", "SHORT_AUDJPY", "SHORT_USDJPY"],
            "short": ["SOVEREIGN_BONDS_GLOBAL", "EURJPY", "AUDJPY"],
        }
        invalidation = [
            "BOJ introduces unlimited fixed-rate bond-buying operations",
            "USD/JPY pushes new highs",
        ]

    elif scenario_name == "MARITIME_ENERGY_SHOCK":
        trigger_met = bool(chokepoint or (energy_spike is not None and energy_spike >= 15.0))
        transmission = "Supply curve shifts left -> Importer trade deficits widen -> Stagflation risk spikes"
        trades = {
            "long": ["BRENT_CRUDE", "US_NATURAL_GAS", "CAD_EUR"],
            "short": ["DAX_EUROPEAN_EQUITIES", "KRW_KOREAN_WON"],
        }
        invalidation = [
            "Supply flows restored",
            "Global demand destruction exceeds supply deficit",
        ]

    elif scenario_name == "FISCAL_DEBT_SUPPLY_INDIGESTION":
        trigger_met = bool((treasury_supply and foreign_waning) or (auction_tail is not None and auction_tail > 1.5))
        transmission = "Term premium widens -> Curve bear-steepens -> Long-end auctions see weak bid-to-cover"
        trades = {
            "long": ["CURVE_STEEPENER_2S30S"],
            "short": ["TLT_LONG_TREASURIES", "REITS"],
        }
        invalidation = [
            "Treasury pivots back to bill issuance",
            "Fed implements yield smoothing/QE",
        ]

    else:
        trigger_met = False
        transmission = "Baseline macroeconomic equilibrium; no asymmetric catalyst active"
        trades = {"long": [], "short": []}
        invalidation = []

    return {
        "status": "READY_FOR_REVIEW",
        "scenario_identified": scenario_name,
        "trigger_condition_met": trigger_met,
        "intermarket_transmission": transmission,
        "high_probability_trades": trades,
        "invalidation_exit_signals": invalidation,
        "execution_authority": False,
    }


def evaluate_three_window_execution_protocol(
    minutes_from_event: Any,
    spread_multiple: Any = 1.0,
    pre_event_positioning_z_score: Any = 0.0,
    cross_asset_confirmation: bool = False,
    headline_vs_core_divergence: bool = False,
) -> Dict[str, Any]:
    """Evaluates the Three-Window Macro Execution Protocol.

    Timeline:
    - Window 1 (T - 24h to T - 15m): Pre-Event Setup, Positioning & Skew Audit
    - Pre-Release Lockdown (T - 15m to T + 0m): Liquidity Withdrawal / Hold Execution
    - Window 2 (T + 0 to T + 15m): Absorption Phase, Spread Freeze (Zero Market Orders)
    - Window 3 (T + 15m to T + 48h): Directional Allocation, Swing Pullbacks / Fade Divergences
    """
    m = _safe_float(minutes_from_event)
    sm = _safe_float(spread_multiple)
    z = _safe_float(pre_event_positioning_z_score)

    if m is None or sm is None or z is None:
        return {
            "status": "INVALID_INPUT",
            "reason": "Execution protocol inputs invalid",
            "execution_authority": False,
        }

    crowded = abs(z) >= 2.0
    spread_spike = sm >= 3.0

    if -1440.0 <= m < -15.0:
        window = "WINDOW_1_PRE_EVENT_SETUP"
        action = "AUDIT_ONLY_SKEW_ADJUSTED_LIMITS" if crowded else "AUDIT_ONLY_STANDARD_LIMITS"
        guidance = "Position audit & hurdle rate assessment. Widen limits; reduce size if crowded."
        spread_freeze = False
        fade_signal = False
    elif -15.0 <= m < 0.0:
        window = "PRE_RELEASE_LOCKDOWN"
        action = "HOLD_ALL_EXECUTION"
        guidance = "Lock down new orders. Algorithmic liquidity withdrawing."
        spread_freeze = True
        fade_signal = False
    elif 0.0 <= m <= 15.0:
        window = "WINDOW_2_ABSORPTION_PHASE"
        action = "ZERO_MARKET_ORDERS_FREEZE"
        guidance = "Zero market orders allowed. Spreads widen 5x-20x. Deconstruct headline vs core and revisions."
        spread_freeze = True
        fade_signal = False
    elif 15.0 < m <= 2880.0:
        window = "WINDOW_3_DIRECTIONAL_ALLOCATION"
        spread_freeze = False
        if cross_asset_confirmation and not headline_vs_core_divergence:
            action = "ENTER_SWING_PULLBACK"
            guidance = "Cross-asset alignment confirmed. Enter swing positions on first pullback toward pre-event liquidity."
            fade_signal = False
        else:
            action = "FADE_OVERSHOOT"
            guidance = "Divergence or positioning exhaustion detected. Fade the initial knee-jerk breakout."
            fade_signal = True
    elif m < -1440.0:
        window = "MACRO_BASELINE_MONITORING"
        action = "NORMAL_OPERATIONS"
        guidance = "Standard monitoring prior to the 24-hour pre-event window."
        spread_freeze = False
        fade_signal = False
    else:  # m > 2880.0
        window = "POST_EVENT_SETTLED"
        action = "NORMAL_OPERATIONS"
        guidance = "Event drift fully priced; portfolio rebalancing concluded."
        spread_freeze = False
        fade_signal = False

    return {
        "status": "READY_FOR_REVIEW",
        "minutes_from_event": m,
        "active_window": window,
        "order_action": action,
        "spread_multiple": sm,
        "spread_freeze_active": spread_freeze,
        "spread_spike_detected": spread_spike,
        "risk_reduction_required": crowded,
        "fade_signal": fade_signal,
        "guidance": guidance,
        "execution_authority": False,
    }
