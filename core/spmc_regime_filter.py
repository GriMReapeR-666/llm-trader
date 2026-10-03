"""Semi-Parametric Markov Chain (SPMC) Strategy-Edge Regime Filter.

Based on the empirical econometrics of:
Tolusic, M. (2026). "Semi-Parametric Markov Chain Analysis of Strategy
Performance Regimes in Algorithmic Forex Trading". 17th International Odyssey
Conference on Economics and Business, FEB Zagreb.

Key Insights:
1. Strategy performance regimes are EMPIRICALLY ORTHOGONAL to market price
   volatility regimes (r ~ 0.026-0.037). Market state != strategy edge state.
2. Performance persistence exhibits near-perfect symmetry:
   P(G -> G) ~ 0.793, P(B -> B) ~ 0.794, with natural duration ~4.8 trades.
3. 2nd-Order Markov transitions provide a sharp gradient:
   P(G | GG) = 0.849, P(G | BG) = 0.449, P(G | GB) = 0.321, P(G | BB) = 0.143.
4. Optimal recursive Bayesian filter beta_t determines the posterior probability
   of being in the Good state, driving adaptive sizing (100% in G, 25% in B).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class SPMCTransitionMatrix:
    """First-order and second-order transition probabilities."""
    # First-order transitions
    p_gg: float = 0.793
    p_gb: float = 0.207
    p_bg: float = 0.206
    p_bb: float = 0.794
    # Second-order transitions: P(next = G | history)
    p_g_given_gg: float = 0.849
    p_g_given_bg: float = 0.449
    p_g_given_gb: float = 0.321
    p_g_given_bb: float = 0.143

    @property
    def stationary_g(self) -> float:
        """Stationary probability of Good state pi_G."""
        denom = self.p_gb + self.p_bg
        return self.p_bg / denom if denom > 0 else 0.5

    @property
    def stationary_b(self) -> float:
        """Stationary probability of Bad state pi_B."""
        denom = self.p_gb + self.p_bg
        return self.p_gb / denom if denom > 0 else 0.5

    @property
    def expected_duration_g(self) -> float:
        """Expected duration in Good state E[D_G] = 1 / p_GB."""
        return 1.0 / self.p_gb if self.p_gb > 0 else float("inf")

    @property
    def expected_duration_b(self) -> float:
        """Expected duration in Bad state E[D_B] = 1 / p_BG."""
        return 1.0 / self.p_bg if self.p_bg > 0 else float("inf")


@dataclass
class SPMCStateSnapshot:
    """Point-in-time assessment of strategy performance regime."""
    trade_count: int
    posterior_beta: float
    current_regime: str  # "GOOD", "BAD", "STANDBY"
    history_state: str   # "GG", "GB", "BG", "BB", or "INIT"
    sizing_multiplier: float  # 1.0 for Good, 0.25 for Bad, 0.0 for Deep Bad
    rolling_k_mean: float
    is_edge_active: bool
    empirical_p_next_g: float


class SPMCRegimeFilter:
    """Semi-Parametric Markov Chain & Recursive Bayesian Performance Filter.

    Tracks a strategy's trade outcome sequence (in pips or R-multiples),
    infers the latent strategy performance regime, and computes optimal
    Bayesian posteriors and position sizing multipliers.
    """

    def __init__(
        self,
        rolling_window_k: int = 5,
        threshold_tau: float = 0.50,
        bad_regime_multiplier: float = 0.25,
        matrix: Optional[SPMCTransitionMatrix] = None,
        use_second_order: bool = True,
    ):
        self.k = max(2, rolling_window_k)
        self.threshold_tau = threshold_tau
        self.bad_regime_multiplier = bad_regime_multiplier
        self.matrix = matrix or SPMCTransitionMatrix()
        self.use_second_order = use_second_order

        # State history
        self._trade_outcomes: List[float] = []
        self._discrete_states: List[str] = []  # "G" or "B"
        self._posterior_beta: float = self.matrix.stationary_g

        # Empirical likelihood parameters
        self._good_outcomes: List[float] = []
        self._bad_outcomes: List[float] = []

    @property
    def trade_count(self) -> int:
        return len(self._trade_outcomes)

    @property
    def posterior_beta(self) -> float:
        return self._posterior_beta

    def _discretize_state(self, recent_outcomes: Sequence[float]) -> str:
        """Discretize performance into Good (G) or Bad (B) based on rolling k mean."""
        if not recent_outcomes:
            return "G"
        mean_pips = sum(recent_outcomes) / len(recent_outcomes)
        return "G" if mean_pips > 0 else "B"

    def _empirical_likelihood(self, outcome: float, state: str) -> float:
        """Semi-parametric empirical likelihood f(r_t | S)."""
        samples = self._good_outcomes if state == "G" else self._bad_outcomes
        if len(samples) < 5:
            # Cold-start fallback: gentle Gaussian likelihood
            mu = 15.0 if state == "G" else -15.0
            sigma = 30.0
            diff = (outcome - mu) / sigma
            return max(1e-4, (1.0 / (sigma * math.sqrt(2 * math.pi))) * math.exp(-0.5 * diff * diff))

        # Non-parametric kernel/bandwidth estimate
        h = 15.0  # Bandwidth in pips
        kernel_sum = 0.0
        for s in samples:
            u = (outcome - s) / h
            if abs(u) <= 3.0:
                kernel_sum += math.exp(-0.5 * u * u)
        density = kernel_sum / (len(samples) * h * math.sqrt(2 * math.pi))
        return max(1e-4, density)

    def update(self, net_pips: float) -> SPMCStateSnapshot:
        """Record trade outcome and update Bayesian posterior beta_t."""
        self._trade_outcomes.append(net_pips)

        # 1. Determine discrete state for the trade
        window = self._trade_outcomes[-self.k:]
        state = self._discretize_state(window)
        self._discrete_states.append(state)

        # Record outcome in state distribution
        if state == "G":
            self._good_outcomes.append(net_pips)
        else:
            self._bad_outcomes.append(net_pips)

        # 2. Compute 2nd-order history state
        if len(self._discrete_states) >= 2:
            history_state = f"{self._discrete_states[-2]}{self._discrete_states[-1]}"
        elif len(self._discrete_states) == 1:
            history_state = f"{self._discrete_states[-1]}{self._discrete_states[-1]}"
        else:
            history_state = "INIT"

        # 3. Prediction Step (Prior Propagation)
        if self.use_second_order and history_state in ("GG", "GB", "BG", "BB"):
            # Condition prediction on 2nd-order empirical probability
            if history_state == "GG":
                p_next_g = self.matrix.p_g_given_gg
            elif history_state == "BG":
                p_next_g = self.matrix.p_g_given_bg
            elif history_state == "GB":
                p_next_g = self.matrix.p_g_given_gb
            else:  # "BB"
                p_next_g = self.matrix.p_g_given_bb
            beta_prior = p_next_g * self._posterior_beta + (1.0 - p_next_g) * (1.0 - self._posterior_beta)
        else:
            # First-order Chapman-Kolmogorov prior: beta_{t|t-1} = p_GG * beta_{t-1} + p_BG * (1 - beta_{t-1})
            beta_prior = self.matrix.p_gg * self._posterior_beta + self.matrix.p_bg * (1.0 - self._posterior_beta)
            p_next_g = self.matrix.p_gg if state == "G" else self.matrix.p_bg

        # 4. Bayesian Update Step with Empirical Likelihood
        f_g = self._empirical_likelihood(net_pips, "G")
        f_b = self._empirical_likelihood(net_pips, "B")

        num = f_g * beta_prior
        denom = num + f_b * (1.0 - beta_prior)
        if denom > 1e-12:
            self._posterior_beta = max(0.01, min(0.99, num / denom))
        else:
            self._posterior_beta = beta_prior

        # 5. Position Sizing Multiplier
        is_good = self._posterior_beta >= self.threshold_tau
        if self._posterior_beta >= self.threshold_tau:
            current_regime = "GOOD"
            sizing_multiplier = 1.0
        elif self._posterior_beta >= 0.20:
            current_regime = "BAD"
            sizing_multiplier = self.bad_regime_multiplier  # default 0.25 (quarter-size)
        else:
            current_regime = "STANDBY"
            sizing_multiplier = 0.0  # complete pause until edge recovers

        rolling_k_mean = sum(window) / len(window)

        return SPMCStateSnapshot(
            trade_count=self.trade_count,
            posterior_beta=round(self._posterior_beta, 4),
            current_regime=current_regime,
            history_state=history_state,
            sizing_multiplier=sizing_multiplier,
            rolling_k_mean=round(rolling_k_mean, 2),
            is_edge_active=is_good,
            empirical_p_next_g=round(p_next_g, 4),
        )

    def current_state(self) -> SPMCStateSnapshot:
        """Get the current snapshot without advancing state."""
        if not self._trade_outcomes:
            return SPMCStateSnapshot(
                trade_count=0,
                posterior_beta=round(self.matrix.stationary_g, 4),
                current_regime="GOOD",
                history_state="INIT",
                sizing_multiplier=1.0,
                rolling_k_mean=0.0,
                is_edge_active=True,
                empirical_p_next_g=round(self.matrix.p_gg, 4),
            )
        window = self._trade_outcomes[-self.k:]
        rolling_k_mean = sum(window) / len(window)
        history_state = f"{self._discrete_states[-2]}{self._discrete_states[-1]}" if len(self._discrete_states) >= 2 else "INIT"
        
        if self._posterior_beta >= self.threshold_tau:
            regime = "GOOD"
            mult = 1.0
        elif self._posterior_beta >= 0.20:
            regime = "BAD"
            mult = self.bad_regime_multiplier
        else:
            regime = "STANDBY"
            mult = 0.0

        p_next_g = self.matrix.p_g_given_gg if history_state == "GG" else (
            self.matrix.p_g_given_bg if history_state == "BG" else (
                self.matrix.p_g_given_gb if history_state == "GB" else self.matrix.p_g_given_bb
            )
        )

        return SPMCStateSnapshot(
            trade_count=self.trade_count,
            posterior_beta=round(self._posterior_beta, 4),
            current_regime=regime,
            history_state=history_state,
            sizing_multiplier=mult,
            rolling_k_mean=round(rolling_k_mean, 2),
            is_edge_active=(regime == "GOOD"),
            empirical_p_next_g=round(p_next_g, 4),
        )

    @property
    def current_regime(self) -> str:
        """Current regime string: 'GOOD', 'BAD', or 'STANDBY'."""
        return self.current_state().current_regime

    def get_sizing_multiplier(self) -> float:
        """Current sizing multiplier (1.0 for GOOD, 0.25 for BAD, 0.0 for STANDBY)."""
        return self.current_state().sizing_multiplier

    def observe_trade(self, outcome: Any) -> SPMCStateSnapshot:
        """Record trade outcome from boolean or numeric pips/PnL."""
        if isinstance(outcome, bool):
            pips = 10.0 if outcome else -10.0
        else:
            try:
                pips = float(outcome)
            except (TypeError, ValueError):
                pips = -10.0
        return self.update(pips)

