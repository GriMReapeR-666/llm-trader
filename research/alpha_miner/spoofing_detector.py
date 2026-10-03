"""Spoofing Detection Module.

Implements the framework from Kc (2025):
  - Order Imbalance Ratio (OIR) = (B - A) / (B + A)
  - Spoofing score: OIR spikes followed by reversals
  - Duration analysis: short-lived large orders
  - Price reversal post-cancellation detection

References:
  - Kc, B. (2025). A Practical Framework for Retail and Quantitative
    Traders to Analyze Spoofing in Limit Order Books.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class SpoofingBias(Enum):
    BULLISH_SPOOF = "bullish_spoof"  # spoof sell wall -> price reversal up
    BEARISH_SPOOF = "bearish_spoof"  # spoof buy wall -> price reversal down
    NONE = "none"


@dataclass
class SpoofingEvent:
    """Detected spoofing event."""
    bar_index: int
    bias: SpoofingBias
    oir_spike: float
    oir_reversal: float
    duration_bars: int
    order_size_ratio: float
    price_reversal_pct: float
    spoof_score: float
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bar_index": self.bar_index,
            "bias": self.bias.value,
            "oir_spike": round(self.oir_spike, 4),
            "oir_reversal": round(self.oir_reversal, 4),
            "duration_bars": self.duration_bars,
            "order_size_ratio": round(self.order_size_ratio, 4),
            "price_reversal_pct": round(self.price_reversal_pct, 4),
            "spoof_score": round(self.spoof_score, 4),
            "confidence": round(self.confidence, 4),
        }


@dataclass
class SpoofingResult:
    """Full spoofing analysis result."""
    events: List[SpoofingEvent]
    total_events: int
    bullish_spoofs: int
    bearish_spoofs: int
    avg_spoof_score: float
    current_bias: SpoofingBias
    oir_mean: float
    oir_std: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_events": self.total_events,
            "bullish_spoofs": self.bullish_spoofs,
            "bearish_spoofs": self.bearish_spoofs,
            "avg_spoof_score": round(self.avg_spoof_score, 4),
            "current_bias": self.current_bias.value,
            "oir_mean": round(self.oir_mean, 4),
            "oir_std": round(self.oir_std, 4),
            "events": [e.to_dict() for e in self.events[-10:]],
        }


class SpoofingDetector:
    """Spoofing detector using OIR analysis.

    Detects spoofing patterns by monitoring Order Imbalance Ratio
    spikes followed by reversals, combined with price direction changes.
    """

    def __init__(
        self,
        oir_lookback: int = 5,
        spike_threshold: float = 0.5,
        reversal_threshold: float = 0.3,
        min_spoof_bars: int = 2,
        max_events: int = 30,
    ):
        self.oir_lookback = oir_lookback
        self.spike_threshold = spike_threshold
        self.reversal_threshold = reversal_threshold
        self.min_spoof_bars = min_spoof_bars
        self.max_events = max_events

    def detect(
        self,
        ohlcv: pd.DataFrame,
        bid_volume: Optional[pd.Series] = None,
        ask_volume: Optional[pd.Series] = None,
    ) -> SpoofingResult:
        """Detect spoofing events.

        Args:
            ohlcv: DataFrame with columns [open, high, low, close, volume]
            bid_volume: Optional bid-side volume
            ask_volume: Optional ask-side volume

        Returns:
            SpoofingResult with detected events
        """
        if len(ohlcv) < self.oir_lookback + 3:
            return self._empty_result()

        closes = ohlcv["close"].values

        if bid_volume is not None and ask_volume is not None:
            oir = self._compute_oir(bid_volume, ask_volume)
        else:
            oir = self._estimate_oir(ohlcv)

        oir_mean = float(np.mean(oir))
        oir_std = float(np.std(oir)) if len(oir) > 1 else 0.01

        events: List[SpoofingEvent] = []
        i = self.oir_lookback

        while i < len(ohlcv):
            if abs(oir[i]) > self.spike_threshold:
                oir_at_spike = oir[i]
                duration = 1
                j = i + 1

                while j < len(ohlcv) and j - i < 10:
                    if abs(oir[j]) < abs(oir_at_spike) * 0.5:
                        break
                    duration += 1
                    j += 1

                if duration >= self.min_spoof_bars:
                    oir_after = oir[min(j, len(ohlcv) - 1)]
                    oir_reversal = oir_after - oir_at_spike

                    if abs(oir_reversal) > self.reversal_threshold:
                        price_rev = self._compute_price_reversal(
                            closes, i, min(j, len(closes) - 1)
                        )

                        if oir_at_spike < -self.spike_threshold:
                            bias = SpoofingBias.BULLISH_SPOOF
                        elif oir_at_spike > self.spike_threshold:
                            bias = SpoofingBias.BEARISH_SPOOF
                        else:
                            bias = SpoofingBias.NONE

                        if bias != SpoofingBias.NONE:
                            order_ratio = abs(oir_at_spike)
                            spoof_score = self._compute_spoof_score(
                                order_ratio, abs(oir_reversal), price_rev, duration
                            )
                            conf = self._compute_confidence(spoof_score, duration)

                            events.append(SpoofingEvent(
                                bar_index=i,
                                bias=bias,
                                oir_spike=oir_at_spike,
                                oir_reversal=oir_reversal,
                                duration_bars=duration,
                                order_size_ratio=order_ratio,
                                price_reversal_pct=price_rev,
                                spoof_score=spoof_score,
                                confidence=conf,
                            ))
                    i = j
                else:
                    i += 1
            else:
                i += 1

        events = events[-self.max_events:]

        bull = sum(1 for e in events if e.bias == SpoofingBias.BULLISH_SPOOF)
        bear = sum(1 for e in events if e.bias == SpoofingBias.BEARISH_SPOOF)
        current = events[-1].bias if events else SpoofingBias.NONE

        return SpoofingResult(
            events=events,
            total_events=len(events),
            bullish_spoofs=bull,
            bearish_spoofs=bear,
            avg_spoof_score=float(np.mean([e.spoof_score for e in events])) if events else 0.0,
            current_bias=current,
            oir_mean=oir_mean,
            oir_std=oir_std,
        )

    def _compute_oir(
        self, bid_vol: pd.Series, ask_vol: pd.Series
    ) -> np.ndarray:
        """Compute Order Imbalance Ratio."""
        b = bid_vol.values.astype(float)
        a = ask_vol.values.astype(float)
        total = b + a
        total = np.where(total == 0, 1, total)
        return (b - a) / total

    def _estimate_oir(self, ohlcv: pd.DataFrame) -> np.ndarray:
        """Estimate OIR from OHLCV data."""
        closes = ohlcv["close"].values
        volumes = ohlcv["volume"].values
        delta = np.diff(closes, prepend=closes[0])

        buy_vol = np.where(delta > 0, volumes * 0.6, volumes * 0.4)
        sell_vol = np.where(delta > 0, volumes * 0.4, volumes * 0.6)
        total = buy_vol + sell_vol
        total = np.where(total == 0, 1, total)
        return (buy_vol - sell_vol) / total

    def _compute_price_reversal(
        self, closes: np.ndarray, start: int, end: int
    ) -> float:
        """Compute price reversal magnitude after spoofing event."""
        if start >= len(closes) or end >= len(closes):
            return 0.0
        entry = closes[start]
        exit_price = closes[end]
        if entry == 0:
            return 0.0
        return abs(exit_price - entry) / abs(entry) * 100

    def _compute_spoof_score(
        self, oir_ratio: float, oir_reversal: float,
        price_rev: float, duration: int
    ) -> float:
        """Compute spoofing score (0-1)."""
        oir_score = min(oir_ratio, 1.0)
        rev_score = min(oir_reversal / 1.0, 1.0)
        price_score = min(price_rev / 2.0, 1.0)
        dur_score = 1.0 - min(duration / 10, 0.8)
        return 0.3 * oir_score + 0.3 * rev_score + 0.2 * price_score + 0.2 * dur_score

    def _compute_confidence(self, spoof_score: float, duration: int) -> float:
        """Compute event confidence."""
        dur_bonus = min(duration / 5, 0.3)
        return min(spoof_score + dur_bonus, 1.0)

    def _empty_result(self) -> SpoofingResult:
        return SpoofingResult(
            events=[], total_events=0, bullish_spoofs=0,
            bearish_spoofs=0, avg_spoof_score=0.0,
            current_bias=SpoofingBias.NONE, oir_mean=0.0, oir_std=0.0,
        )
