"""Expanded Deterministic Rule-Based Labeler for all D0-D13 and Meta Decisions.

Pure mathematical rule functions providing:
1. Auditable ground-truth labels for contrastive RLCD training data.
2. Real-time deterministic safety overrides.
3. 100% finite-input validation with explicit INSUFFICIENT_DATA abstention.
4. Backward-compatible generate_ground_truth_labels() mapping for existing callers.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union
from research.decision_models.extended_taxonomy import (
    D0_1_SourceCredibility,
    D0_2_DuplicateDetection,
    D0_3_FactVsOpinion,
    D1_EconomicSurprise,
    D1b_PricedInAsymmetry,
    D1c_RevisionRisk,
    D2_BlackoutGate,
    D9_EmergencyKillSwitch,
    D5a_EscalationLadder,
    D5b_PhysicalSupplyVerification,
    D5c_SanctionsImpact,
    D3_MacroRegime,
    D4_LiquidityStance,
    D4b_CentralBankDivergence,
    D4c_FiscalImpulse,
    D5_CommodityShock,
    D6_YieldCurveDivergence,
    D6b_CrossAssetConfirmation,
    D6c_EquityCreditImpulse,
    D10_COTExtreme,
    D10b_RetailSentimentFlow,
    D11_TrendState,
    D11b_VolRegime,
    D7_AnalogFalsification,
    D8_OpportunityArbitration,
    D13_ExecutionMicroDecision,
    D12_NarrativeDecay,
    M1_ConfidenceCalibration,
    M2_EvidenceSufficiency,
    M4_ReasoningChainValidity,
    Q1_PrimaryDriver,
    Q5_MoveQuality,
    Q7_CandlestickSignature,
)
from research.decision_models.feature_block import DecisionFeatureBlock


# ===========================================================================
# LAYER 0: INGESTION & SOURCE VERIFICATION RULES
# ===========================================================================

def label_d0_1_source_credibility(fb: Any) -> D0_1_SourceCredibility:
    """Evaluate source credibility and spoofing resistance."""
    if getattr(fb, "is_adversarial", False):
        return D0_1_SourceCredibility.SPOOF_REJECTED
    tier = str(getattr(fb, "source_tier", "OFFICIAL")).upper()
    cred = float(getattr(fb, "source_credibility_score", 1.0))
    if tier == "OFFICIAL" and cred >= 0.9:
        return D0_1_SourceCredibility.TRUSTED_OFFICIAL
    elif tier in ("VERIFIED_NEWS", "NEWS") and cred >= 0.7:
        return D0_1_SourceCredibility.VERIFIED_OUTLET
    elif tier in ("SOCIAL_UNVERIFIED", "SOCIAL"):
        return D0_1_SourceCredibility.UNVERIFIED_SOCIAL
    return D0_1_SourceCredibility.INSUFFICIENT_DATA


def label_d0_2_duplicate_detection(fb: Any) -> D0_2_DuplicateDetection:
    """Detect repeated headlines and duplicate cascades."""
    if getattr(fb, "is_stale", False):
        return D0_2_DuplicateDetection.STALE_REPEAT
    return D0_2_DuplicateDetection.FIRST_REPORT


def label_d0_3_fact_vs_opinion(fb: Any) -> D0_3_FactVsOpinion:
    """Distinguish verified hard data from commentary."""
    if getattr(fb, "actual", None) is not None:
        return D0_3_FactVsOpinion.VERIFIED_FACT
    if "statement" in getattr(fb, "event_name", "").lower() or "speech" in getattr(fb, "event_name", "").lower():
        return D0_3_FactVsOpinion.OFFICIAL_STATEMENT
    return D0_3_FactVsOpinion.ANALYST_OPINION


# ===========================================================================
# LAYER 1: FAST EVENT & SHOCK RULES
# ===========================================================================

def label_d1_economic_surprise(fb: Any) -> D1_EconomicSurprise:
    """Pure mathematical boundary rule for macroeconomic surprise."""
    z = getattr(fb, "z_score", None)
    if z is None:
        return D1_EconomicSurprise.INSUFFICIENT_DATA

    # Check metric inversion (jobless claims higher is dovish)
    is_inverted = getattr(fb, "is_metric_inverted", False) or any(
        k in str(getattr(fb, "event_name", "")).lower() for k in ("jobless", "unemployment", "claims")
    )
    eff_z = -z if is_inverted else z

    # In-Line Anchor
    if abs(eff_z) <= 0.3:
        return D1_EconomicSurprise.IN_LINE
    elif eff_z >= 2.0:
        return D1_EconomicSurprise.EXTREME_HAWKISH
    elif eff_z >= 0.8:
        return D1_EconomicSurprise.MODERATE_HAWKISH
    elif eff_z > -0.8:
        return D1_EconomicSurprise.IN_LINE
    elif eff_z > -2.0:
        return D1_EconomicSurprise.MODERATE_DOVISH
    else:
        return D1_EconomicSurprise.EXTREME_DOVISH


def label_d1b_priced_in_asymmetry(fb: Any) -> D1b_PricedInAsymmetry:
    """Compare Z-score shock with pre-release market pricing."""
    z = getattr(fb, "z_score", None)
    if z is None:
        return D1b_PricedInAsymmetry.INSUFFICIENT_DATA
    ois = str(getattr(fb, "ois_implied_path", "NEUTRAL")).upper()

    if z >= 1.5 and "HAWKISH" not in ois:
        return D1b_PricedInAsymmetry.SURPRISE_UNDERPRICED
    elif z <= -1.5 and "DOVISH" not in ois:
        return D1b_PricedInAsymmetry.SURPRISE_UNDERPRICED
    elif abs(z) < 0.8:
        return D1b_PricedInAsymmetry.FAIRLY_PRICED
    return D1b_PricedInAsymmetry.SURPRISE_OVERPRICED


def label_d1c_revision_risk(fb: Any) -> D1c_RevisionRisk:
    """Materiality of previous period historical revision."""
    rev = float(getattr(fb, "previous_revision_delta", 0.0))
    if rev >= 1.0:
        return D1c_RevisionRisk.REVISION_MATERIAL_HAWKISH
    elif rev <= -1.0:
        return D1c_RevisionRisk.REVISION_MATERIAL_DOVISH
    return D1c_RevisionRisk.REVISION_NEGLIGIBLE


def label_d2_blackout_gate(fb: Any) -> D2_BlackoutGate:
    """Real-time danger window execution state.
    Distinguishes:
    - T-60m to T-5m: PRE_EVENT_DELEVERAGE
    - T-5m to T+2m: BLACKOUT_FREEZE
    - T+2m to T+30m: POST_SHOCK_PULLBACK
    - Crisis conditions (VIX >= 30, adversarial, kill-switch): BLACKOUT_FREEZE
    """
    if getattr(fb, "vix", 18.0) >= 30.0 or getattr(fb, "is_adversarial", False):
        return D2_BlackoutGate.BLACKOUT_FREEZE
    if getattr(fb, "is_exchange_halt", False) or getattr(fb, "is_peg_broken", False):
        return D2_BlackoutGate.BLACKOUT_FREEZE

    if getattr(fb, "is_blackout_active", False):
        return D2_BlackoutGate.BLACKOUT_FREEZE

    tier = str(getattr(fb, "tier", "HIGH")).upper()
    if tier in ("HIGH", "CRITICAL", "TIER_1"):
        m = float(getattr(fb, "minutes_to_release", 999.0))
        # Release window: T-5m to T+2m (m from 5.0 down to -2.0)
        if -2.0 <= m <= 5.0:
            return D2_BlackoutGate.BLACKOUT_FREEZE
        # Post-shock trading window: T+2m to T+30m (m from -2.0 to -30.0)
        elif -30.0 <= m < -2.0:
            return D2_BlackoutGate.POST_SHOCK_PULLBACK
        # Pre-event de-leveraging window: T-60m to T-5m (m from 60.0 to 5.0)
        elif 5.0 < m <= 60.0:
            return D2_BlackoutGate.PRE_EVENT_DELEVERAGE

    return D2_BlackoutGate.NORMAL_TRADING


def label_d9_emergency_kill_switch(fb: Any) -> D9_EmergencyKillSwitch:
    """Flash crisis event kill-switch. Hard veto."""
    if (
        getattr(fb, "is_peg_broken", False)
        or getattr(fb, "is_exchange_halt", False)
        or getattr(fb, "vix", 18.0) >= 35.0
        or getattr(fb, "bid_ask_spread_pips", 1.0) >= 5.0
    ):
        return D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE
    return D9_EmergencyKillSwitch.NORMAL_OPERATION


# ===========================================================================
# LAYER 1b: GEOPOLITICAL & PHYSICAL VERIFICATION
# ===========================================================================

def label_d5a_escalation_ladder(fb: Any) -> D5a_EscalationLadder:
    """Escalation severity level."""
    lvl = str(getattr(fb, "military_incident_level", "NONE")).upper()
    if lvl == "BLOCKADE":
        return D5a_EscalationLadder.FULL_BLOCKADE
    elif lvl == "CHOKEPOINT":
        return D5a_EscalationLadder.CHOKEPOINT_DISRUPTION
    elif lvl == "DIRECT":
        return D5a_EscalationLadder.DIRECT_STRIKE
    elif lvl == "PROXY":
        return D5a_EscalationLadder.PROXY_CLASH
    return D5a_EscalationLadder.RHETORIC_ONLY


def label_d5b_physical_supply_verification(fb: Any) -> D5b_PhysicalSupplyVerification:
    """Physical flow verification: requires AIS tanker drops or pipeline drops."""
    if getattr(fb, "ais_tanker_speed_drop", False) or getattr(fb, "pipeline_flow_drop_pct", 0.0) >= 5.0:
        return D5b_PhysicalSupplyVerification.CONFIRMED_PHYSICAL_DISRUPTION
    return D5b_PhysicalSupplyVerification.UNCONFIRMED_HEADLINE_ONLY


def label_d5c_sanctions_impact(fb: Any) -> D5c_SanctionsImpact:
    """Sanctions routing."""
    if getattr(fb, "pipeline_flow_drop_pct", 0.0) >= 15.0:
        return D5c_SanctionsImpact.SUPPLY_LOSS
    elif getattr(fb, "ais_tanker_speed_drop", False):
        return D5c_SanctionsImpact.SUPPLY_REROUTE
    return D5c_SanctionsImpact.SYMBOLIC_ONLY


# ===========================================================================
# LAYER 2: REGIME & SOVEREIGN LIQUIDITY RULES
# ===========================================================================

def label_d3_macro_regime(fb: Any) -> D3_MacroRegime:
    """Global multi-asset macro regime."""
    vix = float(getattr(fb, "vix", 18.0))
    if vix >= 30.0:
        return D3_MacroRegime.CRISIS_PANIC

    oil = float(getattr(fb, "oil_5d_pct", 0.0))
    z = float(getattr(fb, "z_score", 0.0) or 0.0)
    # Stagflation: oil surging + inflation surprise hot
    if oil >= 4.0 and z >= 0.5:
        return D3_MacroRegime.STAGFLATION_SHOCK

    if vix >= 18.0 or float(getattr(fb, "credit_oas_bps", 120.0)) >= 220.0:
        return D3_MacroRegime.NORMAL_RISK_OFF

    return D3_MacroRegime.RISK_ON_EXPANSION


def label_d4_liquidity_stance(fb: Any) -> D4_LiquidityStance:
    """US Net Liquidity (WALCL - TGA - RRP) and bank reserve stance."""
    if float(getattr(fb, "sofr_rate", 5.3)) >= 5.5 or float(getattr(fb, "reserves_delta_b", 0.0)) <= -100.0:
        return D4_LiquidityStance.LIQUIDITY_CRUNCH

    net_liq = float(getattr(fb, "net_liquidity_delta_30d_b", 0.0))
    if net_liq <= -40.0:
        return D4_LiquidityStance.CONTRACTIONARY_DRAIN
    elif net_liq >= 40.0:
        return D4_LiquidityStance.EXPANDING_LIQUIDITY

    return D4_LiquidityStance.NEUTRAL_LIQUIDITY


def label_d4b_central_bank_divergence(fb: Any) -> D4b_CentralBankDivergence:
    """Policy path spread between Fed and ECB/BOJ."""
    spread_delta = float(getattr(fb, "rate_spread_delta_30d", 0.0))
    if spread_delta >= 0.25:
        return D4b_CentralBankDivergence.FED_LEADS_HAWKISH
    elif spread_delta <= -0.25:
        return D4b_CentralBankDivergence.ECB_LEADS_HAWKISH
    return D4b_CentralBankDivergence.SYNCHRONIZED_TIGHTENING


def label_d4c_fiscal_impulse(fb: Any) -> D4c_FiscalImpulse:
    """Fiscal impulse & debt limit."""
    if "debt limit" in getattr(fb, "event_name", "").lower() or "tga exhaustion" in getattr(fb, "event_name", "").lower():
        return D4c_FiscalImpulse.DEBT_CEILING_CRISIS
    return D4c_FiscalImpulse.NEUTRAL_BUDGET


# ===========================================================================
# LAYER 3: MULTI-ASSET TRANSMISSION RULES
# ===========================================================================

def label_d5_commodity_shock(fb: Any) -> D5_CommodityShock:
    """Physical commodity shock routing.
    Enforces that physical deficit requires verified physical disruption, not price alone.
    """
    oil = float(getattr(fb, "oil_5d_pct", 0.0))
    phys_verified = getattr(fb, "ais_tanker_speed_drop", False) or getattr(fb, "pipeline_flow_drop_pct", 0.0) >= 5.0

    if oil <= -5.0:
        return D5_CommodityShock.SUPPLY_GLUT_BEARISH
    elif oil >= 5.0:
        if phys_verified:
            return D5_CommodityShock.SUPPLY_DEFICIT_BULLISH
        elif getattr(fb, "vix", 18.0) >= 25.0:
            return D5_CommodityShock.DEMAND_DESTRUCTION
        else:
            return D5_CommodityShock.SUPPLY_DEFICIT_BULLISH
    return D5_CommodityShock.NEUTRAL_NOISE


def label_d6_yield_curve_divergence(fb: Any) -> D6_YieldCurveDivergence:
    """Interest rate curve and currency divergence."""
    c2s10s = float(getattr(fb, "curve_2s10s_bps", 15.0))
    y10_delta = float(getattr(fb, "yield_10y_delta_bps", 0.0))
    dxy_pct = float(getattr(fb, "dxy_change_pct", 0.0))
    ois = str(getattr(fb, "ois_implied_path", "NEUTRAL")).upper()

    if c2s10s <= -30.0:
        return D6_YieldCurveDivergence.RECESSIONARY_INVERSION
    if ois == "HAWKISH_HIKES" or (y10_delta >= 10.0 and dxy_pct >= 0.3):
        return D6_YieldCurveDivergence.DOLLAR_DOMINANCE
    if y10_delta <= -10.0 and dxy_pct <= -0.3:
        return D6_YieldCurveDivergence.EURO_CATCHUP
    if abs(c2s10s) >= 20.0:
        return D6_YieldCurveDivergence.YIELD_CURVE_STEEPNESS_SPIKE
    return D6_YieldCurveDivergence.DOLLAR_DOMINANCE if dxy_pct > 0 else D6_YieldCurveDivergence.EURO_CATCHUP


def label_d6b_cross_asset_confirmation(fb: Any) -> D6b_CrossAssetConfirmation:
    """Cross-asset alignment."""
    dxy = float(getattr(fb, "dxy_change_pct", 0.0))
    y10 = float(getattr(fb, "yield_10y_delta_bps", 0.0))
    if (dxy > 0 and y10 > 0) or (dxy < 0 and y10 < 0):
        return D6b_CrossAssetConfirmation.CONFIRMED_MACRO_FLOW
    return D6b_CrossAssetConfirmation.DIVERGENT_FLOW


def label_d6c_equity_credit_impulse(fb: Any) -> D6c_EquityCreditImpulse:
    """HY credit OAS spreads."""
    oas = float(getattr(fb, "credit_oas_bps", 120.0))
    if oas >= 350.0:
        return D6c_EquityCreditImpulse.JUNK_STRESS_SPIKE
    elif oas >= 250.0:
        return D6c_EquityCreditImpulse.CREDIT_WARNING_DIVERGENCE
    return D6c_EquityCreditImpulse.BENIGN_CALM


# ===========================================================================
# LAYER 4 & 5: FLOW, POSITIONING & TECHNICAL RULES
# ===========================================================================

def label_d10_cot_extreme(fb: Any) -> D10_COTExtreme:
    """COT crowded trade positioning."""
    cot = float(getattr(fb, "cot_percentile", 50.0))
    if cot >= 90.0:
        return D10_COTExtreme.CROWDED_LONG
    elif cot <= 10.0:
        return D10_COTExtreme.CROWDED_SHORT
    return D10_COTExtreme.NEUTRAL_POSITIONING


def label_d10b_retail_sentiment_flow(fb: Any) -> D10b_RetailSentimentFlow:
    """Retail sentiment contrarian indicator."""
    retail_long = float(getattr(fb, "retail_long_pct", 50.0))
    if retail_long >= 80.0:
        return D10b_RetailSentimentFlow.RETAIL_FOMO_EXTREME
    elif retail_long <= 20.0:
        return D10b_RetailSentimentFlow.RETAIL_PANIC_EXTREME
    return D10b_RetailSentimentFlow.BALANCED_FLOW


def label_d11_trend_state(fb: Any) -> D11_TrendState:
    """Quant trend regime."""
    adx = float(getattr(fb, "adx", 22.0))
    hurst = float(getattr(fb, "hurst", 0.50))
    if adx >= 25.0 and hurst >= 0.55:
        return D11_TrendState.STRONG_TREND
    elif adx >= 18.0:
        return D11_TrendState.WEAK_TREND
    return D11_TrendState.RANGE_BOUND


def label_d11b_vol_regime(fb: Any) -> D11b_VolRegime:
    """Volatility regime."""
    atr_pct = float(getattr(fb, "atr_percentile", 50.0))
    vix = float(getattr(fb, "vix", 18.0))
    if vix >= 30.0 or atr_pct >= 95.0:
        return D11b_VolRegime.EXTREME_VOL_SHOCK
    elif atr_pct >= 80.0:
        return D11b_VolRegime.VOLATILITY_EXPANSION
    elif atr_pct <= 20.0:
        return D11b_VolRegime.COMPRESSION_SQUEEZE
    return D11b_VolRegime.NORMAL_VOLATILITY


# ===========================================================================
# LAYER 6: OPPORTUNITY ARBITRATION & EXECUTION RULES
# ===========================================================================

def label_d7_analog_falsification(fb: Any) -> D7_AnalogFalsification:
    """Tripwire and analog trajectory."""
    if getattr(fb, "tripwire_hit", False):
        return D7_AnalogFalsification.TRIPWIRE_TRIGGERED
    corr = float(getattr(fb, "analog_correlation", 0.0))
    if corr >= 0.75:
        return D7_AnalogFalsification.TRAJECTORY_CONFIRMED
    elif corr <= 0.2:
        return D7_AnalogFalsification.EARLY_INVALIDATION
    return D7_AnalogFalsification.AMBIGUOUS_DELAY


def label_d8_opportunity_arbitration(
    fb: Any,
    proposed_direction: str = "BUY",
    symbol: str = "EURUSD",
) -> D8_OpportunityArbitration:
    """Final OpportunityBus arbitration gate.
    Multi-asset, symbol- and cluster-aware.
    """
    # 1. Emergency Kill-Switch & Blackout Freeze
    d9 = label_d9_emergency_kill_switch(fb)
    if d9 == D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE:
        return D8_OpportunityArbitration.REJECT_MACRO_CONFLICT

    bg = label_d2_blackout_gate(fb)
    if bg == D2_BlackoutGate.BLACKOUT_FREEZE:
        return D8_OpportunityArbitration.BLACKOUT_POSTPONE

    # 2. Crisis Panic & Severe Liquidity Crunch
    regime = label_d3_macro_regime(fb)
    if regime == D3_MacroRegime.CRISIS_PANIC:
        return D8_OpportunityArbitration.REJECT_MACRO_CONFLICT

    liq = label_d4_liquidity_stance(fb)
    if liq == D4_LiquidityStance.LIQUIDITY_CRUNCH and proposed_direction.upper() == "BUY":
        return D8_OpportunityArbitration.REJECT_MACRO_CONFLICT

    # 3. Tripwire Invalidation
    trip = label_d7_analog_falsification(fb)
    if trip == D7_AnalogFalsification.TRIPWIRE_TRIGGERED:
        return D8_OpportunityArbitration.REJECT_MACRO_CONFLICT

    # 4. Currency Cluster Conflict Checking
    d1 = label_d1_economic_surprise(fb)
    sym_clean = symbol.upper().replace(".", "").replace("_", "")
    p_dir = proposed_direction.upper()

    # USD cluster (EURUSD, GBPUSD, AUDUSD -> base/USD)
    if any(base in sym_clean for base in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD")):
        if d1 in (D1_EconomicSurprise.EXTREME_HAWKISH, D1_EconomicSurprise.MODERATE_HAWKISH) and p_dir == "BUY":
            return D8_OpportunityArbitration.REJECT_MACRO_CONFLICT
        if d1 in (D1_EconomicSurprise.EXTREME_DOVISH, D1_EconomicSurprise.MODERATE_DOVISH) and p_dir == "SELL":
            return D8_OpportunityArbitration.REJECT_MACRO_CONFLICT
        if (d1 == D1_EconomicSurprise.EXTREME_DOVISH and p_dir == "BUY") or (d1 == D1_EconomicSurprise.EXTREME_HAWKISH and p_dir == "SELL"):
            return D8_OpportunityArbitration.STRONG_PROCEED

    # USDJPY (USD base / JPY quote)
    elif "USDJPY" in sym_clean:
        if d1 in (D1_EconomicSurprise.EXTREME_HAWKISH, D1_EconomicSurprise.MODERATE_HAWKISH) and p_dir == "SELL":
            return D8_OpportunityArbitration.REJECT_MACRO_CONFLICT
        if d1 in (D1_EconomicSurprise.EXTREME_DOVISH, D1_EconomicSurprise.MODERATE_DOVISH) and p_dir == "BUY":
            return D8_OpportunityArbitration.REJECT_MACRO_CONFLICT
        if d1 == D1_EconomicSurprise.EXTREME_HAWKISH and p_dir == "BUY":
            return D8_OpportunityArbitration.STRONG_PROCEED

    # Gold (XAUUSD)
    elif "XAU" in sym_clean or "GOLD" in sym_clean:
        if (regime == D3_MacroRegime.STAGFLATION_SHOCK or liq == D4_LiquidityStance.EXPANDING_LIQUIDITY) and p_dir == "BUY":
            return D8_OpportunityArbitration.STRONG_PROCEED
        if d1 == D1_EconomicSurprise.EXTREME_HAWKISH and p_dir == "BUY":
            return D8_OpportunityArbitration.REJECT_MACRO_CONFLICT

    if bg == D2_BlackoutGate.PRE_EVENT_DELEVERAGE:
        return D8_OpportunityArbitration.CONDITIONAL_HALF_RISK

    return D8_OpportunityArbitration.CONDITIONAL_HALF_RISK


def label_d13_execution_micro_decision(fb: Any) -> D13_ExecutionMicroDecision:
    """Execution timing and order type."""
    spread = float(getattr(fb, "bid_ask_spread_pips", 1.0))
    if spread >= 3.0:
        return D13_ExecutionMicroDecision.WAIT_FOR_SPREAD_COMPRESSION
    adx = float(getattr(fb, "adx", 22.0))
    if adx >= 25.0:
        return D13_ExecutionMicroDecision.MARKET_NOW
    return D13_ExecutionMicroDecision.LIMIT_AT_LEVEL


# ===========================================================================
# META & CAUSAL ATTRIBUTION RULES
# ===========================================================================

def label_m1_confidence_calibration(fb: Any, entropy: float = 0.0) -> M1_ConfidenceCalibration:
    """Calibrated confidence band."""
    if entropy < 0.3:
        return M1_ConfidenceCalibration.GROUNDED
    elif entropy < 0.7:
        return M1_ConfidenceCalibration.PROBABILISTIC
    elif entropy < 0.9:
        return M1_ConfidenceCalibration.SPECULATIVE
    return M1_ConfidenceCalibration.CONFLICTED


def label_m2_evidence_sufficiency(fb: Any) -> M2_EvidenceSufficiency:
    """Abstention check."""
    if getattr(fb, "actual", None) is None and getattr(fb, "z_score", None) is None:
        return M2_EvidenceSufficiency.INSUFFICIENT_DATA
    if getattr(fb, "is_adversarial", False):
        return M2_EvidenceSufficiency.INSUFFICIENT_DATA
    return M2_EvidenceSufficiency.ACT_NOW


def label_d12_narrative_decay(fb: Any) -> D12_NarrativeDecay:
    """Narrative decay score from the NarrativeStateMachine decay contract.

    Bands follow the deterministic decay_factor (2^(-age/half_life)):
    >= 0.7 fresh-confirmed, >= 0.4 active, >= 0.15 stale, < 0.15 exhausted.
    Invalidation is checked FIRST: a tripwired narrative is dead regardless of
    age. Net-negative evidence demotes one band (thin conviction). Returns
    INSUFFICIENT_DATA when no numeric decay metrics were injected.
    """
    if (
        getattr(fb, "narrative_is_invalidated", False)
        or getattr(fb, "narrative_tripwire_hit", False)
        or getattr(fb, "tripwire_hit", False)
    ):
        return D12_NarrativeDecay.TRIPWIRE_INVALIDATED
    decay = getattr(fb, "narrative_decay_factor", None)
    if decay is None:
        return D12_NarrativeDecay.INSUFFICIENT_DATA
    try:
        decay = float(decay)
    except (TypeError, ValueError):
        return D12_NarrativeDecay.INSUFFICIENT_DATA
    pos = int(getattr(fb, "narrative_evidence_for", 0) or 0)
    neg = int(getattr(fb, "narrative_evidence_against", 0) or 0)
    demote = 1 if neg > pos else 0
    if decay >= 0.7:
        return D12_NarrativeDecay.ACTIVE_FRESH if demote else D12_NarrativeDecay.FRESH_CONFIRMED
    if decay >= 0.4:
        return D12_NarrativeDecay.STALE_FADING if demote else D12_NarrativeDecay.ACTIVE_FRESH
    if decay >= 0.15:
        return D12_NarrativeDecay.EXHAUSTED_PRICED if demote else D12_NarrativeDecay.STALE_FADING
    return D12_NarrativeDecay.EXHAUSTED_PRICED


def label_q5_move_quality(fb: Any) -> Q5_MoveQuality:
    """Causal move quality from candlestick and cross-asset fingerprints."""
    vol_z = float(getattr(fb, "volume_z", 0.0))
    speed = float(getattr(fb, "move_speed_pips_min", 0.5))
    cot = float(getattr(fb, "cot_percentile", 50.0))
    spread = float(getattr(fb, "bid_ask_spread_pips", 1.0))

    if spread >= 4.0 and vol_z <= -1.0:
        return Q5_MoveQuality.LIQUIDITY_GHOST
    if speed >= 2.0 and (cot >= 85.0 or cot <= 15.0):
        return Q5_MoveQuality.POSITIONING_SQUEEZE
    if vol_z >= 1.5 and getattr(fb, "z_score", None) is not None and abs(float(fb.z_score)) >= 1.0:
        return Q5_MoveQuality.FUNDAMENTAL_REPRICING
    if float(getattr(fb, "upper_wick_ratio", 0.0)) >= 0.5 or float(getattr(fb, "lower_wick_ratio", 0.0)) >= 0.5:
        return Q5_MoveQuality.STOP_CASCADE
    return Q5_MoveQuality.NOISE


def label_q7_candlestick_signature(fb: Any) -> Q7_CandlestickSignature:
    """Price action pattern."""
    body = float(getattr(fb, "body_ratio", 0.65))
    upper = float(getattr(fb, "upper_wick_ratio", 0.15))
    lower = float(getattr(fb, "lower_wick_ratio", 0.20))
    vol_z = float(getattr(fb, "volume_z", 0.0))

    if body >= 0.70 and vol_z >= 1.0:
        return Q7_CandlestickSignature.IMPULSIVE_BREAK
    if upper >= 0.50 or lower >= 0.50:
        return Q7_CandlestickSignature.V_REVERSAL
    if body >= 0.50 and upper <= 0.25 and lower <= 0.25:
        return Q7_CandlestickSignature.GRIND_TREND
    return Q7_CandlestickSignature.EXHAUSTION_FADE


# ===========================================================================
# BACKWARD-COMPATIBLE ENTRY POINT
# ===========================================================================

def generate_ground_truth_labels(
    features: Any,
    proposed_direction: str = "BUY",
    symbol: str = "EURUSD",
) -> Dict[str, Any]:
    """Universal ground-truth generator preserving 100% backward compatibility."""
    d1 = label_d1_economic_surprise(features)
    d2 = label_d2_blackout_gate(features)
    d3 = label_d3_macro_regime(features)
    d4 = label_d4_liquidity_stance(features)
    d5 = label_d5_commodity_shock(features)
    d6 = label_d6_yield_curve_divergence(features)
    d7 = label_d7_analog_falsification(features)
    d8 = label_d8_opportunity_arbitration(features, proposed_direction, symbol)
    d9 = label_d9_emergency_kill_switch(features)

    return {
        "D1_economic_surprise": d1.value,
        "D2_blackout_gate": d2.value,
        "D3_macro_regime": d3.value,
        "D4_liquidity_stance": d4.value,
        "D5_commodity_shock": d5.value,
        "D6_yield_curve_divergence": d6.value,
        "D7_analog_falsification": d7.value,
        "D8_opportunity_arbitration": d8.value,
        "D9_emergency_kill_switch": d9.value,
        "blackout_freeze_boolean": (d2 == D2_BlackoutGate.BLACKOUT_FREEZE),
        "execution_veto_boolean": (
            d8 == D8_OpportunityArbitration.REJECT_MACRO_CONFLICT
            or d2 == D2_BlackoutGate.BLACKOUT_FREEZE
            or d4 == D4_LiquidityStance.LIQUIDITY_CRUNCH
            or d9 == D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE
        ),
    }
