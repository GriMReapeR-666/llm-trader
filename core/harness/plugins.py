"""Standard Trading Harness Plugins.

Wraps all trading subsystems, mathematical pillars, analytical frameworks,
geopolitical foresight engines, Kronos AI validators, execution safety supervisors,
and alternative data pipelines into spatiotemporally composable Harness Plugins.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Callable

from core.harness.kernel import HarnessPlugin, TradingContext
from core.harness.disposable import Disposable

logger = logging.getLogger(__name__)


class RiskManagementPlugin(HarnessPlugin):
    """Plugin mounting deterministic risk arbitration and account drawdown breakers."""
    name = "risk_management"
    version = "1.0.0"
    dependencies = []

    def __init__(self, db_path: str = "trading_state.db", balance: float = 10000.0,
                 max_risk_pct: float = 0.01, paper_mode: bool = False):
        self.db_path = db_path
        self.balance = balance
        self.max_risk_pct = max_risk_pct
        # Paper sessions must never read the attached demo/live MT5 equity as
        # their own: the data terminal is not the paper book. Without this the
        # RiskManager reported demo equity (e.g. $100k) against the $10k paper
        # balance and every decision saw a malformed account (balance != equity
        # with no paper positions) and held.
        self.paper_mode = bool(paper_mode)

    def apply(self, ctx: TradingContext) -> None:
        import os
        from core.risk_manager import RiskManager
        from core.trading_safety import DeterministicValidator, TradingStore

        store = TradingStore(self.db_path)
        effective_balance = float(self.balance)
        if effective_balance <= 0.0:
            env_balance = os.getenv("PAPER_EQUITY") or os.getenv("PAPER_BALANCE")
            if env_balance:
                try:
                    effective_balance = float(env_balance)
                except (ValueError, TypeError):
                    effective_balance = 0.0
            if effective_balance <= 0.0 and store:
                try:
                    r_state = store.get_state("risk_manager_state") or {}
                    effective_balance = float(r_state.get("initial_balance") or r_state.get("start_equity") or r_state.get("peak_equity") or 0.0)
                except Exception:
                    effective_balance = 0.0
            if effective_balance <= 0.0:
                effective_balance = 10000.0
        self.balance = effective_balance
        risk_mgr = RiskManager(balance=self.balance, risk_per_trade=min(self.max_risk_pct, 0.01),
                               store=store, is_paper=self.paper_mode)
        validator = DeterministicValidator(store=store)

        ctx.provide("trading_store", store)
        ctx.provide("risk_manager", risk_mgr)
        ctx.provide("deterministic_validator", validator)
        logger.info("RiskManagementPlugin initialized with balance %.2f, max risk %.2f%% (mode=%s)",
                    self.balance, self.max_risk_pct * 100.0,
                    "PAPER" if self.paper_mode else "LIVE")


class TradingMemoryPlugin(HarnessPlugin):
    """Plugin mounting multi-tier SQLite trading memory (L0-L3)."""
    name = "trading_memory"
    version = "1.0.0"
    dependencies = []

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path

    def apply(self, ctx: TradingContext) -> None:
        from intelligence.trading_memory import TradingMemory

        memory = TradingMemory(db_path=self.db_path)
        ctx.provide("memory", memory)
        ctx.effect(lambda: memory.close_all() if hasattr(memory, "close_all") else None, name="memory_cleanup")
        logger.info("TradingMemoryPlugin mounted on database '%s'", self.db_path)


class BrokerPlugin(HarnessPlugin):
    """Plugin mounting PaperBroker or live MT5 order routing."""
    name = "broker"
    version = "1.0.0"
    dependencies = ["risk_manager"]

    def __init__(self, paper_mode: bool = True, db_path: str = "trading_state.db"):
        self.paper_mode = paper_mode
        self.db_path = db_path

    def apply(self, ctx: TradingContext) -> None:
        from core.order_execution import PaperBroker
        from core.trade_manager import TradeManager
        from core.trading_safety import TradingStore
        from core.harness.sandbox import CapabilitySandbox, CapabilityToken, PermissionLevel

        if self.paper_mode:
            broker = PaperBroker()
        else:
            try:
                import MetaTrader5 as mt5
                broker = mt5
            except ImportError:
                logger.warning("MetaTrader5 package not available; falling back to PaperBroker.")
                broker = PaperBroker()

        risk_mgr = ctx.get("risk_manager")
        store = ctx.get("trading_store", TradingStore(self.db_path))
        trade_mgr = TradeManager(client=broker, risk_manager=risk_mgr, store=store)

        # Issue active execution token for broker lifecycle
        sandbox = CapabilitySandbox(default_level=PermissionLevel.ORDER_WRITE if not self.paper_mode else PermissionLevel.SIMULATION)
        token = sandbox.issue_token(
            level=PermissionLevel.ORDER_WRITE if not self.paper_mode else PermissionLevel.SIMULATION,
            scopes=["broker", "order_execution"],
            duration_minutes=24 * 60,
        )

        ctx.provide("broker", broker)
        ctx.provide("trade_manager", trade_mgr)
        ctx.provide("broker_token", token)
        ctx.provide("sandbox", sandbox)

        def cleanup():
            if hasattr(broker, "shutdown"):
                broker.shutdown()

        ctx.effect(cleanup, name="broker_shutdown")
        logger.info("BrokerPlugin mounted (mode=%s)", "PAPER" if self.paper_mode else "LIVE")


class CausalIntelligencePlugin(HarnessPlugin):
    """Plugin mounting MacroGraph DAG and Autonomous Causal Validator."""
    name = "causal_intelligence"
    version = "1.0.0"
    dependencies = ["memory"]

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path

    def apply(self, ctx: TradingContext) -> None:
        from intelligence.macro_graph import MacroGraph
        from intelligence.autonomous_causal_engine import AutonomousCausalValidator

        macro_graph = MacroGraph(db_path=self.db_path)
        causal_val = AutonomousCausalValidator(graph=macro_graph)

        ctx.provide("macro_graph", macro_graph)
        ctx.provide("causal_validator", causal_val)
        logger.info("CausalIntelligencePlugin mounted with 134 macro dynamic nodes.")


class DataHubPlugin(HarnessPlugin):
    """Unified market + macro data hub.

    Provides market_data (MT5 ticks/bars, when broker + risk_manager are
    present) plus the dependency-free feeds: fred_fetch_fn (FRED series),
    cot_provider (CFTC positioning), news_ingest_fn (RSS/NewsAPI), and
    openbb_bridge (institutional market data).  Profiles without a broker
    (research/backtest) still receive the standalone feeds.
    """
    name = "data_hub"
    version = "1.0.0"
    dependencies = []

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def apply(self, ctx: TradingContext) -> None:
        broker = ctx.get("broker", None)
        risk_mgr = ctx.get("risk_manager", None)
        if broker is not None and risk_mgr is not None:
            from data.market_data_provider import MarketDataProvider

            provider = MarketDataProvider(client=broker, risk_manager=risk_mgr, api_key=self.api_key)
            ctx.provide("market_data", provider)

            def cleanup(provider=provider):
                if hasattr(provider, "close"):
                    provider.close()

            ctx.effect(cleanup, name="market_data_cleanup")

        from data.data_source_pipeline import catalog_fred_series

        def fetch_series(series_ids: Optional[list] = None):
            return catalog_fred_series(series_ids or ["DGS10", "T10Y2Y", "VIXCLS", "FEDFUNDS"])

        ctx.provide("fred_fetch_fn", fetch_series)

        from data.cftc_cot_provider import CFTCCOTProvider

        ctx.provide("cot_provider", CFTCCOTProvider())

        from data.realtime_news_ingest import run as run_news_ingest

        ctx.provide("news_ingest_fn", run_news_ingest)

        from data.openbb_data_bridge import OpenBBDataBridge

        ctx.provide("openbb_bridge", OpenBBDataBridge())
        logger.info("DataHubPlugin mounted (market_data=%s + FRED/COT/news/OpenBB).",
                    "yes" if broker is not None and risk_mgr is not None else "skipped-no-broker")


class AnalyticalFrameworksPlugin(HarnessPlugin):
    """Plugin mounting Modules 01-06 Geopolitical Foresight, Macro Spreads, and Analog Case Studies."""
    name = "analytical_frameworks"
    version = "1.0.0"
    dependencies = []

    def apply(self, ctx: TradingContext) -> None:
        from intelligence.extrapolation_and_state_trajectory import StateSpaceTrajectoryForecaster
        from intelligence.geopolitical_macro_foresight import BayesianGeopoliticalRegimeClassifier
        from intelligence.case_study_matcher import get_case_study_matcher
        from intelligence.macro_alpha_signals_and_microstructure import MacroAlphaSignalsEngine

        ctx.provide("trajectory_forecaster", StateSpaceTrajectoryForecaster())
        ctx.provide("geopolitical_classifier", BayesianGeopoliticalRegimeClassifier())
        ctx.provide("case_study_matcher", get_case_study_matcher())
        ctx.provide("macro_alpha_signals", MacroAlphaSignalsEngine())
        logger.info("AnalyticalFrameworksPlugin mounted (Modules 01-07 Foresight, Spreads & Signals).")


class KronosIntelligencePlugin(HarnessPlugin):
    """Plugin mounting Kronos Neural Foundation Models, Synthetic Simulator, and CPCV Backtesting."""
    name = "kronos_intelligence"
    version = "1.0.0"
    dependencies = []

    def apply(self, ctx: TradingContext) -> None:
        from intelligence.kronos_signal_validator import KronosSignalValidator
        from intelligence.kronos_synthetic_generator import generate_correlated_multi_asset_synthetic_dataset
        from core.cpcv_backtester import run_combinatorial_purged_backtest

        ctx.provide("kronos_signal_validator", KronosSignalValidator())
        ctx.provide("synthetic_generator_fn", generate_correlated_multi_asset_synthetic_dataset)
        ctx.provide("cpcv_backtest_fn", run_combinatorial_purged_backtest)
        logger.info("KronosIntelligencePlugin mounted (Signal Calibration, Synthetic Jumps & CPCV).")


class ExecutionSafetyPlugin(HarnessPlugin):
    """Plugin mounting Zero-Latency Execution Safety Supervisor (<250ms latency, 2.5x slippage, kill-switch)."""
    name = "execution_safety"
    version = "1.0.0"
    dependencies = []

    def apply(self, ctx: TradingContext) -> None:
        from core.execution_safety_supervisor import get_execution_safety_supervisor

        supervisor = get_execution_safety_supervisor()
        ctx.provide("execution_safety_supervisor", supervisor)
        logger.info("ExecutionSafetyPlugin mounted.")


class CointegrationGovernancePlugin(HarnessPlugin):
    """Plugin mounting Cointegration Stationarity, Backtest Deflation, and Compounding Governance."""
    name = "cointegration_governance"
    version = "1.0.0"
    dependencies = []

    def apply(self, ctx: TradingContext) -> None:
        from core.cointegration_and_compounding_governance import (
            calculate_engle_granger_adf_cointegration,
            evaluate_out_of_sample_deflation_hurdle,
            apply_position_band_inertia_filter,
            calculate_multi_asset_portfolio_variance,
            calculate_fractional_growth_optimal_kelly,
            evaluate_multi_horizon_drawdown_circuit_breakers,
            evaluate_alpha_decay_decommission_thresholds,
        )

        ctx.provide("eg_cointegration_fn", calculate_engle_granger_adf_cointegration)
        ctx.provide("deflation_hurdle_fn", evaluate_out_of_sample_deflation_hurdle)
        ctx.provide("inertia_filter_fn", apply_position_band_inertia_filter)
        ctx.provide("portfolio_variance_fn", calculate_multi_asset_portfolio_variance)
        ctx.provide("growth_kelly_fn", calculate_fractional_growth_optimal_kelly)
        ctx.provide("circuit_breakers_fn", evaluate_multi_horizon_drawdown_circuit_breakers)
        ctx.provide("alpha_decommission_fn", evaluate_alpha_decay_decommission_thresholds)
        logger.info("CointegrationGovernancePlugin mounted.")


class SkillsPlugin(HarnessPlugin):
    """Agent skills registry + sandboxed code interpreter + creator engine."""
    name = "skills"
    version = "1.0.0"
    dependencies = []

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path

    def apply(self, ctx: TradingContext) -> None:
        from core.skill_registry import SkillRegistry
        from research.code_interpreter import DynamicMetricInterpreter
        from research.creator_engine import CreatorEngine

        registry = SkillRegistry()
        ctx.provide("skill_registry", registry)
        ctx.provide("code_interpreter", DynamicMetricInterpreter(db_path=self.db_path))
        ctx.provide("creator_engine", CreatorEngine(db_path=self.db_path))
        logger.info("SkillsPlugin mounted with %d standard skills.", len(registry._skills))


class DeliberativeGraphPlugin(HarnessPlugin):
    """Plugin mounting the LangGraph Deliberative Reasoning Graph (Standard Mode).

    Also provides the llm_router service (previously a standalone
    ModelRouterPlugin): the router is resolved from the context when present,
    otherwise constructed with defaults.
    """
    name = "deliberative_graph"
    version = "1.0.0"
    dependencies = ["risk_manager", "broker", "memory"]

    def __init__(self, db_path: str = "trading_state.db", require_human_approval: bool = False,
                 config_path: Optional[str] = None):
        self.db_path = db_path
        self.require_human_approval = require_human_approval
        self.config_path = config_path

    def apply(self, ctx: TradingContext) -> None:
        from intelligence.deliberative_graph import build_deliberative_graph

        router = ctx.get("llm_router", None)
        if router is None:
            from data.llm_router import LLMRouter

            router = LLMRouter()
            ctx.provide("llm_router", router)
        broker = ctx.get("broker")

        graph = build_deliberative_graph(
            db_path=self.db_path,
            router=router,
            broker=broker,
            require_human_approval=self.require_human_approval,
        )
        ctx.provide("deliberative_graph", graph)
        logger.info("DeliberativeGraphPlugin compiled and mounted.")


class JournalAuditPlugin(HarnessPlugin):
    """Plugin mounting 14-rule deterministic journal audit and verification."""
    name = "journal_audit"
    version = "1.0.0"
    dependencies = []

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path

    def apply(self, ctx: TradingContext) -> None:
        from operations.journal_audit import audit as run_audit, qualified_metrics

        def audit_fn():
            return run_audit(self.db_path)

        def metrics_fn():
            return qualified_metrics(self.db_path)

        ctx.provide("audit_fn", audit_fn)
        ctx.provide("audit_metrics_fn", metrics_fn)
        logger.info("JournalAuditPlugin mounted for database '%s'.", self.db_path)


class QuantWorkflowsPlugin(HarnessPlugin):
    """Plugin mounting institutional quantitative and algorithmic trading workflows."""
    name = "quant_workflows"
    version = "1.0.0"
    dependencies = ["risk_manager"]

    def apply(self, ctx: TradingContext) -> None:
        from core.harness.workflows import QuantWorkflowEngine

        engine = QuantWorkflowEngine()
        ctx.provide("quant_workflow_engine", engine)
        logger.info("QuantWorkflowsPlugin mounted with %d trading workflows.", len(engine.list_workflows()))


class MacroEventAlphaPlugin(HarnessPlugin):
    """Plugin mounting Macroeconomic Event-Driven Alpha & Surprise Transmission Engine."""
    name = "macro_event_alpha"
    version = "1.0.0"
    dependencies = ["risk_manager"]

    def apply(self, ctx: TradingContext) -> None:
        from core.macro_event_alpha_engine import (
            MacroEventAlphaEngine,
            MacroLeadingIndicatorScorecard,
            RollingEconomicSurpriseIndex,
            StandardizedSurpriseDecomposer,
        )

        engine = MacroEventAlphaEngine()
        ctx.provide("macro_event_engine", engine)
        ctx.provide("leading_scorecard_evaluator", MacroLeadingIndicatorScorecard)
        ctx.provide("esi_calculator", RollingEconomicSurpriseIndex)
        ctx.provide("surprise_decomposer", StandardizedSurpriseDecomposer)
        logger.info("MacroEventAlphaPlugin mounted with leading indicators, ESI & 3-phase execution.")
