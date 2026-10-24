"""Alpha Decay Monitor — Real-Time Alpha Quality Monitoring.

Tracks the rolling Information Coefficient (IC) of each alpha factor and
detects when predictive power degrades below configurable thresholds.
Triggers alerts via the operator outbox so the trading system can react
(e.g., reduce position size, halt trading, or flag degraded alphas).

Design:
  - Rolling Spearman IC computed on each data fetch (60-bar window)
  - Decay detection: IC drops below threshold for N consecutive bars
  - Trend analysis: linear regression of IC over recent window
  - Alerts: INFO (first warning), WARNING (sustained decay), CRITICAL (IC reversal)
  - State persistence: JSON file so monitoring survives restarts

References:
    Kakushadze, Z. (2016). "101 Formulaic Alphas." SSRN 2701346.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.state_persistence import StoreBackedState

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

log = logging.getLogger(__name__)


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Ensure value is finite float, defaulting on NaN/Inf/None."""
    try:
        f = float(val)
        return default if not math.isfinite(f) else f
    except (ValueError, TypeError):
        return default

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

DEFAULT_IC_WINDOW = 60          # Bars for rolling IC computation
DEFAULT_DECAY_THRESHOLD = 0.02  # IC below this = degraded
DEFAULT_REVERSAL_THRESHOLD = -0.01  # IC below this = reversed (negative alpha)
DEFAULT_CONSECUTIVE_BARS = 5    # How many consecutive low-IC bars before alert
DEFAULT_TREND_WINDOW = 30       # Bars for IC trend regression
DEFAULT_STATE_FILE = "alpha_decay_state.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AlphaHealth:
    """Health status of a single alpha factor."""
    name: str
    ic_current: float = 0.0           # Most recent purified IC value
    ic_mean: float = 0.0              # Mean purified IC over rolling window
    ic_std: float = 0.0               # Std of purified IC
    ic_trend: float = 0.0             # Slope of IC regression (positive = improving)
    ic_consecutive_low: int = 0       # Consecutive bars with IC < threshold
    status: str = "HEALTHY"           # HEALTHY / DEGRADED / REVERSED / DEAD
    last_alert_time: float = 0.0      # Timestamp of last alert
    alert_count: int = 0              # Total alerts sent
    # Raw IC (before orthogonalization) — only populated when orthogonalize=True
    ic_raw_current: float = 0.0       # Most recent raw IC value
    ic_raw_mean: float = 0.0          # Mean raw IC over rolling window


@dataclass
class DecayAlert:
    """An alpha decay alert."""
    alpha_name: str
    severity: str          # INFO / WARNING / CRITICAL
    code: str              # Alert code (e.g. "ALPHA_DECAY_012")
    ic_value: float
    ic_mean: float
    ic_trend: float
    consecutive_low: int
    message: str
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# IC computation (reuses logic from alpha_ensemble)
# ---------------------------------------------------------------------------

def _compute_rolling_ic(
    alpha: pd.Series,
    forward_returns: pd.Series,
    window: int,
) -> pd.Series:
    """Rolling Spearman IC — manual loop for correctness."""
    a = alpha.values.astype(float)
    r = forward_returns.values.astype(float)
    n = len(a)
    result = np.full(n, np.nan)

    for i in range(window - 1, n):
        start = i - window + 1
        x = a[start : i + 1]
        y = r[start : i + 1]
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() >= 10:
            xs, ys = x[mask], y[mask]
            # Constant inputs make spearmanr undefined (ConstantInputWarning
            # on weekend/flat data); IC is 0 there.
            if np.ptp(xs) == 0 or np.ptp(ys) == 0:
                result[i] = 0.0
            else:
                corr, _ = sp_stats.spearmanr(xs, ys)
                result[i] = corr

    return pd.Series(result, index=alpha.index)


def _compute_ic_trend(ic_series: pd.Series, window: int) -> float:
    """Linear regression slope of IC over recent window.

    Positive slope = IC improving, negative = decaying.
    Returns slope value (bars per unit IC change).
    """
    recent = ic_series.dropna().tail(window)
    if len(recent) < 10:
        return 0.0
    x = np.arange(len(recent), dtype=float)
    y = recent.values
    mask = np.isfinite(y)
    if mask.sum() < 10:
        return 0.0
    slope, _, _, _, _ = sp_stats.linregress(x[mask], y[mask])
    return float(slope) if np.isfinite(slope) else 0.0


# ---------------------------------------------------------------------------
# Main monitor class
# ---------------------------------------------------------------------------

class AlphaDecayMonitor(StoreBackedState):
    """Real-time alpha quality monitoring and decay detection.

    Orthogonalizes alpha signals against market factors before IC computation
    by default (ψ from UAQP Agent σ). This ensures decay detection measures
    UNIQUE predictive power, not shared market beta.

    When orthogonalize=True (default):
      1. Alpha signals are regressed against market factors (returns, vol, etc.)
      2. Residuals (purified signals) are used for IC computation
      3. IC reflects alpha's independent edge, not market correlation
      4. Re-weighting after decay uses purified signals for more stable weights

    Usage:
        monitor = AlphaDecayMonitor(alert_db_path="trading_state.db", orthogonalize=True)
        health = monitor.update(alpha_df, alpha_columns, forward_returns)
        alerts = monitor.check_decay()
    """

    store_key = "alpha_decay_state"

    def __init__(
        self,
        ic_window: int = DEFAULT_IC_WINDOW,
        decay_threshold: float = DEFAULT_DECAY_THRESHOLD,
        reversal_threshold: float = DEFAULT_REVERSAL_THRESHOLD,
        consecutive_bars: int = DEFAULT_CONSECUTIVE_BARS,
        trend_window: int = DEFAULT_TREND_WINDOW,
        alert_db_path: str = "",
        state_file: str = DEFAULT_STATE_FILE,
        cooldown_seconds: float = 300.0,
        orthogonalize: bool = True,
        ortho_method: str = "ols",
        ortho_rolling_window: int = 120,
        store: Optional[Any] = None,
    ):
        self.ic_window = ic_window
        self.decay_threshold = decay_threshold
        self.reversal_threshold = reversal_threshold
        self.consecutive_bars = consecutive_bars
        self.trend_window = trend_window
        self.alert_db_path = alert_db_path
        self.state_file = state_file
        self.store = store
        self.cooldown_seconds = cooldown_seconds
        self.orthogonalize = orthogonalize
        self.ortho_method = ortho_method
        self.ortho_rolling_window = ortho_rolling_window

        # Per-alpha state
        self._health: Dict[str, AlphaHealth] = {}
        self._ic_history: Dict[str, List[float]] = {}

        # Orthogonalization diagnostics
        self._last_ortho_result: Optional[Any] = None

        # Load persisted state
        self._load_state()
        if store is not None:
            self.attach_store(store)

    # -------------------------------------------------------------------
    # Core monitoring
    # -------------------------------------------------------------------

    def update(
        self,
        alpha_df: pd.DataFrame,
        alpha_columns: List[str],
        forward_returns: Optional[pd.Series] = None,
    ) -> Dict[str, AlphaHealth]:
        """Update IC tracking for all alpha columns.

        When orthogonalize=True, signals are purified against market factors
        before IC computation. This ensures decay detection measures UNIQUE
        predictive power, not shared market beta.

        Parameters
        ----------
        alpha_df : pd.DataFrame
            DataFrame with alpha columns and 'close' column.
        alpha_columns : list[str]
            Names of alpha columns to monitor.
        forward_returns : pd.Series, optional
            Pre-computed forward returns. If None, computed from 'close'.

        Returns
        -------
        dict mapping alpha name → AlphaHealth
        """
        if forward_returns is None:
            forward_returns = alpha_df["close"].pct_change().shift(-1)

        # P0-2: Check N/T noise ratio
        n_alphas = len(alpha_columns)
        r = n_alphas / float(self.ic_window)
        if r > 0.25:
            log.warning(
                "AlphaDecayMonitor N/T ratio r=%.3f > 0.25 (N=%d, T=%d). "
                "High noise sensitivity regime in rolling IC estimation.",
                r, n_alphas, self.ic_window,
            )

        # --- Signal orthogonalization (ψ) ---
        # Re-purify signals against market factors before IC computation.
        # This is the key integration: decay detection uses PURIFIED signals
        # so IC reflects unique alpha edge, not market correlation.
        work_df = alpha_df
        if self.orthogonalize:
            work_df = self._orthogonalize_for_decay(alpha_df, alpha_columns)

        for col in alpha_columns:
            if col not in work_df.columns:
                continue

            alpha = work_df[col]

            # Compute rolling IC (on purified signal if orthogonalize=True)
            ic_series = _compute_rolling_ic(alpha, forward_returns, self.ic_window)
            ic_current = _safe_float(ic_series.iloc[-1]) if not ic_series.empty else 0.0

            # IC statistics
            ic_valid = ic_series.dropna()
            ic_mean = _safe_float(ic_valid.mean()) if len(ic_valid) > 0 else 0.0
            ic_std = _safe_float(ic_valid.std(), 1.0) if len(ic_valid) > 1 else 1.0

            # IC trend
            ic_trend = _safe_float(_compute_ic_trend(ic_series, self.trend_window))

            # Consecutive low IC bars
            consecutive_low = 0
            for val in reversed(ic_valid.values):
                if val < self.decay_threshold:
                    consecutive_low += 1
                else:
                    break

            # --- Raw IC (before orthogonalization) for comparison ---
            ic_raw_current = 0.0
            ic_raw_mean = 0.0
            if self.orthogonalize and col in alpha_df.columns:
                raw_series = _compute_rolling_ic(
                    alpha_df[col], forward_returns, self.ic_window,
                )
                raw_valid = raw_series.dropna()
                ic_raw_current = _safe_float(raw_series.iloc[-1]) if not raw_series.empty else 0.0
                ic_raw_mean = _safe_float(raw_valid.mean()) if len(raw_valid) > 0 else 0.0

            # Determine status
            status = self._determine_status(ic_current, ic_mean, ic_trend, consecutive_low)

            # Get or create health record
            if col not in self._health:
                self._health[col] = AlphaHealth(name=col)
            h = self._health[col]
            h.ic_current = ic_current
            h.ic_mean = ic_mean
            h.ic_std = ic_std
            h.ic_trend = ic_trend
            h.ic_consecutive_low = consecutive_low
            h.status = status
            h.ic_raw_current = ic_raw_current
            h.ic_raw_mean = ic_raw_mean

            # Track IC history for trend analysis
            if col not in self._ic_history:
                self._ic_history[col] = []
            self._ic_history[col].append(ic_current)
            # Keep last 500 IC values
            if len(self._ic_history[col]) > 500:
                self._ic_history[col] = self._ic_history[col][-500:]

        # Persist state
        self._save_state()

        return dict(self._health)

    def check_decay(self) -> List[DecayAlert]:
        """Check all tracked alphas for decay and generate alerts.

        Returns a list of DecayAlert objects. Alerts are also automatically
        enqueued to the operator outbox if alert_db_path is configured.
        """
        alerts = []
        now = time.time()

        for name, h in self._health.items():
            # Check cooldown
            if now - h.last_alert_time < self.cooldown_seconds:
                continue

            alert = self._evaluate_alpha(h, now)
            if alert:
                alerts.append(alert)
                h.last_alert_time = now
                h.alert_count += 1

                # Enqueue to operator outbox
                self._emit_alert(alert)

                # P0-1: Log knockout reason to lessons.jsonl and TradingMemory Level 2
                if alert.severity == "CRITICAL" or alert.code.startswith(("ALPHA_DEAD_", "ALPHA_REVERSED_")):
                    self._log_decay_knockout_to_lessons(alert)
                    self._record_decay_knockout_to_memory(alert)

        return alerts

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all alpha health statuses."""
        summary = {
            "total_alphas": len(self._health),
            "healthy": 0,
            "degraded": 0,
            "reversed": 0,
            "dead": 0,
            "alphas": {},
        }
        for name, h in self._health.items():
            summary[h.status.lower()] = summary.get(h.status.lower(), 0) + 1
            entry = {
                "status": h.status,
                "ic_current": round(h.ic_current, 4),
                "ic_mean": round(h.ic_mean, 4),
                "ic_trend": round(h.ic_trend, 6),
                "consecutive_low": h.ic_consecutive_low,
                "alert_count": h.alert_count,
            }
            # Raw vs purified IC comparison
            if self.orthogonalize and h.ic_raw_mean != 0.0:
                entry["ic_raw_current"] = round(h.ic_raw_current, 4)
                entry["ic_raw_mean"] = round(h.ic_raw_mean, 4)
                # Signal quality: fraction of IC that is genuine (not market beta)
                if abs(h.ic_raw_mean) > 1e-8:
                    entry["signal_purity"] = round(h.ic_mean / h.ic_raw_mean, 4)
                else:
                    entry["signal_purity"] = 0.0
            summary["alphas"][name] = entry
        # Add orthogonalization info
        if self._last_ortho_result:
            summary["orthogonalization"] = self._last_ortho_result
        elif self.orthogonalize:
            summary["orthogonalization"] = {
                "method": self.ortho_method,
                "status": "not_yet_run",
            }

        return summary

    def format_report(self) -> str:
        """Format a human-readable alpha health report with IC comparison."""
        s = self.get_summary()
        has_raw = any("ic_raw_mean" in v for v in s["alphas"].values())
        lines = [
            "=" * 80,
            "ALPHA DECAY MONITOR — HEALTH REPORT",
            "=" * 80,
            f"Total: {s['total_alphas']}  |  "
            f"Healthy: {s['healthy']}  |  "
            f"Degraded: {s['degraded']}  |  "
            f"Reversed: {s['reversed']}  |  "
            f"Dead: {s['dead']}",
            "",
        ]
        # Main health table
        if has_raw:
            header = (
                f"{'Alpha':<14} {'Status':<10} {'Purified':>8} {'Raw':>8} "
                f"{'Purity':>8} {'Trend':>10} {'Low':>5} {'Alerts':>7}"
            )
        else:
            header = (
                f"{'Alpha':<14} {'Status':<10} {'IC':>8} {'IC_Mean':>8} "
                f"{'Trend':>10} {'Low_Bars':>9} {'Alerts':>7}"
            )
        lines.append(header)
        lines.append("-" * 80)
        for name, info in sorted(
            s["alphas"].items(), key=lambda x: x[1]["ic_mean"], reverse=True
        ):
            if has_raw:
                raw_mean = info.get("ic_raw_mean", 0.0)
                purity = info.get("signal_purity", 0.0)
                lines.append(
                    f"{name:<14} {info['status']:<10} "
                    f"{info['ic_mean']:>8.4f} {raw_mean:>8.4f} "
                    f"{purity:>7.1%} {info['ic_trend']:>10.6f} "
                    f"{info['consecutive_low']:>5d} {info['alert_count']:>7d}"
                )
            else:
                lines.append(
                    f"{name:<14} {info['status']:<10} {info['ic_current']:>8.4f} "
                    f"{info['ic_mean']:>8.4f} {info['ic_trend']:>10.6f} "
                    f"{info['consecutive_low']:>9d} {info['alert_count']:>7d}"
                )
        lines.append("=" * 80)
        # IC comparison section
        if has_raw:
            lines.append("")
            lines.append("RAW vs PURIFIED IC COMPARISON")
            lines.append("-" * 80)
            lines.append(
                f"{'Alpha':<14} {'Raw_IC':>8} {'Purified':>9} {'Delta':>8} "
                f"{'Purity':>8} {'Interpretation':<30}"
            )
            lines.append("-" * 80)
            for name, info in sorted(
                s["alphas"].items(), key=lambda x: abs(x[1].get("ic_raw_mean", 0)), reverse=True
            ):
                raw_m = info.get("ic_raw_mean", 0.0)
                pur_m = info.get("ic_mean", 0.0)
                purity = info.get("signal_purity", 0.0)
                delta = pur_m - raw_m
                if abs(raw_m) < 0.005:
                    interp = "No edge detected"
                elif purity > 1.2:
                    interp = "Alpha amplified by denoising"
                elif purity > 0.8:
                    interp = "Mostly genuine alpha"
                elif purity > 0.5:
                    interp = "Partial market beta"
                elif purity > 0.2:
                    interp = "High market correlation"
                else:
                    interp = "Almost entirely beta"
                lines.append(
                    f"{name:<14} {raw_m:>8.4f} {pur_m:>9.4f} {delta:>+8.4f} "
                    f"{purity:>7.1%}   {interp}"
                )
            lines.append("=" * 80)
            lines.append("Purity = Purified_IC / Raw_IC.  >80% = genuine, <20% = mostly beta.")
        return "\n".join(lines)

    # -------------------------------------------------------------------
    # Signal orthogonalization for decay detection
    # -------------------------------------------------------------------

    def _orthogonalize_for_decay(
        self,
        alpha_df: pd.DataFrame,
        alpha_columns: List[str],
    ) -> pd.DataFrame:
        """Orthogonalize alpha signals before IC computation.

        Uses the ψ function from UAQP Agent σ to purify signals against
        market factors. Returns a copy of alpha_df with purified alpha columns.

        This ensures decay detection measures each alpha's UNIQUE predictive
        power, not shared market beta. An alpha that's correlated with market
        returns won't falsely appear healthy just because the market is trending.
        """
        try:
            from core.signal_orthogonalizer import orthogonalize_for_ensemble

            # Extract normalized alpha signals
            alpha_signals = {}
            for col in alpha_columns:
                if col in alpha_df.columns:
                    series = alpha_df[col]
                    # Z-score normalize for orthogonalization
                    mean = series.expanding(min_periods=20).mean()
                    std = series.expanding(min_periods=20).std().replace(0, 1)
                    alpha_signals[col] = ((series - mean) / std).fillna(0.0)

            if not alpha_signals:
                return alpha_df

            # Orthogonalize against market factors
            purified = orthogonalize_for_ensemble(
                alpha_signals, alpha_df,
                method=self.ortho_method,
                rolling_window=self.ortho_rolling_window,
            )

            # Build result DataFrame with purified signals
            result = alpha_df.copy()
            for col, purified_series in purified.items():
                if col in result.columns:
                    result[col] = purified_series

            # Store diagnostics
            self._last_ortho_result = {
                "method": self.ortho_method,
                "n_alphas": len(purified),
                "n_factors": 5,  # Default factor count
                "window": self.ortho_rolling_window,
            }

            log.debug(
                "Decay monitor: orthogonalized %d signals (method=%s)",
                len(purified), self.ortho_method,
            )

            return result

        except Exception as exc:
            log.warning(
                "Decay monitor orthogonalization failed (using raw signals): %s", exc
            )
            return alpha_df

    # -------------------------------------------------------------------
    # Internal methods
    # -------------------------------------------------------------------

    def _determine_status(
        self,
        ic_current: float,
        ic_mean: float,
        ic_trend: float,
        consecutive_low: int,
    ) -> str:
        """Determine alpha health status from IC metrics."""
        if ic_current < self.reversal_threshold and consecutive_low >= self.consecutive_bars:
            return "REVERSED"
        if ic_mean < self.reversal_threshold:
            return "DEAD"
        if consecutive_low >= self.consecutive_bars:
            return "DEGRADED"
        if ic_trend < -0.001 and ic_mean < self.decay_threshold:
            return "DEGRADED"
        return "HEALTHY"

    def _evaluate_alpha(self, h: AlphaHealth, now: float) -> Optional[DecayAlert]:
        """Evaluate a single alpha and return an alert if decay detected."""
        # Determine severity
        if h.status == "REVERSED":
            severity = "CRITICAL"
            code = f"ALPHA_REVERSED_{h.name}"
            message = (
                f"Alpha {h.name} has REVERSED: IC={h.ic_current:.4f} "
                f"(mean={h.ic_mean:.4f}, trend={h.ic_trend:.6f}). "
                f"Negative IC means alpha trades against profitable signals. "
                f"Consider removing from ensemble."
            )
        elif h.status == "DEAD":
            severity = "CRITICAL"
            code = f"ALPHA_DEAD_{h.name}"
            message = (
                f"Alpha {h.name} is DEAD: mean IC={h.ic_mean:.4f} "
                f"below reversal threshold ({self.reversal_threshold}). "
                f"No predictive power remaining."
            )
        elif h.status == "DEGRADED":
            severity = "WARNING"
            code = f"ALPHA_DECAY_{h.name}"
            message = (
                f"Alpha {h.name} DEGRADED: IC={h.ic_current:.4f} "
                f"below {self.decay_threshold} for {h.ic_consecutive_low} bars. "
                f"Trend={h.ic_trend:.6f} ({'improving' if h.ic_trend > 0 else 'decaying'})."
            )
        else:
            return None

        return DecayAlert(
            alpha_name=h.name,
            severity=severity,
            code=code[:200],
            ic_value=h.ic_current,
            ic_mean=h.ic_mean,
            ic_trend=h.ic_trend,
            consecutive_low=h.ic_consecutive_low,
            message=message,
            timestamp=now,
        )

    def _emit_alert(self, alert: DecayAlert) -> None:
        """Enqueue alert to the operator outbox."""
        if not self.alert_db_path:
            return
        try:
            # Upsert, not enqueue: the payload embeds live IC metrics, so a
            # plain enqueue hashes a new fingerprint every check and piles up
            # duplicate PENDING rows (audit 2026-09-26: 185 copies of one
            # alpha's alert). upsert_alert refreshes the unresolved row in
            # place and only inserts fresh after the previous alert resolved.
            from operations.alert_outbox import upsert_alert
            upsert_alert(
                self.alert_db_path,
                [{
                    "code": alert.code,
                    "severity": alert.severity,
                    "details": {
                        "alpha": alert.alpha_name,
                        "ic_current": alert.ic_value,
                        "ic_mean": alert.ic_mean,
                        "ic_trend": alert.ic_trend,
                        "consecutive_low": alert.consecutive_low,
                        "message": alert.message,
                    },
                }],
                channel="operator",
            )
            log.info("Alpha decay alert upserted: %s [%s]", alert.code, alert.severity)
        except Exception as exc:
            log.warning("Failed to enqueue alpha decay alert: %s", exc)

    def _log_decay_knockout_to_lessons(self, alert: DecayAlert) -> None:
        """Log dead/reversed alpha decay knockout lesson to lessons.jsonl (P0-1)."""
        try:
            from pathlib import Path
            import json
            from datetime import datetime, timezone
            status_str = "DEAD" if "DEAD" in alert.code else ("REVERSED" if "REVERSED" in alert.code else "DEGRADED")
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "kind": "alpha_decay_knockout",
                "alpha_name": alert.alpha_name,
                "severity": alert.severity,
                "code": alert.code,
                "status": status_str,
                "ic_current": round(float(alert.ic_value), 4),
                "ic_mean": round(float(alert.ic_mean), 4),
                "consecutive_low": int(alert.consecutive_low),
                "reason": alert.message,
            }
            lessons_path = Path("lessons.jsonl")
            with open(lessons_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            log.info("Decay knockout logged to lessons.jsonl: %s", alert.alpha_name)
        except Exception as exc:
            log.warning("Failed to write decay knockout to lessons.jsonl: %s", exc)

    def _record_decay_knockout_to_memory(self, alert: DecayAlert) -> None:
        """Record decay knockout to TradingMemory Level 2 verified memories (P0-1)."""
        try:
            from intelligence.trading_memory import TradingMemory
            db_path = self.alert_db_path if self.alert_db_path else "trading_state.db"
            tm = TradingMemory(db_path)
            status_str = "DEAD" if "DEAD" in alert.code else ("REVERSED" if "REVERSED" in alert.code else "DEGRADED")
            source_id = f"decay_knockout:{alert.alpha_name}:{int(alert.timestamp)}"
            summary = f"Alpha {alert.alpha_name} knocked out from ensemble ({status_str}, IC={alert.ic_value:.4f}, mean={alert.ic_mean:.4f})"
            lesson = f"Alpha {alert.alpha_name} entered {status_str} status; zeroed out to prevent capital erosion and Sharpe degradation."
            evidence = {
                "alpha": alert.alpha_name,
                "status": status_str,
                "ic_current": alert.ic_value,
                "ic_mean": alert.ic_mean,
                "ic_trend": alert.ic_trend,
                "consecutive_low": alert.consecutive_low,
            }
            tm.save_verified_memory(
                source_id=source_id,
                kind="alpha_decay_knockout",
                tags={"alpha": alert.alpha_name, "status": status_str, "layer": "level_2_pattern"},
                summary=summary,
                lesson=lesson,
                evidence=evidence,
                confidence=0.95,
                status="APPROVED",
            )
            log.info("Decay knockout recorded to TradingMemory Level 2: %s", alert.alpha_name)
        except Exception as exc:
            log.warning("Failed to record decay knockout to TradingMemory: %s", exc)

    def get_alive_alphas(self) -> List[str]:
        """Return list of alpha names that are not DEAD or REVERSED (P0-1)."""
        return [
            name for name, h in self._health.items()
            if h.status not in ("DEAD", "REVERSED")
        ]

    def filter_alive(self, alpha_columns: List[str]) -> List[str]:
        """Filter a list of alpha columns to include only alive ones (P0-1)."""
        alive = set(self.get_alive_alphas())
        if not self._health:
            return alpha_columns
        result = [col for col in alpha_columns if col in alive or col not in self._health]
        return result if result else alpha_columns

    # -------------------------------------------------------------------
    # State persistence
    # -------------------------------------------------------------------

    def _save_state(self) -> None:
        """Persist monitoring state to JSON file."""
        try:
            state = {
                "health": {
                    name: {
                        "ic_current": _safe_float(h.ic_current),
                        "ic_mean": _safe_float(h.ic_mean),
                        "ic_std": _safe_float(h.ic_std, 1.0),
                        "ic_trend": _safe_float(h.ic_trend),
                        "ic_consecutive_low": int(h.ic_consecutive_low),
                        "status": str(h.status),
                        "last_alert_time": _safe_float(h.last_alert_time),
                        "alert_count": int(h.alert_count),
                        "ic_raw_current": _safe_float(h.ic_raw_current),
                        "ic_raw_mean": _safe_float(h.ic_raw_mean),
                    }
                    for name, h in self._health.items()
                },
                "ic_history": {
                    name: [_safe_float(v) for v in vals[-100:]]  # Keep last 100 in persistence
                    for name, vals in self._ic_history.items()
                },
            }
            self._save_state_dict(state)
        except Exception as exc:
            log.debug("Alpha decay state save failed: %s", exc)

    def _load_state(self) -> None:
        """Load persisted monitoring state."""
        try:
            state = self._load_state_dict()
            if not state:
                return
            for name, data in state.get("health", {}).items():
                h = AlphaHealth(name=name)
                h.ic_current = _safe_float(data.get("ic_current", 0.0))
                h.ic_mean = _safe_float(data.get("ic_mean", 0.0))
                h.ic_std = _safe_float(data.get("ic_std", 1.0), 1.0)
                h.ic_trend = _safe_float(data.get("ic_trend", 0.0))
                h.ic_consecutive_low = int(data.get("ic_consecutive_low", 0))
                h.status = str(data.get("status", "HEALTHY"))
                h.last_alert_time = _safe_float(data.get("last_alert_time", 0.0))
                h.alert_count = int(data.get("alert_count", 0))
                h.ic_raw_current = _safe_float(data.get("ic_raw_current", 0.0))
                h.ic_raw_mean = _safe_float(data.get("ic_raw_mean", 0.0))
                self._health[name] = h
            for name, vals in state.get("ic_history", {}).items():
                self._ic_history[name] = [_safe_float(v) for v in vals]
            log.debug("Loaded alpha decay state for %d alphas", len(self._health))
        except Exception as exc:
            log.debug("Alpha decay state load failed: %s", exc)
