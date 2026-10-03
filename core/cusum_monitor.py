"""CUSUM Residual Monitor for Alpha Decay & Strategy Drift Detection.

Implements the Cumulative Sum (CUSUM) control chart (Part2 §5.3, Part3 §11):
    S_t = max(0, S_{t-1} + (x_t - mu_0) - K)
    If S_t > H_crit → Cut allocation by 50% (or decommission)

Also includes:
- Two-sided CUSUM for detecting shifts in both directions
- CUSUM-based structural break detection (Prado CUSUM filter)
- Real-time drift monitoring with configurable sensitivity
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import numpy as np


@dataclass
class CUSUMState:
    """Persistent state of the CUSUM monitor."""
    s_pos: float = 0.0          # Positive CUSUM (detecting upward drift)
    s_neg: float = 0.0          # Negative CUSUM (detecting downward drift)
    n_observations: int = 0
    n_breaches: int = 0
    last_breach_idx: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "s_pos": round(self.s_pos, 6),
            "s_neg": round(self.s_neg, 6),
            "n_observations": self.n_observations,
            "n_breaches": self.n_breaches,
            "last_breach_idx": self.last_breach_idx,
        }


@dataclass
class CUSUMAlert:
    """Alert generated when CUSUM threshold is breached."""
    alert_type: str       # "DRIFT_UP", "DRIFT_DOWN", "STRUCTURAL_BREAK"
    s_value: float
    threshold: float
    observation_index: int
    observation_value: float
    recommended_action: str
    allocation_multiplier: float  # 0.5 for drift, 0.0 for severe break

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_type": self.alert_type,
            "s_value": round(self.s_value, 4),
            "threshold": round(self.threshold, 4),
            "observation_index": self.observation_index,
            "recommended_action": self.recommended_action,
            "allocation_multiplier": self.allocation_multiplier,
        }


class CUSUMMonitor:
    """Two-sided CUSUM monitor for alpha drift and strategy degradation.

    Tracks cumulative deviations from an expected mean (mu_0) and generates
    alerts when the CUSUM statistic exceeds the critical threshold H.

    Typical usage:
        monitor = CUSUMMonitor(mu_0=0.0, threshold=4.0)
        for residual in residuals:
            alert = monitor.update(residual)
            if alert:
                reduce_allocation(0.5)
    """

    def __init__(
        self,
        mu_0: float = 0.0,
        k: float = 0.5,
        threshold: float = 4.0,
        warmup_period: int = 20,
    ):
        """
        Args:
            mu_0: Expected value of the monitored signal (e.g., 0 for residuals).
            k: Allowable slack / reference value (smaller = more sensitive).
            threshold: Critical threshold H. Breach triggers alert.
            warmup_period: Number of observations before monitoring is active.
        """
        self.mu_0 = float(mu_0)
        self.k = max(0.01, float(k))
        self.threshold = max(1.0, float(threshold))
        self.warmup_period = int(warmup_period)
        self.state = CUSUMState()

    def update(self, x: float, index: Optional[int] = None) -> Optional[CUSUMAlert]:
        """Process a new observation and check for drift/breach.

        Args:
            x: New observation (e.g., residual, R-multiple, IC value).
            index: Optional observation index for tracking.

        Returns:
            CUSUMAlert if threshold breached, None otherwise.
        """
        val = float(x)
        idx = index if index is not None else self.state.n_observations

        # Update CUSUM statistics
        # Positive CUSUM: detects upward shifts (mu > mu_0)
        self.state.s_pos = max(0.0, self.state.s_pos + (val - self.mu_0) - self.k)
        # Negative CUSUM: detects downward shifts (mu < mu_0)
        self.state.s_neg = max(0.0, self.state.s_neg - (val - self.mu_0) - self.k)

        self.state.n_observations = idx + 1

        # Check threshold after warmup
        if self.state.n_observations <= self.warmup_period:
            return None

        alert = None

        if self.state.s_pos > self.threshold:
            alert = CUSUMAlert(
                alert_type="DRIFT_UP",
                s_value=self.state.s_pos,
                threshold=self.threshold,
                observation_index=idx,
                observation_value=val,
                recommended_action="CUT_ALLOCATION_50PCT",
                allocation_multiplier=0.5,
            )
            self.state.n_breaches += 1
            self.state.last_breach_idx = idx
            self.state.s_pos = 0.0  # Reset after alert

        elif self.state.s_neg > self.threshold:
            alert = CUSUMAlert(
                alert_type="DRIFT_DOWN",
                s_value=self.state.s_neg,
                threshold=self.threshold,
                observation_index=idx,
                observation_value=val,
                recommended_action="CUT_ALLOCATION_50PCT",
                allocation_multiplier=0.5,
            )
            self.state.n_breaches += 1
            self.state.last_breach_idx = idx
            self.state.s_neg = 0.0  # Reset after alert

        return alert

    def update_batch(self, observations: List[float]) -> List[CUSUMAlert]:
        """Process a batch of observations and return all alerts."""
        alerts = []
        for i, val in enumerate(observations):
            alert = self.update(val, index=self.state.n_observations + i)
            if alert is not None:
                alerts.append(alert)
        return alerts

    def get_allocation_multiplier(self) -> float:
        """Returns 0.5 if any breach has occurred, 1.0 otherwise.

        This implements the Part2 §5.3 rule: CUSUM S_t > H_crit → Cut allocation by 50%.
        """
        if self.state.n_breaches > 0:
            return 0.5
        return 1.0

    def reset(self) -> None:
        """Reset the CUSUM state."""
        self.state = CUSUMState()

    def get_status(self) -> Dict[str, Any]:
        """Current CUSUM status."""
        return {
            **self.state.to_dict(),
            "mu_0": self.mu_0,
            "k": self.k,
            "threshold": self.threshold,
            "warmup_period": self.warmup_period,
            "allocation_multiplier": self.get_allocation_multiplier(),
        }


class CUSUMStructuralBreakDetector:
    """CUSUM-based structural break / bubble detector (Prado CUSUM filter).

    Detects regime breaks in cumulative returns:
        cusum_pos = max(0, cusum_pos + r_t)
        cusum_neg = min(0, cusum_neg + r_t)
        if cusum_pos > threshold → break detected (bullish regime)
        if cusum_neg < -threshold → break detected (bearish regime)
    """

    def __init__(self, threshold: float = 0.05):
        """
        Args:
            threshold: CUSUM threshold for break detection in return space.
        """
        self.threshold = max(0.001, float(threshold))
        self.cusum_pos = 0.0
        self.cusum_neg = 0.0
        self.breaks: List[int] = []

    def update(self, return_val: float, index: int = 0) -> bool:
        """Process a return and detect structural breaks.

        Returns True if a break was detected.
        """
        r = float(return_val)
        self.cusum_pos = max(0.0, self.cusum_pos + r)
        self.cusum_neg = min(0.0, self.cusum_neg + r)

        detected = False
        if self.cusum_pos > self.threshold:
            self.breaks.append(index)
            self.cusum_pos = 0.0
            self.cusum_neg = 0.0
            detected = True
        elif self.cusum_neg < -self.threshold:
            self.breaks.append(index)
            self.cusum_pos = 0.0
            self.cusum_neg = 0.0
            detected = True

        return detected

    def get_breaks(self) -> List[int]:
        """Return list of detected break indices."""
        return list(self.breaks)

    def reset(self) -> None:
        self.cusum_pos = 0.0
        self.cusum_neg = 0.0
        self.breaks = []


# Canonical aliases
CUSUMResidualMonitor = CUSUMMonitor
CumulativeSumMonitor = CUSUMMonitor
CUSUMControlChart = CUSUMMonitor
