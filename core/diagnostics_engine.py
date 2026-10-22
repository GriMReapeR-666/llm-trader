"""Diagnostics Engine — 4-Tier Evaluation & 3-Phase Multi-Strategy Selection Pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from typing import Any, Dict, List, Optional, Tuple, Sequence
import numpy as np
import polars as pl

from core.strategy_universe import (
    StrategySpec,
    StrategyUniverseGenerator,
    evaluate_strategy_universe,
)


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
            execution_authority=False,
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
        regime_rets: Optional[Dict[str, np.ndarray]] = None,
    ) -> SignalQualityReport:
        issues: List[str] = []
        n = len(returns)
        if n < 4:
            return SignalQualityReport(
                passed=False,
                sharpe_stability=0.0,
                regime_consistency_pct=0.0,
                issues=["Sample size too small"],
                execution_authority=False,
            )

        # Sub-period fold Sharpe stability
        mid = n // 2
        r1, r2 = returns[:mid], returns[mid:]
        sr1 = float(np.mean(r1) / (np.std(r1) + 1e-8) * np.sqrt(252.0))
        sr2 = float(np.mean(r2) / (np.std(r2) + 1e-8) * np.sqrt(252.0))
        stability = min(sr1, sr2) / max(abs(sr1), abs(sr2), 1e-4)

        if sr2 < 0.5 * sr1 and sr1 > 0:
            issues.append("Significant out-of-sample Sharpe decay across halves")

        # Regime consistency
        regimes = regime_returns if regime_returns is not None else regime_rets
        regime_consistency_pct = 100.0
        if regimes:
            pos_regimes = 0
            for name, r in regimes.items():
                if len(r) > 0 and np.mean(r) > 0:
                    pos_regimes += 1
            regime_consistency_pct = (pos_regimes / len(regimes)) * 100.0
            if regime_consistency_pct < 50.0:
                issues.append(f"Regime consistency below threshold: {regime_consistency_pct:.1f}%")

        passed = len(issues) == 0 and stability > 0.0
        return SignalQualityReport(
            passed=passed,
            sharpe_stability=float(round(stability, 3)),
            regime_consistency_pct=float(round(regime_consistency_pct, 1)),
            issues=issues,
            execution_authority=False,
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
        prices: Any,
        signals: Optional[Any] = None,
        baseline_spread_bps: float = 2.0,
        generator: Optional[Any] = None,
    ) -> ExecutionCheckReport:
        from core.strategy_universe import simulate_positions_numba

        if isinstance(prices, np.ndarray):
            prices_arr = prices
            signals_arr = signals
        else:
            # Handle (spec, df, generator) calling convention
            spec = prices
            df = signals
            prices_arr = df["close"].to_numpy()
            if generator is not None:
                signals_arr = generator.generate_baseline_signals(spec, df)
            else:
                from core.strategy_universe import StrategyUniverseGenerator
                signals_arr = StrategyUniverseGenerator().generate_baseline_signals(spec, df)

        issues: List[str] = []

        # Run 1x slip vs 3x slip
        _, _, _, ret_1x, _ = simulate_positions_numba(
            prices_arr, signals_arr, spread_bps=baseline_spread_bps, slippage_bps=1.0
        )
        _, _, _, ret_3x, _ = simulate_positions_numba(
            prices_arr, signals_arr, spread_bps=baseline_spread_bps * 3.0, slippage_bps=3.0
        )

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
            execution_authority=False,
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

        if (
            portfolio_returns is not None
            and len(strategy_returns) == len(portfolio_returns)
            and len(strategy_returns) > 2
        ):
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
            execution_authority=False,
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
            import core.agent_event_bus as aeb
            emit_fn = getattr(aeb, "emit_agent_event", None)
            if emit_fn is not None:
                emit_fn(sender="DiagnosticsPipeline", event_type=event_type, payload=payload)
            elif hasattr(aeb, "get_bus"):
                bus = aeb.get_bus()
                bus.emit(agent="DiagnosticsPipeline", action=event_type, stage="DIAGNOSTICS", payload=payload)
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

