"""Deterministic Macro Ground-Truth Label Generator.

Provides mathematically grounded, rule-based label generation for macro decisions D1–D8.
Guarantees 100% label consistency without human annotator drift, serving as the
ground-truth supervisor for training and safety post-validation.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from research.decision_models.macro_ontology import (
    BlackoutGate,
    CaseFalsification,
    CommodityShock,
    EconomicSurprise,
    LiquidityStance,
    MacroContextFeatures,
    MacroRegime,
    OpportunityArbitration,
    YieldCurveDivergence,
)


def label_d1_economic_surprise(features: MacroContextFeatures) -> EconomicSurprise:
    """Ground truth for D1: Economic Surprise Shock Classification.
    Thresholds:
      Z >= +2.0  -> EXTREME_HAWKISH
      Z in [+0.8, +2.0) -> MODERATE_HAWKISH
      Z in (-0.8, +0.8) -> IN_LINE
      Z in (-2.0, -0.8] -> MODERATE_DOVISH
      Z <= -2.0  -> EXTREME_DOVISH
    """
    z = features.z_score
    if z is None:
        # Fallback to in-line if no quantitative variance exists
        return EconomicSurprise.IN_LINE

    # Note: For inverted metrics (e.g. Jobless Claims or Unemployment rate),
    # higher actual = weaker labor market = dovish pressure.
    is_inverted = any(term in features.event_name.lower() for term in ("jobless", "unemployment", "claims"))

    effective_z = -z if is_inverted else z

    if effective_z >= 2.0:
        return EconomicSurprise.EXTREME_HAWKISH
    elif effective_z >= 0.8:
        return EconomicSurprise.MODERATE_HAWKISH
    elif effective_z > -0.8:
        return EconomicSurprise.IN_LINE
    elif effective_z > -2.0:
        return EconomicSurprise.MODERATE_DOVISH
    else:
        return EconomicSurprise.EXTREME_DOVISH


def label_d2_blackout_gate(features: MacroContextFeatures) -> BlackoutGate:
    """Ground truth for D2: Real-time News Blackout and De-risking Gate."""
    # Emergency conditions trigger immediate blackout
    if features.vix >= 30.0 or features.is_adversarial:
        return BlackoutGate.BLACKOUT_FREEZE

    if features.tier == "HIGH":
        m = features.minutes_to_release
        # T-5m to T+2m: Blackout freeze (note: m = -5 means 5m after, or m is minutes to release)
        # Using convention: -2.0 <= m <= 5.0 min around release
        if -2.0 <= m <= 5.0 or features.is_in_blackout_window():
            return BlackoutGate.BLACKOUT_FREEZE
        elif -30.0 <= m < -2.0:
            return BlackoutGate.POST_SHOCK_PULLBACK
        elif 5.0 < m <= 60.0:
            return BlackoutGate.PRE_EVENT_DELEVERAGE

    return BlackoutGate.NORMAL_TRADING


def label_d3_macro_regime(features: MacroContextFeatures) -> MacroRegime:
    """Ground truth for D3: Global Macro Regime State."""
    if features.vix >= 30.0 or features.credit_oas_bps >= 400.0:
        return MacroRegime.CRISIS_PANIC

    # Stagflation: Oil surging + inflation hot + growth contracting
    z = features.z_score or 0.0
    if features.oil_5d_pct >= 4.0 and z >= 0.5:
        return MacroRegime.STAGFLATION_SHOCK

    if features.vix >= 18.0 or features.credit_oas_bps >= 220.0 or features.dxy_change_pct >= 0.6:
        return MacroRegime.NORMAL_RISK_OFF

    return MacroRegime.RISK_ON_EXPANSION


def label_d4_liquidity_stance(features: MacroContextFeatures) -> LiquidityStance:
    """Ground truth for D4: US Net Liquidity and Central Bank Stance."""
    if features.sofr_rate >= 5.5 or features.reserves_delta_b <= -100.0:
        return LiquidityStance.LIQUIDITY_CRUNCH

    net_liq = features.net_liquidity_delta_30d_b
    if net_liq <= -40.0:
        return LiquidityStance.CONTRACTIONARY_DRAIN
    elif net_liq >= 40.0:
        return LiquidityStance.EXPANDING_LIQUIDITY

    return LiquidityStance.NEUTRAL_LIQUIDITY


def label_d5_commodity_shock(features: MacroContextFeatures) -> CommodityShock:
    """Ground truth for D5: Commodity & Geopolitical Disruption."""
    if features.oil_5d_pct >= 5.0:
        return CommodityShock.SUPPLY_DEFICIT_BULLISH
    elif features.oil_5d_pct <= -5.0:
        return CommodityShock.SUPPLY_GLUT_BEARISH

    # If high noise or verbal rhetoric without volume shifts
    if features.is_garbled or ("rhetoric" in features.narrative.lower() and abs(features.oil_5d_pct) < 1.0):
        return CommodityShock.NEUTRAL_NOISE

    return CommodityShock.NEUTRAL_NOISE


def label_d6_yield_curve_divergence(features: MacroContextFeatures) -> YieldCurveDivergence:
    """Ground truth for D6: Yield Curve Divergence."""
    if features.yield_10y_delta_bps >= 12.0 or features.dxy_change_pct >= 0.4:
        return YieldCurveDivergence.DOLLAR_DOMINANCE
    elif features.dxy_change_pct <= -0.4:
        return YieldCurveDivergence.EURO_CATCHUP
    elif features.yield_10y_delta_bps <= -15.0:
        return YieldCurveDivergence.RECESSIONARY_INVERSION

    return YieldCurveDivergence.DOLLAR_DOMINANCE


def label_d8_opportunity_arbitration(
    features: MacroContextFeatures,
    proposed_direction: str = "BUY",
    symbol: str = "EURUSD",
) -> OpportunityArbitration:
    """Ground truth for D8: Macro-to-Execution Arbitration."""
    # 1. Blackout gate has absolute postponement authority
    bg = label_d2_blackout_gate(features)
    if bg == BlackoutGate.BLACKOUT_FREEZE:
        return OpportunityArbitration.BLACKOUT_POSTPONE

    # 2. Crisis panic rejects pro-cyclical trading
    regime = label_d3_macro_regime(features)
    if regime == MacroRegime.CRISIS_PANIC:
        return OpportunityArbitration.REJECT_MACRO_CONFLICT

    # 3. Macro conflict checking
    d1 = label_d1_economic_surprise(features)
    if symbol == "EURUSD":
        # Hot US inflation = Bearish EURUSD. Buying EURUSD here is direct conflict.
        if d1 in (EconomicSurprise.EXTREME_HAWKISH, EconomicSurprise.MODERATE_HAWKISH) and proposed_direction == "BUY":
            return OpportunityArbitration.REJECT_MACRO_CONFLICT
        # Dovish US inflation = Bullish EURUSD. Selling EURUSD here is direct conflict.
        if d1 in (EconomicSurprise.EXTREME_DOVISH, EconomicSurprise.MODERATE_DOVISH) and proposed_direction == "SELL":
            return OpportunityArbitration.REJECT_MACRO_CONFLICT

        # Macro trend alignment
        if (d1 == EconomicSurprise.EXTREME_DOVISH and proposed_direction == "BUY") or \
           (d1 == EconomicSurprise.EXTREME_HAWKISH and proposed_direction == "SELL"):
            return OpportunityArbitration.STRONG_PROCEED

    return OpportunityArbitration.CONDITIONAL_HALF_RISK


def generate_ground_truth_labels(
    features: MacroContextFeatures,
    proposed_direction: str = "BUY",
    symbol: str = "EURUSD",
) -> Dict[str, Any]:
    """Generate the full dictionary of deterministic ground-truth labels."""
    d1 = label_d1_economic_surprise(features)
    d2 = label_d2_blackout_gate(features)
    d3 = label_d3_macro_regime(features)
    d4 = label_d4_liquidity_stance(features)
    d5 = label_d5_commodity_shock(features)
    d6 = label_d6_yield_curve_divergence(features)
    d8 = label_d8_opportunity_arbitration(features, proposed_direction, symbol)

    return {
        "D1_economic_surprise": d1.value,
        "D2_blackout_gate": d2.value,
        "D3_macro_regime": d3.value,
        "D4_liquidity_stance": d4.value,
        "D5_commodity_shock": d5.value,
        "D6_yield_curve_divergence": d6.value,
        "D8_opportunity_arbitration": d8.value,
        "blackout_freeze_boolean": (d2 == BlackoutGate.BLACKOUT_FREEZE),
        "execution_veto_boolean": (
            d8 == OpportunityArbitration.REJECT_MACRO_CONFLICT
            or d2 == BlackoutGate.BLACKOUT_FREEZE
            or d4 == LiquidityStance.LIQUIDITY_CRUNCH
        ),
    }
