"""Improvement Pipeline — automated low-risk self-improvement cycle."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class ImprovementPipeline:
    def __init__(self, base_dir: Optional[str] = None):
        self._base_dir = Path(base_dir) if base_dir else Path(".")
        self._cycle_count = 0
        self._last_run = 0.0
        self._improvements_applied = 0

    def run_cycle(self, cycle_num: int = 0, cycle_count: Optional[int] = None, **kwargs: Any) -> Dict[str, Any]:
        if cycle_count is not None and cycle_num == 0:
            cycle_num = cycle_count
        self._cycle_count += 1
        self._last_run = time.time()

        results = {
            "cycle": self._cycle_count,
            "improvements_applied": 0,
            "checks": {},
        }

        # 1. Check parameter stability
        # core.parameter_stability exposes run_parameter_stability(grids, strategy_fn)
        # and persists no status file, so there is nothing cheap to poll here.
        try:
            import core.parameter_stability as _ps
            results["checks"]["parameter_stability"] = {
                "status": "no_persisted_status",
                "available": hasattr(_ps, "run_parameter_stability"),
                "note": "on-demand only: run_parameter_stability(grids, strategy_fn)",
            }
        except Exception as e:
            results["checks"]["parameter_stability"] = {"error": str(e)[:100]}

        # 2. Check overfit gate
        try:
            from core.overfit_gate import OverfitGate
            gate = OverfitGate()
            status = gate.get_status() if hasattr(gate, 'get_status') else {}
            results["checks"]["overfit_gate"] = status
        except Exception as e:
            results["checks"]["overfit_gate"] = {"error": str(e)[:100]}

        # 3. Check return diagnostics (computed on-demand from closed journal trades).
        # ReturnDiagnostics is a value object; diagnose_returns() is the factory.
        try:
            import sqlite3

            from core.return_diagnostics import diagnose_returns

            _db = str(self._base_dir / "trading_state.db")
            try:
                from research.research_validation import broker_reconciled_tickets
            except ImportError:
                from research.research_validation import broker_reconciled_tickets
            _tickets = sorted(broker_reconciled_tickets(_db))
            rows = []
            if _tickets:
                _marks = ",".join("?" for _ in _tickets)
                con = sqlite3.connect(_db, timeout=2)
                rows = con.execute(
                    "SELECT gross_pnl, risk_amount FROM trades "
                    "WHERE closed_at IS NOT NULL AND risk_amount > 0 "
                    "AND ticket IN (%s) "
                    "ORDER BY closed_at DESC LIMIT 200" % _marks,
                    _tickets,
                ).fetchall()
                con.close()

            r_returns = [float(p) / float(r) for p, r in reversed(rows)
                         if p is not None and r]
            if len(r_returns) >= 30:
                diag = diagnose_returns(
                    r_returns, max_lag=min(20, max(5, len(r_returns) // 4)))
                diagnostics = {
                    "n_observations": diag.n_observations,
                    "is_white_noise": diag.is_white_noise,
                    "has_linear_dependence": diag.has_linear_dependence,
                    "has_volatility_clustering": diag.has_volatility_clustering,
                    "durbin_watson": round(diag.durbin_watson, 4),
                    "runs_test_p_value": round(diag.runs_test_p_value, 4),
                    "summary": diag.summary,
                }
            else:
                diagnostics = {"status": "insufficient_data",
                               "closed_trades": len(r_returns)}
            results["checks"]["return_diagnostics"] = diagnostics
        except Exception as e:
            results["checks"]["return_diagnostics"] = {"error": str(e)[:100]}

        results["improvements_applied"] = self._improvements_applied
        return results

    def get_status(self) -> Dict[str, Any]:
        return {
            "total_cycles": self._cycle_count,
            "last_run": self._last_run,
            "improvements_applied": self._improvements_applied,
        }
