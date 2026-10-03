"""Advisory-only online regime, volatility, and Kelly estimators.

These models contain no MT5 calls and have no execution authority. Callers must
pass the journal promotion gate before using their output to adjust confidence
or size; hard risk limits always remain in force.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Dict, List


@dataclass
class OnlineGarch:
    omega: float = 1e-7
    alpha: float = 0.08
    beta: float = 0.90
    variance: float = 1e-6
    observations: int = 0

    def __post_init__(self):
        try:
            numeric = [float(self.omega), float(self.alpha), float(self.beta), float(self.variance)]
        except (TypeError, ValueError, OverflowError):
            raise ValueError("GARCH state is malformed") from None
        if (not all(math.isfinite(value) for value in numeric)
                or any(value < 0 for value in numeric[:3]) or numeric[3] <= 0
                or numeric[1] + numeric[2] >= 1
                or not isinstance(self.observations, int) or isinstance(self.observations, bool)
                or self.observations < 0):
            raise ValueError("GARCH state is malformed")
        self.omega, self.alpha, self.beta, self.variance = numeric

    def update(self, log_return: float) -> float:
        try:
            value = float(log_return)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("GARCH log return must be finite") from None
        if not math.isfinite(value):
            raise ValueError("GARCH log return must be finite")
        if (not all(math.isfinite(float(item)) for item in
                     (self.omega, self.alpha, self.beta, self.variance))
                or self.omega < 0 or self.alpha < 0 or self.beta < 0
                or self.variance <= 0 or not isinstance(self.observations, int)
                or isinstance(self.observations, bool) or self.observations < 0):
            raise ValueError("GARCH state is malformed")
        self.variance = max(1e-12, self.omega + self.alpha * value * value + self.beta * self.variance)
        self.observations += 1
        return self.volatility

    @property
    def volatility(self) -> float:
        return math.sqrt(self.variance)

    def size_multiplier(self, target_volatility: float) -> float:
        try:
            target = float(target_volatility)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if (not math.isfinite(target) or target <= 0 or self.observations < 100
                or not math.isfinite(self.volatility) or self.volatility <= 0):
            return 0.0
        return max(0.0, min(1.0, target / self.volatility))

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class OnlineHMM:
    """A transparent 3-state Gaussian filtering model, not a trade signal."""
    transition: List[List[float]] = None
    means: List[float] = None
    variances: List[float] = None
    posterior: List[float] = None
    observations: int = 0

    def __post_init__(self):
        self.transition = self.transition or [[.94, .04, .02], [.05, .90, .05], [.08, .12, .80]]
        self.means = self.means or [0.0, 0.0002, 0.0]  # range, trend, high-vol
        self.variances = self.variances or [1e-6, 2e-6, 9e-6]
        self.posterior = self.posterior or [1 / 3, 1 / 3, 1 / 3]
        if (len(self.transition) != 3 or any(len(row) != 3 for row in self.transition)
                or len(self.means) != 3 or len(self.variances) != 3 or len(self.posterior) != 3):
            raise ValueError("HMM state must have three states")
        if any(not math.isfinite(float(value)) or float(value) < 0
               for row in self.transition for value in row):
            raise ValueError("HMM transition matrix is malformed")
        if any(abs(sum(float(value) for value in row) - 1.0) > 1e-6 for row in self.transition):
            raise ValueError("HMM transition rows must sum to one")
        if any(not math.isfinite(float(value)) for value in self.means):
            raise ValueError("HMM means are malformed")
        if any(not math.isfinite(float(value)) or float(value) <= 0 for value in self.variances):
            raise ValueError("HMM variances are malformed")
        if any(not math.isfinite(float(value)) or float(value) < 0 for value in self.posterior):
            raise ValueError("HMM posterior is malformed")
        total = sum(float(value) for value in self.posterior)
        if total <= 0:
            raise ValueError("HMM posterior must have positive mass")
        self.posterior = [float(value) / total for value in self.posterior]
        if not isinstance(self.observations, int) or isinstance(self.observations, bool) or self.observations < 0:
            raise ValueError("HMM observation count is malformed")

    def update(self, log_return: float) -> Dict[str, float]:
        try:
            log_return = float(log_return)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("HMM log return must be finite") from None
        if not math.isfinite(log_return):
            raise ValueError("HMM log return must be finite")
        predicted = [sum(self.posterior[i] * self.transition[i][j] for i in range(3)) for j in range(3)]
        likelihoods = []
        for mean, variance in zip(self.means, self.variances):
            likelihoods.append(math.exp(-((log_return - mean) ** 2) / (2 * variance)) / math.sqrt(2 * math.pi * variance))
        weights = [p * l for p, l in zip(predicted, likelihoods)]
        total = sum(weights) or 1.0
        self.posterior = [w / total for w in weights]
        self.observations += 1
        return self.probabilities

    @property
    def probabilities(self) -> Dict[str, float]:
        return dict(zip(("range", "trend", "high_vol"), self.posterior))

    @property
    def state(self) -> str:
        return max(self.probabilities, key=self.probabilities.get)

    def to_dict(self) -> Dict:
        return {"transition": self.transition, "means": self.means, "variances": self.variances,
                "posterior": self.posterior, "observations": self.observations}


def fractional_bayesian_kelly(wins: int, losses: int, avg_win_r: float, avg_loss_r: float,
                              fraction: float = .25, min_samples: int = 100) -> Dict[str, float | bool | str]:
    """Beta(1,1)-shrunk Kelly estimate, deliberately disabled below sample gate."""
    try:
        valid = (isinstance(wins, int) and not isinstance(wins, bool) and wins >= 0
                 and isinstance(losses, int) and not isinstance(losses, bool) and losses >= 0
                 and all(math.isfinite(float(value)) for value in (avg_win_r, avg_loss_r, fraction))
                 and float(avg_win_r) >= 0 and float(avg_loss_r) >= 0
                 and 0 < float(fraction) <= 1
                 and isinstance(min_samples, int) and not isinstance(min_samples, bool)
                 and min_samples >= 100)
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        return {"samples": 0, "posterior_win_rate": 0.0, "payoff_ratio": 0.0,
                "raw_kelly": 0.0, "fractional_kelly": 0.0, "eligible": False,
                "valid": False, "reason": "malformed Kelly inputs", "execution_authority": False}
    avg_win_r = float(avg_win_r)
    avg_loss_r = float(avg_loss_r)
    fraction = float(fraction)
    samples = wins + losses
    probability = (wins + 1) / (samples + 2)
    payoff = avg_win_r / avg_loss_r if avg_loss_r > 0 else 0.0
    raw = probability - ((1 - probability) / payoff) if payoff > 0 else 0.0
    eligible = samples >= min_samples and raw > 0
    return {"samples": samples, "posterior_win_rate": probability, "payoff_ratio": payoff,
            "raw_kelly": raw, "fractional_kelly": max(0.0, raw * fraction) if eligible else 0.0,
            "eligible": eligible, "valid": True, "execution_authority": False}
