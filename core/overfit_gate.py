"""Overfit Gate — blocks strategies with HIGH/CRITICAL overfit risk from live trading.

Workflow:
  1. Before a strategy goes live, `validate_strategy()` runs detect_overfitting()
     on its backtested returns and stores the result.
  2. Every proposal in `_validate_proposal()` calls `check_strategy()` which
     reads the stored result.  HIGH/CRITICAL → REJECT.  NOT_VALIDATED → REJECT.
     LOW/MODERATE → PASS.

This prevents:
  - Deploying a strategy whose backtested Sharpe is noise (deflated Sharpe fails)
  - Deploying with insufficient observations (min backtest length)
  - Deploying when IS→OOS divergence > 50% (overfit)
  - Deploying untested strategies (no backtest at all)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from core.overfit_detector import detect_overfitting, OverfitResult
from core.trial_ledger import TrialLedger, DSR_GATE_THRESHOLD
from core.state_persistence import StoreBackedState

logger = logging.getLogger(__name__)

# Risk levels that block live deployment
BLOCKED_RISK_LEVELS = frozenset({"HIGH", "CRITICAL"})


@dataclass
class GateResult:
    """Result of checking a strategy against the overfit gate."""
    strategy: str
    status: str          # "PASS", "REJECT", "NOT_VALIDATED"
    risk_level: str      # "LOW", "MODERATE", "HIGH", "CRITICAL", "UNKNOWN"
    grade: str           # "A"-"F" or "N/A"
    reason: str
    overfit_result: Optional[Dict[str, Any]] = None
    checked_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "strategy": self.strategy,
            "status": self.status,
            "risk_level": self.risk_level,
            "grade": self.grade,
            "reason": self.reason,
            "checked_at": self.checked_at,
        }
        if self.overfit_result:
            d["overfit_result"] = self.overfit_result
        return d


class OverfitGate(StoreBackedState):
    """Persistent overfit validation gate.

    Stores per-strategy overfit results in a JSON file so they survive restarts.
    """

    store_key = "overfit_gate_state"

    def __init__(self, state_file: str = "overfit_gate_state.json",
                  trial_ledger: Optional[TrialLedger] = None,
                  store: Optional[Any] = None):
        self.state_file = state_file
        self.store = store
        self._results: Dict[str, Dict[str, Any]] = self._load()
        self._trial_ledger = trial_ledger or TrialLedger()
        if store is not None:
            self.attach_store(store)

    def _load(self) -> Dict[str, Dict[str, Any]]:
        data = self._load_state_dict()
        strategies = data.get("strategies", {})
        return strategies if isinstance(strategies, dict) else {}

    def _save(self) -> None:
        self._save_state_dict({"strategies": self._results, "updated_at": time.time()})

    # ------------------------------------------------------------------
    # Validate (run overfit detection and store)
    # ------------------------------------------------------------------

    def validate_strategy(
        self,
        strategy: str,
        returns: Sequence[float],
        is_returns: Optional[Sequence[float]] = None,
        oos_returns: Optional[Sequence[float]] = None,
        n_strategies_tested: int = 1,
        fold_sharpes: Optional[List[float]] = None,
        sharpe_trajectory: Optional[List[float]] = None,
        returns_matrix: Optional[np.ndarray] = None,
        strategy_class: Optional[str] = None,
        returns_provenance: str = "measured",
    ) -> GateResult:
        """Run overfit detection on a strategy's returns and store the result.

        Args:
            strategy: Strategy identifier (e.g. "scalp_eurusd_m15")
            returns: Full-sample backtested returns
            is_returns: In-sample returns (optional)
            oos_returns: Out-of-sample returns (optional)
            n_strategies_tested: Total strategies/params tested
            fold_sharpes: CV fold Sharpe ratios (optional)
            sharpe_trajectory: Sharpe after each iteration (optional)
            returns_matrix: (n_strategies, n_obs) matrix (optional)
            strategy_class: Hypothesis-class tag for per-class DSR deflation
                (e.g. "persistence_tsmom"). None → global ledger count.
            returns_provenance: Where the series came from. Anything other
                than "measured" (e.g. "synthetic") is refused without
                evaluation — see the guard below.

        Returns:
            GateResult with PASS/REJECT status
        """
        # A gate that is fed a series built to hit a target Sharpe is not
        # measuring anything. Callers used to synthesise
        # np.random.normal(sharpe/252, 0.01, 200) from a backtest claim, which
        # makes a random walk score grade C / MODERATE and clear the DSR bar,
        # while an identical real series is rejected F / CRITICAL. Overfitting
        # detection must never certify its own input.
        if str(returns_provenance or "measured").strip().lower() != "measured":
            return GateResult(
                strategy=strategy,
                status="NOT_VALIDATED",
                risk_level="UNKNOWN",
                grade="N/A",
                reason=(
                    "returns provenance is %r, not measured evidence; overfit "
                    "detection refuses to certify a constructed series"
                    % (returns_provenance,)
                ),
                checked_at=time.time(),
            )
        # Per-trial statistics — computed once, used by the canonical
        # record below AND by the gate's authoritative DSR check.
        _returns_arr = np.asarray(returns, dtype=float)
        _sharpe_est = (
            float(_returns_arr.mean() / max(_returns_arr.std(), 1e-10))
            if len(_returns_arr) > 1 else 0.0
        )
        _skew = (
            float(np.mean((_returns_arr - _returns_arr.mean()) ** 3) / max(_returns_arr.std() ** 3, 1e-10))
            if len(_returns_arr) > 2 else 0.0
        )
        _kurt = (
            float(np.mean((_returns_arr - _returns_arr.mean()) ** 4) / max(_returns_arr.std() ** 4, 1e-10))
            if len(_returns_arr) > 3 else 3.0
        )

        # --- S.Ξ Trial Ledger: ONE canonical record per validation ---
        # record_trial runs FIRST with the real strategy id, so every DSR
        # computation in this call (the detector's internal hard gate AND
        # the authoritative check below) re-gates with record=False
        # against the same freshly-updated count. This kills two defects
        # at once: (a) the detector deflating against the GLOBAL ledger
        # (9000+ historical trials → DSR≈0 for everything) while the
        # gate's own check deflated per-class — two gates disagreeing
        # inside one call; and (b) the double-counting a second
        # evaluate_dsr(record=True) would cause now that per-class
        # forwarding is consistent end-to-end.
        try:
            self._trial_ledger.record_trial(
                strategy_id=strategy,
                sharpe=_sharpe_est,
                n_observations=max(10, len(returns)),
                skewness=_skew,
                kurtosis=_kurt,
                metadata={"n_strategies_tested": n_strategies_tested},
                strategy_class=strategy_class if strategy_class else "general",
            )
        except Exception as _rec_exc:
            logger.debug("Trial Ledger record skipped: %s", _rec_exc)

        # True per-class count AFTER the canonical record — the count every
        # DSR in this call deflates against (None → global, fail-closed).
        try:
            _class_n = self._trial_ledger.n_trials_for_class(strategy_class)
        except Exception:
            _class_n = 0
        _counted_n = max(n_strategies_tested, _class_n, 1)

        result = detect_overfitting(
            returns=returns,
            is_returns=is_returns,
            oos_returns=oos_returns,
            n_strategies_tested=_counted_n,
            fold_sharpes=fold_sharpes,
            sharpe_trajectory=sharpe_trajectory,
            returns_matrix=returns_matrix,
            # Per-class deflation inside the detector's hard gate — same
            # count, same class, and record_trial=False so the detector
            # does NOT append a second row for the same hypothesis.
            strategy_class=strategy_class,
            record_trial=False,
        )

        # Store
        self._results[strategy] = {
            "risk_level": result.risk_level,
            "grade": result.grade,
            "is_overfit": result.is_overfit,
            "confidence": result.confidence,
            "diagnosis": result.diagnosis,
            "recommendations": result.recommendations,
            "validated_at": time.time(),
            "n_observations": len(returns),
            "n_strategies_tested": n_strategies_tested,
            "strategy_class": strategy_class,
        }
        self._save()

        status = "REJECT" if result.risk_level in BLOCKED_RISK_LEVELS else "PASS"
        reason = (
            f"Overfit gate {status}: risk={result.risk_level} grade={result.grade} "
            f"({result.diagnosis})"
        )

        # --- S.Ξ Trial Ledger: authoritative DSR (re-gate, no new record) ---
        dsr_verdict = "PASS"
        dsr_result = None
        try:
            # Persist for check_strategy's DSR re-check
            self._results[strategy]["est_sharpe"] = _sharpe_est
            self._save()

            # record=False: the canonical trial is already in the ledger
            # (recorded above with the real strategy id). Re-evaluating
            # must not append a phantom second trial for the same
            # hypothesis — that double-count inflated E[max|null] and
            # auto-failed honest strategies.
            dsr_result = self._trial_ledger.evaluate_dsr(
                estimated_sharpe=_sharpe_est,
                n_observations=max(10, len(returns)),
                skewness=_skew,
                kurtosis=_kurt,
                strategy_id=strategy,
                strategy_class=strategy_class,
                metadata={"risk_level": result.risk_level, "grade": result.grade},
                record=False,
            )
            if dsr_result is not None and not dsr_result.is_significant:
                dsr_verdict = "FAIL_NOT_SIGNIFICANT"
                status = "REJECT"
                reason = (
                    f"DSR GATE REJECT: DSR={dsr_result.dsr:.4f} < {DSR_GATE_THRESHOLD} "
                    f"(n_trials={dsr_result.n_trials}, est_sharpe={dsr_result.estimated_sharpe:.3f}) "
                    f"| Overfit: {result.risk_level} grade={result.grade}"
                )
                logger.warning(
                    "DSR GATE REJECT strategy=%s DSR=%.4f n_trials=%d",
                    strategy, dsr_result.dsr, dsr_result.n_trials,
                )
        except Exception as _dsr_exc:
            logger.debug("Trial Ledger DSR check skipped: %s", _dsr_exc)

        gate = GateResult(
            strategy=strategy,
            status=status,
            risk_level=result.risk_level,
            grade=result.grade,
            reason=reason,
            overfit_result=self._results[strategy],
            checked_at=time.time(),
        )

        if status == "REJECT":
            logger.warning(
                "OVERFIT GATE REJECT strategy=%s risk=%s grade=%s diag=%s dsr=%s",
                strategy, result.risk_level, result.grade, result.diagnosis, dsr_verdict,
            )
        else:
            logger.info(
                "OVERFIT GATE PASS strategy=%s risk=%s grade=%s DSR=%s",
                strategy, result.risk_level, result.grade, dsr_verdict,
            )

        return gate

    # ------------------------------------------------------------------
    # Check (read stored result)
    # ------------------------------------------------------------------

    def check_strategy(self, strategy: str) -> GateResult:
        """Check if a strategy has passed the overfit gate.

        Also checks the S.Ξ Trial Ledger DSR gate — strategies with
        insufficient trial-corrected significance are rejected.

        Returns:
            GateResult with PASS, REJECT, or NOT_VALIDATED status
        """
        stored = self._results.get(strategy)
        if stored is None:
            return GateResult(
                strategy=strategy,
                status="NOT_VALIDATED",
                risk_level="UNKNOWN",
                grade="N/A",
                reason=f"Strategy '{strategy}' has never been validated against overfit detection",
                checked_at=time.time(),
            )

        risk_level = stored.get("risk_level", "UNKNOWN")
        grade = stored.get("grade", "N/A")
        is_overfit = stored.get("is_overfit", True)

        # --- S.Ξ DSR gate check (scoped to the stored hypothesis class) ---
        # record=False: this is a RE-GATE of an already-recorded hypothesis,
        # not a new trial. Recording here appended a phantom trial on every
        # check and inflated the multiple-testing bar.
        dsr_result = None
        try:
            dsr_result = self._trial_ledger.evaluate_dsr(
                estimated_sharpe=float(stored.get("est_sharpe", 0.0) or 0.0),
                n_observations=max(10, int(stored.get("n_observations", 30) or 30)),
                strategy_id=strategy,
                strategy_class=stored.get("strategy_class"),
                record=False,
            )
        except Exception:
            pass

        if dsr_result is not None and not dsr_result.is_significant:
            return GateResult(
                strategy=strategy,
                status="REJECT",
                risk_level=risk_level,
                grade=grade,
                reason=(
                    f"DSR GATE REJECT: DSR={dsr_result.dsr:.4f} < {DSR_GATE_THRESHOLD} "
                    f"(n_trials={dsr_result.n_trials}, est_sharpe={dsr_result.estimated_sharpe:.3f})"
                ),
                overfit_result=stored,
                checked_at=time.time(),
            )

        if risk_level in BLOCKED_RISK_LEVELS or is_overfit:
            status = "REJECT"
            reason = (
                f"Overfit gate REJECT: risk={risk_level} grade={grade} "
                f"(validated {stored.get('validated_at', 'unknown')})"
            )
        else:
            status = "PASS"
            reason = (
                f"Overfit gate PASS: risk={risk_level} grade={grade} "
                f"(validated {stored.get('validated_at', 'unknown')})"
            )

        return GateResult(
            strategy=strategy,
            status=status,
            risk_level=risk_level,
            grade=grade,
            reason=reason,
            overfit_result=stored,
            checked_at=time.time(),
        )

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def get_all_strategies(self) -> Dict[str, Dict[str, Any]]:
        """Return all stored strategy validation results."""
        return dict(self._results)

    def get_rejected_strategies(self) -> List[str]:
        """Return list of strategies blocked by the overfit gate."""
        return [
            name for name, data in self._results.items()
            if data.get("risk_level") in BLOCKED_RISK_LEVELS or data.get("is_overfit")
        ]

    def get_passed_strategies(self) -> List[str]:
        """Return list of strategies that passed the overfit gate."""
        return [
            name for name, data in self._results.items()
            if data.get("risk_level") not in BLOCKED_RISK_LEVELS
            and not data.get("is_overfit")
        ]

    def reset_strategy(self, strategy: str) -> bool:
        """Remove a strategy's validation result (forces re-validation)."""
        if strategy in self._results:
            del self._results[strategy]
            self._save()
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """Return full gate status summary."""
        all_strats = list(self._results.keys())
        rejected = self.get_rejected_strategies()
        passed = self.get_passed_strategies()
        return {
            "total_validated": len(all_strats),
            "passed": len(passed),
            "rejected": len(rejected),
            "not_validated_count": 0,  # checked via check_strategy()
            "blocked_risk_levels": list(BLOCKED_RISK_LEVELS),
            "strategies": {
                name: {
                    "risk_level": data.get("risk_level"),
                    "grade": data.get("grade"),
                    "validated_at": data.get("validated_at"),
                }
                for name, data in self._results.items()
            },
        }
