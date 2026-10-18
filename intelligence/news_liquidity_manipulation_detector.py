"""Microstructure News Liquidity Sweep & Manipulation Detector.

Identifies institutional manipulation setups around high-impact news releases:
1. Maps pre-event liquidity pools (Equal Highs / EQH and Equal Lows / EQL) as Buy-Side (BSL) and Sell-Side Liquidity (SSL).
2. Detects the "Judas Swing" (the fakeout wick that sweeps liquidity against the true macroeconomic fundamental impulse).
3. Confirms Market Structure Shift (MSS) upon aggressive counter-displacement.
4. Generates an asymmetric Risk-Reward Mitigation Setup (entering at the Order Block / FVG mitigation with SL beyond the manipulation wick).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Sequence

from core.smc_structure import liquidity_pools, detect_structure, find_order_blocks


@dataclass(frozen=True)
class ManipulationSetup:
    """Optimal trade execution setup generated post-manipulation."""
    suggested_action: str            # "BUY" or "SELL"
    limit_entry_price: float         # Retracement / Mitigation entry price
    stop_loss: float                 # Invalidated beyond manipulation extreme
    take_profit: float               # Macro SVAR equilibrium target
    risk_reward_ratio: float         # Expected R:R (e.g. 2.5 - 6.0)
    mitigation_zone_top: float
    mitigation_zone_bottom: float


@dataclass(frozen=True)
class LiquiditySweepVerdict:
    """Diagnostic outcome of liquidity sweep and manipulation analysis."""
    is_manipulation_detected: bool
    sweep_type: str                  # "BUY_SIDE_LIQUIDITY_SWEPT", "SELL_SIDE_LIQUIDITY_SWEPT", "NONE"
    swept_level: float               # The EQH or EQL level that was raided
    manipulation_extreme: float      # The wick high or low that swept the liquidity
    market_structure_shift: bool     # True if displacement reversed back in macro direction
    displacement_ratio: float        # Body to range displacement strength
    setup: Optional[ManipulationSetup] = None


class NewsLiquidityManipulationDetector:
    """Detects institutional liquidity sweeps and frames asymmetric mitigation entries."""

    def __init__(self, sweep_tolerance_pips: float = 1.5, min_displacement_body_ratio: float = 0.50) -> None:
        self.sweep_tolerance_pips = sweep_tolerance_pips
        self.min_displacement_ratio = min_displacement_body_ratio

    def analyze_news_bar(
        self,
        opens: Sequence[float],
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        macro_directional_bias: str,
        news_bar_index: int,
        pip_size: float = 0.0001,
        macro_target_price: Optional[float] = None,
        spread_pips: float = 0.8
    ) -> LiquiditySweepVerdict:
        """Analyze price action around news_bar_index to detect liquidity sweeps and manipulation."""
        n = len(closes)
        if news_bar_index >= n or news_bar_index < 5:
            return LiquiditySweepVerdict(
                is_manipulation_detected=False,
                sweep_type="NONE",
                swept_level=0.0,
                manipulation_extreme=0.0,
                market_structure_shift=False,
                displacement_ratio=0.0
            )

        # 1. Map pre-event liquidity pools up to news_bar_index
        pre_highs = list(highs[:news_bar_index])
        pre_lows = list(lows[:news_bar_index])
        pools = liquidity_pools(pre_highs, pre_lows, tol_frac=0.0006, lookback=15)
        eqh_list = [p[1] for p in pools.get("EQH", [])]
        eql_list = [p[1] for p in pools.get("EQL", [])]

        # Fallback to local swing highs / lows if no strict EQH/EQL formed
        if not eqh_list and len(pre_highs) >= 5:
            eqh_list = [max(pre_highs[-10:])]
        if not eql_list and len(pre_lows) >= 5:
            eql_list = [min(pre_lows[-10:])]

        # 2. Inspect the news bar (and immediate follow-through bar if available)
        bar_o = opens[news_bar_index]
        bar_h = highs[news_bar_index]
        bar_l = lows[news_bar_index]
        bar_c = closes[news_bar_index]
        bar_range = max(bar_h - bar_l, 1e-6)
        body = abs(bar_c - bar_o)
        displacement_ratio = body / bar_range

        is_manipulation = False
        sweep_type = "NONE"
        swept_level = 0.0
        manipulation_extreme = 0.0
        mss = False

        tol = self.sweep_tolerance_pips * pip_size

        if macro_directional_bias.upper() == "SELL":
            # Bearish Macro Shock -> Look for Bullish Judas Swing sweeping Buy-Side Liquidity (BSL)
            swept_candidates = [eqh for eqh in eqh_list if bar_h > eqh]
            if swept_candidates:
                swept_eqh = max(swept_candidates)
                if bar_c < bar_h - tol or bar_c < swept_eqh + tol:
                    is_manipulation = True
                    sweep_type = "BUY_SIDE_LIQUIDITY_SWEPT"
                    swept_level = swept_eqh
                    manipulation_extreme = bar_h
                    # MSS confirmed if candle closed bearish with body displacement, rejection wick, or broke below prior bar
                    upper_wick = bar_h - max(bar_o, bar_c)
                    closed_below_prev = bar_c < closes[news_bar_index - 1] if news_bar_index > 0 else False
                    if (bar_c < bar_o and displacement_ratio >= self.min_displacement_ratio) or (upper_wick >= 0.35 * bar_range) or closed_below_prev:
                        mss = True

        elif macro_directional_bias.upper() == "BUY":
            # Bullish Macro Shock -> Look for Bearish Judas Swing sweeping Sell-Side Liquidity (SSL)
            swept_candidates = [eql for eql in eql_list if bar_l < eql]
            if swept_candidates:
                swept_eql = min(swept_candidates)
                if bar_c > bar_l + tol or bar_c > swept_eql - tol:
                    is_manipulation = True
                    sweep_type = "SELL_SIDE_LIQUIDITY_SWEPT"
                    swept_level = swept_eql
                    manipulation_extreme = bar_l
                    # MSS confirmed if candle closed bullish with body displacement, rejection wick, or broke above prior bar
                    lower_wick = min(bar_o, bar_c) - bar_l
                    closed_above_prev = bar_c > closes[news_bar_index - 1] if news_bar_index > 0 else False
                    if (bar_c > bar_o and displacement_ratio >= self.min_displacement_ratio) or (lower_wick >= 0.35 * bar_range) or closed_above_prev:
                        mss = True

        # 3. Generate setup if manipulation detected
        setup = None
        if is_manipulation:
            # Default macro target fallback if not provided
            if macro_target_price is None:
                if macro_directional_bias == "SELL":
                    macro_target_price = bar_c - (bar_range * 2.5)
                else:
                    macro_target_price = bar_c + (bar_range * 2.5)

            setup = self.build_manipulation_setup(
                side=macro_directional_bias,
                sweep_extreme=manipulation_extreme,
                displacement_close=bar_c,
                macro_target_price=macro_target_price,
                pip_size=pip_size,
                spread_pips=spread_pips
            )

        return LiquiditySweepVerdict(
            is_manipulation_detected=is_manipulation,
            sweep_type=sweep_type,
            swept_level=swept_level,
            manipulation_extreme=manipulation_extreme,
            market_structure_shift=mss,
            displacement_ratio=displacement_ratio,
            setup=setup
        )

    def build_manipulation_setup(
        self,
        side: str,
        sweep_extreme: float,
        displacement_close: float,
        macro_target_price: float,
        pip_size: float = 0.0001,
        spread_pips: float = 0.8
    ) -> ManipulationSetup:
        """Frame the asymmetric mitigation entry zone with tight stop and macro target."""
        buffer = (spread_pips + 1.0) * pip_size

        if side.upper() == "SELL":
            # Limit entry at 50% retracement of the displacement candle toward the swept extreme
            entry = displacement_close + 0.382 * (sweep_extreme - displacement_close)
            stop = sweep_extreme + buffer
            tp = macro_target_price
            zone_top = sweep_extreme
            zone_bottom = displacement_close + 0.236 * (sweep_extreme - displacement_close)
            risk = max(stop - entry, buffer)
            reward = max(entry - tp, 0.0)
        else:
            # BUY setup: Limit entry at 50% retracement of the upward displacement
            entry = displacement_close - 0.382 * (displacement_close - sweep_extreme)
            stop = sweep_extreme - buffer
            tp = macro_target_price
            zone_top = displacement_close - 0.236 * (displacement_close - sweep_extreme)
            zone_bottom = sweep_extreme
            risk = max(entry - stop, buffer)
            reward = max(tp - entry, 0.0)

        rr = reward / risk if risk > 0 else 1.0

        return ManipulationSetup(
            suggested_action=side.upper(),
            limit_entry_price=round(entry, 5),
            stop_loss=round(stop, 5),
            take_profit=round(tp, 5),
            risk_reward_ratio=round(rr, 2),
            mitigation_zone_top=round(zone_top, 5),
            mitigation_zone_bottom=round(zone_bottom, 5)
        )
