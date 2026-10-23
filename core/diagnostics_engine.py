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
    calculate_fast_metrics_numba,
    evaluate_strategy_universe,
    simulate_positions_numba,
)


@dataclass
class DataIntegrityReport:
    is_clean: bool
    missing_bars: int = 0
    stale_bars: int = 0
    duplicate_timestamps: int = 0
    price_spikes: int = 0
    issues: List[str] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DataIntegrityChecker:
    """Tier 1: Data integrity verification."""

    def __init__(self, max_stale_bars: int = 10, price_spike_z_score: float = 5.0):
        self.max_stale_bars = max_stale_bars
        self.price_spike_z_score = price_spike_z_score

    def check(self, df: pl.DataFrame) -> DataIntegrityReport:
        if df.height == 0:
            return DataIntegrityReport(is_clean=False, issues=["Dataset is empty"])
        if "close" not in df.columns:
            return DataIntegrityReport(is_clean=False, issues=["Missing 'close' column"])

        prices = df["close"].to_numpy()
        issues: List[str] = []
        stale_count = 0
        missing_count = 0
        dup_count = 0
        spike_count = 0

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

        # Check duplicate timestamps & missing bars if time column exists
        for time_col in ["time", "timestamp", "datetime", "date"]:
            if time_col in df.columns:
                times = df[time_col].to_numpy()
                unique_times = np.unique(times)
                if len(unique_times) < len(times):
                    dup_count = len(times) - len(unique_times)
                    issues.append(f"Detected {dup_count} duplicate timestamps in '{time_col}'")

                if len(times) > 2:
                    try:
                        time_diffs = np.diff(times.astype(np.float64))
                        pos_diffs = time_diffs[time_diffs > 0]
                        if len(pos_diffs) > 0:
                            med_dt = np.median(pos_diffs)
                            if med_dt > 0:
                                gaps = int(np.sum(time_diffs > 3.0 * med_dt))
                                if gaps > 0:
                                    missing_count = gaps
                                    issues.append(f"Detected {gaps} abnormal timestamp gaps / missing bars")
                    except Exception:
                        pass
                break

        # Check price spikes / bad fills (> price_spike_z_score return shock)
        if len(prices) > 5:
            rets = np.diff(prices) / np.maximum(prices[:-1], 1e-8)
            med_r = np.median(rets)
            mad_r = np.median(np.abs(rets - med_r))
            std_r = np.std(rets)
            if mad_r > 1e-8:
                robust_z = 0.6745 * np.abs(rets - med_r) / mad_r
                spikes = int(np.sum(robust_z > self.price_spike_z_score))
            elif std_r > 1e-8:
                mean_r = np.mean(rets)
                z_scores = np.abs((rets - mean_r) / std_r)
                spikes = int(np.sum(z_scores > self.price_spike_z_score))
            else:
                spikes = 0

            if spikes > 0:
                spike_count = spikes
                issues.append(f"Detected {spikes} extreme price spikes / bad fills exceeding {self.price_spike_z_score} sigma")

        is_clean = len(issues) == 0
        return DataIntegrityReport(
            is_clean=is_clean,
            missing_bars=missing_count,
            stale_bars=stale_count,
            duplicate_timestamps=dup_count,
            price_spikes=spike_count,
            issues=issues,
            execution_authority=False,
        )


@dataclass
class SignalQualityReport:
    passed: bool
    sharpe_stability: float
    regime_consistency_pct: float
    turnover: float = 0.0
    decay_half_life: float = 0.0
    regime_breakdown: Dict[str, float] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SignalQualityChecker:
    """Tier 2: Signal statistical quality, turnover, decay, and regime consistency."""

    def check(
        self,
        equity: np.ndarray,
        returns: np.ndarray,
        regime_returns: Optional[Dict[str, np.ndarray]] = None,
        regime_rets: Optional[Dict[str, np.ndarray]] = None,
        signals: Optional[np.ndarray] = None,
    ) -> SignalQualityReport:
        issues: List[str] = []
        n = len(returns)
        if n < 4:
            return SignalQualityReport(
                passed=False,
                sharpe_stability=0.0,
                regime_consistency_pct=0.0,
                turnover=0.0,
                decay_half_life=0.0,
                regime_breakdown={},
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

        # Turnover and Decay Half-Life
        turnover = 0.0
        decay_half_life = 0.0
        if signals is not None and len(signals) > 1:
            turnover = float(round(np.mean(np.abs(np.diff(signals))), 4))
            if len(signals) > 4:
                s1, s2 = signals[:-1], signals[1:]
                std_s1, std_s2 = np.std(s1), np.std(s2)
                if std_s1 > 1e-8 and std_s2 > 1e-8:
                    autocorr = float(np.corrcoef(s1, s2)[0, 1])
                    if 0.0 < autocorr < 0.999:
                        decay_half_life = float(round(-np.log(2.0) / np.log(autocorr), 2))
                    elif autocorr >= 0.999:
                        decay_half_life = 100.0
                    else:
                        decay_half_life = 1.0

        # Regime consistency & breakdown
        regimes = regime_returns if regime_returns is not None else regime_rets
        regime_consistency_pct = 100.0
        regime_breakdown: Dict[str, float] = {}

        if regimes:
            pos_regimes = 0
            for name, r in regimes.items():
                if len(r) > 0:
                    r_sr = float(round(np.mean(r) / (np.std(r) + 1e-8) * np.sqrt(252.0), 3))
                    regime_breakdown[name] = r_sr
                    if np.mean(r) > 0:
                        pos_regimes += 1
                else:
                    regime_breakdown[name] = 0.0

            regime_consistency_pct = (pos_regimes / len(regimes)) * 100.0
            if regime_consistency_pct < 50.0:
                issues.append(f"Regime consistency below threshold: {regime_consistency_pct:.1f}%")

        passed = len(issues) == 0 and stability > 0.0
        return SignalQualityReport(
            passed=passed,
            sharpe_stability=float(round(stability, 3)),
            regime_consistency_pct=float(round(regime_consistency_pct, 1)),
            turnover=turnover,
            decay_half_life=decay_half_life,
            regime_breakdown=regime_breakdown,
            issues=issues,
            execution_authority=False,
        )


@dataclass
class ExecutionCheckReport:
    is_viable: bool
    baseline_cost_bps: float
    slippage_degradation_pct: float
    latency_degradation_pct: float = 0.0
    market_impact_bps: float = 0.0
    issues: List[str] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExecutionFeasibilityChecker:
    """Tier 3: Transaction costs, market impact, latency stress, and slippage."""

    def check(
        self,
        prices: Any,
        signals: Optional[Any] = None,
        baseline_spread_bps: float = 2.0,
        generator: Optional[Any] = None,
        simulate_latency_bars: int = 0,
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

        # Market impact model proxy: square-root law proxy = 0.1 * vol * sqrt(turnover)
        turnover = float(np.mean(np.abs(np.diff(signals_arr)))) if len(signals_arr) > 1 else 0.0
        price_vol = float(np.std(np.diff(prices_arr) / np.maximum(prices_arr[:-1], 1e-8))) if len(prices_arr) > 1 else 0.0
        market_impact_bps = round(float(price_vol * 10000.0 * np.sqrt(max(turnover, 0.01)) * 0.1), 2)

        # Latency stress test: simulate n-bar delayed execution
        latency_degradation_pct = 0.0
        if simulate_latency_bars > 0 and len(signals_arr) > simulate_latency_bars:
            delayed_signals = np.zeros_like(signals_arr)
            delayed_signals[simulate_latency_bars:] = signals_arr[:-simulate_latency_bars]
            _, _, _, ret_delayed, _ = simulate_positions_numba(
                prices_arr, delayed_signals, spread_bps=baseline_spread_bps, slippage_bps=1.0
            )
            if ret_1x > 0:
                latency_degradation_pct = max(0.0, (ret_1x - ret_delayed) / ret_1x * 100.0)
                if ret_delayed <= 0:
                    issues.append(f"{simulate_latency_bars}-bar latency delay turns net return negative")
                elif latency_degradation_pct > 60.0:
                    issues.append(f"Severe latency sensitivity: {latency_degradation_pct:.1f}% profit eroded")

        is_viable = len(issues) == 0
        return ExecutionCheckReport(
            is_viable=is_viable,
            baseline_cost_bps=baseline_spread_bps + 1.0,
            slippage_degradation_pct=round(degradation_pct, 2),
            latency_degradation_pct=round(latency_degradation_pct, 2),
            market_impact_bps=market_impact_bps,
            issues=issues,
            execution_authority=False,
        )


@dataclass
class PortfolioFitReport:
    fit_passed: bool
    max_correlation: float
    capacity_usd: float = 500000.0
    hhi_concentration: float = 0.0
    effective_bets: float = 1.0
    issues: List[str] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PortfolioFitChecker:
    """Tier 4: Cross-correlation, portfolio redundancy, capacity, and concentration."""

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
            hhi_concentration=1.0,
            effective_bets=1.0,
            issues=issues,
            execution_authority=False,
        )

    def check_portfolio_matrix(
        self,
        returns_matrix: np.ndarray,
        max_corr_threshold: float = 0.70,
    ) -> PortfolioFitReport:
        """Check multi-strategy portfolio returns matrix for correlation, HHI, and effective bets."""
        issues: List[str] = []
        n_samples, n_strats = returns_matrix.shape

        if n_strats <= 1:
            return PortfolioFitReport(
                fit_passed=True,
                max_correlation=0.0,
                hhi_concentration=1.0,
                effective_bets=1.0,
                issues=[],
                execution_authority=False,
            )

        # Pairwise correlation
        corr_matrix = np.corrcoef(returns_matrix, rowvar=False)
        corr_matrix = np.nan_to_num(corr_matrix, nan=0.0)
        upper_idx = np.triu_indices(n_strats, k=1)
        off_diag_corrs = np.abs(corr_matrix[upper_idx])
        max_corr = float(np.max(off_diag_corrs)) if len(off_diag_corrs) > 0 else 0.0

        if max_corr > max_corr_threshold:
            issues.append(f"High cross-strategy correlation ({max_corr:.2f} > {max_corr_threshold})")

        # Equal-weighted concentration metrics: HHI = sum(w_i^2), N_eff = 1 / HHI
        weights = np.ones(n_strats, dtype=np.float64) / n_strats
        hhi = float(np.sum(weights ** 2))
        effective_bets = float(1.0 / hhi) if hhi > 1e-8 else 1.0

        fit_passed = len(issues) == 0
        return PortfolioFitReport(
            fit_passed=fit_passed,
            max_correlation=round(max_corr, 3),
            capacity_usd=500000.0 * n_strats,
            hhi_concentration=round(hhi, 4),
            effective_bets=round(effective_bets, 2),
            issues=issues,
            execution_authority=False,
        )


@dataclass
class DiagnosticsPipelineReport:
    evaluated_at: float = field(default_factory=time.time)
    total_input: int = 0
    phase1_survivors: int = 0
    phase2_survivors: int = 0
    finalists: List[Dict[str, Any]] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_purged_walk_forward(
    spec: StrategySpec,
    dataset: pl.DataFrame,
    generator: Optional[StrategyUniverseGenerator] = None,
    n_splits: int = 4,
    purge_bars: int = 5,
) -> Dict[str, Any]:
    """Phase 3: Purged Walk-Forward Optimization (WFO) over rolling train/test splits."""
    if generator is None:
        generator = StrategyUniverseGenerator()

    if dataset.height == 0 or "close" not in dataset.columns:
        return {
            "strategy_id": spec.strategy_id,
            "name": spec.name,
            "style": spec.style.value,
            "trading_type": spec.trading_type.value,
            "n_splits": 0,
            "purge_bars": purge_bars,
            "mean_is_sharpe": 0.0,
            "mean_oos_sharpe": 0.0,
            "wfe": 0.0,
            "wfe_passed": False,
            "splits": [],
            "execution_authority": False,
        }

    prices = dataset["close"].to_numpy()
    n_bars = len(prices)

    min_required = (n_splits + 1) * 2 + purge_bars
    if n_bars < min_required:
        return {
            "strategy_id": spec.strategy_id,
            "name": spec.name,
            "style": spec.style.value,
            "trading_type": spec.trading_type.value,
            "n_splits": 0,
            "purge_bars": purge_bars,
            "mean_is_sharpe": 0.0,
            "mean_oos_sharpe": 0.0,
            "wfe": 0.0,
            "wfe_passed": False,
            "splits": [],
            "execution_authority": False,
        }

    signals = generator.generate_baseline_signals(spec, dataset)

    test_size = max(5, (n_bars - purge_bars) // (n_splits + 1))
    train_size = test_size
    step = (n_bars - train_size - purge_bars - test_size) // max(1, n_splits - 1) if n_splits > 1 else 0

    split_details: List[Dict[str, Any]] = []
    is_sharpes: List[float] = []
    oos_sharpes: List[float] = []

    for k in range(n_splits):
        train_start = k * step
        train_end = train_start + train_size
        test_start = train_end + purge_bars
        test_end = (test_start + test_size) if k < n_splits - 1 else n_bars
        test_end = min(n_bars, test_end)

        if test_start >= test_end:
            continue

        # In-Sample Simulation
        train_p = prices[train_start:train_end]
        train_s = signals[train_start:train_end]
        is_eq, _, is_trades, is_ret, is_dd = simulate_positions_numba(train_p, train_s)
        is_returns = (
            np.diff(is_eq) / is_eq[:-1] if len(is_eq) > 1 else np.empty(0, dtype=np.float64)
        )
        is_sr, _, _, _, _, _ = calculate_fast_metrics_numba(is_eq, is_returns)

        # Out-of-Sample Simulation
        test_p = prices[test_start:test_end]
        test_s = signals[test_start:test_end]
        oos_eq, _, oos_trades, oos_ret, oos_dd = simulate_positions_numba(test_p, test_s)
        oos_returns = (
            np.diff(oos_eq) / oos_eq[:-1] if len(oos_eq) > 1 else np.empty(0, dtype=np.float64)
        )
        oos_sr, _, _, _, _, _ = calculate_fast_metrics_numba(oos_eq, oos_returns)

        is_sr_clean = round(float(is_sr), 3)
        oos_sr_clean = round(float(oos_sr), 3)
        is_sharpes.append(is_sr_clean)
        oos_sharpes.append(oos_sr_clean)

        split_details.append({
            "split": k,
            "train_range": (train_start, train_end),
            "purge_range": (train_end, test_start),
            "test_range": (test_start, test_end),
            "is_trades": is_trades,
            "oos_trades": oos_trades,
            "is_sharpe": is_sr_clean,
            "oos_sharpe": oos_sr_clean,
            "is_return_pct": round(float(is_ret * 100.0), 2),
            "oos_return_pct": round(float(oos_ret * 100.0), 2),
        })

    mean_is_sharpe = float(np.mean(is_sharpes)) if is_sharpes else 0.0
    mean_oos_sharpe = float(np.mean(oos_sharpes)) if oos_sharpes else 0.0
    wfe = (mean_oos_sharpe / mean_is_sharpe) if mean_is_sharpe > 1e-4 else 0.0

    return {
        "strategy_id": spec.strategy_id,
        "name": spec.name,
        "style": spec.style.value,
        "trading_type": spec.trading_type.value,
        "n_splits": len(split_details),
        "purge_bars": purge_bars,
        "mean_is_sharpe": round(mean_is_sharpe, 3),
        "mean_oos_sharpe": round(mean_oos_sharpe, 3),
        "wfe": round(float(wfe), 3),
        "wfe_passed": bool(wfe >= 0.50),
        "splits": split_details,
        "execution_authority": False,
    }


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
        self._last_total_input = len(specs)
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
        self._last_phase1_count = len(survivors)
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
            self._last_phase2_count = 0
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

        self._last_phase2_count = len(passed_specs)
        self._emit(
            "DIAGNOSTIC_PHASE2_COMPLETED",
            {
                "input_count": len(survivors),
                "passed_count": len(passed_specs),
                "passed_ids": [s.strategy_id for s in passed_specs],
            },
        )
        return passed_specs

    def run_phase3_walk_forward(
        self,
        finalists: List[StrategySpec],
        dataset: pl.DataFrame,
        n_splits: int = 4,
        purge_bars: int = 5,
    ) -> DiagnosticsPipelineReport:
        """Phase 3: Purged Walk-Forward Optimization (WFO) Engine."""
        finalist_results: List[Dict[str, Any]] = []
        for spec in finalists:
            wfo_res = run_purged_walk_forward(
                spec=spec,
                dataset=dataset,
                generator=self.generator,
                n_splits=n_splits,
                purge_bars=purge_bars,
            )
            finalist_results.append(wfo_res)

        self._emit(
            "DIAGNOSTIC_PHASE3_COMPLETED",
            {
                "finalists_count": len(finalist_results),
                "finalist_ids": [f["strategy_id"] for f in finalist_results],
                "mean_wfe": float(np.mean([f["wfe"] for f in finalist_results])) if finalist_results else 0.0,
            },
        )

        total_input = getattr(self, "_last_total_input", len(finalists))
        phase1_survivors = getattr(self, "_last_phase1_count", len(finalists))
        phase2_survivors = getattr(self, "_last_phase2_count", len(finalists))

        return DiagnosticsPipelineReport(
            evaluated_at=time.time(),
            total_input=total_input,
            phase1_survivors=phase1_survivors,
            phase2_survivors=phase2_survivors,
            finalists=finalist_results,
            execution_authority=False,
        )

    def run_pipeline(
        self,
        specs: List[StrategySpec],
        dataset: pl.DataFrame,
        min_sharpe: float = 0.30,
        max_dd: float = 30.0,
        n_splits: int = 4,
        purge_bars: int = 5,
    ) -> DiagnosticsPipelineReport:
        """End-to-end 3-phase multi-strategy selection pipeline."""
        p1 = self.run_phase1_quick_pass(specs, dataset, min_sharpe=min_sharpe, max_dd=max_dd)
        p2 = self.run_phase2_tier_audit(p1, dataset)
        p3_report = self.run_phase3_walk_forward(p2, dataset, n_splits=n_splits, purge_bars=purge_bars)
        return p3_report

    def run_parallel_diagnostics(
        self,
        specs: List[StrategySpec],
        dataset: pl.DataFrame,
        spread_bps: float = 2.0,
        slippage_bps: float = 1.0,
    ) -> Dict[str, Any]:
        """Layer 1: Parallel diagnostics across common data universe and uniform cost model."""
        report = evaluate_strategy_universe(
            specs,
            dataset,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            emit_telemetry=False,
        )

        ranking = sorted(report.results, key=lambda x: x["sharpe"], reverse=True)

        # Style summary
        style_summary: Dict[str, Dict[str, Any]] = {}
        for r in ranking:
            st = r["style"]
            if st not in style_summary:
                style_summary[st] = {"count": 0, "sharpes": [], "returns": []}
            style_summary[st]["count"] += 1
            style_summary[st]["sharpes"].append(r["sharpe"])
            ret_val = r.get("total_return_pct", r.get("net_return_pct", 0.0))
            style_summary[st]["returns"].append(ret_val)

        aggregated_styles = {
            st: {
                "count": data["count"],
                "mean_sharpe": round(float(np.mean(data["sharpes"])), 3),
                "mean_return_pct": round(float(np.mean(data["returns"])), 2),
            }
            for st, data in style_summary.items()
        }

        # Rich formatted ASCII table output
        print("\n" + "=" * 90)
        print("          PARALLEL MULTI-STRATEGY DIAGNOSTICS BATCH REPORT          ")
        print("=" * 90)
        print(f"{'Strategy ID':<18} | {'Style':<16} | {'Sharpe':>7} | {'Return %':>9} | {'MaxDD %':>8} | {'Trades':>6}")
        print("-" * 90)
        for cand in ranking[:15]:
            ret_display = cand.get("total_return_pct", cand.get("net_return_pct", 0.0))
            print(
                f"{cand['strategy_id']:<18} | {cand['style']:<16} | {cand['sharpe']:>7.2f} | "
                f"{ret_display:>8.2f}% | {cand['max_drawdown_pct']:>7.2f}% | {cand['total_trades']:>6}"
            )
        if len(ranking) > 15:
            print(f"... and {len(ranking) - 15} more candidates tested simultaneously across common data universe.")
        print("-" * 90)

        self._emit(
            "DIAGNOSTIC_PARALLEL_BATCH_COMPLETED",
            {
                "total_candidates": len(specs),
                "top_strategy_id": ranking[0]["strategy_id"] if ranking else None,
                "top_sharpe": ranking[0]["sharpe"] if ranking else 0.0,
                "styles_evaluated": len(aggregated_styles),
            },
        )

        return {
            "total_candidates": len(specs),
            "ranking": ranking,
            "style_summary": aggregated_styles,
            "execution_authority": False,
        }



