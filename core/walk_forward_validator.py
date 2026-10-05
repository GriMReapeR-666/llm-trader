"""Walk-Forward Validator with Continuous Alpha Decay Detection.

Implements rolling walk-forward validation and real-time decay detection
inspired by Author-C (2018) and FactorMiner (2026).

Key concepts:
1. Walk-Forward Analysis: Rolling train/test windows that move forward
   in time, avoiding lookahead bias
2. Information Coefficient (IC): Correlation between predicted and
   actual returns — measures alpha signal quality
3. IC Decay: When IC drops below threshold, the alpha is decaying
4. Decay Velocity: How fast IC is dropping (acceleration of decay)
5. Half-Life: Time for IC to drop to 50% of its peak

References:
    - Author-C (2018): Advances in Financial Machine Learning
    - FactorMiner (2026): Experience memory for decay tracking
    - AlphaAgent (2025): Decay-resistant alpha mining

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │           WALK-FORWARD VALIDATOR                             │
    │                                                              │
    │  Input: Returns series + predicted signals                   │
    │      ↓                                                       │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Step 1: Rolling IC Computation        │                   │
    │  │   IC(t) = corr(predicted, actual)     │                   │
    │  │   over rolling window of size W       │                   │
    │  └──────────────┬───────────────────────┘                   │
    │                 ↓                                            │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Step 2: Decay Detection               │                   │
    │  │   IC < threshold → DECAYING           │                   │
    │  │   IC dropping for N bars → CONFIRMED  │                   │
    │  │   Decay velocity = d(IC)/dt           │                   │
    │  └──────────────┬───────────────────────┘                   │
    │                 ↓                                            │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Step 3: Half-Life Estimation          │                   │
    │  │   Fit exponential decay to IC history │                   │
    │  │   half_life = ln(2) / lambda          │                   │
    │  └──────────────┬───────────────────────┘                   │
    │                 ↓                                            │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Step 4: Walk-Forward Splits           │                   │
    │  │   Rolling train/test windows          │                   │
    │  │   No lookahead bias                   │                   │
    │  └──────────────┬───────────────────────┘                   │
    │                 ↓                                            │
    │  Output: DecayReport with alerts and recommendations        │
    └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from core.state_persistence import StoreBackedState
except ImportError:
    from core.state_persistence import StoreBackedState

logger = logging.getLogger("WalkForward")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_IC_WINDOW = 20           # Rolling window for IC computation
DEFAULT_IC_DECAY_THRESHOLD = 0.02  # IC below this = decaying
DEFAULT_IC_CONFIRM_BARS = 3     # Bars of decay before confirmation
DEFAULT_HALF_LIFE_MAX = 100     # Maximum half-life in bars
DEFAULT_WFE_ACCEPT_THRESHOLD = 0.5  # WFE = OOS Sharpe / IS Sharpe must exceed this
STATE_FILE = "walk_forward_state.json"


# ---------------------------------------------------------------------------
# Enums & Dataclasses
# ---------------------------------------------------------------------------

class DecayStatus(str, Enum):
    HEALTHY = "HEALTHY"
    WATCH = "WATCH"           # IC declining but still above threshold
    DECAYING = "DECAYING"     # IC below threshold
    DEAD = "DEAD"             # IC consistently near zero


@dataclass
class ICPoint:
    """Single IC observation."""
    timestamp: float
    ic_value: float
    predicted_returns: List[float]
    actual_returns: List[float]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "ic_value": round(self.ic_value, 6),
        }


@dataclass
class DecayReport:
    """Complete decay analysis report."""
    status: DecayStatus
    current_ic: float
    mean_ic: float
    ic_std: float
    ic_trend: float           # Slope of IC over recent window
    decay_velocity: float     # Rate of IC decline
    half_life: Optional[float]  # Estimated half-life in bars
    bars_below_threshold: int
    consecutive_declines: int
    alerts: List[str]
    recommendations: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "current_ic": round(self.current_ic, 6),
            "mean_ic": round(self.mean_ic, 6),
            "ic_std": round(self.ic_std, 6),
            "ic_trend": round(self.ic_trend, 6),
            "decay_velocity": round(self.decay_velocity, 6),
            "half_life": round(self.half_life, 1) if self.half_life else None,
            "bars_below_threshold": self.bars_below_threshold,
            "consecutive_declines": self.consecutive_declines,
            "alerts": self.alerts,
            "recommendations": self.recommendations,
        }


@dataclass
class WalkForwardResult:
    """Result of a single walk-forward split."""
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_sharpe: float
    test_sharpe: float
    train_ic: float
    test_ic: float
    degradation: float        # train_sharpe - test_sharpe
    wfe: float = 0.0          # Per-fold walk-forward efficiency (test/train Sharpe)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "train_period": f"{self.train_start}-{self.train_end}",
            "test_period": f"{self.test_start}-{self.test_end}",
            "train_sharpe": round(self.train_sharpe, 4),
            "test_sharpe": round(self.test_sharpe, 4),
            "train_ic": round(self.train_ic, 6),
            "test_ic": round(self.test_ic, 6),
            "degradation": round(self.degradation, 4),
            "wfe": round(self.wfe, 4),
        }


@dataclass
class WFEVerdict:
    """Aggregated walk-forward efficiency gate verdict."""
    wfe: float                    # mean(OOS Sharpe) / mean(IS Sharpe)
    mean_train_sharpe: float
    mean_test_sharpe: float
    n_folds: int
    threshold: float
    passed: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wfe": round(self.wfe, 4),
            "mean_train_sharpe": round(self.mean_train_sharpe, 4),
            "mean_test_sharpe": round(self.mean_test_sharpe, 4),
            "n_folds": self.n_folds,
            "threshold": self.threshold,
            "passed": self.passed,
            "reason": self.reason,
        }


def compute_wfe(
    results: List[WalkForwardResult],
    threshold: float = DEFAULT_WFE_ACCEPT_THRESHOLD,
) -> WFEVerdict:
    """Aggregate walk-forward splits into a single WFE gate verdict.

    WFE = mean(OOS Sharpe) / mean(IS Sharpe).  The canonical acceptance
    bar is WFE > 0.5: a strategy that loses more than half its in-sample
    edge out-of-sample is curve-fit, regardless of absolute Sharpe.

    Edge cases fail closed:
    - Fewer than 2 folds: not enough evidence → not passed.
    - mean IS Sharpe <= 0: no in-sample edge to preserve → not passed.
    - Negative WFE (OOS loses money while IS profited) → not passed.
    """
    n = len(results)
    if n < 2:
        return WFEVerdict(
            wfe=0.0, mean_train_sharpe=0.0, mean_test_sharpe=0.0,
            n_folds=n, threshold=threshold, passed=False,
            reason=f"insufficient folds ({n}) for WFE evaluation",
        )

    mean_train = float(np.mean([r.train_sharpe for r in results]))
    mean_test = float(np.mean([r.test_sharpe for r in results]))

    if mean_train <= 0.0:
        return WFEVerdict(
            wfe=0.0, mean_train_sharpe=mean_train, mean_test_sharpe=mean_test,
            n_folds=n, threshold=threshold, passed=False,
            reason=f"no in-sample edge (mean IS Sharpe {mean_train:.4f} <= 0)",
        )

    wfe = mean_test / mean_train
    passed = wfe >= threshold and mean_test > 0.0
    if not passed and mean_test <= 0.0:
        reason = f"out-of-sample unprofitable (mean OOS Sharpe {mean_test:.4f} <= 0)"
    elif not passed:
        reason = f"WFE {wfe:.3f} below acceptance threshold {threshold:.2f}"
    else:
        reason = f"WFE {wfe:.3f} >= threshold {threshold:.2f}"

    return WFEVerdict(
        wfe=wfe, mean_train_sharpe=mean_train, mean_test_sharpe=mean_test,
        n_folds=n, threshold=threshold, passed=passed, reason=reason,
    )


# ---------------------------------------------------------------------------
# IC Computation
# ---------------------------------------------------------------------------

def compute_ic(
    predicted: np.ndarray,
    actual: np.ndarray,
) -> float:
    """Compute Information Coefficient (Spearman rank correlation).
    
    IC = corr(ranked_predicted, ranked_actual)
    
    This is the standard measure of alpha signal quality in quant finance.
    """
    n = min(len(predicted), len(actual))
    if n < 5:
        return 0.0
    
    pred = predicted[-n:]
    act = actual[-n:]
    
    # Filter out NaN/inf
    valid = np.isfinite(pred) & np.isfinite(act)
    if np.sum(valid) < 5:
        return 0.0
    
    pred_clean = pred[valid]
    act_clean = act[valid]
    
    # Spearman rank correlation
    try:
        from scipy import stats
        ic, _ = stats.spearmanr(pred_clean, act_clean)
        return float(ic) if math.isfinite(ic) else 0.0
    except ImportError:
        # Fallback: Pearson correlation
        try:
            ic = float(np.corrcoef(pred_clean, act_clean)[0, 1])
            return ic if math.isfinite(ic) else 0.0
        except Exception:
            return 0.0


def compute_rolling_ic(
    predicted: np.ndarray,
    actual: np.ndarray,
    window: int = DEFAULT_IC_WINDOW,
) -> List[float]:
    """Compute rolling IC over time."""
    n = min(len(predicted), len(actual))
    if n < window:
        return []
    
    rolling_ics = []
    for i in range(window, n + 1):
        ic = compute_ic(predicted[i - window:i], actual[i - window:i])
        rolling_ics.append(ic)
    
    return rolling_ics


# ---------------------------------------------------------------------------
# Walk-Forward Validator
# ---------------------------------------------------------------------------

class WalkForwardValidator(StoreBackedState):
    """Walk-forward validator with continuous alpha decay detection.

    Provides:
    1. Rolling IC tracking
    2. Decay detection and alerts
    3. Half-life estimation
    4. Walk-forward splits (no lookahead bias)
    """

    store_key = "walk_forward_state"

    def __init__(
        self,
        state_file: str = STATE_FILE,
        ic_window: int = DEFAULT_IC_WINDOW,
        ic_decay_threshold: float = DEFAULT_IC_DECAY_THRESHOLD,
        ic_confirm_bars: int = DEFAULT_IC_CONFIRM_BARS,
        store: Optional[Any] = None,
    ):
        self.state_file = state_file
        self.store = store
        self.ic_window = ic_window
        self.ic_decay_threshold = ic_decay_threshold
        self.ic_confirm_bars = ic_confirm_bars
        self.state = self._load_state()
        if store is not None:
            self.attach_store(store)

    def _load_state(self) -> Dict[str, Any]:
        data = self._load_state_dict()
        if isinstance(data, dict) and data:
            return data
        return {"ic_history": [], "decay_history": [], "walk_forward_results": []}

    def _save_state(self) -> None:
        # Keep only last 200 IC observations
        if len(self.state.get("ic_history", [])) > 200:
            self.state["ic_history"] = self.state["ic_history"][-200:]
        self._save_state_dict(self.state)

    def detect_decay(
        self,
        predicted: np.ndarray,
        actual: np.ndarray,
    ) -> DecayReport:
        """Analyze IC history and detect alpha decay.
        
        Args:
            predicted: Array of predicted returns/signals
            actual: Array of actual returns
            
        Returns:
            DecayReport with status, alerts, and recommendations
        """
        n = min(len(predicted), len(actual))
        if n < self.ic_window:
            return DecayReport(
                status=DecayStatus.HEALTHY,
                current_ic=0.0, mean_ic=0.0, ic_std=0.0,
                ic_trend=0.0, decay_velocity=0.0, half_life=None,
                bars_below_threshold=0, consecutive_declines=0,
                alerts=["Insufficient data for decay analysis"],
                recommendations=["Collect more data"],
            )
        
        # Compute rolling IC
        rolling_ics = compute_rolling_ic(predicted, actual, self.ic_window)
        
        if not rolling_ics:
            return DecayReport(
                status=DecayStatus.HEALTHY,
                current_ic=0.0, mean_ic=0.0, ic_std=0.0,
                ic_trend=0.0, decay_velocity=0.0, half_life=None,
                bars_below_threshold=0, consecutive_declines=0,
                alerts=["Could not compute rolling IC"],
                recommendations=[],
            )
        
        ic_array = np.array(rolling_ics)
        current_ic = float(ic_array[-1])
        mean_ic = float(np.mean(ic_array))
        ic_std = float(np.std(ic_array)) if len(ic_array) > 1 else 0.0
        
        # IC trend (slope of recent IC values)
        recent = ic_array[-min(20, len(ic_array)):]
        if len(recent) >= 2:
            x = np.arange(len(recent))
            slope = float(np.polyfit(x, recent, 1)[0])
        else:
            slope = 0.0
        
        # Decay velocity: rate of IC decline
        decay_velocity = -slope if slope < 0 else 0.0
        
        # Bars below threshold
        bars_below = 0
        for ic in reversed(ic_array):
            if ic < self.ic_decay_threshold:
                bars_below += 1
            else:
                break
        
        # Consecutive declines
        consecutive_declines = 0
        for i in range(len(ic_array) - 1, 0, -1):
            if ic_array[i] < ic_array[i - 1]:
                consecutive_declines += 1
            else:
                break
        
        # Half-life estimation (exponential decay fit)
        half_life = self._estimate_half_life(ic_array)
        
        # Determine status
        alerts = []
        recommendations = []
        
        if bars_below >= self.ic_confirm_bars:
            status = DecayStatus.DECAYING
            alerts.append(f"IC below threshold for {bars_below} bars (confirmed decay)")
            recommendations.append("Consider rotating alpha or reducing position size")
        elif current_ic < self.ic_decay_threshold:
            status = DecayStatus.WATCH
            alerts.append(f"IC {current_ic:.4f} approaching threshold {self.ic_decay_threshold}")
            recommendations.append("Monitor closely for next few bars")
        elif consecutive_declines >= 5:
            status = DecayStatus.WATCH
            alerts.append(f"IC declining for {consecutive_declines} consecutive bars")
            recommendations.append("Investigate potential alpha decay")
        else:
            status = DecayStatus.HEALTHY
        
        if half_life and half_life < 30:
            alerts.append(f"Estimated half-life {half_life:.0f} bars — alpha decaying fast")
            recommendations.append("Urgent: alpha may become unprofitable soon")
        
        if decay_velocity > 0.001:
            alerts.append(f"Decay velocity {decay_velocity:.4f} — IC dropping at accelerating rate")
        
        # Check if IC is negative (alpha is harmful)
        if current_ic < -0.05:
            status = DecayStatus.DEAD
            alerts.append(f"IC is negative ({current_ic:.4f}) — alpha is harmful")
            recommendations.append("Remove alpha from ensemble immediately")
        
        # Store in history
        self.state["ic_history"].append({
            "ic": round(current_ic, 6),
            "mean_ic": round(mean_ic, 6),
            "status": status.value,
            "timestamp": time.time(),
        })
        self._save_state()
        
        return DecayReport(
            status=status,
            current_ic=current_ic,
            mean_ic=mean_ic,
            ic_std=ic_std,
            ic_trend=slope,
            decay_velocity=decay_velocity,
            half_life=half_life,
            bars_below_threshold=bars_below,
            consecutive_declines=consecutive_declines,
            alerts=alerts,
            recommendations=recommendations,
        )
    
    def _estimate_half_life(self, ic_array: np.ndarray) -> Optional[float]:
        """Estimate half-life of IC decay using exponential fit.
        
        Fits: IC(t) = IC_0 * exp(-lambda * t)
        half_life = ln(2) / lambda
        """
        if len(ic_array) < 10:
            return None
        
        # Only fit if there's a downward trend
        recent = ic_array[-min(30, len(ic_array)):]
        if len(recent) < 10:
            return None
        
        # Find peak IC in the window
        peak_ic = float(np.max(recent))
        if peak_ic <= 0:
            return None
        
        # Normalize to [0, 1]
        normalized = recent / peak_ic
        
        # Fit exponential decay: y = exp(-lambda * x)
        x = np.arange(len(normalized), dtype=float)
        
        try:
            # Use log-linear regression: ln(y) = -lambda * x
            log_y = np.log(np.maximum(normalized, 1e-10))
            slope = float(np.polyfit(x, log_y, 1)[0])
            
            if slope >= 0:
                return None  # Not decaying
            
            lam = -slope
            if lam <= 0:
                return None
            
            half_life = math.log(2) / lam
            return min(half_life, DEFAULT_HALF_LIFE_MAX)
        except Exception:
            return None
    
    def walk_forward_splits(
        self,
        returns: np.ndarray,
        train_size: int = 100,
        test_size: int = 20,
        step_size: int = 20,
    ) -> List[WalkForwardResult]:
        """Generate walk-forward train/test splits.
        
        Each split uses train_size bars for training and test_size bars
        for testing, stepping forward by step_size bars each time.
        No lookahead bias — test always comes after train.
        """
        n = len(returns)
        results = []
        
        for start in range(0, n - train_size - test_size + 1, step_size):
            train_end = start + train_size
            test_end = train_end + test_size
            
            train_returns = returns[start:train_end]
            test_returns = returns[train_end:test_end]
            
            # Compute Sharpe for each window
            train_sharpe = self._compute_sharpe(train_returns)
            test_sharpe = self._compute_sharpe(test_returns)
            
            # Compute predictive IC: rolling signal (first N bars mean) vs next bar return
            # This measures whether past returns predict future returns — NOT lagged autocorrelation.
            # Signal at bar t = mean(returns[t-N:t]), Actual at bar t = returns[t]
            # We slide across the window to get multiple (signal, return) pairs.
            sig_window = max(5, min(20, train_size // 4))
            if train_size >= sig_window + 10:
                train_signals = np.array([
                    np.mean(train_returns[max(0, j - sig_window):j])
                    for j in range(sig_window, train_size)
                ])
                train_actuals = train_returns[sig_window:train_size]
                train_ic = compute_ic(train_signals, train_actuals)
            else:
                train_ic = 0.0
            if test_size >= sig_window + 5:
                test_signals = np.array([
                    np.mean(test_returns[max(0, j - sig_window):j])
                    for j in range(sig_window, test_size)
                ])
                test_actuals = test_returns[sig_window:test_size]
                test_ic = compute_ic(test_signals, test_actuals)
            else:
                test_ic = 0.0
            
            results.append(WalkForwardResult(
                train_start=start,
                train_end=train_end,
                test_start=train_end,
                test_end=test_end,
                train_sharpe=train_sharpe,
                test_sharpe=test_sharpe,
                train_ic=train_ic,
                test_ic=test_ic,
                degradation=train_sharpe - test_sharpe,
                wfe=(test_sharpe / train_sharpe) if train_sharpe > 0 else 0.0,
            ))
        
        return results
    
    def walk_forward_efficiency_gate(
        self,
        returns: np.ndarray,
        train_size: int = 100,
        test_size: int = 20,
        step_size: int = 20,
        threshold: float = DEFAULT_WFE_ACCEPT_THRESHOLD,
    ) -> WFEVerdict:
        """Run walk-forward splits and return the aggregated WFE gate verdict.

        This is the quantitative acceptance gate: WFE = mean(OOS Sharpe) /
        mean(IS Sharpe) must clear `threshold` (default 0.5) with a
        profitable OOS leg.  Fail-closed on thin evidence.
        """
        splits = self.walk_forward_splits(
            returns, train_size=train_size, test_size=test_size, step_size=step_size,
        )
        verdict = compute_wfe(splits, threshold=threshold)
        # Persist for status dashboards / cross-session drift tracking.
        # wfe_history keeps the last 40 verdicts so the deck can chart the trend.
        try:
            self.state["last_wfe"] = verdict.to_dict()
            history = self.state.get("wfe_history")
            if not isinstance(history, list):
                history = []
            history.append({
                "wfe": round(verdict.wfe, 4),
                "passed": verdict.passed,
                "n_folds": verdict.n_folds,
                "timestamp": time.time(),
            })
            self.state["wfe_history"] = history[-40:]
            self._save_state()
        except Exception:
            pass
        return verdict
    
    def _compute_sharpe(self, returns: np.ndarray, annualize: bool = False) -> float:
        """Compute Sharpe ratio. Delegates to core.math_utils."""
        try:
            from core.math_utils import compute_sharpe
        except ImportError:
            from core.math_utils import compute_sharpe
        ann = 252.0 if annualize else 1.0
        return compute_sharpe(returns.tolist() if hasattr(returns, 'tolist') else list(returns), ann)
    
    def get_status(self) -> Dict[str, Any]:
        """Get walk-forward validator status."""
        ic_history = self.state.get("ic_history", [])
        recent_ics = [h["ic"] for h in ic_history[-20:]]
        return {
            "total_observations": len(ic_history),
            "recent_mean_ic": round(float(np.mean(recent_ics)), 6) if recent_ics else 0.0,
            "recent_status": ic_history[-1]["status"] if ic_history else "NO_DATA",
            "ic_window": self.ic_window,
            "decay_threshold": self.ic_decay_threshold,
            "wfe_threshold": DEFAULT_WFE_ACCEPT_THRESHOLD,
            "last_wfe": self.state.get("last_wfe"),
            "wfe_history": self.state.get("wfe_history", []),
        }
