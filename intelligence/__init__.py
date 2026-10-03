"""Intelligence package for LLM Trader system."""
from __future__ import annotations

import os as _os
import sys as _sys
_pkg_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _pkg_root not in _sys.path:
    _sys.path.insert(0, _pkg_root)

# Re-exports are resolved lazily (PEP 562). Eagerly importing every submodule
# here made `import intelligence.<anything>` execute all of them, and
# intelligence.macrofundamental_pipeline reaches into the data package, whose
# __init__ reaches back into intelligence -- a package-level cycle that made
# `import data.news_triage` fail with a partially-initialized-module error
# depending on which package was imported first.
_LAZY = {
    "MacroGraph": "intelligence.macro_graph",
    "AutonomousCausalValidator": "intelligence.autonomous_causal_engine",
    "resolve_causal_setup": "intelligence.causal_setup_map",
    "propagate": "intelligence.causal_propagate",
    "ENTITIES": "intelligence.economic_dynamics_catalog",
    "CAUSAL_EDGES": "intelligence.economic_dynamics_catalog",
    "validate_catalog": "intelligence.economic_dynamics_catalog",
    "seed_economic_dynamics": "intelligence.economic_dynamics_seed",
    "BEHAVIORAL_ENTITIES": "intelligence.behavioral_macro_catalog",
    "BEHAVIORAL_CAUSAL_EDGES": "intelligence.behavioral_macro_catalog",
    "seed_behavioral_dynamics_into_graph": "intelligence.behavioral_macro_catalog",
    "AutonomousFactChecker": "intelligence.fact_checker",
    "FactValidationResult": "intelligence.fact_checker",
    "MacroRegimeClassifier": "intelligence.regime_classifier",
    "MacroRegimeResult": "intelligence.regime_classifier",
    "SystemMetaReflector": "intelligence.meta_reflector",
    "HealthAuditReport": "intelligence.meta_reflector",
    "AutonomousMacroPipeline": "intelligence.macrofundamental_pipeline",
    "publish_opportunity": ("intelligence.opportunity_bus", "publish"),
    "load_open_opportunities": ("intelligence.opportunity_bus", "load_open"),
    "score_opportunity": "intelligence.opportunity_fusion",
    "TradingMemory": "intelligence.trading_memory",
    "UniversalDecisionEngine": "intelligence.universal_decision_engine",
    "UniversalDecision": "intelligence.universal_decision_engine",
    "DecisionContext": "intelligence.universal_decision_engine",
    "DecisionDomain": "intelligence.universal_decision_engine",
    "DecisionVerdict": "intelligence.universal_decision_engine",
    "PhilosophicalPrinciple": "intelligence.universal_decision_engine",
    "arbitrate_signals": "intelligence.signal_arbitrator",
}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if isinstance(target, tuple):
        modname, attr = target
    else:
        modname, attr = target, name
    import importlib

    value = getattr(importlib.import_module(modname), attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    "MacroGraph",
    "AutonomousCausalValidator",
    "resolve_causal_setup",
    "propagate",
    "ENTITIES",
    "CAUSAL_EDGES",
    "validate_catalog",
    "seed_economic_dynamics",
    "BEHAVIORAL_ENTITIES",
    "BEHAVIORAL_CAUSAL_EDGES",
    "seed_behavioral_dynamics_into_graph",
    "AutonomousFactChecker",
    "FactValidationResult",
    "MacroRegimeClassifier",
    "MacroRegimeResult",
    "SystemMetaReflector",
    "HealthAuditReport",
    "AutonomousMacroPipeline",
    "publish_opportunity",
    "load_open_opportunities",
    "score_opportunity",
    "TradingMemory",
    "UniversalDecisionEngine",
    "UniversalDecision",
    "DecisionContext",
    "DecisionDomain",
    "DecisionVerdict",
    "PhilosophicalPrinciple",
    "arbitrate_signals",
]
