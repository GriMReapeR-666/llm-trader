"""Avellaneda-Stoikov Market Making & Reservation Pricing Scalp Strategy.

Thin BaseStrategy wrapper around core.avellaneda_stoikov.AvellanedaStoikovEngine.
Calculates reservation prices and optimal bid/ask quoting brackets for micro-scalp execution.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from strategies.base import BaseStrategy, StrategySignal
from core.avellaneda_stoikov import AvellanedaStoikovEngine, AvellanedaStoikovQuotes


class AvellanedaStoikovScalpStrategy(BaseStrategy):
    """High-frequency Micro-Scalp Reservation Pricing Strategy."""

    name: str = "avellaneda_stoikov_scalp"
    description: str = "Avellaneda-Stoikov (2008) Optimal Market-Making & Inventory-Skewed Scalp"
    symbols: List[str] = ["EURUSD", "GBPUSD", "USDJPY"]
    timeframe: str = "M1"
    trading_style: str = "SCALP"
    score_type: str = "SPREAD_CAPTURE"

    def __init__(
        self,
        gamma: float = 0.5,
        kappa: float = 1.5,
        horizon_seconds: float = 300.0,
    ):
        super().__init__()
        self.gamma = float(gamma)
        self.kappa = float(kappa)
        self.horizon_seconds = float(horizon_seconds)
        self.engine = AvellanedaStoikovEngine(
            gamma=self.gamma,
            kappa=self.kappa,
            default_horizon_seconds=self.horizon_seconds,
        )

    def evaluate(self, context: Dict[str, Any]) -> StrategySignal:
        """Evaluate market context and produce a tight scalp quote signal."""
        symbol = str(context.get("symbol", "EURUSD")).upper()
        price = float(context.get("price", 0.0) or 0.0)
        digits = int(context.get("digits", 5) or 5)
        point = float(context.get("point", 0.0001) or 0.0001)
        features = context.get("features", {}) if isinstance(context.get("features"), dict) else {}

        if price <= 0.0:
            return self.hold(symbol, "Invalid price for Avellaneda-Stoikov evaluation")

        # Inventory in normalized lot units
        account = context.get("account", {}) if isinstance(context.get("account"), dict) else {}
        inventory_units = float(context.get("inventory_units", 0.0) or account.get("position_units", 0.0) or 0.0)

        # Volatility proxy: ATR / price or direct volatility
        atr = float(features.get("atr", 10 * point) or (10 * point))
        volatility = float(features.get("volatility", atr / max(1e-5, price)) or 0.01)

        quotes: AvellanedaStoikovQuotes = self.engine.compute_quotes(
            mid_price=price,
            inventory_units=inventory_units,
            volatility=volatility,
            horizon_seconds=self.horizon_seconds,
        )

        metadata = {
            "mid_price": round(quotes.mid_price, digits),
            "reservation_price": round(quotes.reservation_price, digits),
            "optimal_bid": round(quotes.optimal_bid, digits),
            "optimal_ask": round(quotes.optimal_ask, digits),
            "optimal_half_spread": round(quotes.optimal_half_spread, digits),
            "inventory_skew_discount": round(quotes.inventory_skew_discount, digits),
            "trading_style": self.trading_style,
            "score_type": self.score_type,
        }

        # If inventory is heavily long, reservation price drops below mid -> prioritize SELL/exit
        # If inventory is heavily short, reservation price rises above mid -> prioritize BUY/cover
        # If neutral, scalp direction determined by micro orderbook imbalance or short-term momentum
        imbalance = float(features.get("dom_imbalance", 0.0) or features.get("imbalance_rate", 0.0) or 0.0)

        # Directional tilt
        if quotes.inventory_skew_discount < -0.5 * point:
            # Long inventory discount: hedge or sell at optimal ask
            side = "SELL"
            entry = round(quotes.optimal_ask, digits)
            stop_loss = round(entry + 1.5 * atr, digits)
            take_profit = round(quotes.reservation_price, digits)
            rationale = f"A-S Scalp: Long inventory skew ({inventory_units:+.1f} units), quoting Ask {entry}"
        elif quotes.inventory_skew_discount > 0.5 * point:
            # Short inventory discount: hedge or buy at optimal bid
            side = "BUY"
            entry = round(quotes.optimal_bid, digits)
            stop_loss = round(entry - 1.5 * atr, digits)
            take_profit = round(quotes.reservation_price, digits)
            rationale = f"A-S Scalp: Short inventory skew ({inventory_units:+.1f} units), quoting Bid {entry}"
        else:
            # Balanced inventory: quote with micro imbalance
            if imbalance > 0.1:
                side = "BUY"
                entry = round(quotes.optimal_bid, digits)
                stop_loss = round(entry - 1.2 * atr, digits)
                take_profit = round(entry + 1.5 * quotes.optimal_half_spread, digits)
                rationale = f"A-S Scalp: Bid quote with positive DOM depth imbalance ({imbalance:+.2f})"
            elif imbalance < -0.1:
                side = "SELL"
                entry = round(quotes.optimal_ask, digits)
                stop_loss = round(entry + 1.2 * atr, digits)
                take_profit = round(entry - 1.5 * quotes.optimal_half_spread, digits)
                rationale = f"A-S Scalp: Ask quote with negative DOM depth imbalance ({imbalance:+.2f})"
            else:
                return self.hold(symbol, f"A-S Scalp: Inventory balanced and orderbook neutral (r={quotes.reservation_price:.5f})")

        confidence = min(0.90, max(0.40, 0.60 + abs(imbalance) * 0.2))

        return StrategySignal(
            strategy=self.name,
            symbol=symbol,
            side=side,
            confidence=round(confidence, 3),
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timeframe=self.timeframe,
            rationale=rationale,
            confluence_score=round(confidence, 2),
            regime_alignment=True,
            tags=["scalp", "market_making", "avellaneda_stoikov", "microstructure"],
            metadata=metadata,
        )
