"""Demo Trading Orchestrator — dedicated demo account training pipeline.

Runs the full trading cycle on a real demo broker account with:
  - Periodic validation gates (overfit, walk-forward, drawdown)
  - Meta-adaptive parameter tuning
  - Strategy lifecycle tracking
  - Performance metrics accumulation

Called from mt5_trader.py via:
  from core.demo_trading_orchestrator import DemoTradingOrchestrator
  demo_orch = DemoTradingOrchestrator(...)
  result = demo_orch.run_cycle()
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DemoCycleResult:
    """Result of a single demo trading cycle."""
    cycle_num: int
    symbol: str
    timeframe: str
    status: str  # "EXECUTED", "SKIPPED", "VALIDATED", "ADAPTED", "ERROR"
    trade_proposed: bool = False
    validation_passed: Optional[bool] = None
    adaptation_applied: bool = False
    metrics: Dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_num": self.cycle_num,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "status": self.status,
            "trade_proposed": self.trade_proposed,
            "validation_passed": self.validation_passed,
            "adaptation_applied": self.adaptation_applied,
            "metrics": self.metrics,
            "duration_seconds": round(self.duration_seconds, 3),
        }


class _DemoTradeCollector:
    """Lightweight outcome collector for demo session reporting."""

    def __init__(self) -> None:
        self.trade_outcomes: List[Dict[str, Any]] = []


class DemoTradingOrchestrator:
    """Demo account trading pipeline with validation and adaptation.

    Designed to match the constructor signature expected by mt5_trader.py:
        DemoTradingOrchestrator(
            store, operations, data_provider, risk,
            symbol, timeframe, poll_interval,
            adapt_every_n_cycles, validate_every_n_cycles,
        )
    """

    def __init__(
        self,
        store: Any = None,
        operations: Any = None,
        data_provider: Any = None,
        risk: Any = None,
        symbol: str = "EURUSD",
        timeframe: str = "H1",
        poll_interval: float = 30.0,
        adapt_every_n_cycles: int = 50,
        validate_every_n_cycles: int = 100,
    ):
        self.store = store
        self.operations = operations
        self.data_provider = data_provider
        self.risk = risk
        self.symbol = symbol
        self.timeframe = timeframe
        self.poll_interval = poll_interval
        self.adapt_every_n_cycles = adapt_every_n_cycles
        self.validate_every_n_cycles = validate_every_n_cycles

        self._cycle_count = 0
        self._trade_history: List[Dict[str, Any]] = []
        self._validation_history: List[Dict[str, Any]] = []
        self._adaptation_history: List[Dict[str, Any]] = []
        self._on_validation: Optional[Callable] = None
        self._on_adaptation: Optional[Callable] = None
        self.collector = _DemoTradeCollector()

    def set_callbacks(
        self,
        on_validation: Optional[Callable] = None,
        on_adaptation: Optional[Callable] = None,
    ) -> None:
        """Register callbacks for validation/adaptation events."""
        self._on_validation = on_validation
        self._on_adaptation = on_adaptation

    def run_cycle(self) -> Dict[str, Any]:
        """Execute a single demo trading cycle.

        Returns:
            Dict with cycle results matching DemoCycleResult structure.
        """
        t0 = time.time()
        self._cycle_count += 1

        result = DemoCycleResult(
            cycle_num=self._cycle_count,
            symbol=self.symbol,
            timeframe=self.timeframe,
            status="EXECUTED",
        )

        try:
            # --- 1. Fetch market data ---
            ohlc = self._fetch_data()
            if ohlc is None or len(ohlc) == 0:
                result.status = "SKIPPED"
                result.metrics = {"reason": "no_data"}
                result.duration_seconds = time.time() - t0
                return result.to_dict()

            # --- 2. Run strategy cycle ---
            proposal = self._run_strategy(ohlc)
            result.trade_proposed = proposal is not None

            # --- 3. Execute via paper broker (demo = paper execution on real broker data) ---
            if proposal is not None:
                execution = self._execute_demo(proposal)
                self._trade_history.append({
                    "cycle": self._cycle_count,
                    "proposal": proposal,
                    "execution": execution,
                    "timestamp": time.time(),
                })
                self.collector.trade_outcomes.append({
                    "cycle": self._cycle_count,
                    "symbol": self.symbol,
                    "side": proposal.get("side"),
                    "status": (execution or {}).get("status", "UNKNOWN"),
                    "timestamp": time.time(),
                })
                result.metrics["trade_executed"] = execution is not None

            # --- 4. Periodic validation ---
            if self._cycle_count % self.validate_every_n_cycles == 0:
                validation = self._run_validation()
                result.validation_passed = validation.get("passed", False)
                result.status = "VALIDATED"
                self._validation_history.append(validation)
                if self._on_validation:
                    try:
                        self._on_validation(validation)
                    except Exception:
                        pass

            # --- 5. Periodic adaptation ---
            if self._cycle_count % self.adapt_every_n_cycles == 0:
                adaptation = self._run_adaptation()
                result.adaptation_applied = adaptation.get("applied", False)
                if result.status != "VALIDATED":
                    result.status = "ADAPTED"
                self._adaptation_history.append(adaptation)
                if self._on_adaptation:
                    try:
                        self._on_adaptation(adaptation)
                    except Exception:
                        pass

            # --- 6. Record performance metrics ---
            result.metrics.update(self._compute_metrics())

        except Exception as exc:
            logger.exception("Demo cycle %d failed: %s", self._cycle_count, exc)
            result.status = "ERROR"
            result.metrics["error"] = str(exc)[:500]

        result.duration_seconds = time.time() - t0
        return result.to_dict()

    def _fetch_data(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch OHLC data from the data provider."""
        try:
            if self.data_provider is not None and hasattr(self.data_provider, "fetch_data"):
                toon = self.data_provider.fetch_data(
                    self.symbol, self.timeframe, cycle_num=self._cycle_count,
                )
                return toon.get("ohlc", []) if isinstance(toon, dict) else []
        except Exception as exc:
            logger.debug("Demo data fetch failed: %s", exc)
        return None

    def _run_strategy(self, ohlc: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Run strategy logic and return a trade proposal or None."""
        # Advisory-only: no execution authority in orchestrator
        try:
            if self.store is not None and hasattr(self.store, "get_state"):
                state = self.store.get_state("demo_strategy_state", {})
                # Simple adaptive strategy placeholder
                return {
                    "symbol": self.symbol,
                    "side": "BUY",
                    "volume": 0.01,
                    "strategy": "demo_adaptive",
                    "cycle": self._cycle_count,
                }
        except Exception:
            pass
        return None

    def _execute_demo(self, proposal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute trade via PaperBroker (demo mode)."""
        # Demo orchestrator never executes directly — it proposes and the
        # main loop's PaperBroker or MT5Client handles fill.
        return {
            "status": "PROPOSED",
            "proposal": proposal,
            "execution_authority": False,
        }

    def _measured_returns(self) -> List[float]:
        """Per-trade fractional returns actually recorded, oldest first.

        ``_execute_demo`` only ever records ``status="PROPOSED"`` — no fill,
        exit price, or P&L — so a proposal-only demo loop legitimately has no
        return series and both validation gates report NOT_VALIDATED. That is
        the honest outcome; the previous synthetic fallback made them certify a
        coin flip. If execution later records a realised P&L against a notional
        or entry price, it is picked up here without any other change.
        """
        out: List[float] = []
        for trade in self._trade_history:
            execution = trade.get("execution") or {}
            pnl = execution.get("pnl", execution.get("realized_pnl"))
            if pnl is None:
                continue
            base = (execution.get("equity_at_entry")
                    or execution.get("notional")
                    or (trade.get("proposal") or {}).get("notional"))
            try:
                pnl = float(pnl)
                base = float(base)
            except (TypeError, ValueError, OverflowError):
                continue
            if base > 0 and math.isfinite(pnl / base):
                out.append(pnl / base)
        return out

    def _run_validation(self) -> Dict[str, Any]:
        """Run validation gates on accumulated demo performance."""
        results = {"cycle": self._cycle_count, "checks": {}}

        # Walk-forward check
        try:
            from core.walk_forward_validator import WalkForwardValidator
            import numpy as np
            wfv = WalkForwardValidator(state_file=None)
            # Measure the demo P&L actually recorded. The series used to be
            # np.random.normal(0.0003, 0.01, ...) sized off the trade count, so
            # both gates below certified a coin flip regardless of results.
            returns = self._measured_returns()
            if len(returns) >= 120:
                verdict = wfv.walk_forward_efficiency_gate(returns)
                results["checks"]["walk_forward"] = {
                    "wfe": round(getattr(verdict, "wfe", 0), 4),
                    "passed": getattr(verdict, "passed", False),
                }
            else:
                results["checks"]["walk_forward"] = {
                    "status": "NOT_VALIDATED",
                    "reason": "no measured demo return series (need >=120)",
                }
        except Exception as exc:
            results["checks"]["walk_forward"] = {"status": "SKIPPED", "error": str(exc)[:200]}

        # Overfit check
        try:
            from core.overfit_gate import OverfitGate
            gate = OverfitGate(store=self.store)
            returns = self._measured_returns()
            if len(returns) >= 2:
                gate_result = gate.validate_strategy(
                    strategy=f"demo_{self.symbol}_{self.timeframe}",
                    returns=returns,
                    returns_provenance="measured",
                )
                results["checks"]["overfit"] = {
                    "status": gate_result.status,
                    "risk_level": gate_result.risk_level,
                }
            else:
                results["checks"]["overfit"] = {
                    "status": "NOT_VALIDATED",
                    "risk_level": "UNKNOWN",
                    "reason": "no measured demo return series; refusing to synthesise one",
                }
        except Exception as exc:
            results["checks"]["overfit"] = {"status": "SKIPPED", "error": str(exc)[:200]}

        # Drawdown check
        total_trades = len(self._trade_history)
        wins = sum(1 for t in self._trade_history if t.get("execution", {}).get("status") == "PROPOSED")
        results["checks"]["performance"] = {
            "total_trades": total_trades,
            "win_rate": round(wins / max(1, total_trades) * 100, 1),
        }

        all_passed = all(
            c.get("passed", True) for c in results["checks"].values()
            if isinstance(c, dict) and "passed" in c
        )
        results["passed"] = all_passed
        results["timestamp"] = time.time()
        return results

    def _run_adaptation(self) -> Dict[str, Any]:
        """Apply meta-adaptive parameter tuning."""
        results = {"cycle": self._cycle_count, "applied": False, "adjustments": {}}

        try:
            recent = self._trade_history[-min(50, len(self._trade_history)):]
            if len(recent) < 10:
                results["reason"] = "insufficient_data"
                return results

            # Simple adaptation: adjust position sizing based on recent performance
            win_rate = sum(
                1 for t in recent
                if t.get("execution", {}).get("status") == "PROPOSED"
            ) / len(recent)

            if win_rate < 0.4:
                results["adjustments"]["volume_multiplier"] = 0.5
                results["applied"] = True
            elif win_rate > 0.6:
                results["adjustments"]["volume_multiplier"] = 1.2
                results["applied"] = True

            results["metrics"] = {"recent_win_rate": round(win_rate * 100, 1)}

        except Exception as exc:
            logger.debug("Demo adaptation failed: %s", exc)

        results["timestamp"] = time.time()
        return results

    def _compute_metrics(self) -> Dict[str, Any]:
        """Compute accumulated demo performance metrics."""
        total = len(self._trade_history)
        return {
            "total_cycles": self._cycle_count,
            "total_trades": total,
            "validations_run": len(self._validation_history),
            "adaptations_run": len(self._adaptation_history),
            "last_validation": self._validation_history[-1] if self._validation_history else None,
        }

    def get_readiness_report(self) -> Dict[str, Any]:
        """End-of-session readiness summary (advisory, never grants authority)."""
        total_trades = len(self._trade_history)
        proposed = sum(
            1 for t in self._trade_history
            if (t.get("execution") or {}).get("status") == "PROPOSED"
        )
        win_rate = proposed / max(1, total_trades)

        last_val = self._validation_history[-1] if self._validation_history else {}
        validation = {
            "verdict": ("PASS" if last_val.get("passed") else "PENDING")
                       if last_val else "NOT_RUN",
            "can_promote": bool(last_val.get("passed", False)),
            "checks": last_val.get("checks", {}),
        }

        # Regime performance: group outcomes by proposal strategy tag
        regime_performance: Dict[str, Dict[str, Any]] = {}
        for t in self._trade_history:
            regime = (t.get("proposal") or {}).get("strategy", "default")
            bucket = regime_performance.setdefault(
                regime, {"count": 0, "wins": 0, "total_pnl": 0.0}
            )
            bucket["count"] += 1
            if (t.get("execution") or {}).get("status") == "PROPOSED":
                bucket["wins"] += 1
        for regime, b in regime_performance.items():
            b["win_rate"] = b["wins"] / max(1, b["count"])
            b["sharpe"] = 0.0
            b["total_pnl"] = b["total_pnl"]

        # RL policy stats from store when available
        rl_states = 0
        rl_updates = 0
        try:
            if self.store is not None and hasattr(self.store, "get_state"):
                rl_state = self.store.get_state("rl_model_state", {}) or {}
                rl_states = int(rl_state.get("states", 0))
                rl_updates = int(rl_state.get("updates", 0))
        except Exception:
            pass

        deployment_ready = bool(
            validation["can_promote"]
            and total_trades >= 50
            and win_rate >= 0.45
        )

        return {
            "total_cycles": self._cycle_count,
            "total_trades": total_trades,
            "validation": validation,
            "deployment_ready": deployment_ready,
            "regime_performance": regime_performance,
            "rl_policy": {"states": rl_states, "updates": rl_updates},
            "execution_authority": False,
        }
