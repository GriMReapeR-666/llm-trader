"""Domain Plugins for Quantitative Research Modules + Named Profiles.

The 11 document-derived quantitative models mount through a single
RetailQuantBundlePlugin (one class, 12 service keys), composed into named
profiles (paper, live, research, backtest) à la DeepSeek Harness bundles.

Modules mounted by the bundle:
  SPRT kill test, CUSUM monitor (+ structural-break detector), alpha
  capacity, retail friction, Bayesian updater, correlation breakdown,
  sentiment veto, account sizing, session spread, OU time stop,
  portfolio de-correlation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.harness.kernel import HarnessPlugin, TradingContext

logger = logging.getLogger(__name__)


# ── Domain Plugins (one per module) ──────────────────────────────────
# NOTE: the 11 single-engine leaf plugins were folded into
# RetailQuantBundlePlugin below (identical service keys + classes).


class RetailQuantBundlePlugin(HarnessPlugin):
    """Aggregate plugin mounting all retail quant models at once (12 service keys)."""
    name = "retail_quant_bundle"
    version = "1.0.0"
    dependencies = []

    def apply(self, ctx: TradingContext) -> None:
        from core.retail_quant_models import (
            SPRTStrategyTest, CUSUMMonitor, CUSUMStructuralBreakDetector, AlphaCapacityModel,
            RetailFrictionEngine, BayesianWinRateUpdater,
            CorrelationBreakdownDetector, SentimentVetoEngine,
            AccountSizingEngine, SessionSpreadEngine,
            OUTimeStopEngine, PortfolioDeCorrelationEngine,
        )
        ctx.provide("sprt_test", SPRTStrategyTest())
        ctx.provide("cusum_monitor", CUSUMMonitor())
        ctx.provide("cusum_structural_break", CUSUMStructuralBreakDetector())
        ctx.provide("alpha_capacity_model", AlphaCapacityModel())
        ctx.provide("retail_friction_engine", RetailFrictionEngine())
        ctx.provide("bayesian_updater", BayesianWinRateUpdater())
        ctx.provide("corr_breakdown_detector", CorrelationBreakdownDetector())
        ctx.provide("sentiment_veto", SentimentVetoEngine())
        ctx.provide("account_sizer", AccountSizingEngine())
        ctx.provide("session_spread_engine", SessionSpreadEngine())
        ctx.provide("ou_time_stop", OUTimeStopEngine())
        ctx.provide("portfolio_decorr", PortfolioDeCorrelationEngine())
        logger.info("RetailQuantBundlePlugin mounted (12 modules).")


# ── Named Profiles (DeepSeek-style compositions) ─────────────────────

# Profile: the set of plugins and their parameters for each operating mode
PROFILE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "paper": {
        "description": "Paper trading — all plugins, simulated execution, no real money at risk.",
        "plugins": [
            ("core.harness.plugins:RiskManagementPlugin", {"balance": 10000.0, "max_risk_pct": 0.01}),
            ("core.harness.plugins:TradingMemoryPlugin", {}),
            ("core.harness.plugins:BrokerPlugin", {"paper_mode": True}),
            ("core.harness.plugins:DataHubPlugin", {}),
            ("core.harness.plugins:CausalIntelligencePlugin", {}),
            ("core.harness.plugins:AnalyticalFrameworksPlugin", {}),
            ("core.harness.plugins:KronosIntelligencePlugin", {}),
            ("core.harness.plugins:ExecutionSafetyPlugin", {}),
            ("core.harness.plugins:CointegrationGovernancePlugin", {}),
            ("core.harness.plugins:SkillsPlugin", {}),
            ("core.harness.plugins:JournalAuditPlugin", {}),
            ("core.harness.plugins:QuantWorkflowsPlugin", {}),
            ("core.harness.plugins:MacroEventAlphaPlugin", {}),
            ("core.harness.extracted_module_plugins:ExtractedModulesPlugin", {}),
            ("core.harness.quant_models_plugins:RetailQuantBundlePlugin", {}),
            ("core.harness.plugins:DeliberativeGraphPlugin", {"require_human_approval": False}),
            ("core.harness.live_trading_plugin:LiveTradingPlugin", {}),
        ],
    },
    "live": {
        "description": "Live trading — real broker, real execution, human approval on new hypotheses.",
        "plugins": [
            ("core.harness.plugins:RiskManagementPlugin", {"balance": 10000.0, "max_risk_pct": 0.01}),
            ("core.harness.plugins:TradingMemoryPlugin", {}),
            ("core.harness.plugins:BrokerPlugin", {"paper_mode": False}),
            ("core.harness.plugins:DataHubPlugin", {}),
            ("core.harness.plugins:CausalIntelligencePlugin", {}),
            ("core.harness.plugins:AnalyticalFrameworksPlugin", {}),
            ("core.harness.plugins:KronosIntelligencePlugin", {}),
            ("core.harness.plugins:ExecutionSafetyPlugin", {}),
            ("core.harness.plugins:CointegrationGovernancePlugin", {}),
            ("core.harness.plugins:SkillsPlugin", {}),
            ("core.harness.plugins:JournalAuditPlugin", {}),
            ("core.harness.plugins:QuantWorkflowsPlugin", {}),
            ("core.harness.plugins:MacroEventAlphaPlugin", {}),
            ("core.harness.extracted_module_plugins:ExtractedModulesPlugin", {}),
            ("core.harness.quant_models_plugins:RetailQuantBundlePlugin", {}),
            ("core.harness.plugins:DeliberativeGraphPlugin", {"require_human_approval": True}),
            ("core.harness.live_trading_plugin:LiveTradingPlugin", {"paper_mode": False}),
        ],
    },
    "research": {
        "description": "Research mode — discovery engine, backtesting, no execution.",
        "plugins": [
            ("core.harness.plugins:TradingMemoryPlugin", {}),
            ("core.harness.plugins:DataHubPlugin", {}),
            ("core.harness.plugins:AnalyticalFrameworksPlugin", {}),
            ("core.harness.plugins:KronosIntelligencePlugin", {}),
            ("core.harness.plugins:CointegrationGovernancePlugin", {}),
            ("core.harness.plugins:SkillsPlugin", {}),
            ("core.harness.extracted_module_plugins:ExtractedModulesPlugin", {}),
            ("core.harness.quant_models_plugins:RetailQuantBundlePlugin", {}),
        ],
    },
    "backtest": {
        "description": "Backtest mode — validation, CPCV, overfit detection, no live execution.",
        "plugins": [
            ("core.harness.plugins:TradingMemoryPlugin", {}),
            ("core.harness.plugins:DataHubPlugin", {}),
            ("core.harness.plugins:AnalyticalFrameworksPlugin", {}),
            ("core.harness.plugins:KronosIntelligencePlugin", {}),
            ("core.harness.plugins:CointegrationGovernancePlugin", {}),
            ("core.harness.extracted_module_plugins:ExtractedModulesPlugin", {}),
            ("core.harness.quant_models_plugins:RetailQuantBundlePlugin", {}),
        ],
    },
}


def load_profile(name: str) -> List[Dict[str, Any]]:
    """Load a named profile and return the plugin list for mounting.

    Args:
        name: Profile name ('paper', 'live', 'research', 'backtest').

    Returns:
        List of {class_path, kwargs} dicts ready for kernel.plugin() calls.
    """
    if name not in PROFILE_DEFINITIONS:
        raise ValueError(
            f"Unknown profile '{name}'. Available: {list(PROFILE_DEFINITIONS.keys())}"
        )
    profile = PROFILE_DEFINITIONS[name]
    plugins = []
    for class_path, kwargs in profile["plugins"]:
        plugins.append({"class_path": class_path, "kwargs": kwargs})
    return plugins


def mount_profile(ctx: TradingContext, profile_name: str) -> TradingContext:
    """Mount all plugins from a named profile into a TradingContext.

    This is the high-level entry point that replaces manual plugin wiring.
    """
    import importlib

    plugins = load_profile(profile_name)
    for entry in plugins:
        class_path = entry["class_path"]
        kwargs = entry["kwargs"]

        module_path, class_name = class_path.rsplit(":", 1)
        module = importlib.import_module(module_path)
        plugin_cls = getattr(module, class_name)

        ctx.plugin(plugin_cls(**kwargs))

    logger.info(
        "Profile '%s' mounted with %d plugins into context '%s'.",
        profile_name, len(plugins), ctx.name,
    )
    return ctx


def list_profiles() -> Dict[str, str]:
    """Return {name: description} for all available profiles."""
    return {name: defn["description"] for name, defn in PROFILE_DEFINITIONS.items()}
