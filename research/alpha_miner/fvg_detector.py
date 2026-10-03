"""Fair Value Gap (FVG) Detection and Degree Scoring Module.

Implements the FVG degree metric from Kondapally (2025):
  - Detects 3-candle FVGs (bullish: candle[2].high < candle[0].low;
    bearish: candle[2].low > candle[0].high)
  - Computes "degree" = |slope| of linear regression through formation ticks
  - Low-degree FVGs (<=0.00015) generate 3.2x stronger reactions (p<0.001)
  - FVG quality score: degree + volume + ATR alignment

References:
  - Kondapally, A. (2025). Quantifying Fair Value Gaps.
  - GitHub: aryan1ko/fvg-degree-study
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FVGType(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class FVGQuality(Enum):
    STRONG = "strong"      # degree <= 0.00015
    MODERATE = "moderate"  # 0.00015 < degree <= 0.0004
    WEAK = "weak"          # degree > 0.0004


@dataclass
class FVGSignal:
    """Detected FVG signal."""
    bar_index: int
    fvg_type: FVGType
    gap_high: float
    gap_low: float
    gap_mid: float
    gap_size: float
    degree: float
    quality: FVGQuality
    volume_ratio: float
    atr_normalized: float
    reaction_magnitude: float
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bar_index": self.bar_index,
            "type": self.fvg_type.value,
            "gap_high": round(self.gap_high, 6),
            "gap_low": round(self.gap_low, 6),
            "gap_mid": round(self.gap_mid, 6),
            "gap_size": round(self.gap_size, 6),
            "degree": round(self.degree, 8),
            "quality": self.quality.value,
            "volume_ratio": round(self.volume_ratio, 4),
            "atr_normalized": round(self.atr_normalized, 6),
            "reaction_magnitude": round(self.reaction_magnitude, 4),
            "confidence": round(self.confidence, 4),
        }


@dataclass
class FVGResult:
    """Full FVG analysis result."""
    signals: List[FVGSignal]
    total_fvgs: int
    strong_count: int
    moderate_count: int
    weak_count: int
    avg_degree: float
    coverage_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_fvgs": self.total_fvgs,
            "strong_count": self.strong_count,
            "moderate_count": self.moderate_count,
            "weak_count": self.weak_count,
            "avg_degree": round(self.avg_degree, 8),
            "coverage_pct": round(self.coverage_pct, 4),
            "signals": [s.to_dict() for s in self.signals[-10:]],
        }


class FVGDetector:
    """Fair Value Gap detector with degree scoring.

    Detects FVGs and scores them by formation quality.
    Strong FVGs (low degree) have higher retest probability.
    """

    def __init__(
        self,
        atr_period: int = 14,
        volume_lookback: int = 20,
        strong_threshold: float = 0.00015,
        weak_threshold: float = 0.0004,
        min_gap_atr_ratio: float = 0.5,
        max_fvgs: int = 50,
    ):
        self.atr_period = atr_period
        self.volume_lookback = volume_lookback
        self.strong_threshold = strong_threshold
        self.weak_threshold = weak_threshold
        self.min_gap_atr_ratio = min_gap_atr_ratio
        self.max_fvgs = max_fvgs

    def detect(self, ohlcv: pd.DataFrame) -> FVGResult:
        """Detect FVGs and compute degree scores.

        Args:
            ohlcv: DataFrame with columns [open, high, low, close, volume]

        Returns:
            FVGResult with detected signals and summary
        """
        if len(ohlcv) < 5:
            return self._empty_result()

        closes = ohlcv["close"].values
        highs = ohlcv["high"].values
        lows = ohlcv["low"].values
        volumes = ohlcv["volume"].values

        atr = self._compute_atr(highs, lows, closes)
        vol_ma = pd.Series(volumes).rolling(self.volume_lookback).mean().values

        signals: List[FVGSignal] = []

        for i in range(2, len(ohlcv)):
            # Bullish FVG: candle[2].high < candle[0].low
            if highs[i] < lows[i - 2]:
                gap_high = lows[i - 2]
                gap_low = highs[i]
                gap_size = gap_high - gap_low
                fvg_type = FVGType.BULLISH
            # Bearish FVG: candle[2].low > candle[0].high
            elif lows[i] > highs[i - 2]:
                gap_low = highs[i - 2]
                gap_high = lows[i]
                gap_size = gap_high - gap_low
                fvg_type = FVGType.BEARISH
            else:
                continue

            if atr[i] <= 0 or gap_size < atr[i] * self.min_gap_atr_ratio:
                continue

            degree = self._compute_degree(
                closes, highs, lows, i, fvg_type
            )

            vol_ratio = volumes[i] / vol_ma[i] if vol_ma[i] > 0 else 1.0

            quality = self._classify_quality(degree)

            atr_norm = gap_size / atr[i] if atr[i] > 0 else 0.0

            reaction = self._measure_reaction(
                closes, i, fvg_type, lookahead=5
            )

            confidence = self._compute_confidence(
                degree, vol_ratio, atr_norm, quality
            )

            signals.append(FVGSignal(
                bar_index=i,
                fvg_type=fvg_type,
                gap_high=gap_high,
                gap_low=gap_low,
                gap_mid=(gap_high + gap_low) / 2,
                gap_size=gap_size,
                degree=degree,
                quality=quality,
                volume_ratio=vol_ratio,
                atr_normalized=atr_norm,
                reaction_magnitude=reaction,
                confidence=confidence,
            ))

        signals = signals[-self.max_fvgs:]

        return FVGResult(
            signals=signals,
            total_fvgs=len(signals),
            strong_count=sum(1 for s in signals if s.quality == FVGQuality.STRONG),
            moderate_count=sum(1 for s in signals if s.quality == FVGQuality.MODERATE),
            weak_count=sum(1 for s in signals if s.quality == FVGQuality.WEAK),
            avg_degree=float(np.mean([s.degree for s in signals])) if signals else 0.0,
            coverage_pct=len(signals) / max(len(ohlcv) - 2, 1) * 100,
        )

    def _compute_atr(
        self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray
    ) -> np.ndarray:
        """Compute Average True Range."""
        n = len(highs)
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )

        atr = np.zeros(n)
        atr[:self.atr_period] = tr[:self.atr_period].mean()
        for i in range(self.atr_period, n):
            atr[i] = (atr[i - 1] * (self.atr_period - 1) + tr[i]) / self.atr_period
        return atr

    def _compute_degree(
        self,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        bar_idx: int,
        fvg_type: FVGType,
    ) -> float:
        """Compute FVG degree (|slope| of linear regression).

        Uses candle midpoints during formation window as proxy for tick data.
        """
        window = closes[max(0, bar_idx - 4):bar_idx + 1]
        if len(window) < 3:
            return 0.001

        x = np.arange(len(window), dtype=float)
        y = window

        slope = np.polyfit(x, y, 1)[0]
        return abs(slope)

    def _classify_quality(self, degree: float) -> FVGQuality:
        """Classify FVG quality by degree threshold."""
        if degree <= self.strong_threshold:
            return FVGQuality.STRONG
        elif degree <= self.weak_threshold:
            return FVGQuality.MODERATE
        else:
            return FVGQuality.WEAK

    def _measure_reaction(
        self,
        closes: np.ndarray,
        bar_idx: int,
        fvg_type: FVGType,
        lookahead: int = 5,
    ) -> float:
        """Measure price reaction after FVG (for backtest validation)."""
        end = min(bar_idx + lookahead + 1, len(closes))
        if end <= bar_idx + 1:
            return 0.0

        future_prices = closes[bar_idx + 1:end]
        entry = closes[bar_idx]

        if fvg_type == FVGType.BULLISH:
            max_move = max(future_prices) - entry
        else:
            max_move = entry - min(future_prices)

        return abs(max_move) / abs(entry) * 100 if entry != 0 else 0.0

    def _compute_confidence(
        self,
        degree: float,
        vol_ratio: float,
        atr_norm: float,
        quality: FVGQuality,
    ) -> float:
        """Compute composite confidence score (0-1)."""
        deg_score = 1.0 - min(degree / 0.001, 1.0)
        vol_score = min(vol_ratio / 2.0, 1.0)
        atr_score = min(atr_norm / 3.0, 1.0)

        weights = {
            FVGQuality.STRONG: (0.5, 0.3, 0.2),
            FVGQuality.MODERATE: (0.4, 0.35, 0.25),
            FVGQuality.WEAK: (0.3, 0.4, 0.3),
        }
        w = weights[quality]
        return w[0] * deg_score + w[1] * vol_score + w[2] * atr_score

    def _empty_result(self) -> FVGResult:
        return FVGResult(
            signals=[], total_fvgs=0, strong_count=0, moderate_count=0,
            weak_count=0, avg_degree=0.0, coverage_pct=0.0,
        )
