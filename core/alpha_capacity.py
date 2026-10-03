"""Alpha Capacity Decay Model & Strategy Scaling Engine.

Implements the Alpha Capacity Decay Curve (Part1 §3.8):
    SR(A) = SR_0 * (1 - (A / A_max)^gamma) - (A * sigma_strategy) / (Y * sigma_daily * ADV)

Where:
    SR_0 = unconstrained Sharpe at zero scale
    A_max = hard capacity ceiling (EV = 0)
    gamma = [0.5, 2.0] elasticity of market depth depletion
    tau_i = strategy turnover velocity

Also implements:
- The Alpha-Capacity Tradeoff (Part1 §3.1): SR vs AUM tiers
- Breakeven capacity where net SR = 0
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CapacityResult:
    """Result of alpha capacity evaluation."""
    current_aum: float
    max_capacity: float
    unconstrained_sr: float
    realized_sr: float
    capacity_utilization: float   # A / A_max
    impact_drag: float           # Market impact cost in SR terms
    net_edge_after_capacity: float
    is_viable: bool              # True if realized SR > hurdle
    sr_hurdle: float
    recommended_max_allocation: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_aum": round(self.current_aum, 2),
            "max_capacity": round(self.max_capacity, 2),
            "unconstrained_sr": round(self.unconstrained_sr, 4),
            "realized_sr": round(self.realized_sr, 4),
            "capacity_utilization_pct": round(self.capacity_utilization * 100, 2),
            "impact_drag_sr": round(self.impact_drag, 4),
            "net_edge_after_capacity": round(self.net_edge_after_capacity, 4),
            "is_viable": self.is_viable,
            "sr_hurdle": self.sr_hurdle,
            "recommended_max_allocation": round(self.recommended_max_allocation, 2),
        }


class AlphaCapacityModel:
    """Alpha Capacity Decay Model for strategy scaling decisions.

    Evaluates how a strategy's Sharpe ratio degrades as AUM increases
    due to market impact and capacity constraints.

    Typical usage:
        model = AlphaCapacityModel(sr_0=2.5, max_capacity=500000)
        result = model.evaluate(current_aum=250000, daily_vol=0.01, adv=1000000)
        if not result.is_viable:
            reduce_sizing()
    """

    def __init__(
        self,
        sr_0: float = 2.0,
        max_capacity: float = 500000.0,
        gamma: float = 1.0,
        impact_constant_y: float = 0.5,
        sr_hurdle: float = 0.5,
        turnover_velocity: float = 1.0,
    ):
        """
        Args:
            sr_0: Unconstrained Sharpe ratio at zero scale.
            max_capacity: Hard capacity ceiling where EV = 0 (in USD).
            gamma: Elasticity of market depth depletion [0.5, 2.0].
            impact_constant_y: Market structure constant for square-root impact.
            sr_hurdle: Minimum acceptable Sharpe after capacity drag.
            turnover_velocity: Strategy turnover rate (trades per day).
        """
        self.sr_0 = max(0.0, float(sr_0))
        self.max_capacity = max(1000.0, float(max_capacity))
        self.gamma = max(0.5, min(2.0, float(gamma)))
        self.impact_constant_y = max(0.1, float(impact_constant_y))
        self.sr_hurdle = max(0.0, float(sr_hurdle))
        self.turnover_velocity = max(0.01, float(turnover_velocity))

    def evaluate(
        self,
        current_aum: float,
        daily_volatility: float,
        average_daily_volume: float,
    ) -> CapacityResult:
        """Evaluate alpha capacity at current AUM level.

        Args:
            current_aum: Current assets under management (USD).
            daily_volatility: Strategy daily return volatility (e.g., 0.01 = 1%).
            average_daily_volume: Average daily volume of traded instrument (USD).

        Returns:
            CapacityResult with SR degradation and viability assessment.
        """
        a = max(0.0, float(current_aum))
        sigma_strat = max(0.0001, float(daily_volatility))
        adv = max(1000.0, float(average_daily_volume))
        y = self.impact_constant_y

        # Capacity utilization ratio
        capacity_ratio = min(1.0, a / self.max_capacity) if self.max_capacity > 0 else 0.0

        # Part1 §3.8: SR(A) = SR_0 * (1 - (A/A_max)^gamma) - impact_drag
        capacity_decay = 1.0 - (capacity_ratio ** self.gamma)
        sr_after_capacity = self.sr_0 * capacity_decay

        # Market impact drag: (A * sigma_strategy) / (Y * sigma_daily * ADV)
        # This models the square-root impact cost per unit of AUM
        impact_drag = (a * sigma_strat) / max(1.0, y * sigma_strat * adv)

        realized_sr = max(0.0, sr_after_capacity - impact_drag)

        # Breakeven capacity: find A where realized_sr = 0
        # Solve: SR_0 * (1 - (A/A_max)^gamma) = (A * sigma_strat) / (Y * sigma_strat * ADV)
        recommended_max = self._find_breakeven_capacity(sigma_strat, adv)

        return CapacityResult(
            current_aum=a,
            max_capacity=self.max_capacity,
            unconstrained_sr=self.sr_0,
            realized_sr=realized_sr,
            capacity_utilization=capacity_ratio,
            impact_drag=impact_drag,
            net_edge_after_capacity=realized_sr - self.sr_hurdle,
            is_viable=realized_sr >= self.sr_hurdle,
            sr_hurdle=self.sr_hurdle,
            recommended_max_allocation=recommended_max,
        )

    def _find_breakeven_capacity(
        self,
        daily_volatility: float,
        adv: float,
    ) -> float:
        """Binary search for breakeven AUM where net SR = 0."""
        low, high = 0.0, self.max_capacity
        sigma_strat = max(0.0001, daily_volatility)
        y = self.impact_constant_y

        for _ in range(50):
            mid = (low + high) / 2.0
            ratio = mid / self.max_capacity if self.max_capacity > 0 else 0.0
            sr_cap = self.sr_0 * (1.0 - ratio ** self.gamma)
            drag = (mid * sigma_strat) / max(1.0, y * sigma_strat * adv)
            net = sr_cap - drag

            if net > 0:
                low = mid
            else:
                high = mid

        return round(low, 2)

    @staticmethod
    def get_capacity_tier(aum: float) -> str:
        """Classify AUM into capacity tier (Part1 §3.1).

        $10k: Retail Sweet Spot (Sharpe 4.0+)
        $1M: Transition Zone (Sharpe 2.5)
        $100M: Institutional Domain (Sharpe 1.5)
        $10B: High-Capacity Macro (Sharpe 0.5)
        """
        if aum <= 50000:
            return "RETAIL_SWEET_SPOT"
        elif aum <= 500000:
            return "MID_TIER"
        elif aum <= 5000000:
            return "TRANSITION_ZONE"
        elif aum <= 100000000:
            return "INSTITUTIONAL"
        else:
            return "HIGH_CAPACITY_MACRO"


# Canonical aliases
AlphaCapacityDecayModel = AlphaCapacityModel
StrategyCapacityEvaluator = AlphaCapacityModel
