"""Hawkes Self-Exciting Point-Process Engine.

Implements the univariate Hawkes process with exponential kernel for trade arrival
clustering, momentum cascade detection, and microstructure contagion:

    lambda(t) = mu + sum_{t_i < t} alpha * exp(-beta * (t - t_i))

Branching Ratio:
    n = alpha / beta

Regimes:
    - n >= 1.00: SUPER_CRITICAL (volatility explosion / liquidity vacuum risk)
    - n >= 0.85: MOMENTUM_CASCADE (self-exciting cluster -> breakout continuation)
    - 0.30 <= n < 0.85: MODERATE_CLUSTERING (normal trading flow)
    - n < 0.30: POISSON_BASELINE (stationary exogenous arrivals -> range-bound)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Union
import numpy as np

try:
    from scipy.optimize import minimize
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


@dataclass
class HawkesResult:
    """Estimated parameters and regime classification of a Hawkes point process."""
    mu: float               # Baseline intensity (events / second)
    alpha: float            # Self-excitation magnitude
    beta: float             # Exponential decay rate
    branching_ratio: float  # n = alpha / beta
    log_likelihood: float   # Log-likelihood at optimum
    event_count: int        # Number of events calibrated
    regime: str = ""        # SUPER_CRITICAL, MOMENTUM_CASCADE, MODERATE_CLUSTERING, POISSON_BASELINE, INSUFFICIENT_DATA
    is_cascade: bool = False

    def __post_init__(self) -> None:
        if not self.regime:
            self._classify_regime()

    def _classify_regime(self) -> None:
        n = float(self.branching_ratio)
        if not math.isfinite(n):
            self.regime = "INSUFFICIENT_DATA"
            self.is_cascade = False
        elif n >= 1.0:
            self.regime = "SUPER_CRITICAL"
            self.is_cascade = True
        elif n >= 0.85:
            self.regime = "MOMENTUM_CASCADE"
            self.is_cascade = True
        elif n >= 0.30:
            self.regime = "MODERATE_CLUSTERING"
            self.is_cascade = False
        else:
            self.regime = "POISSON_BASELINE"
            self.is_cascade = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mu": round(float(self.mu), 6),
            "alpha": round(float(self.alpha), 6),
            "beta": round(float(self.beta), 6),
            "branching_ratio": round(float(self.branching_ratio), 4),
            "log_likelihood": round(float(self.log_likelihood), 4),
            "event_count": int(self.event_count),
            "regime": self.regime,
            "is_cascade": bool(self.is_cascade),
        }


def hawkes_log_likelihood(
    mu: float,
    alpha: float,
    beta: float,
    timestamps: Union[Sequence[float], np.ndarray],
) -> float:
    """Calculates log-likelihood of univariate Hawkes process with exponential kernel.

    Uses O(M) recursive formulation for exponential decay:
        R(1) = 0
        R(i) = exp(-beta * (t_i - t_{i-1})) * (1 + R(i-1))
        lambda(t_i) = mu + alpha * R(i)
    """
    ts = np.asarray(timestamps, dtype=float)
    m = len(ts)
    if m < 2:
        return 0.0

    mu = max(float(mu), 1e-6)
    alpha = max(float(alpha), 0.0)
    beta = max(float(beta), 1e-4)

    t_0 = ts[0]
    t_m = ts[-1]
    total_time = max(t_m - t_0, 1e-6)

    # 1. Sum of log intensities ln(lambda(t_i))
    r = 0.0
    log_sum = math.log(mu)  # at i = 0

    for i in range(1, m):
        dt = max(0.0, ts[i] - ts[i - 1])
        r = math.exp(-beta * dt) * (1.0 + r)
        intensity = mu + alpha * r
        log_sum += math.log(max(intensity, 1e-9))

    # 2. Integral of intensity over [t_0, t_M]
    # int_0^T lambda(s) ds = mu * T + (alpha / beta) * sum_{i=0}^{M-1} (1 - exp(-beta * (T - t_i)))
    time_to_end = t_m - ts
    exp_terms = np.exp(-np.clip(beta * time_to_end, 0.0, 700.0))
    integral = mu * total_time + (alpha / beta) * float(np.sum(1.0 - exp_terms))

    return float(log_sum - integral)


def calibrate_hawkes_process(
    timestamps: Sequence[float],
    max_events: int = 500,
) -> HawkesResult:
    """Calibrates Hawkes process parameters (mu, alpha, beta) from trade arrival timestamps."""
    if not timestamps or len(timestamps) < 10:
        return HawkesResult(
            mu=0.0, alpha=0.0, beta=1.0, branching_ratio=0.0,
            log_likelihood=0.0, event_count=len(timestamps) if timestamps else 0,
            regime="INSUFFICIENT_DATA", is_cascade=False,
        )

    ts = np.asarray(sorted(timestamps[-max_events:]), dtype=float)
    # Re-zero timestamps to avoid large floating point offsets
    ts = ts - ts[0]
    m = len(ts)
    total_time = max(ts[-1], 1e-5)
    mean_rate = float(m / total_time)

    # Initial guess
    init_mu = max(mean_rate * 0.3, 1e-3)
    init_alpha = max(mean_rate * 0.5, 1e-3)
    init_beta = 1.0

    def objective(params: np.ndarray) -> float:
        mu_val, alpha_val, beta_val = params
        ll = hawkes_log_likelihood(mu_val, alpha_val, beta_val, ts)
        if not math.isfinite(ll):
            return 1e12
        return -ll

    best_mu, best_alpha, best_beta, best_ll = init_mu, init_alpha, init_beta, -1e12

    if SCIPY_AVAILABLE:
        bounds = [(1e-4, max(mean_rate * 2.0, 10.0)), (1e-4, 50.0), (1e-2, 50.0)]
        init_guesses = [
            np.array([init_mu, init_alpha, init_beta]),
            np.array([mean_rate * 0.1, mean_rate * 0.8, 1.0]),
            np.array([mean_rate * 0.5, mean_rate * 0.2, 2.0]),
        ]
        for guess in init_guesses:
            try:
                res = minimize(objective, guess, method="L-BFGS-B", bounds=bounds,
                               options={"maxiter": 60, "ftol": 1e-4})
                if res.success and -res.fun > best_ll:
                    best_mu, best_alpha, best_beta = res.x
                    best_ll = -res.fun
            except Exception:
                continue

    # Analytical moment-matching fallback if optimization failed or unavailable
    if best_ll == -1e12:
        diffs = np.diff(ts)
        cv = float(np.std(diffs) / max(np.mean(diffs), 1e-6))
        # Dispersion > 1 implies clustering/super-Poissonian burstiness
        if cv > 1.2:
            branching_est = min(0.92, 1.0 - (1.0 / (cv ** 2)))
        elif cv > 1.0:
            branching_est = 0.50
        else:
            branching_est = max(0.05, 0.25 * cv)

        best_beta = 1.0
        best_alpha = branching_est * best_beta
        best_mu = max(1e-4, mean_rate * (1.0 - branching_est))
        best_ll = hawkes_log_likelihood(best_mu, best_alpha, best_beta, ts)

    branching_ratio = float(best_alpha / max(best_beta, 1e-6))
    branching_ratio = max(0.0, min(2.5, branching_ratio))

    return HawkesResult(
        mu=float(best_mu),
        alpha=float(best_alpha),
        beta=float(best_beta),
        branching_ratio=branching_ratio,
        log_likelihood=float(best_ll),
        event_count=m,
    )
