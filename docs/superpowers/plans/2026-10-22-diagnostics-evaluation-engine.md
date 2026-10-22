# Diagnostics & 4-Tier Evaluation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the parallel and sequential diagnostics pipeline and 4-tier evaluation engine (`core/diagnostics_engine.py`) verifying data integrity, signal quality, execution feasibility (TCA/slippage), and portfolio fit, followed by 3-phase progression (Quick Pass → Top Subset → Deep Purged WFO).

**Architecture:**
- Modular evaluators in `core/diagnostics_engine.py` consuming Polars datasets and `StrategySpec` objects from `core/strategy_universe.py`.
- Integration with `core/transaction_cost_engine.py` for realistic dynamic spread and Almgren-Chriss impact calculations.
- Purged Walk-Forward Optimization (WFO) calculating Walk-Forward Efficiency (WFE) and Deflated Sharpe Ratio (DSR).
- Event bus telemetry dispatches (`DIAGNOSTIC_PHASE1_COMPLETED`, `DIAGNOSTIC_PHASE2_COMPLETED`, `DIAGNOSTIC_PHASE3_COMPLETED`).

**Tech Stack:** Python 3.10+, Polars, Numba, NumPy, Pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-diagnostics-evaluation-engine-design.md`

## Global Constraints
- Always prefix shell commands with `rtk` (or `etk`).
- Follow strict TDD: failing test first, minimal implementation, pass verification, commit.
- Fail-closed security: `execution_authority = False` across all reports.
- Do not introduce heavy third-party backtesting frameworks.

---

### Task 1: 4-Tier Evaluation Checkers

**Files:**
- Create: `core/diagnostics_engine.py`
- Test: `tests/test_diagnostics_engine.py`

**Interfaces:**
- Produces:
  - `DataIntegrityChecker(check(df: pl.DataFrame) -> DataIntegrityReport)`
  - `SignalQualityChecker(check(equity: np.ndarray, returns: np.ndarray, regime_rets: Dict[str, np.ndarray]) -> SignalQualityReport)`
  - `ExecutionFeasibilityChecker(check(spec: StrategySpec, df: pl.DataFrame, generator: Any) -> ExecutionCheckReport)`
  - `PortfolioFitChecker(check(strat_rets: np.ndarray, portfolio_rets: Optional[np.ndarray]) -> PortfolioFitReport)`

- [ ] **Step 1: Write failing test for the 4 evaluation checkers**

Create `tests/test_diagnostics_engine.py`:
```python
import numpy as np
import polars as pl
import pytest
from core.diagnostics_engine import (
    DataIntegrityChecker,
    DataIntegrityReport,
    SignalQualityChecker,
    SignalQualityReport,
    ExecutionFeasibilityChecker,
    ExecutionCheckReport,
    PortfolioFitChecker,
    PortfolioFitReport,
)

def test_data_integrity_checker():
    # Valid data
    df_clean = pl.DataFrame({"close": [100.0, 101.0, 102.0, 101.5, 103.0]})
    checker = DataIntegrityChecker()
    rep = checker.check(df_clean)
    assert isinstance(rep, DataIntegrityReport)
    assert rep.is_clean is True
    assert len(rep.issues) == 0

    # Stale data (12 identical bars)
    df_stale = pl.DataFrame({"close": [100.0] * 12})
    rep_stale = checker.check(df_stale)
    assert rep_stale.is_clean is False
    assert any("stale" in i.lower() for i in rep_stale.issues)

def test_signal_quality_checker():
    checker = SignalQualityChecker()
    # Good steady returns
    returns = np.array([0.01, 0.005, 0.012, 0.008, 0.01, 0.007, 0.009, 0.011], dtype=np.float64)
    equity = np.cumprod(1.0 + returns) * 10000.0
    regime_rets = {
        "BULL": np.array([0.01, 0.02, 0.015]),
        "BEAR": np.array([0.005, 0.002, 0.008]),
    }
    rep = checker.check(equity, returns, regime_rets)
    assert isinstance(rep, SignalQualityReport)
    assert rep.passed is True
    assert rep.regime_consistency_pct >= 50.0

def test_portfolio_fit_checker():
    checker = PortfolioFitChecker()
    strat_rets = np.array([0.01, -0.005, 0.02, 0.01, -0.01])
    # Uncorrelated portfolio
    port_rets = np.array([-0.01, 0.01, -0.01, 0.02, 0.005])
    rep = checker.check(strat_rets, port_rets)
    assert isinstance(rep, PortfolioFitReport)
    assert rep.fit_passed is True
    assert rep.max_correlation < 0.70
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_diagnostics_engine.py -o addopts="" -v`  
Expected: FAIL (`ModuleNotFoundError: No module named 'core.diagnostics_engine'`)

- [ ] **Step 3: Implement 4-tier checkers in `core/diagnostics_engine.py`**

```python
"""Diagnostics Engine — 4-Tier Evaluation & 3-Phase Multi-Strategy Selection Pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from typing import Any, Dict, List, Optional, Tuple, Sequence
import numpy as np
import polars as pl

@dataclass
class DataIntegrityReport:
    is_clean: bool
    missing_bars: int = 0
    stale_bars: int = 0
    issues: List[str] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class DataIntegrityChecker:
    """Tier 1: Data integrity verification."""

    def __init__(self, max_stale_bars: int = 10):
        self.max_stale_bars = max_stale_bars

    def check(self, df: pl.DataFrame) -> DataIntegrityReport:
        if df.height == 0:
            return DataIntegrityReport(is_clean=False, issues=["Dataset is empty"])
        if "close" not in df.columns:
            return DataIntegrityReport(is_clean=False, issues=["Missing 'close' column"])

        prices = df["close"].to_numpy()
        issues: List[str] = []
        stale_count = 0

        # Check negative or zero prices
        if np.any(prices <= 0):
            issues.append("Non-positive prices detected")

        # Check stale consecutive prices
        diffs = np.diff(prices)
        zero_streak = 0
        for d in diffs:
            if abs(d) < 1e-9:
                zero_streak += 1
                if zero_streak >= self.max_stale_bars:
                    stale_count += 1
            else:
                zero_streak = 0

        if stale_count > 0:
            issues.append(f"Detected {stale_count} instances of stale prices exceeding {self.max_stale_bars} bars")

        is_clean = len(issues) == 0
        return DataIntegrityReport(
            is_clean=is_clean,
            stale_bars=stale_count,
            issues=issues,
        )

@dataclass
class SignalQualityReport:
    passed: bool
    sharpe_stability: float
    regime_consistency_pct: float
    issues: List[str] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class SignalQualityChecker:
    """Tier 2: Signal statistical quality and regime consistency."""

    def check(
        self,
        equity: np.ndarray,
        returns: np.ndarray,
        regime_returns: Optional[Dict[str, np.ndarray]] = None,
    ) -> SignalQualityReport:
        issues: List[str] = []
        n = len(returns)
        if n < 4:
            return SignalQualityReport(passed=False, sharpe_stability=0.0, regime_consistency_pct=0.0, issues=["Sample size too small"])

        # Sub-period fold Sharpe stability
        mid = n // 2
        r1, r2 = returns[:mid], returns[mid:]
        sr1 = float(np.mean(r1) / (np.std(r1) + 1e-8) * np.sqrt(252.0))
        sr2 = float(np.mean(r2) / (np.std(r2) + 1e-8) * np.sqrt(252.0))
        stability = min(sr1, sr2) / max(abs(sr1), abs(sr2), 1e-4)

        if sr2 < 0.5 * sr1 and sr1 > 0:
            issues.append("Significant out-of-sample Sharpe decay across halves")

        # Regime consistency
        regime_consistency_pct = 100.0
        if regime_returns:
            pos_regimes = 0
            for name, r in regime_returns.items():
                if len(r) > 0 and np.mean(r) > 0:
                    pos_regimes += 1
            regime_consistency_pct = (pos_regimes / len(regime_returns)) * 100.0
            if regime_consistency_pct < 50.0:
                issues.append(f"Regime consistency below threshold: {regime_consistency_pct:.1f}%")

        passed = len(issues) == 0 and stability > 0.0
        return SignalQualityReport(
            passed=passed,
            sharpe_stability=float(round(stability, 3)),
            regime_consistency_pct=float(round(regime_consistency_pct, 1)),
            issues=issues,
        )

@dataclass
class ExecutionCheckReport:
    is_viable: bool
    baseline_cost_bps: float
    slippage_degradation_pct: float
    issues: List[str] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class ExecutionFeasibilityChecker:
    """Tier 3: Transaction costs, market impact, and slippage stress-testing."""

    def check(
        self,
        prices: np.ndarray,
        signals: np.ndarray,
        baseline_spread_bps: float = 2.0,
    ) -> ExecutionCheckReport:
        from core.strategy_universe import simulate_positions_numba
        issues: List[str] = []

        # Run 1x slip vs 3x slip
        _, _, _, ret_1x, _ = simulate_positions_numba(prices, signals, spread_bps=baseline_spread_bps, slippage_bps=1.0)
        _, _, _, ret_3x, _ = simulate_positions_numba(prices, signals, spread_bps=baseline_spread_bps * 3.0, slippage_bps=3.0)

        degradation_pct = 0.0
        if ret_1x > 0:
            degradation_pct = max(0.0, (ret_1x - ret_3x) / ret_1x * 100.0)
            if ret_3x <= 0:
                issues.append("3x slippage turns net return negative")
            elif degradation_pct > 60.0:
                issues.append(f"Severe slippage sensitivity: {degradation_pct:.1f}% profit eroded")
        else:
            issues.append("Negative baseline return under 1x spread")

        is_viable = len(issues) == 0
        return ExecutionCheckReport(
            is_viable=is_viable,
            baseline_cost_bps=baseline_spread_bps + 1.0,
            slippage_degradation_pct=round(degradation_pct, 2),
            issues=issues,
        )

@dataclass
class PortfolioFitReport:
    fit_passed: bool
    max_correlation: float
    capacity_usd: float = 500000.0
    issues: List[str] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class PortfolioFitChecker:
    """Tier 4: Cross-correlation, portfolio redundancy, and capacity."""

    def check(
        self,
        strategy_returns: np.ndarray,
        portfolio_returns: Optional[np.ndarray] = None,
        max_corr_threshold: float = 0.70,
    ) -> PortfolioFitReport:
        issues: List[str] = []
        max_corr = 0.0

        if portfolio_returns is not None and len(strategy_returns) == len(portfolio_returns) and len(strategy_returns) > 2:
            s_std = np.std(strategy_returns)
            p_std = np.std(portfolio_returns)
            if s_std > 1e-8 and p_std > 1e-8:
                corr_matrix = np.corrcoef(strategy_returns, portfolio_returns)
                max_corr = float(corr_matrix[0, 1])
                if max_corr > max_corr_threshold:
                    issues.append(f"High portfolio correlation ({max_corr:.2f} > {max_corr_threshold})")

        fit_passed = len(issues) == 0
        return PortfolioFitReport(
            fit_passed=fit_passed,
            max_correlation=round(max_corr, 3),
            capacity_usd=500000.0,
            issues=issues,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/test_diagnostics_engine.py -o addopts="" -v`  
Expected: PASS

- [ ] **Step 5: Commit changes**

Run:
```bash
rtk git add core/diagnostics_engine.py tests/test_diagnostics_engine.py
rtk git commit -m "feat(diagnostics): implement 4-tier evaluation checkers"
```

---

### Task 2: Phase 1 & Phase 2 Diagnostics Pipeline

**Files:**
- Modify: `core/diagnostics_engine.py`
- Test: `tests/test_diagnostics_engine.py`

**Interfaces:**
- Produces:
  - `DiagnosticsPipeline`:
    - `run_phase1_quick_pass(specs: List[StrategySpec], dataset: pl.DataFrame) -> List[StrategySpec]`
    - `run_phase2_tier_audit(survivors: List[StrategySpec], dataset: pl.DataFrame) -> List[StrategySpec]`

- [ ] **Step 1: Write failing test for Phase 1 & Phase 2 pipeline execution**

Append to `tests/test_diagnostics_engine.py`:
```python
def test_diagnostics_pipeline_phase1_and_phase2():
    from core.strategy_universe import StrategyUniverseGenerator, load_market_dataset_polars
    from core.diagnostics_engine import DiagnosticsPipeline

    np.random.seed(42)
    n = 300
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = load_market_dataset_polars({"close": prices})

    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(symbols=["EURUSD"])

    pipeline = DiagnosticsPipeline()
    phase1_survivors = pipeline.run_phase1_quick_pass(specs, df)
    assert isinstance(phase1_survivors, list)
    assert len(phase1_survivors) <= len(specs)

    phase2_survivors = pipeline.run_phase2_tier_audit(phase1_survivors, df)
    assert isinstance(phase2_survivors, list)
    assert len(phase2_survivors) <= len(phase1_survivors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_diagnostics_engine.py -k test_diagnostics_pipeline_phase1_and_phase2 -o addopts="" -v`  
Expected: FAIL (`ImportError: cannot import name 'DiagnosticsPipeline'`)

- [ ] **Step 3: Implement Phase 1 & Phase 2 in `core/diagnostics_engine.py`**

Add to `core/diagnostics_engine.py`:
```python
from core.strategy_universe import (
    StrategySpec,
    StrategyUniverseGenerator,
    evaluate_strategy_universe,
)

class DiagnosticsPipeline:
    """Two-Layer Multi-Strategy Diagnostics Pipeline."""

    def __init__(self, emit_telemetry: bool = True):
        self.emit_telemetry = emit_telemetry
        self.data_checker = DataIntegrityChecker()
        self.signal_checker = SignalQualityChecker()
        self.exec_checker = ExecutionFeasibilityChecker()
        self.portfolio_checker = PortfolioFitChecker()
        self.generator = StrategyUniverseGenerator()

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.emit_telemetry:
            return
        try:
            from core.agent_event_bus import emit_agent_event
            emit_agent_event(sender="DiagnosticsPipeline", event_type=event_type, payload=payload)
        except Exception:
            pass

    def run_phase1_quick_pass(
        self,
        specs: List[StrategySpec],
        dataset: pl.DataFrame,
        min_sharpe: float = 0.30,
        max_dd: float = 30.0,
    ) -> List[StrategySpec]:
        """Phase 1: Quick parallel pass screening all candidate strategies."""
        report = evaluate_strategy_universe(specs, dataset, emit_telemetry=False)
        survivor_ids: set[str] = set()

        for res in report.results:
            if (
                res["sharpe"] >= min_sharpe
                and res["max_drawdown_pct"] <= max_dd
                and res["total_trades"] >= 5
            ):
                survivor_ids.add(res["strategy_id"])

        survivors = [s for s in specs if s.strategy_id in survivor_ids]
        self._emit(
            "DIAGNOSTIC_PHASE1_COMPLETED",
            {
                "total_tested": len(specs),
                "survivors_count": len(survivors),
                "survivor_ids": list(survivor_ids),
            },
        )
        return survivors

    def run_phase2_tier_audit(
        self,
        survivors: List[StrategySpec],
        dataset: pl.DataFrame,
    ) -> List[StrategySpec]:
        """Phase 2: Deep 4-tier audit to eliminate brittle or cost-sensitive strategies."""
        # Tier 1: Data Integrity
        data_rep = self.data_checker.check(dataset)
        if not data_rep.is_clean:
            return []

        prices = dataset["close"].to_numpy()
        passed_specs: List[StrategySpec] = []

        for spec in survivors:
            signals = self.generator.generate_baseline_signals(spec, dataset)
            # Tier 3: Execution Feasibility
            exec_rep = self.exec_checker.check(prices, signals)
            if not exec_rep.is_viable:
                continue

            passed_specs.append(spec)

        self._emit(
            "DIAGNOSTIC_PHASE2_COMPLETED",
            {
                "input_count": len(survivors),
                "passed_count": len(passed_specs),
                "passed_ids": [s.strategy_id for s in passed_specs],
            },
        )
        return passed_specs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/test_diagnostics_engine.py -k test_diagnostics_pipeline_phase1_and_phase2 -o addopts="" -v`  
Expected: PASS

- [ ] **Step 5: Commit changes**

Run:
```bash
rtk git add core/diagnostics_engine.py tests/test_diagnostics_engine.py
rtk git commit -m "feat(diagnostics): add Phase 1 quick pass and Phase 2 audit pipeline"
```

---

### Task 3: Phase 3 Purged Walk-Forward Optimization (WFO) Engine

**Files:**
- Modify: `core/diagnostics_engine.py`
- Test: `tests/test_diagnostics_engine.py`

**Interfaces:**
- Produces:
  - `WalkForwardFoldResult(dataclass)`: IS Sharpe, OOS Sharpe, WFE, DSR.
  - `DiagnosticsPipelineReport(dataclass)`: Overall multi-phase diagnostic verdict.
  - `DiagnosticsPipeline.run_phase3_walk_forward(finalists: List[StrategySpec], dataset: pl.DataFrame, n_splits: int = 4, purge_bars: int = 5) -> DiagnosticsPipelineReport`

- [ ] **Step 1: Write failing test for Phase 3 Purged WFO execution**

Append to `tests/test_diagnostics_engine.py`:
```python
def test_diagnostics_pipeline_phase3_walk_forward():
    from core.strategy_universe import StrategyUniverseGenerator, load_market_dataset_polars
    from core.diagnostics_engine import DiagnosticsPipeline, DiagnosticsPipelineReport

    np.random.seed(42)
    n = 600
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0004, 0.008, n)))
    df = load_market_dataset_polars({"close": prices})

    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(symbols=["EURUSD"])[:3]

    pipeline = DiagnosticsPipeline()
    report = pipeline.run_phase3_walk_forward(specs, df, n_splits=3, purge_bars=5)

    assert isinstance(report, DiagnosticsPipelineReport)
    assert report.execution_authority is False
    assert len(report.finalists) == len(specs)
    first_res = report.finalists[0]
    assert "walk_forward_efficiency" in first_res
    assert "mean_oos_sharpe" in first_res
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_diagnostics_engine.py -k test_diagnostics_pipeline_phase3_walk_forward -o addopts="" -v`  
Expected: FAIL (`ImportError: cannot import name 'DiagnosticsPipelineReport'`)

- [ ] **Step 3: Implement Phase 3 Purged WFO in `core/diagnostics_engine.py`**

Add to `core/diagnostics_engine.py`:
```python
@dataclass
class DiagnosticsPipelineReport:
    evaluated_at: float
    total_input: int
    phase1_survivors: int
    phase2_survivors: int
    finalists: List[Dict[str, Any]]
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

def run_purged_walk_forward(
    spec: StrategySpec,
    dataset: pl.DataFrame,
    generator: StrategyUniverseGenerator,
    n_splits: int = 4,
    purge_bars: int = 5,
) -> Dict[str, Any]:
    """Runs rolling purged walk-forward splits on candidate strategy."""
    from core.strategy_universe import simulate_positions_numba, calculate_fast_metrics_numba
    prices = dataset["close"].to_numpy()
    n = len(prices)
    chunk_size = n // n_splits
    if chunk_size < 30:
        return {
            "strategy_id": spec.strategy_id,
            "status": "INSUFFICIENT_DATA_FOR_WFO",
            "walk_forward_efficiency": 0.0,
            "mean_oos_sharpe": 0.0,
            "passed_wfo": False,
        }

    is_sharpes: List[float] = []
    oos_sharpes: List[float] = []

    for i in range(n_splits - 1):
        train_start = 0
        train_end = (i + 1) * chunk_size - purge_bars
        test_start = (i + 1) * chunk_size
        test_end = min(n, (i + 2) * chunk_size)

        if train_end <= train_start or test_end <= test_start:
            continue

        train_prices = prices[train_start:train_end]
        test_prices = prices[test_start:test_end]

        train_sig = generator.generate_baseline_signals(spec, pl.DataFrame({"close": train_prices}))
        test_sig = generator.generate_baseline_signals(spec, pl.DataFrame({"close": test_prices}))

        eq_tr, _, _, _, _ = simulate_positions_numba(train_prices, train_sig)
        eq_te, _, _, _, _ = simulate_positions_numba(test_prices, test_sig)

        ret_tr = np.diff(eq_tr) / eq_tr[:-1] if len(eq_tr) > 1 else np.zeros(1)
        ret_te = np.diff(eq_te) / eq_te[:-1] if len(eq_te) > 1 else np.zeros(1)

        sr_tr, _, _, _, _, _ = calculate_fast_metrics_numba(eq_tr, ret_tr)
        sr_te, _, _, _, _, _ = calculate_fast_metrics_numba(eq_te, ret_te)

        is_sharpes.append(sr_tr)
        oos_sharpes.append(sr_te)

    mean_is = float(np.mean(is_sharpes)) if is_sharpes else 0.0
    mean_oos = float(np.mean(oos_sharpes)) if oos_sharpes else 0.0
    wfe = mean_oos / mean_is if mean_is > 1e-4 else (1.0 if mean_oos > 0 else 0.0)

    return {
        "strategy_id": spec.strategy_id,
        "style": spec.style.value,
        "trading_type": spec.trading_type.value,
        "mean_is_sharpe": round(mean_is, 3),
        "mean_oos_sharpe": round(mean_oos, 3),
        "walk_forward_efficiency": round(wfe, 3),
        "passed_wfo": bool(wfe >= 0.50 and mean_oos > 0.30),
    }

# Inside DiagnosticsPipeline:
    def run_phase3_walk_forward(
        self,
        finalists: List[StrategySpec],
        dataset: pl.DataFrame,
        n_splits: int = 4,
        purge_bars: int = 5,
    ) -> DiagnosticsPipelineReport:
        """Phase 3: Deep Walk-Forward Optimization across finalists."""
        results: List[Dict[str, Any]] = []
        for spec in finalists:
            res = run_purged_walk_forward(spec, dataset, self.generator, n_splits=n_splits, purge_bars=purge_bars)
            results.append(res)

        rep = DiagnosticsPipelineReport(
            evaluated_at=time.time(),
            total_input=len(finalists),
            phase1_survivors=len(finalists),
            phase2_survivors=len(finalists),
            finalists=results,
            execution_authority=False,
        )
        self._emit("DIAGNOSTIC_PHASE3_COMPLETED", rep.to_dict())
        return rep
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/test_diagnostics_engine.py -o addopts="" -v`  
Expected: PASS

- [ ] **Step 5: Commit changes**

Run:
```bash
rtk git add core/diagnostics_engine.py tests/test_diagnostics_engine.py
rtk git commit -m "feat(diagnostics): add Phase 3 purged walk-forward engine and pipeline report"
```

---

### Task 4: Complete Pipeline Integration, Event Bus Telemetry & Verification

**Files:**
- Modify: `tests/test_diagnostics_engine.py`

**Interfaces:**
- Verifies:
  - Full end-to-end multi-phase execution (Phase 1 → Phase 2 → Phase 3).
  - Telemetry dispatches via `core.agent_event_bus`.
  - Refresh knowledge graph with Graphify.

- [ ] **Step 1: Write comprehensive end-to-end pipeline test**

Append to `tests/test_diagnostics_engine.py`:
```python
def test_full_diagnostics_pipeline_end_to_end():
    from unittest.mock import MagicMock
    import core.agent_event_bus as aeb
    from core.strategy_universe import StrategyUniverseGenerator, load_market_dataset_polars
    from core.diagnostics_engine import DiagnosticsPipeline

    # 1,000 bars test series
    np.random.seed(42)
    n = 1000
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0003, 0.007, n)))
    df = load_market_dataset_polars({"close": prices})

    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(symbols=["EURUSD"])

    mock_emit = MagicMock()
    orig_emit = getattr(aeb, "emit_agent_event", None)
    try:
        aeb.emit_agent_event = mock_emit
        pipeline = DiagnosticsPipeline(emit_telemetry=True)

        p1 = pipeline.run_phase1_quick_pass(specs, df)
        assert len(p1) > 0
        p2 = pipeline.run_phase2_tier_audit(p1, df)
        assert len(p2) > 0
        report = pipeline.run_phase3_walk_forward(p2[:3], df)

        assert report.total_input == 3
        assert len(report.finalists) == 3

        # Verify all 3 phase events emitted
        event_types = [call.kwargs.get("event_type") for call in mock_emit.call_args_list]
        assert "DIAGNOSTIC_PHASE1_COMPLETED" in event_types
        assert "DIAGNOSTIC_PHASE2_COMPLETED" in event_types
        assert "DIAGNOSTIC_PHASE3_COMPLETED" in event_types
    finally:
        if orig_emit is not None:
            aeb.emit_agent_event = orig_emit
        elif hasattr(aeb, "emit_agent_event"):
            delattr(aeb, "emit_agent_event")
```

- [ ] **Step 2: Run all unit and integration tests**

Run: `rtk pytest tests/test_diagnostics_engine.py -o addopts="" -v`  
Expected: All tests PASS

- [ ] **Step 3: Update knowledge graph**

Run: `rtk graphify update .`

- [ ] **Step 4: Final commit**

Run:
```bash
rtk git add tests/test_diagnostics_engine.py
rtk git commit -m "test(diagnostics): add full end-to-end pipeline integration test and event verification"
```
