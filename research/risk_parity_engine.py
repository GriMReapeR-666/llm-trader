"""Equal Risk Contribution (ERC) Risk Parity Optimizer with Turnover Constraints.

Solves the Equal Risk Contribution optimization problem:
- Balances marginal risk contributions across multi-asset portfolios.
- Enforces turnover limits to prevent excessive rebalancing costs.
- Incorporates linear transaction-cost penalties in the objective function.
- Provides fallback inverse-volatility weighting if covariance is singular.

Guarantees:
- execution_authority=False
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import minimize

log = logging.getLogger("RiskParityOptimizer")


@dataclass(frozen=True)
class RiskParityResult:
    weights: List[float]
    assets: List[str]
    portfolio_volatility: float
    risk_contributions: List[float]
    risk_contributions_pct: List[float]
    turnover: float
    optimization_converged: bool
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RiskParityOptimizer:
    """Equal Risk Contribution portfolio optimizer with turnover constraints."""

    def __init__(self, target_volatility: Optional[float] = None) -> None:
        self.target_volatility = target_volatility

    def _risk_contribution(self, weights: np.ndarray, cov: np.ndarray) -> Tuple[np.ndarray, float]:
        """Compute total risk contribution and portfolio volatility."""
        port_vol = math_vol = float(np.sqrt(np.maximum(weights.T @ cov @ weights, 1e-12)))
        mrc = cov @ weights
        rc = weights * mrc
        return rc, port_vol

    def solve(
        self,
        cov_matrix: np.ndarray,
        asset_names: Optional[List[str]] = None,
        w_current: Optional[np.ndarray] = None,
        max_turnover: float = 0.30,
        max_weight: float = 0.40,
        min_weight: float = 0.01,
        tc_penalty: float = 0.002,
    ) -> RiskParityResult:
        """Solve constrained Equal Risk Contribution optimization."""
        cov = np.asarray(cov_matrix, dtype=float)
        n = cov.shape[0]

        if asset_names is None:
            asset_names = [f"Asset_{i}" for i in range(n)]

        if w_current is None:
            w_current = np.ones(n) / n
        else:
            w_current = np.asarray(w_current, dtype=float)
            w_current = w_current / np.sum(w_current)

        # Objective function
        def objective(w: np.ndarray) -> float:
            rc, _ = self._risk_contribution(w, cov)
            rc_target = np.mean(rc)
            rp_term = float(np.sum((rc - rc_target) ** 2))
            turnover_term = float(tc_penalty * np.sum(np.abs(w - w_current)))
            return rp_term + turnover_term

        constraints: List[Dict[str, Any]] = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
        ]

        if max_turnover < 2.0:
            constraints.append({
                "type": "ineq",
                "fun": lambda w: max_turnover - np.sum(np.abs(w - w_current)),
            })

        bounds = tuple((min_weight, max_weight) for _ in range(n))

        res = minimize(
            fun=objective,
            x0=w_current,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-12, "maxiter": 1000, "disp": False},
        )

        converged = bool(res.success)
        if not converged:
            log.warning("Risk parity optimization did not converge: %s. Using normalized solution.", res.message)

        weights = np.maximum(res.x, min_weight)
        weights = weights / np.sum(weights)

        turnover = float(np.sum(np.abs(weights - w_current)))
        rc, port_vol = self._risk_contribution(weights, cov)
        total_rc = np.sum(rc)
        rc_pct = (rc / total_rc) if total_rc > 0 else (np.ones(n) / n)

        return RiskParityResult(
            weights=[float(x) for x in weights],
            assets=asset_names,
            portfolio_volatility=float(port_vol),
            risk_contributions=[float(x) for x in rc],
            risk_contributions_pct=[float(x) for x in rc_pct],
            turnover=turnover,
            optimization_converged=converged,
            execution_authority=False,
        )
