"""Backtest DSR Integrator — S.Ξ Trial Ledger wiring for backtest harnesses.

Wraps any backtest result with the Deflated Sharpe Ratio gate from the
S.Ξ Trial Ledger (UAQP Patch 2). After a backtest completes, this module:

  1. Records the backtested Sharpe as a trial in the ledger
  2. Computes DSR using the TRUE trial count (monotonic, append-only)
  3. Rejects strategies where DSR < τ_dsr (default 0.95)
  4. Produces a DSRReport comparing raw vs deflated Sharpe

This prevents deploying strategies whose backtested Sharpe is noise from
multiple-testing bias — the #1 cause of strategy decay in production.

Usage:
    from research.backtest_dsr_integrator import BacktestDSRIntegrator

    integrator = BacktestDSRIntegrator()
    report = integrator.validate_backtest(
        strategy_id="scalp_eurusd_m15",
        sharpe=1.8,
        n_observations=500,
        returns=equity_curve_returns,
    )
    if not report.passed_gate:
        print(f"REJECTED: DSR={report.dsr:.4f} < {report.gate_threshold}")
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from core.trial_ledger import TrialLedger, DSR_GATE_THRESHOLD, DSRResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DSR Report
# ---------------------------------------------------------------------------
@dataclass
class DSRReport:
    """Full DSR validation report for a backtest."""

    strategy_id: str
    passed_gate: bool              # True if DSR >= threshold
    raw_sharpe: float              # The backtested Sharpe (before deflation)
    dsr: float                     # DSR confidence Φ(z) = 1 − p (higher = more significant; NOT a p-value)
    dsr_threshold: float           # τ_dsr used
    n_trials: int                  # True trial count from ledger (monotonic)
    n_observations: int            # Sample size of this backtest
    expected_max_sharpe: float     # E[max Sharpe] under null hypothesis
    trial_sharpe_mean: float       # Mean of all trial Sharpes in ledger
    trial_sharpe_std: float        # Std of all trial Sharpes in ledger
    pbo_proxy: float               # Fraction of trials with Sharpe <= 0
    skewness: float = 0.0
    kurtosis: float = 3.0
    verdict: str = ""              # "PASS" or "FAIL_NOT_SIGNIFICANT"
    metadata: Optional[Dict[str, Any]] = None
    validated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "passed_gate": self.passed_gate,
            "raw_sharpe": round(self.raw_sharpe, 4),
            "dsr": round(self.dsr, 6),
            "dsr_confidence": round(self.dsr, 6),
            "dsr_threshold": self.dsr_threshold,
            "n_trials": self.n_trials,
            "n_observations": self.n_observations,
            "expected_max_sharpe": round(self.expected_max_sharpe, 4),
            "trial_sharpe_mean": round(self.trial_sharpe_mean, 4),
            "trial_sharpe_std": round(self.trial_sharpe_std, 4),
            "pbo_proxy": round(self.pbo_proxy, 4),
            "skewness": round(self.skewness, 4),
            "kurtosis": round(self.kurtosis, 4),
            "verdict": self.verdict,
            "metadata": self.metadata or {},
            "validated_at": self.validated_at,
        }

    def summary_text(self) -> str:
        """Human-readable summary."""
        status = "✅ PASS" if self.passed_gate else "❌ REJECT"
        lines = [
            f"DSR Report: {self.strategy_id}",
            f"  Status:    {status}",
            f"  Raw Sharpe: {self.raw_sharpe:.4f}",
            f"  DSR:        {self.dsr:.4f} (threshold: {self.dsr_threshold})",
            f"  Verdict:    {self.verdict}",
            f"  Trials:     {self.n_trials} (ledger count, monotonic)",
            f"  Sample:     {self.n_observations} observations",
            f"  E[max Sharpe]: {self.expected_max_sharpe:.4f}",
            f"  Trial μ={self.trial_sharpe_mean:.4f}, σ={self.trial_sharpe_std:.4f}",
            f"  PBO proxy:  {self.pbo_proxy:.1%}",
        ]
        if not self.passed_gate:
            lines.append(
                f"  ⚠️  Strategy REJECTED — Sharpe likely noise from {self.n_trials} trials"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Integrator
# ---------------------------------------------------------------------------
class BacktestDSRIntegrator:
    """Wires the S.Ξ Trial Ledger DSR gate into any backtest harness.

    After a backtest completes, call validate_backtest() with the results.
    This will:
      1. Record the trial in the ledger (append-only, monotonic)
      2. Compute DSR using the true trial count
      3. Return a DSRReport with PASS/FAIL verdict

    The integrator is stateless — all state lives in the TrialLedger.
    """

    def __init__(
        self,
        ledger: Optional[TrialLedger] = None,
        gate_threshold: float = DSR_GATE_THRESHOLD,
        strategy_class: Optional[str] = None,
    ):
        """
        Args:
            ledger: TrialLedger instance (uses singleton if None).
            gate_threshold: τ_dsr threshold (default 0.95).
            strategy_class: Default hypothesis-class tag for DSR deflation.
                When set, the multiple-testing bar is computed within this
                class instead of the global ledger. Callers may override
                per-call. None → global (strictest, fail-closed).
        """
        self._ledger = ledger or TrialLedger()
        self._gate_threshold = gate_threshold
        self._strategy_class = strategy_class

    def validate_backtest(
        self,
        strategy_id: str,
        sharpe: float,
        n_observations: int,
        returns: Optional[Sequence[float]] = None,
        skewness: Optional[float] = None,
        kurtosis: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        strategy_class: Optional[str] = None,
    ) -> DSRReport:
        """Validate a backtest result against the DSR gate.

        Args:
            strategy_id: Unique strategy identifier
            sharpe: Backtested Sharpe ratio
            n_observations: Number of observations in the backtest
            returns: Optional return series for computing skew/kurtosis
            skewness: Optional pre-computed skewness
            kurtosis: Optional pre-computed kurtosis (excess + 3)
            metadata: Optional extra context (params, universe, etc.)

        Returns:
            DSRReport with PASS/FAIL verdict
        """
        # Compute skewness and kurtosis from returns if not provided
        _skew = skewness if skewness is not None else 0.0
        _kurt = kurtosis if kurtosis is not None else 3.0

        if returns is not None and len(returns) > 2:
            arr = np.asarray(returns, dtype=float)
            mean = float(np.mean(arr))
            std = float(np.std(arr))
            if std > 1e-10:
                _skew = float(np.mean((arr - mean) ** 3) / (std ** 3))
                _kurt = float(np.mean((arr - mean) ** 4) / (std ** 4))
                # Keep FULL kurtosis (excess + 3) — the convention documented
                # by TrialLedger.evaluate_dsr and calculate_deflated_sharpe_ratio.
                # (Passing excess here once collapsed the DSR variance term
                # (gamma4-1)/4*SR^2 to negative for large SR, bailing to
                # p=1.0 and auto-failing every high-Sharpe strategy.)
            else:
                _skew = 0.0
                _kurt = 3.0
        else:
            _kurt = _kurt if kurtosis is not None else 3.0

        # Record trial and compute DSR via the ledger. Per-call
        # strategy_class overrides the integrator-level default.
        _strategy_class = strategy_class or self._strategy_class
        dsr_result: DSRResult = self._ledger.evaluate_dsr(
            estimated_sharpe=sharpe,
            n_observations=max(10, n_observations),
            skewness=_skew,
            kurtosis=_kurt,
            strategy_id=strategy_id,
            gate_threshold=self._gate_threshold,
            metadata=metadata,
            strategy_class=_strategy_class,
        )

        # Build report
        report = DSRReport(
            strategy_id=strategy_id,
            passed_gate=dsr_result.is_significant,
            raw_sharpe=sharpe,
            dsr=dsr_result.dsr,
            dsr_threshold=self._gate_threshold,
            n_trials=dsr_result.n_trials,
            n_observations=n_observations,
            expected_max_sharpe=dsr_result.max_expected_sharpe,
            trial_sharpe_mean=dsr_result.trial_sharpe_mean,
            trial_sharpe_std=dsr_result.trial_sharpe_std,
            pbo_proxy=dsr_result.pbo_proxy,
            skewness=_skew,
            kurtosis=_kurt,
            verdict=dsr_result.verdict,
            metadata=metadata,
            validated_at=time.time(),
        )

        if report.passed_gate:
            logger.info(
                "DSR GATE PASS: %s (Sharpe=%.3f, DSR=%.4f, n_trials=%d)",
                strategy_id, sharpe, dsr_result.dsr, dsr_result.n_trials,
            )
        else:
            logger.warning(
                "DSR GATE REJECT: %s (Sharpe=%.3f, DSR=%.4f < %.2f, n_trials=%d)",
                strategy_id, sharpe, dsr_result.dsr, self._gate_threshold,
                dsr_result.n_trials,
            )

        return report

    def validate_backtest_with_returns(
        self,
        strategy_id: str,
        returns: Sequence[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DSRReport:
        """Convenience: validate using raw returns (computes Sharpe + skew + kurt).

        Args:
            strategy_id: Unique strategy identifier
            returns: Return series (e.g., daily or bar-level returns)
            metadata: Optional extra context

        Returns:
            DSRReport with PASS/FAIL verdict
        """
        arr = np.asarray(returns, dtype=float)
        n = len(arr)
        if n < 2:
            return DSRReport(
                strategy_id=strategy_id,
                passed_gate=False,
                raw_sharpe=0.0,
                dsr=0.0,
                dsr_threshold=self._gate_threshold,
                n_trials=self._ledger.n_trials,
                n_observations=n,
                expected_max_sharpe=0.0,
                trial_sharpe_mean=0.0,
                trial_sharpe_std=0.0,
                pbo_proxy=0.0,
                verdict="FAIL_NOT_SIGNIFICANT",
                metadata=metadata,
                validated_at=time.time(),
            )

        mean = float(np.mean(arr))
        std = float(np.std(arr))
        sharpe = mean / max(std, 1e-10) * np.sqrt(252)  # Annualized

        return self.validate_backtest(
            strategy_id=strategy_id,
            sharpe=sharpe,
            n_observations=n,
            returns=returns,
            metadata=metadata,
        )

    def get_ledger_summary(self) -> Dict[str, Any]:
        """Get the current state of the trial ledger."""
        return self._ledger.summary()

    def get_strategy_history(self, strategy_id: str) -> Dict[str, Any]:
        """Get all trial history for a specific strategy."""
        return self._ledger.get_strategy_stats(strategy_id)

    def get_all_gate_results(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent gate evaluation history."""
        return self._ledger.get_gate_history(limit=limit)


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------
def validate_backtest_dsr(
    strategy_id: str,
    sharpe: float,
    n_observations: int,
    returns: Optional[Sequence[float]] = None,
    gate_threshold: float = DSR_GATE_THRESHOLD,
    ledger: Optional[TrialLedger] = None,
) -> DSRReport:
    """One-shot DSR validation for a backtest result.

    Usage:
        from research.backtest_dsr_integrator import validate_backtest_dsr

        report = validate_backtest_dsr(
            strategy_id="my_strategy",
            sharpe=1.8,
            n_observations=500,
            returns=equity_returns,
        )
        if not report.passed_gate:
            print(f"Strategy rejected: DSR={report.dsr:.4f}")
    """
    integrator = BacktestDSRIntegrator(ledger=ledger, gate_threshold=gate_threshold)
    return integrator.validate_backtest(
        strategy_id=strategy_id,
        sharpe=sharpe,
        n_observations=n_observations,
        returns=returns,
    )
