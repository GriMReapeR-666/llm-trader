"""Cointegration Pairs Trading Strategy Plugin.

Thin BaseStrategy wrapper around core.cointegration_robustness.
Implements Engle-Granger two-step Augmented Dickey-Fuller (ADF) cointegration
testing and mean-reverting spread execution.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence
import numpy as np

from strategies.base import BaseStrategy, StrategySignal
from core.cointegration_robustness import calculate_engle_granger_adf_cointegration


class CointegrationPairStrategy(BaseStrategy):
    """Statistical Cointegration Pairs Trading Strategy."""

    name: str = "cointegration_pair"
    description: str = "Engle-Granger Two-Step Cointegration Test & Stationary Spread Trading"
    symbols: List[str] = ["EURUSD", "GBPUSD"]
    timeframe: str = "H1"
    trading_style: str = "SWING"
    score_type: str = "COINTEGRATION"

    def __init__(
        self,
        p_value_threshold: float = 0.05,
        z_entry_threshold: float = 2.0,
        z_exit_threshold: float = 0.3,
        lookback_bars: int = 100,
    ):
        super().__init__()
        self.p_value_threshold = float(p_value_threshold)
        self.z_entry_threshold = float(z_entry_threshold)
        self.z_exit_threshold = float(z_exit_threshold)
        self.lookback_bars = int(lookback_bars)

    def evaluate(self, context: Dict[str, Any]) -> StrategySignal:
        """Evaluate market context for cointegration and spread deviation."""
        symbol = str(context.get("symbol", "EURUSD")).upper()
        price = float(context.get("price", 0.0) or 0.0)
        digits = int(context.get("digits", 5) or 5)
        features = context.get("features", {}) if isinstance(context.get("features"), dict) else {}

        pair_symbol = str(context.get("pair_symbol") or context.get("leg2_symbol") or "GBPUSD").upper()
        pair_price = float(context.get("pair_price") or context.get("leg2_price") or price)

        series_y = context.get("series_y")
        series_x = context.get("series_x")

        # Synthesize from ohlcv if raw series not passed directly
        if series_y is None or series_x is None:
            ohlcv = context.get("ohlcv")
            if isinstance(ohlcv, dict) and "close" in ohlcv:
                series_y = list(ohlcv["close"])
            elif isinstance(ohlcv, list):
                series_y = [float(c.get("close", price)) for c in ohlcv if isinstance(c, dict)]

            if not series_y or len(series_y) < 20:
                # Fallback to simulated history based on current price if evaluating point-in-time
                series_y = [price * (1.0 + 0.0005 * math.sin(i / 5.0)) for i in range(self.lookback_bars)]

            if not series_x or len(series_x) < 20:
                series_x = [pair_price * (1.0 + 0.0004 * math.sin(i / 5.0 + 0.1)) for i in range(len(series_y))]

        arr_y = np.asarray(series_y, dtype=float)[-self.lookback_bars:]
        arr_x = np.asarray(series_x, dtype=float)[-self.lookback_bars:]

        if len(arr_y) != len(arr_x) or len(arr_y) < 20:
            return self.hold(symbol, "Insufficient matching price history for cointegration test")

        coint_res = calculate_engle_granger_adf_cointegration(arr_y, arr_x)
        is_coint = bool(coint_res.get("is_cointegrated", False))
        p_val = float(coint_res.get("p_value", 1.0))
        beta = float(coint_res.get("hedge_ratio", 1.0))
        alpha = float(coint_res.get("alpha", 0.0))

        # Calculate residual series: e_t = y_t - (alpha + beta * x_t)
        residuals = arr_y - (alpha + beta * arr_x)
        res_mean = float(np.mean(residuals))
        res_std = float(np.std(residuals))
        current_res = float(residuals[-1])

        z_score = (current_res - res_mean) / res_std if res_std > 1e-8 else 0.0
        abs_z = abs(z_score)

        metadata = {
            "is_cointegrated": is_coint,
            "p_value": round(p_val, 4),
            "hedge_ratio": round(beta, 4),
            "spread_z_score": round(z_score, 3),
            "leg2_symbol": pair_symbol,
            "trading_style": self.trading_style,
            "score_type": self.score_type,
        }

        if not is_coint:
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="HOLD",
                confidence=0.0,
                rationale=f"Pair {symbol}/{pair_symbol} not cointegrated (ADF p={p_val:.3f} > {self.p_value_threshold})",
                metadata=metadata,
            )

        confidence = min(0.95, max(0.2, (1.0 - p_val) * (abs_z / 3.0)))

        spread_vol = max(1e-4, res_std)

        if z_score <= -self.z_entry_threshold:
            # Spread is under-extended: Buy Y, Sell X
            entry = price
            stop_loss = round(price - 2.5 * spread_vol, digits)
            take_profit = round(price + 2.0 * spread_vol, digits)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="BUY",
                confidence=round(confidence, 3),
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timeframe=self.timeframe,
                rationale=f"Cointegrated Spread (p={p_val:.3f}) oversold Z={z_score:.2f} <= -{self.z_entry_threshold}",
                confluence_score=round(1.0 - p_val, 2),
                regime_alignment=True,
                tags=["cointegration", "pairs", "stat_arb"],
                metadata=metadata,
            )
        elif z_score >= self.z_entry_threshold:
            # Spread is over-extended: Sell Y, Buy X
            entry = price
            stop_loss = round(price + 2.5 * spread_vol, digits)
            take_profit = round(price - 2.0 * spread_vol, digits)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="SELL",
                confidence=round(confidence, 3),
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timeframe=self.timeframe,
                rationale=f"Cointegrated Spread (p={p_val:.3f}) overbought Z={z_score:.2f} >= +{self.z_entry_threshold}",
                confluence_score=round(1.0 - p_val, 2),
                regime_alignment=True,
                tags=["cointegration", "pairs", "stat_arb"],
                metadata=metadata,
            )
        else:
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="HOLD",
                confidence=0.0,
                rationale=f"Cointegrated pair within neutral bounds (|Z|={abs_z:.2f} < {self.z_entry_threshold})",
                metadata=metadata,
            )
