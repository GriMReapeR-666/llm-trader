"""Research package for LLM Trader system."""
from __future__ import annotations

import os as _os
import sys as _sys
_pkg_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _pkg_root not in _sys.path:
    _sys.path.insert(0, _pkg_root)

from research.synthetic_path_simulator import (
    AR1ModelFit,
    fit_ar1_model,
    generate_synthetic_paths,
    default_mean_reversion_strategy,
    optimize_strategy_ensemble,
)
from research.regime_monte_carlo import (
    RegimeMonteCarloResult,
    RegimeSwitchingMonteCarlo,
)
from research.risk_parity_engine import (
    RiskParityOptimizer,
    RiskParityResult,
)

__all__ = [
    "AR1ModelFit",
    "fit_ar1_model",
    "generate_synthetic_paths",
    "default_mean_reversion_strategy",
    "optimize_strategy_ensemble",
    "RegimeMonteCarloResult",
    "RegimeSwitchingMonteCarlo",
    "RiskParityOptimizer",
    "RiskParityResult",
]
