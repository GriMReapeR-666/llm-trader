"""Example Strategy Template — Copy and customize.

This is a template for creating custom strategies.  Copy this file,
rename it (e.g., ``my_breakout.py``), and modify the class below.

The loader will automatically discover and register any ``BaseStrategy``
subclass in this directory.
"""
from strategies.base import BaseStrategy, StrategySignal


class ExampleMomentumStrategy(BaseStrategy):
    """Example: Simple momentum strategy using RSI and ADX.

    This template demonstrates:
    - How to define strategy metadata
    - How to read market context
    - How to produce a signal with confidence and levels
    - How to use convenience methods (reject, hold)
    """

    name = "example_momentum"
    description = "RSI + ADX momentum strategy (template)"
    symbols = ["EURUSD", "GBPUSD"]
    timeframe = "H1"

    def evaluate(self, context) -> StrategySignal:
        """Evaluate the market context and produce a signal.

        Args:
            context: Dictionary containing:
                - symbol: str
                - price: float (current mid price)
                - point: float (symbol point value)
                - features: dict (technical indicators)
                - regime: str (current regime)
                - ohlcv: dict (recent candles)
        """
        symbol = context.get("symbol", "")
        price = context.get("price", 0.0)
        point = context.get("point", 0.0001)
        features = context.get("features", {})

        # Extract indicators
        rsi = features.get("rsi", 50.0)
        adx = features.get("adx", 20.0)
        ema_fast = features.get("ema_fast", price)
        ema_slow = features.get("ema_slow", price)

        # Skip if ADX too low (no trend)
        if adx < 20:
            return self.hold(symbol, f"ADX {adx:.1f} < 20 — no trend")

        # Skip if RSI is neutral
        if 40 <= rsi <= 60:
            return self.hold(symbol, f"RSI {rsi:.1f} in neutral zone")

        # Generate signal
        side = "BUY" if rsi < 40 and ema_fast > ema_slow else "SELL" if rsi > 60 and ema_fast < ema_slow else None

        if not side:
            return self.hold(symbol, "No clear momentum signal")

        # Calculate levels
        atr = features.get("atr", 20 * point)
        if side == "BUY":
            entry = price
            stop_loss = price - 2 * atr
            take_profit = price + 3 * atr
        else:
            entry = price
            stop_loss = price + 2 * atr
            take_profit = price - 3 * atr

        confidence = min(0.9, 0.5 + (adx - 20) / 100 + abs(rsi - 50) / 200)

        return StrategySignal(
            strategy=self.name,
            symbol=symbol,
            side=side,
            confidence=round(confidence, 3),
            entry=round(entry, context.get("digits", 5)),
            stop_loss=round(stop_loss, context.get("digits", 5)),
            take_profit=round(take_profit, context.get("digits", 5)),
            timeframe=self.timeframe,
            rationale=f"RSI={rsi:.1f}, ADX={adx:.1f}, EMA {'bullish' if ema_fast > ema_slow else 'bearish'}",
            confluence_score=0.5 + adx / 200,
            regime_alignment=True,
            tags=["momentum", "rsi", "adx"],
        )
