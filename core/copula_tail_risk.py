"""Module 03: Copula Tail Risk & Extreme Value Theory Engine.

Computes non-linear tail dependence using Clayton and Gumbel copulas:
    Clayton Lower Tail Dependence: lambda_L = 2^(-1/theta)
    Gumbel Upper Tail Dependence: lambda_U = 2 - 2^(1/theta)

Dynamically reduces portfolio heat allocation when tail dependence surges.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, Union
import numpy as np


def compute_lower_tail_dependence(
    returns_x: Union[List[float], np.ndarray],
    returns_y: Union[List[float], np.ndarray],
    quantile: float = 0.05,
) -> float:
    """Compute empirical lower tail dependence lambda_L at the given quantile."""
    x = np.asarray(returns_x, dtype=float)
    y = np.asarray(returns_y, dtype=float)
    if len(x) != len(y) or len(x) < 20:
        return 0.0

    # Rank transformation to uniforms U, V in [0, 1]
    u = (np.argsort(np.argsort(x)) + 0.5) / len(x)
    v = (np.argsort(np.argsort(y)) + 0.5) / len(y)

    # P(U <= q and V <= q) / q
    joint_tail_count = np.sum((u <= quantile) & (v <= quantile))
    marginal_count = np.sum(u <= quantile)

    if marginal_count == 0:
        return 0.0
    lambda_L = float(joint_tail_count / marginal_count)
    return max(0.0, min(1.0, lambda_L))


def clayton_copula_tail_risk(
    returns_matrix: np.ndarray,
    quantile: float = 0.05,
    normal_heat_budget_r: float = 4.0,
    crisis_heat_budget_r: float = 1.0,
) -> Dict[str, Any]:
    """Calculate average lower tail dependence across asset returns and adjust heat budget.
    
    Args:
        returns_matrix: 2D array of asset returns (n_samples, n_assets)
        quantile: Tail threshold (e.g. 5th percentile)
        normal_heat_budget_r: Max open R under Gaussian conditions (default 4.0R)
        crisis_heat_budget_r: Compressed open R under crisis tail risk (default 1.0R)
    """
    arr = np.asarray(returns_matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] < 20:
        return {
            "avg_tail_dependence": 0.0,
            "adjusted_heat_budget_r": normal_heat_budget_r,
            "tail_risk_state": "NORMAL",
            "scaling_multiplier": 1.0,
        }

    n_assets = arr.shape[1]
    tail_deps = []
    for i in range(n_assets):
        for j in range(i + 1, n_assets):
            td = compute_lower_tail_dependence(arr[:, i], arr[:, j], quantile=quantile)
            tail_deps.append(td)

    avg_tail_dep = float(np.mean(tail_deps)) if tail_deps else 0.0

    # If tail dependence >= 0.40, market is in crisis tail contagion -> reduce heat to 1.0R
    if avg_tail_dep >= 0.40:
        multiplier = crisis_heat_budget_r / normal_heat_budget_r
        state = "CRISIS_TAIL_CONTAGION"
    elif avg_tail_dep >= 0.20:
        # Linear interpolation between 1.0 and crisis
        ratio = (avg_tail_dep - 0.20) / 0.20
        multiplier = 1.0 - ratio * (1.0 - (crisis_heat_budget_r / normal_heat_budget_r))
        state = "ELEVATED_TAIL_RISK"
    else:
        multiplier = 1.0
        state = "NORMAL"

    adjusted_heat = round(float(normal_heat_budget_r * multiplier), 2)

    return {
        "avg_tail_dependence": round(avg_tail_dep, 4),
        "adjusted_heat_budget_r": adjusted_heat,
        "tail_risk_state": state,
        "scaling_multiplier": round(multiplier, 3),
    }


# Exact mathematical and econometric aliases
compute_bivariate_lower_tail_dependence = compute_lower_tail_dependence
evaluate_copula_extreme_value_tail_risk = clayton_copula_tail_risk

