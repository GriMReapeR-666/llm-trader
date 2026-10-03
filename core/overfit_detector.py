"""Overfit Detection Framework.

Detects when a strategy's backtested performance is a result of curve-fitting
rather than genuine market edge. The core principle from Author-C:

  "A backtest with too few observations relative to the number of strategies
   tested will almost certainly be overfit."

Key detection methods:
  1. IS vs OOS divergence — if in-sample massively outperforms out-of-sample
  2. Deflated Sharpe Ratio — adjusts for multiple testing and selection bias
  3. Minimum backtest length — BaR (Backtest overfitting Ratio)
  4. Stepwise fitting detection — detects iterative feature/prompt additions
  5. Cross-validation stability — variance across CV folds
  6. Strategy overlap — if many tested strategies are correlated
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.metrics import calculate_deflated_sharpe_ratio

# Optional trial ledger import (S.Ξ from UAQP Patch 2)
try:
    from core.trial_ledger import get_trial_ledger, DSR_GATE_THRESHOLD
except ImportError:
    get_trial_ledger = None
    DSR_GATE_THRESHOLD = 0.95


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OverfitResult:
    """Complete overfit analysis result."""
    is_overfit: bool
    risk_level: str              # "LOW", "MODERATE", "HIGH", "CRITICAL"
    confidence: float            # 0-1, confidence in the overfit assessment
    factors: Dict[str, Any]      # Individual detection factors
    diagnosis: str               # Human-readable explanation
    recommendations: List[str]   # What to do about it
    grade: str                   # A/B/C/D/F


@dataclass
class DivergenceResult:
    """In-sample vs out-of-sample divergence metrics."""
    is_sharpe: float
    oos_sharpe: float
    divergence: float            # is_sharpe - oos_sharpe
    divergence_ratio: float      # (is - oos) / is, 0 = perfect generalization
    degradation_pct: float       # % degradation from IS to OOS
    is_significant: bool         # Is the divergence statistically significant
    p_value: float


@dataclass
class MinBacktestResult:
    """Minimum backtest length analysis."""
    n_observations: int
    n_strategies_tested: int
    min_required: int            # Minimum observations for statistical significance
    current_ratio: float         # n_obs / min_required
    is_sufficient: bool
    deflated_sharpe_p_value: float
    max_expected_sharpe: float   # Max Sharpe achievable by luck alone


@dataclass
class StepwiseFitResult:
    """Detection of iterative curve-fitting."""
    n_iterations: int
    sharpe_trajectory: List[float]
    is_stepwise_fitting: bool    # True if each iteration improves Sharpe suspiciously
    fit_velocity: float          # Rate of Sharpe improvement per iteration
    diminishing_returns: bool    # True if later iterations add little


# ---------------------------------------------------------------------------
# Deflated Sharpe with multiple testing correction
# ---------------------------------------------------------------------------

def deflated_sharpe_test(
    estimated_sharpe: float,
    n_trials: int,
    sample_size: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    benchmark_sharpe: float = 0.0,
) -> Dict[str, Any]:
    """Deflated Sharpe Ratio with multiple testing correction.

    Tests whether the observed Sharpe is genuine or the result of
    testing many strategies and picking the best.

    Args:
        estimated_sharpe: The backtested Sharpe ratio
        n_trials: Number of different strategies/parameters tested
        sample_size: Number of observations in the sample
        skewness: Return skewness
        kurtosis: Return excess kurtosis + 3
        benchmark_sharpe: Benchmark Sharpe (0 = random walk)

    Returns:
        Dict with p_value, is_significant, max_expected_sharpe, rating
    """
    p_value = calculate_deflated_sharpe_ratio(
        estimated_sharpe=estimated_sharpe,
        num_trials=n_trials,
        sample_size=sample_size,
        skewness=skewness,
        kurtosis=kurtosis,
        benchmark_sharpe=benchmark_sharpe,
    )

    # Maximum expected Sharpe under null (from Benford's law / extreme value theory)
    if n_trials > 1:
        max_expected = (
            math.sqrt(2.0 * math.log(n_trials))
            - (math.log(math.pi) + math.log(math.log(n_trials)))
            / (2.0 * math.sqrt(2.0 * math.log(n_trials)))
        )
    else:
        max_expected = 0.0

    is_significant = p_value < 0.05

    if p_value >= 0.5:
        rating = "LIKELY_OVERFIT"
    elif p_value >= 0.1:
        rating = "SUSPICIOUS"
    elif p_value >= 0.05:
        rating = "MARGINAL"
    else:
        rating = "SIGNIFICANT"

    return {
        "p_value": p_value,
        "is_significant": is_significant,
        "max_expected_sharpe": max_expected,
        "estimated_sharpe": estimated_sharpe,
        "n_trials": n_trials,
        "sample_size": sample_size,
        "rating": rating,
    }


# ---------------------------------------------------------------------------
# Minimum backtest length
# ---------------------------------------------------------------------------

def minimum_backtest_length(
    n_observations: int,
    n_strategies_tested: int,
    target_sharpe: float = 1.0,
) -> MinBacktestResult:
    """Compute minimum required backtest length for statistical significance.

    Based on Bailey & Author-C (2014):
    T_min = T^2 * (1 + (1 - gamma_4/4) * TSR^2) / (TSR - E[TSR_max])^2

    where TSR = true Sharpe, E[TSR_max] = expected max Sharpe under null.

    Args:
        n_observations: Current number of observations
        n_strategies_tested: How many strategies were tested
        target_sharpe: Target Sharpe to validate

    Returns:
        MinBacktestResult
    """
    # Expected max Sharpe under null of no edge
    if n_strategies_tested > 1:
        max_expected = (
            math.sqrt(2.0 * math.log(n_strategies_tested))
            - (math.log(math.pi) + math.log(math.log(max(n_strategies_tested, 2))))
            / (2.0 * math.sqrt(2.0 * math.log(max(n_strategies_tested, 2))))
        )
    else:
        max_expected = 0.0

    # Simplified minimum length formula
    # T_min ≈ (2 * target_sharpe)^2 when no multiple testing correction
    # With multiple testing: T_min grows with log(n_strategies)
    #
    # ``target_sharpe`` is a per-observation Sharpe (mean/std of per-bar
    # returns), so the null's expected max must be expressed in the SAME
    # units: m_k × SE with SE = 1/√n for a per-observation SR estimator.
    # Comparing tsr against the raw m_k quantile (SR units, ~0.8-3.2)
    # made every per-bar strategy look impossible and pinned the old
    # delta<0.01 → n_obs*10 fallback at exactly 10x regardless of depth.
    tsr = max(target_sharpe, 0.01)
    se_null = 1.0 / math.sqrt(max(n_observations, 2))
    null_max = max_expected * se_null  # 0.0 when n_strategies_tested <= 1
    delta = tsr - null_max

    if delta <= 0:
        # The null's expected max meets the target at THIS sample size.
        # Report the length at which the null falls below the target:
        # T* = (m_k / tsr)^2 — an honest requirement, not a meaningless 10x.
        if tsr > 0 and max_expected > 0:
            min_required = max(40, int(math.ceil((max_expected / tsr) ** 2)))
        else:
            min_required = n_observations * 10  # Effectively impossible
    else:
        min_required = max(40, int(math.ceil((2.0 * tsr / delta) ** 2)))

    ratio = n_observations / min_required if min_required > 0 else 0.0

    # Deflated Sharpe p-value
    dsr = deflated_sharpe_test(
        estimated_sharpe=target_sharpe,
        n_trials=n_strategies_tested,
        sample_size=n_observations,
    )

    return MinBacktestResult(
        n_observations=n_observations,
        n_strategies_tested=n_strategies_tested,
        min_required=min_required,
        current_ratio=ratio,
        is_sufficient=ratio >= 1.0,
        deflated_sharpe_p_value=dsr["p_value"],
        max_expected_sharpe=max_expected,
    )


# ---------------------------------------------------------------------------
# IS vs OOS divergence
# ---------------------------------------------------------------------------

def measure_is_oos_divergence(
    is_sharpe: float,
    oos_sharpe: float,
    is_n: int,
    oos_n: int,
) -> DivergenceResult:
    """Measure divergence between in-sample and out-of-sample Sharpe.

    Args:
        is_sharpe: In-sample Sharpe ratio
        oos_sharpe: Out-of-sample Sharpe ratio
        is_n: Number of in-sample observations
        oos_n: Number of out-of-sample observations

    Returns:
        DivergenceResult
    """
    divergence = is_sharpe - oos_sharpe
    divergence_ratio = abs(divergence) / max(abs(is_sharpe), 0.01)
    degradation_pct = (1.0 - oos_sharpe / max(is_sharpe, 0.01)) * 100.0

    # Z-test for difference in Sharpe ratios
    # Approximate SE of Sharpe ≈ sqrt(1/T * (1 - gamma3*SR + (gamma4-1)/4 * SR^2))
    se_is = 1.0 / math.sqrt(max(is_n, 10))
    se_oos = 1.0 / math.sqrt(max(oos_n, 10))
    se_diff = math.sqrt(se_is ** 2 + se_oos ** 2)

    if se_diff < 1e-10:
        z = 0.0
    else:
        z = abs(divergence) / se_diff

    # Two-sided p-value
    p_value = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))
    is_significant = p_value < 0.05

    return DivergenceResult(
        is_sharpe=is_sharpe,
        oos_sharpe=oos_sharpe,
        divergence=divergence,
        divergence_ratio=divergence_ratio,
        degradation_pct=degradation_pct,
        is_significant=is_significant,
        p_value=p_value,
    )


# ---------------------------------------------------------------------------
# Stepwise fitting detection
# ---------------------------------------------------------------------------

def detect_stepwise_fitting(
    sharpe_trajectory: List[float],
    min_iterations: int = 3,
) -> StepwiseFitResult:
    """Detect iterative curve-fitting from Sharpe improvement trajectory.

    If each iteration monotonically improves Sharpe without diminishing returns,
    the strategy is likely being curve-fitted.

    Args:
        sharpe_trajectory: Sharpe after each iteration (e.g., after adding features)
        min_iterations: Minimum iterations needed for analysis

    Returns:
        StepwiseFitResult
    """
    n = len(sharpe_trajectory)

    if n < min_iterations:
        return StepwiseFitResult(
            n_iterations=n,
            sharpe_trajectory=sharpe_trajectory,
            is_stepwise_fitting=False,
            fit_velocity=0.0,
            diminishing_returns=False,
        )

    arr = np.array(sharpe_trajectory)

    # Check for monotonic improvement
    diffs = np.diff(arr)
    n_improvements = int(np.sum(diffs > 0))
    monotonicity = n_improvements / len(diffs) if len(diffs) > 0 else 0.0

    # Fit velocity (Sharpe gain per iteration)
    fit_velocity = float((arr[-1] - arr[0]) / (n - 1))

    # Diminishing returns: last third improvement < first third
    third = max(1, n // 3)
    early_gain = float(arr[third] - arr[0]) / third if third > 0 else 0.0
    late_gain = float(arr[-1] - arr[-third]) / third if third > 0 else 0.0

    diminishing_returns = late_gain < 0.3 * early_gain if early_gain > 0 else False

    # Stepwise fitting: >80% monotonic AND no diminishing returns AND high velocity
    is_stepwise = (
        monotonicity > 0.8
        and not diminishing_returns
        and fit_velocity > 0.1  # Sharpe improves > 0.1 per iteration
    )

    return StepwiseFitResult(
        n_iterations=n,
        sharpe_trajectory=sharpe_trajectory,
        is_stepwise_fitting=is_stepwise,
        fit_velocity=fit_velocity,
        diminishing_returns=diminishing_returns,
    )


# ---------------------------------------------------------------------------
# Cross-validation stability
# ---------------------------------------------------------------------------

def cv_stability_test(
    fold_sharpes: List[float],
    threshold_cv: float = 0.5,
) -> Dict[str, Any]:
    """Test stability of performance across cross-validation folds.

    High variance across folds suggests the strategy is not robust.

    Args:
        fold_sharpes: Sharpe from each CV fold
        threshold_cv: Max acceptable coefficient of variation

    Returns:
        Dict with stability metrics
    """
    arr = np.array(fold_sharpes)
    n = len(arr)

    if n < 2:
        return {"stable": True, "cv": 0.0, "mean": 0.0, "std": 0.0, "n_folds": n}

    mean = float(np.mean(arr))
    std = float(np.std(arr))
    cv = std / max(abs(mean), 0.01)

    # Fraction of folds with positive Sharpe
    positive_fraction = float(np.mean(arr > 0))

    stable = cv < threshold_cv and positive_fraction > 0.6

    return {
        "stable": stable,
        "cv": cv,
        "mean": mean,
        "std": std,
        "positive_fraction": positive_fraction,
        "n_folds": n,
    }


# ---------------------------------------------------------------------------
# Strategy overlap detection
# ---------------------------------------------------------------------------

def detect_strategy_overlap(
    returns_matrix: np.ndarray,
    threshold: float = 0.7,
) -> Dict[str, Any]:
    """Detect if tested strategies are highly correlated (not independent trials).

    If 100 strategies are tested but they're all variants of the same idea,
    the effective number of trials is much less than 100.

    Args:
        returns_matrix: (n_strategies, n_observations) matrix of strategy returns
        threshold: Correlation threshold for "overlapping" strategies

    Returns:
        Dict with overlap metrics
    """
    n_strategies, n_obs = returns_matrix.shape

    if n_strategies < 2 or n_obs < 10:
        return {
            "effective_trials": n_strategies,
            "overlap_fraction": 0.0,
            "avg_correlation": 0.0,
        }

    # Compute pairwise correlations
    corr_matrix = np.corrcoef(returns_matrix)
    # Extract upper triangle (excluding diagonal)
    upper_idx = np.triu_indices(n_strategies, k=1)
    correlations = corr_matrix[upper_idx]

    avg_corr = float(np.mean(correlations))
    high_corr_fraction = float(np.mean(np.abs(correlations) > threshold))

    # Effective number of independent trials
    # Conservative: effective = n * (1 - avg_corr) + 1
    effective_trials = max(1, int(round(n_strategies * (1.0 - avg_corr) + 1)))

    return {
        "effective_trials": effective_trials,
        "overlap_fraction": high_corr_fraction,
        "avg_correlation": avg_corr,
        "n_strategies": n_strategies,
    }


# ---------------------------------------------------------------------------
# Full overfit detection
# ---------------------------------------------------------------------------

def detect_overfitting(
    returns: Sequence[float],
    is_returns: Optional[Sequence[float]] = None,
    oos_returns: Optional[Sequence[float]] = None,
    n_strategies_tested: int = 1,
    fold_sharpes: Optional[List[float]] = None,
    sharpe_trajectory: Optional[List[float]] = None,
    returns_matrix: Optional[np.ndarray] = None,
    strategy_class: Optional[str] = None,
    record_trial: bool = True,
) -> OverfitResult:
    """Run comprehensive overfit detection.

    Args:
        returns: Full sample returns
        is_returns: In-sample returns (if available)
        oos_returns: Out-of-sample returns (if available)
        n_strategies_tested: Number of strategies/parameters tested
        fold_sharpes: Sharpe from each CV fold (if available)
        sharpe_trajectory: Sharpe after each iteration (if available)
        returns_matrix: (n_strategies, n_obs) matrix (if available)        Returns:
            OverfitResult with full diagnosis
        """
    factors = {}
    risk_scores = []  # 0-1, higher = more overfit risk

    # 1. Return diagnostics
    from core.return_diagnostics import diagnose_returns
    diag = diagnose_returns(returns)
    factors["return_diagnostics"] = {
        "is_white_noise": diag.is_white_noise,
        "has_volatility_clustering": diag.has_volatility_clustering,
        "has_linear_dependence": diag.has_linear_dependence,
    }
    # If returns are white noise, any backtested edge is likely curve-fitted
    if diag.is_white_noise:
        risk_scores.append(0.8)
    elif diag.has_volatility_clustering:
        risk_scores.append(0.2)
    else:
        risk_scores.append(0.3)

    # 2. Deflated Sharpe Ratio
    sharpe = _compute_sharpe(returns)
    n_obs = len([r for r in returns if math.isfinite(r)])
    dsr = deflated_sharpe_test(
        estimated_sharpe=sharpe,
        n_trials=n_strategies_tested,
        sample_size=n_obs,
    )
    factors["deflated_sharpe"] = dsr
    if dsr["rating"] == "LIKELY_OVERFIT":
        risk_scores.append(0.9)
    elif dsr["rating"] == "SUSPICIOUS":
        risk_scores.append(0.6)
    elif dsr["rating"] == "MARGINAL":
        risk_scores.append(0.4)
    else:
        risk_scores.append(0.1)

    # 3. Minimum backtest length
    min_bt = minimum_backtest_length(n_obs, n_strategies_tested, target_sharpe=sharpe)
    factors["min_backtest"] = {
        "n_observations": min_bt.n_observations,
        "min_required": min_bt.min_required,
        "current_ratio": min_bt.current_ratio,
        "is_sufficient": min_bt.is_sufficient,
    }
    if not min_bt.is_sufficient:
        risk_scores.append(0.7)
    else:
        risk_scores.append(0.2)

    # 4. IS vs OOS divergence
    if is_returns is not None and oos_returns is not None:
        is_sharpe = _compute_sharpe(is_returns)
        oos_sharpe = _compute_sharpe(oos_returns)
        div = measure_is_oos_divergence(
            is_sharpe, oos_sharpe,
            len(is_returns), len(oos_returns),
        )
        factors["divergence"] = {
            "is_sharpe": div.is_sharpe,
            "oos_sharpe": div.oos_sharpe,
            "degradation_pct": div.degradation_pct,
            "is_significant": div.is_significant,
            "p_value": div.p_value,
        }
        if div.degradation_pct > 50:
            risk_scores.append(0.8)
        elif div.degradation_pct > 25:
            risk_scores.append(0.5)
        else:
            risk_scores.append(0.2)

    # 5. Stepwise fitting
    if sharpe_trajectory is not None:
        step = detect_stepwise_fitting(sharpe_trajectory)
        factors["stepwise"] = {
            "is_stepwise_fitting": step.is_stepwise_fitting,
            "fit_velocity": step.fit_velocity,
            "diminishing_returns": step.diminishing_returns,
        }
        if step.is_stepwise_fitting:
            risk_scores.append(0.7)
        else:
            risk_scores.append(0.1)

    # 6. CV stability
    if fold_sharpes is not None and len(fold_sharpes) > 1:
        cv = cv_stability_test(fold_sharpes)
        factors["cv_stability"] = cv
        if not cv["stable"]:
            risk_scores.append(0.6)
        else:
            risk_scores.append(0.1)

    # 7. Strategy overlap
    if returns_matrix is not None and returns_matrix.shape[0] > 1:
        overlap = detect_strategy_overlap(returns_matrix)
        factors["overlap"] = overlap
        effective_ratio = overlap["effective_trials"] / max(overlap["n_strategies"], 1)
        if effective_ratio < 0.3:
            risk_scores.append(0.6)  # Most strategies are duplicates
        else:
            risk_scores.append(0.15)

    # Aggregate risk score
    if risk_scores:
        avg_risk = sum(risk_scores) / len(risk_scores)
    else:
        avg_risk = 0.5

    # Risk level
    if avg_risk >= 0.7:
        risk_level = "CRITICAL"
    elif avg_risk >= 0.5:
        risk_level = "HIGH"
    elif avg_risk >= 0.3:
        risk_level = "MODERATE"
    else:
        risk_level = "LOW"

    is_overfit = risk_level in ("HIGH", "CRITICAL")

    # Grade
    if avg_risk < 0.2:
        grade = "A"
    elif avg_risk < 0.35:
        grade = "B"
    elif avg_risk < 0.5:
        grade = "C"
    elif avg_risk < 0.7:
        grade = "D"
    else:
        grade = "F"

    # Recommendations
    recommendations = []
    if not min_bt.is_sufficient:
        recommendations.append(
            f"Need at least {min_bt.min_required} observations "
            f"(have {n_obs}). Collect more data or reduce parameters."
        )
    if dsr["rating"] in ("LIKELY_OVERFIT", "SUSPICIOUS"):
        recommendations.append(
            f"Tested {n_strategies_tested} strategies but only "
            f"{n_obs} observations. Deflated Sharpe p={dsr['p_value']:.3f}. "
            f"Reduce parameter search space."
        )
    if factors.get("divergence", {}).get("degradation_pct", 0) > 25:
        div_data = factors["divergence"]
        recommendations.append(
            f"IS→OOS degradation: {div_data['degradation_pct']:.0f}%. "
            f"IS Sharpe={div_data['is_sharpe']:.2f}, OOS={div_data['oos_sharpe']:.2f}. "
            f"Strategy may not generalize."
        )
    if diag.is_white_noise:
        recommendations.append(
            "Returns appear to be white noise. "
            "Any backtested edge is likely noise."
        )
    if factors.get("stepwise", {}).get("is_stepwise_fitting"):
        recommendations.append(
            "Detected stepwise fitting: each iteration improves Sharpe "
            "without diminishing returns. Stop adding features/prompts."
        )
    if factors.get("cv_stability", {}).get("stable") is False:
        recommendations.append(
            "High variance across CV folds. "
            "Strategy is unstable across time periods."
        )

    if not recommendations:
        recommendations.append("No overfitting concerns detected.")

    # Diagnosis
    diagnosis_parts = [f"Overall risk: {risk_level} (score={avg_risk:.2f})"]
    diagnosis_parts.append(f"Grade: {grade}")
    diagnosis_parts.append(f"Sharpe: {sharpe:.2f} on {n_obs} obs with {n_strategies_tested} strategies tested")
    if factors.get("deflated_sharpe"):
        diagnosis_parts.append(f"Deflated Sharpe: {dsr['rating']} (p={dsr['p_value']:.3f})")
    if not min_bt.is_sufficient:
        diagnosis_parts.append(
            f"Insufficient backtest length: have {n_obs}, need {min_bt.min_required}"
        )

    # --- S.Ξ Trial Ledger Hard Gate (UAQP Patch 2) ---
    # If trial ledger is available, compute DSR using TRUE trial count.
    # This overrides the local n_strategies_tested count, preventing
    # any agent from gaming the multiple-testing correction.
    ledger_result = None
    if get_trial_ledger is not None:
        try:
            ledger = get_trial_ledger()
            ledger_result = ledger.evaluate_dsr(
                estimated_sharpe=sharpe,
                n_observations=n_obs,
                skewness=factors.get("deflated_sharpe", {}).get("skewness", 0.0),
                kurtosis=factors.get("deflated_sharpe", {}).get("kurtosis", 3.0),
                strategy_id="overfit_detector",
                strategy_class=strategy_class,
                # record_trial=False: the caller (OverfitGate) already
                # recorded the canonical trial with the real strategy id.
                # The detector re-gates against the updated count without
                # appending a second row for the same hypothesis.
                record=record_trial,
            )
            factors["trial_ledger_dsr"] = ledger_result.to_dict()

            if not ledger_result.is_significant:
                # Hard gate: FAIL_NOT_SIGNIFICANT overrides local assessment
                risk_level = "CRITICAL"
                is_overfit = True
                grade = "F"
                recommendations.insert(
                    0,
                    f"DSR HARD GATE REJECTED: DSR={ledger_result.dsr:.4f} < "
                    f"threshold={DSR_GATE_THRESHOLD}. True trial count="
                    f"{ledger_result.n_trials}. Strategy shows no significance "
                    f"after multiple-testing correction."
                )
                diagnosis_parts.append(
                    f"DSR HARD GATE: REJECTED (DSR={ledger_result.dsr:.4f}, "
                    f"true_n_trials={ledger_result.n_trials})"
                )
        except Exception:
            pass  # Ledger unavailable — fall through to local assessment

    return OverfitResult(
        is_overfit=is_overfit,
        risk_level=risk_level,
        confidence=avg_risk,
        factors=factors,
        diagnosis=" | ".join(diagnosis_parts),
        recommendations=recommendations,
        grade=grade,
    )


def _compute_sharpe(returns: Sequence[float], annualization: float = 1.0) -> float:
    """Compute annualized Sharpe ratio. Delegates to core.math_utils."""
    from core.math_utils import compute_sharpe
    return compute_sharpe(returns, annualization)
