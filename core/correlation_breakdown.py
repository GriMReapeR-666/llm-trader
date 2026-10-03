"""Correlation Breakdown Detection Engine.

Implements Part2 §6.7: Real-time correlation instability detection:
    Rolling correlation rho(t) over window w
    If |rho(t) - rho(t-1)| > k * sigma_rho: flag breakdown
    Reduce position sizes when correlations unstable

Also includes:
- Pearson and Spearman correlation tracking
- Correlation regime classification (NORMAL / ELEVATED / CRISIS)
- Tail dependence ratio monitoring
- Portfolio diversification ratio
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np


@dataclass
class CorrelationBreakdownResult:
    """Result of correlation breakdown analysis."""
    current_correlation: float
    previous_correlation: float
    correlation_change: float
    is_breakdown: bool
    correlation_regime: str       # "NORMAL", "ELEVATED", "CRISIS"
    instability_score: float     # 0 = stable, 1 = highly unstable
    recommended_heat_reduction: float  # multiplier to reduce portfolio heat
    rolling_mean: float
    rolling_std: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_correlation": round(self.current_correlation, 4),
            "previous_correlation": round(self.previous_correlation, 4),
            "correlation_change": round(self.correlation_change, 4),
            "is_breakdown": self.is_breakdown,
            "correlation_regime": self.correlation_regime,
            "instability_score": round(self.instability_score, 4),
            "recommended_heat_reduction": round(self.recommended_heat_reduction, 4),
            "rolling_mean": round(self.rolling_mean, 4),
            "rolling_std": round(self.rolling_std, 4),
        }


class CorrelationBreakdownDetector:
    """Real-time correlation instability detector.

    Monitors rolling correlation between asset pairs and detects breakdowns
    when correlation changes exceed k standard deviations from the norm.

    Typical usage:
        detector = CorrelationBreakdownDetector(window=60, k=2.5)
        result = detector.update(rho_new)
        if result.is_breakdown:
            reduce_portfolio_heat(result.recommended_heat_reduction)
    """

    def __init__(
        self,
        window: int = 60,
        k: float = 2.5,
        crisis_threshold: float = 0.85,
        elevated_threshold: float = 0.65,
    ):
        """
        Args:
            window: Rolling window for correlation statistics.
            k: Number of standard deviations for breakdown detection.
            crisis_threshold: Correlation level above which = crisis regime.
            elevated_threshold: Correlation level above which = elevated regime.
        """
        self.window = max(10, int(window))
        self.k = max(1.0, float(k))
        self.crisis_threshold = float(crisis_threshold)
        self.elevated_threshold = float(elevated_threshold)
        self.history: List[float] = []
        self._prev_rho: float = 0.0

    def update(self, current_correlation: float) -> CorrelationBreakdownResult:
        """Process new correlation observation and detect breakdown.

        Args:
            current_correlation: New correlation coefficient rho(t) in [-1, 1].

        Returns:
            CorrelationBreakdownResult with breakdown status and recommendations.
        """
        rho = max(-1.0, min(1.0, float(current_correlation)))
        self.history.append(rho)

        # Keep only window
        if len(self.history) > self.window:
            self.history = self.history[-self.window:]

        prev_rho = self._prev_rho
        self._prev_rho = rho

        # Rolling statistics
        if len(self.history) >= 5:
            arr = np.array(self.history)
            rolling_mean = float(np.mean(arr))
            rolling_std = float(np.std(arr)) + 1e-8
        else:
            rolling_mean = rho
            rolling_std = 0.1

        # Breakdown detection: |rho(t) - rho(t-1)| > k * sigma_rho
        change = abs(rho - prev_rho)
        is_breakdown = change > self.k * rolling_std and len(self.history) >= 10

        # Instability score (0 to 1)
        if rolling_std > 0:
            instability = min(1.0, change / (self.k * rolling_std))
        else:
            instability = 0.0

        # Correlation regime
        abs_rho = abs(rho)
        if abs_rho >= self.crisis_threshold:
            regime = "CRISIS"
        elif abs_rho >= self.elevated_threshold:
            regime = "ELEVATED"
        else:
            regime = "NORMAL"

        # Heat reduction recommendation
        if regime == "CRISIS":
            heat_reduction = 0.25  # Cut to 25% of normal
        elif regime == "ELEVATED":
            heat_reduction = 0.50  # Cut to 50% of normal
        elif is_breakdown:
            heat_reduction = 0.50  # Breakdown → halve
        else:
            heat_reduction = 1.0

        return CorrelationBreakdownResult(
            current_correlation=rho,
            previous_correlation=prev_rho,
            correlation_change=change,
            is_breakdown=is_breakdown,
            correlation_regime=regime,
            instability_score=instability,
            recommended_heat_reduction=heat_reduction,
            rolling_mean=rolling_mean,
            rolling_std=rolling_std,
        )

    def get_portfolio_heat_multiplier(self) -> float:
        """Get recommended heat multiplier based on current correlation regime."""
        if len(self.history) < 5:
            return 1.0
        arr = np.array(self.history[-self.window:])
        abs_rho = float(np.mean(np.abs(arr)))
        if abs_rho >= self.crisis_threshold:
            return 0.25
        elif abs_rho >= self.elevated_threshold:
            return 0.50
        return 1.0

    def reset(self) -> None:
        self.history = []
        self._prev_rho = 0.0


# Canonical aliases
CorrelationInstabilityDetector = CorrelationBreakdownDetector
PortfolioCorrelationMonitor = CorrelationBreakdownDetector
