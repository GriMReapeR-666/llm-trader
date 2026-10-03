"""Volume Breakout Detection Module.

Implements the 4x volume spike breakout algorithm from the
volume spike breakout paper (2025):
  - Detects 4x average volume spikes
  - Confirms breakout with price exceeding previous high
  - 5-day lookback window for spike confirmation
  - Generates entry signals with confidence scoring

References:
  - Research Paper on Algorithmic Breakout Detection via Volume Spike
    in Options Trading (2025).
  - Lo & Wang (2000): Trading Volume.
  - Chen, Noronha, Singal (2012): Information Content of Trading Volume.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class BreakoutDirection(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


@dataclass
class BreakoutSignal:
    """Detected breakout signal."""
    bar_index: int
    direction: BreakoutDirection
    volume_ratio: float
    price_breakout_pct: float
    volume_lookback: int
    spike_count: int
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bar_index": self.bar_index,
            "direction": self.direction.value,
            "volume_ratio": round(self.volume_ratio, 4),
            "price_breakout_pct": round(self.price_breakout_pct, 4),
            "volume_lookback": self.volume_lookback,
            "spike_count": self.spike_count,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class BreakoutResult:
    """Full breakout analysis result."""
    signals: List[BreakoutSignal]
    total_breakouts: int
    bullish_count: int
    bearish_count: int
    avg_volume_ratio: float
    avg_confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_breakouts": self.total_breakouts,
            "bullish_count": self.bullish_count,
            "bearish_count": self.bearish_count,
            "avg_volume_ratio": round(self.avg_volume_ratio, 4),
            "avg_confidence": round(self.avg_confidence, 4),
            "signals": [s.to_dict() for s in self.signals[-10:]],
        }


class VolumeBreakoutDetector:
    """4x Volume Spike Breakout Detector.

    Identifies breakouts confirmed by abnormal volume spikes.
    """

    def __init__(
        self,
        volume_avg_period: int = 20,
        spike_multiplier: float = 4.0,
        lookback_days: int = 5,
        min_breakout_pct: float = 0.001,
        max_signals: int = 50,
    ):
        self.volume_avg_period = volume_avg_period
        self.spike_multiplier = spike_multiplier
        self.lookback_days = lookback_days
        self.min_breakout_pct = min_breakout_pct
        self.max_signals = max_signals

    def detect(self, ohlcv: pd.DataFrame) -> BreakoutResult:
        """Detect volume breakout signals.

        Args:
            ohlcv: DataFrame with columns [open, high, low, close, volume]

        Returns:
            BreakoutResult with detected signals
        """
        if len(ohlcv) < self.volume_avg_period + self.lookback_days + 1:
            return self._empty_result()

        highs = ohlcv["high"].values
        lows = ohlcv["low"].values
        closes = ohlcv["close"].values
        volumes = ohlcv["volume"].values

        vol_ma = pd.Series(volumes).rolling(self.volume_avg_period).mean().values

        signals: List[BreakoutSignal] = []

        for i in range(self.volume_avg_period + self.lookback_days, len(ohlcv)):
            if vol_ma[i] <= 0:
                continue

            vol_ratio = volumes[i] / vol_ma[i]

            if vol_ratio < self.spike_multiplier:
                continue

            lookback_high = max(highs[i - self.lookback_days:i])
            lookback_low = min(lows[i - self.lookback_days:i])

            is_bullish_breakout = highs[i] > lookback_high
            is_bearish_breakout = lows[i] < lookback_low

            if is_bullish_breakout:
                direction = BreakoutDirection.BULLISH
                breakout_pct = (highs[i] - lookback_high) / lookback_high * 100 if lookback_high > 0 else 0
            elif is_bearish_breakout:
                direction = BreakoutDirection.BEARISH
                breakout_pct = (lookback_low - lows[i]) / lookback_low * 100 if lookback_low > 0 else 0
            else:
                continue

            if abs(breakout_pct) < self.min_breakout_pct * 100:
                continue

            spike_count = 0
            for j in range(max(0, i - self.lookback_days), i + 1):
                if vol_ma[j] > 0 and volumes[j] >= self.spike_multiplier * vol_ma[j]:
                    spike_count += 1

            confidence = self._compute_confidence(vol_ratio, breakout_pct, spike_count)

            signals.append(BreakoutSignal(
                bar_index=i,
                direction=direction,
                volume_ratio=vol_ratio,
                price_breakout_pct=breakout_pct,
                volume_lookback=self.lookback_days,
                spike_count=spike_count,
                confidence=confidence,
            ))

        signals = signals[-self.max_signals:]

        bull = sum(1 for s in signals if s.direction == BreakoutDirection.BULLISH)
        bear = sum(1 for s in signals if s.direction == BreakoutDirection.BEARISH)

        return BreakoutResult(
            signals=signals,
            total_breakouts=len(signals),
            bullish_count=bull,
            bearish_count=bear,
            avg_volume_ratio=float(np.mean([s.volume_ratio for s in signals])) if signals else 0.0,
            avg_confidence=float(np.mean([s.confidence for s in signals])) if signals else 0.0,
        )

    def _compute_confidence(
        self, vol_ratio: float, breakout_pct: float, spike_count: int
    ) -> float:
        """Compute signal confidence (0-1)."""
        vol_score = min(vol_ratio / (self.spike_multiplier * 2), 1.0)
        break_score = min(abs(breakout_pct) / 5.0, 1.0)
        spike_score = min(spike_count / 3, 1.0)
        return 0.5 * vol_score + 0.3 * break_score + 0.2 * spike_score

    def _empty_result(self) -> BreakoutResult:
        return BreakoutResult(
            signals=[], total_breakouts=0, bullish_count=0,
            bearish_count=0, avg_volume_ratio=0.0, avg_confidence=0.0,
        )
