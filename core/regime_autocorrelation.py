"""Regime-Aware Autocorrelation Analysis.

Detects microstructure changes by comparing autocorrelation structure
across market regimes. When the autocorrelation pattern changes between
regimes (e.g., trending → ranging), it signals that the statistical
properties of returns have shifted — a regime transition.

Key detection:
  1. Segment returns by regime (BOCPD changepoints or HMM states)
  2. Compute per-regime Ljung-Box, ACF, PACF, runs test
  3. Compare ACF structures across regimes (KS test on ACF distributions)
  4. Detect microstructure breaks: autocorrelation sign flip, magnitude shift
  5. Alert when current regime's ACF diverges significantly from historical

Why this matters:
  - A momentum strategy works when returns have positive autocorrelation
  - A mean-reversion strategy works when returns have negative autocorrelation
  - If autocorrelation flips sign between regimes, the strategy edge vanishes
  - Detecting this early prevents entering trades with wrong regime assumption

References:
  - Cont (2001), "Empirical properties of asset returns"
  - Preis & Stanley (2010), "Switching phenomena in financial markets"
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.return_diagnostics import (
    ljung_box_test,
    compute_acf,
    compute_pacf,
    runs_test,
    durbin_watson,
    LjungBoxResult,
    ACFResult,
    PACFResult,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RegimeAutocorrelation:
    """Autocorrelation structure for a single regime."""
    regime_name: str
    n_bars: int
    ljung_box_returns: LjungBoxResult
    ljung_box_abs_returns: LjungBoxResult
    acf_returns: ACFResult
    pacf_returns: PACFResult
    durbin_watson: float
    runs_test_p: float
    mean_return: float
    std_return: float
    skewness: float
    kurtosis: float
    # Microstructure fingerprints
    has_momentum: bool          # Positive autocorrelation (trending)
    has_mean_reversion: bool    # Negative autocorrelation (reverting)
    has_volatility_clustering: bool  # |returns| autocorrelated (ARCH)
    acf_lag1: float             # First-order autocorrelation (key fingerprint)


@dataclass
class MicrostructureChange:
    """Detected change in autocorrelation structure between regimes."""
    from_regime: str
    to_regime: str
    acf_lag1_shift: float       # Change in first-order autocorrelation
    acf_lag1_sign_flip: bool    # Did autocorrelation change sign?
    ljung_box_p_change: float   # Change in Ljung-Box p-value
    ks_statistic: float         # KS test on ACF distributions
    ks_p_value: float           # KS test p-value
    severity: str               # "INFO", "WARNING", "CRITICAL"
    message: str
    recommendation: str         # What to do about it


@dataclass
class RegimeAutocorrelationResult:
    """Complete regime-aware autocorrelation analysis."""
    current_regime: RegimeAutocorrelation
    regime_history: Dict[str, RegimeAutocorrelation]  # regime_name -> analysis
    changes: List[MicrostructureChange]               # Detected changes
    current_vs_baseline: Optional[MicrostructureChange]  # Current vs historical avg
    acf_stability_score: float    # 0-1, how stable ACF is across regimes
    microstructure_risk: str      # "LOW", "MODERATE", "HIGH"
    regime_transition_detected: bool
    summary: str


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------

def _compute_returns_stats(returns: np.ndarray) -> Tuple[float, float, float, float]:
    """Compute basic return statistics."""
    mean = float(np.mean(returns))
    std = float(np.std(returns)) if len(returns) > 1 else 0.0
    if std > 1e-15 and len(returns) > 2:
        skew = float(np.mean(((returns - mean) / std) ** 3))
        kurt = float(np.mean(((returns - mean) / std) ** 4)) - 3.0  # excess kurtosis
    else:
        skew, kurt = 0.0, 0.0
    return mean, std, skew, kurt


def analyze_regime_autocorrelation(
    returns: np.ndarray,
    regime_name: str = "unknown",
    max_lag: int = 20,
) -> RegimeAutocorrelation:
    """Compute full autocorrelation structure for a single regime's returns.

    Args:
        returns: Return series for this regime
        regime_name: Name of the regime
        max_lag: Maximum lag for ACF/PACF

    Returns:
        RegimeAutocorrelation with complete diagnostics
    """
    returns = returns[np.isfinite(returns)]
    n = len(returns)

    if n < 20:
        # Not enough data for meaningful analysis
        return RegimeAutocorrelation(
            regime_name=regime_name, n_bars=n,
            ljung_box_returns=ljung_box_test(returns, max_lag=min(5, n//2)),
            ljung_box_abs_returns=ljung_box_test(np.abs(returns), max_lag=min(5, n//2)),
            acf_returns=ACFResult(lags=[], acf_values=[], confidence_band=0.0),
            pacf_returns=PACFResult(lags=[], pacf_values=[], confidence_band=0.0),
            durbin_watson=2.0, runs_test_p=1.0,
            mean_return=0.0, std_return=0.0, skewness=0.0, kurtosis=0.0,
            has_momentum=False, has_mean_reversion=False,
            has_volatility_clustering=False, acf_lag1=0.0,
        )

    # Ljung-Box tests
    lb_returns = ljung_box_test(returns, max_lag=max_lag)
    lb_abs = ljung_box_test(np.abs(returns), max_lag=max_lag)

    # ACF and PACF
    acf = compute_acf(returns, max_lag=max_lag)
    pacf = compute_pacf(returns, max_lag=max_lag)

    # Durbin-Watson
    dw = durbin_watson(returns)

    # Runs test
    runs_p = runs_test(returns)

    # Return statistics
    mean_r, std_r, skew, kurt = _compute_returns_stats(returns)

    # Microstructure fingerprints
    acf_lag1 = acf.acf_values[0] if acf.acf_values else 0.0

    # Momentum: positive autocorrelation (lag-1 ACF > 0 and significant)
    se = 1.0 / math.sqrt(n) if n > 0 else 1.0
    has_momentum = acf_lag1 > 2 * se  # > 2 standard errors

    # Mean reversion: negative autocorrelation
    has_mean_reversion = acf_lag1 < -2 * se

    # Volatility clustering: |returns| autocorrelated
    has_volatility_clustering = not lb_abs.is_random

    return RegimeAutocorrelation(
        regime_name=regime_name,
        n_bars=n,
        ljung_box_returns=lb_returns,
        ljung_box_abs_returns=lb_abs,
        acf_returns=acf,
        pacf_returns=pacf,
        durbin_watson=dw,
        runs_test_p=runs_p,
        mean_return=mean_r,
        std_return=std_r,
        skewness=skew,
        kurtosis=kurt,
        has_momentum=has_momentum,
        has_mean_reversion=has_mean_reversion,
        has_volatility_clustering=has_volatility_clustering,
        acf_lag1=acf_lag1,
    )


def detect_microstructure_changes(
    regime_analyses: Dict[str, RegimeAutocorrelation],
    baseline: Optional[RegimeAutocorrelation] = None,
) -> List[MicrostructureChange]:
    """Compare autocorrelation structures across regimes to detect changes.

    Args:
        regime_analyses: Dict mapping regime_name -> RegimeAutocorrelation
        baseline: Optional historical baseline for comparison

    Returns:
        List of detected microstructure changes
    """
    changes = []
    regime_names = list(regime_analyses.keys())

    # Pairwise comparison between regimes
    for i in range(len(regime_names)):
        for j in range(i + 1, len(regime_names)):
            r1 = regime_analyses[regime_names[i]]
            r2 = regime_analyses[regime_names[j]]
            change = _compare_two_regimes(r1, r2)
            if change:
                changes.append(change)

    # Compare each regime against baseline if provided
    if baseline is not None:
        for name, regime in regime_analyses.items():
            change = _compare_to_baseline(regime, baseline)
            if change:
                changes.append(change)

    return changes


def _compare_two_regimes(
    r1: RegimeAutocorrelation,
    r2: RegimeAutocorrelation,
) -> Optional[MicrostructureChange]:
    """Compare autocorrelation structure between two regimes."""
    # ACF lag-1 shift
    acf_shift = r2.acf_lag1 - r1.acf_lag1
    sign_flip = (r1.acf_lag1 > 0) != (r2.acf_lag1 > 0) and abs(acf_shift) > 0.05

    # KS test on ACF values (are the autocorrelation distributions different?)
    ks_stat, ks_p = _ks_test_acf(r1, r2)

    # Ljung-Box p-value change
    p_change = r2.ljung_box_returns.p_value - r1.ljung_box_returns.p_value

    # Determine severity
    if sign_flip or ks_stat > 0.5:
        severity = "CRITICAL"
        msg = (f"Microstructure BREAK between {r1.regime_name} and {r2.regime_name}: "
               f"ACF lag-1 flipped from {r1.acf_lag1:+.4f} to {r2.acf_lag1:+.4f}")
        rec = ("Switch strategy type: momentum→reversion or vice versa. "
               "Reduce position size until new regime is confirmed.")
    elif abs(acf_shift) > 0.15 or ks_stat > 0.3:
        severity = "WARNING"
        msg = (f"Microstructure SHIFT between {r1.regime_name} and {r2.regime_name}: "
               f"ACF lag-1 changed by {acf_shift:+.4f}")
        rec = "Review strategy parameters for current regime. Consider adaptive weighting."
    elif abs(acf_shift) > 0.05:
        severity = "INFO"
        msg = (f"Minor ACF difference between {r1.regime_name} and {r2.regime_name}: "
               f"lag-1 shift {acf_shift:+.4f}")
        rec = "Monitor. No immediate action needed."
    else:
        return None

    return MicrostructureChange(
        from_regime=r1.regime_name,
        to_regime=r2.regime_name,
        acf_lag1_shift=acf_shift,
        acf_lag1_sign_flip=sign_flip,
        ljung_box_p_change=p_change,
        ks_statistic=ks_stat,
        ks_p_value=ks_p,
        severity=severity,
        message=msg,
        recommendation=rec,
    )


def _compare_to_baseline(
    regime: RegimeAutocorrelation,
    baseline: RegimeAutocorrelation,
) -> Optional[MicrostructureChange]:
    """Compare a regime against historical baseline."""
    acf_shift = regime.acf_lag1 - baseline.acf_lag1
    sign_flip = (baseline.acf_lag1 > 0) != (regime.acf_lag1 > 0) and abs(acf_shift) > 0.05
    ks_stat, ks_p = _ks_test_acf(baseline, regime)
    p_change = regime.ljung_box_returns.p_value - baseline.ljung_box_returns.p_value

    if abs(acf_shift) < 0.03:
        return None

    if sign_flip or ks_stat > 0.5:
        severity = "CRITICAL"
        msg = (f"Current regime '{regime.regime_name}' has BREAK from baseline: "
               f"ACF lag-1: {baseline.acf_lag1:+.4f} → {regime.acf_lag1:+.4f}")
        rec = "Current regime microstructure differs fundamentally from historical average. Review all strategy assumptions."
    elif abs(acf_shift) > 0.1:
        severity = "WARNING"
        msg = (f"Current regime '{regime.regime_name}' deviates from baseline: "
               f"ACF lag-1 shift {acf_shift:+.4f}")
        rec = "Strategy may need parameter adjustment for this regime's autocorrelation structure."
    else:
        severity = "INFO"
        msg = f"Current regime '{regime.regime_name}' minor deviation from baseline: {acf_shift:+.4f}"
        rec = "Monitor."

    return MicrostructureChange(
        from_regime="baseline",
        to_regime=regime.regime_name,
        acf_lag1_shift=acf_shift,
        acf_lag1_sign_flip=sign_flip,
        ljung_box_p_change=p_change,
        ks_statistic=ks_stat,
        ks_p_value=ks_p,
        severity=severity,
        message=msg,
        recommendation=rec,
    )


def _ks_test_acf(
    r1: RegimeAutocorrelation,
    r2: RegimeAutocorrelation,
) -> Tuple[float, float]:
    """Two-sample KS test on ACF values between two regimes.

    Tests whether the autocorrelation distributions are drawn from
    the same underlying process.
    """
    acf1 = np.array(r1.acf_returns.acf_values) if r1.acf_returns.acf_values else np.array([0.0])
    acf2 = np.array(r2.acf_returns.acf_values) if r2.acf_returns.acf_values else np.array([0.0])

    if len(acf1) < 3 or len(acf2) < 3:
        return 0.0, 1.0

    # KS statistic: max absolute difference in empirical CDFs
    all_vals = np.sort(np.concatenate([acf1, acf2]))
    n1, n2 = len(acf1), len(acf2)

    cdf1 = np.searchsorted(np.sort(acf1), all_vals, side='right') / n1
    cdf2 = np.searchsorted(np.sort(acf2), all_vals, side='right') / n2

    ks_stat = float(np.max(np.abs(cdf1 - cdf2)))

    # Approximate p-value using KS distribution
    effective_n = math.sqrt(n1 * n2 / (n1 + n2))
    lam = (effective_n + 0.12 + 0.11 / effective_n) * ks_stat
    # Kolmogorov distribution: P(K <= lam) = 1 - 2*sum((-1)^(k-1)*exp(-2*k^2*lam^2))
    p_value = 1.0
    if lam > 0:
        p_approx = 0.0
        for k in range(1, 20):
            term = 2.0 * ((-1) ** (k - 1)) * math.exp(-2.0 * k * k * lam * lam)
            p_approx += term
        p_value = max(0.0, min(1.0, p_approx))

    return ks_stat, p_value


def compute_acf_stability_score(
    regime_analyses: Dict[str, RegimeAutocorrelation],
) -> float:
    """Compute how stable the ACF structure is across regimes.

    Returns 0 (completely different across regimes) to 1 (identical).
    """
    if len(regime_analyses) < 2:
        return 1.0

    acf_lag1s = [r.acf_lag1 for r in regime_analyses.values()]
    arr = np.array(acf_lag1s)

    # Stability = 1 - normalized range of ACF lag-1 values
    acf_range = float(np.max(arr) - np.min(arr))
    # Typical ACF range is [-0.5, 0.5], so normalize by 1.0
    stability = max(0.0, 1.0 - acf_range)

    return stability


def classify_microstructure_risk(
    result: RegimeAutocorrelationResult,
) -> str:
    """Classify overall microstructure risk level."""
    score = 0

    # Check for sign flips
    for change in result.changes:
        if change.acf_lag1_sign_flip:
            score += 3
        elif change.severity == "CRITICAL":
            score += 2
        elif change.severity == "WARNING":
            score += 1

    # Low ACF stability
    if result.acf_stability_score < 0.5:
        score += 2
    elif result.acf_stability_score < 0.7:
        score += 1

    # Current regime has weak autocorrelation (unreliable signal)
    if abs(result.current_regime.acf_lag1) < 0.02:
        score += 1

    if score >= 4:
        return "HIGH"
    elif score >= 2:
        return "MODERATE"
    else:
        return "LOW"


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------

def analyze_regime_autocorrelation_full(
    returns: np.ndarray,
    regime_labels: np.ndarray,
    current_regime_label: str,
    max_lag: int = 20,
) -> RegimeAutocorrelationResult:
    """Run full regime-aware autocorrelation analysis.

    Args:
        returns: Full return series
        regime_labels: Array of regime labels (same length as returns)
        current_regime_label: Which regime is currently active
        max_lag: Maximum lag for ACF/PACF

    Returns:
        RegimeAutocorrelationResult with complete analysis
    """
    returns = np.asarray(returns, dtype=float)
    regime_labels = np.asarray(regime_labels)

    # Compute baseline (full sample)
    baseline = analyze_regime_autocorrelation(returns, "baseline", max_lag)

    # Per-regime analysis
    regime_analyses: Dict[str, RegimeAutocorrelation] = {}
    unique_regimes = np.unique(regime_labels[regime_labels != ""])

    for regime in unique_regimes:
        mask = regime_labels == regime
        regime_returns = returns[mask]
        if len(regime_returns) >= 20:
            regime_analyses[str(regime)] = analyze_regime_autocorrelation(
                regime_returns, str(regime), max_lag
            )

    # Current regime
    current_mask = regime_labels == current_regime_label
    current_returns = returns[current_mask]
    current_analysis = analyze_regime_autocorrelation(
        current_returns, current_regime_label, max_lag
    )

    # Detect changes
    changes = detect_microstructure_changes(regime_analyses, baseline)

    # Current vs baseline
    current_vs_baseline = _compare_to_baseline(current_analysis, baseline)

    # Stability score
    stability = compute_acf_stability_score(regime_analyses)

    # Build result
    result = RegimeAutocorrelationResult(
        current_regime=current_analysis,
        regime_history=regime_analyses,
        changes=changes,
        current_vs_baseline=current_vs_baseline,
        acf_stability_score=stability,
        microstructure_risk="LOW",
        regime_transition_detected=False,
        summary="",
    )

    # Classify risk
    result.microstructure_risk = classify_microstructure_risk(result)

    # Detect transitions
    result.regime_transition_detected = (
        current_vs_baseline is not None
        and current_vs_baseline.severity in ("WARNING", "CRITICAL")
    )

    # Summary
    n_regimes = len(regime_analyses)
    n_breaks = sum(1 for c in changes if c.severity == "CRITICAL")
    n_shifts = sum(1 for c in changes if c.severity == "WARNING")
    result.summary = (
        f"Regime autocorrelation: {n_regimes} regimes, "
        f"{n_breaks} breaks, {n_shifts} shifts, "
        f"stability={stability:.2f}, risk={result.microstructure_risk}, "
        f"current_acf_lag1={current_analysis.acf_lag1:+.4f}"
    )

    return result


def format_regime_autocorrelation_report(result: RegimeAutocorrelationResult) -> str:
    """Format a human-readable report."""
    lines = [
        "=" * 70,
        "REGIME-AWARE AUTOCORRELATION ANALYSIS",
        "=" * 70,
        f"Current regime: {result.current_regime.regime_name} "
        f"(n={result.current_regime.n_bars})",
        f"ACF lag-1: {result.current_regime.acf_lag1:+.4f}",
        f"Microstructure risk: {result.microstructure_risk}",
        f"ACF stability: {result.acf_stability_score:.2f}",
        "",
        "--- Per-Regime Summary ---",
    ]

    for name, regime in sorted(result.regime_history.items()):
        signals = []
        if regime.has_momentum:
            signals.append("MOMENTUM")
        if regime.has_mean_reversion:
            signals.append("MEAN_REVERSION")
        if regime.has_volatility_clustering:
            signals.append("VOL_CLUSTER")
        if not signals:
            signals.append("WHITE_NOISE")

        lines.append(
            f"  {name:<20} n={regime.n_bars:>5}  "
            f"ACF1={regime.acf_lag1:+.4f}  "
            f"DW={regime.durbin_watson:.2f}  "
            f"LB_p={regime.ljung_box_returns.p_value:.4f}  "
            f"[{', '.join(signals)}]"
        )

    if result.changes:
        lines.append("")
        lines.append("--- Detected Changes ---")
        for change in result.changes:
            lines.append(f"  [{change.severity}] {change.message}")
            lines.append(f"    → {change.recommendation}")

    if result.current_vs_baseline:
        lines.append("")
        lines.append("--- Current vs Baseline ---")
        lines.append(f"  [{result.current_vs_baseline.severity}] {result.current_vs_baseline.message}")

    lines.append("=" * 70)
    return "\n".join(lines)
