"""Kalman Filter Statistical Arbitrage Strategy Plugin.

Thin BaseStrategy wrapper around core.kalman_statarb.DynamicKalmanStatArbEngine.
Performs recursive state-space cointegration tracking and Ornstein-Uhlenbeck
mean-reversion modeling for pair statistical arbitrage.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from strategies.base import BaseStrategy, StrategySignal, StrategyMetadata
from core.kalman_statarb import DynamicKalmanStatArbEngine, KalmanState


class KalmanStatArbStrategy(BaseStrategy):
    """Dynamic Kalman Filter Statistical Arbitrage Strategy."""

    name: str = "kalman_statarb"
    description: str = "Online Kalman Filter Cointegration & Ornstein-Uhlenbeck StatArb"
    symbols: List[str] = ["EURUSD", "GBPUSD"]
    timeframe: str = "M15"
    trading_style: str = "SWING"
    score_type: str = "Z_SCORE"

    def __init__(
        self,
        delta: float = 1e-4,
        measurement_var: float = 1e-2,
        z_entry_threshold: float = 2.0,
        z_exit_threshold: float = 0.20,
        z_stop_threshold: float = 3.0,
        max_half_life_bars: int = 50,
    ):
        super().__init__()
        self.engine = DynamicKalmanStatArbEngine(
            delta=delta,
            measurement_var=measurement_var,
            z_entry_threshold=z_entry_threshold,
            z_exit_threshold=z_exit_threshold,
            z_stop_threshold=z_stop_threshold,
            max_half_life_bars=max_half_life_bars,
        )

    def evaluate(self, context: Dict[str, Any]) -> StrategySignal:
        """Evaluate pair context and produce a stat-arb StrategySignal.

        Context expects:
            - symbol: Primary leg (Y) symbol
            - price: Primary leg price
            - pair_symbol / leg2_symbol: Secondary leg (X) symbol (optional)
            - pair_price / leg2_price: Secondary leg price (optional)
            - features: technical dict containing pair metrics or benchmark price
            - digits: quote decimal places
        """
        symbol = str(context.get("symbol", "EURUSD")).upper()
        price = float(context.get("price", 0.0) or 0.0)
        digits = int(context.get("digits", 5) or 5)
        features = context.get("features", {}) if isinstance(context.get("features"), dict) else {}

        # Resolve secondary leg price
        pair_symbol = str(context.get("pair_symbol") or context.get("leg2_symbol") or features.get("pair_symbol") or "GBPUSD").upper()
        pair_price = float(context.get("pair_price") or context.get("leg2_price") or features.get("pair_price") or features.get("ema_slow") or price)

        if price <= 0.0 or pair_price <= 0.0:
            return self.hold(symbol, "Invalid or non-positive price for primary/secondary leg")

        state: KalmanState = self.engine.update_state_space(y=price, x=pair_price)

        z = state.spread_z_score
        abs_z = abs(z)
        signal_action = state.state_machine_signal

        # Confidence scaled by Z-score extremity up to max 0.95
        confidence = min(0.95, max(0.1, abs_z / 3.0))

        metadata = {
            "z_score": round(z, 3),
            "alpha": round(state.alpha, 6),
            "beta": round(state.beta, 6),
            "innovation_error": round(state.innovation_error, 6),
            "half_life_bars": round(state.ou_half_life_bars, 1),
            "state_machine_signal": signal_action,
            "leg2_symbol": pair_symbol,
            "hedge_ratio": round(state.beta, 6),
            "trading_style": self.trading_style,
            "score_type": self.score_type,
        }

        # Calculate estimated SL/TP based on synthetic spread levels
        point = float(context.get("point", 0.0001) or 0.0001)
        spread_vol_proxy = math.sqrt(max(1e-8, state.innovation_variance))

        if signal_action == "BUY_SPREAD":
            # Buy primary leg Y, sell leg X
            entry = price
            stop_loss = round(price - 2.5 * spread_vol_proxy, digits)
            take_profit = round(price + 2.0 * spread_vol_proxy, digits)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="BUY",
                confidence=round(confidence, 3),
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timeframe=self.timeframe,
                rationale=f"Kalman StatArb Z={z:.2f} <= -2.0 (Buy Y, Hedge {pair_symbol})",
                confluence_score=round(confidence, 2),
                regime_alignment=True,
                tags=["statarb", "kalman", "mean_reversion"],
                metadata=metadata,
            )
        elif signal_action == "SELL_SPREAD":
            # Sell primary leg Y, buy leg X
            entry = price
            stop_loss = round(price + 2.5 * spread_vol_proxy, digits)
            take_profit = round(price - 2.0 * spread_vol_proxy, digits)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="SELL",
                confidence=round(confidence, 3),
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timeframe=self.timeframe,
                rationale=f"Kalman StatArb Z={z:.2f} >= +2.0 (Sell Y, Hedge {pair_symbol})",
                confluence_score=round(confidence, 2),
                regime_alignment=True,
                tags=["statarb", "kalman", "mean_reversion"],
                metadata=metadata,
            )
        elif signal_action in {"EXIT_TARGET", "STOP_LOSS", "EXIT_TIME_STOP"}:
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="HOLD",
                confidence=0.0,
                rationale=f"Kalman StatArb state machine exit: {signal_action} (Z={z:.2f})",
                metadata=metadata,
            )
        else:
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="HOLD",
                confidence=0.0,
                rationale=f"Kalman StatArb spread within bounds (Z={z:.2f}, half_life={state.ou_half_life_bars:.1f})",
                metadata=metadata,
            )
