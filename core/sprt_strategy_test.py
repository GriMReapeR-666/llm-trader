"""Sequential Probability Ratio Test (SPRT) for Strategy Kill Decisions.

Implements Wald's SPRT (Part2 §6.9) for real-time strategy edge validation:
    H0: strategy has edge (p = p0)
    H1: no edge (p = p1)
    LR = prod [p1^x_i * (1-p1)^{1-x_i}] / [p0^x_i * (1-p0)^{1-x_i}]
    If LR > (1-beta)/alpha → accept H1 (kill strategy)
    If LR < beta/(1-alpha) → accept H0 (continue)

Also includes cumulative log-likelihood ratio formulation for numerical stability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SPRTResult:
    """Result of a single SPRT evaluation."""
    log_likelihood_ratio: float
    decision: str           # "CONTINUE", "KILL", "INCONCLUSIVE"
    n_observations: int
    expected_alpha: float
    expected_beta: float
    p0: float
    p1: float
    upper_bound: float      # ln((1-beta)/alpha)
    lower_bound: float      # ln(beta/(1-alpha))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "log_likelihood_ratio": round(self.log_likelihood_ratio, 4),
            "decision": self.decision,
            "n_observations": self.n_observations,
            "p0": self.p0,
            "p1": self.p1,
            "upper_bound_kill": round(self.upper_bound, 4),
            "lower_bound_continue": round(self.lower_bound, 4),
        }


class SPRTStrategyTest:
    """Sequential Probability Ratio Test for strategy edge validation.

    Tracks cumulative log-likelihood ratio across trade outcomes and makes
    CONTINUE / KILL / INCONCLUSIVE decisions based on Wald boundaries.

    Typical usage:
        sprt = SPRTStrategyTest(p0=0.55, p1=0.50)
        for outcome in trade_outcomes:  # 1 = win, 0 = loss
            result = sprt.update(outcome)
            if result.decision == "KILL":
                halt_strategy()
    """

    def __init__(
        self,
        p0: float = 0.55,
        p1: float = 0.50,
        alpha: float = 0.05,
        beta: float = 0.10,
        max_observations: int = 500,
    ):
        """
        Args:
            p0: Win rate under H0 (strategy has edge). Default 55%.
            p1: Win rate under H1 (no edge / breakeven). Default 50%.
            alpha: Type I error probability (false kill). Default 5%.
            beta: Type II error probability (false continue). Default 10%.
            max_observations: Force decision after this many trades.
        """
        self.p0 = max(0.01, min(0.99, float(p0)))
        self.p1 = max(0.01, min(0.99, float(p1)))
        self.alpha = max(0.001, min(0.20, float(alpha)))
        self.beta = max(0.001, min(0.20, float(beta)))
        self.max_observations = int(max_observations)

        # Wald boundaries (log-scale)
        self.upper_bound = math.log((1.0 - self.beta) / self.alpha)
        self.lower_bound = math.log(self.beta / (1.0 - self.alpha))

        # State
        self.log_lr: float = 0.0
        self.n_observations: int = 0

    def update(self, win: bool) -> SPRTResult:
        """Process a single trade outcome and return the current SPRT decision.

        Args:
            win: True if trade was a winner, False otherwise.

        Returns:
            SPRTResult with decision: CONTINUE, KILL, or INCONCLUSIVE.
        """
        x = 1.0 if win else 0.0

        # Log-likelihood ratio increment:
        # ln( p1^x * (1-p1)^(1-x) / (p0^x * (1-p0)^(1-x)) )
        if x > 0.5:
            self.log_lr += math.log(self.p1 / self.p0)
        else:
            self.log_lr += math.log((1.0 - self.p1) / (1.0 - self.p0))

        self.n_observations += 1

        # Decision
        if self.log_lr >= self.upper_bound:
            decision = "KILL"
        elif self.log_lr <= self.lower_bound:
            decision = "CONTINUE"
        elif self.n_observations >= self.max_observations:
            # Force decision: if LR is closer to upper, kill; else continue
            if self.log_lr > 0:
                decision = "KILL"
            else:
                decision = "CONTINUE"
        else:
            decision = "INCONCLUSIVE"

        return SPRTResult(
            log_likelihood_ratio=self.log_lr,
            decision=decision,
            n_observations=self.n_observations,
            expected_alpha=self.alpha,
            expected_beta=self.beta,
            p0=self.p0,
            p1=self.p1,
            upper_bound=self.upper_bound,
            lower_bound=self.lower_bound,
        )

    def update_batch(self, outcomes: List[bool]) -> SPRTResult:
        """Process a batch of trade outcomes sequentially."""
        result = None
        for win in outcomes:
            result = self.update(win)
        return result or SPRTResult(
            log_likelihood_ratio=0.0, decision="INCONCLUSIVE",
            n_observations=0, expected_alpha=self.alpha,
            expected_beta=self.beta, p0=self.p0, p1=self.p1,
            upper_bound=self.upper_bound, lower_bound=self.lower_bound,
        )

    def reset(self) -> None:
        """Reset the test state for a new evaluation cycle."""
        self.log_lr = 0.0
        self.n_observations = 0

    def get_status(self) -> Dict[str, Any]:
        """Current test status without making a decision."""
        return {
            "log_lr": round(self.log_lr, 4),
            "n_observations": self.n_observations,
            "upper_bound": round(self.upper_bound, 4),
            "lower_bound": round(self.lower_bound, 4),
            "distance_to_kill": round(self.upper_bound - self.log_lr, 4),
            "distance_to_continue": round(self.log_lr - self.lower_bound, 4),
            "implied_win_rate": round(
                self._implied_win_rate(), 4
            ) if self.n_observations > 0 else None,
        }

    def _implied_win_rate(self) -> float:
        """Estimated win rate from observed outcomes."""
        if self.n_observations == 0:
            return 0.0
        # Reconstruct from log_lr
        # log_lr = n_wins * ln(p1/p0) + n_losses * ln((1-p1)/(1-p0))
        # We need to solve for n_wins from the cumulative LR
        ln_ratio_win = math.log(self.p1 / self.p0)
        ln_ratio_loss = math.log((1.0 - self.p1) / (1.0 - self.p0))
        if abs(ln_ratio_win - ln_ratio_loss) < 1e-10:
            return 0.5
        n_wins = (self.log_lr - self.n_observations * ln_ratio_loss) / (ln_ratio_win - ln_ratio_loss)
        return max(0.0, min(1.0, n_wins / self.n_observations))


# Canonical aliases
WaldSPRT = SPRTStrategyTest
SequentialProbabilityRatioTest = SPRTStrategyTest
