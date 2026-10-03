"""Shared mathematical utilities for core modules.

Eliminates duplicated _compute_sharpe and other math functions.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def compute_sharpe(returns: Sequence[float], annualization: float = 1.0) -> float:
    """Compute annualized Sharpe ratio.

    Args:
        returns: Array of period returns.
        annualization: Annualization factor (e.g. 252 for daily, sqrt(252) for
            already-standardized series). Pass 1.0 for raw ratio.

    Returns:
        Annualized Sharpe ratio, or 0.0 if insufficient data.
    """
    arr = np.array([r for r in returns if math.isfinite(r)])
    if len(arr) < 10:
        return 0.0
    std = float(np.std(arr))
    if std < 1e-15:
        return 0.0
    return float(np.mean(arr) / std * math.sqrt(annualization))
