"""Kronos Neural Foundation Model Alpha Strategy Plugin.

Thin BaseStrategy wrapper around research.kronos_mt5_adapter.KronosMT5Adapter.
Integrates Kronos foundation model predictive distributions with MT5 L2 DOM depth
and microstructure features to generate neural direction signals.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from strategies.base import BaseStrategy, StrategySignal
from research.kronos_mt5_adapter import KronosMT5Adapter


class KronosNeuralStrategy(BaseStrategy):
    """Deep Time-Series / RL Foundation Model Alpha Strategy."""

    name: str = "kronos_neural"
    description: str = "Kronos Foundation Model Time-Series Forecasting & DOM-Augmented Alpha"
    symbols: List[str] = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    timeframe: str = "H1"
    trading_style: str = "SWING"
    score_type: str = "NEURAL_PROBABILITY"

    def __init__(self, model_key: str = "kronos-mini", force_heuristic: Optional[bool] = None):
        super().__init__()
        self.adapter = KronosMT5Adapter(model_key=model_key, force_heuristic=force_heuristic)

    def evaluate(self, context: Dict[str, Any]) -> StrategySignal:
        """Evaluate MT5 rates and order book depth through Kronos adapter."""
        symbol = str(context.get("symbol", "EURUSD")).upper()
        price = float(context.get("price", 0.0) or 0.0)
        digits = int(context.get("digits", 5) or 5)
        point = float(context.get("point", 0.0001) or 0.0001)
        features = context.get("features", {}) if isinstance(context.get("features"), dict) else {}

        if price <= 0.0:
            return self.hold(symbol, "Invalid price for Kronos Neural evaluation")

        # Extract or synthesize rates
        rates = context.get("rates") or context.get("ohlcv")
        if not rates or not isinstance(rates, (list, tuple)) or len(rates) < 10:
            # Construct standard window if rates not passed directly
            rates = [
                {
                    "open": price * (1.0 + 0.0002 * (i - 15)),
                    "high": price * (1.0 + 0.0002 * (i - 15) + 0.0005),
                    "low": price * (1.0 + 0.0002 * (i - 15) - 0.0005),
                    "close": price * (1.0 + 0.0002 * (i - 14)),
                    "volume": 100.0,
                    "spread": 1.0,
                    "real_volume": 0.0,
                }
                for i in range(25)
            ]

        dom = context.get("dom")
        if not dom and ("imbalance_rate" in features or "dom_imbalance" in features):
            dom = {
                "imbalance_rate": float(features.get("imbalance_rate", features.get("dom_imbalance", 0.0))),
                "spread": float(features.get("spread", 1.0)),
            }

        alpha_res = self.adapter.get_mt5_alpha_factor(symbol=symbol, mt5_rates=rates, dom=dom)

        composite_score = float(alpha_res.get("composite_alpha_score", 0.0))
        direction = str(alpha_res.get("direction", "NEUTRAL")).upper()
        confidence = float(alpha_res.get("confidence", 0.5))

        metadata = {
            "composite_alpha_score": round(composite_score, 4),
            "kronos_alpha_score": round(float(alpha_res.get("kronos_alpha_score", 0.0)), 4),
            "dom_imbalance_modifier": round(float(alpha_res.get("dom_imbalance_modifier", 0.0)), 4),
            "direction": direction,
            "confidence": round(confidence, 3),
            "trading_style": self.trading_style,
            "score_type": self.score_type,
        }

        atr = float(features.get("atr", 20 * point) or (20 * point))

        if direction == "BUY" and composite_score > 0.15:
            entry = price
            stop_loss = round(price - 2.0 * atr, digits)
            take_profit = round(price + 3.0 * atr, digits)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="BUY",
                confidence=round(confidence, 3),
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timeframe=self.timeframe,
                rationale=f"Kronos Neural Alpha score {composite_score:+.2f} > +0.15 (Bullish prediction)",
                confluence_score=round(confidence, 2),
                regime_alignment=True,
                tags=["kronos", "neural", "deep_learning", "foundation_model"],
                metadata=metadata,
            )
        elif direction == "SELL" and composite_score < -0.15:
            entry = price
            stop_loss = round(price + 2.0 * atr, digits)
            take_profit = round(price - 3.0 * atr, digits)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="SELL",
                confidence=round(confidence, 3),
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timeframe=self.timeframe,
                rationale=f"Kronos Neural Alpha score {composite_score:+.2f} < -0.15 (Bearish prediction)",
                confluence_score=round(confidence, 2),
                regime_alignment=True,
                tags=["kronos", "neural", "deep_learning", "foundation_model"],
                metadata=metadata,
            )
        else:
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="HOLD",
                confidence=round(confidence, 3),
                rationale=f"Kronos Neural score {composite_score:+.2f} within neutral deadband",
                metadata=metadata,
            )
