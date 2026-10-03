"""Return Diagnostics — Autocorrelation & Serial Dependence Testing.

Tests whether return series exhibit statistically significant autocorrelation
in raw returns (trend/momentum) and absolute returns (volatility clustering).

Key tests:
  1. Ljung-Box Q-test for returns — detects linear serial dependence (momentum/mean-reversion)
  2. Ljung-Box Q-test for |returns| — detects volatility clustering (ARCH effects)
  3. ACF/PACF computation — identifies lag structure
  4. Durbin-Watson statistic — first-order autocorrelation
  5. Runs test — checks for randomness

These diagnostics are critical for:
  - Verifying that alpha signals capture genuine serial dependence, not noise
  - Detecting regime-dependent autocorrelation (changes in market microstructure)
  - Validating that model residuals are white noise (model is well-specified)
  - Overoptimization detection (excessive autocorrelation in backtested returns
    may indicate data snooping or unrealistic fill assumptions)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LjungBoxResult:
    """Result of a Ljung-Box Q-test."""
    statistic: float          # Q-statistic
    p_value: float            # p-value (asymptotic chi-squared)
    lags_tested: int          # Number of lags tested
    significant_lags: List[int]  # Lags with p < 0.05 (individual)
    is_random: bool           # True if series is white noise at alpha=0.05
    alpha: float              # Significance level used


@dataclass
class ACFResult:
    """Autocorrelation function results."""
    lags: List[int]
    acf_values: List[float]
    confidence_band: float    # 95% CI = ±1.96/sqrt(n)


@dataclass
class PACFResult:
    """Partial autocorrelation function results."""
    lags: List[int]
    pacf_values: List[float]
    confidence_band: float


@dataclass
class ReturnDiagnostics:
    """Complete diagnostics for a return series."""
    n_observations: int
    returns: LjungBoxResult
    abs_returns: LjungBoxResult
    acf_returns: ACFResult
    acf_abs_returns: ACFResult
    pacf_returns: PACFResult
    durbin_watson: float
    runs_test_p_value: float
    has_volatility_clustering: bool   # |returns| autocorrelated
    has_linear_dependence: bool       # returns autocorrelated
    is_white_noise: bool              # Neither type of dependence
    summary: str


# ---------------------------------------------------------------------------
# Core statistical functions (no scipy dependency)
# ---------------------------------------------------------------------------

def _chi_squared_cdf(x: float, df: int) -> float:
    """Approximate chi-squared CDF using regularized incomplete gamma function.

    Uses the series expansion for the lower incomplete gamma function.
    Accuracy is sufficient for p-value computation (3-4 decimal places).
    """
    if x <= 0:
        return 0.0
    if df <= 0:
        return 1.0

    # Use normal approximation for large df
    if df > 100:
        z = (x / df) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * df))
        denom = math.sqrt(2.0 / (9.0 * df))
        return _normal_cdf(z / denom)

    # Series expansion for incomplete gamma
    a = df / 2.0
    xh = x / 2.0
    if xh < a + 1.0:
        # Series expansion
        term = 1.0 / a
        total = term
        for n in range(1, 300):
            term *= xh / (a + n)
            total += term
            if abs(term) < 1e-12:
                break
        result = total * math.exp(-xh + a * math.log(xh) - _log_gamma(a))
    else:
        # Continued fraction (for large x)
        f = 1e-30
        c = f
        d = 0.0
        for n in range(1, 300):
            an = -(a + n - 1) if n % 2 == 1 else n // 2
            if n % 2 == 1:
                an = -(a + (n - 1) / 2.0)
                an_val = a + (n - 1) / 2.0
            else:
                an_val = n / 2.0
                an = -an_val

            d = 1.0 + an * d
            if abs(d) < 1e-30:
                d = 1e-30
            d = 1.0 / d
            c = 1.0 + an / c
            if abs(c) < 1e-30:
                c = 1e-30
            delta = c * d
            f *= delta
            if abs(delta - 1.0) < 1e-12:
                break
        result = 1.0 - f * math.exp(-xh + a * math.log(xh) - _log_gamma(a))

    return max(0.0, min(1.0, result))


def _log_gamma(x: float) -> float:
    """Lanczos approximation for log(gamma(x))."""
    g = 7.0
    c = [
        0.99999999999980993,
        676.5203681218851,
        -1259.1392167224028,
        771.32342877765313,
        -176.61502916214059,
        12.507343278686905,
        -0.13857109526572012,
        9.9843695780195716e-6,
        1.5056327351493116e-7,
    ]
    if x < 0.5:
        return math.log(math.pi / math.sin(math.pi * x)) - _log_gamma(1.0 - x)
    x -= 1.0
    a = c[0]
    t = x + g + 0.5
    for i in range(1, len(c)):
        a += c[i] / (x + i)
    return 0.5 * math.log(2.0 * math.pi) + (x + 0.5) * math.log(t) - t + math.log(a)


def _normal_cdf(x: float) -> float:
    """Standard normal CDF using error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# Ljung-Box Q-test
# ---------------------------------------------------------------------------

def ljung_box_test(
    series: Sequence[float],
    max_lag: int = 20,
    alpha: float = 0.05,
) -> LjungBoxResult:
    """Ljung-Box Q-test for autocorrelation.

    H0: The series is independently distributed (no autocorrelation).
    H1: The series exhibits autocorrelation at one or more lags.

    Q(n,h) = n(n+2) * sum_{k=1}^{h} rho_k^2 / (n-k)

    Args:
        series: Time series of values (e.g., returns or |returns|)
        max_lag: Maximum lag to test (default 20)
        alpha: Significance level (default 0.05)

    Returns:
        LjungBoxResult with Q-statistic, p-value, and significant lags
    """
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)

    if n < 5:
        return LjungBoxResult(
            statistic=0.0, p_value=1.0, lags_tested=0,
            significant_lags=[], is_random=True, alpha=alpha,
        )

    # Clamp max_lag to at most n//2
    h = min(max_lag, n // 2)
    if h < 1:
        return LjungBoxResult(
            statistic=0.0, p_value=1.0, lags_tested=0,
            significant_lags=[], is_random=True, alpha=alpha,
        )

    mean = float(np.mean(x))
    variance = float(np.var(x, ddof=1))
    if variance < 1e-15:
        return LjungBoxResult(
            statistic=0.0, p_value=1.0, lags_tested=h,
            significant_lags=[], is_random=True, alpha=alpha,
        )

    # Compute autocorrelations
    acf_vals = []
    for k in range(1, h + 1):
        cov = float(np.sum((x[:n - k] - mean) * (x[k:] - mean))) / n
        rho_k = cov / variance
        acf_vals.append(rho_k)

    # Q-statistic
    q_stat = 0.0
    for k, rho_k in enumerate(acf_vals, start=1):
        q_stat += rho_k ** 2 / (n - k)
    q_stat *= n * (n + 2)

    # p-value from chi-squared with h degrees of freedom
    p_value = 1.0 - _chi_squared_cdf(q_stat, h)

    # Individual lag significance (Bonferroni-corrected)
    significant_lags = []
    threshold = alpha / h  # Bonferroni correction
    se = 1.0 / math.sqrt(n)
    for k, rho_k in enumerate(acf_vals, start=1):
        z = abs(rho_k) / se
        # Two-sided test
        p_lag = 2.0 * (1.0 - _normal_cdf(z))
        if p_lag < threshold:
            significant_lags.append(k)

    is_random = p_value >= alpha

    return LjungBoxResult(
        statistic=q_stat,
        p_value=p_value,
        lags_tested=h,
        significant_lags=significant_lags,
        is_random=is_random,
        alpha=alpha,
    )


# ---------------------------------------------------------------------------
# ACF / PACF computation
# ---------------------------------------------------------------------------

def compute_acf(series: Sequence[float], max_lag: int = 20) -> ACFResult:
    """Compute autocorrelation function.

    Args:
        series: Time series values
        max_lag: Maximum lag to compute

    Returns:
        ACFResult with lag values and 95% confidence band
    """
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    h = min(max_lag, n // 2)

    if n < 3:
        return ACFResult(lags=[], acf_values=[], confidence_band=1.0)

    mean = float(np.mean(x))
    variance = float(np.var(x, ddof=1))
    conf_band = 1.96 / math.sqrt(n)

    lags = list(range(1, h + 1))
    acf_values = []

    for k in lags:
        cov = float(np.sum((x[:n - k] - mean) * (x[k:] - mean))) / n
        rho = cov / variance if variance > 1e-15 else 0.0
        acf_values.append(max(-1.0, min(1.0, rho)))

    return ACFResult(lags=lags, acf_values=acf_values, confidence_band=conf_band)


def compute_pacf(series: Sequence[float], max_lag: int = 20) -> PACFResult:
    """Compute partial autocorrelation function using Durbin-Levinson recursion.

    PACF at lag k is the autocorrelation of the series with its own
    lagged values, after removing the linear dependence of intermediate lags.
    """
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    h = min(max_lag, n // 2)

    if n < 3:
        return PACFResult(lags=[], pacf_values=[], confidence_band=1.0)

    mean = float(np.mean(x))
    centered = x - mean
    n_eff = len(centered)

    # Compute ACF up to h
    variance = float(np.var(centered, ddof=1))
    if variance < 1e-15:
        return PACFResult(lags=list(range(1, h + 1)),
                          pacf_values=[0.0] * h,
                          confidence_band=1.96 / math.sqrt(n))

    acf = []
    for k in range(1, h + 1):
        rho = float(np.sum(centered[:n_eff - k] * centered[k:])) / (n_eff * variance)
        acf.append(rho)

    # Durbin-Levinson recursion
    pacf_vals = []
    phi = np.zeros((h + 1, h + 1))

    for k in range(1, h + 1):
        if k == 1:
            phi[1, 1] = acf[0]
        else:
            num = acf[k - 1] - sum(phi[k - 1, j] * acf[k - 1 - j] for j in range(1, k))
            denom = 1.0 - sum(phi[k - 1, j] * acf[j - 1] for j in range(1, k))
            phi[k, k] = num / denom if abs(denom) > 1e-15 else 0.0
            for j in range(1, k):
                phi[k, j] = phi[k - 1, j] - phi[k, k] * phi[k - 1, k - j]

        pacf_vals.append(phi[k, k])

    conf_band = 1.96 / math.sqrt(n)

    return PACFResult(lags=list(range(1, h + 1)),
                      pacf_values=[max(-1.0, min(1.0, v)) for v in pacf_vals],
                      confidence_band=conf_band)


# ---------------------------------------------------------------------------
# Durbin-Watson statistic
# ---------------------------------------------------------------------------

def durbin_watson(series: Sequence[float]) -> float:
    """Durbin-Watson test for first-order autocorrelation.

    DW ≈ 2(1 - rho_1) where rho_1 is the lag-1 autocorrelation.
    DW ≈ 2 → no autocorrelation
    DW ≈ 0 → positive autocorrelation
    DW ≈ 4 → negative autocorrelation

    Returns:
        Durbin-Watson statistic (0 to 4)
    """
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return 2.0

    diffs = np.diff(x)
    ss_diffs = float(np.sum(diffs ** 2))
    ss_levels = float(np.sum((x - np.mean(x)) ** 2))

    if ss_levels < 1e-15:
        return 2.0

    return max(0.0, min(4.0, ss_diffs / ss_levels))


# ---------------------------------------------------------------------------
# Runs test for randomness
# ---------------------------------------------------------------------------

def runs_test(series: Sequence[float], cutoff: float = 0.0) -> float:
    """Wald-Wolfowitz runs test for randomness.

    Tests whether the number of "runs" (consecutive same-sign observations)
    is consistent with a random series.

    Args:
        series: Time series values
        cutoff: Threshold to classify observations as above/below

    Returns:
        p-value of the test (H0: series is random)
    """
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)

    if n < 10:
        return 1.0

    # Binary sequence: above/below cutoff
    signs = (x > cutoff).astype(int)
    n_pos = int(np.sum(signs))
    n_neg = n - n_pos

    if n_pos == 0 or n_neg == 0:
        return 1.0

    # Count runs
    runs = 1
    for i in range(1, n):
        if signs[i] != signs[i - 1]:
            runs += 1

    # Expected runs and variance
    expected = (2.0 * n_pos * n_neg) / n + 1.0
    variance = (2.0 * n_pos * n_neg * (2.0 * n_pos * n_neg - n)) / (n ** 2 * (n - 1))

    if variance < 1e-10:
        return 1.0

    z = (runs - expected) / math.sqrt(variance)
    p_value = 2.0 * (1.0 - _normal_cdf(abs(z)))

    return max(0.0, min(1.0, p_value))


# ---------------------------------------------------------------------------
# Full diagnostics
# ---------------------------------------------------------------------------

def diagnose_returns(
    returns: Sequence[float],
    max_lag: int = 20,
    alpha: float = 0.05,
) -> ReturnDiagnostics:
    """Run full return diagnostics: autocorrelation, volatility clustering, randomness.

    Args:
        returns: Series of period returns
        max_lag: Maximum lag for autocorrelation tests
        alpha: Significance level

    Returns:
        ReturnDiagnostics with all test results and interpretation
    """
    x = np.asarray(returns, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)

    # Ljung-Box on raw returns (linear dependence)
    lb_returns = ljung_box_test(x, max_lag=max_lag, alpha=alpha)

    # Ljung-Box on |returns| (volatility clustering / ARCH effects)
    lb_abs_returns = ljung_box_test(np.abs(x), max_lag=max_lag, alpha=alpha)

    # ACF / PACF
    acf_r = compute_acf(x, max_lag=max_lag)
    acf_ar = compute_acf(np.abs(x), max_lag=max_lag)
    pacf_r = compute_pacf(x, max_lag=max_lag)

    # Durbin-Watson
    dw = durbin_watson(x)

    # Runs test
    rt_p = runs_test(x, cutoff=0.0)

    # Interpretation
    has_linear_dep = not lb_returns.is_random
    has_vol_clust = not lb_abs_returns.is_random
    is_white_noise = not has_linear_dep and not has_vol_clust

    # Summary
    parts = [f"n={n}"]
    if has_linear_dep:
        parts.append(f"RETURNS AUTOCORRELATED (Q={lb_returns.statistic:.2f}, p={lb_returns.p_value:.4f})")
    else:
        parts.append("returns: no linear dependence")
    if has_vol_clust:
        parts.append(f"VOL CLUSTERING (Q={lb_abs_returns.statistic:.2f}, p={lb_abs_returns.p_value:.4f})")
    else:
        parts.append("|returns|: no volatility clustering")
    parts.append(f"DW={dw:.3f}")
    if is_white_noise:
        parts.append("WHITE NOISE ✓")
    else:
        parts.append("NOT white noise")

    return ReturnDiagnostics(
        n_observations=n,
        returns=lb_returns,
        abs_returns=lb_abs_returns,
        acf_returns=acf_r,
        acf_abs_returns=acf_ar,
        pacf_returns=pacf_r,
        durbin_watson=dw,
        runs_test_p_value=rt_p,
        has_volatility_clustering=has_vol_clust,
        has_linear_dependence=has_linear_dep,
        is_white_noise=is_white_noise,
        summary=" | ".join(parts),
    )


# ---------------------------------------------------------------------------
# Regime-aware diagnostics
# ---------------------------------------------------------------------------

def diagnose_by_regime(
    returns: Sequence[float],
    regimes: Sequence[int],
    max_lag: int = 20,
    alpha: float = 0.05,
) -> Dict[int, ReturnDiagnostics]:
    """Run diagnostics per regime segment.

    Detects if autocorrelation structure changes across regimes (e.g.,
    trending regime has positive autocorrelation, mean-reverting regime
    has negative autocorrelation).

    Args:
        returns: Series of returns
        regimes: Regime labels (same length as returns)
        max_lag: Maximum lag for tests
        alpha: Significance level

    Returns:
        Dict mapping regime label to ReturnDiagnostics
    """
    r = np.asarray(returns, dtype=float)
    reg = np.asarray(regimes, dtype=int)

    if len(r) != len(reg):
        raise ValueError("returns and regimes must have equal length")

    unique_regimes = sorted(set(reg))
    results = {}

    for regime_id in unique_regimes:
        mask = reg == regime_id
        if np.sum(mask) < 10:
            continue
        results[regime_id] = diagnose_returns(r[mask], max_lag=max_lag, alpha=alpha)

    return results
