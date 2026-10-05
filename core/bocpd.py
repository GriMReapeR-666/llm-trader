"""Module 03: Bayesian Online Changepoint Detection (BOCPD).

Implements Ryan Prescott Adams & David J.C. MacKay (2007) BOCPD algorithm.
Models the run-length probability r_t given incoming observations x_{1:t}:
    P(r_t | x_{1:t}) proportional to sum_{r_{t-1}} P(r_t | r_{t-1}) P(x_t | r_{t-1}, theta) P(r_{t-1} | x_{1:t-1})
with constant hazard function H(tau) = 1 / lambda.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, Union
import numpy as np


class BayesianOnlineChangepointDetector:
    """Online sub-tick Bayesian Changepoint Detection."""

    def __init__(
        self,
        hazard_lambda: float = 100.0,
        mean_prior: float = 0.0,
        var_prior: float = 1.0,
        max_run_length: int = 200,
    ):
        self.hazard_lambda = max(float(hazard_lambda), 1.0)
        self.hazard = 1.0 / self.hazard_lambda
        self.mean_prior = float(mean_prior)
        self.var_prior = float(var_prior)
        self.max_run_length = int(max_run_length)

        # R_matrix tracks posterior run-length probabilities: P(r_t = k)
        self.R = np.array([1.0])
        self.history: List[float] = []

    def update(self, x: float) -> float:
        """Process new observation x_t and return the changepoint probability Pr(CP_t) = P(r_t = 0)."""
        val = float(x)
        self.history.append(val)

        # 1. Predictive probability under Gaussian model for each possible run-length
        n = len(self.R)
        pred_probs = np.zeros(n)
        for r in range(n):
            if r == 0:
                mu = self.mean_prior
                var = max(self.var_prior, 4.0)
            else:
                data_window = self.history[-r - 1 : -1]
                mu = float(np.mean(data_window)) if len(data_window) > 0 else self.mean_prior
                var = float(np.var(data_window)) + 0.05 if len(data_window) > 1 else self.var_prior
            std = math.sqrt(max(var, 1e-6))
            diff = (val - mu) / std
            pdf = math.exp(-0.5 * min(diff ** 2, 50.0)) / (std * math.sqrt(2.0 * math.pi))
            pred_probs[r] = max(pdf, 1e-12)

        # 2. Growth probabilities: P(r_t = r_{t-1} + 1)
        growth_probs = self.R * pred_probs * (1.0 - self.hazard)

        # 3. Changepoint probability: P(r_t = 0) generated from new regime prior
        cp_prob = float(np.sum(self.R * self.hazard) * pred_probs[0])

        # 4. Concatenate and normalize posterior run-length distribution
        new_R = np.empty(n + 1)
        new_R[0] = cp_prob
        new_R[1:] = growth_probs
        total_mass = np.sum(new_R)
        if total_mass > 0:
            new_R /= total_mass
        else:
            new_R = np.zeros(n + 1)
            new_R[0] = 1.0

        # Truncate if exceeds max_run_length
        if len(new_R) > self.max_run_length:
            new_R = new_R[: self.max_run_length]
            new_R /= np.sum(new_R)

        self.R = new_R
        return float(new_R[0])  # P(r_t = 0)

    @property
    def changepoint_probability(self) -> float:
        """Current probability that a changepoint occurred at step t."""
        if not self.history:
            return 0.0
        return float(self.R[0]) if len(self.R) > 0 else 0.0

    @property
    def expected_run_length(self) -> float:
        """Expected run length E[r_t] = sum_k k * P(r_t = k)."""
        k = np.arange(len(self.R))
        return float(np.sum(k * self.R))


def detect_changepoints_offline(
    series: Union[List[float], np.ndarray],
    hazard_lambda: float = 50.0,
    threshold: float = 0.30,
) -> List[Dict[str, Any]]:
    """Run BOCPD over a full historical series and return detected changepoints."""
    detector = BayesianOnlineChangepointDetector(hazard_lambda=hazard_lambda)
    changepoints = []
    for i, val in enumerate(series):
        cp_prob = detector.update(val)
        if cp_prob >= threshold:
            changepoints.append({
                "index": i,
                "value": float(val),
                "changepoint_probability": round(cp_prob, 4),
                "expected_run_length": round(detector.expected_run_length, 2),
            })
    return changepoints


# Canonical exact alias
detect_changepoints_offline_series = detect_changepoints_offline

