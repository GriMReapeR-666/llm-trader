"""CV@R (Conditional Value at Risk) Risk Module.

Implements tail-risk based portfolio allocation from the MNTO paper.
CV@R is used instead of standard deviation to avoid normality assumptions.

Key insight: CV@R = E[Loss | Loss > VaR]
  - Captures the expected loss in the worst cases
  - More robust to fat tails than variance-based measures
  - Used for position sizing and portfolio construction

References:
  - MNTO Paper (Mbimi, 2011): Section 4.5 Risk Control and Money Management
  - Rockafellar & Uryasev (2000): Optimization of Conditional Value-at-Risk
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CVaRResult:
    """Result of CV@R computation."""
    var: float  # Value at Risk
    cvar: float  # Conditional Value at Risk
    confidence: float  # Confidence level
    tail_returns: np.ndarray  # Returns in the tail
    tail_mean: float  # Mean of tail returns
    tail_std: float  # Std of tail returns
    n_tail_obs: int  # Number of tail observations

    def to_dict(self) -> Dict[str, Any]:
        return {
            "var": round(self.var, 6),
            "cvar": round(self.cvar, 6),
            "confidence": self.confidence,
            "tail_mean": round(self.tail_mean, 6),
            "tail_std": round(self.tail_std, 6),
            "n_tail_obs": self.n_tail_obs,
        }


@dataclass
class PositionSizing:
    """Position sizing result based on CV@R."""
    positions: Dict[str, float]  # symbol -> weight
    portfolio_cvar: float
    total_risk_budget: float
    risk_used: float
    risk_remaining: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "positions": {k: round(v, 4) for k, v in self.positions.items()},
            "portfolio_cvar": round(self.portfolio_cvar, 6),
            "total_risk_budget": round(self.total_risk_budget, 6),
            "risk_used": round(self.risk_used, 6),
            "risk_remaining": round(self.risk_remaining, 6),
        }


class CVaRRiskManager:
    """CV@R-based risk management.

    Uses historical simulation for VaR and CV@R estimation.
    Supports both single-asset and portfolio-level analysis.
    """

    def __init__(
        self,
        confidence: float = 0.99,
        lookback: int = 252,
        min_observations: int = 60,
        max_cvar_pct: float = 0.01,
    ):
        self.confidence = confidence
        self.lookback = lookback
        self.min_observations = min_observations
        self.max_cvar_pct = max_cvar_pct

    def compute_var(self, returns: np.ndarray, confidence: Optional[float] = None) -> float:
        """Compute Value at Risk (historical simulation).

        Args:
            returns: Array of historical returns
            confidence: Confidence level (default: self.confidence)

        Returns:
            VaR (positive number representing potential loss)
        """
        conf = confidence or self.confidence
        if len(returns) < self.min_observations:
            return 0.0

        # Historical VaR
        sorted_returns = np.sort(returns)
        idx = int((1 - conf) * len(sorted_returns))
        idx = max(0, min(idx, len(sorted_returns) - 1))

        return float(-sorted_returns[idx])

    def compute_cvar(
        self, returns: np.ndarray, confidence: Optional[float] = None
    ) -> CVaRResult:
        """Compute Conditional Value at Risk.

        CV@R = E[Loss | Loss > VaR]
        """
        conf = confidence or self.confidence
        if len(returns) < self.min_observations:
            return CVaRResult(
                var=0.0, cvar=0.0, confidence=conf,
                tail_returns=np.array([]), tail_mean=0.0, tail_std=0.0, n_tail_obs=0,
            )

        # VaR
        var = self.compute_var(returns, conf)

        # Tail returns (losses beyond VaR)
        tail_returns = returns[returns <= -var]

        if len(tail_returns) == 0:
            return CVaRResult(
                var=var, cvar=var, confidence=conf,
                tail_returns=np.array([]), tail_mean=-var, tail_std=0.0, n_tail_obs=0,
            )

        # CV@R is the mean of tail losses
        cvar = float(-np.mean(tail_returns))
        tail_mean = float(np.mean(tail_returns))
        tail_std = float(np.std(tail_returns)) if len(tail_returns) > 1 else 0.0

        return CVaRResult(
            var=var, cvar=cvar, confidence=conf,
            tail_returns=tail_returns, tail_mean=tail_mean,
            tail_std=tail_std, n_tail_obs=len(tail_returns),
        )

    def compute_rolling_cvar(
        self, returns: pd.Series, window: int = 60
    ) -> pd.DataFrame:
        """Compute rolling CV@R and VaR."""
        results = []
        for i in range(window, len(returns)):
            window_returns = returns.iloc[i - window:i].values
            cvar_result = self.compute_cvar(window_returns)
            results.append({
                "date": returns.index[i],
                "var": cvar_result.var,
                "cvar": cvar_result.cvar,
                "confidence": self.confidence,
            })

        return pd.DataFrame(results).set_index("date")

    def cvar_position_sizing(
        self,
        portfolio_value: float,
        max_cvar_pct: Optional[float] = None,
        positions: Optional[Dict[str, float]] = None,
        asset_returns: Optional[Dict[str, np.ndarray]] = None,
    ) -> PositionSizing:
        """Position sizing based on CV@R budget.

        Args:
            portfolio_value: Total portfolio value
            max_cvar_pct: Maximum CV@R as % of portfolio (default: self.max_cvar_pct)
            positions: Current position weights {symbol: weight}
            asset_returns: Historical returns per asset

        Returns:
            PositionSizing with adjusted weights
        """
        max_cvar = max_cvar_pct or self.max_cvar_pct
        risk_budget = portfolio_value * max_cvar

        if positions is None or asset_returns is None:
            return PositionSizing(
                positions=positions or {},
                portfolio_cvar=0.0,
                total_risk_budget=risk_budget,
                risk_used=0.0,
                risk_remaining=risk_budget,
            )

        # Compute portfolio CV@R via simulation
        portfolio_cvar = self._simulate_portfolio_cvar(
            positions, asset_returns, portfolio_value
        )

        # Scale positions if over budget
        if portfolio_cvar > risk_budget and portfolio_cvar > 0:
            scale_factor = risk_budget / portfolio_cvar
            adjusted = {k: v * scale_factor for k, v in positions.items()}
        else:
            adjusted = positions.copy()

        return PositionSizing(
            positions=adjusted,
            portfolio_cvar=portfolio_cvar,
            total_risk_budget=risk_budget,
            risk_used=portfolio_cvar,
            risk_remaining=risk_budget - portfolio_cvar,
        )

    def tail_risk_adjusted_weights(
        self,
        asset_returns: Dict[str, np.ndarray],
        target_cvar: Optional[float] = None,
    ) -> Dict[str, float]:
        """Compute weights that achieve target CV@R.

        Uses inverse-CVaR weighting as starting point.
        """
        target = target_cvar or self.max_cvar_pct
        n_assets = len(asset_returns)

        if n_assets == 0:
            return {}

        # Compute individual CV@Rs
        cvars = {}
        for symbol, returns in asset_returns.items():
            result = self.compute_cvar(returns)
            cvars[symbol] = max(result.cvar, 1e-10)

        # Inverse-CVaR weighting
        inv_cvars = {k: 1.0 / v for k, v in cvars.items()}
        total_inv = sum(inv_cvars.values())

        weights = {k: v / total_inv for k, v in inv_cvars.items()}

        # Scale to target
        portfolio_cvar = self._simulate_portfolio_cvar(
            weights, asset_returns, 1.0
        )
        if portfolio_cvar > 0:
            scale = target / portfolio_cvar
            weights = {k: v * scale for k, v in weights.items()}

        # Ensure no negative weights (long-only)
        weights = {k: max(v, 0) for k, v in weights.items()}
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    def _simulate_portfolio_cvar(
        self,
        weights: Dict[str, float],
        asset_returns: Dict[str, np.ndarray],
        portfolio_value: float,
        n_simulations: int = 1000,
    ) -> float:
        """Simulate portfolio CV@R via Monte Carlo."""
        symbols = list(weights.keys())
        if not symbols:
            return 0.0

        # Get common length
        min_len = min(len(asset_returns.get(s, np.array([0]))) for s in symbols)
        if min_len < self.min_observations:
            return 0.0

        # Build return matrix
        return_matrix = np.column_stack([
            asset_returns[s][-min_len:] for s in symbols
        ])
        weight_vector = np.array([weights.get(s, 0) for s in symbols])

        # Historical simulation
        portfolio_returns = return_matrix @ weight_vector

        # VaR and CV@R
        sorted_returns = np.sort(portfolio_returns)
        idx = int((1 - self.confidence) * len(sorted_returns))
        idx = max(0, min(idx, len(sorted_returns) - 1))

        var = -sorted_returns[idx]
        tail = sorted_returns[:idx + 1]
        cvar = float(-np.mean(tail)) if len(tail) > 0 else var

        return cvar * portfolio_value

    def compute_stress_cvar(
        self, returns: np.ndarray, stress_scenarios: Optional[List[float]] = None
    ) -> Dict[str, float]:
        """Compute CV@R under stress scenarios."""
        if stress_scenarios is None:
            stress_scenarios = [-0.02, -0.05, -0.10, -0.20]

        results = {}
        for scenario in stress_scenarios:
            # Add scenario to returns
            stressed = np.append(returns, scenario)
            cvar_result = self.compute_cvar(stressed)
            results[f"stress_{abs(scenario):.0%}"] = cvar_result.cvar

        return results
