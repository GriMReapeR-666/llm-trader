"""Macroeconomic Event-Driven Alpha & Surprise Transmission Engine.

Implements institutional quantitative frameworks for trading scheduled macroeconomic announcements:
1. Multi-Horizon Leading Indicator Composite Forecasting Scorecards (NFP, CPI, Central Bank Rates)
2. Rolling 90-Day Economic Surprise Index (ESI)
3. Standardized Surprise Vector (Z_Delta) & Disaggregated Component Shock Decomposition
4. Macroeconomic Regime & CFTC COT Speculative Positioning Interaction Arbitration (Multiplier vs. Exhaustion)
5. 3-Phase Chronological Event Execution State Machine (Pre-Positioning -> Toxic Spike Blackout -> Fib Pullback)
6. 4 Institutional Execution Models (Post-Announcement Pullback, Dual OCO Straddle, Spike Fade, Pre-Event)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np


# ==============================================================================
# 1. Leading Indicator Forecasting Models
# ==============================================================================

@dataclass
class LeadingScorecardResult:
    """Predictive forecast result generated from leading economic indicators."""
    event_name: str
    composite_score: float  # [-25.0, +25.0]
    directional_bias: str   # "STRONG_BEAT", "MODERATE_BEAT", "IN_LINE", "MODERATE_MISS", "STRONG_MISS"
    confidence: float       # [0.0, 1.0]
    sub_scores: Dict[str, float] = field(default_factory=dict)
    weights: Dict[str, float] = field(default_factory=dict)
    recommended_model: str = "MODEL_A_POST_NEWS_PULLBACK"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MacroLeadingIndicatorScorecard:
    """Computes multi-factor leading indicator composite predictive scorecards."""

    @staticmethod
    def calculate_nfp_scorecard(
        adp_change_k: float,
        adp_consensus_k: float,
        jobless_claims_4w_ma_k: float,
        jobless_claims_baseline_k: float = 215.0,
        ism_employment_pmi: float = 50.0,
        jolts_openings_millions: float = 8.5,
        jolts_baseline_millions: float = 8.0,
        esi_trend_score: float = 50.0,  # 0 to 100
    ) -> LeadingScorecardResult:
        """Calculates NFP leading indicator prediction score."""
        # 1. ADP Employment sub-score [-5, +5]
        adp_delta = float(adp_change_k - adp_consensus_k)
        s_adp = max(-5.0, min(5.0, adp_delta / 20.0))

        # 2. Jobless Claims 4W-MA sub-score [-5, +5] (inverse: higher claims = weaker jobs)
        claims_delta = float(jobless_claims_4w_ma_k - jobless_claims_baseline_k)
        s_claims = max(-5.0, min(5.0, -claims_delta / 5.0))

        # 3. ISM Employment PMI sub-score [-5, +5] (>50 expanding, <50 contracting)
        s_ism = max(-5.0, min(5.0, (float(ism_employment_pmi) - 50.0) * 1.5))

        # 4. JOLTS Openings sub-score [-5, +5]
        jolts_delta = float(jolts_openings_millions - jolts_baseline_millions)
        s_jolts = max(-5.0, min(5.0, jolts_delta * 5.0))

        # 5. ESI Trend sub-score [-5, +5]
        s_esi = max(-5.0, min(5.0, (float(esi_trend_score) - 50.0) / 10.0))

        weights = {
            "adp": 0.25,
            "jobless_claims": 0.20,
            "ism_employment": 0.20,
            "jolts_openings": 0.15,
            "esi_trend": 0.20,
        }

        # Weighted raw score [-5.0, +5.0] scaled to [-25.0, +25.0]
        raw_weighted = (
            weights["adp"] * s_adp
            + weights["jobless_claims"] * s_claims
            + weights["ism_employment"] * s_ism
            + weights["jolts_openings"] * s_jolts
            + weights["esi_trend"] * s_esi
        )
        comp_score = float(raw_weighted * 5.0)

        bias = "IN_LINE"
        if comp_score >= 15.0:
            bias = "STRONG_BEAT"
        elif comp_score >= 5.0:
            bias = "MODERATE_BEAT"
        elif comp_score <= -15.0:
            bias = "STRONG_MISS"
        elif comp_score <= -5.0:
            bias = "MODERATE_MISS"

        confidence = float(min(0.95, 0.50 + abs(comp_score) / 50.0))
        rec_model = "MODEL_D_PRE_POSITIONING" if abs(comp_score) >= 15.0 else "MODEL_A_POST_NEWS_PULLBACK"

        return LeadingScorecardResult(
            event_name="Non-Farm Payrolls (NFP)",
            composite_score=round(comp_score, 2),
            directional_bias=bias,
            confidence=round(confidence, 2),
            sub_scores={"adp": s_adp, "claims": s_claims, "ism": s_ism, "jolts": s_jolts, "esi": s_esi},
            weights=weights,
            recommended_model=rec_model,
        )

    @staticmethod
    def calculate_cpi_scorecard(
        ppi_mom_pct: float,
        ppi_consensus_pct: float,
        crude_oil_30d_change_pct: float,
        agriculture_basket_change_pct: float = 0.0,
        metals_basket_change_pct: float = 0.0,
        import_price_mom_pct: float = 0.0,
        ism_prices_paid_pmi: float = 50.0,
        wage_growth_yoy_pct: float = 4.0,
        wage_baseline_pct: float = 3.5,
    ) -> LeadingScorecardResult:
        """Calculates CPI leading indicator prediction score using Granger-causal commodity transmission."""
        # 1. PPI Surprise sub-score [-5, +5]
        ppi_delta = float(ppi_mom_pct - ppi_consensus_pct)
        s_ppi = max(-5.0, min(5.0, ppi_delta * 10.0))

        # 2. Commodity Cost Pressures sub-score [-5, +5]
        s_commodities = max(
            -5.0,
            min(
                5.0,
                0.50 * (float(crude_oil_30d_change_pct) / 3.0)
                + 0.30 * (float(agriculture_basket_change_pct) / 3.0)
                + 0.20 * (float(metals_basket_change_pct) / 3.0),
            ),
        )

        # 3. Import Price Pressures sub-score [-5, +5]
        s_import = max(-5.0, min(5.0, float(import_price_mom_pct) * 8.0))

        # 4. ISM Prices Paid sub-score [-5, +5]
        s_ism_prices = max(-5.0, min(5.0, (float(ism_prices_paid_pmi) - 50.0) / 4.0))

        # 5. Wage-Price Spiral sub-score [-5, +5]
        wage_delta = float(wage_growth_yoy_pct - wage_baseline_pct)
        s_wage = max(-5.0, min(5.0, wage_delta * 4.0))

        weights = {
            "ppi": 0.25,
            "commodities": 0.30,
            "import_prices": 0.15,
            "ism_prices": 0.15,
            "wage_pressures": 0.15,
        }

        raw_weighted = (
            weights["ppi"] * s_ppi
            + weights["commodities"] * s_commodities
            + weights["import_prices"] * s_import
            + weights["ism_prices"] * s_ism_prices
            + weights["wage_pressures"] * s_wage
        )
        comp_score = float(raw_weighted * 5.0)

        bias = "IN_LINE"
        if comp_score >= 15.0:
            bias = "STRONG_BEAT"  # High inflation surprise expected
        elif comp_score >= 5.0:
            bias = "MODERATE_BEAT"
        elif comp_score <= -15.0:
            bias = "STRONG_MISS"  # Disinflation surprise expected
        elif comp_score <= -5.0:
            bias = "MODERATE_MISS"

        confidence = float(min(0.95, 0.50 + abs(comp_score) / 50.0))
        rec_model = "MODEL_D_PRE_POSITIONING" if abs(comp_score) >= 15.0 else "MODEL_A_POST_NEWS_PULLBACK"

        return LeadingScorecardResult(
            event_name="Consumer Price Index (CPI)",
            composite_score=round(comp_score, 2),
            directional_bias=bias,
            confidence=round(confidence, 2),
            sub_scores={
                "ppi": s_ppi,
                "commodities": s_commodities,
                "import_prices": s_import,
                "ism_prices": s_ism_prices,
                "wage": s_wage,
            },
            weights=weights,
            recommended_model=rec_model,
        )

    @staticmethod
    def calculate_central_bank_scorecard(
        cme_fedwatch_hike_prob_pct: float,
        cme_fedwatch_cut_prob_pct: float,
        yield_curve_2y10y_bps: float,
        esi_score: float = 50.0,
        inflation_momentum_3m_pct: float = 0.0,
        speaker_tone_score: float = 0.0,  # -5 (ultra-dovish) to +5 (ultra-hawkish)
    ) -> LeadingScorecardResult:
        """Calculates Central Bank policy stance and surprise probability."""
        # 1. Market-implied rate probability sub-score [-5, +5]
        prob_delta = float(cme_fedwatch_hike_prob_pct - cme_fedwatch_cut_prob_pct)
        s_prob = max(-5.0, min(5.0, prob_delta / 15.0))

        # 2. Yield Curve Slope sub-score [-5, +5] (inversion = rate cut expectation)
        s_curve = max(-5.0, min(5.0, float(yield_curve_2y10y_bps) / 20.0))

        # 3. ESI Momentum sub-score [-5, +5]
        s_esi = max(-5.0, min(5.0, (float(esi_score) - 50.0) / 10.0))

        # 4. Inflation 3M Momentum sub-score [-5, +5]
        s_infl = max(-5.0, min(5.0, float(inflation_momentum_3m_pct) * 5.0))

        # 5. Speaker Tone sub-score [-5, +5]
        s_tone = max(-5.0, min(5.0, float(speaker_tone_score)))

        weights = {
            "market_pricing": 0.35,
            "yield_curve": 0.20,
            "esi_momentum": 0.20,
            "inflation_momentum": 0.15,
            "speaker_tone": 0.10,
        }

        raw_weighted = (
            weights["market_pricing"] * s_prob
            + weights["yield_curve"] * s_curve
            + weights["esi_momentum"] * s_esi
            + weights["inflation_momentum"] * s_infl
            + weights["speaker_tone"] * s_tone
        )
        comp_score = float(raw_weighted * 5.0)

        bias = "NEUTRAL"
        if comp_score >= 12.0:
            bias = "HAWKISH_SURPRISE"
        elif comp_score >= 4.0:
            bias = "HAWKISH_LEAN"
        elif comp_score <= -12.0:
            bias = "DOVISH_SURPRISE"
        elif comp_score <= -4.0:
            bias = "DOVISH_LEAN"

        confidence = float(min(0.95, 0.50 + abs(comp_score) / 50.0))

        return LeadingScorecardResult(
            event_name="Central Bank Interest Rate Decision",
            composite_score=round(comp_score, 2),
            directional_bias=bias,
            confidence=round(confidence, 2),
            sub_scores={
                "market_pricing": s_prob,
                "yield_curve": s_curve,
                "esi": s_esi,
                "inflation": s_infl,
                "tone": s_tone,
            },
            weights=weights,
            recommended_model="MODEL_A_POST_NEWS_PULLBACK",
        )


# ==============================================================================
# 2. Rolling Economic Surprise Index (ESI)
# ==============================================================================

class RollingEconomicSurpriseIndex:
    """Computes rolling 0-100% Economic Surprise Index across macro announcements."""

    @staticmethod
    def calculate_esi(
        releases: List[Dict[str, Any]],
        lookback_count: int = 30,
    ) -> Dict[str, Any]:
        """Calculates percentage of releases beating consensus and interprets macro momentum."""
        if not releases:
            return {"esi_score": 50.0, "momentum": "NEUTRAL", "beating_pct": 50.0, "sample_size": 0}

        sample = releases[-lookback_count:]
        beats = sum(1 for r in sample if float(r.get("actual", 0)) > float(r.get("consensus", 0)))
        total = len(sample)
        esi_score = float((beats / total) * 100.0) if total > 0 else 50.0

        if esi_score >= 80.0:
            momentum = "PERSISTENT_EXPANSION_HAWKISH"
        elif esi_score >= 55.0:
            momentum = "MODERATE_POSITIVE_MOMENTUM"
        elif esi_score <= 20.0:
            momentum = "PERSISTENT_DECELERATION_DOVISH"
        elif esi_score <= 45.0:
            momentum = "SOFTENING_NEGATIVE_MOMENTUM"
        else:
            momentum = "NEUTRAL_MIXED"

        return {
            "esi_score": round(esi_score, 1),
            "momentum": momentum,
            "beating_pct": round(esi_score, 1),
            "sample_size": total,
            "bullish_currency_bias": esi_score >= 55.0,
            "dovish_pivot_risk": esi_score <= 30.0,
        }


# ==============================================================================
# 3. Standardized Surprise Vector (Z_Delta) & Component Shock Decomposition
# ==============================================================================

class StandardizedSurpriseDecomposer:
    """Calculates standardized statistical surprise and disaggregated shock vectors."""

    DEFAULT_HISTORICAL_VOLS = {
        "NFP": 45.0,           # Standard deviation of NFP surprise in thousands
        "CPI": 0.15,           # Std dev of MoM CPI surprise in %
        "PPI": 0.20,           # Std dev of MoM PPI surprise in %
        "GDP": 0.40,           # Std dev of QoQ GDP surprise in %
        "RETAIL_SALES": 0.35,  # Std dev of Retail Sales surprise in %
        "RATE_DECISION": 15.0, # Std dev of Rate Decision surprise in bps
        "DEFAULT": 1.0,
    }

    @classmethod
    def decompose_surprise(
        cls,
        event_name: str,
        actual: float,
        consensus: float,
        historical_std: Optional[float] = None,
        disaggregated_components: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Calculates standardized Z_Delta and categorizes impact level."""
        delta = float(actual - consensus)
        key = str(event_name).upper().replace(" ", "_").replace("-", "_")
        sigma = float(historical_std or cls.DEFAULT_HISTORICAL_VOLS.get(key, cls.DEFAULT_HISTORICAL_VOLS["DEFAULT"]))
        sigma = max(1e-6, sigma)

        z_delta = float(delta / sigma)
        abs_z = abs(z_delta)

        if abs_z < 0.50:
            impact_level = "PRICED_IN_IN_LINE"
            permitted_models = ["MODEL_C_SPIKE_FADE", "NO_TRADE"]
        elif abs_z < 1.00:
            impact_level = "MODERATE_SURPRISE"
            permitted_models = ["MODEL_A_POST_NEWS_PULLBACK"]
        elif abs_z < 2.00:
            impact_level = "HIGH_IMPACT_SURPRISE"
            permitted_models = ["MODEL_A_POST_NEWS_PULLBACK", "MODEL_D_PRE_POSITIONING"]
        else:
            impact_level = "REGIME_SHIFT_STRUCTURAL_SHOCK"
            permitted_models = ["MODEL_A_POST_NEWS_PULLBACK", "MODEL_B_DUAL_STRADDLE"]

        return {
            "event_name": event_name,
            "actual": actual,
            "consensus": consensus,
            "delta": round(delta, 4),
            "historical_sigma": sigma,
            "z_delta": round(z_delta, 2),
            "abs_z_delta": round(abs_z, 2),
            "impact_level": impact_level,
            "permitted_models": permitted_models,
            "disaggregated_components": disaggregated_components or {},
        }


# ==============================================================================
# 4. Macro Regime & Positioning Interaction Arbitration
# ==============================================================================

class MacroRegimePositioningArbitrator:
    """Evaluates Multiplier Effect vs. Diminishing Returns Exhaustion based on COT and macro state."""

    @staticmethod
    def evaluate_regime_and_crowding(
        z_delta: float,
        macro_regime: str = "EXPANSION",  # "EXPANSION", "SLOWDOWN", "RECESSION", "RECOVERY"
        cot_speculative_percentile: float = 50.0,  # 0 to 100 percentile
    ) -> Dict[str, Any]:
        """Calculates conviction multiplier and identifies crowded liquidity traps."""
        is_beat = z_delta > 0.50
        is_miss = z_delta < -0.50
        is_long_crowded = cot_speculative_percentile >= 85.0
        is_short_crowded = cot_speculative_percentile <= 15.0

        # Base conviction multiplier
        conviction_multiplier = 1.0
        reaction_type = "NORMAL_TRANSMISSION"

        if is_beat:
            if is_long_crowded:
                # Diminishing returns / sell-the-fact exhaustion
                conviction_multiplier = 0.40
                reaction_type = "DIMINISHING_RETURNS_EXHAUSTION"
            elif macro_regime in ("RECESSION", "SLOWDOWN"):
                # Reversal / short squeeze
                conviction_multiplier = 1.40
                reaction_type = "SHORT_SQUEEZE_REVERSAL"
            else:
                # Normal trend continuation
                conviction_multiplier = 1.20
                reaction_type = "MULTIPLIER_ACCELERATION"
        elif is_miss:
            if macro_regime in ("RECESSION", "SLOWDOWN"):
                # Multiplier effect: Miss reinforces deterioration
                conviction_multiplier = 1.70
                reaction_type = "MULTIPLIER_DETERIORATION_CASCADE"
            elif is_long_crowded:
                # Cascading long liquidations
                conviction_multiplier = 1.85
                reaction_type = "CASCADING_LONG_LIQUIDATION"
            elif is_short_crowded:
                # Diminishing returns on miss
                conviction_multiplier = 0.50
                reaction_type = "DIMINISHING_RETURNS_EXHAUSTION"

        return {
            "z_delta": z_delta,
            "macro_regime": macro_regime,
            "cot_percentile": cot_speculative_percentile,
            "conviction_multiplier": round(conviction_multiplier, 2),
            "reaction_type": reaction_type,
            "is_exhaustion_trap": "EXHAUSTION" in reaction_type,
            "is_high_conviction_cascade": conviction_multiplier >= 1.50,
        }


# ==============================================================================
# 5. 3-Phase Chronological Event State Machine
# ==============================================================================

class ThreePhaseEventStateMachine:
    """Manages Phase 1 Pre-Positioning, Phase 2 Toxic Blackout, and Phase 3 Fib Retracements."""

    @staticmethod
    def evaluate_phase(
        event_time_unix: float,
        current_time_unix: float,
        quote_spread: float,
        baseline_spread: float,
        spike_high: float,
        spike_low: float,
        current_price: float,
        direction: str = "BUY",
    ) -> Dict[str, Any]:
        """Evaluates current chronological phase and calculates execution levels."""
        time_delta_sec = current_time_unix - event_time_unix

        # 1. Phase 1: Pre-News (T - 60m to T - 5m)
        if time_delta_sec < -300:
            phase = "PHASE_1_PRE_NEWS"
            can_execute_market = False
            can_place_pending = True
            action_guidance = "Prepare leading scorecard, mark key levels, place straddles if Model B."
            fib_levels = {}
        # 2. Phase 2: Initial Spike Blackout (T - 5m to T + 15m)
        elif -300 <= time_delta_sec <= 900:
            phase = "PHASE_2_TOXIC_SPIKE_BLACKOUT"
            can_execute_market = False
            can_place_pending = False
            action_guidance = "HARD BLACKOUT: Prohibit market entries; cancel unhit straddles; record spike extremes."
            fib_levels = {}
        # 3. Phase 3: Post-News Real Trend & Fib Retracement (T + 15m to T + 60m)
        else:
            phase = "PHASE_3_POST_NEWS_TREND"
            can_execute_market = True
            can_place_pending = True
            action_guidance = "Spreads normalized. Execute on 38.2%-61.8% Fibonacci retracement pullbacks."

            # Calculate Golden Pocket Fib levels
            spike_range = max(1e-6, spike_high - spike_low)
            if direction == "BUY":
                fib_382 = spike_high - (0.382 * spike_range)
                fib_500 = spike_high - (0.500 * spike_range)
                fib_618 = spike_high - (0.618 * spike_range)
                optimal_entry = fib_500
                stop_loss = spike_low - (0.10 * spike_range)
            else:
                fib_382 = spike_low + (0.382 * spike_range)
                fib_500 = spike_low + (0.500 * spike_range)
                fib_618 = spike_low + (0.618 * spike_range)
                optimal_entry = fib_500
                stop_loss = spike_high + (0.10 * spike_range)

            fib_levels = {
                "spike_high": spike_high,
                "spike_low": spike_low,
                "spike_range": round(spike_range, 5),
                "fib_382": round(fib_382, 5),
                "fib_500": round(fib_500, 5),
                "fib_618": round(fib_618, 5),
                "optimal_entry": round(optimal_entry, 5),
                "stop_loss": round(stop_loss, 5),
            }

        # Spread blowout safety check
        spread_ratio = quote_spread / max(1e-6, baseline_spread)
        spread_acceptable = spread_ratio <= 2.20

        return {
            "phase": phase,
            "time_delta_seconds": round(time_delta_sec, 1),
            "can_execute_market": can_execute_market and spread_acceptable,
            "can_place_pending": can_place_pending,
            "spread_ratio": round(spread_ratio, 2),
            "spread_acceptable": spread_acceptable,
            "action_guidance": action_guidance,
            "fib_levels": fib_levels,
        }


# ==============================================================================
# 6. Master Macro Event-Driven Alpha Engine
# ==============================================================================

class MacroEventAlphaEngine:
    """Master orchestrator for institutional macroeconomic event trading."""

    def __init__(self):
        self.leading_scorecard = MacroLeadingIndicatorScorecard()
        self.esi_calculator = RollingEconomicSurpriseIndex()
        self.surprise_decomposer = StandardizedSurpriseDecomposer()
        self.regime_arbitrator = MacroRegimePositioningArbitrator()
        self.state_machine = ThreePhaseEventStateMachine()

    def evaluate_announcement(
        self,
        event_name: str,
        symbol: str,
        actual: float,
        consensus: float,
        previous: float,
        event_timestamp_unix: float,
        current_timestamp_unix: float,
        current_price: float,
        quote_spread: float,
        baseline_spread: float,
        spike_high: float,
        spike_low: float,
        atr: float = 0.0015,
        macro_regime: str = "EXPANSION",
        cot_percentile: float = 50.0,
        leading_inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Runs the complete macroeconomic surprise transmission and execution model."""
        start_time = time.time()

        # 1. Standardized Surprise Decomposition
        surprise_res = self.surprise_decomposer.decompose_surprise(
            event_name=event_name,
            actual=actual,
            consensus=consensus,
        )
        z_delta = surprise_res["z_delta"]

        # 2. Regime and Positioning Interaction Arbitration
        regime_res = self.regime_arbitrator.evaluate_regime_and_crowding(
            z_delta=z_delta,
            macro_regime=macro_regime,
            cot_speculative_percentile=cot_percentile,
        )

        # 3. Determine Underlying Fundamental Side
        is_usd_pair = "USD" in symbol
        is_usd_base = symbol.startswith("USD")

        # By default, a positive beat on USD data (NFP, CPI, Rates) is USD Bullish
        usd_bullish = z_delta > 0
        if is_usd_base:
            side = "BUY" if usd_bullish else "SELL"
        else:
            side = "SELL" if usd_bullish else "BUY"

        # 4. Chronological 3-Phase State Machine
        phase_res = self.state_machine.evaluate_phase(
            event_time_unix=event_timestamp_unix,
            current_time_unix=current_timestamp_unix,
            quote_spread=quote_spread,
            baseline_spread=baseline_spread,
            spike_high=spike_high,
            spike_low=spike_low,
            current_price=current_price,
            direction=side,
        )

        # 5. Build Execution Proposal based on Models
        proposal = None
        decision = "HOLD"

        if phase_res["phase"] == "PHASE_2_TOXIC_SPIKE_BLACKOUT":
            decision = "CIRCUIT_BREAKER_BLACKOUT"
        elif phase_res["phase"] == "PHASE_3_POST_NEWS_TREND":
            if regime_res["is_exhaustion_trap"]:
                decision = "HOLD_EXHAUSTION_TRAP"
            elif phase_res["can_execute_market"]:
                decision = "EXECUTE"
                fib = phase_res["fib_levels"]
                entry_p = fib.get("optimal_entry", current_price)
                sl_p = fib.get("stop_loss", current_price - (2.0 * atr) if side == "BUY" else current_price + (2.0 * atr))
                risk_dist = abs(entry_p - sl_p)
                tp_p = entry_p + (2.5 * risk_dist) if side == "BUY" else entry_p - (2.5 * risk_dist)

                proposal = {
                    "strategy": "macro_event_alpha_pullback",
                    "symbol": symbol,
                    "side": side,
                    "entry_price": entry_p,
                    "stop_loss": sl_p,
                    "take_profit": tp_p,
                    "risk_reward_ratio": 2.5,
                    "conviction_multiplier": regime_res["conviction_multiplier"],
                }

        total_latency_ms = (time.time() - start_time) * 1000.0

        return {
            "event_name": event_name,
            "symbol": symbol,
            "decision": decision,
            "side": side,
            "surprise_decomposition": surprise_res,
            "regime_positioning": regime_res,
            "phase_telemetry": phase_res,
            "proposal": proposal,
            "total_latency_ms": round(total_latency_ms, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
