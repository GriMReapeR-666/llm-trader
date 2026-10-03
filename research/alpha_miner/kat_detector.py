"""Asymmetric Absorption Threshold (KAT) Detection Module.

Implements the KAT metric from Khobragade (2026):
  - Detects "pinning traps" where retail CVD diverges from price
  - KAT triggers when: CVD slope > 0, price velocity ~0, OBI skew < -0.85
  - Identifies institutional absorption events
  - Provides signal strength and directional bias

References:
  - Khobragade, A.A. (2026). The Asymmetric Absorption Threshold (KAT).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class KATSignal(Enum):
    ABSORPTION_BULL = "absorption_bull"   # institutional wall absorbs retail selling
    ABSORPTION_BEAR = "absorption_bear"   # institutional wall absorbs retail buying
    NONE = "none"


@dataclass
class KATEvent:
    """Detected KAT absorption event."""
    bar_index: int
    signal_type: KATSignal
    cvd_slope: float
    price_velocity: float
    obi_skew: float
    absorption_strength: float
    duration_bars: int
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bar_index": self.bar_index,
            "signal": self.signal_type.value,
            "cvd_slope": round(self.cvd_slope, 6),
            "price_velocity": round(self.price_velocity, 6),
            "obi_skew": round(self.obi_skew, 4),
            "absorption_strength": round(self.absorption_strength, 4),
            "duration_bars": self.duration_bars,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class KATResult:
    """Full KAT analysis result."""
    events: List[KATEvent]
    total_events: int
    bull_absorptions: int
    bear_absorptions: int
    avg_confidence: float
    current_state: KATSignal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_events": self.total_events,
            "bull_absorptions": self.bull_absorptions,
            "bear_absorptions": self.bear_absorptions,
            "avg_confidence": round(self.avg_confidence, 4),
            "current_state": self.current_state.value,
            "events": [e.to_dict() for e in self.events[-10:]],
        }


class KATDetector:
    """Asymmetric Absorption Threshold detector.

    Detects pinning traps where institutional limit walls absorb
    aggressive retail flow without allowing price to move.
    """

    def __init__(
        self,
        cvd_lookback: int = 5,
        price_velocity_threshold: float = 0.0001,
        obi_skew_threshold: float = -0.85,
        min_absorption_bars: int = 3,
        max_events: int = 30,
    ):
        self.cvd_lookback = cvd_lookback
        self.price_velocity_threshold = price_velocity_threshold
        self.obi_skew_threshold = obi_skew_threshold
        self.min_absorption_bars = min_absorption_bars
        self.max_events = max_events

    def detect(
        self,
        ohlcv: pd.DataFrame,
        delta: Optional[pd.Series] = None,
        bid_volume: Optional[pd.Series] = None,
        ask_volume: Optional[pd.Series] = None,
    ) -> KATResult:
        """Detect KAT absorption events.

        Args:
            ohlcv: DataFrame with columns [open, high, low, close, volume]
            delta: Optional tick-level volume delta (buy - sell)
            bid_volume: Optional bid-side volume
            ask_volume: Optional ask-side volume

        Returns:
            KATResult with detected events
        """
        if len(ohlcv) < self.cvd_lookback + 2:
            return self._empty_result()

        closes = ohlcv["close"].values

        if delta is not None:
            cvd = delta.cumsum().values
        else:
            cvd = self._estimate_cvd(ohlcv)

        price_vel = self._compute_price_velocity(closes)

        if bid_volume is not None and ask_volume is not None:
            obi_skew = self._compute_obi_skew(bid_volume, ask_volume)
        else:
            obi_skew = self._estimate_obi_skew(ohlcv)

        events: List[KATEvent] = []
        i = self.cvd_lookback

        while i < len(ohlcv):
            cvd_slope = self._compute_cvd_slope(cvd, i)
            pv = price_vel[i]
            obi = obi_skew[i] if i < len(obi_skew) else 0.0

            is_bear_abs = (
                cvd_slope > 0
                and abs(pv) < self.price_velocity_threshold
                and obi < self.obi_skew_threshold
            )
            is_bull_abs = (
                cvd_slope < 0
                and abs(pv) < self.price_velocity_threshold
                and obi > abs(self.obi_skew_threshold)
            )

            if is_bear_abs or is_bull_abs:
                duration = 1
                j = i + 1
                while j < len(ohlcv):
                    next_slope = self._compute_cvd_slope(cvd, j)
                    next_pv = price_vel[j]
                    next_obi = obi_skew[j] if j < len(obi_skew) else 0.0
                    still_active = (
                        (is_bear_abs and next_slope > 0 and abs(next_pv) < self.price_velocity_threshold and next_obi < self.obi_skew_threshold)
                        or (is_bull_abs and next_slope < 0 and abs(next_pv) < self.price_velocity_threshold and next_obi > abs(self.obi_skew_threshold))
                    )
                    if not still_active:
                        break
                    duration += 1
                    j += 1

                if duration >= self.min_absorption_bars:
                    signal = KATSignal.ABSORPTION_BEAR if is_bear_abs else KATSignal.ABSORPTION_BULL
                    strength = self._compute_absorption_strength(cvd_slope, pv, obi)
                    conf = self._compute_confidence(strength, duration)

                    events.append(KATEvent(
                        bar_index=i,
                        signal_type=signal,
                        cvd_slope=cvd_slope,
                        price_velocity=pv,
                        obi_skew=obi,
                        absorption_strength=strength,
                        duration_bars=duration,
                        confidence=conf,
                    ))
                    i = j
                else:
                    i += 1
            else:
                i += 1

        events = events[-self.max_events:]

        bull = sum(1 for e in events if e.signal_type == KATSignal.ABSORPTION_BULL)
        bear = sum(1 for e in events if e.signal_type == KATSignal.ABSORPTION_BEAR)
        current = events[-1].signal_type if events else KATSignal.NONE

        return KATResult(
            events=events,
            total_events=len(events),
            bull_absorptions=bull,
            bear_absorptions=bear,
            avg_confidence=float(np.mean([e.confidence for e in events])) if events else 0.0,
            current_state=current,
        )

    def _estimate_cvd(self, ohlcv: pd.DataFrame) -> np.ndarray:
        """Estimate cumulative volume delta from OHLCV."""
        closes = ohlcv["close"].values
        volumes = ohlcv["volume"].values
        delta = np.where(
            closes[1:] >= closes[:-1],
            volumes[1:] * 0.6,
            -volumes[1:] * 0.6,
        )
        return np.concatenate([[0], delta]).cumsum()

    def _compute_price_velocity(self, closes: np.ndarray) -> np.ndarray:
        """Compute price velocity (normalized)."""
        vel = np.zeros(len(closes))
        for i in range(1, len(closes)):
            vel[i] = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] != 0 else 0
        return vel

    def _compute_obi_skew(
        self, bid_vol: pd.Series, ask_vol: pd.Series
    ) -> np.ndarray:
        """Compute Order Book Imbalance skew."""
        bid = bid_vol.values.astype(float)
        ask = ask_vol.values.astype(float)
        total = bid + ask
        total = np.where(total == 0, 1, total)
        return (bid - ask) / total

    def _estimate_obi_skew(self, ohlcv: pd.DataFrame) -> np.ndarray:
        """Estimate OBI skew from volume and price."""
        closes = ohlcv["close"].values
        volumes = ohlcv["volume"].values
        delta = np.diff(closes, prepend=closes[0])
        obi = np.where(delta > 0, 0.6, np.where(delta < 0, -0.6, 0.0))
        return obi

    def _compute_cvd_slope(self, cvd: np.ndarray, idx: int) -> float:
        """Compute CVD slope over lookback window."""
        start = max(0, idx - self.cvd_lookback)
        window = cvd[start:idx + 1]
        if len(window) < 2:
            return 0.0
        x = np.arange(len(window), dtype=float)
        slope = np.polyfit(x, window, 1)[0]
        return slope

    def _compute_absorption_strength(
        self, cvd_slope: float, price_vel: float, obi_skew: float
    ) -> float:
        """Compute absorption strength (0-1)."""
        slope_score = min(abs(cvd_slope) / 1000, 1.0)
        vel_score = 1.0 - min(abs(price_vel) / 0.001, 1.0)
        obi_score = min(abs(obi_skew) / 0.95, 1.0)
        return 0.4 * slope_score + 0.2 * vel_score + 0.4 * obi_score

    def _compute_confidence(self, strength: float, duration: int) -> float:
        """Compute event confidence."""
        dur_score = min(duration / 10, 1.0)
        return 0.6 * strength + 0.4 * dur_score

    def _empty_result(self) -> KATResult:
        return KATResult(
            events=[], total_events=0, bull_absorptions=0,
            bear_absorptions=0, avg_confidence=0.0, current_state=KATSignal.NONE,
        )
