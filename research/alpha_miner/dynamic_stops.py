"""Dynamic Stop Loss Module.

Implements regime-adaptive stop loss and trailing stop from the MNTO paper.
Stops widen in high-volatility regimes and tighten in low-volatility regimes.

Key concepts from the paper:
  - Dynamic stop loss: adjusts based on current volatility
  - Trailing stop loss: follows price up, never moves down
  - Regime-adaptive: wider stops in volatile markets
  - ATR-based: uses Average True Range for stop distance

References:
  - MNTO Paper (Mbimi, 2011): Section 4.3 Exit (stops)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from .regime_detector import MarketRegime

logger = logging.getLogger(__name__)


class StopType(Enum):
    FIXED = "fixed"
    ATR = "atr"
    TRAILING = "trailing"
    REGIME_ADAPTIVE = "regime_adaptive"


@dataclass
class StopState:
    """Current state of a stop loss."""
    stop_price: float
    stop_type: StopType
    entry_price: float
    current_price: float
    highest_since_entry: float
    stop_distance: float  # absolute
    stop_distance_pct: float  # percentage
    regime: MarketRegime
    is_triggered: bool
    unrealized_pnl_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stop_price": round(self.stop_price, 5),
            "stop_type": self.stop_type.value,
            "entry_price": round(self.entry_price, 5),
            "current_price": round(self.current_price, 5),
            "highest_since_entry": round(self.highest_since_entry, 5),
            "stop_distance": round(self.stop_distance, 5),
            "stop_distance_pct": round(self.stop_distance_pct, 4),
            "regime": self.regime.value,
            "is_triggered": self.is_triggered,
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct, 4),
        }


class DynamicStopManager:
    """Manage dynamic stop losses with regime adaptation.

    Stop distances adapt to:
    - Current volatility regime
    - Asset's ATR (Average True Range)
    - Time since entry (tightening over time)
    """

    # Regime-based ATR multipliers
    REGIME_MULTIPLIERS = {
        MarketRegime.TRENDING_UP: 2.5,
        MarketRegime.TRENDING_DOWN: 2.0,
        MarketRegime.RANGING: 1.5,
        MarketRegime.VOLATILE: 3.0,
    }

    # Minimum stop distances (percentage)
    MIN_STOP_PCT = {
        MarketRegime.TRENDING_UP: 0.01,
        MarketRegime.TRENDING_DOWN: 0.015,
        MarketRegime.RANGING: 0.008,
        MarketRegime.VOLATILE: 0.02,
    }

    def __init__(
        self,
        default_atr_period: int = 14,
        default_multiplier: float = 2.0,
        trailing_activation_pct: float = 0.02,
        trailing_step_pct: float = 0.005,
        max_stop_pct: float = 0.10,
    ):
        self.default_atr_period = default_atr_period
        self.default_multiplier = default_multiplier
        self.trailing_activation_pct = trailing_activation_pct
        self.trailing_step_pct = trailing_step_pct
        self.max_stop_pct = max_stop_pct

    def compute_atr(
        self, ohlcv: pd.DataFrame, period: Optional[int] = None
    ) -> float:
        """Compute Average True Range."""
        p = period or self.default_atr_period
        if len(ohlcv) < p + 1:
            return 0.0

        high = ohlcv["high"].values
        low = ohlcv["low"].values
        close = ohlcv["close"].values

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )

        atr = pd.Series(tr).rolling(p).mean().iloc[-1]
        return float(atr) if not np.isnan(atr) else 0.0

    def compute_stop_price(
        self,
        entry_price: float,
        current_price: float,
        ohlcv: pd.DataFrame,
        regime: MarketRegime = MarketRegime.RANGING,
        stop_type: StopType = StopType.REGIME_ADAPTIVE,
        highest_since_entry: Optional[float] = None,
    ) -> StopState:
        """Compute current stop price.

        Args:
            entry_price: Position entry price
            current_price: Current market price
            ohlcv: Recent OHLCV data
            regime: Current market regime
            stop_type: Type of stop to use
            highest_since_entry: Highest price since entry (for trailing)

        Returns:
            StopState with current stop information
        """
        atr = self.compute_atr(ohlcv)
        high = highest_since_entry or current_price

        if stop_type == StopType.FIXED:
            stop_price, stop_dist = self._fixed_stop(entry_price, regime)
        elif stop_type == StopType.ATR:
            stop_price, stop_dist = self._atr_stop(entry_price, atr)
        elif stop_type == StopType.TRAILING:
            stop_price, stop_dist = self._trailing_stop(
                entry_price, current_price, high, atr, regime
            )
        else:  # REGIME_ADAPTIVE
            stop_price, stop_dist = self._regime_adaptive_stop(
                entry_price, current_price, atr, regime
            )

        # Apply maximum stop
        max_stop = entry_price * self.max_stop_pct
        if stop_dist > max_stop:
            stop_price = entry_price - max_stop
            stop_dist = max_stop

        # Check if triggered
        is_triggered = current_price <= stop_price
        stop_distance_pct = stop_dist / entry_price if entry_price > 0 else 0
        unrealized_pnl = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        return StopState(
            stop_price=stop_price,
            stop_type=stop_type,
            entry_price=entry_price,
            current_price=current_price,
            highest_since_entry=high,
            stop_distance=stop_dist,
            stop_distance_pct=stop_distance_pct,
            regime=regime,
            is_triggered=is_triggered,
            unrealized_pnl_pct=unrealized_pnl,
        )

    def update_trailing_stop(
        self,
        current_stop: StopState,
        current_price: float,
        ohlcv: pd.DataFrame,
    ) -> StopState:
        """Update trailing stop as price moves in our favor."""
        if current_price > current_stop.highest_since_entry:
            new_highest = current_price
        else:
            new_highest = current_stop.highest_since_entry

        return self.compute_stop_price(
            entry_price=current_stop.entry_price,
            current_price=current_price,
            ohlcv=ohlcv,
            regime=current_stop.regime,
            stop_type=current_stop.stop_type,
            highest_since_entry=new_highest,
        )

    def compute_optimal_stop_distance(
        self,
        atr: float,
        regime: MarketRegime,
        confidence: float = 0.5,
    ) -> float:
        """Compute optimal stop distance in price terms.

        Uses regime and confidence to adjust the multiplier.
        """
        base_multiplier = self.REGIME_MULTIPLIERS.get(regime, self.default_multiplier)

        # Confidence adjustment: higher confidence = tighter stop
        confidence_adj = 1.0 - 0.3 * confidence
        multiplier = base_multiplier * confidence_adj

        return atr * multiplier

    def regime_stop_profile(self, regime: MarketRegime) -> Dict[str, Any]:
        """Get stop parameters for a given regime."""
        return {
            "regime": regime.value,
            "atr_multiplier": self.REGIME_MULTIPLIERS.get(regime, self.default_multiplier),
            "min_stop_pct": self.MIN_STOP_PCT.get(regime, 0.01),
            "max_stop_pct": self.max_stop_pct,
        }

    def compute_stop_series(
        self,
        ohlcv: pd.DataFrame,
        entries: pd.Series,
        regime_series: pd.Series,
        stop_type: StopType = StopType.REGIME_ADAPTIVE,
    ) -> pd.DataFrame:
        """Compute stop prices for a series of entries.

        Args:
            ohlcv: Full OHLCV DataFrame
            entries: Series of (entry_price, entry_idx) tuples
            regime_series: Series of regime values
            stop_type: Type of stop to use

        Returns:
            DataFrame with stop prices and states
        """
        results = []
        highest = 0.0

        for idx in range(len(ohlcv)):
            price = ohlcv["close"].iloc[idx]

            if idx in entries.index:
                entry_price, entry_idx = entries[idx]
                highest = entry_price
            else:
                highest = max(highest, price) if highest > 0 else price

            regime_str = regime_series.iloc[idx] if idx < len(regime_series) else "ranging"
            try:
                regime = MarketRegime(regime_str)
            except ValueError:
                regime = MarketRegime.RANGING

            window_start = max(0, idx - 30)
            window = ohlcv.iloc[window_start:idx + 1]

            state = self.compute_stop_price(
                entry_price=entries.get(idx, (price, idx))[0] if idx in entries.index else price,
                current_price=price,
                ohlcv=window,
                regime=regime,
                stop_type=stop_type,
                highest_since_entry=highest,
            )

            results.append({
                "date": ohlcv.index[idx],
                "stop_price": state.stop_price,
                "stop_distance_pct": state.stop_distance_pct,
                "is_triggered": state.is_triggered,
                "regime": regime.value,
            })

        return pd.DataFrame(results).set_index("date")

    def _fixed_stop(
        self, entry_price: float, regime: MarketRegime
    ) -> tuple[float, float]:
        """Fixed percentage stop."""
        min_pct = self.MIN_STOP_PCT.get(regime, 0.01)
        stop_dist = entry_price * min_pct
        return entry_price - stop_dist, stop_dist

    def _atr_stop(self, entry_price: float, atr: float) -> tuple[float, float]:
        """ATR-based stop."""
        stop_dist = atr * self.default_multiplier
        return entry_price - stop_dist, stop_dist

    def _trailing_stop(
        self,
        entry_price: float,
        current_price: float,
        highest: float,
        atr: float,
        regime: MarketRegime,
    ) -> tuple[float, float]:
        """Trailing stop that follows price up."""
        # Activate trailing after reaching activation threshold
        if (highest - entry_price) / entry_price < self.trailing_activation_pct:
            # Use initial stop
            return self._regime_adaptive_stop(entry_price, current_price, atr, regime)

        # Trailing: stop = highest - multiplier * atr
        multiplier = self.REGIME_MULTIPLIERS.get(regime, self.default_multiplier)
        stop_dist = atr * multiplier
        stop_price = highest - stop_dist

        # Never move stop down
        return stop_price, highest - stop_price

    def _regime_adaptive_stop(
        self,
        entry_price: float,
        current_price: float,
        atr: float,
        regime: MarketRegime,
    ) -> tuple[float, float]:
        """Regime-adaptive stop."""
        multiplier = self.REGIME_MULTIPLIERS.get(regime, self.default_multiplier)
        min_pct = self.MIN_STOP_PCT.get(regime, 0.01)

        stop_dist_atr = atr * multiplier
        stop_dist_pct = entry_price * min_pct

        # Use the larger of ATR-based or minimum percentage
        stop_dist = max(stop_dist_atr, stop_dist_pct)

        return entry_price - stop_dist, stop_dist
