"""Multimodal Analysis Engine — FinAgent-Inspired Visual Reasoning.

Since most LLM providers don't support vision, this module:
  1. Detects chart patterns algorithmically from OHLCV data
  2. Translates patterns into structured text descriptions
  3. Feeds pattern confluence scores into LLM context

This gives the LLM "visual reasoning" capability without image inputs.

Patterns detected:
  - Candlestick: Doji, Hammer, Engulfing, Morning/Evening Star
  - Chart: Head & Shoulders, Double Top/Bottom, Triangle, Wedge
  - Trend: Support/Resistance breaks, Channel, Flag/Pennant
  - Volume: Volume Spike, OBV Divergence, Accumulation/Distribution

References:
  - FinAgent (2024): Multimodal fusion of price + text + visual
  - DeepFund (2025): Multi-agent framework with pattern consensus
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from core.state_persistence import StoreBackedState
except ImportError:
    from core.state_persistence import StoreBackedState
from core import flue_thresholds as flue

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

class PatternType(str, Enum):
    # Candlestick
    DOJI = "doji"
    HAMMER = "hammer"
    INVERTED_HAMMER = "inverted_hammer"
    ENGULFING_BULLISH = "engulfing_bullish"
    ENGULFING_BEARISH = "engulfing_bearish"
    MORNING_STAR = "morning_star"
    EVENING_STAR = "evening_star"
    # Chart
    DOUBLE_TOP = "double_top"
    DOUBLE_BOTTOM = "double_bottom"
    HEAD_SHOULDERS = "head_and_shoulders"
    ASCENDING_TRIANGLE = "ascending_triangle"
    DESCENDING_TRIANGLE = "descending_triangle"
    SYMMETRIC_TRIANGLE = "symmetric_triangle"
    # Trend
    SUPPORT_BREAK = "support_break"
    RESISTANCE_BREAK = "resistance_break"
    CHANNEL_TOUCH = "channel_touch"
    FLAG_BULLISH = "flag_bullish"
    FLAG_BEARISH = "flag_bearish"
    # Volume
    VOLUME_SPIKE = "volume_spike"
    OBV_DIVERGENCE = "obv_divergence"


class SignalDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class DetectedPattern:
    """A single detected chart pattern."""
    pattern_type: PatternType
    direction: SignalDirection
    confidence: float  # 0.0 - 1.0
    bar_index: int  # Which bar triggered it
    description: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pattern_type"] = self.pattern_type.value
        d["direction"] = self.direction.value
        return d


@dataclass
class PatternConfluence:
    """Aggregated pattern confluence score."""
    bullish_count: int = 0
    bearish_count: int = 0
    bullish_confidence: float = 0.0
    bearish_confidence: float = 0.0
    net_direction: SignalDirection = SignalDirection.NEUTRAL
    confluence_score: float = 0.0  # 0 = no consensus, 1 = strong consensus
    dominant_pattern: str = ""
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["net_direction"] = self.net_direction.value
        return d


@dataclass
class VisualAnalysisReport:
    """Complete visual analysis report for a symbol."""
    symbol: str
    timeframe: str
    patterns: List[DetectedPattern] = field(default_factory=list)
    confluence: PatternConfluence = field(default_factory=PatternConfluence)
    support_resistance: Dict[str, Any] = field(default_factory=dict)
    trend_assessment: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "patterns": [p.to_dict() for p in self.patterns],
            "confluence": self.confluence.to_dict(),
            "support_resistance": self.support_resistance,
            "trend_assessment": self.trend_assessment,
            "timestamp": self.timestamp,
        }

    def to_llm_context(self) -> str:
        """Format as compact text for LLM prompt injection."""
        lines = []
        if self.patterns:
            lines.append(f"CHART PATTERNS ({len(self.patterns)} detected):")
            for p in self.patterns[:8]:
                emoji = "🟢" if p.direction == SignalDirection.BULLISH else "🔴" if p.direction == SignalDirection.BEARISH else "⚪"
                lines.append(f"  {emoji} {p.pattern_type.value} ({p.confidence:.0%}): {p.description}")

        c = self.confluence
        if c.summary:
            lines.append(f"\nPATTERN CONFLUENCE: {c.net_direction.value} (score={c.confluence_score:.2f})")
            lines.append(f"  {c.summary}")

        sr = self.support_resistance
        if sr:
            lines.append(f"\nSUPPORT/RESISTANCE:")
            if sr.get("nearest_support"):
                lines.append(f"  Support: {sr['nearest_support']:.5f}")
            if sr.get("nearest_resistance"):
                lines.append(f"  Resistance: {sr['nearest_resistance']:.5f}")

        trend = self.trend_assessment
        if trend:
            lines.append(f"\nTREND: {trend.get('direction', 'UNKNOWN')} "
                         f"(strength={trend.get('strength', 0):.2f}, "
                         f"bars_in_trend={trend.get('bars_in_trend', 0)})")

        return "\n".join(lines) if lines else "No patterns detected."


# ---------------------------------------------------------------------------
# Candlestick Pattern Detection
# ---------------------------------------------------------------------------

class CandlestickDetector:
    """Detect candlestick patterns from OHLCV data."""

    def __init__(self, lookback: int = 50):
        self.lookback = lookback

    def detect(self, ohlcv: List[Dict[str, Any]]) -> List[DetectedPattern]:
        """Detect candlestick patterns in recent bars."""
        if len(ohlcv) < 5:
            return []

        patterns = []
        bars = ohlcv[-self.lookback:]

        for i in range(2, len(bars)):
            o, h, l, c = (
                bars[i].get("open", 0),
                bars[i].get("high", 0),
                bars[i].get("low", 0),
                bars[i].get("close", 0),
            )
            if h <= l or o <= 0:
                continue

            body = abs(c - o)
            total_range = h - l
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l

            # --- Doji ---
            if total_range > 0 and body / total_range < 0.1 and total_range > 0:
                patterns.append(DetectedPattern(
                    pattern_type=PatternType.DOJI,
                    direction=SignalDirection.NEUTRAL,
                    confidence=min(0.9, 1.0 - body / total_range),
                    bar_index=i,
                    description="Indecision candle — small body, potential reversal",
                ))

            # --- Hammer (FLUE P02: C>O + D>2B, no upper wick restriction) ---
            if flue.hammer(o, h, l, c):
                patterns.append(DetectedPattern(
                    pattern_type=PatternType.HAMMER,
                    direction=SignalDirection.BULLISH,
                    confidence=min(0.85, 0.5 + lower_wick / max(total_range, 1e-9) * 0.5),
                    bar_index=i,
                    description="Bullish reversal — long lower wick rejects lower prices",
                ))

            # --- Engulfing (FLUE P02: exact body containment, not 1.2x) ---
            if i >= 1:
                prev_o = bars[i - 1].get("open", 0)
                prev_h = bars[i - 1].get("high", 0)
                prev_l = bars[i - 1].get("low", 0)
                prev_c = bars[i - 1].get("close", 0)
                pair = [(prev_o, prev_h, prev_l, prev_c), (o, h, l, c)]

                if flue.bullish_engulfing(pair):
                    prev_body = abs(prev_c - prev_o)
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.ENGULFING_BULLISH,
                        direction=SignalDirection.BULLISH,
                        confidence=min(0.9, 0.6 + body / max(prev_body, 1e-9) * 0.1),
                        bar_index=i,
                        description=f"Bullish engulfing — exact body containment",
                    ))
                elif flue.bearish_engulfing(pair):
                    prev_body = abs(prev_c - prev_o)
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.ENGULFING_BEARISH,
                        direction=SignalDirection.BEARISH,
                        confidence=min(0.9, 0.6 + body / max(prev_body, 1e-9) * 0.1),
                        bar_index=i,
                        description=f"Bearish engulfing — exact body containment",
                    ))

            # --- Morning Star (3-candle) ---
            if i >= 2:
                p2_o, p2_c = bars[i - 2].get("open", 0), bars[i - 2].get("close", 0)
                p1_o, p1_c = bars[i - 1].get("open", 0), bars[i - 1].get("close", 0)
                p1_body = abs(p1_c - p1_o)
                p2_body = abs(p2_c - p2_o)

                if (p2_c < p2_o and p2_body > 0  # First: bearish
                        and p1_body < p2_body * 0.3  # Second: small body
                        and c > o and c > (p2_o + p2_c) / 2):  # Third: bullish recovery
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.MORNING_STAR,
                        direction=SignalDirection.BULLISH,
                        confidence=0.8,
                        bar_index=i,
                        description="Morning star — 3-candle bullish reversal",
                    ))

            # --- Evening Star (3-candle) ---
            if i >= 2:
                p2_o, p2_c = bars[i - 2].get("open", 0), bars[i - 2].get("close", 0)
                p1_o, p1_c = bars[i - 1].get("open", 0), bars[i - 1].get("close", 0)
                p1_body = abs(p1_c - p1_o)
                p2_body = abs(p2_c - p2_o)

                if (p2_c > p2_o and p2_body > 0  # First: bullish
                        and p1_body < p2_body * 0.3  # Second: small body
                        and c < o and c < (p2_o + p2_c) / 2):  # Third: bearish recovery
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.EVENING_STAR,
                        direction=SignalDirection.BEARISH,
                        confidence=0.8,
                        bar_index=i,
                        description="Evening star — 3-candle bearish reversal",
                    ))

        return patterns


# ---------------------------------------------------------------------------
# Chart Pattern Detection (Head & Shoulders, Double Top/Bottom, Triangles)
# ---------------------------------------------------------------------------

class ChartPatternDetector:
    """Detect geometric chart patterns using pivot points."""

    def __init__(self, lookback: int = 100, pivot_window: int = 5):
        self.lookback = lookback
        self.pivot_window = pivot_window

    def _find_pivots(self, highs: np.ndarray, lows: np.ndarray) -> Tuple[List[int], List[int]]:
        """Find swing highs and lows."""
        swing_highs = []
        swing_lows = []
        w = self.pivot_window
        for i in range(w, len(highs) - w):
            if all(highs[i] >= highs[i - j] for j in range(1, w + 1)) and \
               all(highs[i] >= highs[i + j] for j in range(1, w + 1)):
                swing_highs.append(i)
            if all(lows[i] <= lows[i - j] for j in range(1, w + 1)) and \
               all(lows[i] <= lows[i + j] for j in range(1, w + 1)):
                swing_lows.append(i)
        return swing_highs, swing_lows

    def detect(self, ohlcv: List[Dict[str, Any]]) -> List[DetectedPattern]:
        """Detect chart patterns."""
        if len(ohlcv) < 20:
            return []

        bars = ohlcv[-self.lookback:]
        highs = np.array([b.get("high", 0) for b in bars], dtype=float)
        lows = np.array([b.get("low", 0) for b in bars], dtype=float)
        closes = np.array([b.get("close", 0) for b in bars], dtype=float)

        swing_highs, swing_lows = self._find_pivots(highs, lows)
        patterns: List[DetectedPattern] = []

        # --- Double Top ---
        if len(swing_highs) >= 2:
            h1_idx, h2_idx = swing_highs[-2], swing_highs[-1]
            h1, h2 = highs[h1_idx], highs[h2_idx]
            tolerance = (h1 + h2) / 2 * 0.02  # 2% tolerance
            if abs(h1 - h2) < tolerance and h2_idx - h1_idx >= 5:
                # Check for neckline break
                neckline_idx = (h1_idx + h2_idx) // 2
                neckline = lows[neckline_idx]
                if len(closes) > h2_idx and closes[-1] < neckline:
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.DOUBLE_TOP,
                        direction=SignalDirection.BEARISH,
                        confidence=min(0.85, 0.5 + (1 - abs(h1 - h2) / tolerance) * 0.35),
                        bar_index=h2_idx,
                        description=f"Double top at {h1:.5f}/{h2:.5f}, neckline broken",
                        metadata={"resistance": float((h1 + h2) / 2), "neckline": float(neckline)},
                    ))
                else:
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.DOUBLE_TOP,
                        direction=SignalDirection.BEARISH,
                        confidence=0.5,
                        bar_index=h2_idx,
                        description=f"Double top forming at {h1:.5f}/{h2:.5f}, neckline {neckline:.5f}",
                        metadata={"resistance": float((h1 + h2) / 2), "neckline": float(neckline)},
                    ))

        # --- Double Bottom ---
        if len(swing_lows) >= 2:
            l1_idx, l2_idx = swing_lows[-2], swing_lows[-1]
            l1, l2 = lows[l1_idx], lows[l2_idx]
            tolerance = (l1 + l2) / 2 * 0.02
            if abs(l1 - l2) < tolerance and l2_idx - l1_idx >= 5:
                neckline_idx = (l1_idx + l2_idx) // 2
                neckline = highs[neckline_idx]
                if len(closes) > l2_idx and closes[-1] > neckline:
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.DOUBLE_BOTTOM,
                        direction=SignalDirection.BULLISH,
                        confidence=min(0.85, 0.5 + (1 - abs(l1 - l2) / tolerance) * 0.35),
                        bar_index=l2_idx,
                        description=f"Double bottom at {l1:.5f}/{l2:.5f}, neckline broken",
                        metadata={"support": float((l1 + l2) / 2), "neckline": float(neckline)},
                    ))
                else:
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.DOUBLE_BOTTOM,
                        direction=SignalDirection.BULLISH,
                        confidence=0.5,
                        bar_index=l2_idx,
                        description=f"Double bottom forming at {l1:.5f}/{l2:.5f}, neckline {neckline:.5f}",
                        metadata={"support": float((l1 + l2) / 2), "neckline": float(neckline)},
                    ))

        # --- Triangle (converging highs and lows) ---
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            recent_highs = [highs[i] for i in swing_highs[-3:]]
            recent_lows = [lows[i] for i in swing_lows[-3:]]
            if len(recent_highs) >= 2 and len(recent_lows) >= 2:
                highs_declining = all(recent_highs[j] >= recent_highs[j + 1] for j in range(len(recent_highs) - 1))
                lows_increasing = all(recent_lows[j] <= recent_lows[j + 1] for j in range(len(recent_lows) - 1))
                highs_flat = all(abs(recent_highs[j] - recent_highs[j + 1]) / max(recent_highs[j], 1e-10) < 0.005
                                for j in range(len(recent_highs) - 1))

                if highs_declining and lows_increasing:
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.SYMMETRIC_TRIANGLE,
                        direction=SignalDirection.NEUTRAL,
                        confidence=0.6,
                        bar_index=len(bars) - 1,
                        description="Symmetric triangle — converging, breakout imminent",
                    ))
                elif highs_flat and lows_increasing:
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.ASCENDING_TRIANGLE,
                        direction=SignalDirection.BULLISH,
                        confidence=0.6,
                        bar_index=len(bars) - 1,
                        description="Ascending triangle — flat resistance, rising support",
                    ))
                elif highs_declining and len(recent_lows) >= 2:
                    lows_flat = all(abs(recent_lows[j] - recent_lows[j + 1]) / max(abs(recent_lows[j]), 1e-10) < 0.005
                                   for j in range(len(recent_lows) - 1))
                    if lows_flat:
                        patterns.append(DetectedPattern(
                            pattern_type=PatternType.DESCENDING_TRIANGLE,
                            direction=SignalDirection.BEARISH,
                            confidence=0.6,
                            bar_index=len(bars) - 1,
                            description="Descending triangle — falling resistance, flat support",
                        ))

        return patterns


# ---------------------------------------------------------------------------
# Trend & Support/Resistance Detection
# ---------------------------------------------------------------------------

class TrendAnalyzer:
    """Detect trend direction, strength, and support/resistance levels."""

    def __init__(self, lookback: int = 100):
        self.lookback = lookback

    def analyze(self, ohlcv: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze trend and S/R levels."""
        if len(ohlcv) < 20:
            return {}

        bars = ohlcv[-self.lookback:]
        closes = np.array([b.get("close", 0) for b in bars], dtype=float)
        highs = np.array([b.get("high", 0) for b in bars], dtype=float)
        lows = np.array([b.get("low", 0) for b in bars], dtype=float)

        # --- Trend via linear regression slope ---
        x = np.arange(len(closes))
        slope, intercept = np.polyfit(x, closes, 1)
        predicted = slope * x + intercept
        ss_res = np.sum((closes - predicted) ** 2)
        ss_tot = np.sum((closes - np.mean(closes)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        # Normalize slope to pips
        avg_price = np.mean(closes)
        slope_pips = slope / avg_price * 10000 if avg_price > 0 else 0

        if slope_pips > 0.1:
            direction = "UPTREND"
        elif slope_pips < -0.1:
            direction = "DOWNTREND"
        else:
            direction = "SIDEWAYS"

        # Count bars in current trend
        bars_in_trend = 0
        if direction == "UPTREND":
            for i in range(len(closes) - 1, -1, -1):
                if closes[i] >= closes[max(0, i - 1)]:
                    bars_in_trend += 1
                else:
                    break
        elif direction == "DOWNTREND":
            for i in range(len(closes) - 1, -1, -1):
                if closes[i] <= closes[max(0, i - 1)]:
                    bars_in_trend += 1
                else:
                    break

        # --- Support/Resistance via pivot clustering ---
        sr_levels = []
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                sr_levels.append(("resistance", float(highs[i])))
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                sr_levels.append(("support", float(lows[i])))

        # Cluster nearby levels (within 0.1% of each other)
        clustered = []
        for level_type, level in sorted(sr_levels, key=lambda x: x[1]):
            if clustered and abs(level - clustered[-1][1]) / max(level, 1e-10) < 0.001:
                # Merge — keep the one with more touches
                continue
            clustered.append((level_type, level))

        current_price = closes[-1]
        supports = sorted([l for t, l in clustered if t == "support" and l < current_price], reverse=True)
        resistances = sorted([l for t, l in clustered if t == "resistance" and l > current_price])

        return {
            "direction": direction,
            "strength": round(float(min(abs(r_squared) * abs(slope_pips), 1.0)), 4),
            "slope_pips_per_bar": round(float(slope_pips), 4),
            "r_squared": round(float(r_squared), 4),
            "bars_in_trend": bars_in_trend,
            "nearest_support": supports[0] if supports else None,
            "nearest_resistance": resistances[0] if resistances else None,
            "support_count": len(supports),
            "resistance_count": len(resistances),
        }


# ---------------------------------------------------------------------------
# Volume Pattern Detection
# ---------------------------------------------------------------------------

class VolumePatternDetector:
    """Detect volume-based patterns."""

    def __init__(self, lookback: int = 50, spike_threshold: float = 2.0):
        self.lookback = lookback
        self.spike_threshold = spike_threshold

    def detect(self, ohlcv: List[Dict[str, Any]]) -> List[DetectedPattern]:
        """Detect volume patterns."""
        if len(ohlcv) < 10:
            return []

        bars = ohlcv[-self.lookback:]
        volumes = np.array([b.get("volume", 0) for b in bars], dtype=float)
        closes = np.array([b.get("close", 0) for b in bars], dtype=float)

        patterns: List[DetectedPattern] = []

        # --- Volume Spike ---
        if len(volumes) > 5:
            avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
            if avg_vol > 0:
                last_vol = volumes[-1]
                vol_ratio = last_vol / avg_vol
                if vol_ratio > self.spike_threshold:
                    # Direction based on close vs open
                    last_bar = bars[-1]
                    direction = (SignalDirection.BULLISH
                                 if last_bar.get("close", 0) > last_bar.get("open", 0)
                                 else SignalDirection.BEARISH)
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.VOLUME_SPIKE,
                        direction=direction,
                        confidence=min(0.9, 0.4 + vol_ratio * 0.1),
                        bar_index=len(bars) - 1,
                        description=f"Volume spike {vol_ratio:.1f}x average — {'bullish' if direction == SignalDirection.BULLISH else 'bearish'} conviction",
                        metadata={"volume_ratio": round(float(vol_ratio), 2)},
                    ))

        # --- OBV Divergence ---
        if len(closes) > 20:
            obv = np.zeros(len(closes))
            for i in range(1, len(closes)):
                if closes[i] > closes[i - 1]:
                    obv[i] = obv[i - 1] + volumes[i]
                elif closes[i] < closes[i - 1]:
                    obv[i] = obv[i - 1] - volumes[i]
                else:
                    obv[i] = obv[i - 1]

            # Check if price and OBV diverge over last 10 bars
            lookback = min(10, len(closes) - 1)
            price_change = closes[-1] - closes[-lookback - 1]
            obv_change = obv[-1] - obv[-lookback - 1]

            if price_change > 0 and obv_change < 0:
                patterns.append(DetectedPattern(
                    pattern_type=PatternType.OBV_DIVERGENCE,
                    direction=SignalDirection.BEARISH,
                    confidence=0.6,
                    bar_index=len(bars) - 1,
                    description="Bearish OBV divergence — price rising but volume declining",
                ))
            elif price_change < 0 and obv_change > 0:
                patterns.append(DetectedPattern(
                    pattern_type=PatternType.OBV_DIVERGENCE,
                    direction=SignalDirection.BULLISH,
                    confidence=0.6,
                    bar_index=len(bars) - 1,
                    description="Bullish OBV divergence — price falling but volume accumulating",
                ))

        return patterns


# ---------------------------------------------------------------------------
# Confluence Scorer
# ---------------------------------------------------------------------------

class ConfluenceScorer:
    """Compute pattern confluence scores."""

    def score(self, patterns: List[DetectedPattern]) -> PatternConfluence:
        """Aggregate pattern signals into a confluence score."""
        if not patterns:
            return PatternConfluence(summary="No patterns detected")

        bullish = [p for p in patterns if p.direction == SignalDirection.BULLISH]
        bearish = [p for p in patterns if p.direction == SignalDirection.BEARISH]

        bull_conf = sum(p.confidence for p in bullish)
        bear_conf = sum(p.confidence for p in bearish)

        if bull_conf > bear_conf:
            net = SignalDirection.BULLISH
            confluence = min(1.0, bull_conf / max(bull_conf + bear_conf, 1) * 1.5)
        elif bear_conf > bull_conf:
            net = SignalDirection.BEARISH
            confluence = min(1.0, bear_conf / max(bull_conf + bear_conf, 1) * 1.5)
        else:
            net = SignalDirection.NEUTRAL
            confluence = 0.0

        # Find dominant pattern
        all_patterns = sorted(patterns, key=lambda p: p.confidence, reverse=True)
        dominant = all_patterns[0].pattern_type.value if all_patterns else ""

        # Build summary
        parts = []
        if bullish:
            parts.append(f"{len(bullish)} bullish ({', '.join(p.pattern_type.value for p in bullish[:3])})")
        if bearish:
            parts.append(f"{len(bearish)} bearish ({', '.join(p.pattern_type.value for p in bearish[:3])})")

        summary = " vs ".join(parts) if parts else "Neutral"

        return PatternConfluence(
            bullish_count=len(bullish),
            bearish_count=len(bearish),
            bullish_confidence=round(bull_conf, 4),
            bearish_confidence=round(bear_conf, 4),
            net_direction=net,
            confluence_score=round(confluence, 4),
            dominant_pattern=dominant,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# Multimodal Analysis Engine
# ---------------------------------------------------------------------------

class MultimodalAnalysisEngine(StoreBackedState):
    """Complete visual/pattern analysis pipeline.

    Workflow:
      1. Detect candlestick patterns
      2. Detect chart patterns (H&S, Double Top/Bottom, Triangles)
      3. Detect volume patterns (spike, OBV divergence)
      4. Analyze trend and support/resistance
      5. Compute confluence score
      6. Format for LLM context
    """

    store_key = "multimodal_analysis_state"

    def __init__(
        self,
        lookback: int = 100,
        state_file: str = "multimodal_analysis_state.json",
        store: Optional[Any] = None,
    ):
        self.lookback = lookback
        self.state_file = state_file
        self.store = store
        self.candlestick_detector = CandlestickDetector(lookback)
        self.chart_detector = ChartPatternDetector(lookback)
        self.volume_detector = VolumePatternDetector(lookback)
        self.trend_analyzer = TrendAnalyzer(lookback)
        self.confluence_scorer = ConfluenceScorer()
        if store is not None:
            self.attach_store(store)

    def analyze(
        self, symbol: str, timeframe: str, ohlcv: List[Dict[str, Any]]
    ) -> VisualAnalysisReport:
        """Run full pattern analysis on OHLCV data."""
        # 1. Candlestick patterns
        candle_patterns = self.candlestick_detector.detect(ohlcv)

        # 2. Chart patterns
        chart_patterns = self.chart_detector.detect(ohlcv)

        # 3. Volume patterns
        volume_patterns = self.volume_detector.detect(ohlcv)

        # 4. Trend & S/R
        trend = self.trend_analyzer.analyze(ohlcv)
        sr = {
            "nearest_support": trend.get("nearest_support"),
            "nearest_resistance": trend.get("nearest_resistance"),
            "support_count": trend.get("support_count", 0),
            "resistance_count": trend.get("resistance_count", 0),
        }

        # 5. Confluence
        all_patterns = candle_patterns + chart_patterns + volume_patterns
        confluence = self.confluence_scorer.score(all_patterns)

        report = VisualAnalysisReport(
            symbol=symbol,
            timeframe=timeframe,
            patterns=all_patterns,
            confluence=confluence,
            support_resistance=sr,
            trend_assessment=trend,
            timestamp=time.time(),
        )

        self._save_state(report)
        return report

    def _save_state(self, report: VisualAnalysisReport) -> None:
        """Persist latest report to the store + file mirror."""
        # Keep only summary (not full pattern list)
        self._save_state_dict({
            "symbol": report.symbol,
            "timeframe": report.timeframe,
            "confluence": report.confluence.to_dict(),
            "trend": report.trend_assessment,
            "pattern_count": len(report.patterns),
            "timestamp": report.timestamp,
        })

    def load_state(self) -> Optional[Dict[str, Any]]:
        """Load latest analysis state."""
        return self._load_state_dict() or None

    def get_status(self) -> Dict[str, Any]:
        """Return current status."""
        state = self.load_state()
        if state is None:
            return {"status": "NO_ANALYSIS"}
        return {
            "status": "ANALYZED",
            "symbol": state.get("symbol"),
            "confluence": state.get("confluence", {}).get("net_direction"),
            "pattern_count": state.get("pattern_count", 0),
            "trend": state.get("trend", {}).get("direction"),
        }


# Compatibility alias
MultimodalEngine = MultimodalAnalysisEngine
