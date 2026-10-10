"""Automated Strategy Promotion System

Handles the automated promotion of strategies through their lifecycle:
CANDIDATE → VALIDATING → PAPER_PROVEN → DEMO_PROVEN → DEPLOYED

This system:
1. Automatically runs validation gates (DSR, CPCV, walk-forward)
2. Automatically promotes when all gates pass
3. Automatically freezes when degradation is detected
4. Logs all promotions for audit

IMPORTANT: automation graduates a strategy only as far as
READY_FOR_DEPLOYMENT. DEPLOYED carries live trading authority and is never
written by this module - ``_update_strategy_status`` raises unless an
operator actor performs the transition, and no automated transition table
lists DEPLOYED as a reachable target.

Usage:
    promoter = AutoPromoter()
    result = promoter.promote_strategy("strategy_v1")
    if result["promoted"]:
        print(f"Strategy promoted to {result['new_status']}")
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("AutoPromotion")

# Statuses that carry live trading authority. The research layer may never
# write one of these on its own: graduating to it is an operator action
# with a recorded sign-off, not a gate result.
LIVE_AUTHORITY_STATUSES = frozenset({"DEPLOYED"})


class AutoPromoter:
    """Automated strategy promotion system."""

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or str(Path(__file__).parent.parent)
        self.db_path = os.path.join(self.base_dir, "trading_state.db")
        self.state_path = os.path.join(self.base_dir, "artifacts", "promotion_state.json")
        self.state = self._load_state()

        # Promotion thresholds (from research_validation.py)
        self.min_trades = 100
        self.min_profit_factor = 1.2
        self.min_expectancy_r = 0.0
        self.max_ruin_probability = 0.25
        self.max_drawdown_p95 = 0.35

    def _load_state(self) -> Dict[str, Any]:
        """Load promotion state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "strategies": {},
            "promotion_history": [],
            "last_check": None,
        }

    def _save_state(self) -> None:
        """Save promotion state."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save promotion state: %s", exc)

    def promote_strategy(self, strategy_key: str) -> Dict[str, Any]:
        """Attempt to promote a strategy through its lifecycle."""
        log.info("Attempting promotion for strategy: %s", strategy_key)

        result = {
            "strategy_key": strategy_key,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "promoted": False,
            "old_status": None,
            "new_status": None,
            "reason": "",
            "gates": {},
        }

        try:
            # Get current status
            current = self._get_strategy_status(strategy_key)
            if not current:
                result["reason"] = "strategy_not_found"
                return result

            result["old_status"] = current["status"]

            # Check what transitions are allowed
            allowed = self._get_allowed_transitions(current["status"])
            if not allowed:
                result["reason"] = f"no_allowed_transitions_from_{current['status']}"
                return result

            # Run validation gates
            gates = self._run_validation_gates(strategy_key, current)
            result["gates"] = gates

            # Determine if we can promote
            if all(gates.get(k, {}).get("passed", False) for k in gates):
                # All gates passed - promote to next status
                new_status = self._get_next_status(current["status"])
                if new_status:
                    # For DEPLOYED or READY_FOR_DEPLOYMENT transition, require operator approval
                    if new_status in ("DEPLOYED", "READY_FOR_DEPLOYMENT"):
                        # Do NOT self-promote to READY_FOR_DEPLOYMENT. The old
                        # code wrote the state here while also requesting operator
                        # approval, so every gated strategy reached
                        # READY_FOR_DEPLOYMENT unattended - that is the path the
                        # 11 unbacked rows came from. Automation now graduates only
                        # to PAPER_PROVEN; the operator performs the transition.
                        result["promoted"] = False
                        result["reason"] = "requires_operator_approval"
                        result["new_status"] = None
                        # Auto-notify: enqueue alert for operator
                        try:
                            from operations.alert_outbox import enqueue as _alert_enqueue
                            _alert_enqueue(
                                category="STRATEGY_PROMOTION",
                                severity="INFO",
                                title=f"Strategy {strategy_key} ready for deployment",
                                body=json.dumps({
                                    "strategy": strategy_key,
                                    "previous_status": current["status"],
                                    "new_status": "READY_FOR_DEPLOYMENT",
                                    "gates_passed": list(gates.keys()),
                                    "action_required": "Operator must approve deployment to live",
                                }, default=str),
                                source="auto_promotion",
                            )
                        except Exception as _alert_exc:
                            log.debug("Failed to enqueue promotion alert: %s", _alert_exc)
                        try:
                            from operations.strategy_factory import get_factory
                            get_factory().request_approval({
                                "kind": "PROMOTION",
                                "title": f"Promote {strategy_key} to LIVE DEPLOYED",
                                "detail_lines": [
                                    f"Strategy {strategy_key} passed all validation gates",
                                    f"Gates: {list(gates.keys())}",
                                    "Requires operator sign-off for live execution",
                                ],
                                "signature_required": True,
                                "strategy_id": strategy_key,
                            })
                        except Exception as _appr_exc:
                            log.debug("Failed to request factory approval: %s", _appr_exc)
                    else:
                        self._update_strategy_status(strategy_key, new_status, gates)
                        result["promoted"] = True
                        result["new_status"] = new_status
                        result["reason"] = "all_gates_passed"

                    # Log promotion
                    self._log_promotion(strategy_key, current["status"], new_status, gates)
            else:
                # Some gates failed
                failed_gates = [k for k, v in gates.items() if not v.get("passed", False)]
                result["reason"] = f"gates_failed: {', '.join(failed_gates)}"
                # --- Honest verdict journal: gate rejections were previously
                # silent no-ops. Every evaluated-but-rejected promotion now
                # names its failed gates in the journal. Thresholds unchanged.
                try:
                    from operations.event_bridge import emit as _gate_emit
                    _gate_emit("IMPROVEMENT_PROMOTION_GATE", {
                        "strategy": strategy_key,
                        "status": current["status"],
                        "verdict": "GATES_FAILED",
                        "failed_gates": failed_gates,
                        "gate_details": {
                            k: {
                                "passed": bool(v.get("passed")),
                                "reason": str(v.get("reason", ""))[:120],
                            }
                            for k, v in gates.items() if not v.get("passed", False)
                        },
                    }, correlation_id=f"promo-{strategy_key}")
                except Exception:
                    pass

        except Exception as exc:
            result["reason"] = f"error: {exc}"
            log.error("Promotion failed: %s", exc)

        self.state["last_check"] = result["timestamp"]
        self._save_state()

        return result

    def _get_strategy_status(self, strategy_key: str) -> Optional[Dict[str, Any]]:
        """Get current strategy status from registry or strategy_lifecycle."""
        try:
            if not os.path.exists(self.db_path):
                return None

            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row

            # Check strategy_lifecycle table first
            row_sl = con.execute(
                "SELECT * FROM strategy_lifecycle WHERE setup_id=?", (strategy_key,)
            ).fetchone()
            if row_sl:
                st = row_sl["state"]
                ver = row_sl["version_fingerprint"] or "1.0.0"
                created = row_sl["updated_at"]
                con.close()
                return {
                    "status": st,
                    "version": ver,
                    "kind": "strategy",
                    "created_at": created,
                    "metrics": {},
                }

            # Check model_registry for strategy status
            row = con.execute(
                "SELECT * FROM model_registry WHERE model_key=? ORDER BY created_at DESC LIMIT 1",
                (strategy_key,)
            ).fetchone()

            con.close()

            if row:
                metrics = {}
                if "metrics" in row.keys() and row["metrics"]:
                    try:
                        metrics = json.loads(row["metrics"])
                    except Exception:
                        pass
                return {
                    "status": row["status"],
                    "version": row["version"],
                    "kind": row["kind"],
                    "created_at": row["created_at"],
                    "metrics": metrics,
                }

            # Default to CANDIDATE if not found
            return {"status": "CANDIDATE", "version": "0.0.0", "kind": "unknown", "created_at": None}

        except Exception as exc:
            log.debug("Failed to get strategy status: %s", exc)
            return None

    def _get_allowed_transitions(self, current_status: str) -> List[str]:
        """Get allowed transitions from current status."""
        transitions = {
            "CANDIDATE": ["VALIDATING"],
            "VALIDATING": ["PAPER_PROVEN", "REJECTED"],
            "PAPER_PROVEN": ["DEMO_PROVEN", "REJECTED"],
            # DEMO_PROVEN previously listed DEPLOYED as an automated target.
            # The only thing that blocked it was a later branch in
            # promote_strategy, so the table itself advertised a route to
            # live execution. Automation now stops at READY_FOR_DEPLOYMENT.
            "DEMO_PROVEN": ["READY_FOR_DEPLOYMENT", "REJECTED"],
            "DEPLOYED": ["DECAYING"],
            # A decaying strategy is being demoted. Returning it straight to
            # DEPLOYED reversed the demotion without re-running any evidence.
            "DECAYING": ["FROZEN"],
            "FROZEN": [],
            "REJECTED": [],
            # Operator-only: leaving this empty is what makes the transition
            # unreachable from automation.
            "READY_FOR_DEPLOYMENT": [],
        }
        return transitions.get(current_status, [])

    def _get_next_status(self, current_status: str) -> Optional[str]:
        """Get the next status in the promotion chain."""
        chain = {
            "CANDIDATE": "VALIDATING",
            "VALIDATING": "PAPER_PROVEN",
            "PAPER_PROVEN": "DEMO_PROVEN",
            "DEMO_PROVEN": "READY_FOR_DEPLOYMENT",
            # READY_FOR_DEPLOYMENT is the end of the automated chain. The
            # DEPLOYED edge used to be here, which meant a strategy one gate
            # away from live had a machine-chosen next step straight into it.
        }
        return chain.get(current_status)

    def _run_validation_gates(
        self,
        strategy_key: str,
        current: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run all validation gates."""
        gates = {}

        # Gate 1: Sample size
        gates["sample_size"] = self._check_sample_size(strategy_key)

        # Gate 2: DSR
        gates["dsr"] = self._check_dsr(strategy_key)

        # Gate 3: Walk-forward
        gates["walkforward"] = self._check_walkforward(strategy_key)

        # Gate 4: Live performance (if applicable)
        if current["status"] in ("PAPER_PROVEN", "DEMO_PROVEN"):
            gates["live_performance"] = self._check_live_performance(strategy_key)

        # Gate 5: No divergence (if applicable)
        if current["status"] in ("PAPER_PROVEN", "DEMO_PROVEN"):
            gates["no_divergence"] = self._check_no_divergence(strategy_key)

        # Gate 6: P01 Judge Rubric (Vu et al. 2026)
        gates["judge_rubric"] = self._check_judge_rubric(strategy_key, current)

        # Gate 7: P08 OpenQuant Benchmark Dominance (Reid et al. 2026 Eq. 6)
        gates["openquant_admissibility"] = self._check_openquant_admissibility(strategy_key, current)

        return gates

    def _admissible_trades(self, con, strategy_key: str, columns: str, extra: str = "") -> list:
        """Rows from `trades` that a gate may actually grade.

        Only tickets the journal independently records as submitted or
        reconciled count. Reading this table directly is what let a synthetic
        fixture (one distinct entry/stop/TP, labelled source="broker") drive
        DSR, walk-forward and live-performance gates.
        """
        from research.research_validation import broker_reconciled_tickets
        tickets = broker_reconciled_tickets(self.db_path)
        if not tickets:
            return []
        # Without a ticket column there is no way to tie a row to broker
        # evidence, so nothing is admissible. Fail closed rather than raise.
        cols = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
        if "ticket" not in cols:
            return []
        marks = ",".join("?" for _ in tickets)
        sql = ("SELECT %s FROM trades WHERE closed_at IS NOT NULL AND strategy = ? "
               "AND ticket IN (%s) %s" % (columns, marks, extra))
        return con.execute(sql, (strategy_key, *sorted(tickets))).fetchall()

    def _no_evidence(self) -> dict:
        return {"passed": False, "reason": "no_broker_reconciled_evidence",
                "note": "journal records no submitted/reconciled broker tickets; "
                        "no trade row may be graded as evidence"}

    def _gate_unavailable(self, reason: str, detail: str = "") -> Dict[str, Any]:
        """A gate that could not be evaluated has NOT passed.

        Every exception handler in this file used to answer ``{"passed": True}``.
        That is the fail-open shape: a module that failed to import, an empty
        trial ledger, or a missing monitor all read as a green light, so an
        unverified strategy advanced on the strength of a crash. A gate that
        cannot be evaluated is a refusal, not a pass.
        """
        return {"passed": False, "reason": reason, "note": detail or reason}

    def _check_sample_size(self, strategy_key: str) -> Dict[str, Any]:
        """Check if we have enough trades."""
        try:
            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row

            rows = self._admissible_trades(con, strategy_key, "COUNT(*) as cnt")
            con.close()

            count = rows[0]["cnt"] if rows else 0
            return {
                "passed": count >= self.min_trades,
                "count": count,
                "required": self.min_trades,
            }

        except Exception as exc:
            return {"passed": False, "error": str(exc)}

    def _check_dsr(self, strategy_key: str) -> Dict[str, Any]:
        """Check Deflated Sharpe Ratio via trial ledger."""
        try:
            from research.backtest_dsr_integrator import BacktestDSRIntegrator
            integrator = BacktestDSRIntegrator(strategy_class=strategy_key)

            # Get all previous gate results to compute DSR
            gate_results = integrator.get_all_gate_results(limit=100)
            if not gate_results:
                # An empty trial ledger means the number of trials this
                # strategy has actually been tested against is unknown, so
                # the deflation factor is unknown. Assuming one trial is
                # how an unbacked strategy claims the strictest DSR.
                return self._gate_unavailable(
                    "trial_ledger_empty",
                    "No trial history: DSR deflation factor is unknown, so the "
                    "deflated Sharpe cannot be computed and must not be assumed")

            # Get most recent Sharpe from journal trades
            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row
            rows = self._admissible_trades(
                con, strategy_key, "r_multiple",
                "AND r_multiple IS NOT NULL ORDER BY closed_at DESC LIMIT 200")
            con.close()

            if not rows:
                return self._no_evidence()
            if len(rows) < 20:
                # Fail closed. "Not enough data" is unknown, not a pass - the
                # previous vacuous True is how unverified strategies advanced.
                return {"passed": False, "reason": "insufficient_trades",
                        "note": "Insufficient admissible trades for DSR check",
                        "count": len(rows)}

            import numpy as np
            returns = np.array([float(r["r_multiple"]) for r in rows])
            sharpe = float(np.mean(returns) / max(np.std(returns), 1e-8) * np.sqrt(252))

            # Validate with DSR gate
            report = integrator.validate_backtest(
                strategy_id=strategy_key,
                sharpe=sharpe,
                n_observations=len(returns),
                returns=returns.tolist(),
            )

            return {
                "passed": report.passed_gate,
                "dsr": report.dsr,
                "raw_sharpe": report.raw_sharpe,
                "n_trials": report.n_trials,
                "verdict": report.verdict,
            }

        except Exception as exc:
            log.warning("DSR gate unavailable, failing closed: %s", exc)
            return self._gate_unavailable("dsr_unavailable", str(exc))

    def _check_walkforward(self, strategy_key: str) -> Dict[str, Any]:
        """Check walk-forward analysis via purged CV."""
        try:
            from research.bayesian_walk_forward_sandbox import BayesianWalkForwardSandbox
            sandbox = BayesianWalkForwardSandbox()

            # Get recent trades for walk-forward analysis
            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row
            rows = self._admissible_trades(
                con, strategy_key, "r_multiple, symbol, opened_at",
                "AND r_multiple IS NOT NULL ORDER BY closed_at DESC LIMIT 200")
            con.close()

            if not rows:
                return self._no_evidence()
            if len(rows) < 50:
                return {"passed": False, "reason": "insufficient_trades",
                        "note": "Insufficient admissible trades for walk-forward",
                        "count": len(rows)}

            import numpy as np
            returns = np.array([float(r["r_multiple"]) for r in rows])

            # Run walk-forward with 3 folds
            result = sandbox.run_walk_forward(
                returns=returns,
                n_folds=3,
                purge_window=5,
                strategy_id=strategy_key,
            )

            oos_sharpe = result.get("oos_sharpe", 0)
            is_sharpe = result.get("is_sharpe", 0)
            degradation = abs(is_sharpe - oos_sharpe) / max(abs(is_sharpe), 0.01)

            return {
                "passed": oos_sharpe > 0.5 and degradation < 0.5,
                "oos_sharpe": oos_sharpe,
                "is_sharpe": is_sharpe,
                "degradation_pct": round(degradation, 4),
                "n_folds": result.get("n_folds", 0),
            }

        except Exception as exc:
            log.warning("Walk-forward gate unavailable, failing closed: %s", exc)
            return self._gate_unavailable("walkforward_unavailable", str(exc))

    def _check_live_performance(self, strategy_key: str) -> Dict[str, Any]:
        """Check live/demo performance."""
        try:
            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row

            trades = self._admissible_trades(
                con, strategy_key, "gross_pnl, risk_amount",
                "ORDER BY closed_at DESC LIMIT 50")

            con.close()

            if len(trades) < 10:
                return {"passed": False, "reason": "insufficient_trades", "count": len(trades)}

            # Compute metrics
            r_multiples = [
                (t["gross_pnl"] or 0) / max(t["risk_amount"] or 1, 0.01)
                for t in trades
            ]
            win_rate = sum(1 for r in r_multiples if r > 0) / len(r_multiples)
            avg_r = sum(r_multiples) / len(r_multiples)

            # Check thresholds
            passed = win_rate >= 0.45 and avg_r > 0

            return {
                "passed": passed,
                "win_rate": round(win_rate, 4),
                "avg_r": round(avg_r, 4),
                "trade_count": len(trades),
            }

        except Exception as exc:
            return {"passed": False, "error": str(exc)}

    def _check_no_divergence(self, strategy_key: str) -> Dict[str, Any]:
        """Check for live-vs-backtest divergence."""
        try:
            from research.divergence_monitor import DivergenceMonitor
            monitor = DivergenceMonitor(self.base_dir)
            # Check most recent symbol
            result = monitor.check_divergence("EURUSD")
            return {
                "passed": not result.get("alert", False),
                "alert": result.get("alert", False),
                "severity": result.get("severity", "OK"),
            }
        except Exception as exc:
            log.warning("Divergence gate unavailable, failing closed: %s", exc)
            return self._gate_unavailable("divergence_unavailable", str(exc))

    def _check_judge_rubric(self, strategy_key: str, current: Dict[str, Any]) -> Dict[str, Any]:
        """Check P01 Judge Rubric (Vu et al. 2026) for formula-based alphas."""
        try:
            from research.alpha_miner.alpha_judge import judge_candidate
            metrics = current.get("metrics") or {}
            formula = metrics.get("formula") or metrics.get("expression")
            if not formula:
                # Not applicable is not the same as passed. Returning True here
                # let an ungraded strategy satisfy the gate.
                return {"passed": False, "reason": "not_applicable",
                        "note": "no formula/expression to judge"}
            score = judge_candidate(str(formula))
            return {
                "passed": score.accepted,
                "score": round(score.aggregate, 2),
                "threshold": 7.5,
                "feedback": score.feedback,
            }
        except Exception as exc:
            log.warning("Judge rubric gate unavailable, failing closed: %s", exc)
            return self._gate_unavailable("judge_rubric_unavailable", str(exc))

    def _check_openquant_admissibility(self, strategy_key: str, current: Dict[str, Any]) -> Dict[str, Any]:
        """Check P08 OpenQuant Benchmark Dominance gates (Reid et al. 2026 Eq. 6)."""
        try:
            from research.openquant_eval import check_admissibility, composite_score
            metrics = current.get("metrics") or {}
            oq_keys = {"jensen_alpha", "excess_return", "sharpe", "profit_factor", "max_drawdown"}
            if not any(k in metrics for k in oq_keys):
                # Missing benchmark metrics must not read as admissibility.
                return {"passed": False, "reason": "no_benchmark_metrics",
                        "note": "no OpenQuant benchmark metrics recorded"}
            verdict = check_admissibility(metrics)
            c_score = composite_score(metrics) if verdict.admissible else 0.0
            return {
                "passed": verdict.admissible,
                "failed_gates": verdict.failed,
                "composite_score": c_score,
            }
        except Exception as exc:
            log.warning("OpenQuant gate unavailable, failing closed: %s", exc)
            return self._gate_unavailable("openquant_unavailable", str(exc))

    def _update_strategy_status(self, strategy_key: str, new_status: str,
                                gates: Optional[Dict[str, Any]] = None,
                                *, actor: str = "auto_promoter") -> None:
        """Update strategy status in registry and strategy_lifecycle.

        `gates` is persisted into the lifecycle audit trail. Writing "{}" made
        every automated transition unauditable: the gate results existed only
        in artifacts/promotion_state.json, never in the tables a reviewer reads.

        Automation may not grant live authority. This method used to write
        whatever status it was handed with no transition and no actor check, so
        any caller holding an AutoPromoter could put a strategy into DEPLOYED
        without passing a single gate. The only thing separating the research
        layer from live execution was a convention, so the check is now a raise.
        """
        if new_status in LIVE_AUTHORITY_STATUSES and actor != "operator":
            raise PermissionError(
                f"refusing to write live-authority status {new_status!r} for "
                f"{strategy_key!r} as actor {actor!r}: "
                f"{sorted(LIVE_AUTHORITY_STATUSES)} is an operator transition "
                f"and requires an operator sign-off")
        try:
            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row
            now = datetime.now(timezone.utc).isoformat()

            gate_map = gates or {}
            passed = sorted(k for k, v in gate_map.items() if v.get("passed", False))
            failed = sorted(k for k, v in gate_map.items() if not v.get("passed", False))
            evidence = json.dumps({
                "actor": actor,
                "gates": gate_map,
                "gates_passed": passed,
                "gates_failed": failed,
            }, sort_keys=True, default=str)
            reason = ("automated gate evaluation: %d/%d gates passed%s"
                      % (len(passed), len(passed) + len(failed),
                         (": " + ", ".join(failed)) if failed else ""))

            # 1. Update strategy_lifecycle table
            try:
                row = con.execute("SELECT setup_id, state FROM strategy_lifecycle WHERE setup_id=?", (strategy_key,)).fetchone()
                prev_state = row["state"] if row else None
                if row:
                    con.execute(
                        "UPDATE strategy_lifecycle SET state=?, updated_at=?, updated_by=?, reason=? WHERE setup_id=?",
                        (new_status, now, actor, reason, strategy_key)
                    )
                else:
                    con.execute(
                        "INSERT INTO strategy_lifecycle (setup_id, state, updated_at, updated_by, reason) VALUES (?, ?, ?, ?, ?)",
                        (strategy_key, new_status, now, actor, reason)
                    )
                # Same no-op guard as operations.strategy_lifecycle.transition():
                # a state that is already correct is not a transition, so it must
                # not be journaled. Logging 104 READY_FOR_DEPLOYMENT ->
                # READY_FOR_DEPLOYMENT rows buried the real history in noise.
                if prev_state != new_status:
                    con.execute(
                        "INSERT INTO strategy_lifecycle_history (setup_id, from_state, to_state, actor, reason, evidence, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (strategy_key, prev_state, new_status, actor,
                         "promoted via validation gates - " + reason, evidence, now)
                    )
            except Exception as _sl_err:
                log.warning("Strategy lifecycle sync failed: %s", _sl_err)

            # 2. Update model_registry table
            try:
                con.execute(
                    "INSERT OR REPLACE INTO model_registry (model_key, version, kind, status, metrics, created_at, reviewed_at, reviewed_by, review_note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (strategy_key, "auto", "strategy", new_status, evidence, now, now,
                     actor, reason)
                )
            except Exception as _mr_err:
                log.warning("Model registry update failed: %s", _mr_err)

            con.commit()
            con.close()

        except Exception as exc:
            # A write that fails here leaves the registry claiming a status
            # the database does not hold. It must not be a debug line.
            log.warning("Failed to update strategy status: %s", exc)

    def _log_promotion(
        self,
        strategy_key: str,
        old_status: str,
        new_status: str,
        gates: Dict[str, Any],
    ) -> None:
        """Log promotion to history."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy_key": strategy_key,
            "old_status": old_status,
            "new_status": new_status,
            "gates": gates,
        }
        self.state["promotion_history"].append(entry)
        # Keep last 50 promotions
        self.state["promotion_history"] = self.state["promotion_history"][-50:]

        log.info("Strategy %s promoted: %s → %s", strategy_key, old_status, new_status)

    def check_all_strategies(self) -> List[Dict[str, Any]]:
        """Check and promote all strategies from strategy_lifecycle, strategy registry, and model_registry."""
        results = []

        try:
            keys = set()
            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row

            try:
                for row in con.execute("SELECT setup_id FROM strategy_lifecycle").fetchall():
                    if row["setup_id"]:
                        keys.add(row["setup_id"])
            except Exception:
                pass

            try:
                for row in con.execute("SELECT DISTINCT model_key FROM model_registry").fetchall():
                    if row["model_key"]:
                        keys.add(row["model_key"])
            except Exception:
                pass

            con.close()

            # Also check file-based strategy registry
            try:
                from strategies.registry import StrategyRegistry
                sr = StrategyRegistry()
                entries = sr.list_all() if hasattr(sr, "list_all") else (sr.all() if hasattr(sr, "all") else [])
                for entry in entries:
                    ename = getattr(entry, "name", None) or (entry.get("name") if isinstance(entry, dict) else None)
                    if ename:
                        keys.add(ename)
            except Exception:
                pass

            # Also check strategy factory
            try:
                from operations.strategy_factory import get_factory
                sf = get_factory()
                for s in sf.all():
                    sid = s.get("id") or s.get("name")
                    if sid:
                        keys.add(sid)
            except Exception:
                pass

            for strategy_key in sorted(keys):
                result = self.promote_strategy(strategy_key)
                results.append(result)

        except Exception as exc:
            log.error("Failed to check strategies: %s", exc)

        return results

    def get_promotion_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent promotion history."""
        return self.state.get("promotion_history", [])[-limit:]

    def get_status(self) -> Dict[str, Any]:
        """Get promoter status."""
        return {
            "last_check": self.state.get("last_check"),
            "total_promotions": len(self.state.get("promotion_history", [])),
            "strategies_tracked": len(self.state.get("strategies", {})),
        }


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Automated Strategy Promotion")
    parser.add_argument("--strategy", type=str, help="Strategy key to promote")
    parser.add_argument("--all", action="store_true", help="Check all strategies")
    parser.add_argument("--history", action="store_true", help="Show promotion history")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    promoter = AutoPromoter()

    if args.history:
        history = promoter.get_promotion_history()
        print(json.dumps(history, indent=2, default=str))
    elif args.all:
        results = promoter.check_all_strategies()
        for r in results:
            status = "PROMOTED" if r["promoted"] else "NO_ACTION"
            print(f"{r['strategy_key']}: {status} - {r['reason']}")
    elif args.strategy:
        result = promoter.promote_strategy(args.strategy)
        print(json.dumps(result, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
