"""Ornstein-Uhlenbeck Time Stop with Half-Life Estimation.

Implements Part3 §10.1 (Chan's OU Time Stops):
    If position fails to mean-revert within 2*lambda to 3*lambda bars, close.
    OU half-life: t_1/2 = ln(2) / theta

Also includes:
- Full OU parameter estimation (theta, mu, sigma)
- Time stop calculation based on OU dynamics
- Fixed price stops replaced by OU-driven time stops
- Integration with position manager for rule-based exits
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
import numpy as np


@dataclass
class OUParameters:
    """Estimated Ornstein-Uhlenbeck parameters."""
    theta: float        # Mean-reversion speed (negative = mean-reverting)
    mu: float           # Long-term mean
    sigma: float        # Volatility of the process
    half_life: float    # Half-life in periods: ln(2) / |theta|
    is_mean_reverting: bool  # True if theta < 0
    adf_statistic: float     # ADF test statistic for stationarity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "theta": round(self.theta, 6),
            "mu": round(self.mu, 6),
            "sigma": round(self.sigma, 6),
            "half_life": round(self.half_life, 2),
            "is_mean_reverting": self.is_mean_reverting,
            "adf_statistic": round(self.adf_statistic, 3),
        }


@dataclass
class OUTimeStopResult:
    """Result of OU time stop evaluation."""
    time_stop_bars: int         # Close if not mean-reverted within this many bars
    ou_parameters: OUParameters
    is_stagnant: bool           # True if position has exceeded time stop
    bars_held: int
    expected_reversion_time: float
    recommended_action: str     # "HOLD", "TIME_STOP_CLOSE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time_stop_bars": self.time_stop_bars,
            "ou_parameters": self.ou_parameters.to_dict(),
            "is_stagnant": self.is_stagnant,
            "bars_held": self.bars_held,
            "expected_reversion_time": round(self.expected_reversion_time, 2),
            "recommended_action": self.recommended_action,
        }


class OUTimeStopEngine:
    """Ornstein-Uhlenbeck time stop for mean-reversion strategies.

    Replaces fixed price stops with OU-based time stops:
    - Estimate OU half-life from price series
    - If position hasn't mean-reverted within 2-3 half-lives, close
    - Prevents "stuck" positions that fail to revert

    Typical usage:
        engine = OUTimeStopEngine()
        params = engine.estimate_ou_parameters(prices)
        result = engine.evaluate_time_stop(params, bars_held=20)
        if result.is_stagnant:
            close_position()
    """

    def __init__(
        self,
        multiplier: float = 2.5,
        min_half_life: int = 5,
        max_half_life: int = 100,
    ):
        """
        Args:
            multiplier: Half-life multiplier for time stop (2.0 = 2*lambda, 3.0 = 3*lambda).
            min_half_life: Minimum half-life in bars (floor).
            max_half_life: Maximum half-life in bars (cap).
        """
        self.multiplier = max(1.0, min(5.0, float(multiplier)))
        self.min_half_life = max(1, int(min_half_life))
        self.max_half_life = max(10, int(max_half_life))

    def estimate_ou_parameters(
        self,
        series: Union[List[float], np.ndarray],
    ) -> OUParameters:
        """Estimate OU parameters from price series.

        OU process: dX = theta*(mu - X)*dt + sigma*dW
        Discretized: Delta_y_t = theta*(mu - y_{t-1}) + epsilon_t

        Returns OUParameters with theta, mu, sigma, half_life.
        """
        arr = np.asarray(series, dtype=float)
        arr = arr[~np.isnan(arr)]
        if len(arr) < 20:
            return OUParameters(
                theta=0.0, mu=float(np.mean(arr)) if len(arr) > 0 else 0.0,
                sigma=0.0, half_life=float('inf'),
                is_mean_reverting=False, adf_statistic=0.0,
            )

        y = arr
        y_lag = arr[:-1]
        y_diff = np.diff(arr)

        # OLS regression: Delta_y = alpha + beta * y_{t-1} + epsilon
        mean_lag = np.mean(y_lag)
        mean_diff = np.mean(y_diff)
        var_lag = np.sum((y_lag - mean_lag) ** 2)
        if var_lag == 0:
            return OUParameters(
                theta=0.0, mu=mean_lag, sigma=0.0, half_life=float('inf'),
                is_mean_reverting=False, adf_statistic=0.0,
            )

        beta = np.sum((y_lag - mean_lag) * (y_diff - mean_diff)) / var_lag
        alpha = mean_diff - beta * mean_lag

        # OU parameters
        theta = float(beta)
        mu = float(-alpha / beta) if abs(beta) > 1e-10 else mean_lag

        # Residuals for sigma estimation
        residuals = (y_diff - mean_diff) - beta * (y_lag - mean_lag)
        sigma = float(np.std(residuals))

        # ADF test statistic
        se_beta = math.sqrt(np.sum(residuals ** 2) / (len(y_lag) - 2) / var_lag) if var_lag > 0 else 1.0
        adf_stat = float(beta / se_beta) if se_beta > 0 else 0.0

        # Half-life: t_1/2 = ln(2) / |theta|
        is_mr = theta < 0
        if is_mr and abs(theta) > 1e-10:
            half_life = math.log(2.0) / abs(theta)
        else:
            half_life = float('inf')

        return OUParameters(
            theta=theta,
            mu=mu,
            sigma=sigma,
            half_life=half_life,
            is_mean_reverting=is_mr,
            adf_statistic=adf_stat,
        )

    def evaluate_time_stop(
        self,
        ou_params: OUParameters,
        bars_held: int,
        current_price: Optional[float] = None,
        entry_price: Optional[float] = None,
    ) -> OUTimeStopResult:
        """Evaluate whether a position should be closed based on OU time stop.

        Rule: If position held > multiplier * half_life → close (Part3 §10.1).
        """
        hl = ou_params.half_life
        if not math.isfinite(hl) or hl <= 0:
            time_stop = self.max_half_life
        else:
            time_stop = max(self.min_half_life, min(self.max_half_life, int(self.multiplier * hl)))

        is_stagnant = bars_held >= time_stop

        # Expected reversion time: 1/|theta|
        if ou_params.is_mean_reverting and abs(ou_params.theta) > 1e-10:
            expected_reversion = 1.0 / abs(ou_params.theta)
        else:
            expected_reversion = float('inf')

        if is_stagnant:
            action = "TIME_STOP_CLOSE"
        else:
            action = "HOLD"

        return OUTimeStopResult(
            time_stop_bars=time_stop,
            ou_parameters=ou_params,
            is_stagnant=is_stagnant,
            bars_held=bars_held,
            expected_reversion_time=expected_reversion,
            recommended_action=action,
        )

    def get_half_life(
        self,
        series: Union[List[float], np.ndarray],
    ) -> float:
        """Quick helper: estimate OU half-life from a series."""
        params = self.estimate_ou_parameters(series)
        return params.half_life


def evaluate_scalp_time_stop(
    prices: Union[List[float], np.ndarray],
    bars_held: int,
    multiplier: float = 2.5,
) -> OUTimeStopResult:
    """Evaluates whether an active scalp position has exceeded its OU half-life timeout."""
    engine = OUTimeStopEngine(multiplier=multiplier)
    params = engine.estimate_ou_parameters(prices)
    return engine.evaluate_time_stop(params, bars_held=bars_held)


# Canonical aliases
OrnsteinUhlenbeckTimeStop = OUTimeStopEngine
MeanReversionTimeStop = OUTimeStopEngine

