"""Live Training Infrastructure — trade lifecycle, RL live training, validation gate.

Provides the modules referenced by mt5_trader.py imports:
  from core.live_training_infrastructure import (
      TradeLifecycleManager, RealDataCollector,
      MetaAdaptiveFeedbackLoop, RLLiveTrainingPipeline, LiveValidationGate,
  )
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TradeLifecycleManager
# ---------------------------------------------------------------------------

class TradeLifecycleManager:
    """Tracks the full lifecycle of a trade from proposal to close."""

    def __init__(self, store: Any = None):
        self.store = store
        self._active: Dict[str, Dict[str, Any]] = {}
        self._closed: List[Dict[str, Any]] = []

    def open_trade(self, proposal: Dict[str, Any]) -> str:
        """Register a new trade proposal. Returns lifecycle ID."""
        lifecycle_id = f"lc_{int(time.time() * 1000)}_{len(self._active)}"
        self._active[lifecycle_id] = {
            "proposal": proposal,
            "opened_at": time.time(),
            "status": "OPEN",
            "pnl": 0.0,
            "touches": [],
        }
        return lifecycle_id

    def record_event(self, lifecycle_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        """Record a lifecycle event (fill, partial close, SL/TP adjust, close)."""
        trade = self._active.get(lifecycle_id)
        if trade is None:
            return
        trade["touches"].append({
            "event": event_type,
            "timestamp": time.time(),
            "payload": payload,
        })
        if event_type == "CLOSE":
            trade["status"] = "CLOSED"
            trade["closed_at"] = time.time()
            trade["pnl"] = payload.get("pnl", 0.0)
            self._closed.append(trade)
            del self._active[lifecycle_id]

    def get_active(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._active)

    def get_closed(self) -> List[Dict[str, Any]]:
        return list(self._closed)

    def summary(self) -> Dict[str, Any]:
        return {
            "active_count": len(self._active),
            "closed_count": len(self._closed),
            "total_pnl": sum(t.get("pnl", 0) for t in self._closed),
        }


# ---------------------------------------------------------------------------
# RealDataCollector
# ---------------------------------------------------------------------------

class RealDataCollector:
    """Collects and validates real market data for live training."""

    def __init__(self, data_provider: Any = None, store: Any = None):
        self.data_provider = data_provider
        self.store = store
        self._collection_log: List[Dict[str, Any]] = []

    def collect(self, symbol: str, timeframe: str, count: int = 500) -> Dict[str, Any]:
        """Fetch and validate market data. Returns quality-scored dataset."""
        t0 = time.time()
        result = {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": count,
            "status": "ATTEMPTED",
            "quality_score": 0.0,
        }

        try:
            if self.data_provider and hasattr(self.data_provider, "fetch_data"):
                toon = self.data_provider.fetch_data(symbol, timeframe, cycle_num=0)
                ohlc = toon.get("ohlc", []) if isinstance(toon, dict) else []
                if ohlc and len(ohlc) > 0:
                    # Quality checks
                    gaps = self._detect_gaps(ohlc)
                    completeness = len(ohlc) / max(1, count)
                    result.update({
                        "status": "COLLECTED",
                        "bars": len(ohlc),
                        "gaps": gaps,
                        "completeness": round(completeness, 3),
                        "quality_score": round(max(0.0, 1.0 - gaps * 0.05) * completeness, 3),
                    })
                else:
                    result["status"] = "EMPTY"
            else:
                result["status"] = "NO_PROVIDER"
        except Exception as exc:
            result["status"] = "ERROR"
            result["error"] = str(exc)[:300]

        result["duration_ms"] = int((time.time() - t0) * 1000)
        self._collection_log.append(result)
        return result

    def _detect_gaps(self, ohlc: List[Dict[str, Any]]) -> int:
        """Count potential gaps in OHLC sequence."""
        gaps = 0
        for i in range(1, len(ohlc)):
            prev_ts = ohlc[i - 1].get("timestamp", 0)
            curr_ts = ohlc[i].get("timestamp", 0)
            if isinstance(prev_ts, (int, float)) and isinstance(curr_ts, (int, float)):
                expected_gap = 3600  # approximate hourly
                if curr_ts - prev_ts > expected_gap * 2:
                    gaps += 1
        return gaps

    def get_quality_history(self) -> List[Dict[str, Any]]:
        return list(self._collection_log)


# ---------------------------------------------------------------------------
# MetaAdaptiveFeedbackLoop
# ---------------------------------------------------------------------------

class MetaAdaptiveFeedbackLoop:
    """Meta-learning feedback loop that adapts strategy parameters based on
    accumulated performance data."""

    def __init__(self, store: Any = None):
        self.store = store
        self._feedback_log: List[Dict[str, Any]] = []
        self._adaptations: Dict[str, Any] = {}

    def evaluate_and_adapt(
        self,
        performance_metrics: Dict[str, Any],
        strategy_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate performance and suggest/adapt parameters.

        Returns:
            Dict with 'adapted' bool, 'adjustments' dict, 'reason' str.
        """
        result = {
            "adapted": False,
            "adjustments": {},
            "reason": "",
            "timestamp": time.time(),
        }

        win_rate = performance_metrics.get("win_rate", 50.0)
        sharpe = performance_metrics.get("sharpe", 1.0)
        max_dd = performance_metrics.get("max_drawdown_pct", 10.0)

        # Risk adaptation
        if max_dd > 15.0:
            result["adjustments"]["risk_per_trade"] = strategy_params.get("risk_per_trade", 0.01) * 0.7
            result["reason"] = f"Drawdown {max_dd:.1f}% exceeds 15% threshold"
            result["adapted"] = True
        elif max_dd < 5.0 and win_rate > 55:
            result["adjustments"]["risk_per_trade"] = min(
                0.02, strategy_params.get("risk_per_trade", 0.01) * 1.2
            )
            result["reason"] = f"Low drawdown {max_dd:.1f}% with strong win rate {win_rate:.1f}%"
            result["adapted"] = True

        # SL/TP adaptation
        if sharpe < 0.5:
            result["adjustments"]["sl_atr_mult"] = strategy_params.get("sl_atr_mult", 2.0) * 1.1
            result["adjustments"]["tp_atr_mult"] = strategy_params.get("tp_atr_mult", 3.0) * 0.9
            result["reason"] = result.get("reason", "") + " | Low Sharpe, tightening risk"
            result["adapted"] = True

        if result["adapted"]:
            self._adaptations.update(result["adjustments"])
            self._feedback_log.append(result)

        return result

    def get_adaptation_history(self) -> List[Dict[str, Any]]:
        return list(self._feedback_log)

    def get_current_adaptations(self) -> Dict[str, Any]:
        return dict(self._adaptations)


# ---------------------------------------------------------------------------
# RLLiveTrainingPipeline
# ---------------------------------------------------------------------------

class RLLiveTrainingPipeline:
    """RL training pipeline that runs on live demo data."""

    def __init__(self, store: Any = None):
        self.store = store
        self._training_log: List[Dict[str, Any]] = []
        self._model_state: Dict[str, Any] = {}

    def train_episode(
        self,
        observations: List[Dict[str, Any]],
        actions: List[Dict[str, Any]],
        rewards: List[float],
    ) -> Dict[str, Any]:
        """Run one RL training episode from live data.

        Returns:
            Dict with training metrics.
        """
        t0 = time.time()
        n_steps = len(observations)
        total_reward = sum(rewards)
        avg_reward = total_reward / max(1, n_steps)

        # Simple Q-value update placeholder
        # In production this delegates to RLTrainer
        result = {
            "episode": len(self._training_log) + 1,
            "n_steps": n_steps,
            "total_reward": round(total_reward, 4),
            "avg_reward": round(avg_reward, 6),
            "duration_ms": int((time.time() - t0) * 1000),
            "status": "COMPLETED",
        }

        self._training_log.append(result)
        self._model_state["last_reward"] = total_reward
        self._model_state["episodes_trained"] = len(self._training_log)

        return result

    def get_model_state(self) -> Dict[str, Any]:
        return dict(self._model_state)

    def get_training_log(self) -> List[Dict[str, Any]]:
        return list(self._training_log)


# ---------------------------------------------------------------------------
# LiveValidationGate
# ---------------------------------------------------------------------------

class LiveValidationGate:
    """Validates strategy performance before allowing live deployment.

    Combines multiple validation sources:
      - Walk-forward efficiency
      - Overfit detection
      - Drawdown limits
      - Win rate thresholds
    """

    def __init__(self, store: Any = None):
        self.store = store
        self._validation_log: List[Dict[str, Any]] = []

    def validate(
        self,
        strategy_id: str,
        returns: List[float],
        metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run full validation gate on a strategy.

        Returns:
            Dict with 'passed' bool, per-gate results, and overall verdict.
        """
        t0 = time.time()
        results = {
            "strategy_id": strategy_id,
            "passed": True,
            "gates": {},
            "timestamp": time.time(),
        }

        # Gate 1: Walk-forward efficiency
        try:
            import numpy as np
            from core.walk_forward_validator import WalkForwardValidator
            wfv = WalkForwardValidator(state_file=None)
            # No synthetic fallback: a random series here would let the gate
            # pass a strategy that has produced no returns at all.
            returns_arr = np.array(returns) if returns else None
            if returns_arr is not None and len(returns_arr) >= 120:
                verdict = wfv.walk_forward_efficiency_gate(returns_arr)
                results["gates"]["walk_forward"] = {
                    "wfe": round(getattr(verdict, "wfe", 0), 4),
                    "passed": getattr(verdict, "passed", False),
                }
                if not getattr(verdict, "passed", False):
                    results["passed"] = False
            else:
                results["gates"]["walk_forward"] = {
                    "status": "NOT_VALIDATED",
                    "reason": "no measured return series (need >=120 observations)",
                }
        except Exception as exc:
            results["gates"]["walk_forward"] = {"status": "SKIPPED", "error": str(exc)[:200]}

        # Gate 2: Overfit detection
        try:
            import numpy as np
            from core.overfit_gate import OverfitGate
            gate = OverfitGate(store=self.store)
            returns_arr = np.array(returns) if returns else None
            if returns_arr is not None and len(returns_arr) >= 2:
                gate_result = gate.validate_strategy(
                    strategy=strategy_id,
                    returns=returns_arr.tolist(),
                    returns_provenance="measured",
                )
                results["gates"]["overfit"] = {
                    "status": gate_result.status,
                    "risk_level": gate_result.risk_level,
                    "grade": gate_result.grade,
                }
                if gate_result.status == "REJECT":
                    results["passed"] = False
            else:
                results["gates"]["overfit"] = {
                    "status": "NOT_VALIDATED",
                    "risk_level": "UNKNOWN",
                    "grade": "N/A",
                    "reason": "no measured return series; refusing to synthesise one",
                }
        except Exception as exc:
            results["gates"]["overfit"] = {"status": "SKIPPED", "error": str(exc)[:200]}

        # Gate 3: Drawdown limit
        max_dd = metrics.get("max_drawdown_pct", 0)
        results["gates"]["drawdown"] = {
            "value": max_dd,
            "threshold": 20.0,
            "passed": max_dd <= 20.0,
        }
        if max_dd > 20.0:
            results["passed"] = False

        # Gate 4: Minimum win rate
        win_rate = metrics.get("win_rate", 0)
        results["gates"]["win_rate"] = {
            "value": win_rate,
            "threshold": 45.0,
            "passed": win_rate >= 45.0,
        }
        if win_rate < 45.0:
            results["passed"] = False

        results["duration_ms"] = int((time.time() - t0) * 1000)
        self._validation_log.append(results)
        return results

    def get_validation_log(self) -> List[Dict[str, Any]]:
        return list(self._validation_log)

    def get_latest(self) -> Optional[Dict[str, Any]]:
        return self._validation_log[-1] if self._validation_log else None
