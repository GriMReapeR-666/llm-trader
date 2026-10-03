"""
Order Execution Modules
=======================
Implements transaction cost modeling, market impact, and execution algorithms.
"""

from typing import Dict, List, Optional

from quant_framework.modules.interface import quant_module, quant_tool, QuantModule, registry


@quant_module(
    id="order_execution.spread",
    name="Dynamic Spread Calculator",
    version="1.0.0",
    category="order_execution",
    tags=["spread", "slippage", "market microstructure", "transaction cost"],
)
class DynamicSpreadCalculator(QuantModule):
    """
    Calculates dynamic bid-ask spreads based on market conditions.
    """

    @quant_tool(
        module_id="order_execution.spread",
        name="order_execution.spread.calculate",
        description="Calculate dynamic spread for given market conditions",
        parameters={
            "base_spread_bps": {"type": "float", "description": "Base spread in basis points"},
            "volatility_ratio": {"type": "float", "description": "Current volatility / baseline volatility"},
            "volume_ratio": {"type": "float", "description": "Baseline volume / current volume"},
            "session_multiplier": {"type": "float", "description": "Time-of-day multiplier", "default": 1.0},
        },
        returns={
            "dynamic_spread_bps": {"type": "float"},
            "half_spread_cost": {"type": "float"},
        },
    )
    def calculate(self, base_spread_bps: float = 1.5, volatility_ratio: float = 1.0, volume_ratio: float = 1.0, session_multiplier: float = 1.0, alpha: float = 0.75, beta: float = 0.50) -> Dict:
        vol_mult = alpha * max(0.0, volatility_ratio - 1.0)
        liq_mult = beta * max(0.0, volume_ratio - 1.0)
        dynamic_spread = base_spread_bps * (1.0 + vol_mult + liq_mult) * session_multiplier
        return {
            "dynamic_spread_bps": float(dynamic_spread),
            "half_spread_cost": float(dynamic_spread / 2.0),
        }


@quant_module(
    id="order_execution.almgren_chriss",
    name="Almgren-Chriss Market Impact Calculator",
    version="1.0.0",
    category="order_execution",
    tags=["almgren-chriss", "market impact", "execution", "slippage", "implementation shortfall"],
)
class AlmgrenChrissCalculator(QuantModule):
    """
    Calculates market impact using the Almgren-Chriss framework.
    """

    def __init__(self, gamma: float = 0.314, eta: float = 0.142, alpha: float = 0.5):
        self.gamma = gamma
        self.eta = eta
        self.alpha = alpha

    @quant_tool(
        module_id="order_execution.almgren_chriss",
        name="order_execution.almgren_chriss.calculate",
        description="Calculate Almgren-Chriss market impact for an order",
        parameters={
            "order_size": {"type": "float", "description": "Order size in shares/contracts"},
            "adv": {"type": "float", "description": "Average Daily Volume"},
            "price": {"type": "float", "description": "Current price"},
            "volatility": {"type": "float", "description": "Daily volatility"},
            "duration_bars": {"type": "int", "description": "Execution duration in bars", "default": 1},
            "avg_bar_volume": {"type": "float", "description": "Average bar volume", "default": 0},
        },
        returns={
            "permanent_impact": {"type": "float"},
            "temporary_impact": {"type": "float"},
            "total_impact_bps": {"type": "float"},
        },
    )
    def calculate(self, order_size: float, adv: float, price: float, volatility: float, duration_bars: int = 1, avg_bar_volume: float = 0.0) -> Dict:
        if adv <= 0 or price <= 0:
            return {"error": "ADV and price must be positive"}

        participation = order_size / adv
        trading_rate = order_size / max(duration_bars, 1)
        i_perm = self.gamma * volatility * price * participation

        v_bar = avg_bar_volume if avg_bar_volume > 0 else adv / 78
        i_temp = self.eta * volatility * price * (trading_rate / v_bar) ** self.alpha

        total_impact = (i_perm + i_temp) / price * 10000
        return {
            "permanent_impact": float(i_perm),
            "temporary_impact": float(i_temp),
            "total_impact_bps": float(total_impact),
            "participation_ratio": float(participation),
        }
