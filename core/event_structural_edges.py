"""Module 11: Event-Driven & Structural Order Flow Edges.

Implements the remaining concepts from MASTER_SOURCE_OF_TRUTH §3-4:

1. VRP Options Selling Harvest — Short straddle / iron condor on IV > RV
2. PEAD / SUE — Post-Earnings Announcement Drift via SUE scoring
3. Spinoff Undervaluation — Forced selling exhaustion detection
4. Buyback / 10b-18 — Blackout calendar + post-blackout drift
5. OPEX / Max Pain — Options expiration pinning detection
6. Lottery Effect / Short Lotto — Selling high-skew retail overpricing

All functions: execution_authority=False, advisory only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# 1. VRP Options Selling Harvest
# ---------------------------------------------------------------------------

@dataclass
class VRPHarvestResult:
    implied_vol: float
    realized_vol: float
    vrp_spread: float  # IV - RV (positive = harvestable)
    vrp_annualized: float
    recommended_structure: str  # "IRON_CONDOR", "SHORT_STRADDLE", "SHORT_PUT_SPREAD", "NO_TRADE"
    confidence: float
    risk_reward: str  # "ATTRACTIVE", "FAIR", "UNATTRACTIVE"
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_vrp_harvest_signal(
    implied_vol: float = 0.0,
    realized_vol: float = 0.0,
    current_price: float = 0.0,
    days_to_expiration: int = 30,
    risk_free_rate: float = 0.05,
    vrp_threshold: float = 0.05,
) -> VRPHarvestResult:
    """Variance Risk Premium harvest: sell options when IV structurally exceeds RV.

    VRP = IV - RV. Historical tendency: IV > RV ~85% of the time.
    Structure selection:
    - Iron Condor: range-bound, defined risk
    - Short Straddle: high IV, expects compression
    - Short Put Spread: directional bullish bias
    """
    if implied_vol <= 0 or realized_vol <= 0 or current_price <= 0:
        return VRPHarvestResult(
            implied_vol=0, realized_vol=0, vrp_spread=0, vrp_annualized=0,
            recommended_structure="NO_TRADE", confidence=0, risk_reward="UNATTRACTIVE",
        )

    vrp_spread = implied_vol - realized_vol
    # Annualize: assume IV and RV are annualized; spread is already annual
    vrp_annual = vrp_spread

    # Structure selection based on VRP magnitude
    if vrp_spread > vrp_threshold * 2:
        structure = "SHORT_STRADDLE"  # High VRP, expect vol compression
        confidence = min(1.0, vrp_spread / (vrp_threshold * 4))
    elif vrp_spread > vrp_threshold:
        structure = "IRON_CONDOR"  # Moderate VRP, range-bound assumption
        confidence = min(1.0, vrp_spread / (vrp_threshold * 2))
    elif vrp_spread > vrp_threshold * 0.5:
        structure = "SHORT_PUT_SPREAD"  # Low VRP, directional lean
        confidence = 0.4
    else:
        structure = "NO_TRADE"
        confidence = 0.0

    # Risk/reward assessment
    if vrp_annual > 0.10:
        rr = "ATTRACTIVE"
    elif vrp_annual > 0.05:
        rr = "FAIR"
    else:
        rr = "UNATTRACTIVE"

    return VRPHarvestResult(
        implied_vol=round(implied_vol, 4),
        realized_vol=round(realized_vol, 4),
        vrp_spread=round(vrp_spread, 4),
        vrp_annualized=round(vrp_annual, 4),
        recommended_structure=structure,
        confidence=round(confidence, 3),
        risk_reward=rr,
    )


# ---------------------------------------------------------------------------
# 2. PEAD / Post-Earnings Announcement Drift
# ---------------------------------------------------------------------------

@dataclass
class PEADResult:
    sue_score: float  # Standardized Unexpected Earnings
    surprise_magnitude: str  # "MEGA_BEAT", "BEAT", "INLINE", "MISS", "MEGA_MISS"
    drift_direction: str  # "LONG", "SHORT", "NO_SIGNAL"
    expected_drift_days: int  # Expected drift holding period
    volume_confirmation: bool
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_pead_signal(
    actual_eps: Optional[float] = None,
    consensus_eps: Optional[float] = None,
    earnings_std: Optional[float] = None,
    pre_announcement_price: float = 0.0,
    post_announcement_price: float = 0.0,
    avg_volume_30d: float = 0.0,
    announcement_volume: float = 0.0,
) -> PEADResult:
    """Post-Earnings Announcement Drift via SUE scoring.

    SUE = (Actual EPS - Consensus EPS) / Standard Deviation of EPS surprises
    Bernard & Thomas (1989): SUE > 2.5 → drift for 20-60 days
    """
    if actual_eps is None or consensus_eps is None or earnings_std is None or earnings_std <= 0:
        return PEADResult(
            sue_score=0.0, surprise_magnitude="NO_DATA", drift_direction="NO_SIGNAL",
            expected_drift_days=0, volume_confirmation=False, confidence=0.0,
        )

    sue = (actual_eps - consensus_eps) / earnings_std

    # Classify surprise magnitude
    if sue > 3.0:
        magnitude = "MEGA_BEAT"
    elif sue > 2.0:
        magnitude = "BEAT"
    elif sue > -2.0:
        magnitude = "INLINE"
    elif sue > -3.0:
        magnitude = "MISS"
    else:
        magnitude = "MEGA_MISS"

    # Drift direction: only trade SUE > |2.5| (top/bottom decile)
    if sue > 2.5:
        direction = "LONG"
        drift_days = 40  # ~2 months average drift
    elif sue < -2.5:
        direction = "SHORT"
        drift_days = 40
    else:
        direction = "NO_SIGNAL"
        drift_days = 0

    # Volume confirmation: announcement day volume > 3x 30-day avg
    vol_confirmed = announcement_volume > avg_volume_30d * 3 if avg_volume_30d > 0 else False

    # Confidence: stronger SUE + volume confirmation = higher confidence
    base_conf = min(1.0, abs(sue) / 4.0) if abs(sue) > 2.5 else 0.0
    if vol_confirmed:
        base_conf = min(1.0, base_conf + 0.15)

    return PEADResult(
        sue_score=round(sue, 3),
        surprise_magnitude=magnitude,
        drift_direction=direction,
        expected_drift_days=drift_days,
        volume_confirmation=vol_confirmed,
        confidence=round(base_conf, 3),
    )


# ---------------------------------------------------------------------------
# 3. Spinoff Undervaluation
# ---------------------------------------------------------------------------

@dataclass
class SpinoffResult:
    days_since_spinoff: int
    institutional_selling_exhausted: bool
    volume_normalized: bool
    accumulation_signal: str  # "ENTER", "WAIT", "NO_SIGNAL"
    expected_outperformance_12m: float  # Historical average
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def assess_spinoff_opportunity(
    spinoff_date: Optional[datetime] = None,
    current_date: Optional[datetime] = None,
    daily_volumes: Optional[List[float]] = None,
    avg_volume_pre_spinoff: float = 0.0,
    parent_price_at_spinoff: float = 0.0,
    current_price: float = 0.0,
) -> SpinoffResult:
    """Spinoff undervaluation: wait for institutional forced selling to exhaust.

    Institutions dump mandated-to-sell shares on day 1. Wait 5-15 days for
    volume to normalize, then enter. Historical: spinoffs outperform by ~10-15% over 12m.
    """
    if spinoff_date is None or current_date is None:
        return SpinoffResult(
            days_since_spinoff=0, institutional_selling_exhausted=False,
            volume_normalized=False, accumulation_signal="NO_SIGNAL",
            expected_outperformance_12m=0.0, confidence=0.0,
        )

    days_since = (current_date - spinoff_date).days

    # Volume normalization: current avg volume vs pre-spinoff avg
    vol_exhausted = False
    vol_normalized = False
    if daily_volumes and len(daily_volumes) >= 5 and avg_volume_pre_spinoff > 0:
        recent_avg = float(np.mean(daily_volumes[-5:]))
        # Institutional selling exhausted when recent volume < 1.5x pre-spinoff avg
        vol_exhausted = recent_avg < avg_volume_pre_spinoff * 1.5
        # Normalized when within 1.2x
        vol_normalized = recent_avg < avg_volume_pre_spinoff * 1.2

    # Signal: wait 5-15 days, require volume exhaustion
    if days_since >= 5 and vol_exhausted:
        signal = "ENTER"
        confidence = min(1.0, 0.5 + days_since / 30)
    elif days_since >= 5 and not vol_exhausted:
        signal = "WAIT"
        confidence = 0.3
    else:
        signal = "NO_SIGNAL"
        confidence = 0.0

    return SpinoffResult(
        days_since_spinoff=days_since,
        institutional_selling_exhausted=vol_exhausted,
        volume_normalized=vol_normalized,
        accumulation_signal=signal,
        expected_outperformance_12m=0.12,  # Historical ~12% 12m outperformance
        confidence=round(confidence, 3),
    )


# ---------------------------------------------------------------------------
# 4. Buyback / 10b-18 / Blackout Calendar
# ---------------------------------------------------------------------------

@dataclass
class BuybackResult:
    blackout_active: bool
    days_until_blackout_lift: int
    post_blackout_drift_expected: bool
    buyback_yield_pct: float
    signal: str  # "ACCUMULATE", "HOLD", "AVOID"
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def assess_buyback_blackout(
    earnings_date: Optional[datetime] = None,
    blackout_weeks_before_earnings: int = 2,
    current_date: Optional[datetime] = None,
    buyback_yield_pct: float = 0.0,
    shares_outstanding: float = 0.0,
    buyback_authorization: float = 0.0,
) -> BuybackResult:
    """Rule 10b-18 buyback blackout tracker.

    Companies cannot repurchase shares during 2-4 week blackout before earnings.
    Post-blackout resumption creates upward drift.
    """
    if earnings_date is None or current_date is None:
        return BuybackResult(
            blackout_active=False, days_until_blackout_lift=0,
            post_blackout_drift_expected=False, buyback_yield_pct=0.0,
            signal="HOLD", confidence=0.0,
        )

    blackout_start = earnings_date - timedelta(weeks=blackout_weeks_before_earnings)
    blackout_end = earnings_date + timedelta(days=1)  # Can resume day after earnings

    blackout_active = blackout_start <= current_date <= blackout_end
    days_until_lift = max(0, (blackout_end - current_date).days) if blackout_active else 0

    # Post-blackout drift: strongest in first 2 weeks after blackout lifts
    post_blackout = not blackout_active and (current_date - blackout_end).days <= 14

    # Buyback yield: authorization / market cap
    if shares_outstanding > 0 and buyback_authorization > 0 and current_date is not None:
        # Approximate market cap from yield if not provided directly
        byield = buyback_yield_pct if buyback_yield_pct > 0 else 0.0
    else:
        byield = buyback_yield_pct

    if post_blackout and byield > 0.03:
        signal = "ACCUMULATE"
        confidence = min(1.0, 0.6 + byield * 5)
    elif blackout_active:
        signal = "HOLD"  # Wait for blackout to lift
        confidence = 0.2
    else:
        signal = "HOLD"
        confidence = 0.3

    return BuybackResult(
        blackout_active=blackout_active,
        days_until_blackout_lift=days_until_lift,
        post_blackout_drift_expected=post_blackout,
        buyback_yield_pct=round(byield, 4),
        signal=signal,
        confidence=round(confidence, 3),
    )


# ---------------------------------------------------------------------------
# 5. OPEX / Max Pain / Options Pinning
# ---------------------------------------------------------------------------

@dataclass
class MaxPainResult:
    max_pain_strike: float
    current_price: float
    distance_to_max_pain_pct: float
    pin_risk: str  # "HIGH", "MODERATE", "LOW"
    days_to_opex: int
    expected_drift: float  # Expected drift toward max pain
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_max_pain(
    strikes: Optional[List[float]] = None,
    call_open_interest: Optional[List[float]] = None,
    put_open_interest: Optional[List[float]] = None,
    current_price: float = 0.0,
    days_to_opex: int = 0,
) -> MaxPainResult:
    """Max Pain: strike where total options expiration value is minimized.

    Dealers' dynamic delta hedging pins price near max pain on OPEX day.
    """
    if not strikes or not call_open_interest or not put_open_interest or current_price <= 0:
        return MaxPainResult(
            max_pain_strike=0.0, current_price=current_price,
            distance_to_max_pain_pct=0.0, pin_risk="LOW",
            days_to_opex=0, expected_drift=0.0, confidence=0.0,
        )

    strikes_arr = np.array(strikes, dtype=float)
    calls_oi = np.array(call_open_interest, dtype=float)
    puts_oi = np.array(put_open_interest, dtype=float)

    # Max Pain: strike that maximizes total OI value (minimizes payout)
    total_pain = np.zeros(len(strikes_arr))
    for i, K in enumerate(strikes_arr):
        # Pain = sum of |OI * (strike - K)| for all strikes
        call_pain = float(np.sum(calls_oi * np.abs(strikes_arr - K)))
        put_pain = float(np.sum(puts_oi * np.abs(strikes_arr - K)))
        total_pain[i] = call_pain + put_pain

    max_pain_idx = int(np.argmin(total_pain))
    max_pain_strike = float(strikes_arr[max_pain_idx])

    distance = abs(current_price - max_pain_strike) / current_price * 100

    # Pin risk: high when within 1% and OPEX is imminent
    if days_to_opex <= 2 and distance < 1.0:
        pin_risk = "HIGH"
    elif days_to_opex <= 5 and distance < 2.0:
        pin_risk = "MODERATE"
    else:
        pin_risk = "LOW"

    # Expected drift: pull toward max pain
    drift = (max_pain_strike - current_price) / current_price if days_to_opex > 0 else 0.0

    return MaxPainResult(
        max_pain_strike=round(max_pain_strike, 2),
        current_price=round(current_price, 2),
        distance_to_max_pain_pct=round(distance, 2),
        pin_risk=pin_risk,
        days_to_opex=days_to_opex,
        expected_drift=round(drift, 4),
        confidence=min(1.0, max(0.0, 1.0 - days_to_opex / 10)) if days_to_opex > 0 else 0.0,
    )


# ---------------------------------------------------------------------------
# 6. Lottery Effect / Short Lotto / Call Skew
# ---------------------------------------------------------------------------

@dataclass
class LotteryEffectResult:
    skew_z_score: float  # How extreme is the current skew vs historical
    call_skew_premium: float  # Extra IV in 25-delta calls vs puts
    retail_overpricing_detected: bool
    recommended_trade: str  # "SELL_CALL_SPREAD", "SELL_PUT_SPREAD", "NO_TRADE"
    expected_edge: float  # Expected edge from selling overpriced convexity
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_lottery_effect(
    call_25d_iv: float = 0.0,
    put_25d_iv: float = 0.0,
    atm_iv: float = 0.0,
    historical_skew_mean: float = 0.0,
    historical_skew_std: float = 1.0,
    retail_call_volume_ratio: float = 1.0,
) -> LotteryEffectResult:
    """Lottery effect: retail overpricing of high-skew OTM calls.

    When 25-delta call IV >> 25-delta put IV (unusual skew), retail is paying
    excessive premium for lottery upside. Sell the overpriced call spreads.
    """
    if call_25d_iv <= 0 or put_25d_iv <= 0 or atm_iv <= 0:
        return LotteryEffectResult(
            skew_z_score=0.0, call_skew_premium=0.0, retail_overpricing_detected=False,
            recommended_trade="NO_TRADE", expected_edge=0.0, confidence=0.0,
        )

    # Skew: call 25d IV vs put 25d IV (normally puts > calls)
    call_skew = call_25d_iv - put_25d_iv  # Positive = unusual call overpricing
    call_premium = call_25d_iv - atm_iv  # How much calls trade above ATM

    # Z-score vs historical
    skew_z = (call_skew - historical_skew_mean) / max(historical_skew_std, 0.01)

    # Retail overpricing: call skew premium + high retail call volume
    retail_overpriced = call_premium > 0.02 and retail_call_volume_ratio > 1.5

    if retail_overpriced and skew_z > 1.5:
        trade = "SELL_CALL_SPREAD"
        edge = call_premium * 0.3  # Expected capture ~30% of excess premium
    elif skew_z > 2.0:
        trade = "SELL_CALL_SPREAD"
        edge = call_premium * 0.25
    elif skew_z < -1.5 and call_premium < -0.02:
        trade = "SELL_PUT_SPREAD"  # Put overpricing
        edge = abs(call_premium) * 0.25
    else:
        trade = "NO_TRADE"
        edge = 0.0

    return LotteryEffectResult(
        skew_z_score=round(skew_z, 3),
        call_skew_premium=round(call_premium, 4),
        retail_overpricing_detected=retail_overpriced,
        recommended_trade=trade,
        expected_edge=round(edge, 4),
        confidence=min(1.0, max(0.0, skew_z / 3)) if trade != "NO_TRADE" else 0.0,
    )
