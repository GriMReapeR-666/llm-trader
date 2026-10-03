"""Optimal Execution Scheduler — Almgren-Chriss Based.

Enhances the existing TransactionCostEngine with:
1. TWAP (Time-Weighted Average Price) scheduling
2. VWAP (Volume-Weighted Average Price) scheduling
3. Optimal execution horizon selection
4. Cost-aware entry timing (spread-aware)
5. Position size adjustment based on expected costs

References:
    - Almgren & Chriss (2001): Optimal execution of portfolio transactions
    - Bertsimas & Lo (1998): Optimal execution with market impact
    - Kissell & Glantz (2003): Optimal Trading Strategies

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │           OPTIMAL EXECUTION SCHEDULER                        │
    │                                                              │
    │  Input: Order (side, size, urgency) + Market Context         │
    │      ↓                                                       │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Step 1: Estimate Market Impact        │                   │
    │  │   Almgren-Chriss permanent/temporary   │                   │
    │  │   Bouchaud square-root                 │                   │
    │  └──────────────┬───────────────────────┘                   │
    │                 ↓                                            │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Step 2: Select Execution Algorithm    │                   │
    │  │   TWAP: Equal slices over time        │                   │
    │  │   VWAP: Volume-proportional slices    │                   │
    │  │   IS: Minimize implementation shortfall│                  │
    │  └──────────────┬───────────────────────┘                   │
    │                 ↓                                            │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Step 3: Generate Schedule             │                   │
    │  │   {time: qty, time: qty, ...}         │                   │
    │  │   With urgency-based front-loading    │                   │
    │  └──────────────┬───────────────────────┘                   │
    │                 ↓                                            │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Step 4: Cost-Aware Entry Timing       │                   │
    │  │   Wait for favorable spread?          │                   │
    │  │   Entry cost > edge? → HOLD           │                   │
    │  └──────────────────────────────────────┘                   │
    └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Execution Schedule
# ---------------------------------------------------------------------------

@dataclass
class ExecutionSlice:
    """A single child order in an execution schedule."""
    time_offset_bars: int      # Bars from now
    quantity: float            # Quantity for this slice
    expected_price: float      # Expected execution price
    expected_cost: float       # Expected cost for this slice
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "time_offset": self.time_offset_bars,
            "quantity": round(self.quantity, 4),
            "expected_price": round(self.expected_price, 5),
            "expected_cost": round(self.expected_cost, 4),
        }


@dataclass
class ExecutionSchedule:
    """Complete execution schedule for an order."""
    algorithm: str             # TWAP, VWAP, IS
    slices: List[ExecutionSlice]
    total_quantity: float
    total_expected_cost: float
    total_expected_slippage: float
    execution_horizon_bars: int
    urgency: float             # 0-1, higher = more front-loaded
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "num_slices": len(self.slices),
            "total_quantity": round(self.total_quantity, 4),
            "total_expected_cost": round(self.total_expected_cost, 4),
            "total_expected_slippage": round(self.total_expected_slippage, 5),
            "execution_horizon": self.execution_horizon_bars,
            "urgency": self.urgency,
            "slices": [s.to_dict() for s in self.slices],
        }


# ---------------------------------------------------------------------------
# Optimal Execution Scheduler
# ---------------------------------------------------------------------------

class OptimalExecutionScheduler:
    """Almgren-Chriss based optimal execution scheduler.
    
    Provides TWAP, VWAP, and Implementation Shortfall scheduling
    algorithms with cost-aware entry timing.
    """
    
    def __init__(
        self,
        permanent_gamma: float = 0.314,
        temporary_eta: float = 0.142,
        temporary_alpha: float = 0.50,
    ):
        self.permanent_gamma = permanent_gamma
        self.temporary_eta = temporary_eta
        self.temporary_alpha = temporary_alpha
    
    def estimate_market_impact(
        self,
        quantity: float,
        price: float,
        daily_volume: float,
        daily_volatility: float,
        bar_volume: float,
    ) -> Tuple[float, float]:
        """Estimate permanent and temporary market impact.
        
        Returns:
            (permanent_impact, temporary_impact) in price units
        """
        if daily_volume <= 0 or bar_volume <= 0:
            return 0.0, 0.0
        
        # Permanent impact: I_perm = γ × σ × P₀ × (Q / V_ADV)
        i_perm = self.permanent_gamma * daily_volatility * price * (quantity / daily_volume)
        
        # Temporary impact: I_temp = η × σ × P₀ × (v / V_bar)^α
        v_rate = quantity  # Simplified: assuming 1-bar execution
        i_temp = self.temporary_eta * daily_volatility * price * (v_rate / bar_volume) ** self.temporary_alpha
        
        return float(i_perm), float(i_temp)
    
    def generate_twap_schedule(
        self,
        quantity: float,
        price: float,
        num_slices: int,
        daily_volume: float,
        daily_volatility: float,
        bar_volume: float,
        urgency: float = 0.5,
    ) -> ExecutionSchedule:
        """Generate TWAP (Time-Weighted Average Price) schedule.
        
        Splits order equally over time, with urgency-based front-loading.
        """
        if num_slices <= 0 or quantity <= 0:
            return ExecutionSchedule(
                algorithm="TWAP", slices=[], total_quantity=0,
                total_expected_cost=0, total_expected_slippage=0,
                execution_horizon_bars=num_slices, urgency=urgency,
            )
        
        # Base quantity per slice
        base_qty = quantity / num_slices
        
        # Urgency adjustment: front-load if urgent
        slices = []
        total_cost = 0.0
        total_slippage = 0.0
        
        for i in range(num_slices):
            # Urgency weighting: higher urgency = more weight on early slices
            weight = (1 - urgency) * (1.0 / num_slices) + urgency * ((num_slices - i) / num_slices)
            weight *= num_slices  # Normalize
            slice_qty = base_qty * weight
            
            # Estimate impact for this slice
            i_perm, i_temp = self.estimate_market_impact(
                slice_qty, price, daily_volume, daily_volatility, bar_volume,
            )
            expected_price = price + i_perm + i_temp
            expected_cost = (i_perm + i_temp) * slice_qty
            
            slices.append(ExecutionSlice(
                time_offset_bars=i,
                quantity=slice_qty,
                expected_price=expected_price,
                expected_cost=expected_cost,
            ))
            total_cost += expected_cost
            total_slippage += i_perm + i_temp
        
        # Normalize quantities to sum to total
        total_qty = sum(s.quantity for s in slices)
        if total_qty > 0:
            for s in slices:
                s.quantity = s.quantity * quantity / total_qty
        
        return ExecutionSchedule(
            algorithm="TWAP",
            slices=slices,
            total_quantity=quantity,
            total_expected_cost=total_cost,
            total_expected_slippage=total_slippage / num_slices,
            execution_horizon_bars=num_slices,
            urgency=urgency,
        )
    
    def generate_vwap_schedule(
        self,
        quantity: float,
        price: float,
        volume_profile: List[float],
        daily_volume: float,
        daily_volatility: float,
        urgency: float = 0.5,
    ) -> ExecutionSchedule:
        """Generate VWAP (Volume-Weighted Average Price) schedule.
        
        Splits order proportional to historical volume profile.
        """
        if not volume_profile or quantity <= 0:
            return ExecutionSchedule(
                algorithm="VWAP", slices=[], total_quantity=0,
                total_expected_cost=0, total_expected_slippage=0,
                execution_horizon_bars=0, urgency=urgency,
            )
        
        # Normalize volume profile
        total_vol = sum(volume_profile)
        if total_vol <= 0:
            volume_profile = [1.0 / len(volume_profile)] * len(volume_profile)
            total_vol = 1.0
        
        slices = []
        total_cost = 0.0
        total_slippage = 0.0
        
        for i, vol in enumerate(volume_profile):
            # Volume-proportional quantity
            vol_fraction = vol / total_vol
            slice_qty = quantity * vol_fraction
            
            # Urgency adjustment
            time_weight = (1 - urgency) + urgency * ((len(volume_profile) - i) / len(volume_profile))
            slice_qty *= time_weight
            
            # Estimate impact
            bar_volume = vol if vol > 0 else daily_volume / len(volume_profile)
            i_perm, i_temp = self.estimate_market_impact(
                slice_qty, price, daily_volume, daily_volatility, bar_volume,
            )
            expected_price = price + i_perm + i_temp
            expected_cost = (i_perm + i_temp) * slice_qty
            
            slices.append(ExecutionSlice(
                time_offset_bars=i,
                quantity=slice_qty,
                expected_price=expected_price,
                expected_cost=expected_cost,
            ))
            total_cost += expected_cost
            total_slippage += i_perm + i_temp
        
        # Normalize
        total_qty = sum(s.quantity for s in slices)
        if total_qty > 0:
            for s in slices:
                s.quantity = s.quantity * quantity / total_qty
        
        return ExecutionSchedule(
            algorithm="VWAP",
            slices=slices,
            total_quantity=quantity,
            total_expected_cost=total_cost,
            total_expected_slippage=total_slippage / len(volume_profile),
            execution_horizon_bars=len(volume_profile),
            urgency=urgency,
        )
    
    def select_optimal_horizon(
        self,
        quantity: float,
        price: float,
        daily_volume: float,
        daily_volatility: float,
        bar_volume: float,
        urgency: float = 0.5,
    ) -> Tuple[int, float]:
        """Select optimal execution horizon by minimizing total cost.
        
        Tests horizons from 1 to 20 bars and returns the one with
        lowest total expected cost.
        
        Returns:
            (optimal_horizon, expected_cost)
        """
        best_horizon = 1
        best_cost = float('inf')
        
        for horizon in range(1, min(21, max(2, int(daily_volume / max(1, quantity)) + 1))):
            schedule = self.generate_twap_schedule(
                quantity, price, horizon, daily_volume, daily_volatility,
                bar_volume, urgency,
            )
            if schedule.total_expected_cost < best_cost:
                best_cost = schedule.total_expected_cost
                best_horizon = horizon
        
        return best_horizon, best_cost
    
    def cost_aware_entry_check(
        self,
        entry_cost_bps: float,
        expected_edge_bps: float,
        min_edge_after_cost_bps: float = 2.0,
    ) -> Tuple[bool, str]:
        """Check if entry is cost-effective.
        
        Args:
            entry_cost_bps: Expected entry cost in basis points
            expected_edge_bps: Expected edge (alpha signal strength) in bps
            min_edge_after_cost_bps: Minimum edge after costs to proceed
            
        Returns:
            (should_enter, reason)
        """
        net_edge = expected_edge_bps - entry_cost_bps
        
        if net_edge < min_edge_after_cost_bps:
            return False, f"Net edge {net_edge:.1f} bps < min {min_edge_after_cost_bps:.1f} bps after costs"
        
        return True, f"Net edge {net_edge:.1f} bps OK (cost={entry_cost_bps:.1f}, edge={expected_edge_bps:.1f})"
    
    def size_adjusted_for_costs(
        self,
        original_lots: float,
        entry_cost_bps: float,
        expected_edge_bps: float,
        price: float,
        pip_value: float = 0.0001,
    ) -> Tuple[float, Dict[str, Any]]:
        """Adjust position size based on transaction costs.
        
        If costs eat too much of the edge, reduce position size.
        """
        net_edge_bps = expected_edge_bps - entry_cost_bps
        
        if net_edge_bps <= 0:
            return 0.0, {"reason": "Costs exceed edge", "net_edge_bps": net_edge_bps}
        
        # Scale position by net edge ratio
        # If net edge is 50% of gross edge, use 50% of original size
        edge_ratio = min(1.0, net_edge_bps / max(expected_edge_bps, 0.01))
        adjusted_lots = original_lots * edge_ratio
        
        return adjusted_lots, {
            "original_lots": original_lots,
            "adjusted_lots": round(adjusted_lots, 4),
            "edge_ratio": round(edge_ratio, 4),
            "net_edge_bps": round(net_edge_bps, 2),
            "entry_cost_bps": round(entry_cost_bps, 2),
        }
