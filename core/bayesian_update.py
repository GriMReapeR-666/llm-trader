"""Bayesian Online Win Rate Update Engine.

Implements Part2 §6.8: Bayesian Beta-Binomial updating for strategy edge estimation:
    Prior: p ~ Beta(alpha, beta)
    After win:  alpha += 1
    After loss: beta += 1
    Posterior mean: alpha / (alpha + beta)
    Shrink position when credible interval includes breakeven

Also includes:
- Posterior credible interval computation
- Probability of positive edge
- Position shrinkage based on Bayesian uncertainty
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class BayesianState:
    """Current Bayesian belief state about win rate."""
    alpha: float        # Prior + wins
    beta: float         # Prior + losses
    n_wins: int
    n_losses: int
    total_trades: int

    @property
    def posterior_mean(self) -> float:
        """Posterior mean of win rate: alpha / (alpha + beta)."""
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.5

    @property
    def posterior_variance(self) -> float:
        """Posterior variance: alpha*beta / ((alpha+beta)^2 * (alpha+beta+1))"""
        total = self.alpha + self.beta
        if total <= 0 or total + 1 <= 0:
            return 0.0
        return (self.alpha * self.beta) / (total ** 2 * (total + 1))

    def credible_interval(self, mass: float = 0.95) -> Tuple[float, float]:
        """Highest density interval for the posterior."""
        try:
            from scipy import stats
            lower_q = (1.0 - mass) / 2.0
            upper_q = 1.0 - lower_q
            lo = stats.beta.ppf(lower_q, self.alpha, self.beta)
            hi = stats.beta.ppf(upper_q, self.alpha, self.beta)
            return float(lo), float(hi)
        except ImportError:
            # Fallback: normal approximation
            mean = self.posterior_mean
            std = math.sqrt(self.posterior_variance)
            z = 1.96 if mass >= 0.95 else 1.645
            return max(0.0, mean - z * std), min(1.0, mean + z * std)

    def probability_positive_edge(self, breakeven: float = 0.5) -> float:
        """P(p > breakeven) using the posterior Beta distribution."""
        try:
            from scipy import stats
            return float(1.0 - stats.beta.cdf(breakeven, self.alpha, self.beta))
        except ImportError:
            # Normal approximation
            mean = self.posterior_mean
            std = math.sqrt(self.posterior_variance)
            if std <= 0:
                return 1.0 if mean > breakeven else 0.0
            z = (mean - breakeven) / std
            return float(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))

    def to_dict(self) -> Dict[str, Any]:
        ci_lo, ci_hi = self.credible_interval()
        return {
            "alpha": round(self.alpha, 2),
            "beta": round(self.beta, 2),
            "posterior_mean": round(self.posterior_mean, 4),
            "posterior_std": round(math.sqrt(self.posterior_variance), 4),
            "credible_interval_95": [round(ci_lo, 4), round(ci_hi, 4)],
            "prob_positive_edge": round(self.probability_positive_edge(), 4),
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "total_trades": self.total_trades,
        }


class BayesianWinRateUpdater:
    """Online Bayesian win rate estimator with position shrinkage.

    Implements Part2 §6.8:
        Prior: p ~ Beta(alpha, beta)
        After each trade, update: alpha += wins, beta += losses
        Posterior mean: alpha / (alpha + beta)
        Shrink position when credible interval includes breakeven

    Typical usage:
        updater = BayesianWinRateUpdater()
        for trade in trade_history:
            updater.update(trade.is_win)
        shrink = updater.get_position_shrinkage()
    """

    def __init__(
        self,
        prior_alpha: float = 1.0,
        prior_beta: float = 1.0,
        breakeven: float = 0.5,
        shrink_threshold: float = 0.60,
    ):
        """
        Args:
            prior_alpha: Prior alpha parameter (default 1.0 = uniform prior).
            prior_beta: Prior beta parameter (default 1.0 = uniform prior).
            breakeven: Win rate considered breakeven (default 0.5).
            shrink_threshold: P(edge) below this → shrink position (default 0.60).
        """
        self.prior_alpha = max(0.1, float(prior_alpha))
        self.prior_beta = max(0.1, float(prior_beta))
        self.breakeven = float(breakeven)
        self.shrink_threshold = float(shrink_threshold)
        self.state = BayesianState(
            alpha=self.prior_alpha,
            beta=self.prior_beta,
            n_wins=0,
            n_losses=0,
            total_trades=0,
        )

    def update(self, win: bool) -> BayesianState:
        """Update posterior with a new trade outcome.

        Args:
            win: True if trade was a winner.

        Returns:
            Updated BayesianState.
        """
        if win:
            self.state.alpha += 1.0
            self.state.n_wins += 1
        else:
            self.state.beta += 1.0
            self.state.n_losses += 1
        self.state.total_trades += 1
        return self.state

    def update_batch(self, outcomes: List[bool]) -> BayesianState:
        """Update with a batch of trade outcomes."""
        for win in outcomes:
            self.update(win)
        return self.state

    def get_position_shrinkage(self) -> float:
        """Returns position size multiplier based on Bayesian uncertainty.

        When credible interval includes breakeven → shrink toward 0.
        Returns value in [0, 1] where 1.0 = full size, 0.0 = no trade.
        """
        prob_edge = self.state.probability_positive_edge(self.breakeven)

        if prob_edge >= self.shrink_threshold:
            # High confidence in edge → full size
            return 1.0
        elif prob_edge >= 0.5:
            # Some edge but uncertain → proportional shrinkage
            return max(0.0, (prob_edge - 0.5) / (self.shrink_threshold - 0.5))
        else:
            # Less than 50% chance of edge → no trade
            return 0.0

    def get_edge_confidence(self) -> Dict[str, Any]:
        """Comprehensive edge confidence assessment."""
        state = self.state
        ci_lo, ci_hi = state.credible_interval()
        prob_edge = state.probability_positive_edge(self.breakeven)

        if prob_edge >= 0.90:
            confidence = "HIGH"
        elif prob_edge >= 0.70:
            confidence = "MODERATE"
        elif prob_edge >= 0.50:
            confidence = "LOW"
        else:
            confidence = "NO_EDGE"

        return {
            "posterior_mean": state.posterior_mean,
            "credible_interval": [ci_lo, ci_hi],
            "probability_positive_edge": prob_edge,
            "position_shrinkage": self.get_position_shrinkage(),
            "confidence_level": confidence,
            "n_trades": state.total_trades,
            "observed_win_rate": state.n_wins / max(1, state.total_trades),
        }

    def reset(self) -> None:
        """Reset to prior beliefs."""
        self.state = BayesianState(
            alpha=self.prior_alpha,
            beta=self.prior_beta,
            n_wins=0,
            n_losses=0,
            total_trades=0,
        )


# Canonical aliases
BayesianBetaUpdater = BayesianWinRateUpdater
OnlineWinRateBayesian = BayesianWinRateUpdater
