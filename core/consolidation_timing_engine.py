"""Consolidation Diagnostics & Breakout Timing Engine.

Answers two fundamental institutional trading questions:
1. "Why is the market consolidated?"
   - Identifies structural causes: Pre-News Accumulation, Session Lull,
     TTM Volatility Squeeze, Liquidity Engineering (Equal Highs/Lows),
     HTF Structural Equilibrium, or Post-Expansion Exhaustion.
2. "When will the expansion move occur?"
   - Predicts specific execution timing: London Open Judas Swing (07:00-09:00 UTC),
     NY Open Catalyst (12:30-14:30 UTC), Squeeze Momentum Release,
     Liquidity Sweep Reversal, or Scheduled Macro Event Timestamp.

Doctrine:
- execution_authority: strictly False.
- Pure Python stdlib & math.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from core.symbol_calibration import SymbolCalibrationEngine

EXECUTION_AUTHORITY = False


class ConsolidationReason(str, Enum):
    PRE_NEWS_ACCUMULATION = "PRE_NEWS_ACCUMULATION"
    SESSION_LULL = "SESSION_LULL"
    VOLATILITY_COMPRESSION_SQUEEZE = "VOLATILITY_COMPRESSION_SQUEEZE"
    LIQUIDITY_ENGINEERING = "LIQUIDITY_ENGINEERING"
    STRUCTURAL_EQUILIBRIUM = "STRUCTURAL_EQUILIBRIUM"
    POST_EXPANSION_EXHAUSTION = "POST_EXPANSION_EXHAUSTION"
    NONE_EXPANDING = "NONE_EXPANDING"


class MoveTimingTrigger(str, Enum):
    LONDON_OPEN_EXPANSION = "LONDON_OPEN_EXPANSION"           # 07:00 - 09:00 UTC
    NY_OPEN_CATALYST = "NY_OPEN_CATALYST"                     # 12:30 - 14:30 UTC
    SCHEDULED_MACRO_CATALYST = "SCHEDULED_MACRO_CATALYST"     # Exact CPI/NFP/FOMC release
    SQUEEZE_MOMENTUM_FIRE = "SQUEEZE_MOMENTUM_FIRE"           # TTM squeeze release
    LIQUIDITY_SWEEP_CONFIRMATION = "LIQUIDITY_SWEEP_CONFIRMATION" # Post-sweep of Session H/L
    LONDON_FIX_REBALANCING = "LONDON_FIX_REBALANCING"         # 15:30 - 16:30 UTC
    NEXT_CYCLE_OPEX = "NEXT_CYCLE_OPEX"                       # Third Friday OPEX
    IN_EXPANSION_RUNNER = "IN_EXPANSION_RUNNER"


@dataclass
class ConsolidationDiagnosis:
    symbol: str
    is_consolidated: bool
    consolidation_score: float             # 0.0 (clean trend) to 1.0 (tight trap range)
    primary_reason: ConsolidationReason
    secondary_factors: List[str]
    is_ttm_squeeze: bool
    adx_14: float
    predicted_move_trigger: MoveTimingTrigger
    estimated_move_window_utc: str
    expected_breakout_bias: str            # BULLISH_BIAS, BEARISH_BIAS, BIDIRECTIONAL_PENDING_SWEEP
    expected_expansion_pips: float
    explanation: str
    actionable_playbook: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": "READY_FOR_REVIEW",
            "symbol": self.symbol,
            "is_consolidated": self.is_consolidated,
            "consolidation_score": round(self.consolidation_score, 2),
            "primary_reason": self.primary_reason.value,
            "secondary_factors": self.secondary_factors,
            "is_ttm_squeeze": self.is_ttm_squeeze,
            "adx_14": round(self.adx_14, 1),
            "predicted_move_trigger": self.predicted_move_trigger.value,
            "estimated_move_window_utc": self.estimated_move_window_utc,
            "expected_breakout_bias": self.expected_breakout_bias,
            "expected_expansion_pips": round(self.expected_expansion_pips, 1),
            "explanation": self.explanation,
            "actionable_playbook": self.actionable_playbook,
            "execution_authority": False,
        }


class ConsolidationTimingEngine:
    """Engine diagnosing why symbols consolidate and predicting when expansion occurs."""

    @classmethod
    def diagnose(
        cls,
        symbol: str,
        current_price: float,
        *,
        adx_14: Optional[float] = None,
        daily_atr: Optional[float] = None,
        m15_highs: Optional[List[float]] = None,
        m15_lows: Optional[List[float]] = None,
        m15_closes: Optional[List[float]] = None,
        hours_until_tier1_news: Optional[float] = None,
        current_time: Optional[datetime] = None,
        recent_expansion_pips: Optional[float] = None,
    ) -> ConsolidationDiagnosis:
        """Runs institutional multi-factor diagnostic on market consolidation and move timing."""
        now_dt = current_time or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        adx = float(adx_14) if adx_14 is not None else 20.0
        exp_move = SymbolCalibrationEngine.calculate_expected_move(
            symbol=symbol,
            current_price=current_price,
            daily_atr=daily_atr,
            is_tier1_catalyst_day=bool(hours_until_tier1_news is not None and hours_until_tier1_news <= 12.0),
            day_of_week_iso=now_dt.isocalendar()[2],
        )

        # -------------------------------------------------------------
        # Factor 1: TTM Volatility Squeeze (Bollinger inside Keltner)
        # -------------------------------------------------------------
        is_squeeze = False
        if m15_closes and len(m15_closes) >= 20:
            recent_20 = m15_closes[-20:]
            mean_c = sum(recent_20) / 20.0
            variance = sum((x - mean_c) ** 2 for x in recent_20) / 20.0
            stdev = math.sqrt(variance)
            bb_upper = mean_c + (2.0 * stdev)
            bb_lower = mean_c - (2.0 * stdev)
            bb_width = bb_upper - bb_lower

            # Keltner Channel width estimate (2.5 * ATR)
            atr_est = max(1e-5, max(recent_20) - min(recent_20))
            kc_width = 2.5 * atr_est
            if bb_width < kc_width:
                is_squeeze = True
        else:
            is_squeeze = bool(adx < 19.0 or exp_move.is_compressed)

        # -------------------------------------------------------------
        # Factor 2: Time-of-Day / Session Lull
        # -------------------------------------------------------------
        total_minutes = now_dt.hour * 60 + now_dt.minute
        is_asian = bool(0 <= total_minutes < 360)        # 00:00 - 06:00 UTC
        is_pre_london = bool(360 <= total_minutes < 420)  # 06:00 - 07:00 UTC
        is_london = bool(420 <= total_minutes < 600)      # 07:00 - 10:00 UTC
        is_ny = bool(720 <= total_minutes < 960)          # 12:00 - 16:00 UTC
        is_off_hours = bool(total_minutes >= 1020 or (600 <= total_minutes < 720))

        # -------------------------------------------------------------
        # Factor 3: Macro News Proximity
        # -------------------------------------------------------------
        is_pre_news = bool(hours_until_tier1_news is not None and 0.5 <= hours_until_tier1_news <= 12.0)

        # -------------------------------------------------------------
        # Factor 4: Post-Expansion Exhaustion
        # -------------------------------------------------------------
        is_post_exhaustion = bool(recent_expansion_pips and recent_expansion_pips >= exp_move.daily_expected_range_pips * 1.35)

        # -------------------------------------------------------------
        # Determine Primary Reason & Consolidation Score
        # -------------------------------------------------------------
        score = 0.0
        reasons_list: List[str] = []

        if is_pre_news:
            score += 0.45
            reasons_list.append(f"Major Tier-1 economic release in {hours_until_tier1_news:.1f}h")
        if is_squeeze:
            score += 0.35
            reasons_list.append("Bollinger Band / Keltner volatility squeeze active")
        if is_asian or is_off_hours:
            score += 0.25
            reasons_list.append("Low-volume session lull (Asian / inter-session dead zone)")
        if adx < 20.0:
            score += 0.20
            reasons_list.append(f"ADX({adx:.1f}) below trend threshold 20.0")
        if is_post_exhaustion:
            score += 0.30
            reasons_list.append("Post-expansion digestion following major directional run")

        score = min(1.0, score)
        is_consolidated = bool(score >= 0.40)

        # Classify primary reason
        if not is_consolidated and adx >= 25.0 and not is_squeeze:
            primary_reason = ConsolidationReason.NONE_EXPANDING
            trigger = MoveTimingTrigger.IN_EXPANSION_RUNNER
            move_window = "IMMEDIATE_ACTIVE_EXPANSION"
            bias = "TREND_CONTINUATION"
            explanation = f"{symbol} is actively expanding with ADX {adx:.1f}. Not in consolidation."
            playbook = "Deploy trend-following runners, trail stops behind M15 pullbacks."
        elif is_pre_news:
            primary_reason = ConsolidationReason.PRE_NEWS_ACCUMULATION
            trigger = MoveTimingTrigger.SCHEDULED_MACRO_CATALYST
            move_window = f"In {hours_until_tier1_news:.1f} hours at exact news release"
            bias = "BIDIRECTIONAL_PENDING_SWEEP"
            explanation = (
                f"{symbol} is in pre-news accumulation because commercial institutions and market makers are "
                f"squaring books and accumulating liquidity prior to the Tier-1 catalyst in {hours_until_tier1_news:.1f}h."
            )
            playbook = "DO NOT trade inside pre-news accumulation. Wait for post-release 15m sweep, then enter on retest."
        elif is_asian or is_off_hours:
            primary_reason = ConsolidationReason.SESSION_LULL
            trigger = MoveTimingTrigger.LONDON_OPEN_EXPANSION
            move_window = "07:00 - 09:00 UTC (London Open)"
            bias = "BIDIRECTIONAL_PENDING_SWEEP"
            explanation = (
                f"{symbol} is consolidated due to the Asian / off-hours session volume lull. "
                f"Order book depth is thin and resting liquidity is accumulating above and below the overnight range."
            )
            playbook = "Record Asian High and Asian Low as key institutional targets for London Open stop-runs."
        elif is_squeeze:
            primary_reason = ConsolidationReason.VOLATILITY_COMPRESSION_SQUEEZE
            if is_pre_london:
                trigger = MoveTimingTrigger.LONDON_OPEN_EXPANSION
                move_window = "07:00 - 08:30 UTC (London Open Killzone)"
            else:
                trigger = MoveTimingTrigger.NY_OPEN_CATALYST
                move_window = "12:30 - 14:00 UTC (NY Open Killzone)"
            bias = "BIDIRECTIONAL_PENDING_SWEEP"
            explanation = (
                f"{symbol} is consolidated inside a high-energy volatility compression squeeze. "
                f"Historical ATR is compressed ({exp_move.compression_ratio:.2f}x baseline). "
                f"Massive kinetic energy is building up for an explosive expansion."
            )
            playbook = f"Mark Asian/Session High and Low. Fade the initial manipulation Judas sweep into London/NY open, targeting {exp_move.trade_target_expected_pips:.1f} pips."
        elif is_post_exhaustion:
            primary_reason = ConsolidationReason.POST_EXPANSION_EXHAUSTION
            trigger = MoveTimingTrigger.NY_OPEN_CATALYST
            move_window = "Next session open after mean reversion balance"
            bias = "STRUCTURAL_EQUILIBRIUM"
            explanation = (
                f"{symbol} completed a large expansion move of {recent_expansion_pips:.1f} pips and is now resting "
                f"in equilibrium to digest inventory before the next directional impulse."
            )
            playbook = "Avoid chasing mid-range. Wait for retest of previous day's breakout level or Fair Value Gap."
        else:
            primary_reason = ConsolidationReason.LIQUIDITY_ENGINEERING
            trigger = MoveTimingTrigger.LIQUIDITY_SWEEP_CONFIRMATION
            move_window = "Next killzone liquidity sweep"
            bias = "BIDIRECTIONAL_PENDING_SWEEP"
            explanation = (
                f"{symbol} is building equal highs/lows inside range boundaries. "
                f"Algorithms are engineering retail buy/sell stop liquidity pools before the real expansion."
            )
            playbook = "Plot liquidity buy-stop (BSL) and sell-stop (SSL) levels. Enter only after false breakout sweeps both sides."

        return ConsolidationDiagnosis(
            symbol=symbol,
            is_consolidated=is_consolidated,
            consolidation_score=score,
            primary_reason=primary_reason,
            secondary_factors=reasons_list,
            is_ttm_squeeze=is_squeeze,
            adx_14=adx,
            predicted_move_trigger=trigger,
            estimated_move_window_utc=move_window,
            expected_breakout_bias=bias,
            expected_expansion_pips=exp_move.trade_target_expected_pips,
            explanation=explanation,
            actionable_playbook=playbook,
        )
