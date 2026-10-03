"""Failure Mode Taxonomy for Alpha Mining.

Implements the FM-1 through FM-6 failure mode classification system
from BrainAlpha for diagnostic repair of alpha expressions.

References:
    Wangpratham, N. (2026). "BrainAlpha: An Autonomous Multi-Agent System
    for Quantitative Alpha Discovery." Working Paper.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class FailureMode(Enum):
    """Failure Mode Codes (FM-1 through FM-6)."""
    FM1_LOW_SIGNAL_QUALITY = "FM-1"
    FM2_EXCESSIVE_TURNOVER = "FM-2"
    FM3_HIGH_DRAWDOWN = "FM-3"
    FM4_SIGNAL_REVERSAL = "FM-4"
    FM5_LOW_COVERAGE = "FM-5"
    FM6_LOW_TURNOVER = "FM-6"


@dataclass
class FailureDiagnostic:
    """Diagnostic result for a failed alpha expression."""
    failure_mode: FailureMode
    severity: float           # 0-1, how badly it failed
    primary_check: str        # Name of failing check
    gap_pct: float            # (actual - threshold) / threshold
    recommendations: List[str]  # Ordered repair actions
    is_recoverable: bool      # Can this be repaired?
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure_mode": self.failure_mode.value,
            "severity": round(self.severity, 4),
            "primary_check": self.primary_check,
            "gap_pct": round(self.gap_pct, 4),
            "recommendations": self.recommendations,
            "is_recoverable": self.is_recoverable,
        }


# ---------------------------------------------------------------------------
# Failure Mode Classification
# ---------------------------------------------------------------------------

# Thresholds for failure classification
FM_THRESHOLDS = {
    "sharpe_min": 0.5,
    "turnover_max": 0.5,
    "turnover_min": 0.05,
    "max_dd_max": 0.15,
    "coverage_min": 0.10,
    "returns_min": 0.0,
}


def classify_failure(checks: Dict[str, Any], 
                     metrics: Dict[str, float]) -> FailureDiagnostic:
    """Classify the primary failure mode of an alpha expression.
    
    Args:
        checks: Dictionary of check results (pass/fail)
        metrics: Dictionary of metric values
        
    Returns:
        FailureDiagnostic with classification and recommendations
    """
    # Extract metrics
    sharpe = metrics.get("sharpe", 0.0)
    turnover = metrics.get("turnover", 0.0)
    max_dd = metrics.get("max_dd", 0.0)
    coverage = metrics.get("coverage", 0.0)
    returns = metrics.get("annualized_returns", 0.0)
    
    # Check for failures in priority order
    
    # FM-1: Low Signal Quality
    if sharpe < FM_THRESHOLDS["sharpe_min"]:
        gap_pct = (FM_THRESHOLDS["sharpe_min"] - sharpe) / FM_THRESHOLDS["sharpe_min"]
        return FailureDiagnostic(
            failure_mode=FailureMode.FM1_LOW_SIGNAL_QUALITY,
            severity=min(1.0, gap_pct),
            primary_check="LOW_SHARPE",
            gap_pct=gap_pct,
            recommendations=[
                "Wrap with group_neutralize(x, sector) to remove sector beta",
                "Apply group_neutralize(x, subindustry) for finer neutralization",
                "Apply zscore(x) cross-sectionally to reduce noise",
                "Clip outliers with winsorize(x, std=3)",
                "Increase all lookback parameters by 2x",
            ],
            is_recoverable=True,
        )
    
    # FM-2: Excessive Turnover
    if turnover > FM_THRESHOLDS["turnover_max"]:
        gap_pct = (turnover - FM_THRESHOLDS["turnover_max"]) / FM_THRESHOLDS["turnover_max"]
        return FailureDiagnostic(
            failure_mode=FailureMode.FM2_EXCESSIVE_TURNOVER,
            severity=min(1.0, gap_pct),
            primary_check="HIGH_TURNOVER",
            gap_pct=gap_pct,
            recommendations=[
                "Smooth via decay_linear(x, 5) to reduce turnover",
                "Normalize as ts_mean(x,d)/ts_std_dev(x,d)",
                "Add pasteurize(x) to improve coverage",
                "Restructure using rank() cross-sectionally",
            ],
            is_recoverable=True,
        )
    
    # FM-3: High Drawdown
    if max_dd > FM_THRESHOLDS["max_dd_max"]:
        gap_pct = (max_dd - FM_THRESHOLDS["max_dd_max"]) / FM_THRESHOLDS["max_dd_max"]
        return FailureDiagnostic(
            failure_mode=FailureMode.FM3_HIGH_DRAWDOWN,
            severity=min(1.0, gap_pct),
            primary_check="HIGH_DRAWDOWN",
            gap_pct=gap_pct,
            recommendations=[
                "Apply winsorize(x, std=3) to clip extreme values",
                "Add volatility conditioning with ts_std_dev normalization",
                "Reduce position concentration",
                "Add stop-loss logic to expression",
            ],
            is_recoverable=True,
        )
    
    # FM-4: Signal Reversal
    if returns < FM_THRESHOLDS["returns_min"] and not checks.get("returns_positive", True):
        gap_pct = abs(returns) / max(abs(FM_THRESHOLDS["returns_min"]), 1e-6)
        return FailureDiagnostic(
            failure_mode=FailureMode.FM4_SIGNAL_REVERSAL,
            severity=min(1.0, gap_pct),
            primary_check="NEGATIVE_RETURNS",
            gap_pct=gap_pct,
            recommendations=[
                "Negate the expression: -1 * (expression)",
                "Invert direction: use cs_rank反转信号",
                "Check for look-ahead bias in expression",
                "Verify data alignment and forward returns calculation",
            ],
            is_recoverable=True,
        )
    
    # FM-5: Low Coverage
    if coverage < FM_THRESHOLDS["coverage_min"]:
        gap_pct = (FM_THRESHOLDS["coverage_min"] - coverage) / FM_THRESHOLDS["coverage_min"]
        return FailureDiagnostic(
            failure_mode=FailureMode.FM5_LOW_COVERAGE,
            severity=min(1.0, gap_pct),
            primary_check="LOW_COVERAGE",
            gap_pct=gap_pct,
            recommendations=[
                "Apply pasteurize(x) to improve coverage",
                "Relax filtering conditions",
                "Use broader field selections",
                "Reduce lookback requirements",
            ],
            is_recoverable=True,
        )
    
    # FM-6: Low Turnover (too static)
    if turnover < FM_THRESHOLDS["turnover_min"]:
        gap_pct = (FM_THRESHOLDS["turnover_min"] - turnover) / FM_THRESHOLDS["turnover_min"]
        return FailureDiagnostic(
            failure_mode=FailureMode.FM6_LOW_TURNOVER,
            severity=min(1.0, gap_pct),
            primary_check="LOW_TURNOVER",
            gap_pct=gap_pct,
            recommendations=[
                "Reduce lookback parameters",
                "Add cross-sectional variation with cs_rank()",
                "Use ts_delta() for momentum component",
                "Apply ts_returns() for change detection",
            ],
            is_recoverable=True,
        )
    
    # Default: Unknown failure
    return FailureDiagnostic(
        failure_mode=FailureMode.FM1_LOW_SIGNAL_QUALITY,
        severity=0.5,
        primary_check="UNKNOWN",
        gap_pct=0.0,
        recommendations=["Review expression manually"],
        is_recoverable=False,
    )


# ---------------------------------------------------------------------------
# Repair Action Catalogue
# ---------------------------------------------------------------------------

REPAIR_CATALOGUE = {
    FailureMode.FM1_LOW_SIGNAL_QUALITY: {
        "LOW_SHARPE": [
            "group_neutralize(x, sector)",
            "group_neutralize(x, subindustry)",
            "zscore(x) cross-sectionally",
            "winsorize(x, std=3)",
            "lookback *= 2",
        ],
        "LOW_FITNESS": [
            "decay_linear(x, 5)",
            "ts_mean(x,d)/ts_std_dev(x,d)",
            "pasteurize(x)",
            "rank(x) cross-sectionally",
        ],
    },
    FailureMode.FM2_EXCESSIVE_TURNOVER: {
        "HIGH_TURNOVER": [
            "decay_linear(x, 5)",
            "ts_mean(x,d)/ts_std_dev(x,d)",
            "pasteurize(x)",
            "rank(x) cross-sectionally",
        ],
    },
    FailureMode.FM3_HIGH_DRAWDOWN: {
        "HIGH_DRAWDOWN": [
            "winsorize(x, std=3)",
            "ts_std_dev normalization",
            "reduce_concentration()",
            "add_stop_loss()",
        ],
    },
    FailureMode.FM4_SIGNAL_REVERSAL: {
        "NEGATIVE_RETURNS": [
            "-1 * (x)",
            "invert_direction(x)",
            "check_lookahead_bias()",
            "verify_data_alignment()",
        ],
    },
    FailureMode.FM5_LOW_COVERAGE: {
        "LOW_COVERAGE": [
            "pasteurize(x)",
            "relax_filters()",
            "broaden_fields()",
            "reduce_lookback()",
        ],
    },
    FailureMode.FM6_LOW_TURNOVER: {
        "LOW_TURNOVER": [
            "reduce_lookback()",
            "rank(x) cross-sectionally",
            "ts_delta(x, 1)",
            "ts_returns(x, 1)",
        ],
    },
}


def get_repair_actions(failure_mode: FailureMode, 
                       primary_check: str) -> List[str]:
    """Get ordered repair actions for a failure mode.
    
    Args:
        failure_mode: The failure mode classification
        primary_check: The primary failing check
        
    Returns:
        Ordered list of repair actions
    """
    mode_catalogue = REPAIR_CATALOGUE.get(failure_mode, {})
    return mode_catalogue.get(primary_check, ["manual_review"])


# ---------------------------------------------------------------------------
# Recoverability Assessment
# ---------------------------------------------------------------------------

def assess_recoverability(expression_complexity: int,
                         failure_count: int,
                         returns: float,
                         self_correlation: float,
                         gap_pct: float) -> Tuple[bool, str]:
    """Assess whether an expression is recoverable.
    
    Based on BrainAlpha's three hard-stop conditions:
    I1. SELF_CORRELATION=FAIL: structural duplication
    I2. Reversed complex signal: returns ≤ 0 and complexity > 20
    I3. Excessive failures: n_f ≥ 4
    
    Args:
        expression_complexity: Number of AST nodes
        failure_count: Number of failed repair attempts
        returns: Annualized returns
        self_correlation: Self-correlation with existing signals
        gap_pct: Maximum gap percentage
        
    Returns:
        Tuple of (is_recoverable, reason)
    """
    # I1: Self-correlation failure
    if self_correlation > 0.8:
        return False, "SELF_CORRELATION_FAIL"
    
    # I2: Reversed complex signal
    if returns <= 0 and expression_complexity > 20:
        return False, "REVERSED_COMPLEX_SIGNAL"
    
    # I3: Excessive failures
    if failure_count >= 4:
        return False, "EXCESSIVE_FAILURES"
    
    # Soft stop: large gap
    if gap_pct > 0.50:
        return False, "LARGE_GAP"
    
    return True, "RECOVERABLE"
