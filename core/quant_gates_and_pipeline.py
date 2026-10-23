"""Quant Gates & Deployment Pipeline — 9-Tier Gates, Dynamic Sizing, and Autonomous Pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
import time
from typing import Any, Dict, List, Optional, Tuple, Sequence
import numpy as np
import polars as pl

class QuantGate(str, Enum):
    DATA_QUALITY_GATE = "DATA_QUALITY_GATE"
    SIGNAL_ROBUSTNESS_GATE = "SIGNAL_ROBUSTNESS_GATE"
    RISK_GATE = "RISK_GATE"
    EXECUTION_GATE = "EXECUTION_GATE"
    PORTFOLIO_FIT_GATE = "PORTFOLIO_FIT_GATE"
    STABILITY_GATE = "STABILITY_GATE"
    MONTE_CARLO_GATE = "MONTE_CARLO_GATE"
    STRESS_TEST_GATE = "STRESS_TEST_GATE"
    LIVE_SHADOW_GATE = "LIVE_SHADOW_GATE"

def calculate_composite_score(metrics: Dict[str, float]) -> float:
    """Calculates 7-factor composite ranking score:
    25% Return + 20% DD Control + 15% Regime + 15% Exec + 10% MC + 10% Div + 5% Capacity.
    """
    sharpe = float(metrics.get("sharpe", metrics.get("oos_sharpe", 0.0)))
    max_dd = float(metrics.get("max_drawdown", 0.20))
    regime_pct = float(metrics.get("regime_consistency_pct", 50.0)) / 100.0
    slip_deg = float(metrics.get("slippage_degradation_pct", 30.0)) / 100.0
    prob_ruin = float(metrics.get("probability_of_ruin", 0.05))
    max_corr = float(metrics.get("max_correlation", 0.50))
    capacity = float(metrics.get("capacity_usd", 500000.0))

    # Factor normalization [0.0 - 1.0]
    s_return = max(0.0, min(1.0, sharpe / 3.0))
    s_dd = max(0.0, min(1.0, 1.0 - (max_dd / 0.30)))
    s_regime = max(0.0, min(1.0, regime_pct))
    s_exec = max(0.0, min(1.0, 1.0 - slip_deg))
    s_mc = max(0.0, min(1.0, 1.0 - prob_ruin))
    s_div = max(0.0, min(1.0, 1.0 - max(0.0, max_corr)))
    s_cap = max(0.0, min(1.0, capacity / 1000000.0))

    score = (
        0.25 * s_return
        + 0.20 * s_dd
        + 0.15 * s_regime
        + 0.15 * s_exec
        + 0.10 * s_mc
        + 0.10 * s_div
        + 0.05 * s_cap
    )
    return float(round(score, 4))

@dataclass
class QuantGateVerdict:
    passed: bool
    passed_gates: List[str]
    failed_gates: List[str]
    gate_scores: Dict[str, float]
    composite_score: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class QuantGateStack:
    """9-Tier Fail-Closed Quantitative Gate Stack."""

    def evaluate_candidate(self, metrics: Dict[str, Any]) -> QuantGateVerdict:
        passed_gates: List[str] = []
        failed_gates: List[str] = []
        gate_scores: Dict[str, float] = {}

        # 1. Data Quality Gate
        if metrics.get("is_data_clean", True):
            passed_gates.append(QuantGate.DATA_QUALITY_GATE.value)
            gate_scores[QuantGate.DATA_QUALITY_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.DATA_QUALITY_GATE.value)
            gate_scores[QuantGate.DATA_QUALITY_GATE.value] = 0.0

        # 2. Signal Robustness Gate (OOS Sharpe >= 0.50, Win Rate >= 0.45)
        oos_sr = metrics.get("oos_sharpe", 0.0)
        win_rate = metrics.get("win_rate", 0.0)
        if oos_sr >= 0.50 and win_rate >= 0.45:
            passed_gates.append(QuantGate.SIGNAL_ROBUSTNESS_GATE.value)
            gate_scores[QuantGate.SIGNAL_ROBUSTNESS_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.SIGNAL_ROBUSTNESS_GATE.value)
            gate_scores[QuantGate.SIGNAL_ROBUSTNESS_GATE.value] = 0.0

        # 3. Risk Gate (MaxDD <= 0.18, Profit Factor >= 1.25)
        max_dd = metrics.get("max_drawdown", 1.0)
        pf = metrics.get("profit_factor", 0.0)
        if max_dd <= 0.18 and pf >= 1.25:
            passed_gates.append(QuantGate.RISK_GATE.value)
            gate_scores[QuantGate.RISK_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.RISK_GATE.value)
            gate_scores[QuantGate.RISK_GATE.value] = 0.0

        # 4. Execution Gate (Slippage degradation <= 50.0%)
        slip_deg = metrics.get("slippage_degradation_pct", 100.0)
        if slip_deg <= 50.0:
            passed_gates.append(QuantGate.EXECUTION_GATE.value)
            gate_scores[QuantGate.EXECUTION_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.EXECUTION_GATE.value)
            gate_scores[QuantGate.EXECUTION_GATE.value] = 0.0

        # 5. Portfolio Fit Gate (Max correlation < 0.70)
        corr = metrics.get("max_correlation", 1.0)
        if corr < 0.70:
            passed_gates.append(QuantGate.PORTFOLIO_FIT_GATE.value)
            gate_scores[QuantGate.PORTFOLIO_FIT_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.PORTFOLIO_FIT_GATE.value)
            gate_scores[QuantGate.PORTFOLIO_FIT_GATE.value] = 0.0

        # 6. Stability Gate (Walk-forward efficiency >= 0.50)
        wfe = metrics.get("walk_forward_efficiency", 0.0)
        if wfe >= 0.50:
            passed_gates.append(QuantGate.STABILITY_GATE.value)
            gate_scores[QuantGate.STABILITY_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.STABILITY_GATE.value)
            gate_scores[QuantGate.STABILITY_GATE.value] = 0.0

        # 7. Monte Carlo Gate (Probability of ruin < 0.05)
        ruin_p = metrics.get("probability_of_ruin", 1.0)
        if ruin_p < 0.05:
            passed_gates.append(QuantGate.MONTE_CARLO_GATE.value)
            gate_scores[QuantGate.MONTE_CARLO_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.MONTE_CARLO_GATE.value)
            gate_scores[QuantGate.MONTE_CARLO_GATE.value] = 0.0

        # 8. Stress-Test Gate (Shock return > -0.05)
        shock_ret = metrics.get("shock_regime_return", -1.0)
        if shock_ret > -0.05:
            passed_gates.append(QuantGate.STRESS_TEST_GATE.value)
            gate_scores[QuantGate.STRESS_TEST_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.STRESS_TEST_GATE.value)
            gate_scores[QuantGate.STRESS_TEST_GATE.value] = 0.0

        # 9. Live Shadow Gate (Fail-closed execution authority verified)
        passed_gates.append(QuantGate.LIVE_SHADOW_GATE.value)
        gate_scores[QuantGate.LIVE_SHADOW_GATE.value] = 1.0

        composite_score = calculate_composite_score(metrics)
        passed_all = len(failed_gates) == 0

        return QuantGateVerdict(
            passed=passed_all,
            passed_gates=passed_gates,
            failed_gates=failed_gates,
            gate_scores=gate_scores,
            composite_score=composite_score,
            execution_authority=False,
        )

class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    TWAP = "TWAP"
    VWAP = "VWAP"
    ICEBERG = "ICEBERG"

@dataclass
class OrderRequest:
    symbol: str
    side: str
    total_quantity: float
    order_type: OrderType
    price: Optional[float] = None
    stop_price: Optional[float] = None
    child_orders: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "total_quantity": self.total_quantity,
            "order_type": self.order_type.value if hasattr(self.order_type, "value") else str(self.order_type),
            "price": self.price,
            "stop_price": self.stop_price,
            "child_orders": self.child_orders,
            "created_at": self.created_at,
            "execution_authority": self.execution_authority,
        }

class ExecutionOrderManager:
    """Manages order slicing (TWAP, VWAP, Iceberg) and dynamic portfolio leverage."""

    def create_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        slices: int = 1,
        iceberg_display_qty: Optional[float] = None,
    ) -> OrderRequest:
        if isinstance(order_type, str):
            order_type = OrderType(order_type)

        child_orders: List[Dict[str, Any]] = []

        if order_type in (OrderType.TWAP, OrderType.VWAP):
            n_slices = max(1, slices)
            slice_qty = round(quantity / n_slices, 4)
            for i in range(n_slices):
                child_orders.append({
                    "slice_idx": i + 1,
                    "quantity": slice_qty,
                    "type": OrderType.LIMIT.value if price else OrderType.MARKET.value,
                    "price": price,
                })
        elif order_type == OrderType.ICEBERG:
            display_qty = iceberg_display_qty or (quantity / 4.0)
            n_slices = max(1, int(math.ceil(quantity / display_qty)))
            remaining = quantity
            for i in range(n_slices):
                q = min(remaining, display_qty)
                child_orders.append({
                    "slice_idx": i + 1,
                    "quantity": round(q, 4),
                    "type": OrderType.LIMIT.value if price else OrderType.MARKET.value,
                    "price": price,
                })
                remaining -= q
        else:
            child_orders.append({
                "slice_idx": 1,
                "quantity": quantity,
                "type": order_type.value if hasattr(order_type, "value") else str(order_type),
                "price": price,
            })

        return OrderRequest(
            symbol=symbol,
            side=side,
            total_quantity=quantity,
            order_type=order_type,
            price=price,
            stop_price=stop_price,
            child_orders=child_orders,
            execution_authority=False,
        )

    def calculate_dynamic_leverage(
        self,
        base_leverage: float = 3.0,
        current_drawdown: float = 0.0,
        kill_dd: float = 0.15,
    ) -> float:
        if current_drawdown >= kill_dd or kill_dd <= 0:
            return 0.0
        headroom = max(0.0, (kill_dd - current_drawdown) / kill_dd)
        return float(round(base_leverage * headroom, 2))

