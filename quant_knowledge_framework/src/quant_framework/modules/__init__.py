"""
Modules Package
===============
Quant modules (HMM, Kalman, Kelly, HRP, execution, etc.).

Importing this package auto-registers every builtin quant module in the
global `registry`.  Registration happens inside `quant_module`'s wrapped
`__init__`, so a plain import alone used to leave the registry empty;
`ensure_builtin_modules_registered()` instantiates each module once with
default parameters, which both registers it and publishes its quant tools.

All 11 builtin module classes are default-constructible, so no explicit
parameters are needed here.
"""

from quant_framework.modules.interface import QuantModule, quant_module, quant_tool, registry
from quant_framework.modules.regime_detection import HMMRegimeClassifier, HurstCalculator
from quant_framework.modules.strategy_development import WalkForwardOptimizer, DeflatedSharpeRatio, TripleBarrierLabeler
from quant_framework.modules.portfolio_management import KellyCriterion, HRPOptimizer
from quant_framework.modules.order_execution import DynamicSpreadCalculator, AlmgrenChrissCalculator
from quant_framework.modules.quant_utilities import ExpectancyCalculator, DrawdownAnalyzer

__all__ = [
    "QuantModule", "quant_module", "quant_tool", "registry",
    "HMMRegimeClassifier", "HurstCalculator",
    "WalkForwardOptimizer", "DeflatedSharpeRatio", "TripleBarrierLabeler",
    "KellyCriterion", "HRPOptimizer",
    "DynamicSpreadCalculator", "AlmgrenChrissCalculator",
    "ExpectancyCalculator", "DrawdownAnalyzer",
    "ensure_builtin_modules_registered",
]

_BUILTIN_MODULES = [
    HMMRegimeClassifier,
    HurstCalculator,
    WalkForwardOptimizer,
    DeflatedSharpeRatio,
    TripleBarrierLabeler,
    KellyCriterion,
    HRPOptimizer,
    DynamicSpreadCalculator,
    AlmgrenChrissCalculator,
    ExpectancyCalculator,
    DrawdownAnalyzer,
]


def ensure_builtin_modules_registered():
    """Instantiate every builtin quant module so it registers with the global registry.

    Registration is performed by `quant_module`'s wrapped `__init__`, so each
    class must be instantiated (not just imported) for its tools to appear in
    `registry.list_tools()`.  Idempotent: modules already present are left
    untouched.  Returns the populated registry.
    """
    for cls in _BUILTIN_MODULES:
        if cls.module_id not in registry._modules:
            cls()
    return registry


# Populate the registry on import so that
# `from quant_framework.modules import registry` exposes all module tools
# without any extra setup.
ensure_builtin_modules_registered()
