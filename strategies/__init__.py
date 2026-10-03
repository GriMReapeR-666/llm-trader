"""Trading Strategies — Plugin-based strategy framework.

Drop-in location for custom signal generation and execution strategy scripts.
Each strategy is a Python module containing a class that inherits from
``BaseStrategy``.  The loader discovers and registers all strategies
automatically.

Usage:
    Drop a .py file in this directory with a class inheriting from BaseStrategy::

        from strategies.base import BaseStrategy, StrategySignal

        class MyStrategy(BaseStrategy):
            name = "my_strategy"
            description = "My custom strategy"
            symbols = ["EURUSD", "GBPUSD"]
            timeframe = "H1"

            def evaluate(self, context) -> StrategySignal:
                # Your signal logic here
                return StrategySignal(
                    strategy=self.name,
                    symbol=context.symbol,
                    side="BUY",
                    confidence=0.7,
                    entry=context.price,
                    stop_loss=context.price - 20 * context.point,
                    take_profit=context.price + 40 * context.point,
                )

    The loader will discover and register it automatically.
"""
from strategies.base import BaseStrategy, StrategySignal, StrategyMetadata
from strategies.loader import StrategyLoader, discover_strategies
from strategies.registry import StrategyRegistry

__all__ = [
    "BaseStrategy",
    "StrategySignal",
    "StrategyMetadata",
    "StrategyLoader",
    "discover_strategies",
    "StrategyRegistry",
]
