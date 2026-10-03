"""
Portfolio Management Modules
=============================
Implements portfolio construction, risk budgeting, and allocation tools.
"""

import numpy as np
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform
from typing import Dict, List, Optional

from quant_framework.modules.interface import quant_module, quant_tool, QuantModule, registry


def _cov_to_corr(cov: np.ndarray) -> np.ndarray:
    """Covariance -> correlation, robust to zero-variance assets."""
    std = np.sqrt(np.diag(cov))
    safe_std = np.where(std > 0, std, 1.0)
    corr = cov / np.outer(safe_std, safe_std)
    corr[~np.isfinite(corr)] = 0.0
    np.fill_diagonal(corr, 1.0)
    return np.clip(corr, -1.0, 1.0)


@quant_module(
    id="portfolio_management.kelly",
    name="Kelly Criterion Position Sizer",
    version="1.1.0",
    category="portfolio_management",
    tags=["kelly", "position sizing", "fractional kelly", "risk management"],
)
class KellyCriterion(QuantModule):
    """
    Calculates optimal position size using Kelly Criterion, plus a
    theoretically grounded drawdown-risk estimate.

    Drawdown risk follows Thorp (2006), "The Kelly Criterion in Blackjack,
    Sports Betting and the Stock Market": when betting f x (full Kelly), the
    probability that wealth EVER falls below a fraction x of its starting
    value is

        P(ever below x) = x ** (2/f - 1)      (continuous approximation)

    e.g. full Kelly (f=1) gives a 50% chance of ever being down 50%;
    quarter Kelly (f=0.25) gives 0.5**7 ~ 0.8%.  v1.x shipped an invented
    0.80*sqrt(fraction) "max_drawdown_estimate" here instead; that field is
    replaced by `prob_ever_half` (x = 0.5).
    """

    @quant_tool(
        module_id="portfolio_management.kelly",
        name="portfolio_management.kelly.calculate",
        description="Calculate optimal Kelly Criterion position size and drawdown risk (Thorp 2006)",
        parameters={
            "win_rate": {"type": "float", "description": "Probability of winning (0-1)"},
            "win_loss_ratio": {"type": "float", "description": "Average win / average loss"},
            "fraction": {"type": "float", "description": "Multiple of full Kelly to apply (0-1 typical)", "default": 0.25},
        },
        returns={
            "kelly_fraction": {"type": "float"},
            "applied_fraction": {"type": "float"},
            "prob_ever_half": {"type": "float", "description": "Probability of ever enduring a >=50% drawdown at this Kelly multiple"},
        },
    )
    def calculate(self, win_rate: float, win_loss_ratio: float, fraction: float = 0.25) -> Dict:
        if win_rate <= 0 or win_rate >= 1 or win_loss_ratio <= 0:
            return {"error": "Invalid parameters: win_rate must be (0,1), win_loss_ratio must be > 0"}
        if fraction < 0:
            return {"error": "fraction must be >= 0"}
        kelly = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
        applied = kelly * fraction
        if kelly <= 0:
            # No positive edge: over a long horizon a deep drawdown is
            # effectively certain, whatever multiple is (mis)applied.
            return {
                "kelly_fraction": float(kelly),
                "applied_fraction": float(applied),
                "prob_ever_half": 1.0,
                "warning": "no positive edge (kelly_fraction <= 0)",
            }
        if fraction == 0:
            prob_ever_half = 0.0  # nothing risked
        else:
            prob_ever_half = float(min(1.0, 0.5 ** (2.0 / fraction - 1.0)))
        return {
            "kelly_fraction": float(kelly),
            "applied_fraction": float(applied),
            "prob_ever_half": prob_ever_half,
        }


@quant_module(
    id="portfolio_management.hrp",
    name="Hierarchical Risk Parity Optimizer",
    version="2.0.0",
    category="portfolio_management",
    tags=["hrp", "risk parity", "portfolio optimization", "clustering"],
)
class HRPOptimizer(QuantModule):
    """
    Hierarchical Risk Parity allocation (Lopez de Prado, 2016, "Building
    Diversified Portfolios that Outperform Out-of-Sample", JPM 42(4)).

    Pipeline:
      1. covariance -> correlation
      2. distance  d_ij = sqrt(0.5 * (1 - rho_ij))
      3. single-linkage tree clustering on d
      4. quasi-diagonalisation (seriation) of the correlation matrix
      5. recursive bisection: each split allocates weight between clusters
         proportional to inverse cluster variance (alpha = 1 - V1/(V1+V2))

    Unlike v1.x (which returned equal weights regardless of covariance),
    weights respond to both volatilities and the correlation structure.
    """

    @quant_tool(
        module_id="portfolio_management.hrp",
        name="portfolio_management.hrp.optimize",
        description="Optimize portfolio weights using Hierarchical Risk Parity (Lopez de Prado 2016)",
        parameters={
            "returns": {"type": "array", "description": "Matrix of asset returns (assets x time)"},
        },
        returns={
            "weights": {"type": "array", "description": "Optimized portfolio weights"},
            "portfolio_volatility": {"type": "float", "description": "Portfolio volatility (per-period)"},
        },
    )
    def optimize(self, returns: List[List[float]]) -> Dict:
        returns_arr = np.array(returns, dtype=float)
        if returns_arr.ndim != 2:
            return {"error": "returns must be a 2D matrix (assets x time)"}
        n_assets, n_obs = returns_arr.shape
        if n_assets < 2:
            return {"error": "At least 2 assets required"}
        if n_obs < 3:
            return {"error": "At least 3 observations per asset required"}

        cov = np.atleast_2d(np.cov(returns_arr))
        corr = _cov_to_corr(cov)

        # Correlation distance + hierarchical clustering (single linkage, per paper).
        dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
        np.fill_diagonal(dist, 0.0)
        link = linkage(squareform(dist, checks=False), method="single")

        sort_ix = self._quasi_diag(link)
        weights_map = self._recursive_bisection(cov, sort_ix)

        weights = np.array([weights_map[i] for i in range(n_assets)], dtype=float)
        port_vol = float(np.sqrt(max(float(weights @ cov @ weights), 0.0)))
        return {
            "weights": weights.tolist(),
            "portfolio_volatility": port_vol,
        }

    @staticmethod
    def _quasi_diag(link: np.ndarray) -> List[int]:
        """Seriation: expand the linkage tree into quasi-diagonal leaf order."""
        n_leaves = link.shape[0] + 1

        def expand(node: int) -> List[int]:
            if node < n_leaves:
                return [int(node)]
            row = link[int(node) - n_leaves]
            return expand(int(row[0])) + expand(int(row[1]))

        return expand(int(link[-1, 0])) + expand(int(link[-1, 1]))

    @staticmethod
    def _cluster_var(cov: np.ndarray, items: List[int]) -> float:
        """Inverse-variance weighted variance of a cluster."""
        sub = cov[np.ix_(items, items)]
        diag = np.diag(sub)
        inv_vol = 1.0 / np.sqrt(np.maximum(diag, 1e-18))
        inv_vol /= inv_vol.sum()
        return float(inv_vol @ sub @ inv_vol)

    def _recursive_bisection(self, cov: np.ndarray, sort_ix: List[int]) -> Dict[int, float]:
        weights: Dict[int, float] = {i: 1.0 for i in sort_ix}

        def bisect(items: List[int]) -> None:
            if len(items) <= 1:
                return
            split = len(items) // 2
            c1, c2 = items[:split], items[split:]
            var1 = self._cluster_var(cov, c1)
            var2 = self._cluster_var(cov, c2)
            alpha = 1.0 - var1 / (var1 + var2) if (var1 + var2) > 0 else 0.5
            for i in c1:
                weights[i] *= alpha
            for i in c2:
                weights[i] *= 1.0 - alpha
            bisect(c1)
            bisect(c2)

        bisect(sort_ix)
        return weights
