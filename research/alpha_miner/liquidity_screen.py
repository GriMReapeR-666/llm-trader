"""Liquidity Screening Module.

Implements liquidity-based stock filtering from the MNTO paper.
The model filters stocks by liquidity before alpha evaluation to
avoid slippage and market impact costs that erode alpha.

Key insight: Low liquidity = high slippage = alpha erosion
  - Filter stocks that are too illiquid to trade profitably
  - Adjust position weights by liquidity score
  - Account for market impact in portfolio construction

References:
  - MNTO Paper (Mbimi, 2011): Section 4.2 Data Integrity, Universe of tradable assets
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class LiquidityScore:
    """Liquidity score for a single asset."""
    symbol: str
    volume_score: float  # 0-1
    spread_score: float  # 0-1
    depth_score: float  # 0-1
    composite_score: float  # 0-1
    is_liquid: bool
    estimated_slippage: float  # in basis points

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "volume_score": round(self.volume_score, 4),
            "spread_score": round(self.spread_score, 4),
            "depth_score": round(self.depth_score, 4),
            "composite_score": round(self.composite_score, 4),
            "is_liquid": self.is_liquid,
            "estimated_slippage": round(self.estimated_slippage, 2),
        }


class LiquidityScreener:
    """Filter stocks by liquidity before alpha evaluation.

    Computes liquidity scores based on:
    - Average daily volume
    - Bid-ask spread (if available)
    - Price impact (Amihud illiquidity)
    - Volume consistency
    """

    def __init__(
        self,
        min_volume: int = 100000,
        min_dollar_volume: float = 1e6,
        max_spread_bps: float = 50.0,
        lookback: int = 20,
        liquidity_threshold: float = 0.5,
    ):
        self.min_volume = min_volume
        self.min_dollar_volume = min_dollar_volume
        self.max_spread_bps = max_spread_bps
        self.lookback = lookback
        self.liquidity_threshold = liquidity_threshold

    def compute_liquidity_score(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        bid_ask_spread: Optional[float] = None,
    ) -> LiquidityScore:
        """Compute liquidity score for a single asset.

        Args:
            symbol: Asset symbol
            ohlcv: OHLCV DataFrame
            bid_ask_spread: Current bid-ask spread (optional)

        Returns:
            LiquidityScore with component scores
        """
        if len(ohlcv) < self.lookback:
            return self._default_score(symbol)

        recent = ohlcv.iloc[-self.lookback:]

        # Volume score
        avg_volume = recent["volume"].mean()
        volume_score = min(avg_volume / self.min_volume, 1.0)

        # Dollar volume score
        avg_price = recent["close"].mean()
        dollar_volume = avg_volume * avg_price
        dollar_score = min(dollar_volume / self.min_dollar_volume, 1.0)

        # Spread score (Amihud proxy)
        spread_score = self._compute_spread_score(recent)

        # Depth score (volume consistency)
        depth_score = self._compute_depth_score(recent["volume"].values)

        # Composite score
        composite = 0.4 * volume_score + 0.3 * dollar_score + 0.2 * spread_score + 0.1 * depth_score

        # Estimate slippage
        estimated_slippage = self._estimate_slippage(avg_volume, dollar_volume)

        is_liquid = composite >= self.liquidity_threshold

        return LiquidityScore(
            symbol=symbol,
            volume_score=volume_score,
            spread_score=spread_score,
            depth_score=depth_score,
            composite_score=composite,
            is_liquid=is_liquid,
            estimated_slippage=estimated_slippage,
        )

    def filter_stocks(
        self,
        ohlcv_dict: Dict[str, pd.DataFrame],
        bid_ask_spreads: Optional[Dict[str, float]] = None,
    ) -> Dict[str, LiquidityScore]:
        """Filter multiple stocks by liquidity.

        Args:
            ohlcv_dict: {symbol: ohlcv DataFrame}
            bid_ask_spreads: {symbol: spread} (optional)

        Returns:
            {symbol: LiquidityScore} for all stocks
        """
        scores = {}
        for symbol, ohlcv in ohlcv_dict.items():
            spread = bid_ask_spreads.get(symbol) if bid_ask_spreads else None
            scores[symbol] = self.compute_liquidity_score(symbol, ohlcv, spread)

        return scores

    def get_liquid_stocks(
        self,
        ohlcv_dict: Dict[str, pd.DataFrame],
        bid_ask_spreads: Optional[Dict[str, float]] = None,
    ) -> List[str]:
        """Return list of liquid stock symbols."""
        scores = self.filter_stocks(ohlcv_dict, bid_ask_spreads)
        return [s for s, score in scores.items() if score.is_liquid]

    def liquidity_adjusted_weights(
        self,
        weights: Dict[str, float],
        liquidity_scores: Dict[str, LiquidityScore],
    ) -> Dict[str, float]:
        """Adjust position weights by liquidity score.

        Reduces weight for less liquid stocks to account for slippage.
        """
        adjusted = {}
        for symbol, weight in weights.items():
            score = liquidity_scores.get(symbol)
            if score is None:
                # No liquidity data, reduce weight
                adjusted[symbol] = weight * 0.5
            elif score.is_liquid:
                # Liquidity discount: less liquid = more discount
                discount = 0.5 + 0.5 * score.composite_score
                adjusted[symbol] = weight * discount
            else:
                # Illiquid, minimal weight
                adjusted[symbol] = weight * 0.1

        # Renormalize
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}

        return adjusted

    def compute_liquidity_panel(
        self,
        ohlcv_dict: Dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """Compute liquidity scores for all stocks as a DataFrame."""
        scores = []
        for symbol, ohlcv in ohlcv_dict.items():
            score = self.compute_liquidity_score(symbol, ohlcv)
            scores.append({
                "symbol": symbol,
                "volume_score": score.volume_score,
                "spread_score": score.spread_score,
                "depth_score": score.depth_score,
                "composite_score": score.composite_score,
                "is_liquid": score.is_liquid,
                "estimated_slippage": score.estimated_slippage,
            })

        return pd.DataFrame(scores).set_index("symbol")

    def _compute_spread_score(self, recent: pd.DataFrame) -> float:
        """Compute spread score using Amihud illiquidity proxy."""
        if len(recent) < 2:
            return 0.5

        returns = recent["close"].pct_change().dropna()
        volume = recent["volume"].iloc[1:]

        # Amihud illiquidity: |return| / dollar_volume
        dollar_volume = volume * recent["close"].iloc[1:]
        dollar_volume = dollar_volume.replace(0, np.nan)

        amihud = (returns.abs() / dollar_volume).dropna()
        if len(amihud) == 0:
            return 0.5

        avg_amihud = amihud.mean()
        # Lower amihud = more liquid
        # Typical values: 1e-8 to 1e-5
        score = 1.0 - min(avg_amihud * 1e6, 1.0)
        return float(max(0, min(score, 1)))

    def _compute_depth_score(self, volume: np.ndarray) -> float:
        """Compute depth score from volume consistency."""
        if len(volume) < 5:
            return 0.5

        # Coefficient of variation (lower = more consistent = better)
        cv = np.std(volume) / max(np.mean(volume), 1)
        score = 1.0 - min(cv, 1.0)
        return float(max(0, min(score, 1)))

    def _estimate_slippage(self, avg_volume: float, dollar_volume: float) -> float:
        """Estimate slippage in basis points.

        Based on empirical market impact models.
        """
        if avg_volume <= 0 or dollar_volume <= 0:
            return 100.0  # Very high slippage

        # Square-root market impact model
        # slippage ~ sigma * sqrt(trade_size / ADV)
        # Simplified: bps = a / sqrt(ADV / 1e6)
        adv_millions = dollar_volume / 1e6
        if adv_millions <= 0:
            return 100.0

        slippage_bps = 10.0 / np.sqrt(adv_millions)
        return float(np.clip(slippage_bps, 1.0, 100.0))

    def _default_score(self, symbol: str) -> LiquidityScore:
        """Default score when insufficient data."""
        return LiquidityScore(
            symbol=symbol,
            volume_score=0.5,
            spread_score=0.5,
            depth_score=0.5,
            composite_score=0.5,
            is_liquid=False,
            estimated_slippage=50.0,
        )
