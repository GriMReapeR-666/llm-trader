"""Research package for LLM Trader system."""
from __future__ import annotations

import os as _os
import sys as _sys

_pkg_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _pkg_root not in _sys.path:
    _sys.path.insert(0, _pkg_root)

_LAZY_EXPORTS = {
    "AR1ModelFit": ("research.synthetic_path_simulator", "AR1ModelFit"),
    "fit_ar1_model": ("research.synthetic_path_simulator", "fit_ar1_model"),
    "generate_synthetic_paths": ("research.synthetic_path_simulator", "generate_synthetic_paths"),
    "default_mean_reversion_strategy": ("research.synthetic_path_simulator", "default_mean_reversion_strategy"),
    "optimize_strategy_ensemble": ("research.synthetic_path_simulator", "optimize_strategy_ensemble"),
    "RegimeMonteCarloResult": ("research.regime_monte_carlo", "RegimeMonteCarloResult"),
    "RegimeSwitchingMonteCarlo": ("research.regime_monte_carlo", "RegimeSwitchingMonteCarlo"),
    "RiskParityOptimizer": ("research.risk_parity_engine", "RiskParityOptimizer"),
    "RiskParityResult": ("research.risk_parity_engine", "RiskParityResult"),
}


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        mod_name, attr_name = _LAZY_EXPORTS[name]
        import importlib
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_LAZY_EXPORTS.keys())
