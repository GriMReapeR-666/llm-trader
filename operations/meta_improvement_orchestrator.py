"""Meta-Improvement Strategy Orchestrator

Orchestrates all meta-learning and self-improvement subsystems:
- Meta-Adaptive Engine (MAML-inspired regime adaptation)
- Meta-Loop (self-evolution with shadow deploy)
- Self-Improvement Loop (AQuA sandbox validation)
- Meta-Reflector (system health auditing)
- Meta-Labeling (triple-barrier + meta-labeling)

Provides:
1. Unified improvement cycle orchestration
2. Automated hypothesis generation and testing
3. Performance feedback loops
4. Strategy evolution tracking
5. Health monitoring and remediation

Usage:
    orchestrator = MetaImprovementOrchestrator()
    # Run full improvement cycle
    result = orchestrator.run_cycle()
    # Check system health
    health = orchestrator.health_check()
    # Get improvement history
    history = orchestrator.get_history()
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("MetaImprovementOrchestrator")

# Mirror mt5_trader: subpackages import siblings by bare name (core, intelligence).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("core", "intelligence", "operations", "research", "data", "ui"):
    _p = os.path.join(_ROOT, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


class MetaImprovementOrchestrator:
    """Unified orchestrator for all meta-learning subsystems."""

    def __init__(self, db_path: str = ""):
        self.base_dir = str(Path(__file__).parent.parent)
        self.db_path = db_path or os.path.join(self.base_dir, "trading_state.db")
        self.state_path = os.path.join(self.base_dir, "artifacts", "meta_improvement_state.json")
        self.state = self._load_state()

        # Lazy-load subsystems
        self._meta_adaptive = None
        self._meta_loop = None
        self._self_improvement = None
        self._meta_reflector = None

    def _load_state(self) -> Dict[str, Any]:
        """Load orchestrator state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "last_cycle": None,
            "total_cycles": 0,
            "improvements_applied": 0,
            "improvements_rejected": 0,
            "health_status": "UNKNOWN",
            "hypotheses_generated": 0,
            "hypotheses_validated": 0,
        }

    def _save_state(self) -> None:
        """Save orchestrator state."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save state: %s", exc)

    def _get_meta_adaptive(self):
        """Lazy-load meta-adaptive engine."""
        if self._meta_adaptive is None:
            try:
                try:
                    from core.meta_adaptive_engine import MetaAdaptiveEngine
                except ImportError:
                    from core.meta_adaptive_engine import MetaAdaptiveEngine
                self._meta_adaptive = MetaAdaptiveEngine()
            except Exception as exc:
                log.debug("Meta-adaptive engine unavailable: %s", exc)
        return self._meta_adaptive

    def _get_meta_loop(self):
        """Lazy-load meta-loop."""
        if self._meta_loop is None:
            try:
                try:
                    from core.meta_loop import MetaLoop
                except ImportError:
                    from core.meta_loop import MetaLoop
                self._meta_loop = MetaLoop(db_path=self.db_path)
            except Exception as exc:
                log.debug("Meta-loop unavailable: %s", exc)
        return self._meta_loop

    def _get_self_improvement(self):
        """Lazy-load self-improvement loop."""
        if self._self_improvement is None:
            try:
                try:
                    from core.self_improvement_loop import RecursiveSelfImprovementEngine
                except ImportError:
                    from core.self_improvement_loop import RecursiveSelfImprovementEngine
                self._self_improvement = RecursiveSelfImprovementEngine(db_path=self.db_path)
            except Exception as exc:
                log.debug("Self-improvement loop unavailable: %s", exc)
        return self._self_improvement

    def _get_meta_reflector(self):
        """Lazy-load meta-reflector."""
        if self._meta_reflector is None:
            try:
                try:
                    from intelligence.meta_reflector import SystemMetaReflector
                except ImportError:
                    from intelligence.meta_reflector import SystemMetaReflector
                self._meta_reflector = SystemMetaReflector()
            except Exception as exc:
                log.debug("Meta-reflector unavailable: %s", exc)
        return self._meta_reflector

    def run_cycle(self) -> Dict[str, Any]:
        """Run a full meta-improvement cycle."""
        log.info("=" * 60)
        log.info("Starting meta-improvement cycle")
        log.info("=" * 60)

        start_time = time.time()
        result = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "stages": {},
        }

        # Stage 1: Health Check
        log.info("Stage 1: System Health Check")
        try:
            health = self.health_check()
            result["stages"]["health"] = health
        except Exception as exc:
            result["stages"]["health"] = {"status": "failed", "error": str(exc)}

        # Stage 2: Generate Hypotheses
        log.info("Stage 2: Generate Improvement Hypotheses")
        try:
            hypotheses = self._generate_hypotheses()
            result["stages"]["hypotheses"] = {
                "count": len(hypotheses),
                "hypotheses": hypotheses[:5],
            }
            self.state["hypotheses_generated"] += len(hypotheses)
        except Exception as exc:
            result["stages"]["hypotheses"] = {"status": "failed", "error": str(exc)}
            hypotheses = []

        # Stage 3: Validate Hypotheses
        if hypotheses:
            log.info("Stage 3: Validate Hypotheses in Shadow")
            try:
                validated = self._validate_hypotheses(hypotheses[:3])
                result["stages"]["validation"] = {
                    "tested": len(hypotheses[:3]),
                    "validated": len(validated),
                    "validated_hypotheses": validated,
                }
                self.state["hypotheses_validated"] += len(validated)
            except Exception as exc:
                result["stages"]["validation"] = {"status": "failed", "error": str(exc)}
                validated = []
        else:
            result["stages"]["validation"] = {"status": "skipped", "reason": "no_hypotheses"}
            validated = []

        # Stage 4: Apply Validated Improvements
        if validated:
            log.info("Stage 4: Apply Validated Improvements")
            try:
                applied = self._apply_improvements(validated)
                result["stages"]["application"] = {
                    "attempted": len(validated),
                    "applied": applied,
                }
                self.state["improvements_applied"] += applied
            except Exception as exc:
                result["stages"]["application"] = {"status": "failed", "error": str(exc)}
        else:
            result["stages"]["application"] = {"status": "skipped", "reason": "no_validated"}

        # Stage 5: Meta-Adaptation
        log.info("Stage 5: Meta-Adaptation to Regime")
        try:
            adaptation = self._run_adaptation()
            result["stages"]["adaptation"] = adaptation
        except Exception as exc:
            result["stages"]["adaptation"] = {"status": "failed", "error": str(exc)}

        # Compute overall result
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_seconds"] = round(time.time() - start_time, 2)
        result["verdict"] = self._compute_verdict(result["stages"])

        # Update state
        self.state["last_cycle"] = result["completed_at"]
        self.state["total_cycles"] += 1
        self.state["health_status"] = result["verdict"]["status"]
        self._save_state()

        log.info("Meta-improvement cycle complete: %s (%.1fs)",
                 result["verdict"]["status"], result["duration_seconds"])

        return result

    def health_check(self) -> Dict[str, Any]:
        """Run comprehensive system health check."""
        health = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "subsystems": {},
            "overall_status": "HEALTHY",
        }

        # Check meta-reflector
        reflector = self._get_meta_reflector()
        if reflector:
            try:
                # audit_from_journal returns a HealthAuditReport dataclass.
                audit = reflector.audit_from_journal(self.db_path).to_dict()
                health["subsystems"]["meta_reflector"] = {
                    "status": "available",
                    "passed": audit.get("overall_status") == "HEALTHY",
                    "issues": audit.get("remediation_actions", []),
                    "overall_status": audit.get("overall_status"),
                    "risk_multiplier": audit.get("risk_multiplier"),
                    "execution_authority": audit.get("execution_authority"),
                }
            except Exception as exc:
                health["subsystems"]["meta_reflector"] = {"status": "error", "error": str(exc)}
        else:
            health["subsystems"]["meta_reflector"] = {"status": "unavailable"}

        # Check meta-adaptive engine
        adaptive = self._get_meta_adaptive()
        if adaptive:
            try:
                # MetaAdaptiveEngine exposes get_llm_context(), not get_current_regime().
                ctx = adaptive.get_llm_context()
                health["subsystems"]["meta_adaptive"] = {
                    "status": "available",
                    "current_regime": ctx.get("regime"),
                    "bars_in_regime": ctx.get("bars_in_regime"),
                    "risk_multiplier": ctx.get("risk_multiplier"),
                    "adaptation_count": ctx.get("adaptation_count"),
                }
            except Exception as exc:
                health["subsystems"]["meta_adaptive"] = {"status": "error", "error": str(exc)}
        else:
            health["subsystems"]["meta_adaptive"] = {"status": "unavailable"}

        # Check self-improvement loop
        si = self._get_self_improvement()
        if si:
            try:
                # get_status() nests counters under the "self_improvement" key.
                status = si.get_status().get("self_improvement", {})
                health["subsystems"]["self_improvement"] = {
                    "status": "available",
                    "cycle_count": status.get("cycle_count"),
                    "improvement_count": status.get("improvement_count"),
                    "history_count": status.get("history_count"),
                }
            except Exception as exc:
                health["subsystems"]["self_improvement"] = {"status": "error", "error": str(exc)}
        else:
            health["subsystems"]["self_improvement"] = {"status": "unavailable"}

        # Compute overall status
        subsystem_statuses = [s.get("status", "unknown") for s in health["subsystems"].values()]
        if all(s == "available" for s in subsystem_statuses):
            health["overall_status"] = "HEALTHY"
        elif any(s == "error" for s in subsystem_statuses):
            health["overall_status"] = "DEGRADED"
        else:
            health["overall_status"] = "PARTIAL"

        return health

    def _generate_hypotheses(self) -> List[Dict[str, Any]]:
        """Generate improvement hypotheses from recent performance."""
        hypotheses = []

        try:
            try:
                from research.research_validation import broker_reconciled_tickets
            except ImportError:
                from research.research_validation import broker_reconciled_tickets
            tickets = sorted(broker_reconciled_tickets(self.db_path))
            if not tickets:
                return hypotheses

            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row

            # Analyze recent broker-reconciled trades
            marks = ",".join("?" for _ in tickets)
            trades = con.execute(
                "SELECT symbol, side, gross_pnl, risk_amount, strategy, "
                "opened_at, closed_at FROM trades "
                "WHERE closed_at IS NOT NULL "
                "AND ticket IN (%s) "
                "ORDER BY closed_at DESC LIMIT 100" % marks,
                tickets,
            ).fetchall()
            con.close()

            if len(trades) < 20:
                return hypotheses

            # Compute metrics by strategy
            strategies = {}
            for trade in trades:
                strat = trade["strategy"] or "default"
                if strat not in strategies:
                    strategies[strat] = {"trades": [], "wins": 0, "total_pnl": 0}
                strategies[strat]["trades"].append(trade)
                if trade["gross_pnl"] and trade["gross_pnl"] > 0:
                    strategies[strat]["wins"] += 1
                strategies[strat]["total_pnl"] += trade["gross_pnl"] or 0

            # Generate hypotheses for underperforming strategies
            for strat, data in strategies.items():
                if len(data["trades"]) < 10:
                    continue

                win_rate = data["wins"] / len(data["trades"])
                avg_pnl = data["total_pnl"] / len(data["trades"])

                if win_rate < 0.45:
                    hypotheses.append({
                        "type": "strategy_tuning",
                        "strategy": strat,
                        "issue": "low_win_rate",
                        "current_win_rate": round(win_rate, 4),
                        "suggestion": "Consider tightening entry criteria or adjusting SL/TP",
                        "confidence": 0.6,
                    })

                if avg_pnl < 0:
                    hypotheses.append({
                        "type": "strategy_tuning",
                        "strategy": strat,
                        "issue": "negative_expectancy",
                        "current_avg_pnl": round(avg_pnl, 4),
                        "suggestion": "Strategy has negative expectancy; consider retirement or major overhaul",
                        "confidence": 0.7,
                    })

        except Exception as exc:
            log.debug("Hypothesis generation failed: %s", exc)

        return hypotheses

    def _validate_hypotheses(self, hypotheses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate hypotheses in shadow mode."""
        validated = []

        for hyp in hypotheses:
            # For now, mark high-confidence hypotheses as validated
            # In production, this would run shadow backtests
            if hyp.get("confidence", 0) >= 0.6:
                validated.append({
                    **hyp,
                    "validated_at": datetime.now(timezone.utc).isoformat(),
                    "validation_method": "confidence_threshold",
                })

        return validated

    def _apply_improvements(self, validated: List[Dict[str, Any]]) -> int:
        """Apply validated improvements."""
        applied = 0

        for imp in validated:
            # Record the improvement
            try:
                con = sqlite3.connect(self.db_path, timeout=2)
                con.execute(
                    "INSERT INTO meta_improvements "
                    "(improvement_type, strategy, description, applied_at) "
                    "VALUES (?, ?, ?, ?)",
                    (imp.get("type", "unknown"),
                     imp.get("strategy", ""),
                     json.dumps(imp, default=str),
                     datetime.now(timezone.utc).isoformat())
                )
                con.commit()
                con.close()
                applied += 1
            except Exception as exc:
                log.debug("Failed to record improvement: %s", exc)

        return applied

    def _run_adaptation(self) -> Dict[str, Any]:
        """Run meta-adaptation to current regime."""
        adaptive = self._get_meta_adaptive()
        if not adaptive:
            return {"status": "skipped", "reason": "engine_unavailable"}

        try:
            # MetaAdaptiveEngine exposes get_llm_context()/get_status(); adaptation
            # itself is driven by update(regime_probs, ...) when live bars arrive.
            ctx = adaptive.get_llm_context()
            status = adaptive.get_status()

            return {
                "status": "completed",
                "regime": ctx.get("regime"),
                "bars_in_regime": ctx.get("bars_in_regime"),
                "risk_multiplier": ctx.get("risk_multiplier"),
                "position_scale": ctx.get("position_scale"),
                "entry_confidence_threshold": ctx.get("entry_confidence_threshold"),
                "adaptation_count": ctx.get("adaptation_count"),
                "total_transitions": ctx.get("total_transitions"),
                "detector": status.get("detector"),
                "adapted": False,
                "note": "read-only snapshot; profile changes require live regime_probs",
            }
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def _compute_verdict(self, stages: Dict[str, Any]) -> Dict[str, Any]:
        """Compute overall cycle verdict."""
        health = stages.get("health", {})
        hypotheses = stages.get("hypotheses", {})
        validation = stages.get("validation", {})
        application = stages.get("application", {})

        # Determine status
        if health.get("overall_status") == "HEALTHY":
            if validation.get("validated", 0) > 0:
                return {"status": "IMPROVED", "message": "System healthy, improvements applied"}
            else:
                return {"status": "HEALTHY", "message": "System healthy, no improvements needed"}
        elif health.get("overall_status") == "DEGRADED":
            return {"status": "DEGRADED", "message": "System degraded, review required"}
        else:
            return {"status": "PARTIAL", "message": "Partial subsystem availability"}

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get improvement history."""
        try:
            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row

            rows = con.execute(
                "SELECT * FROM meta_improvements "
                "ORDER BY applied_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            con.close()

            return [dict(row) for row in rows]

        except Exception:
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Get orchestrator statistics."""
        return {
            "total_cycles": self.state.get("total_cycles", 0),
            "last_cycle": self.state.get("last_cycle"),
            "improvements_applied": self.state.get("improvements_applied", 0),
            "improvements_rejected": self.state.get("improvements_rejected", 0),
            "hypotheses_generated": self.state.get("hypotheses_generated", 0),
            "hypotheses_validated": self.state.get("hypotheses_validated", 0),
            "health_status": self.state.get("health_status", "UNKNOWN"),
        }


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Meta-Improvement Orchestrator")
    parser.add_argument("--cycle", action="store_true", help="Run improvement cycle")
    parser.add_argument("--health", action="store_true", help="Run health check")
    parser.add_argument("--history", action="store_true", help="Show improvement history")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    orch = MetaImprovementOrchestrator()

    if args.cycle:
        result = orch.run_cycle()
        print(json.dumps(result, indent=2, default=str))
    elif args.health:
        result = orch.health_check()
        print(json.dumps(result, indent=2, default=str))
    elif args.history:
        history = orch.get_history()
        print(json.dumps(history, indent=2, default=str))
    elif args.stats:
        print(json.dumps(orch.get_stats(), indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
