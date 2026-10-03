"""Regime Detection Module.

Implements self-learning regime classification from the MNTO paper
(Trend: Catch me if you can!). Classifies market regimes into:
  - trending_up: positive drift, low mean reversion
  - trending_down: negative drift, low mean reversion
  - ranging: low trend strength, high mean reversion
  - volatile: high volatility, regime uncertain

The regime signal adapts stop distances, position sizing, and alpha
weighting across the pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"


@dataclass
class RegimeState:
    """Current regime classification."""
    regime: MarketRegime
    confidence: float  # 0-1
    trend_strength: float  # -1 to 1
    mean_reversion_score: float  # 0-1
    volatility_percentile: float  # 0-1
    duration: int  # bars in current regime
    transition_prob: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 4),
            "trend_strength": round(self.trend_strength, 4),
            "mean_reversion_score": round(self.mean_reversion_score, 4),
            "volatility_percentile": round(self.volatility_percentile, 4),
            "duration": self.duration,
            "transition_prob": {k: round(v, 4) for k, v in self.transition_prob.items()},
        }


@dataclass
class RegimeHistory:
    """Historical regime sequence."""
    regimes: List[MarketRegime]
    timestamps: List[pd.Timestamp]
    transition_matrix: Optional[pd.DataFrame] = None


class RegimeDetector:
    """Self-learning regime detector.

    Uses a combination of:
    - ADX (trend strength)
    - Hurst exponent (mean reversion vs trending)
    - Rolling volatility regime
    - Momentum consistency
    """

    def __init__(
        self,
        lookback: int = 60,
        vol_lookback: int = 20,
        adx_period: int = 14,
        hurst_window: int = 100,
        min_regime_bars: int = 5,
    ):
        self.lookback = lookback
        self.vol_lookback = vol_lookback
        self.adx_period = adx_period
        self.hurst_window = hurst_window
        self.min_regime_bars = min_regime_bars
        self._regime_history: List[MarketRegime] = []
        self._transition_counts: Dict[Tuple[str, str], int] = {}
        self._regime_durations: List[int] = []

    def detect(self, ohlcv: pd.DataFrame, idx: Optional[int] = None) -> RegimeState:
        """Detect current regime from OHLCV data."""
        if len(ohlcv) < self.lookback:
            return self._default_state()

        if idx is None:
            idx = len(ohlcv) - 1

        start = max(0, idx - self.lookback + 1)
        window = ohlcv.iloc[start:idx + 1].copy()

        close = window["close"].values
        high = window["high"].values if "high" in window.columns else close
        low = window["low"].values if "low" in window.columns else close

        # Trend strength via ADX proxy
        trend_strength = self._compute_trend_strength(close, high, low)

        # Mean reversion score via Hurst exponent
        hurst = self._compute_hurst(close[-min(len(close), self.hurst_window):])
        mean_reversion_score = 1.0 - 2.0 * abs(hurst - 0.5)  # 0 at H=0.5, 1 at H=0 or 1

        # Volatility regime
        vol_pct = self._compute_volatility_percentile(close)

        # Momentum consistency
        momentum_consistency = self._compute_momentum_consistency(close)

        # Classify regime
        regime, confidence = self._classify(
            trend_strength, mean_reversion_score, vol_pct, momentum_consistency
        )

        # Duration tracking
        if self._regime_history and self._regime_history[-1] == regime:
            duration = len(self._regime_history) - self._regime_history[::-1].index(regime)
        else:
            duration = 1

        # Transition probabilities
        transition_prob = self._estimate_transitions(regime)

        state = RegimeState(
            regime=regime,
            confidence=confidence,
            trend_strength=trend_strength,
            mean_reversion_score=mean_reversion_score,
            volatility_percentile=vol_pct,
            duration=duration,
            transition_prob=transition_prob,
        )

        # Track history
        self._regime_history.append(regime)
        if len(self._regime_history) > 500:
            self._regime_history = self._regime_history[-500:]

        return state

    def detect_series(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Detect regime for entire OHLCV series."""
        regimes = []
        for i in range(len(ohlcv)):
            if i < self.lookback:
                regimes.append(MarketRegime.RANGING.value)
            else:
                state = self.detect(ohlcv, idx=i)
                regimes.append(state.regime.value)
        return pd.Series(regimes, index=ohlcv.index)

    def get_transition_matrix(self) -> pd.DataFrame:
        """Get empirical regime transition probabilities."""
        if not self._regime_history or len(self._regime_history) < 2:
            regimes = [r.value for r in MarketRegime]
            return pd.DataFrame(
                np.eye(len(regimes)) * 0.25,
                index=regimes, columns=regimes,
            )

        # Count transitions
        transitions: Dict[Tuple[str, str], int] = {}
        for i in range(1, len(self._regime_history)):
            prev = self._regime_history[i - 1].value
            curr = self._regime_history[i].value
            key = (prev, curr)
            transitions[key] = transitions.get(key, 0) + 1

        # Normalize
        regimes = [r.value for r in MarketRegime]
        matrix = pd.DataFrame(0.0, index=regimes, columns=regimes)
        for (prev, curr), count in transitions.items():
            matrix.loc[prev, curr] = count

        # Normalize rows
        row_sums = matrix.sum(axis=1)
        for r in regimes:
            if row_sums[r] > 0:
                matrix.loc[r] = matrix.loc[r] / row_sums[r]
            else:
                matrix.loc[r] = 0.25

        return matrix

    def _compute_trend_strength(self, close: np.ndarray, high: np.ndarray, low: np.ndarray) -> float:
        """Compute trend strength via ADX proxy (-1 to 1)."""
        if len(close) < self.adx_period + 1:
            return 0.0

        # Directional movement
        up_move = np.diff(close)
        down_move = -np.diff(close)

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

        # True range
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )

        if len(tr) < self.adx_period:
            return 0.0

        # Smoothed averages
        atr = pd.Series(tr).rolling(self.adx_period).mean().iloc[-1]
        if atr == 0 or np.isnan(atr):
            return 0.0

        plus_di = pd.Series(plus_dm).rolling(self.adx_period).mean().iloc[-1] / atr
        minus_di = pd.Series(minus_dm).rolling(self.adx_period).mean().iloc[-1] / atr

        # Trend strength: +DI - -DI, normalized
        dx = (plus_di - minus_di) / max(plus_di + minus_di, 1e-10)
        return float(np.clip(dx, -1, 1))

    def _compute_hurst(self, prices: np.ndarray) -> float:
        """Compute Hurst exponent via R/S method."""
        n = len(prices)
        if n < 20:
            return 0.5

        # Rescaled range
        lags = range(2, min(n // 2, 50))
        rs_values = []

        for lag in lags:
            chunks = [prices[i:i + lag] for i in range(0, n - lag, lag)]
            if not chunks:
                continue

            rs_chunk = []
            for chunk in chunks:
                if len(chunk) < 2:
                    continue
                mean_c = np.mean(chunk)
                deviations = np.cumsum(chunk - mean_c)
                R = np.max(deviations) - np.min(deviations)
                S = np.std(chunk, ddof=1)
                if S > 0:
                    rs_chunk.append(R / S)

            if rs_chunk:
                rs_values.append((np.log(lag), np.log(np.mean(rs_chunk))))

        if len(rs_values) < 3:
            return 0.5

        x = np.array([v[0] for v in rs_values])
        y = np.array([v[1] for v in rs_values])

        # Linear regression for Hurst
        slope, _ = np.polyfit(x, y, 1)
        return float(np.clip(slope, 0, 1))

    def _compute_volatility_percentile(self, close: np.ndarray) -> float:
        """Compute current volatility percentile (0-1)."""
        if len(close) < self.vol_lookback * 2:
            return 0.5

        returns = np.diff(np.log(close))
        current_vol = np.std(returns[-self.vol_lookback:])

        # Rolling volatility history
        vols = []
        for i in range(self.vol_lookback, len(returns)):
            v = np.std(returns[i - self.vol_lookback:i])
            vols.append(v)

        if not vols:
            return 0.5

        vols = np.array(vols)
        rank = np.sum(vols < current_vol) / len(vols)
        return float(rank)

    def _compute_momentum_consistency(self, close: np.ndarray) -> float:
        """Compute momentum consistency (0-1)."""
        if len(close) < 20:
            return 0.5

        returns = np.diff(close[-20:])
        positive_returns = np.sum(returns > 0) / len(returns)
        return positive_returns

    def _classify(
        self,
        trend_strength: float,
        mean_reversion_score: float,
        vol_pct: float,
        momentum_consistency: float,
    ) -> Tuple[MarketRegime, float]:
        """Classify regime from indicators."""
        abs_trend = abs(trend_strength)

        # High volatility regime
        if vol_pct > 0.8:
            if abs_trend > 0.3:
                regime = MarketRegime.TRENDING_UP if trend_strength > 0 else MarketRegime.TRENDING_DOWN
                confidence = min(0.5 + vol_pct * 0.3 + abs_trend * 0.2, 0.95)
            else:
                regime = MarketRegime.VOLATILE
                confidence = min(0.4 + vol_pct * 0.4, 0.9)
        # Strong trend
        elif abs_trend > 0.4 and mean_reversion_score < 0.3:
            regime = MarketRegime.TRENDING_UP if trend_strength > 0 else MarketRegime.TRENDING_DOWN
            confidence = min(0.5 + abs_trend * 0.3 + (1 - mean_reversion_score) * 0.2, 0.95)
        # Strong mean reversion
        elif mean_reversion_score > 0.6 and abs_trend < 0.3:
            regime = MarketRegime.RANGING
            confidence = min(0.4 + mean_reversion_score * 0.3 + (1 - abs_trend) * 0.2, 0.9)
        # Default to ranging
        else:
            regime = MarketRegime.RANGING
            confidence = min(0.3 + (1 - abs_trend) * 0.2 + mean_reversion_score * 0.1, 0.8)

        # Momentum consistency adjustment
        if momentum_consistency > 0.7 and abs_trend > 0.2:
            confidence = min(confidence + 0.05, 0.95)
        elif momentum_consistency < 0.3 and abs_trend > 0.2:
            confidence = max(confidence - 0.05, 0.2)

        return regime, confidence

    def _estimate_transitions(self, current: MarketRegime) -> Dict[str, float]:
        """Estimate transition probabilities from history."""
        if len(self._regime_history) < 10:
            return {r.value: 0.25 for r in MarketRegime}

        matrix = self.get_transition_matrix()
        return matrix.loc[current.value].to_dict()

    def _default_state(self) -> RegimeState:
        """Default regime state when insufficient data."""
        return RegimeState(
            regime=MarketRegime.RANGING,
            confidence=0.3,
            trend_strength=0.0,
            mean_reversion_score=0.5,
            volatility_percentile=0.5,
            duration=1,
        )
