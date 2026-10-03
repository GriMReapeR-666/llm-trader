"""Econometric Modeling & Volatility Features.

Reference:
"Econometric Modelling and Algorithmic Trading of Bitcoin" (2026).

Implements:
1. Augmented Dickey-Fuller (ADF) Unit Root Test for Time Series Stationarity.
2. GARCH(1,1) Volatility Modeling and Volatility Persistence Estimation.
3. Multi-Step Forward Volatility Forecasting with Mean Reversion.
4. Volatility Clustering and Stylized Heavy Tail Detection (Mandelbrot / Engle).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize


@dataclass
class ADFTestResult:
    test_statistic: float
    p_value: float
    is_stationary: bool
    critical_values: Dict[str, float]
    used_lag: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GARCHResult:
    omega: float
    alpha: float
    beta: float
    persistence: float
    unconditional_volatility: float
    conditional_volatilities: np.ndarray
    latest_forecast: float
    is_covariance_stationary: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "omega": self.omega,
            "alpha": self.alpha,
            "beta": self.beta,
            "persistence": self.persistence,
            "unconditional_volatility": self.unconditional_volatility,
            "latest_forecast": self.latest_forecast,
            "is_covariance_stationary": self.is_covariance_stationary,
        }


def calculate_adf_test(series: np.ndarray, max_lags: int = 2) -> ADFTestResult:
    """Perform Augmented Dickey-Fuller (ADF) test for stationarity."""
    y = np.asarray(series, dtype=np.float64).ravel()
    n = len(y)

    crit_values = {"1%": -3.434, "5%": -2.863, "10%": -2.568}

    if n < 15:
        return ADFTestResult(
            test_statistic=0.0,
            p_value=1.0,
            is_stationary=False,
            critical_values=crit_values,
            used_lag=0,
        )

    # First difference: dy_t = y_t - y_{t-1}
    dy = np.diff(y)
    y_lag = y[:-1]

    lags = max(1, min(max_lags, int(np.floor(12 * (n / 100) ** 0.25))))

    # Align vectors
    t_start = lags
    dy_target = dy[t_start:]
    y_level = y_lag[t_start:]

    # Construct regression design matrix: [1, y_{t-1}, dy_{t-1}, ..., dy_{t-lags}]
    k = len(dy_target)
    ones = np.ones((k, 1))
    x_mat = [ones, y_level[:, None]]

    for lag in range(1, lags + 1):
        x_mat.append(dy[t_start - lag : t_start - lag + k, None])

    X = np.hstack(x_mat)

    # OLS regression: beta_hat = (X^T X)^{-1} X^T y
    try:
        xtx = X.T @ X
        xty = X.T @ dy_target
        beta_hat = np.linalg.solve(xtx, xty)
        residuals = dy_target - X @ beta_hat
        df = max(1, k - X.shape[1])
        sigma2 = float(np.sum(residuals ** 2) / df)
        inv_xtx = np.linalg.inv(xtx)
        se_beta1 = math.sqrt(max(1e-14, sigma2 * inv_xtx[1, 1]))
        t_stat = float(beta_hat[1] / se_beta1)
    except Exception:
        t_stat = 0.0

    # Approximate p-value from Dickey-Fuller empirical distribution
    if t_stat <= crit_values["1%"]:
        p_val = 0.005
    elif t_stat <= crit_values["5%"]:
        p_val = 0.01 + 0.04 * ((t_stat - crit_values["1%"]) / (crit_values["5%"] - crit_values["1%"]))
    elif t_stat <= crit_values["10%"]:
        p_val = 0.05 + 0.05 * ((t_stat - crit_values["5%"]) / (crit_values["10%"] - crit_values["5%"]))
    else:
        p_val = 0.10 + 0.90 / (1.0 + math.exp(-0.8 * (t_stat - crit_values["10%"])))

    p_val = float(np.clip(p_val, 0.001, 0.999))
    is_stat = p_val < 0.05

    return ADFTestResult(
        test_statistic=round(t_stat, 4),
        p_value=round(p_val, 4),
        is_stationary=is_stat,
        critical_values=crit_values,
        used_lag=lags,
    )


def fit_garch11(returns: np.ndarray) -> GARCHResult:
    """Fit a GARCH(1,1) volatility model via maximum likelihood estimation."""
    r = np.asarray(returns, dtype=np.float64).ravel()
    n = len(r)

    var_sample = float(np.var(r)) if n > 0 else 0.0
    if n < 20 or var_sample <= 1e-12:
        return GARCHResult(
            omega=round(var_sample * 0.10, 8),
            alpha=0.08,
            beta=0.82,
            persistence=0.90,
            unconditional_volatility=round(math.sqrt(var_sample), 6),
            conditional_volatilities=np.full(n, math.sqrt(var_sample)),
            latest_forecast=round(math.sqrt(var_sample), 6),
            is_covariance_stationary=True,
        )

    # De-mean returns
    eps = r - np.mean(r)
    eps2 = eps ** 2

    # Negative log-likelihood objective
    def _nll(params: np.ndarray) -> float:
        w, a, b = params
        if w <= 0 or a < 0 or b < 0 or (a + b) >= 1.0:
            return 1e12

        sig2 = np.empty(n)
        sig2[0] = w / (1.0 - a - b)
        for t in range(1, n):
            sig2[t] = w + a * eps2[t - 1] + b * sig2[t - 1]

        # Gaussian log-likelihood
        # LL = -0.5 * sum(ln(2*pi) + ln(sig2) + eps2 / sig2)
        ll = -0.5 * np.sum(np.log(np.maximum(1e-14, sig2)) + (eps2 / np.maximum(1e-14, sig2)))
        return float(-ll)

    init_alpha = 0.10
    init_beta = 0.85
    init_omega = var_sample * (1.0 - init_alpha - init_beta)
    init_params = np.array([max(1e-7, init_omega), init_alpha, init_beta])

    bounds = [
        (1e-9, max(1e-3, var_sample * 2.0)),
        (0.01, 0.35),
        (0.50, 0.98),
    ]

    constraints = {"type": "ineq", "fun": lambda p: 0.999 - (p[1] + p[2])}

    try:
        res = minimize(
            _nll,
            init_params,
            bounds=bounds,
            constraints=constraints,
            method="SLSQP",
            options={"maxiter": 150, "ftol": 1e-6},
        )
        if res.success and (res.x[1] + res.x[2]) < 1.0:
            omega_est, alpha_est, beta_est = float(res.x[0]), float(res.x[1]), float(res.x[2])
        else:
            omega_est, alpha_est, beta_est = init_params[0], init_params[1], init_params[2]
    except Exception:
        omega_est, alpha_est, beta_est = init_params[0], init_params[1], init_params[2]

    persistence = alpha_est + beta_est
    is_cov_stat = persistence < 1.0

    uncond_var = omega_est / max(1e-6, 1.0 - persistence) if is_cov_stat else var_sample
    uncond_vol = math.sqrt(max(0.0, uncond_var))

    # Compute trajectory of conditional volatilities
    sig2_traj = np.empty(n)
    sig2_traj[0] = uncond_var
    for t in range(1, n):
        sig2_traj[t] = omega_est + alpha_est * eps2[t - 1] + beta_est * sig2_traj[t - 1]

    # Latest 1-step forecast
    latest_sig2 = omega_est + alpha_est * eps2[-1] + beta_est * sig2_traj[-1]
    latest_vol = math.sqrt(max(1e-12, latest_sig2))

    return GARCHResult(
        omega=float(omega_est),
        alpha=float(alpha_est),
        beta=float(beta_est),
        persistence=round(persistence, 4),
        unconditional_volatility=round(uncond_vol, 6),
        conditional_volatilities=np.sqrt(np.maximum(1e-12, sig2_traj)),
        latest_forecast=round(latest_vol, 6),
        is_covariance_stationary=is_cov_stat,
    )


def forecast_garch_volatility(garch_result: GARCHResult, horizon_bars: int = 5) -> float:
    """Multi-step forward volatility forecast with mean-reversion towards unconditional variance."""
    h = max(1, int(horizon_bars))
    p = garch_result.persistence
    uncond_var = garch_result.unconditional_volatility ** 2
    latest_var = garch_result.latest_forecast ** 2

    if h == 1:
        return float(garch_result.latest_forecast)

    # Forecast cumulative average variance over horizon h:
    # E[sigma_{t+k}^2] = uncond_var + P^{k-1} * (latest_var - uncond_var)
    var_sum = 0.0
    for k in range(1, h + 1):
        k_var = uncond_var + (p ** (k - 1)) * (latest_var - uncond_var)
        var_sum += max(1e-12, k_var)

    avg_var = var_sum / h
    return round(math.sqrt(avg_var), 6)


def detect_volatility_clustering(returns: np.ndarray, lags: int = 5) -> Dict[str, Any]:
    """Detect presence of volatility clustering and fat-tail stylized facts (Mandelbrot / Engle)."""
    r = np.asarray(returns, dtype=np.float64).ravel()
    n = len(r)
    if n < 30:
        return {
            "has_volatility_clustering": False,
            "kurtosis": 0.0,
            "squared_return_autocorr_lag1": 0.0,
        }

    # Excess Kurtosis
    eps = r - np.mean(r)
    var = np.var(eps)
    if var <= 1e-12:
        return {
            "has_volatility_clustering": False,
            "kurtosis": 0.0,
            "squared_return_autocorr_lag1": 0.0,
        }

    kurt = float(np.mean(eps ** 4) / (var ** 2) - 3.0)

    # Autocorrelation of squared returns
    eps2 = eps ** 2
    eps2_centered = eps2 - np.mean(eps2)
    denom = float(np.sum(eps2_centered ** 2))

    autocorrs: List[float] = []
    for lag in range(1, lags + 1):
        cov = float(np.sum(eps2_centered[lag:] * eps2_centered[:-lag]))
        ac = cov / denom if denom > 1e-12 else 0.0
        autocorrs.append(ac)

    lag1_ac = autocorrs[0] if autocorrs else 0.0
    has_clustering = (lag1_ac > 0.05 and kurt > 0.0)

    return {
        "has_volatility_clustering": has_clustering,
        "kurtosis": round(kurt, 4),
        "squared_return_autocorr_lag1": round(lag1_ac, 4),
        "autocorrelations": [round(a, 4) for a in autocorrs],
    }
