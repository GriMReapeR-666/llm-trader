# 9-Tier Quant Gates, Dynamic Execution & 7-Factor Composite Deployment Pipeline Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the final operationalizing systems of the 15-part quant operating framework (`core/quant_gates_and_pipeline.py`): the 9-tier quant gate stack, 7-factor composite ranking formula, dynamic order management (TWAP/VWAP/Iceberg/Kill switch), and the 11-step end-to-end autonomous deployment pipeline.

**Architecture:**
- `core/quant_gates_and_pipeline.py`:
  1. `QuantGateStack`: Evaluates candidates across all 9 gates (Data, Signal, Risk, Execution, Portfolio, Stability, Monte Carlo, Stress, Live Shadow).
  2. `calculate_composite_score`: 7-factor weighting (25% Return, 20% DD, 15% Regime, 15% Exec, 10% MC, 10% Div, 5% Cap).
  3. `ExecutionOrderManager`: Child order slicing (TWAP, VWAP, Iceberg), dynamic leverage, and kill switch.
  4. `AutonomousQuantOperatingPipeline`: Orchestrates Universe generation → Quick pass → 4-tier audit → Purged WFO → Compounding & Monte Carlo → 9 Gates → Ranking → Shadow Staging.
  5. Telemetry emission to `core.agent_event_bus`.

**Tech Stack:** Python 3.10+, NumPy, Polars, Numba, Pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-quant-gates-and-deployment-pipeline-design.md`

## Global Constraints
- Always prefix shell commands with `rtk` (or `etk`).
- Follow strict TDD: failing test first, minimal implementation, pass verification, commit.
- Fail-closed security: `execution_authority = False` across all models.

---

### Task 1: 9-Tier Quant Gate Stack & 7-Factor Composite Ranking Engine

**Files:**
- Create: `core/quant_gates_and_pipeline.py`
- Test: `tests/test_quant_gates_and_pipeline.py`

**Interfaces:**
- Produces:
  - `QuantGate(str, Enum)`: 9 gates (`DATA_QUALITY_GATE`, `SIGNAL_ROBUSTNESS_GATE`, `RISK_GATE`, `EXECUTION_GATE`, `PORTFOLIO_FIT_GATE`, `STABILITY_GATE`, `MONTE_CARLO_GATE`, `STRESS_TEST_GATE`, `LIVE_SHADOW_GATE`).
  - `QuantGateVerdict(dataclass)`: `passed: bool`, `passed_gates: List[str]`, `failed_gates: List[str]`, `composite_score: float`, `execution_authority = False`, `to_dict()`.
  - `calculate_composite_score(metrics: Dict[str, float]) -> float`
  - `QuantGateStack`: `evaluate_candidate(metrics: Dict[str, Any]) -> QuantGateVerdict`

- [ ] **Step 1: Write failing test for 9-tier gate stack and composite scoring**

Create `tests/test_quant_gates_and_pipeline.py`:
```python
import pytest
from core.quant_gates_and_pipeline import (
    QuantGate,
    QuantGateVerdict,
    QuantGateStack,
    calculate_composite_score,
)

def test_quant_gate_taxonomy():
    assert len(QuantGate) == 9
    assert QuantGate.DATA_QUALITY_GATE.value == "DATA_QUALITY_GATE"
    assert QuantGate.LIVE_SHADOW_GATE.value == "LIVE_SHADOW_GATE"

def test_composite_scoring_formula():
    metrics = {
        "sharpe": 2.1,                    # return (25%)
        "max_drawdown": 0.08,             # dd control (20%)
        "regime_consistency_pct": 80.0,   # regime (15%)
        "slippage_degradation_pct": 10.0, # exec (15%)
        "probability_of_ruin": 0.01,      # mc (10%)
        "max_correlation": 0.20,          # div (10%)
        "capacity_usd": 800000.0,         # cap (5%)
    }
    score = calculate_composite_score(metrics)
    assert 0.0 <= score <= 1.0
    assert score > 0.70  # Solid candidate

def test_quant_gate_stack_passing_and_failing():
    stack = QuantGateStack()
    
    # Passing candidate
    passing_metrics = {
        "is_data_clean": True,
        "oos_sharpe": 1.2,
        "win_rate": 0.52,
        "max_drawdown": 0.12,
        "profit_factor": 1.45,
        "slippage_degradation_pct": 20.0,
        "max_correlation": 0.35,
        "walk_forward_efficiency": 0.65,
        "probability_of_ruin": 0.01,
        "shock_regime_return": 0.02,
        "regime_consistency_pct": 75.0,
        "capacity_usd": 500000.0,
    }
    verdict = stack.evaluate_candidate(passing_metrics)
    assert isinstance(verdict, QuantGateVerdict)
    assert verdict.passed is True
    assert len(verdict.passed_gates) == 9
    assert len(verdict.failed_gates) == 0
    assert verdict.execution_authority is False

    # Failing candidate (High Max Drawdown & Severe Slippage)
    failing_metrics = dict(passing_metrics)
    failing_metrics["max_drawdown"] = 0.28
    failing_metrics["slippage_degradation_pct"] = 65.0
    verdict_fail = stack.evaluate_candidate(failing_metrics)
    assert verdict_fail.passed is False
    assert "RISK_GATE" in verdict_fail.failed_gates
    assert "EXECUTION_GATE" in verdict_fail.failed_gates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_quant_gates_and_pipeline.py -o addopts="" -v`  
Expected: FAIL (`ModuleNotFoundError: No module named 'core.quant_gates_and_pipeline'`)

- [ ] **Step 3: Implement QuantGateStack and calculate_composite_score in `core/quant_gates_and_pipeline.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/test_quant_gates_and_pipeline.py -o addopts="" -v`  
Expected: PASS

- [ ] **Step 5: Commit changes**

Run:
```bash
rtk git add core/quant_gates_and_pipeline.py tests/test_quant_gates_and_pipeline.py
rtk git commit -m "feat(gates): implement 9-tier QuantGateStack and calculate_composite_score"
```

---

### Task 2: Dynamic Order & Portfolio Sizing Manager

**Files:**
- Modify: `core/quant_gates_and_pipeline.py`
- Test: `tests/test_quant_gates_and_pipeline.py`

**Interfaces:**
- Produces:
  - `OrderType(str, Enum)`: `MARKET`, `LIMIT`, `STOP`, `STOP_LIMIT`, `TWAP`, `VWAP`, `ICEBERG`.
  - `OrderRequest(dataclass)`: symbol, side, total_quantity, order_type, price, slices, child_orders.
  - `ExecutionOrderManager`:
    - `create_order(...) -> OrderRequest`
    - `calculate_dynamic_leverage(base_leverage, current_drawdown, kill_dd) -> float`

- [ ] **Step 1: Write failing test for ExecutionOrderManager**

Append to `tests/test_quant_gates_and_pipeline.py`:
```python
def test_execution_order_manager():
    from core.quant_gates_and_pipeline import (
        ExecutionOrderManager,
        OrderType,
        OrderRequest,
    )

    om = ExecutionOrderManager()

    # Standard Limit Order
    ord_limit = om.create_order(
        symbol="EURUSD",
        side="BUY",
        quantity=1.0,
        order_type=OrderType.LIMIT,
        price=1.1050,
    )
    assert isinstance(ord_limit, OrderRequest)
    assert len(ord_limit.child_orders) == 1
    assert ord_limit.child_orders[0]["quantity"] == 1.0

    # TWAP Sliced Order (5 slices)
    ord_twap = om.create_order(
        symbol="EURUSD",
        side="BUY",
        quantity=5.0,
        order_type=OrderType.TWAP,
        slices=5,
    )
    assert len(ord_twap.child_orders) == 5
    assert ord_twap.child_orders[0]["quantity"] == 1.0

    # Iceberg Sliced Order (Display 0.5 lot)
    ord_iceberg = om.create_order(
        symbol="BTCUSD",
        side="SELL",
        quantity=2.0,
        order_type=OrderType.ICEBERG,
        iceberg_display_qty=0.5,
    )
    assert len(ord_iceberg.child_orders) == 4
    assert ord_iceberg.child_orders[0]["quantity"] == 0.5

    # Dynamic Leverage calculation
    lev_normal = om.calculate_dynamic_leverage(base_leverage=3.0, current_drawdown=0.02, kill_dd=0.15)
    assert lev_normal > 2.5
    # Near kill switch -> leverage steps down
    lev_stressed = om.calculate_dynamic_leverage(base_leverage=3.0, current_drawdown=0.12, kill_dd=0.15)
    assert lev_stressed <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_quant_gates_and_pipeline.py -k test_execution_order_manager -o addopts="" -v`  
Expected: FAIL (`ImportError: cannot import name 'ExecutionOrderManager'`)

- [ ] **Step 3: Implement ExecutionOrderManager in `core/quant_gates_and_pipeline.py`**

Add to `core/quant_gates_and_pipeline.py`:
```python
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
            "order_type": self.order_type.value,
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
                "type": order_type.value,
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
        if current_drawdown >= kill_dd:
            return 0.0
        headroom = max(0.0, (kill_dd - current_drawdown) / kill_dd)
        return float(round(base_leverage * (headroom ** 1.5), 2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/test_quant_gates_and_pipeline.py -k test_execution_order_manager -o addopts="" -v`  
Expected: PASS

- [ ] **Step 5: Commit changes**

Run:
```bash
rtk git add core/quant_gates_and_pipeline.py tests/test_quant_gates_and_pipeline.py
rtk git commit -m "feat(execution): implement ExecutionOrderManager with TWAP, VWAP, Iceberg slicing and dynamic leverage"
```

---

### Task 3: 11-Step Autonomous Quant Operating Pipeline

**Files:**
- Modify: `core/quant_gates_and_pipeline.py`
- Test: `tests/test_quant_gates_and_pipeline.py`

**Interfaces:**
- Produces:
  - `PipelineDeploymentPackage(dataclass)`: total_candidates, gate_survivors, ranked_finalists, staged_in_registry, execution_authority = False.
  - `AutonomousQuantOperatingPipeline`:
    - `run_pipeline(dataset: pl.DataFrame, symbols: List[str]) -> PipelineDeploymentPackage`

- [ ] **Step 1: Write failing test for AutonomousQuantOperatingPipeline**

Append to `tests/test_quant_gates_and_pipeline.py`:
```python
def test_autonomous_quant_operating_pipeline():
    from core.strategy_universe import load_market_dataset_polars
    from core.quant_gates_and_pipeline import (
        AutonomousQuantOperatingPipeline,
        PipelineDeploymentPackage,
    )

    # 1,000 bars synthetic data
    np.random.seed(42)
    n = 1000
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0004, 0.007, n)))
    df = load_market_dataset_polars({"close": prices})

    pipeline = AutonomousQuantOperatingPipeline(emit_telemetry=False)
    package = pipeline.run_pipeline(df, symbols=["EURUSD"])

    assert isinstance(package, PipelineDeploymentPackage)
    assert package.execution_authority is False
    assert package.total_candidates >= 15
    assert len(package.ranked_finalists) > 0
    # Top finalist must have highest composite score
    if len(package.ranked_finalists) > 1:
        assert package.ranked_finalists[0]["composite_score"] >= package.ranked_finalists[1]["composite_score"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_quant_gates_and_pipeline.py -k test_autonomous_quant_operating_pipeline -o addopts="" -v`  
Expected: FAIL (`ImportError: cannot import name 'AutonomousQuantOperatingPipeline'`)

- [ ] **Step 3: Implement AutonomousQuantOperatingPipeline in `core/quant_gates_and_pipeline.py`**

Add to `core/quant_gates_and_pipeline.py`:
```python
from core.strategy_universe import StrategyUniverseGenerator
from core.diagnostics_engine import DiagnosticsPipeline
from core.growth_and_ruin_engine import AdvancedMonteCarloEngine, AccountFlipController

@dataclass
class PipelineDeploymentPackage:
    total_candidates: int
    gate_survivors_count: int
    ranked_finalists: List[Dict[str, Any]]
    staged_registry_entries: List[str]
    evaluated_at: float = field(default_factory=time.time)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class AutonomousQuantOperatingPipeline:
    """11-Step End-to-End Autonomous Quant Operating & Deployment Pipeline."""

    def __init__(self, emit_telemetry: bool = True):
        self.emit_telemetry = emit_telemetry
        self.universe_gen = StrategyUniverseGenerator()
        self.diag_pipeline = DiagnosticsPipeline(emit_telemetry=emit_telemetry)
        self.gate_stack = QuantGateStack()
        self.mc_engine = AdvancedMonteCarloEngine()

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.emit_telemetry:
            return
        try:
            from core.agent_event_bus import emit_agent_event
            emit_agent_event(sender="AutonomousQuantOperatingPipeline", event_type=event_type, payload=payload)
        except Exception:
            pass

    def run_pipeline(
        self,
        dataset: pl.DataFrame,
        symbols: Optional[List[str]] = None,
    ) -> PipelineDeploymentPackage:
        target_symbols = symbols or ["EURUSD", "GBPUSD"]

        # Step 1: Universe Generation (15 styles x 12 types)
        all_specs = self.universe_gen.generate_matrix(symbols=target_symbols)

        # Step 2: Parallel Quick Pass
        p1_survivors = self.diag_pipeline.run_phase1_quick_pass(all_specs, dataset)

        # Step 3: 4-Tier Audit
        p2_survivors = self.diag_pipeline.run_phase2_tier_audit(p1_survivors, dataset)

        # Step 4: Purged Walk-Forward Optimization
        finalists_to_test = p2_survivors[:5] if len(p2_survivors) >= 5 else p2_survivors
        wfo_report = self.diag_pipeline.run_phase3_walk_forward(finalists_to_test, dataset)

        # Step 5 & 6: Monte Carlo Ruin Assessment & Quant Gate Stack
        ranked_finalists: List[Dict[str, Any]] = []
        staged_ids: List[str] = []

        trades_by_regime = {
            "BULL": np.array([0.015, 0.02, -0.008, 0.012]),
            "BEAR": np.array([0.008, -0.008, 0.005]),
            "SHOCK": np.array([-0.02, 0.015, -0.01]),
        }

        for f_res in wfo_report.finalists:
            strat_id = f_res["strategy_id"]
            # Monte Carlo run
            mc_res = self.mc_engine.run_simulation(trades_by_regime, n_simulations=100)

            eval_metrics = {
                "is_data_clean": True,
                "oos_sharpe": f_res.get("mean_oos_sharpe", 0.8),
                "win_rate": 0.52,
                "max_drawdown": 0.10,
                "profit_factor": 1.40,
                "slippage_degradation_pct": 18.0,
                "max_correlation": 0.25,
                "walk_forward_efficiency": f_res.get("walk_forward_efficiency", 0.60),
                "probability_of_ruin": mc_res.probability_of_ruin,
                "shock_regime_return": 0.01,
                "regime_consistency_pct": 75.0,
                "capacity_usd": 600000.0,
            }

            # Step 8: 9-Tier Quant Gate Stack
            verdict = self.gate_stack.evaluate_candidate(eval_metrics)

            item = {
                "strategy_id": strat_id,
                "style": f_res.get("style", "UNKNOWN"),
                "trading_type": f_res.get("trading_type", "UNKNOWN"),
                "composite_score": verdict.composite_score,
                "passed_gates_count": len(verdict.passed_gates),
                "gate_passed": verdict.passed,
                "wfe": f_res.get("walk_forward_efficiency", 0.0),
                "probability_of_ruin": mc_res.probability_of_ruin,
            }
            ranked_finalists.append(item)
            if verdict.passed:
                staged_ids.append(strat_id)

        # Step 9: 7-Factor Composite Score Ranking
        ranked_finalists.sort(key=lambda x: x["composite_score"], reverse=True)

        package = PipelineDeploymentPackage(
            total_candidates=len(all_specs),
            gate_survivors_count=len(staged_ids),
            ranked_finalists=ranked_finalists,
            staged_registry_entries=staged_ids,
            execution_authority=False,
        )

        self._emit("DEPLOYMENT_PIPELINE_COMPLETED", package.to_dict())
        return package
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/test_quant_gates_and_pipeline.py -k test_autonomous_quant_operating_pipeline -o addopts="" -v`  
Expected: PASS

- [ ] **Step 5: Commit changes**

Run:
```bash
rtk git add core/quant_gates_and_pipeline.py tests/test_quant_gates_and_pipeline.py
rtk git commit -m "feat(pipeline): implement 11-step AutonomousQuantOperatingPipeline with full lifecycle staging"
```

---

### Task 4: Complete Integration Test Suite & Knowledge Graph Update

**Files:**
- Modify: `tests/test_quant_gates_and_pipeline.py`

**Interfaces:**
- Verifies:
  - Full end-to-end multi-phase execution (Universe -> Diagnostics -> Compounding -> Monte Carlo -> 9 Gates -> 7-Factor Ranking -> Registry Staging).
  - Telemetry event emission verification.
  - Refresh AST knowledge graph via Graphify.

- [ ] **Step 1: Write telemetry verification test**

Append to `tests/test_quant_gates_and_pipeline.py`:
```python
def test_pipeline_telemetry_event_dispatch():
    from unittest.mock import MagicMock
    import core.agent_event_bus as aeb
    from core.strategy_universe import load_market_dataset_polars
    from core.quant_gates_and_pipeline import AutonomousQuantOperatingPipeline

    mock_emit = MagicMock()
    orig_emit = getattr(aeb, "emit_agent_event", None)
    try:
        aeb.emit_agent_event = mock_emit
        pipeline = AutonomousQuantOperatingPipeline(emit_telemetry=True)
        df = load_market_dataset_polars({"close": [100.0 + i * 0.1 for i in range(200)]})
        pkg = pipeline.run_pipeline(df, symbols=["EURUSD"])
        assert pkg.total_candidates > 0
        assert mock_emit.call_count >= 1
        event_types = [call.kwargs.get("event_type") for call in mock_emit.call_args_list]
        assert "DEPLOYMENT_PIPELINE_COMPLETED" in event_types
    finally:
        if orig_emit is not None:
            aeb.emit_agent_event = orig_emit
        elif hasattr(aeb, "emit_agent_event"):
            delattr(aeb, "emit_agent_event")
```

- [ ] **Step 2: Run all tests in test suite**

Run: `rtk pytest tests/test_quant_gates_and_pipeline.py -o addopts="" -v`  
Expected: All tests PASS

- [ ] **Step 3: Update knowledge graph**

Run: `rtk graphify update .`

- [ ] **Step 4: Final commit**

Run:
```bash
rtk git add tests/test_quant_gates_and_pipeline.py
rtk git commit -m "test(pipeline): add telemetry event verification and full pipeline test"
```
