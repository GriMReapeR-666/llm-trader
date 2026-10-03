from __future__ import annotations
"""LLM Orchestration Engine for AI-driven multi-timeframe trading decisions."""

def print_bordered(console, title, text, style="white"):
    try:
        from rich.panel import Panel
        from rich.console import Console
        from rich.text import Text
        from rich import box
        if not console:
            console = Console(force_terminal=True)
        try:
            content = Text.from_markup(text)
        except Exception:
            content = Text(text)
        console.print(Panel(content, title=title, border_style=style, box=box.HEAVY))
    except Exception:
        # Fallback: strip non-ASCII (emoji) to avoid cp1252 UnicodeEncodeError
        # on Windows terminals that do not support UTF-8 output.
        safe_title = title.encode("ascii", "ignore").decode() if title else ""
        safe_text = text.encode("ascii", "ignore").decode() if text else ""
        print(f"[{safe_title}] {safe_text}")


import argparse
import configparser
import os
import sys
from pathlib import Path
import time
import threading
import json
import logging
import math
import hashlib
import json as _json
from dataclasses import asdict, dataclass, replace
from typing import Optional, List, Dict, Tuple, Any
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

# Both spellings are used in this module; neither was ever bound at module
# scope, so every call site below raised NameError at runtime instead of
# logging. `wire_trade_manager` is the worst offender: its bare `logger.*`
# calls fired while attaching the paper broker, and the resulting NameError
# was swallowed and recorded as the provider-circuit "last_error"
# ("name 'logger' is not defined"), downgrading the tier-4 router to
# deterministic-only and silently disabling the LLM. Bind both names to the
# same logger this module already uses inline via getLogger().
log = logging.getLogger("LLMOrchestrator")
logger = log

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

try:
    from ui.scanner import MarketScanner
except ImportError:
    try:
        from scanner import MarketScanner
    except ImportError:
        MarketScanner = None


def _resolve_symbol(name, default):
    mt5_mod = sys.modules.get("mt5_trader")
    if mt5_mod is not None and hasattr(mt5_mod, name):
        return getattr(mt5_mod, name)
    return default

def _get_mt5():
    mt5_mod = sys.modules.get("mt5_trader")
    if mt5_mod is not None and hasattr(mt5_mod, "mt5"):
        return mt5_mod.mt5
    return mt5

def _get_fetch_ohlc():
    """Resolve fetch_ohlc from mt5_trader module or fallback to market_data_provider."""
    mt5_mod = sys.modules.get("mt5_trader")
    if mt5_mod is not None and hasattr(mt5_mod, "fetch_ohlc"):
        return mt5_mod.fetch_ohlc
    from market_data_provider import fetch_ohlc
    return fetch_ohlc

from risk_manager import RiskManager
from trade_manager import TradeManager
from rl_trainer import RLTrainer
from trading_safety import (
    DeterministicValidator, PortfolioRiskArbiter,
    TradeProposal, TradingStore, ValidationResult,
    safe_risk_fraction, exposure_buckets,
)
from trading_memory import TradingMemory
from journal_audit import audit as audit_journal, baseline_metrics, qualified_metrics, write_report, write_audit_export
from journal_migration import migrate as migrate_legacy_journal
from agent_operations import AgentOperations
from local_llm_memory import process_outbox as process_local_memory_outbox
from boot_fetch_watchdog import run_stage_with_deadline, stage_budgets
from boot_datasheet import boot_datasheet_enabled, get_datasheet
try:
    from research_validation import promotion_gate as research_promotion_gate, labelled_dataset
except ImportError:
    from research.research_validation import promotion_gate as research_promotion_gate, labelled_dataset
from promotion_package import write as write_promotion_package, verify as verify_promotion_package
from baseline_research import baseline_report
from adaptive_research_service import AdaptiveResearchService
from data_quality import DataQualityGate
from provider_circuit import ProviderCircuitBreaker
try:
    from setup_registry import SETUPS, classify as classify_setup
except ImportError:
    from research.setup_registry import SETUPS, classify as classify_setup
import strategy_lifecycle
from strategy_lifecycle import block_info as strategy_lifecycle_block
from strategy_evaluation import evaluate as evaluate_strategy_lifecycle
from broker_preflight import persist_preflight, validate_envelope
from execution_planning import build_pending_entry, validate_emergency_exit_request, validate_quote
from feature_builder import build_snapshot as build_feature_snapshot
try:
    from macro_graph import MacroGraph
except ImportError:
    from intelligence.macro_graph import MacroGraph
from intelligence.reflection_graph import build_reflection_graph
from graph_state import MemoryReflectionState
from lag_delay_engine import (
    TimestampedEvent, HeartbeatTracker, ConfidenceDecayEngine, SmithPredictorEngine,
    SlippageCalibrator, utcnow_iso,
)
from rl_action_bridge import bridge_rl_action, merge_bridge_into_decision
from autonomous_causal_engine import AutonomousCausalValidator, CausalChainSetupAnalyzer
from account_growth import AccountGrowthController
from strategy_accuracy import build_strategy_accuracy_scorecard
from research_sprint_planner import plan_and_persist as plan_research_sprints
from opportunity_bus import (
    publish as publish_opportunities,
    publish_from_scan,
    publish_from_stat_pass_edges,
    opportunity_from_features,
    bus_snapshot,
)
from opportunity_fusion import score_opportunity, fusion_block_reasons
from cycle_brief import build as build_cycle_brief, persist as persist_cycle_brief
from causal_propagate import propagate as propagate_causal_paths
from macro_state_vector import build_macro_state_vector
from data_source_pipeline import (
    catalog_fred_series,
    process_cycle_data_sources,
)
from realtime_news_ingest import run as run_realtime_news_ingest
from profitability_foundation import report as profitability_report
from agent_scheduler import bootstrap as bootstrap_schedules
from dual_velocity import DualVelocityExecutor
from market_filters import evaluate as evaluate_market_conditions, session_name
from market_microstructure import MicrostructureMonitor
from event_policy import evaluate as evaluate_event_policy, symbol_currencies
from correlation_risk import CorrelationRisk
from intermarket_regime import (
    build_intermarket_regime_snapshot,
    filter_edges_for_live_regime,
)
from margin_safety import MarginSafetyGate
from daily_reconciliation import compare_day as compare_broker_day
from data_requirements import evaluate as evaluate_data_requirements
from cost_estimator import assess_costs
from feature_schema import validate_snapshot as validate_feature_schema
from model_registry import list_models, register as register_model, review as review_model, drift_report as model_drift_report
from research_stress import stress_report
from contextual_bandit import bandit_report
from semantic_memory import search as semantic_memory_search
from health_alerts import evaluate as evaluate_health_alerts
from alert_outbox import enqueue as enqueue_alerts, claim as claim_alerts, acknowledge as acknowledge_alert, recover_expired as recover_alerts, status as alert_outbox_status, outbox_health as alert_outbox_health
from legacy_reconciliation import report as legacy_reconciliation_report, quarantine as quarantine_legacy_record
from journal_backup import (
    create_backup, verify_backup, restore_backup,
    create_encrypted_backup, verify_encrypted_backup, restore_encrypted_backup,
    prune_backups,
)
from restart_recovery import smoke as restart_recovery_smoke
from journal_scope import status as journal_scope_status, ensure_scope
from historical_regimes import research_status as historical_research_status
from macro_extraction import status as macro_extraction_status
from model_family_gate import evaluation_plan as model_family_evaluation_plan
from execution_policy import load as load_execution_policy, candidate_for_store as execution_policy_candidate, approve_for_store as approve_execution_policy
from forward_test_report import compare as compare_forward_test
from security_audit import audit as security_audit
from supervised_training import train as train_supervised_baseline
from research_runs import record as record_validation_run, list_runs as list_validation_runs
from live_rollout_gate import status as live_rollout_status, approve as approve_live_rollout, revoke as revoke_live_rollout, require as require_live_rollout, verify_evidence as verify_live_evidence
from sample_gate import report as sample_gate_report
from mt5_auth_diagnostics import report as mt5_auth_report
from readiness_report import build as readiness_report
from incident_drills import run as run_incident_drills
from research_influence_gate import allows as research_influence_allows, status as research_influence_status, approve as approve_research_influence, revoke as revoke_research_influence, evidence_fingerprint as research_evidence_fingerprint
from dashboard_report import build as dashboard_report

try:
    from llm_router import LLMRouter, get_router, reset_router
    LLM_ROUTER_AVAILABLE = True
except ImportError:
    LLM_ROUTER_AVAILABLE = False

from technical_indicators import (
    ema, rsi, macd, atr, wma, hma, adx, compute_indicators
)
from order_execution import (
    PaperBroker, _symbol_tick, _broker_market_filling_mode,
    validate_order_params, validate_pending_entry_geometry,
    place_market_order, emergency_market_exit, place_pending_order,
    place_urgency_routed_order,
    modify_order_sltp, cancel_pending_order
)
from market_data_provider import (
    TIMEFRAME_MAP, tf_const, fetch_ohlc, collect_calendar_event_rows,
    DOMCollector, PerformanceMonitor, MarketDataProvider, ohlc_records
)

# ── Blind spot modules (wired in from audit) ─────────────────────────────────
try:
    from core.news_execution_engine import NewsExecutionEngine
except ImportError:
    NewsExecutionEngine = None

try:
    from core.llm_decision_logger import LLMDecisionLogger
except ImportError:
    LLMDecisionLogger = None

try:
    from core.live_health_monitor import LiveHealthMonitor
except ImportError:
    LiveHealthMonitor = None

try:
    from core.liquidity_vacuum_detector import LiquidityVacuumDetector
except ImportError:
    LiquidityVacuumDetector = None

try:
    from core.adv_tracker import ADVTracker
except ImportError:
    ADVTracker = None

def positions_overview() -> List[Dict[str, Any]]:
    mt5_inst = _get_mt5()
    if mt5_inst is None:
        return []
    pos = mt5_inst.positions_get()
    if pos is None:
        return []
    return [p._asdict() for p in pos]

def orders_overview() -> List[Dict[str, Any]]:
    mt5_inst = _get_mt5()
    if mt5_inst is None:
        return []
    ords = mt5_inst.orders_get()
    if ords is None:
        return []
    return [o._asdict() for o in ords]

def preflight_symbols(symbols: List[str]) -> Dict[str, str]:
    mt5_inst = _get_mt5()
    resolved: Dict[str, str] = {}
    if mt5_inst is None:
        return {s: s for s in symbols}
    all_symbols = mt5_inst.symbols_get() or []
    names = [s.name for s in all_symbols]
    for requested in symbols:
        info = mt5_inst.symbol_info(requested)
        name = requested
        if info is None:
            matches = [candidate for candidate in names if candidate.upper().startswith(requested.upper())]
            if len(matches) != 1:
                raise RuntimeError(f"Broker symbol preflight failed for {requested}; matches={matches[:5]}")
            name, info = matches[0], mt5_inst.symbol_info(matches[0])
        if not getattr(info, "visible", True):
            if not mt5_inst.symbol_select(name, True):
                raise RuntimeError(f"Broker symbol preflight could not select {name}")
        resolved[requested] = name
    return resolved

def authenticated_account_fingerprint(mode: str, account_info: Dict[str, Any]) -> str:
    norm_mode = str(mode or "PAPER").strip().upper()
    if norm_mode == "PAPER":
        return "PAPER:local"
    if not isinstance(account_info, dict):
        raise ValueError("live account fingerprint requires dictionary payload")
    login = str(account_info.get("login") or "").strip()
    server = str(account_info.get("server") or "").strip()
    if not login or not server:
        raise ValueError("live account fingerprint requires login and server")
    return f"{login}|{server}"


def stable_content_hash(value: Any) -> str:
    """Deterministic SHA-256 over a canonical JSON serialization."""
    return hashlib.sha256(
        _json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def setup_registry_fingerprint() -> str:
    """Content hash of every registered setup specification.

    Journal version ids must rotate when setup rules change; hashing the
    literal registry label silently hid rule edits from replay evidence.
    """
    try:
        exported = {str(name): asdict(spec) for name, spec in SETUPS.items()}
    except Exception:
        # Fail visible rather than emitting a stale constant fingerprint.
        raise RuntimeError("setup registry could not be serialized for versioning") from None
    return stable_content_hash(exported)


SIGNAL_SYSTEM_PROMPT_TEMPLATE = (
    "You are a World-Class Hedge Fund Trader & Risk Manager (Macro/Quant). "
    "Your goal is to analyze the provided Market Data (TOON) and output a deterministic JSON decision.\n\n"
    "CRITICAL MANDATE: You MUST output ONLY a valid JSON object starting immediately with '{' and ending with '}'. "
    "Never output preliminary conversational reasoning, preambles, or explanations outside the JSON object. "
    "All analysis, thinking, and step-by-step reasoning must be contained exclusively within the JSON fields ('reasoning', 'self_reflection', 'rationale').\n\n"

    "### ⚔️ THE ALPHA PROTOCOL (TEMPERAMENT & DISCIPLINE)\n"
    "You must embody the following traits of a winning system:\n"
    "1. **Capital Preservation First**: It is better to miss a win than suffer a loss. Do not chase.\n"
    "2. **Evidence-Based**: Trends are real, but Support & Resistance are the battlegrounds. Respect them.\n"
    "3. **Invalidation is Mandatory**: You cannot enter a trade without knowing exactly where you are WRONG.\n\n"

    "### 🏆 BENCHMARK THINKING PATTERNS (EMULATE THESE)\n"
    "Use these archetypes to guide your reasoning:\n"
    "- **The Disciplined Swing Trader**: 'Price is at resistance. FOMO is high. Structure suggests pullback. I will NOT chase. Waiting for 210.80 retest.'\n"
    "- **The Ruthless Risk Manager**: 'Setup is valid (Morning Star), but Stop Loss > 1% (45 pips). R:R is < 2.0. Mathematically negative expectancy. TRADE REJECTED.'\n"
    "- **The Confirmation Hunter**: 'Price is breaking 150.00. Is this a fakeout? I need an M15 candle CLOSE above 150.10 to confirm liquidity grab is over.'\n\n"

    "### 🧠 DEEP THINKING & ANALYSIS PROTOCOL\n"
    "You must follow this strict reasoning process before deciding:\n\n"
    "1. **Account Health (Silent Check)**:\n"
    "   - Check 'account' (Balance, Equity, Margin) internally. ONLY mention it in reasoning if Drawdown > 5% or Margin < 150%.\n"
    "   - If account evidence is missing, stale, or malformed, mark it UNKNOWN and choose HOLD; never assume the account is healthy.\n\n"

    "2. **Open Position Review (CRITICAL)**:\n"
    "   - Review 'open_positions'. For EACH position:\n"
    "   - Is Profit > 1.5R? Consider BREAKEVEN to secure risk.\n"
    "   - Is Profit > 2.0R? Consider PARTIAL_CLOSE (50%) to bank profit.\n"
    "   - Has the Technical Thesis invalidated? (e.g., Price broke key support)? Consider CLOSE_FULL.\n\n"

    "3. **Market Structure & S/R Mapping**:\n"
    "   - Identify the D1/H4 Trend (Bullish/Bearish/Ranging).\n"
    "   - **CRITICAL**: Identify Key Support & Resistance Levels. Where is the liquidity?\n"
    "   - Are we at a Key Level? (Bounce candidates) or in the middle of nowhere? (No Trade Zone).\n\n"

    "4. **Comprehensive Indicator & Price Action Analysis**:\n"
    "   - **PRIMARY SIGNALS** (leading, trust these for direction):\n"
    "     - HMA(21) direction: price above HMA = bullish, below = bearish. HMA halves lag vs EMA.\n"
    "     - Kalman state: spread_z > 2 = mean-reversion, drift direction = trend.\n"
    "     - Alpha ensemble composite: >1.0 = strong bullish, <-1.0 = strong bearish.\n"
    "     - BOCPD: no recent structural shift = stable regime. shift detected = HOLD until confirmed.\n"
    "   - **Trend Strength (ADX)**: If ADX < 20, Market is RANGING. Buy Support/Sell Res. NOT Breakouts.\n"
    "   - **Session Context**: Respect the Session! Don't trade breakouts in ASIAN session (Fakeout risk).\n"
    "   - Analyze M15/M5 Price Action (Candles, Momentum) & DOM Imbalances.\n"
    "   - **MOMENTUM RULE**: If HMA is declining AND Kalman drift is negative AND alpha ensemble < -0.5, you are FORBIDDEN from Buying until a Reversal Candle (Hammer/Engulfing) prints on M5/M15. Do not catch a falling knife.\n"
    "   - **Alpha Factors** (if alpha_factors in context):\n"
    "     012=vol-price divergence, 053=overbought/oversold, 055=stochastic+volume,\n"
    "     034=vol compression(breakout imminent), 009=momentum flip, 019=mean reversion, 036=composite.\n"
    "     Strong signal: |value| > 2× mean. Ensemble composite (if available): HIGH + aligned = boost, HIGH + contradicts = HALT.\n"
    "   - **RULE**: 3+ alphas agreeing + HMA/Kalman alignment = boost. Alphas contradict HMA/Kalman = reduce/HOLD.\n"
    "   - **RSI/MACD are ADVISORY ONLY** (lagging, 3-5h delay on M15). Use as secondary filter: RSI<30/70 or MACD divergence can veto, never trigger.\n\n"

    "4b. **Regime Allocation** (if regime_allocation in context): HARD GATE.\n"
    "   is_tradable=false = HOLD mandatory. risk_multiplier < 1.0 = reduce size. Master EV < 0.20R = skip.\n"
    "   CRISIS = ALWAYS HOLD. Aligned regime + ensemble + HMA/Kalman = MAXIMUM conviction.\n\n"

    "4c. **Regime Duration** (if regime_duration in context):\n"
    "   dwell < 50% avg = FRESH (less reliable). dwell > 150% avg = MATURE (may reverse).\n"
    "   stability > 0.8 = stable (trend edge). stability < 0.3 = chaotic (prefer HOLD).\n\n"

    "4d. **Auto-Tools** (if auto_quant_tools in context, ADVISORY only):\n"
    "   CPCV overfitting = reduce conviction. Kalman z>2 = mean-reversion. BOCPD shift = regime change.\n\n"

    "4e. **CPCV Escalation** (if cpcv_escalation in context): HARD GATE.\n"
    "   position_reduction_recommended=true = multiply position size by recommended_position_scale.\n"
    "   scale=0.0 = HOLD or MANAGE_ONLY only. Do NOT override this reduction.\n\n"

    "4f. **Advanced Auto-Tools** (wired into context by _apply_advanced_auto_tools):\n"
    "   meta_adaptive_override: risk_multiplier applied to risk_per_trade. transition_detected=true = regime just changed.\n"
    "   evolutionary_suggestions: Best gene params. Use sl_atr_mult/tp_atr_mult for SL/TP sizing.\n"
    "   cross_market_robustness: composite_score>0.7 = robust. <0.4 = fragile (HOLD bias active).\n"
    "   cross_market_hold_bias=true = CROSS-MARKET GATE ACTIVE, consider HOLD unless 5+ confluence factors.\n"
    "   alpha_discovery_enrichment: New alphas available. validated_count>0 = consider new alpha signals.\n"
    "   multimodal_patterns: confluence_score>0.7 = strong pattern alignment. direction matches your trade?\n"
    "   self_improvement_entry_threshold: Updated entry confidence if self-improvement applied.\n"
    "   crash_detector_signal: Logic-Q neuro-symbolic trend (RAPID_DESCEND/STEADY_DESCEND/RAPID_ASCEND/STEADY_ASCEND/OSCILLATION).\n"
    "   crash_detector_hold_bias=true = CRASH GATE ACTIVE, all entries blocked.\n"
    "   causal_filter_report: CRL-ABSO causal analysis. spurious_features = DON'T trust these indicators.\n"
    "   causal_density<0.3 = most features are spurious, reduce conviction. confounders = variables driving false signals.\n"
    "   walk_forward_decay: Rolling IC analysis. status=HEALTHY/WATCH/DECAYING/DEAD. current_ic shows signal quality.\n"
    "   walk_forward_decay.status=DEAD = WALK-FORWARD GATE ACTIVE, all entries blocked (alpha is harmful).\n"
    "   walk_forward_decay.status=DECAYING = reduce conviction, consider alpha rotation. half_life shows urgency.\n"
    "   walk_forward_hold_bias=true = WALK-FORWARD GATE ACTIVE, HOLD mandatory.\n"
    "   optimal_execution_cost: Almgren-Chriss execution scheduling. algorithm=TWAP/VWAP. total_expected_cost shows slippage.\n"
    "   execution_cost_hold_bias=true = EXECUTION COST GATE ACTIVE, costs exceed expected edge, HOLD mandatory.\n"
    "   cost_aware_position_scale<1.0 = reduce position size proportionally.\n"
    "   ewc_forgetting_status: EWC catastrophic forgetting prevention. snapshots_count = number of regime snapshots protecting Q-values.\n"
    "   ewc_forgetting_status.total_states_protected > 0 = RL agent has regime-protected knowledge.\n"
    "   ewc_forgetting_status.average_importance > 0.5 = Q-values heavily depend on past regimes (high protection).\n"
    "   advanced_auto_tools_summary: Quick status of all modules.\n\n"

    "   **Quant Models** (12 retail quant models, ADVISORY only):\n"
    "   cusum: n_breaches>0 = drift detected, allocation_multiplier=0.5 (half size).\n"
    "   sprt: decision=KILL = strategy has no edge, HOLD or reduce. CONTINUE = edge confirmed.\n"
    "   bayesian: confidence_level=NO_EDGE = reduce size. position_shrinkage<1.0 = shrink.\n"
    "   sentiment_veto: veto_trade=true = skip this trade. fade_signal=true = contrarian.\n"
    "   account_sizing: forced_overbetting=true = increase account or reduce lot size.\n"
    "   session_spread: is_high_cost_session=true = reduce size or avoid. AVOID = skip entries.\n"
    "   ou_time_stop: is_stagnant=true = close position (failed to mean-revert).\n"
    "   portfolio_decorr: is_well_diversified=false = correlation risk elevated.\n"
    "   alpha_capacity: is_viable=false = strategy near capacity, reduce sizing.\n"
    "   corr_breakdown: is_breakdown=true = correlation regime shift, reduce heat.\n"
    "   retail_friction: is_profitable_after_costs=false = strategy unprofitable after costs.\n"

    "   **Wired Research Modules** (from previously-isolated modules, now live):\n"
    "   alpha_stability: Per-alpha robustness grades (A-F). fragile_alphas should be demoted from ensemble.\n"
    "   canonical_mastery: Soros reflexivity RDI>1.0 = market overshooting. Minsky FFR>0.7 = fragility HIGH.\n"
    "   canonical_mastery.antifragility.convexity=ANTIFRAGILE = position benefits from volatility.\n"
    "   rl_analytics: RL Q-table training progress. n_states>100 = well-explored.\n"
    "   grpo_train: GRPO proposer offline training. mean_group_reward>0 = proposer improving. execution_authority=False (advisory only).\n"
    "   ppo_train: PPO position-management training. mean_episode_reward>0 = manager improving. execution_authority=False (advisory only).\n"
    "   regime_autocorr: Regime-aware autocorrelation. regime_transition_detected=true = microstructure shift. microstructure_risk=HIGH = reduce conviction.\n"
    "   meta_loop: Meta-learning Loop 4. proposals_applied>0 = parameter changes deployed. patterns_found>0 = learning from trade history.\n\n"

    "   news_trading_context: News trading analysis. gate.phase = CLEAR/PRE_NEWS/SPIKE_BLACKOUT/POST_NEWS_ENTRY/TREND_CONTINUATION.\n"
    "   news_trading_context.gate.allowed=false = NEWS GATE ACTIVE, all entries blocked, HOLD mandatory.\n"
    "   news_trading_context.esi = Economic Surprise Index. esi_score>55 = hawkish bias, <30 = dovish risk.\n"
    "   news_gate.phase=SPIKE_BLACKOUT = extreme volatility, NO ENTRIES under any circumstances.\n"
    "   news_gate.phase=PRE_NEWS = event imminent, cancel pending orders, do not open new positions.\n"
    "   news_gate.phase=POST_NEWS_ENTRY = optimal entry zone, look for Fibonacci pullback.\n"
    "   **RULE**: If news_gate.phase is SPIKE_BLACKOUT or PRE_NEWS, decision MUST be HOLD.\n\n"

    "4g. **Alpha Mining Intelligence** (if alpha_mining_results in context):\n"
    "   active_alpha_strategies: List of active alpha strategies discovered by hierarchical GA.\n"
    "   mega_alpha_composite: Aggregated alpha signal. >0 = bullish, <0 = bearish.\n"
    "   alpha_diversity_score: 0-1, higher = more diverse alpha portfolio (reduces correlation risk).\n"
    "   alpha_decay_alerts: Alphas with declining IC. If alert exists, reduce conviction in that alpha.\n"
    "   microstructure: FVG/KAT/Spoofing/RL/Pairs/Breakout signals if available.\n"
    "   - fvg: Fair Value Gaps (strong=low degree = higher retest probability).\n"
    "   - kat: KAT absorption events (institutional pinning traps).\n"
    "   - spoofing: OIR-based spoofing detection.\n"
    "   - rl_signal: RL agent action (BUY/SELL/HOLD with confidence).\n"
    "   - pairs: Cointegrated pairs with optimized entry/exit parameters.\n"
    "   - breakout: 4x volume spike breakout signals.\n"
    "   - walk_forward: Walk-forward validation robustness score.\n"
    "   **RULE**: 3+ active alphas + high diversity (>0.7) + mega_alpha aligned with your analysis = STRONG CONFLUENCE.\n"
    "   **RULE**: alpha_decay_alerts present = HALT any new entries based on decaying alphas.\n"
    "   **RULE**: total_active_alphas < 3 = alpha mining in early stage, use existing ensemble only.\n"
    "   **RULE**: KAT absorption detected = AVOID new entries (institutional manipulation likely).\n"
    "   **RULE**: Strong FVG nearby = potential retest zone, set alerts.\n"
    "   **RULE**: RL agent CONFIDENCE > 0.7 + aligned with your analysis = extra confluence weight.\n\n"

    "5. **Confluence Validation (The 'Why')**:\n"
    "   - Identify at least 3 distinct factors aligning for the trade.\n"
    "   - **One factor MUST be 'Local Price Action Confirmation'** (Green candle on M5/M15).\n"
    "   - If < 3 factors align, the decision MUST be HOLD.\n\n"

    "6. **Bad Habit Check (Self-Correction)**:\n"
    "   - Ask yourself: 'Am I chasing this candle?' 'Is this a revenge trade?'\n"
    "   - 'Am I buying a RED candle expecting it to flip?' (Knife Catching). If YES -> ABORT.\n"
    "   - If YES to any -> ABORT (HOLD).\n\n"

    "7. **Risk & Trade Plan**:\n"
    "   - Calculate R:R (Reward-to-Risk). Must be > 2.0.\n"
    "   - Define precise Entry, Stop Loss, and Take Profit.\n"
    "   - **Define Invalidation Price**: The exact price level that proves your thesis wrong.\n\n"

    "### Output Schema (STRICT JSON ONLY)\n"
    "You must return a SINGLE JSON object. Do NOT include markdown formatting (```json ... ```).\n"
    "Structure:\n"
    "{\n"
    "  \"self_reflection\": \"Critique your thought process. Did you pass the Bad Habit Check?\",\n"
    "  \"reasoning\": \"Start with Market Structure & S/R Levels. Explain the setup clearly.\",\n"
    "  \"key_levels\": { \"support\": [1.0800, 1.0750], \"resistance\": [1.0900, 1.0950] },\n"
    "  \"invalidation_price\": float|null,\n"
    "  \"confluence_explanation\": \"Exact factors in confluence.\",\n"
    "  \"management_decisions\": [\n"
    "    {\n"
    "      \"ticket\": 123456,\n"
    "      \"action\": \"UPDATE_SL\" | \"PARTIAL_CLOSE\" | \"CLOSE_FULL\" | \"HOLD\",\n"
    "      \"params\": { \"new_sl\": 1.1000, \"percentage\": 0.5 },\n"
    "      \"reasoning\": \"ADVISORY ONLY: suggest BE if price > 1.5R. Deterministic PositionManager executes; you cannot place or modify orders.\"\n"
    "    }\n"
    "  ],\n"
    "  NOTE: management_decisions are journaled suggestions only. Never assume they execute.\n"
    "  \"rationale\": {\n"
    "    \"technical\": \"Technical thesis (S/R, Indicators).\",\n"
    "    \"fundamental\": \"News/Macro analysis.\",\n"
    "    \"risk\": \"Risk assessment.\",\n"
    "    \"ensemble_alignment\": \"Does your analysis agree or disagree with the alpha ensemble composite signal? HIGH/MEDIUM/LOW confidence.\",\n"
    "    \"alpha_mining_alignment\": \"Does your analysis agree with the alpha mining mega-alpha composite? Use alpha_mining_results if available.\"\n"
    "  },\n"
    "  \"action\": \"BUY\"|\"SELL\"|\"HOLD\"| \"MANAGE_ONLY\",\n"
    "  \"confidence\": 0.0-1.0,\n"
    "  \"entry\": float|null,\n"
    "  \"stop_loss\": float|null,\n"
    "  \"take_profit\": float|null,\n"
    "  \"position_size\": float|null,\n"
    "  \"waiting_for\": \"...\",\n"
    "  \"conditional_scenarios\": [ { \"condition\": \"Price breaks 1.0900\", \"action\": \"BUY BREAKOUT\" } ]\n"
    "  NOTE: conditional_scenarios are journaled suggestions only. Never assume they execute.\n"
    "}\n"
)


class LLMOrchestrator:
    def __init__(self, client: MT5Client, risk_manager: RiskManager, 
                 perf_monitor: PerformanceMonitor, api_key: str, model_name: str = None,
                 enable_rl: bool = True, llm_router=None, agent_name: str = "trader",
                 journal_mode: Optional[str] = None, account_fingerprint: str = "",
                 store: Any = None, bounded_autonomy: bool = False):
        self.client = client
        self.risk = risk_manager
        self.perf = perf_monitor
        self.data_provider = MarketDataProvider(client, risk_manager, api_key)
        if str(journal_mode or "").upper() == "PAPER":
            # Paper sessions perform no authenticated MT5 login (client is
            # often None), so account evidence is legitimately absent and
            # the decision protocol deadlocks every cycle on "missing
            # account evidence -> HOLD". Enable the honestly-labeled
            # PAPER_SIMULATED backstop in the provider, sourced from the
            # same seeded RiskManager state the sizing/heat math already
            # uses. DEMO/LIVE never enable it: missing account evidence
            # there must keep failing closed.
            self.data_provider.enable_paper_account_evidence()
            log.info("Paper account evidence enabled (PAPER_SIMULATED backstop, seeded from RiskManager)")
        # --- Ablation harness (PU-0156 §2): env-gated component removal ---
        # PU_ABLATION is a comma-list read ONCE at boot (per-component
        # measurement runs, never production config). Supported modes:
        #   no_llm   - deterministic HOLD before any LLM reservation
        #   no_news  - news trading gate bypassed (events still fetched)
        #   no_macro - macro context keys stripped from the decision payload
        _abl_raw = os.getenv("PU_ABLATION", "")
        _abl_supported = {"no_llm", "no_news", "no_macro"}
        self.ablation_modes = tuple(sorted(
            m.strip().lower() for m in _abl_raw.split(",")
            if m.strip().lower() in _abl_supported))
        self._ablation_boot_emitted = False
        # Use the caller-provided store (single source of truth from plugins)
        # or create a scoped one.  The plugin path always passes a store;
        # the legacy fallback creates its own.
        if store is not None:
            self.store = store
        else:
            store_cls = _resolve_symbol("TradingStore", TradingStore)
            self.store = store_cls(mode=journal_mode, account_fingerprint=account_fingerprint)
        if getattr(self.data_provider, "trade_manager", None) is not None:
            # Bind journal + portfolio arbiter so pyramid/scale-in cannot bypass
            # the same open-risk gates used by new entries.
            self.data_provider.trade_manager.attach_safety(
                store=self.store, arbiter=None,
            )
            # Arbiter is created below; re-attach after construction.
        self.microstructure = MicrostructureMonitor(self.store)
        self.trade_memory = TradingMemory(self.store.path)
        # Keep macro/correlation state on the same scoped journal path as trades.
        journal_db = str(getattr(self.store, "path", None) or "trading_state.db")
        self.macro_graph = MacroGraph(journal_db)
        self._reflection_graph = None
        self.causal_validator = AutonomousCausalValidator(self.macro_graph)
        self.causal_setup_analyzer = CausalChainSetupAnalyzer(self.macro_graph)
        self.heartbeat_tracker = HeartbeatTracker()
        self.slippage_calibrator = SlippageCalibrator()
        try:
            self.slippage_calibrator.load_state(self.store.get_state("slippage_calibrator", {}) or {})
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug("Slippage calibrator state load failed: %s", exc)
        SmithPredictorEngine.set_calibrator(self.slippage_calibrator)
        self.adaptive_research = AdaptiveResearchService(self.store)
        self.correlation_risk = CorrelationRisk(journal_db)
        self.margin_safety = MarginSafetyGate()
        self.data_quality = DataQualityGate()
        # Overfit gate: blocks strategies with HIGH/CRITICAL overfit risk from live
        try:
            from core.overfit_gate import OverfitGate
            self.overfit_gate = OverfitGate()
        except Exception as _ofg_exc:
            logging.getLogger("LLMOrchestrator").debug("OverfitGate init skipped: %s", _ofg_exc)
            self.overfit_gate = None
        self.provider_circuit = ProviderCircuitBreaker(self.store)
        # Rule-based position management (replaces LLM suggestions)
        try:
            from core.position_manager import PositionManager, PositionManagerConfig
            self.position_manager = PositionManager(PositionManagerConfig())
        except Exception as _pm_exc:
            logging.getLogger("LLMOrchestrator").debug("PositionManager init skipped: %s", _pm_exc)
            self.position_manager = None
        # Modular institutional quantitative metrics service
        try:
            from core.quantitative_metrics_service import QuantitativeMetricsService
            init_bal = getattr(risk_manager, "initial_balance", 0.0) if risk_manager else 0.0
            self.metrics_service = QuantitativeMetricsService(store=self.store, initial_balance=init_bal)
        except Exception as _qms_exc:
            logging.getLogger("LLMOrchestrator").debug("QuantitativeMetricsService init skipped: %s", _qms_exc)
            self.metrics_service = None
        # Institutional Quantitative & Algorithmic Workflows Engine
        try:
            from core.harness.workflows import QuantWorkflowEngine
            self.quant_workflow_engine = QuantWorkflowEngine()
        except Exception as _qwe_exc:
            logging.getLogger("LLMOrchestrator").debug("QuantWorkflowEngine init skipped: %s", _qwe_exc)
            self.quant_workflow_engine = None
        # Operational state must share the scoped authoritative journal. A
        # separate default-path database would split heartbeats, task leases,
        # LLM telemetry, and memory from the trade record after a restart.
        self.operations = AgentOperations(self.store.path)
        bootstrap_schedules(self.operations)
        # Idempotent Economic Dynamics catalog seed (CANDIDATE only).
        try:
            from economic_dynamics_seed import seed_economic_dynamics
            seed_result = seed_economic_dynamics(graph=self.macro_graph)
            self.store.set_state("economic_dynamics_seed", {
                **{k: seed_result.get(k) for k in (
                    "catalog_version", "created_count", "skipped_duplicates",
                    "source_id", "status", "execution_authority",
                )},
                "seeded_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as seed_exc:
            logging.getLogger("LLMOrchestrator").warning(
                "Economic dynamics seed skipped/failed: %s", seed_exc,
            )
            try:
                self.store.set_state("economic_dynamics_seed", {
                    "status": "ERROR",
                    "error": str(seed_exc)[:400],
                    "execution_authority": False,
                })
            except Exception as exc:
                logging.getLogger("LLMOrchestrator").debug("Supervisor seed event failed: %s", exc)
        # Slow memory/research jobs run outside the market-pulse call stack.
        # Durable task claims remain authoritative; this executor only provides
        # bounded non-blocking dispatch and never has order authority.
        self.velocity = DualVelocityExecutor(max_pending_fast=16, max_pending_slow=4)
        # Schedule-batch single-flight state (see _run_schedules_bounded):
        # at most one maintenance batch runs at any time; pulses that arrive
        # while a batch is running skip dispatch instead of stacking threads.
        self._schedule_batch_running = False
        self._schedule_thread_lock = threading.Lock()
        # Per-handler overrun telemetry for the schedule batch (see
        # _process_due_schedules): rolling per-schedule counts and worst/total
        # overrun seconds, so budget tuning uses measured evidence instead of
        # one-off log greps. Purely observational — no behavior change.
        self._schedule_overrun_stats = {}
        # Session-skip watchdog state (see _process_due_schedules): last
        # DISPATCH attempt per schedule name (monotonic seconds), the set of
        # schedules already flagged as skipped this episode, and their last
        # alert monotonic timestamps. Populated lazily on first batch.
        self._schedule_last_dispatch: Dict[str, float] = {}
        self._schedule_skip_alerted: Dict[str, float] = {}
        self._schedule_watchdog_ready = False
        self.run_id = self.operations.start_run(agent_name, payload={"model": model_name or "default"})
        _journal_path = getattr(self.store, "path", "trading_state.db")
        self.validator = DeterministicValidator(
            self.store,
            lifecycle_gate=lambda setup_id: strategy_lifecycle_block(_journal_path, setup_id),
            min_net_ev_r=0.2,  # Require net expectancy >= +0.2R after friction
        )
        self.arbiter = PortfolioRiskArbiter(self.store)
        # P8: Portfolio allocation layer — dormant engines wired as a
        # deterministic, reduction-only size cap consulted AFTER validate
        # (per AUTONOMY_MASTER_PLAN §6). These never add risk and never
        # grant order permission; the arbiter remains the sole authority.
        try:
            from core.hierarchical_portfolio import HierarchicalMetaPortfolioEngine
            self.portfolio_allocator = HierarchicalMetaPortfolioEngine(
                base_risk_pct=float(getattr(risk_manager, "risk_per_trade", 0.01)) * 100.0,
            )
        except Exception as _alloc_exc:
            logging.getLogger("LLMOrchestrator").debug("Portfolio allocator init skipped: %s", _alloc_exc)
            self.portfolio_allocator = None
        try:
            from operations.strategy_factory import StrategyFactory
            self.strategy_factory = StrategyFactory()
        except Exception as _sf_exc:
            logging.getLogger("LLMOrchestrator").debug("Strategy factory init skipped: %s", _sf_exc)
            self.strategy_factory = None
        # κ Compliance Audit: hash-chained immutable log of all state transitions
        try:
            from core.compliance_audit import get_audit
            self._audit = get_audit()
        except Exception:
            self._audit = None
        # Growth controller may only reduce risk vs operator base; never grants
        # execution authority and never bypasses PortfolioRiskArbiter.
        self.growth_controller = AccountGrowthController(
            self.store, base_risk=float(getattr(self.risk, "risk_per_trade", 0.01) or 0.01),
        )
        # NOTE: TradeManager safety attachment is deferred to wire_trade_manager()
        # which is called by mt5_trader.py after the orchestrator is constructed.
        # 2026-10-01: mistralai/mistral-7b-instruct-v0.2 was decommissioned
        # upstream (NVIDIA 404/410-Gone, OpenRouter 404 no-endpoints) and its
        # dead slug overrode the correct .env model in every boot that passed
        # --model explicitly. meta/llama-3.2-11b-vision-instruct is the .env
        # default and verified live against the gateway.
        self.model_name = model_name or "meta/llama-3.2-11b-vision-instruct"
        self.agent_name = agent_name
        
        # Multi-LLM Router Integration
        self._llm_router = llm_router
        self._agent_name = agent_name
        if llm_router:
            class _RouterLLMAdapter:
                def __init__(self, router, agent_name: str, model_name: str):
                    self.router = router
                    self.agent_name = agent_name
                    self.model_name = model_name
                def generate(self, messages: list, model: str = None, **kwargs) -> str:
                    return self.router.generate(
                        agent=self.agent_name,
                        messages=messages,
                        model=model or self.model_name,
                        **kwargs
                    )
                def generate_with_tools(self, messages: list, tools: list, tool_executor, model: str = None, **kwargs) -> str:
                    return self.router.generate_with_tools(
                        agent=self.agent_name,
                        messages=messages,
                        tools=tools,
                        tool_executor=tool_executor,
                        model=model or self.model_name,
                        **kwargs
                    )
            self.llm = _RouterLLMAdapter(llm_router, agent_name, self.model_name)
            logging.getLogger("LLMOrchestrator").info(f"Using LLM Router | agent={agent_name} | model={self.model_name}")
        else:
            self.llm = self._create_legacy_llm(api_key, self.model_name)
        
        # RL Training
        self.enable_rl = enable_rl
        # RL is research-only by default.  It may inspect simulated/paper
        # episodes, but cannot learn from or influence a real broker run until
        # an operator explicitly enables an offline training job.
        self.rl_offline_updates_enabled = os.getenv("TRADING_RL_ENABLE_UPDATES", "false").lower() in {"1", "true", "yes"}
        self._research_influence_cache: Dict[str, Tuple[float, bool]] = {}

        self.rl_trainer = None
        self.rl_state_history = []
        
        if enable_rl:
            try:
                from rl_trainer import RLTrainer
                self.rl_trainer = RLTrainer()
            except ImportError as e:
                logging.getLogger("LLMOrchestrator").warning(f"RL trainer not available: {e}")
                self.enable_rl = False

        # Kronos is advisory context only.  It never sets execution_authority and
        # cannot bypass DeterministicValidator / PortfolioRiskArbiter.
        self.enable_kronos_advisory = os.getenv("TRADING_KRONOS_ADVISORY", "true").lower() in {
            "1", "true", "yes",
        }
        self.kronos_integrator = None
        if self.enable_kronos_advisory:
            try:
                from kronos_trading_integrator import KronosTradingIntegrator
                self.kronos_integrator = KronosTradingIntegrator(
                    model_key=os.getenv("TRADING_KRONOS_MODEL", "kronos-mini"),
                )
                logging.getLogger("LLMOrchestrator").info(
                    "Kronos advisory enabled | backend=%s | weights_loaded=%s",
                    getattr(self.kronos_integrator.predictor, "backend", "unknown"),
                    getattr(self.kronos_integrator.predictor, "weights_loaded", False),
                )
            except Exception as e:
                logging.getLogger("LLMOrchestrator").warning(
                    "Kronos advisory unavailable: %s", e
                )
                self.enable_kronos_advisory = False

        # A closed journaled trade is the only source allowed to update RL.
        # The learner remains advisory; it never bypasses the deterministic gate.
        self.perf.rl_update_callback = self.update_rl_from_trade
        self._rl_updated_tickets = set()

        # Memory / Context Buffer - one per strategy, not one per orchestrator.
        # See _cycle_key: mt5_trader.py runs every strategy cycle in a thread
        # pool against this one object.
        # Note: with LLM_TRADER_COMPACT_CONTEXT=1 (R1 payload audit) the buffer
        # itself keeps only the last decision cycles + short summaries; see
        # brain/memory.py. Unset = legacy full-replay behavior.
        self._cycle_state_lock = threading.Lock()
        self._cycle_local = threading.local()
        self._memory_by_strategy = {}
        self._trace_by_strategy = {}

        # Lesson Distiller — extracts lessons from closed trades
        try:
            from brain.distiller import LessonDistiller
            self.lesson_distiller = LessonDistiller()
        except Exception:
            self.lesson_distiller = None

        # Paper Broker
        try:
            self.paper_broker = PaperBroker()
            if getattr(self.data_provider, "trade_manager", None) is not None:
                self.data_provider.trade_manager.attach_paper_broker(self.paper_broker)
            if hasattr(self.risk, "set_active_positions_provider"):
                self.risk.set_active_positions_provider(lambda: len(getattr(self.paper_broker, "positions", {})))
            if hasattr(self.risk, "set_portfolio_heat_provider"):
                self.risk.set_portfolio_heat_provider(self._calc_paper_portfolio_heat)
        except NameError:
             self.paper_broker = None

        # Bounded-Autonomy Pipeline (NZE, 2026: R ∘ V_LLM ∘ S)
        self.enable_bounded_autonomy = bounded_autonomy
        self.bounded_pipeline = None
        if bounded_autonomy:
            audit_path = os.getenv("BOUNDED_AUTONOMY_AUDIT_LOG", "logs/bounded_autonomy_audit.jsonl")
            self.wire_bounded_autonomy(enabled=True, audit_log_path=audit_path)

    def wire_trade_manager(self, trade_manager) -> None:
        """Wire an external TradeManager to the data provider and paper broker.

        Called by mt5_trader.py after orchestrator construction when the
        TradeManager is created by the harness plugin (BrokerPlugin) rather
        than inside MarketDataProvider.
        """
        if trade_manager is None:
            return
        self.data_provider.set_trade_manager(trade_manager)
        # Attach paper broker if available
        if self.paper_broker is not None:
            try:
                trade_manager.attach_paper_broker(self.paper_broker)
            except Exception as exc:
                logger.warning("Failed to attach paper broker to TradeManager: %s", exc)
        # Attach safety (store + arbiter) if available
        _arbiter = getattr(self, "arbiter", None)
        try:
            trade_manager.attach_safety(store=self.store, arbiter=_arbiter)
        except Exception as exc:
            logger.error("Failed to attach safety arbiter to TradeManager: %s; trades will lack safety validation!", exc)

    def _calc_paper_portfolio_heat(self) -> Optional[float]:
        """Measured open paper portfolio heat in R units.

        Calculates sum of (entry - SL) risk across all open paper positions
        divided by the 1R account risk amount.
        """
        if not self.paper_broker or not getattr(self.paper_broker, "positions", None):
            return 0.0
        equity = float(getattr(self.risk, "balance", 10000.0) or 10000.0)
        risk_per_trade = float(getattr(self.risk, "risk_per_trade", 0.01) or 0.01)
        unit = equity * risk_per_trade
        if unit <= 0:
            return None
        total_loss = 0.0
        for pos in self.paper_broker.positions.values():
            try:
                sl = float(pos.get("sl") or 0.0)
                entry_px = float(pos.get("price_open") or 0.0)
                volume = float(pos.get("volume") or 0.0)
                dist = abs(entry_px - sl) if entry_px > 0 and sl > 0 else 0.0
                if dist <= 0:
                    continue
                symbol = pos.get("symbol", "")
                spec = self.risk._symbol_spec(symbol) if hasattr(self.risk, "_symbol_spec") else None
                if spec and spec.trade_tick_size and spec.trade_tick_value:
                    loss_per_lot = (dist / spec.trade_tick_size) * spec.trade_tick_value
                else:
                    loss_per_lot = dist * 100000.0
                total_loss += loss_per_lot * max(volume, 0.01)
            except Exception:
                continue
        return total_loss / unit

    def wire_bounded_autonomy(
        self,
        enabled: bool = True,
        audit_log_path: Optional[str] = "logs/bounded_autonomy_audit.jsonl",
    ) -> None:
        """Wire Bounded-Autonomy LLM Agent Pipeline (NZE 2026: R ∘ V_LLM ∘ S)."""
        self.enable_bounded_autonomy = enabled
        if not enabled:
            self.bounded_pipeline = None
            return

        from core.bounded_autonomy_pipeline import create_bounded_pipeline

        def _llm_veto_caller(payload: Dict[str, Any]) -> Dict[str, Any]:
            import json
            import re
            prompt = (
                "You are an institutional trading risk veto operator (V_LLM).\n"
                "Evaluate the following deterministic trade candidate:\n"
                f"{json.dumps(payload, separators=(',', ':'))}\n\n"
                "Return ONLY a single valid JSON object with the following fields:\n"
                '{"approved": true or false, "rationale": "reason for decision", "confidence": float between 0.0 and 1.0}'
            )
            try:
                messages = [{"role": "user", "content": prompt}]
                raw = self.llm.generate(messages, model=self.model_name)
                m = re.search(r"\{.*\}", str(raw), re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    return {
                        "approved": bool(parsed.get("approved", False)),
                        "rationale": str(parsed.get("rationale", "LLM verdict")),
                        "confidence": float(parsed.get("confidence", 1.0)),
                    }
            except Exception as exc:
                logging.getLogger("LLMOrchestrator").warning("LLM veto caller failed: %s", exc)

            return {"approved": False, "rationale": "Fail-closed veto on LLM error/timeout", "confidence": 0.0}

        self.bounded_pipeline = create_bounded_pipeline(
            llm_caller=_llm_veto_caller,
            model_name=self.model_name,
            deterministic_validator=getattr(self, "validator", None),
            audit_log_path=audit_log_path,
            fail_closed=True,
        )
        logging.getLogger("LLMOrchestrator").info(
            "Bounded-Autonomy Pipeline wired successfully (audit_log=%s)", audit_log_path
        )

    def _poll_velocity_results(self) -> None:
        """Journal completed background jobs without propagating worker errors."""
        for result in self.velocity.poll(limit=20):
            payload = {
                "lane": result.get("lane"), "key": result.get("key"),
                "status": result.get("status"),
                "latency_seconds": result.get("latency_seconds"),
                "error": result.get("error", ""),
                "execution_authority": False,
            }
            self.operations.event(
                "scheduler", "dual_velocity_job_finished", payload,
                run_id=self.run_id,
                correlation_id=str(result.get("key", "")),
                severity="ERROR" if result.get("status") == "FAILED" else "INFO",
            )

    def shutdown(self) -> None:
        """Stop background lanes without granting or changing execution authority."""
        self.velocity.shutdown(wait=False)

    def _llm_provider_and_budget(self, strategy: str) -> Tuple[str, Dict[str, Any]]:
        """Resolve the configured provider and its local safety budget."""
        provider = "legacy"
        if self._llm_router:
            provider = str(getattr(self._llm_router, "agents", {}).get(self._agent_name, {}).get("provider", "openrouter"))
        prefix = {"openrouter": "OPENROUTER", "cerebras": "CEREBRAS", "nvidia_nim": "NVIDIA", "legacy": "OPENROUTER"}.get(provider, provider.upper().replace("-", "_"))
        strategy_hour = 4 if strategy == "memory" else (30 if strategy == "scalp" else 12)
        rpm = int(os.environ.get(f"{prefix}_RPM", os.environ.get("LLM_DEFAULT_RPM", str(strategy_hour))))
        return provider, {
            "max_per_hour": min(strategy_hour, max(1, rpm)),
            "max_per_day": int(os.environ.get(f"{prefix}_RPD", "24" if strategy == "memory" else "200")),
            "max_concurrent": int(os.environ.get(f"{prefix}_MAX_CONCURRENT", "1")),
            "max_tokens_per_hour": int(os.environ.get(f"{prefix}_MAX_TOKENS_PER_HOUR", "12000" if strategy == "memory" else "120000")),
            "max_cost_per_day": float(os.environ.get(f"{prefix}_MAX_COST_PER_DAY", "2.0" if strategy == "memory" else "10.0")),
            "estimated_tokens": 1500 if strategy == "memory" else 4000,
            "estimated_cost": float(os.environ.get(f"{prefix}_ESTIMATED_COST_PER_REQUEST", "0")),
        }

    @staticmethod
    def _normalize_llm_usage(raw_usage: Any) -> Tuple[int, float, str]:
        """Normalize provider usage without allowing malformed telemetry to
        break request finalization.

        Providers may omit usage entirely, which is represented as zero usage.
        If usage is present but non-finite, negative, fractional-token, or
        otherwise malformed, return a durable error instead of raising during
        completion (which could leave a RESERVED request stuck forever).
        """
        if raw_usage in (None, {}):
            return 0, 0.0, ""
        if not isinstance(raw_usage, dict):
            return 0, 0.0, "provider usage metadata is not an object"
        raw_tokens = raw_usage.get("total_tokens", 0)
        raw_cost = raw_usage.get("cost", 0.0)
        try:
            if isinstance(raw_tokens, bool):
                raise ValueError("token count is boolean")
            tokens_float = float(raw_tokens or 0)
            if not math.isfinite(tokens_float) or tokens_float < 0 or not tokens_float.is_integer():
                raise ValueError("token count is not a finite non-negative integer")
            if isinstance(raw_cost, bool):
                raise ValueError("cost is boolean")
            cost = float(raw_cost or 0.0)
            if not math.isfinite(cost) or cost < 0:
                raise ValueError("cost is not finite and non-negative")
        except (TypeError, ValueError, OverflowError) as exc:
            return 0, 0.0, f"provider usage metadata invalid: {exc}"
        return int(tokens_float), cost, ""

    @staticmethod
    def _llm_trace_outcome(trace: Optional[Dict[str, Any]]) -> Tuple[bool, str, str]:
        """Normalize an LLM trace for durable accounting and circuit health.

        A valid JSON HOLD is a successful provider response; a safety HOLD
        caused by an unavailable, malformed, or otherwise failed response is
        not.  Keeping this decision in one pure helper makes the distinction
        replayable and prevents telemetry from silently treating failures as
        successes.
        """
        trace = trace if isinstance(trace, dict) else {}
        parse_status = str(trace.get("parse_status", "") or "").upper()
        usage_status = str(trace.get("usage_status", "VALID") or "VALID").upper()
        success = parse_status == "SUCCESS" and usage_status == "VALID"
        errors = []
        if parse_status != "SUCCESS":
            errors.append(f"parse_status={parse_status or 'UNKNOWN'}")
        if usage_status != "VALID":
            errors.append(str(trace.get("usage_error", "provider usage metadata invalid")))
        error = "" if success else " ".join(errors + [f"attempts={trace.get('attempts', 0)}"])
        return success, parse_status or "UNKNOWN", error

    def _create_legacy_llm(self, api_key: str, model_name: str):
        is_cerebras = False
        if model_name and (model_name.startswith("cerebras") or "llama" in model_name.lower()):
            try:
                from cerebras_client import CerebrasGenerator
                llm = CerebrasGenerator()
                if llm.client:
                    is_cerebras = True
                    if model_name.startswith("cerebras/"):
                        self.model_name = model_name.split("/", 1)[1]
                    logging.getLogger("LLMOrchestrator").info(f"Using Cerebras API for model: {self.model_name}")
            except ImportError:
                logging.getLogger("LLMOrchestrator").warning("Cerebras client not found. Falling back to OpenRouter.")
        
        if not is_cerebras:
            from generate_with_openrouter import OpenRouterGenerator
            llm = OpenRouterGenerator(api_key=api_key)
        return llm

    def process_pending_memory(self, limit: int = 5) -> Dict[str, int]:
        """Distil verified SQLite records with the same configured LLM provider.

        This is advisory memory work only and is rate limited separately from
        signal proposals. It cannot affect the current order path.
        """
        if not self.operations.pending_memory(1):
            return {"processed": 0, "failed": 0, "pending": 0}
        batch_key = f"memory:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        provider, budget = self._llm_provider_and_budget("memory")
        allowed, request_id = self.operations.reserve_llm(
            strategy="memory", provider=provider,
            model=self.model_name, trigger_key=batch_key, run_id=self.run_id,
            **budget)
        if not allowed:
            return {"processed": 0, "failed": 0, "pending": len(self.operations.pending_memory(limit))}
        started = time.perf_counter()
        try:
            result = process_local_memory_outbox(self.operations, self.trade_memory, self.llm, self.model_name, run_id=self.run_id, limit=limit)
            failures = int(result.get("failed", 0) or 0) + int(result.get("fallback_saved", 0) or 0) \
                + int(result.get("outage_failed", 0) or 0)
            outage_failed = int(result.get("outage_failed", 0) or 0)
            # outage_failed MUST count as a failure here: an all-outage batch
            # used to book success=True/"" (SUCCESS + PROVIDER_OUTAGE + 0 chars
            # in llm_requests), which also hid the outage from the provider
            # circuit. Classified-labeled-but-successful rows are not successes.
            if failures and outage_failed == failures and not result.get("fallback_saved"):
                error = ("memory batch blocked by provider outage; items left queued for retry")
            elif result.get("fallback_saved"):
                error = "memory batch items parked FAILED after retry budget; deterministic fallback narratives saved"
            elif result.get("failed"):
                error = "memory batch contained failed items; left queued for retry"
            else:
                error = ""
            self.operations.finish_llm(
                request_id, success=not failures,
                latency_ms=(time.perf_counter() - started) * 1000,
                prompt_hash=str(result.get("prompt_hash", "")),
                response_hash=str(result.get("response_hash", "")),
                attempt_count=int(result.get("attempt_count", 0) or 0),
                parse_status=str(result.get("parse_status", "")),
                response_chars=int(result.get("response_chars", 0) or 0),
                error=error,
            )
            return result
        except Exception as exc:
            self.operations.finish_llm(
                request_id, success=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                parse_status="ERROR", error=str(exc), attempt_count=0,
            )
            return {"processed": 0, "failed": 1, "pending": len(self.operations.pending_memory(limit))}


    def _get_symbol_keywords(self, symbol: str) -> List[str]:
        """Map symbol to news keywords"""
        keywords = [symbol] # Default
        s = symbol.upper()
        
        if "XAU" in s or "GOLD" in s:
            keywords.extend(["Gold", "XAU", "Federal Reserve", "US Dollar"])
        elif "EURUSD" in s:
            keywords.extend(["EUR", "USD", "ECB", "Federal Reserve"])
        elif "GBP" in s:
            keywords.extend(["GBP", "Bank of England", "UK Economy"])
        elif "JPY" in s:
            keywords.extend(["JPY", "BOJ", "Bank of Japan", "Yen"])
        elif "BTC" in s:
            keywords.extend(["Bitcoin", "Crypto", "SEC"])
        elif "US500" in s or "SPX" in s:
            keywords.extend(["S&P 500", "US Economy", "Stock Market"])
            
        return list(set(keywords))

    def _format_lessons_learned(self) -> str:
        """ICL lessons from APPROVED journal memory (not divergent perf JSON)."""
        try:
            text = self.trade_memory.format_approved_lessons(limit=6)
            if text and text.strip():
                return text
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").warning(
                "Journal lesson formatting failed: %s", exc,
            )
        return "No approved journal lessons yet."

    def _get_knowledge_base_context(self, toon: Dict[str, Any]) -> str:
        """Inject relevant trading_knowledge_base excerpts based on current regime/keywords."""
        try:
            from trading_knowledge_base.context_provider import get_knowledge_base_context
            regime = toon.get("advisory_regime", {}).get("active_regime", "")
            keywords = []
            symbol = toon.get("symbol", "")
            if symbol:
                keywords.append(symbol)
            return get_knowledge_base_context(regime=regime, keywords=keywords, max_chars=800)
        except Exception:
            return ""

    def _get_market_session(self) -> str:
        """Identify current market session (UTC)"""
        return session_name()

    @staticmethod
    def _clean_news_entry(item: Any) -> Dict[str, Any]:
        if not isinstance(item, dict):
            return {"summary": str(item)[:200]}
        summary = item.get("summary") or item.get("description") or item.get("content") or item.get("title") or ""
        return {
            "title": str(item.get("title", ""))[:140],
            "summary": str(summary)[:250],
            "sentiment": item.get("sentiment"),
            "source": str(item.get("source", ""))[:60],
            "time": str(item.get("time") or item.get("published_at") or "")[:30],
        }

    @classmethod
    def _round_floats(cls, obj: Any, precision: int = 5) -> Any:
        """Recursively round floating point values to prevent token bloat."""
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return 0.0
            return round(obj, precision)
        elif isinstance(obj, dict):
            return {k: cls._round_floats(v, precision) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [cls._round_floats(v, precision) for v in obj]
        return obj

    @classmethod
    def _sanitize_toon_lite_budget(cls, toon_lite: Dict[str, Any], max_total_chars: int = 60000) -> Dict[str, Any]:
        """Sanitize and budget toon_lite so prompt serialization never exceeds token limits.

        60,000 chars is ~15,000 tokens, safely within 32k context models (e.g. LLaMA-3.3-70B).
        """
        if not isinstance(toon_lite, dict):
            return toon_lite

        # 0. Compact floating point numbers to institutional 5-decimal precision
        toon_lite = cls._round_floats(toon_lite, precision=5)

        # 1. Clean news arrays
        if "news_events" in toon_lite and isinstance(toon_lite["news_events"], list):
            toon_lite["news_events"] = [cls._clean_news_entry(n) for n in toon_lite["news_events"][:5]]
        if "api_news" in toon_lite and isinstance(toon_lite["api_news"], list):
            toon_lite["api_news"] = [cls._clean_news_entry(n) for n in toon_lite["api_news"][:5]]
        if "hot_topics" in toon_lite and isinstance(toon_lite["hot_topics"], list):
            toon_lite["hot_topics"] = [
                {"topic": str(h.get("topic", ""))[:80], "summary": str(h.get("summary", ""))[:200]}
                if isinstance(h, dict) else str(h)[:100]
                for h in toon_lite["hot_topics"][:3]
            ]
        if "event_playbooks" in toon_lite and isinstance(toon_lite["event_playbooks"], list):
            toon_lite["event_playbooks"] = [
                {k: str(v)[:150] for k, v in ep.items()} if isinstance(ep, dict) else str(ep)[:100]
                for ep in toon_lite["event_playbooks"][:3]
            ]

        # 1b. R5 (payload-flow audit 2026-09-30): collapse the long tail of
        # minor sections into one compact summary blob.  Live payloads carry
        # ~73 sections where 48 of them together contribute only ~3% of
        # characters; each still costs prompt-structure overhead.
        try:
            tail_limit = max(0, int(os.getenv("LLM_TRADER_TAIL_COLLAPSE_CHARS", "600")))
            if tail_limit:
                toon_lite = cls._collapse_tail_sections(toon_lite, tail_limit)
        except Exception:
            pass

        # Check serialized size
        try:
            serialized = json.dumps(toon_lite, default=str)
            if len(serialized) <= max_total_chars:
                return toon_lite
        except Exception:
            return toon_lite

        # 2. Progressively prune non-essential research keys to stay within budget
        prunable_keys = [
            "advanced_auto_tools_summary", "canonical_mastery", "alpha_stability",
            "multimodal_patterns", "evolutionary_suggestions", "meta_adaptive_override",
            "rl_analytics", "grpo_train", "ppo_train", "regime_autocorr", "meta_loop",
            "zero_lag_macro", "causal_filter_report", "scenario_memory", "learned_patterns",
            "reviewed_core_memory", "working_memory", "playbook", "macro_causal_context_regime",
            # R4 (payload-flow audit 2026-09-29): advisory keys observed live but
            # missing from the prunable list.  All advisory-only research
            # context; execution_authority lives in the gated allowlist
            # projections above and is never dropped by pruning.
            "live_advisory_hub", "hierarchical_advisory", "meta_label_advisory",
            "advisory_regime", "quant_audit"
        ]
        for key in prunable_keys:
            if key in toon_lite:
                val = toon_lite[key]
                if isinstance(val, dict):
                    toon_lite[key] = {"status": val.get("status", "TRUNCATED_FOR_BUDGET")}
                elif isinstance(val, list):
                    toon_lite[key] = val[:1]
            # R5: the tail blob sits behind every named research key so it is
            # also dropped first under extreme budget pressure.
            if "minor_context_summary" in toon_lite:
                del toon_lite["minor_context_summary"]
                try:
                    if len(json.dumps(toon_lite, default=str)) <= max_total_chars:
                        break
                except Exception:
                    pass
            try:
                if len(json.dumps(toon_lite, default=str)) <= max_total_chars:
                    break
            except Exception:
                pass

        return toon_lite

    @classmethod
    def _collapse_tail_sections(
        cls,
        toon_lite: Dict[str, Any],
        tail_limit: int = 600,
    ) -> Dict[str, Any]:
        """R5: fold minor sections into one compact summary blob.

        Sections whose serialized form is at most ``tail_limit`` chars — and
        that are neither large nor protected — are removed and represented by
        a single ``minor_context_summary`` entry of ``key: head-slice`` lines
        (one line per section, head clipped to 80 chars).

        Never collapsed:
        - any section already larger than ``tail_limit`` chars (its size is
          the point),
        - any dict with ``execution_authority`` or ``status`` (gated
          projections / advisory state the prompt doctrine references),
        - single-entry payloads (nothing to compact),
        - structural/context keys in ``_TAIL_PROTECTED_KEYS`` (account,
          positions, working memory, knowledge context, journal correlation).
        """
        if not isinstance(toon_lite, dict) or len(toon_lite) <= 1:
            return toon_lite
        collapsed: Dict[str, str] = {}
        for key in list(toon_lite.keys()):
            if key in cls._TAIL_PROTECTED_KEYS:
                continue
            try:
                size = len(json.dumps(toon_lite[key], default=str))
            except Exception:
                continue
            if size > tail_limit:
                continue
            val = toon_lite[key]
            if isinstance(val, dict) and (
                "execution_authority" in val or "status" in val
            ):
                continue
            if isinstance(val, str):
                head = " ".join(val.split())[:80]
            elif isinstance(val, list):
                head = f"list[{len(val)}]: " + " ".join(
                    json.dumps(v, default=str) for v in val[:1]
                )[:72]
            elif isinstance(val, dict):
                head = "; ".join(f"{k}={json.dumps(v, default=str)[:24]}" for k, v in list(val.items())[:3])[:80]
            else:
                head = str(val)[:80]
            collapsed[key] = head
            del toon_lite[key]
        if collapsed:
            toon_lite["minor_context_summary"] = {
                "collapsed_sections": len(collapsed),
                "detail": " | ".join(f"{k}: {v}" for k, v in sorted(collapsed.items())),
            }
        return toon_lite

    _TAIL_PROTECTED_KEYS = frozenset({
        "account", "open_positions", "session_info", "journal_context",
        "working_memory", "reviewed_core_memory", "knowledge_base_context",
        "recent_performance", "multi_timeframe", "indicators",
        "news_events", "api_news", "hot_topics", "event_playbooks",
        "minor_context_summary",
    })

    @classmethod
    def _enforce_request_char_budget(
        cls,
        messages: List[Dict[str, Any]],
        max_total_chars: int = 96000,
    ) -> List[Dict[str, Any]]:
        """R2 (payload-flow audit 2026-09-29): enforce the character budget
        over the WHOLE request — system prompt + replayed history + current
        payload — instead of only the current ``toon_lite``.

        Never touches the first system message (role/doctrine/lessons) or the
        last user message (live market data).  Older history turns are
        halved largest-first until the request fits, and are marked with an
        explicit ``[truncated for request budget]`` note so a truncated turn
        can never be mistaken for a complete one.  Best-effort: returns the
        original list on any serialization problem."""
        if not isinstance(messages, list) or len(messages) <= 2:
            return messages

        def estimate(msgs: List[Dict[str, Any]]) -> Dict[str, int]:
            sizes: Dict[str, int] = {}
            total = 0
            for idx, msg in enumerate(msgs):
                try:
                    length = len(str(msg.get("content", "")))
                except Exception:
                    length = 0
                sizes[str(idx)] = length
                total += length
            sizes["total_chars"] = total
            return sizes

        try:
            sizes = estimate(messages)
            if sizes.get("total_chars", 0) <= max_total_chars:
                return messages

            work = [dict(m) for m in messages]
            first_system = next(
                (i for i, m in enumerate(work) if m.get("role") == "system"), None
            )
            # Indices that may be trimmed: everything except the first system
            # message and the final user message (live data).
            last_user = max(
                (i for i, m in enumerate(work) if m.get("role") == "user"),
                default=None,
            )
            trimmable = [
                i for i in range(len(work))
                if i != first_system and i != last_user and work[i].get("content")
            ]
            if not trimmable:
                return messages

            # Re-measure once so repeated calls don't stack markers.
            sizes = estimate(work)
            marker = "\n[truncated for request budget]"
            while sizes.get("total_chars", 0) > max_total_chars and trimmable:
                biggest_idx = max(trimmable, key=lambda i: sizes[str(i)])
                current_len = sizes[str(biggest_idx)]
                if current_len <= 500:
                    trimmable.remove(biggest_idx)
                    continue
                target = max(240, current_len // 2)
                content = str(work[biggest_idx].get("content", ""))
                base = content.split(marker, 1)[0]
                trimmed = base[:target].rstrip() + marker
                work[biggest_idx]["content"] = trimmed
                sizes["total_chars"] += len(trimmed) - sizes[str(biggest_idx)]
                sizes[str(biggest_idx)] = len(trimmed)
            return work
        except Exception:
            return messages

    @classmethod
    def _enforce_request_ceiling(
        cls,
        messages: List[Dict[str, Any]],
        max_chars: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """PU-0161: prompt-size ceiling guard (compacts before ~100k chars).

        ``_enforce_request_char_budget`` budgets the sum of message *content*
        characters, but the provider receives the JSON-serialized request,
        which runs ~11% larger (every quote inside the embedded market-data
        JSON is escaped).  Session-11 (PU-0160) measured that gap directly:
        the three PARSE_ERRORs shipped at 93.4k-94.7k *content* chars — under
        the 96k content budget, so R2 passed them through untouched — but at
        104k-106k *serialized*.  The 11B model answered those with a constant
        368-char degraded response, while every request serialized at ≤102k
        succeeded.

        This guard closes the gap by measuring what the provider actually
        receives and compacting *before* the ceiling rather than after it.
        Same contract as R2: the first system message (role/doctrine) and the
        final user message (live market data) are never touched; older
        history turns are halved largest-first and carry an explicit marker
        so a truncated turn can never be read as a complete one.

        Ceiling resolution: explicit ``max_chars`` > ``LLM_TRADER_PROMPT_
       _CEILING_CHARS`` > 100_000 (below the 104k failure boundary, above the
        ~95k serialized size of a healthy s11 request)."""
        if not isinstance(messages, list) or len(messages) <= 2:
            return messages
        if max_chars is None:
            try:
                max_chars = int(os.environ.get("LLM_TRADER_PROMPT_CEILING_CHARS", "100000"))
            except (TypeError, ValueError):
                max_chars = 100_000
        if max_chars <= 0:
            return messages

        def _serialized_size(msgs: List[Dict[str, Any]]) -> int:
            try:
                return len(json.dumps(msgs, default=str))
            except Exception:
                return sum(len(str(m.get("content", ""))) for m in msgs)

        try:
            if _serialized_size(messages) <= max_chars:
                return messages

            work = [dict(m) for m in messages]
            first_system = next(
                (i for i, m in enumerate(work) if m.get("role") == "system"), None
            )
            last_user = max(
                (i for i, m in enumerate(work) if m.get("role") == "user"),
                default=None,
            )
            trimmable = [
                i for i in range(len(work))
                if i != first_system and i != last_user and work[i].get("content")
            ]
            if not trimmable:
                return messages

            marker = "\n[truncated for request budget]"
            log = logging.getLogger("LLMOrchestrator")
            before = _serialized_size(work)
            # Re-measure per pass so repeated calls never stack markers.
            while _serialized_size(work) > max_chars and trimmable:
                lengths = {
                    i: len(str(work[i].get("content", ""))) for i in trimmable
                }
                biggest_idx = max(lengths, key=lambda i: lengths[i])
                current_len = lengths[biggest_idx]
                if current_len <= 500:
                    trimmable.remove(biggest_idx)
                    continue
                target = max(240, current_len // 2)
                content = str(work[biggest_idx].get("content", ""))
                base = content.split(marker, 1)[0]
                work[biggest_idx]["content"] = base[:target].rstrip() + marker

            after = _serialized_size(work)
            if after > max_chars:
                log.warning(
                    "Prompt ceiling %d could not be met (%d -> %d chars); "
                    "only the system prompt and live payload remain",
                    max_chars, before, after,
                )
            elif after < before:
                log.info(
                    "Prompt ceiling applied: %d -> %d chars (cap %d); "
                    "history turns compacted, system + live payload intact",
                    before, after, max_chars,
                )
            return work
        except Exception:
            return messages

    def get_decision(self, toon: Dict[str, Any]) -> Dict[str, Any]:
        # Real LLM Call
        import json
        
        # Extract recent trades for reflection
        recent_trades = []
        if self.perf and self.perf.history:
            recent_trades = [
                {
                    "action": t.get("action"), 
                    "pnl": t.get("pnl", 0), 
                    "symbol": t.get("symbol"), 
                    "result": "WIN" if t.get("pnl", 0) > 0 else "LOSS" if t.get("pnl", 0) < 0 else "BREAKEVEN"
                }
                for t in self.perf.history[-3:] # Last 3 trades
            ]

        system_prompt = SIGNAL_SYSTEM_PROMPT_TEMPLATE

        # Inject RL-ICL Feedback (Lessons form Past Mistakes)
        lessons = self._format_lessons_learned()
        if lessons:
             system_prompt += f"\n\n### 🛑 PAST MISTAKES - LESSONS LEARNED\nDo NOT repeat these recent failures:\n{lessons}\n"
        
        # Helper to truncate list of dicts or values
        def truncate_history(data, limit=5): # Increased to 5 for better trend analysis
            if isinstance(data, list):
                return data[-limit:]
            return data
            
        def truncate_indicators(inds, limit=5): # Increased to 5
            new_inds = {}
            for k, v in inds.items():
                if isinstance(v, list):
                    new_inds[k] = v[-limit:]
                elif isinstance(v, dict): # For MACD which is nested
                    new_inds[k] = {sub_k: sub_v[-limit:] if isinstance(sub_v, list) else sub_v for sub_k, sub_v in v.items()}
                else:
                    new_inds[k] = v
            return new_inds

        # Filter TOON data to reduce tokens (Optimized but inclusive)
        alpha_ensemble_data = toon.get("indicators", {}).get("alpha_ensemble", {})
        toon_lite = {
            "account": toon.get("account"), 
            "recent_performance": recent_trades, 
            # Increased history limit to 5 for MTF to see trends
            "multi_timeframe": {tf: truncate_history(data, 5) for tf, data in toon.get("multi_timeframe", {}).items()},
            "indicators": truncate_indicators(toon.get("indicators", {}), 5), 
            "alpha_ensemble": alpha_ensemble_data if alpha_ensemble_data else None,
            "alpha_decay_health": toon.get("indicators", {}).get("alpha_decay_health", {}),
            "alpha_rotation": toon.get("indicators", {}).get("alpha_rotation", {}),
            "regime_allocation": toon.get("indicators", {}).get("regime_allocation", {}),
            "regime_duration": toon.get("regime_duration", {}),
            "auto_quant_tools": toon.get("auto_quant_tools", {}),
            "cpcv_escalation": toon.get("auto_quant_tools", {}).get("cpcv_escalation", {}),
            # Advanced auto-tool outputs (wired by _apply_advanced_auto_tools)
            "meta_adaptive_override": toon.get("meta_adaptive_override", {}),
            "evolutionary_suggestions": toon.get("evolutionary_suggestions", {}),
            "cross_market_robustness": toon.get("cross_market_robustness", {}),
            "cross_market_hold_bias": toon.get("cross_market_hold_bias", False),
            "alpha_discovery_enrichment": toon.get("alpha_discovery_enrichment", {}),
            "multimodal_patterns": toon.get("multimodal_patterns", {}),
            "self_improvement_entry_threshold": toon.get("self_improvement_entry_threshold"),
            "crash_detector_signal": toon.get("crash_detector_signal", {}),
            "crash_detector_hold_bias": toon.get("crash_detector_hold_bias", False),
            "causal_filter_report": toon.get("causal_filter_report", {}),
            "walk_forward_decay": toon.get("walk_forward_decay", {}),
            "walk_forward_hold_bias": toon.get("walk_forward_hold_bias", False),
            "optimal_execution_cost": toon.get("optimal_execution_cost", {}),
            "execution_cost_hold_bias": toon.get("execution_cost_hold_bias", False),
            "ewc_forgetting_status": toon.get("ewc_forgetting_status", {}),
            # Wired research modules — documented in prompt section 4f
            "alpha_stability": toon.get("auto_quant_tools", {}).get("results", {}).get("alpha_stability", {}),
            "canonical_mastery": toon.get("auto_quant_tools", {}).get("results", {}).get("canonical_mastery", {}),
            "rl_analytics": toon.get("auto_quant_tools", {}).get("results", {}).get("rl_analytics", {}),
            "grpo_train": toon.get("auto_quant_tools", {}).get("results", {}).get("grpo_train", {}),
            "ppo_train": toon.get("auto_quant_tools", {}).get("results", {}).get("ppo_train", {}),
            "regime_autocorr": toon.get("auto_quant_tools", {}).get("results", {}).get("regime_autocorr", {}),
            "meta_loop": toon.get("auto_quant_tools", {}).get("results", {}).get("meta_loop", {}),
            "zero_lag_macro": toon.get("zero_lag_macro", {}),
            # Alpha mining intelligence (wired by _apply_alpha_mining_enrichment)
            "alpha_mining_results": toon.get("alpha_mining_results", {}),
            # Knowledge base context from trading_knowledge_base/ modules
            "knowledge_base_context": self._get_knowledge_base_context(toon),
            "advanced_auto_tools_summary": toon.get("advanced_auto_tools_summary", {}),
            "completed_feature_snapshot": toon.get("feature_snapshot", {}),
            "volume_analysis": toon.get("volume_analysis"),
            "microstructure": toon.get("microstructure", {}),
            "dom": {k: v for k, v in toon.get("dom", {}).items() if k in ["best_bid", "best_ask", "spread", "imbalance"]},
            "news_events": toon.get("news", [])[:5], # Increased to 5
            "news_trading_context": toon.get("news_trading_context", {}),
            "news_gate": toon.get("news_gate", {}),
            "event_state": toon.get("event_state", {}),
            "event_playbooks": (toon.get("event_playbooks") or [])[:5],
            "api_news": [{"title": n.get("title"), "sentiment": n.get("sentiment"), "source": n.get("source")} for n in toon.get("api_news", [])[:5]], # Increased to 5 + Source
            "hot_topics": toon.get("hot_topics", [])[:3], # Increased to 3
            "macro": toon.get("macro_data"),
            "macro_causal_context": toon.get("macro_causal_context", [])[:8],
            # Intermarket regime is advisory research context only; it never
            # grants order permission. Prefer regime-filtered causal edges when
            # available so CONDITIONAL catalog priors stay regime-aware.
            "intermarket_regime": {
                k: toon.get("intermarket_regime", {}).get(k)
                for k in (
                    "regime_id", "confidence", "status", "advisory_bias",
                    "observed_pair_signs", "pair_correlations",
                    "macro_edges_in_regime", "active_catalog_edges",
                    "execution_authority",
                )
            } if isinstance(toon.get("intermarket_regime"), dict) else {
                "status": "UNAVAILABLE", "execution_authority": False,
            },
            "macro_causal_context_regime": toon.get("macro_causal_context_regime", [])[:8],
            "opportunity_fusion": {
                k: (toon.get("opportunity_fusion") or {}).get(k)
                for k in (
                    "status", "score", "macro_setup", "event_ok", "causal_agree",
                    "edge_agree", "propagation_agree", "causal_path_id",
                    "ta_ok", "agreeing_edge_ids", "reasons", "execution_authority",
                )
            } if isinstance(toon.get("opportunity_fusion"), dict) else {
                "status": "UNAVAILABLE", "execution_authority": False,
            },
            "causal_propagation": {
                k: (toon.get("causal_propagation") or {}).get(k)
                for k in (
                    "status", "path_id", "bias", "score", "horizon_seconds",
                    "execution_authority",
                )
            } if isinstance(toon.get("causal_propagation"), dict) else {
                "status": "UNAVAILABLE", "path_id": None, "execution_authority": False,
            },
            # main driver — top causal path for REPL feed banner + CausalScreen highlight (0ms, already computed in toon)
            "causal_main_driver": toon.get("causal_propagation") if isinstance(toon.get("causal_propagation"), dict) and toon.get("causal_propagation", {}).get("path_id") else (
                {"path_id": (toon.get("opportunity_fusion") or {}).get("causal_path_id"), "bias": (toon.get("opportunity_fusion") or {}).get("causal_agree"), "score": (toon.get("opportunity_fusion") or {}).get("score")} if isinstance(toon.get("opportunity_fusion"), dict) else None
            ),
            "macro_state_vector": {
                k: (toon.get("macro_state_vector") or {}).get(k)
                for k in (
                    "macro_bias", "composite_score", "headline", "regime",
                    "fusion_status", "causal_path_bias", "execution_authority",
                )
            } if isinstance(toon.get("macro_state_vector"), dict) else {
                "status": "UNAVAILABLE", "execution_authority": False,
            },
            "laya_decision_bundle": toon.get("laya_decision_bundle") or {
                "status": "UNAVAILABLE", "reason": "not_attached",
                "execution_authority": False, "source": "laya_decision_bundle",
            },
            "macro_opportunity_bus": {
                k: (toon.get("macro_opportunity_bus") or {}).get(k)
                for k in ("status", "published", "skipped", "candidate_ids", "execution_authority", "source_filter")
            } if isinstance(toon.get("macro_opportunity_bus"), dict) else {"status": "UNAVAILABLE", "execution_authority": False},
            "open_positions": toon.get("open_positions", []), # NEW: Explicitly providing open trades
            "playbook": toon.get("playbook", {}),
            "similar_past_regimes": toon.get("similar_past_regimes", [])[:3],
            "scenario_memory": toon.get("scenario_memory", [])[:3],
            "reviewed_core_memory": toon.get("core_memory", [])[:8],
            # Short-term operational context is bounded, redacted, and
            # point-in-time filtered by AgentOperations. It informs the LLM
            # only; it never grants order permission.
            "working_memory": toon.get("working_memory", {}),
            "learned_patterns": toon.get("learned_patterns", [])[:5],
            # HMM/GARCH output is bounded, advisory context only. It is
            # journaled for explanation and research; deterministic
            # validation/risk gates remain the sole permission path.
            "advisory_regime": toon.get("advisory_regime", {}),
            # Realised-sample survival/edge audit is bounded advisory context;
            # deterministic validation/risk gates remain the sole permission path.
            "quant_audit": {
                k: (toon.get("quant_audit") or {}).get(k)
                for k in (
                    "status", "sample_count", "minimum_sample", "win_rate",
                    "avg_win_r", "avg_loss_r", "payoff_ratio", "expected_value_r",
                    "risk_of_ruin", "quarter_kelly_fraction", "run_theory_l_max",
                    "hurst_exponent", "kind", "execution_authority",
                )
            } if isinstance(toon.get("quant_audit"), dict) else {
                "status": "UNAVAILABLE", "execution_authority": False,
            },
            "session_info": self._get_market_session()
        }
        # --- Live Advisory Hub (wires RED/ORANGE systems as advisory only) ---
        # Hierarchical + Copula + Meta-Labeling + CPCV + Quant Workflow + Kronos are injected here
        # so they appear in the prompt without ever granting execution_authority.
        try:
            from core.live_advisory_hub import build_live_advisory_snapshot
            # Derive minimal inputs from toon without inventing future data
            _prices = []
            try:
                # prefer closes from multi_timeframe H1 or M15
                _mtf = toon.get("multi_timeframe", {}) or {}
                _candles = _mtf.get("H1") or _mtf.get("M15") or _mtf.get("H4") or []
                if isinstance(_candles, list) and _candles:
                    _prices = [float(c.get("close", 0)) for c in _candles if isinstance(c, dict) and c.get("close") is not None]
                if not _prices:
                    _prices = [float(toon.get("indicators", {}).get("close", 1.0850) or 1.0850)] * 40
            except Exception:
                _prices = [1.0850]*40
            _adx = float(toon.get("indicators", {}).get("adx_14", 25.0) if not isinstance(toon.get("indicators", {}).get("adx_14"), list) else (toon.get("indicators", {}).get("adx_14", [25.0])[-1] if toon.get("indicators", {}).get("adx_14") else 25.0))
            _atr = float(toon.get("indicators", {}).get("atr_14", 0.0010) if not isinstance(toon.get("indicators", {}).get("atr_14"), list) else (toon.get("indicators", {}).get("atr_14", [0.0010])[-1] if toon.get("indicators", {}).get("atr_14") else 0.0010))
            _open_r = float(toon.get("open_risk", 0.0) or 0.0)
            # keep advisory bounds; never let it crash the decision
            _hub = build_live_advisory_snapshot(
                prices=_prices,
                current_adx=_adx,
                atr_history=None,
                current_atr=_atr,
                strategy_type=str(toon.get("strategy") or toon.get("setup_id") or "TREND_MOMENTUM"),
                open_portfolio_r=_open_r,
                base_risk_pct=float(getattr(self.risk, "risk_per_trade", 0.01) if hasattr(self, "risk") else 0.01),
            )
            toon_lite["live_advisory_hub"] = _hub
            # Also keep a flat copy for prompt brevity (top-level keys already in SYSTEM_PROMPT 4b note)
            toon_lite["hierarchical_advisory"] = _hub.get("hierarchical", {})
            toon_lite["copula_tail_advisory"] = _hub.get("copula_tail", {})
            toon_lite["meta_label_advisory"] = _hub.get("meta_labeling", {})
            toon_lite["cpcv_promotion_advisory"] = _hub.get("cpcv_promotion", {})
        except Exception as _hub_exc:
            logging.getLogger("LLMOrchestrator").debug("live_advisory_hub failed: %s", _hub_exc)
            toon_lite["live_advisory_hub"] = {"enabled": False, "error": str(_hub_exc)[:200], "execution_authority": False, "advisory_only": True}
        
        toon_lite = self._sanitize_toon_lite_budget(toon_lite, max_total_chars=60000)
        user_prompt = f"Market Data JSON:\n{json.dumps(toon_lite, default=str, separators=(',', ':'))}"
        prompt_hash = hashlib.sha256(
            json.dumps({"system": system_prompt, "user": user_prompt}, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        self._last_llm_trace = {
            "prompt_hash": prompt_hash, "response_hash": "", "attempts": 0,
            "parse_status": "NOT_STARTED", "estimated_tokens": max(1, len(user_prompt) // 4),
            "response_chars": 0,
        }
        logging.getLogger("LLMOrchestrator").info(f"User Prompt Length: {len(user_prompt)} chars")
        
        try:
            if self.memory:
                self.memory.set_system_prompt(system_prompt)
                self.memory.add_message("user", user_prompt)
                messages = self.memory.get_messages()
            else:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            # --- Token budget enforcement (Phase 3 gap) ---
            try:
                from data.llm_router import enforce_token_budget
                messages = enforce_token_budget(
                    messages, max_total_tokens=24000, model_name=self.model_name,
                )
            except Exception:
                pass
            # --- Whole-request budget (R2, payload-flow audit): the 60K
            # toon_lite budget alone doesn't bound replayed history. ---
            try:
                messages = self._enforce_request_char_budget(messages)
            except Exception:
                pass
            # --- PU-0161 prompt-size ceiling (serialized chars): R2 budgets
            # content, but the provider sees JSON-escaped bytes — session 11
            # showed the 11B model degrading at ~104K serialized while still
            # under the 96K content budget. Compact BEFORE that boundary. ---
            try:
                messages = self._enforce_request_ceiling(messages)
            except Exception:
                pass
            self.operations.record_raw_trace(
                "llm_prompt",
                {"model": self.model_name, "messages": messages},
                run_id=self.run_id,
                correlation_id=str(toon.get("journal_context", {}).get("candle_id", "")),
            )
            
            # Prompt preview via the retired Rich display is gone — use the
            # OpenTUI deck (FEED screen) to inspect live prompts instead.
            
            # Pass temperature=0 and seed for deterministic output
            # Retry Loop for Empty/Failed Responses
            max_retries = 3
            response_content = ""
            
            for attempt in range(max_retries):
                try:
                    self._last_llm_trace["attempts"] = attempt + 1
                    # Use tool-calling loop when tools are available
                    try:
                        from core.trading_tools import get_tool_schemas, execute_tool
                        _tool_schemas = get_tool_schemas()
                    except Exception:
                        _tool_schemas = []

                    if _tool_schemas and hasattr(self.llm, 'generate_with_tools'):
                        response_content = self.llm.generate_with_tools(
                            messages, tools=_tool_schemas, tool_executor=execute_tool,
                            model=self.model_name, temperature=0, seed=42, max_tokens=4096,
                        )
                    else:
                        response_content = self.llm.generate(
                            messages, 
                            model=self.model_name, 
                            temperature=0, 
                            seed=42,
                            max_tokens=4096 
                        )
                    
                    if not isinstance(response_content, str):
                        # Handle case where wrapper might return object (defensive)
                        if hasattr(response_content, 'choices'):
                             response_content = response_content.choices[0].message.content
                        else:
                             response_content = str(response_content)
                    
                    response_content = response_content.strip()
                    self._last_llm_trace["response_hash"] = hashlib.sha256(response_content.encode("utf-8")).hexdigest() if response_content else ""
                    self._last_llm_trace["response_chars"] = len(response_content)

                    # DEBUG: Log raw content
                    logging.getLogger("LLMOrchestrator").info(f"Raw LLM Response ({len(response_content)} chars, Attempt {attempt+1}/{max_retries}): {response_content[:100]}...")
                    
                    if response_content and not response_content.startswith("An error occurred"):
                        break # Success
                        
                    time.sleep(1) # Backoff slightly
                except Exception as e:
                     logging.getLogger("LLMOrchestrator").error(f"Generate attempt {attempt+1} failed: {e}")
                     time.sleep(1)

            if self.memory and response_content:
                # R1 compact mode: tag deterministic-fallback replies so the
                # buffer records them as failed cycles instead of replaying
                # dead-weight HOLDs in the rolling window.  Router fallback
                # payloads arrive as plain strings containing the marker, so
                # sniff the content too (same signal alpha_discovery checks).
                _is_fallback = (
                    '"deterministic_fallback": true' in response_content
                    or self._last_llm_trace.get("parse_status") == "ERROR"
                )
                self.memory.add_message(
                    "assistant",
                    response_content,
                    metadata={"deterministic_fallback": _is_fallback},
                )
                if _is_fallback and hasattr(self.memory, "discard_failed_cycle"):
                    # R3: failed cycles become short notes, not replay pairs.
                    try:
                        self.memory.discard_failed_cycle()
                    except Exception:
                        pass
            
            # Never silently change models for an order decision.  Different
            # models have different behaviours, prompts and calibration.  A
            # failover must be explicitly configured, versioned and reviewed;
            # until then a failed selected model is an auditable no-trade.
            if not response_content or response_content.startswith("An error occurred"):
                self._last_llm_trace["parse_status"] = "NO_RESPONSE"
                logging.getLogger("LLMOrchestrator").error("LLM error after retries; selected model=%s", self.model_name)
                return {
                    "action": "HOLD", 
                    "reasoning": f"Selected model {self.model_name} failed after retries; no model failover is permitted for an active decision.",
                    "confidence": 0.0,
                    "confluence_explanation": "All AI Models Failed",
                    "self_reflection": "System Failure."
                }

            self.operations.record_raw_trace(
                "llm_response",
                {"model": self.model_name, "response": response_content},
                run_id=self.run_id,
                correlation_id=str(toon.get("journal_context", {}).get("candle_id", "")),
            )

            # ── LLM Decision Logger (Blind Spot #8) ─────────────────────────
            try:
                if not hasattr(self, '_decision_logger') and LLMDecisionLogger is not None:
                    self._decision_logger = LLMDecisionLogger()
                if hasattr(self, '_decision_logger'):
                    self._pending_decision_log_id = self._decision_logger.log_decision(
                        model=self.model_name,
                        prompt_text=str(messages[-1].get("content", ""))[:2000] if messages else "",
                        response_text=response_content[:2000],
                        latency_ms=0,  # updated after parse
                        symbol=toon.get("context", {}).get("symbol", ""),
                        timeframe=toon.get("context", {}).get("timeframe", ""),
                    )
            except Exception as _dlog_exc:
                log.debug("Decision logger skipped: %s", _dlog_exc)
            json_str = response_content
            
            # Robust JSON Extraction
            def _extract_json(text):
                import re
                # 1. Prioritize Markdown Blocks (```json ... ```)
                match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
                if match: return match.group(1)
                
                # 2. Try Regex for first { to last }
                match = re.search(r"(\{.*\})", text, re.DOTALL)
                if match: return match.group(1)
                
                # 3. Fallback: Find first { and take everything after (Handles Truncation)
                match = re.search(r"(\{.*)", text, re.DOTALL)
                if match: return match.group(1)
                
                return text

            try:
                import re
                json_str = _extract_json(response_content)
                
                # Robust Cleanup
                json_str = re.sub(r"//.*", "", json_str) # Strip Comments
                json_str = re.sub(r",\s*([\]}])", r"\1", json_str) # Strip Trailing Commas
                json_str = json_str.replace("“", "\"").replace("”", "\"") # Smart Quotes
                json_str = json_str.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ') # Flatten
                
                # Attempt 1: Raw Load (with whitespace normalization)
                try:
                    # Normalize newlines to spaces to avoid control char errors in strings
                    json_str_norm = json_str.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')
                    decision = json.loads(json_str_norm)
                except json.JSONDecodeError:
                    # Attempt 2: Repair Truncated JSON
                    # If error is "Unterminated string", try adding quote
                    # If error is "Expecting ',' or '}'", try adding braces
                    # Aggressive Repair Logic
                    logging.getLogger("LLMOrchestrator").warning("Primary JSON load failed. Attempting AGGRESSIVE repair.")
                    
                    # 1. Close unclosed string (Odd number of quotes)
                    if json_str_norm.count('"') % 2 != 0:
                        json_str_norm += '"'
                    
                    # 2. Close brackets/braces
                    open_braces = json_str_norm.count('{') - json_str_norm.count('}')
                    open_brackets = json_str_norm.count('[') - json_str_norm.count(']')
                    
                    if open_braces > 0: json_str_norm += "}" * open_braces
                    if open_brackets > 0: json_str_norm += "]" * open_brackets
                    
                    try:
                        decision = json.loads(json_str_norm)
                        logging.getLogger("LLMOrchestrator").info("JSON repaired successfully.")
                    except json.JSONDecodeError:
                        # Final Hail Mary: Try partial string termination if cut off mid-value
                        # Assume the last open string was a value that got cut off
                        if '"' not in json_str_norm[-10:]: 
                             # If we didn't just close it, maybe force close it
                             pass 
                        raise # Give up if aggressive repair fails

                # ---------------------------------------------------------
                # GUARANTEE: Normalize Keys & Set Defaults
                # ---------------------------------------------------------
                # 1. Lowercase all keys
                decision = {k.lower(): v for k, v in decision.items()}
                
                # 2. Ensure critical keys exist
                defaults = {
                    "action": "HOLD",
                    "reasoning": "LLM returned valid JSON but missing 'reasoning' field.",
                    "confidence": 0.5,
                    "self_reflection": "N/A",
                    "confluence_explanation": "N/A",
                    "entry": 0.0,
                    "stop_loss": 0.0,
                    "take_profit": 0.0
                }
                for k, v in defaults.items():
                    if k not in decision or decision[k] is None:
                        decision[k] = v
                        
                # 3. Handle specific formatting (Action uppercase)
                decision["action"] = str(decision["action"]).upper()
                # Deterministic provider-failure HOLD must never escalate to a trade.
                if decision.get("deterministic_fallback"):
                    decision["action"] = "HOLD"
                    decision["confidence"] = 0.0
                    self._last_llm_trace["parse_status"] = "DETERMINISTIC_FALLBACK"
                # ---------------------------------------------------------

                # DATA VISIBILITY: Inject Prompt for GUI
                decision["user_prompt"] = user_prompt
                decision["system_prompt"] = system_prompt
                if self._last_llm_trace.get("parse_status") != "DETERMINISTIC_FALLBACK":
                    self._last_llm_trace["parse_status"] = "SUCCESS"
                
            except Exception as e:
                # Re-raise to fall into the outer except block
                raise e
            
            # Log the reasoning for verification
            logging.getLogger("LLMOrchestrator").info(f"LLM Decision: {decision.get('action')} | Confidence: {decision.get('confidence')}")
            logging.getLogger("LLMOrchestrator").info(f"Self-Reflection:\n{decision.get('self_reflection')}")
            logging.getLogger("LLMOrchestrator").info(f"Reasoning:\n{decision.get('reasoning')}")
            logging.getLogger("LLMOrchestrator").info(f"Confluence Explanation:\n{decision.get('confluence_explanation')}")
            
            return decision
            
        except (json.JSONDecodeError, ValueError) as e:
            self._last_llm_trace["parse_status"] = "PARSE_ERROR"
            # Fallback: Capture the raw output so the user can see it
            logging.getLogger("LLMOrchestrator").error(f"JSON Parse Error: {e}")
            logging.getLogger("LLMOrchestrator").info(f"Raw Invalid Response: {json_str}...") # Log full raw content?
            
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reasoning": f"JSON Parse Error. Raw Output:\n{json_str[:500]}...", # Show first 500 chars in UI
                "rationale": {"error": str(e), "raw_response": json_str},
                "self_reflection": "Failed to parse LLM response. Defaulting to safety.",
                "chain_of_thought": ["Error parsing JSON", "Safety protocol engaged", "Holding position"]
            }
            
        except Exception as e:
            # Preserve a machine-readable outcome for execute_cycle's
            # durable LLM telemetry. The response remains a safe HOLD.
            self._last_llm_trace["parse_status"] = "ERROR"
            logging.getLogger("LLMOrchestrator").error(f"LLM Generation failed: {e}")
            return {"action": "HOLD", "decision": "HOLD", "reasoning": f"LLM Error: {str(e)}"}

    def select_pairs_with_llm(self, count: int = 3) -> List[str]:
        """
        Stage 1: Market Scanner.
        Asks LLM to select the best pairs to trade based on broad market context.
        """
        log = logging.getLogger("MarketScanner")

        context = self._pair_selection_context()
        if context["selection_blocked"]:
            log.warning("Pair selection blocked because required macro/calendar data is unavailable")
            return []
        
        system_prompt = (
            "You are a Senior Market Analyst. Analyze the market context and select the top trading opportunities. "
            "Return a JSON list of symbol strings."
            f"Select exactly {count} symbols from the available list."
        )
        
        user_prompt = f"Context: {json.dumps(context)}\nSelect top {count} pairs."
        llm_request_id = ""
        request_started = time.perf_counter()
        prompt_hash = hashlib.sha256(
            json.dumps({"system": system_prompt, "user": user_prompt}, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        self._last_llm_trace = {
            "prompt_hash": prompt_hash, "response_hash": "", "attempts": 0,
            "parse_status": "NOT_STARTED", "response_chars": 0,
        }
        operations = getattr(self, "operations", None)
        if operations is not None:
            try:
                provider, budget = self._llm_provider_and_budget("scanner")
                trigger_key = f"pair-selection:{prompt_hash}"
                allowed, llm_request_id = operations.reserve_llm(
                    strategy="scanner", provider=provider, model=self.model_name,
                    trigger_key=trigger_key, run_id=getattr(self, "run_id", ""), **budget,
                )
                if not allowed:
                    self._last_llm_trace["parse_status"] = "BUDGET_BLOCKED"
                    log.warning("Pair selection LLM request blocked: %s", llm_request_id)
                    return []
            except Exception as exc:
                self._last_llm_trace["parse_status"] = "BUDGET_ERROR"
                log.warning("Pair selection LLM budget unavailable: %s", exc)
                return []

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            response = self.llm.generate(messages)
            usage = getattr(self.llm, "last_usage", {}) or {}
            usage_tokens, usage_cost, usage_error = self._normalize_llm_usage(usage)
            self._last_llm_trace["usage_status"] = "INVALID" if usage_error else "VALID"
            self._last_llm_trace["usage_error"] = usage_error
            self._last_llm_trace["attempts"] = 1
            if not isinstance(response, str):
                response = str(response)
            self._last_llm_trace["response_hash"] = hashlib.sha256(response.encode("utf-8")).hexdigest()
            self._last_llm_trace["response_chars"] = len(response)
            
            # Parse JSON list
            if "[" in response and "]" in response:
                start = response.find("[")
                end = response.rfind("]") + 1
                json_str = response[start:end]
                symbols = json.loads(json_str)
                if not isinstance(symbols, list):
                    raise ValueError("pair selection response is not a JSON list")
                allowed = set(context.get("available_pairs", []))
                normalized = []
                for item in symbols:
                    if not isinstance(item, str):
                        continue
                    candidate = item.strip().upper()
                    if candidate in allowed and candidate not in normalized:
                        normalized.append(candidate)
                if not normalized:
                    raise ValueError("pair selection contained no allowed symbols")
                self._last_llm_trace = {
                    **self._last_llm_trace,
                    "parse_status": "SUCCESS",
                    "pair_selection_count": len(normalized),
                    "pair_selection_symbols": normalized[:count],
                }
                request_success = not usage_error
                if llm_request_id:
                    operations.finish_llm(
                        llm_request_id, success=request_success,
                        latency_ms=(time.perf_counter() - request_started) * 1000,
                        prompt_hash=prompt_hash,
                        response_hash=self._last_llm_trace["response_hash"],
                        tokens=usage_tokens, cost=usage_cost, attempt_count=1,
                        parse_status="SUCCESS", response_chars=len(response),
                        error=usage_error,
                    )
                circuit = getattr(self, "provider_circuit", None)
                if circuit is not None:
                    state = circuit.record(request_success, usage_error)
                    if hasattr(operations, "event"):
                        operations.event(
                            "provider", "circuit_health", state,
                            run_id=getattr(self, "run_id", ""),
                            correlation_id=llm_request_id or prompt_hash,
                            severity="INFO" if request_success else "WARNING",
                        )
                if not request_success:
                    log.warning("Pair selection discarded because usage telemetry was invalid: %s", usage_error)
                    return []
                log.info("LLM Selected Pairs: %s", normalized[:count])
                return normalized[:count]
            else:
                raise ValueError("LLM failed to return a JSON pair list")
                
        except Exception as e:
            self._last_llm_trace = {
                **(getattr(self, "_last_llm_trace", {}) or {}),
                "parse_status": "ERROR",
                "pair_selection_error": str(e)[:500],
            }
            if llm_request_id:
                usage = getattr(self.llm, "last_usage", {}) or {}
                usage_tokens, usage_cost, usage_error = self._normalize_llm_usage(usage)
                operations.finish_llm(
                    llm_request_id, success=False,
                    latency_ms=(time.perf_counter() - request_started) * 1000,
                    prompt_hash=prompt_hash,
                    response_hash=str(self._last_llm_trace.get("response_hash", "")),
                    tokens=usage_tokens, cost=usage_cost,
                    attempt_count=int(self._last_llm_trace.get("attempts", 0) or 0),
                    parse_status="ERROR", response_chars=int(self._last_llm_trace.get("response_chars", 0) or 0),
                    error=(f"{e}; {usage_error}" if usage_error else str(e)),
                )
            circuit = getattr(self, "provider_circuit", None)
            if circuit is not None:
                state = circuit.record(False, f"{e}; {usage_error}" if 'usage_error' in locals() and usage_error else str(e))
                if hasattr(operations, "event"):
                    operations.event(
                        "provider", "circuit_health", state,
                        run_id=getattr(self, "run_id", ""),
                        correlation_id=llm_request_id or prompt_hash,
                        severity="WARNING",
                    )
            log.error(f"Market Scanner failed: {e}")
            # A failed selector must not silently widen or replace the
            # universe.  The caller records an empty opportunity set and can
            # retry on the next valid event/candle.
            return []

    def _pair_selection_context(self) -> Dict[str, Any]:
        """Build timestamped, fail-closed macro context for pair scanning."""
        now = datetime.now(timezone.utc)
        available_pairs = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "XAUUSD"]
        context: Dict[str, Any] = {
            "captured_at": now.isoformat(),
            "market_session": "London/New York Overlap" if 13 <= now.hour <= 17 else "Asian/European",
            "available_pairs": available_pairs,
            "data_status": self.data_provider.check_data_sources(),
            "calendar": {"status": "UNAVAILABLE", "events": [], "source": "none"},
            "macro": {"status": "UNAVAILABLE", "indicators": {}, "source": "none"},
            "news": {"status": "UNAVAILABLE", "topics": [], "source": "none"},
            "selection_blocked": False,
        }
        try:
            calendar = self.data_provider.calendar
            if calendar is not None:
                events = calendar.get_events(limit=20, min_importance=2,
                                             currencies=["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"])
                event_rows = [event.to_dict() if hasattr(event, "to_dict") else dict(event) for event in (events or [])]
                context["calendar"] = {"status": "AVAILABLE", "events": event_rows,
                                        "high_impact_count": sum(int(item.get("importance", 0) or 0) >= 3 for item in event_rows),
                                        "source": "configured_calendar"}
        except Exception as exc:
            context["calendar"] = {"status": "ERROR", "events": [], "source": "configured_calendar",
                                    "error": str(exc)[:300]}
        try:
            manager = self.data_provider.api_manager
            if manager is not None:
                topics = manager.get_hot_topics(limit=5) or []
                news_client_ready = getattr(manager, "newsapi", None) is not None
                context["news"] = {
                    "status": "AVAILABLE" if news_client_ready and isinstance(topics, list) else "UNAVAILABLE",
                    "topics": topics if isinstance(topics, list) else [],
                    "source": "configured_news_api",
                }
                series = manager.get_fred_data(catalog_fred_series()) or {}
                fred_client_ready = getattr(manager, "fred", None) is not None
                context["macro"] = {
                    "status": "AVAILABLE" if fred_client_ready and isinstance(series, dict) and bool(series) else "UNAVAILABLE",
                    "indicators": series if isinstance(series, dict) else {},
                    "source": "configured_fred",
                    "series_requested": catalog_fred_series(),
                }
        except Exception as exc:
            context["news"] = {"status": "ERROR", "topics": [], "source": "configured_news_api",
                                "error": str(exc)[:300]}
            context["macro"] = {"status": "ERROR", "indicators": {}, "source": "configured_fred",
                                 "error": str(exc)[:300]}
        # Pair selection is a macro-aware research decision.  Do not let a
        # healthy calendar mask unavailable news or fundamental context.
        context["selection_blocked"] = any(
            context[source]["status"] != "AVAILABLE"
            for source in ("calendar", "macro", "news")
        )
        return context

    def _kronos_advisory_snapshot(self, symbol: str, toon: Dict[str, Any]) -> Dict[str, Any]:
        """Optional Kronos alpha/vol advisory for LLM context only.

        Never grants execution authority. Failures degrade to an explicit
        UNAVAILABLE payload rather than inventing a confident forecast.
        """
        base = {
            "status": "DISABLED",
            "execution_authority": False,
            "backend": None,
            "weights_loaded": False,
        }
        if not getattr(self, "enable_kronos_advisory", False) or self.kronos_integrator is None:
            return base
        try:
            # Prefer completed bars only; exclude the forming candle.
            ohlc = toon.get("ohlc", []) or []
            history = [bar for bar in ohlc[:-1] if isinstance(bar, dict)]
            if len(history) < 8:
                return {
                    **base,
                    "status": "INSUFFICIENT_HISTORY",
                    "bars": len(history),
                    **self.kronos_integrator.predictor.backend_status(),
                }
            bundle = self.kronos_integrator.advisory_bundle(symbol, history, pred_len=12)
            bundle["status"] = "AVAILABLE"
            bundle["execution_authority"] = False
            return bundle
        except Exception as exc:
            return {
                **base,
                "status": "ERROR",
                "error": str(exc)[:400],
                "execution_authority": False,
            }
    
    def _build_rl_state(self, toon: Dict[str, Any]) -> Dict[str, Any]:
        """Build an RL research state from the last completed market snapshot.

        RL is offline/advisory only.  The state must still be temporally sound:
        forming-bar values are never used when a completed feature snapshot is
        available, and missing macro evidence remains explicit rather than a
        neutral fabricated value.
        """
        try:
            def completed_series(value: Any, default: float = 0.0) -> float:
                if isinstance(value, (list, tuple)) and value:
                    value = value[-2] if len(value) >= 2 else value[-1]
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return default

            indicators = toon.get("indicators", {})
            feature_snapshot = toon.get("feature_snapshot", {}) or {}
            point_values = feature_snapshot.get("values", {}) if feature_snapshot.get("ready") else {}
            if not isinstance(point_values, dict):
                point_values = {}
            ohlc = toon.get("ohlc", [])
            completed_bar = ohlc[-2] if isinstance(ohlc, list) and len(ohlc) >= 2 else (ohlc[-1] if ohlc else {})
            close_price = completed_series(point_values.get("close", completed_bar.get("close", 0.0)))
            rsi_14 = completed_series(point_values.get("rsi_14", indicators.get("rsi_14")), 50.0)
            macd_data = indicators.get("macd", {})
            macd_histogram = completed_series(point_values.get("macd_hist", macd_data.get("histogram")))
            ema_50 = completed_series(point_values.get("ema_50", indicators.get("ema_50")), close_price)
            ma_alignment = 1 if close_price > ema_50 else -1

            atr_val = completed_series(point_values.get("atr_14", indicators.get("atr_14")))
            atr_normalized = atr_val / max(close_price, 0.0001)
            confluence_data = toon.get("technical_analysis", {}).get("confluence", {})
            confluence_score = float(confluence_data.get("score", 0) or 0)
            key_levels = toon.get("technical_analysis", {}).get("key_levels", []) or []
            nearest_sr_distance = min((abs(close_price - float(level)) / max(close_price, 0.0001)
                                       for level in key_levels if isinstance(level, (int, float))), default=None)
            near_sr = bool(point_values.get("near_key_level")) or (nearest_sr_distance is not None and nearest_sr_distance <= 0.001)

            sentiment_total = 0.0
            sentiment_count = 0
            for event in (toon.get("news", []) or []) + (toon.get("api_news", []) or []):
                if not isinstance(event, dict):
                    continue
                sentiment = str(event.get("sentiment", "")).upper()
                if sentiment in {"BULLISH", "POSITIVE"}:
                    sentiment_total += 1.0; sentiment_count += 1
                elif sentiment in {"BEARISH", "NEGATIVE"}:
                    sentiment_total -= 1.0; sentiment_count += 1
                try:
                    actual, forecast = float(event.get("actual")), float(event.get("forecast"))
                    if math.isfinite(actual) and math.isfinite(forecast) and actual != forecast:
                        sentiment_total += 0.25 if actual > forecast else -0.25
                        sentiment_count += 1
                except (TypeError, ValueError):
                    pass
            news_sentiment = sentiment_total / sentiment_count if sentiment_count else None
            htf_name = str(toon.get("technical_analysis", {}).get("htf_context", {}).get("trend", "")).upper()
            htf_trend = 1 if htf_name == "BULLISH" else -1 if htf_name == "BEARISH" else 0
            adx = completed_series(point_values.get("adx_14", indicators.get("adx_14")))
            volatility_regime = str(point_values.get("volatility_regime", "")) or (
                "high" if atr_normalized >= 0.01 else "low" if atr_normalized <= 0.002 else "medium")
            rl_state = {
                "rsi_14": rsi_14,
                "macd_histogram": macd_histogram,
                "ma_alignment": ma_alignment,
                "atr_normalized": atr_normalized,
                "confluence_score": confluence_score,
                "near_sr": near_sr,
                "nearest_sr_distance": nearest_sr_distance,
                "news_sentiment": news_sentiment,
                "news_count": sentiment_count,
                "macro_event_state": toon.get("event_state", {}).get("state", "UNKNOWN") if isinstance(toon.get("event_state"), dict) else "UNKNOWN",
                "volatility_regime": volatility_regime,
                "trend_strength": max(0.0, min(adx / 50.0, 1.0)),
                "htf_trend": htf_trend,
                "session": self._get_market_session(),
            }
            
            return rl_state
            
        except Exception as e:
            logging.getLogger("LLMOrchestrator").warning(f"Failed to build RL state: {e}")
            return {}

    def _validate_proposal(self, decision: Dict[str, Any], toon: Dict[str, Any], symbol: str, strategy: str, paper_mode: bool = False) -> Tuple[Optional[TradeProposal], ValidationResult]:
        """Translate an LLM hypothesis into a deterministic, auditable gate."""
        mt5 = _get_mt5()
        side = decision.get("action", "HOLD").upper()
        entry = decision.get("entry") or (toon.get("dom", {}).get("best_ask") if side == "BUY" else toon.get("dom", {}).get("best_bid"))
        sl, tp = decision.get("stop_loss"), decision.get("take_profit")
        if side not in ("BUY", "SELL") or not all(isinstance(v, (int, float)) and v > 0 for v in (entry, sl, tp)):
            result = ValidationResult(False, ["incomplete or invalid LLM proposal"], 0, {
                "proposal_shape_valid": {"passed": False, "reason": "incomplete or invalid LLM proposal"},
            })
            return None, result
        indicators = toon.get("indicators", {})
        feature_snapshot = toon.get("feature_snapshot", {})
        point_features = feature_snapshot.get("values", {}) if feature_snapshot.get("ready") else {}
        # Use the last completed candle scalar snapshot, never the forming bar.
        close = point_features.get("close", toon.get("ohlc", [{}])[-2].get("close", entry) if len(toon.get("ohlc", [])) >= 2 else entry)
        _ema_ind = indicators.get("ema_50")
        ema = point_features.get("ema_50", _ema_ind[-2] if isinstance(_ema_ind, (list, tuple)) and len(_ema_ind) >= 2 else (_ema_ind if isinstance(_ema_ind, (int, float)) else close))
        _adx_ind = indicators.get("adx_14")
        adx = point_features.get("adx_14", _adx_ind[-2] if isinstance(_adx_ind, (list, tuple)) and len(_adx_ind) >= 2 else (_adx_ind if isinstance(_adx_ind, (int, float)) else 0))
        mtf = toon.get("multi_timeframe", {})
        direction = 1 if side == "BUY" else -1
        trends = [1 if item.get("trend") == "BULLISH" else -1 for item in mtf.values() if item.get("trend") in ("BULLISH", "BEARISH")]
        levels = toon.get("technical_analysis", {}).get("key_levels", [])
        _atr_ind = indicators.get("atr_14")
        atr = point_features.get("atr_14", _atr_ind[-2] if isinstance(_atr_ind, (list, tuple)) and len(_atr_ind) >= 2 else (_atr_ind if isinstance(_atr_ind, (int, float)) else 0))
        sweep_candidate = bool(point_features.get("liquidity_sweep_low", False) or point_features.get("liquidity_sweep_high", False) or
                               point_features.get("liquidity_sweep_confirmation_low", False) or point_features.get("liquidity_sweep_confirmation_high", False))
        features = {
            "ema_alignment": 1 if close > ema else -1,
            "mtf_alignment": direction if trends and sum(trends) * direction > 0 else 0,
            "adx": adx,
            "near_key_level": any(abs(close - level) <= max(atr, close * .001) for level in levels),
            # Ordinary registered setups use the completed bar itself. A
            # sweep/reversal requires the separate next-bar confirmation that
            # feature_builder computes from point-in-time bars.
            "confirmed_candle": (bool(point_features.get("confirmed_reversal_candle", False)) if sweep_candidate else True),
            "volume_confirmed": point_features.get("tick_volume_ratio_20", 0) >= 1.1,
            "liquidity_sweep": (point_features.get("liquidity_sweep_confirmation_low", False) if side == "BUY" else point_features.get("liquidity_sweep_confirmation_high", False)),
            "asian_range_breakout": point_features.get("asian_range_breakout_up", False) if side == "BUY" else point_features.get("asian_range_breakout_down", False),
            # The event blackout has cleared and we are in the post-event continuation
# window (core.news_trading phases). Fail closed: with no news-gate context
# there is no post-event continuation to trade.
"macro_continuation": str((toon.get("news_gate") or {}).get("phase") or "").upper()
                in ("POST_NEWS_ENTRY", "TREND_CONTINUATION"),
            "wick_rejection": point_features.get("wick_rejection", False),
            "equal_highs": point_features.get("equal_highs", False),
            "equal_lows": point_features.get("equal_lows", False),
            "round_number_distance": point_features.get("round_number_distance"),
            "regime": toon.get("technical_analysis", {}).get("primary_trend", "UNKNOWN"),
            "feature_schema_version": feature_snapshot.get("version", "legacy_unversioned"),
            "feature_schema_hash": feature_snapshot.get("schema_hash", ""),
            "feature_values_hash": feature_snapshot.get("values_hash", ""),
            "feature_values": point_features,
            "completed_bar_time": feature_snapshot.get("completed_bar_time", ""),
            "artifact_versions": dict(toon.get("journal_context", {})),
            "advisory_regime": {
                key: toon.get("advisory_regime", {}).get(key)
                for key in ("hmm_state", "hmm_probabilities", "garch_volatility",
                            "volatility_multiplier", "observations", "completed_bar_time")
                if key in (toon.get("advisory_regime", {}) or {})
            },
            "causal_edge_ids": [
                int(item["edge_id"]) for item in (toon.get("causal_setup_proposals") or [])
                if item.get("edge_id") is not None
                and str(item.get("suggested_side", "")).upper() == side
            ][:5],
        }
        classify_fn = _resolve_symbol("classify_setup", classify_setup)
        setup = classify_fn(features, self._get_market_session(), strategy)
        features["setup_id"] = setup["setup_id"]
        features["setup_reason"] = setup["reason"]
        if setup.get("spec"):
            features["setup_version"] = setup["spec"].get("version")
        # Phase C: link opportunity bus candidate onto durable proposal features.
        try:
            from opportunity_bus import load_open
            from reward_attribution import match_open_candidate_id
            bus = toon.get("opportunity_bus") if isinstance(toon.get("opportunity_bus"), dict) else {}
            bus_ids = list(bus.get("candidate_ids") or []) if isinstance(bus, dict) else []
            candidate_id = bus_ids[0] if bus_ids else None
            if not candidate_id:
                candidate_id = match_open_candidate_id(
                    load_open(self.store),
                    symbol=symbol,
                    setup_id=features.get("setup_id"),
                    side=side,
                )
            if candidate_id:
                features["opportunity_candidate_id"] = str(candidate_id)
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug("Opportunity candidate feature enrichment failed: %s", exc)
        proposal = TradeProposal(strategy=strategy, symbol=symbol, side=side, entry=float(entry), stop_loss=float(sl), take_profit=float(tp), confidence=float(decision.get("confidence", 0)), features=features, model=self.model_name,
                                 prompt_hash=str(toon.get("journal_context", {}).get("prompt_version", "")))
        feature_schema_result = validate_feature_schema(feature_snapshot, require_ready=bool(feature_snapshot.get("ready")))
        proposal.features["feature_schema_validation"] = feature_schema_result
        if feature_snapshot.get("ready") and not feature_schema_result["valid"]:
            result = ValidationResult(False, feature_schema_result["reasons"], self.validator.confluence(proposal.features, proposal.side), {
                "feature_schema_valid": {"passed": False, "version": feature_snapshot.get("version"), "reasons": feature_schema_result["reasons"]},
            })
            self.store.log_proposal(proposal, result)
            self.operations.agent_event("TechnicalNet", "calculate_features", "data_quality_observation",
                                        "proposal_rejected_feature_schema", {"reasons": feature_schema_result["reasons"]},
                                        run_id=self.run_id, severity="WARNING")
            return proposal, result
        if not setup["eligible"]:
            result = ValidationResult(False, [f"setup registry rejected: {setup['reason']}"], self.validator.confluence(proposal.features, proposal.side), {
                "setup_registry_eligible": {"passed": False, "setup_id": setup.get("setup_id"), "reason": setup["reason"]},
            })
            self.store.log_proposal(proposal, result)
            return proposal, result
        # --- Hierarchical Regime Gate ---
        regime_alloc = toon.get("indicators", {}).get("regime_allocation", {})
        if regime_alloc and not regime_alloc.get("approved", True):
            _regime_reasons = regime_alloc.get("reasons", [])
            _regime_name = (regime_alloc.get("regime") or {}).get("primary_regime", "UNKNOWN")
            result = ValidationResult(False, [
                f"hierarchical_regime_gate: regime={_regime_name} not_tradable reasons={_regime_reasons}",
            ], self.validator.confluence(proposal.features, proposal.side), {
                "regime_allocation": {
                    "passed": False,
                    "regime": _regime_name,
                    "approved": False,
                    "reasons": _regime_reasons,
                },
            })
            self.store.log_proposal(proposal, result)
            self.operations.agent_event("RiskController", "validate_proposal", "validation_decision",
                                        "proposal_rejected_regime_gate", {
                                            "symbol": symbol, "regime": _regime_name, "reasons": _regime_reasons,
                                        },
                                        run_id=self.run_id, correlation_id=proposal.setup_key(), severity="WARNING")
            return proposal, result
        # Apply regime risk multiplier to proposal confidence
        if regime_alloc:
            _mult = regime_alloc.get("applied_risk_multiplier", 1.0)
            if _mult < 1.0:
                proposal.features["regime_risk_multiplier"] = _mult
                proposal.features["regime"] = (regime_alloc.get("regime") or {}).get("primary_regime", "UNKNOWN")

        # --- CPCV Escalation Gate ---
        _cpcv_esc = toon.get("auto_quant_tools", {}).get("cpcv_escalation", {})
        if _cpcv_esc.get("position_reduction_recommended", False):
            _scale = _cpcv_esc.get("recommended_position_scale", 1.0)
            proposal.features["cpcv_position_scale"] = _scale
            proposal.features["cpcv_degradation_streak"] = _cpcv_esc.get("degradation_streak", 0)
            if _scale <= 0.0:
                result = ValidationResult(False, ["CPCV escalation: position_scale=0.0, all tactical entries blocked"],
                                          self.validator.confluence(proposal.features, proposal.side), {
                    "cpcv_escalation": {"scale": 0.0, "streak": _cpcv_esc.get("degradation_streak", 0)},
                })
                self.store.log_proposal(proposal, result)
                self.operations.agent_event("RiskController", "validate_proposal", "validation_decision",
                                            "proposal_rejected_cpcv_escalation", {
                                                "symbol": symbol, "scale": _scale,
                                                "streak": _cpcv_esc.get("degradation_streak", 0),
                                            },
                                            run_id=self.run_id, correlation_id=proposal.setup_key(), severity="CRITICAL")
                return proposal, result
            # Scale down the proposal size proportionally
            proposal.lots = round(proposal.lots * _scale, 2)
            if proposal.lots <= 0:
                result = ValidationResult(False, ["CPCV escalation: lots scaled to 0.0"],
                                          self.validator.confluence(proposal.features, proposal.side), {
                    "cpcv_escalation": {"scale": _scale, "streak": _cpcv_esc.get("degradation_streak", 0)},
                })
                self.store.log_proposal(proposal, result)
                return proposal, result

        # --- Cross-Market Robustness Gate ---
        if toon.get("cross_market_hold_bias"):
            proposal.features["cross_market_robustness_gate"] = "WEAK"
            proposal.features["cross_market_score"] = (
                toon.get("cross_market_robustness", {}).get("composite_score", 0)
            )
            result = ValidationResult(
                False,
                ["Cross-market robustness too weak: strategy not validated across instruments"],
                self.validator.confluence(proposal.features, proposal.side),
                {"cross_market_gate": toon.get("cross_market_robustness", {})},
            )
            self.store.log_proposal(proposal, result)
            self.operations.agent_event(
                "RiskController", "validate_proposal", "validation_decision",
                "proposal_rejected_cross_market_robustness",
                {"symbol": symbol, "score": proposal.features.get("cross_market_score", 0)},
                run_id=self.run_id, correlation_id=proposal.setup_key(), severity="WARNING",
            )
            return proposal, result

        # --- Crash Detector Gate (Logic-Q Neuro-Symbolic) ---
        _crash_hold = toon.get("crash_detector_hold_bias", False)
        _crash_signal = toon.get("crash_detector_signal", {})
        if _crash_hold or (isinstance(_crash_signal, dict) and _crash_signal.get("trend") == "RAPID_DESCEND"):
            proposal.features["crash_detector_gate"] = _crash_signal.get("trend", "RAPID_DESCEND")
            proposal.features["crash_detector_hold_bias"] = True
            result = ValidationResult(
                False,
                [f"Crash detector: {_crash_signal.get('trend', 'RAPID_DESCEND')} detected — all tactical entries blocked"],
                self.validator.confluence(proposal.features, proposal.side),
                {"crash_detector": _crash_signal},
            )
            self.store.log_proposal(proposal, result)
            self.operations.agent_event(
                "RiskController", "validate_proposal", "validation_decision",
                "proposal_rejected_crash_detector",
                {"symbol": symbol, "trend": _crash_signal.get("trend", "?"),
                 "crash_streak": _crash_signal.get("crash_streak", 0)},
                run_id=self.run_id, correlation_id=proposal.setup_key(), severity="CRITICAL",
            )
            return proposal, result

        # --- Overfit Gate (Author-C) ---
        # Blocks strategies with HIGH/CRITICAL overfit risk or never validated.
        if self.overfit_gate is not None:
            _ofg_result = self.overfit_gate.check_strategy(strategy)
            proposal.features["overfit_gate_status"] = _ofg_result.status
            proposal.features["overfit_gate_risk_level"] = _ofg_result.risk_level
            proposal.features["overfit_gate_grade"] = _ofg_result.grade
            if _ofg_result.status in ("REJECT", "NOT_VALIDATED"):
                result = ValidationResult(
                    False,
                    [f"Overfit gate {_ofg_result.status}: {_ofg_result.reason}"],
                    self.validator.confluence(proposal.features, proposal.side),
                    {"overfit_gate": _ofg_result.to_dict()},
                )
                self.store.log_proposal(proposal, result)
                self.operations.agent_event(
                    "RiskController", "validate_proposal", "validation_decision",
                    f"proposal_rejected_overfit_{_ofg_result.status.lower()}",
                    {"symbol": symbol, "strategy": strategy,
                     "risk_level": _ofg_result.risk_level, "grade": _ofg_result.grade},
                    run_id=self.run_id, correlation_id=proposal.setup_key(), severity="WARNING",
                )
                return proposal, result

        # --- Walk-Forward Decay Gate (Author-C) ---
        _wf_hold = toon.get("walk_forward_hold_bias", False)
        _wf_decay = toon.get("walk_forward_decay", {})
        if _wf_hold or (isinstance(_wf_decay, dict) and _wf_decay.get("status") == "DEAD"):
            proposal.features["walk_forward_gate"] = _wf_decay.get("status", "DEAD")
            proposal.features["walk_forward_hold_bias"] = True
            result = ValidationResult(
                False,
                [f"Walk-forward decay: alpha is DEAD (IC={_wf_decay.get('current_ic', 0):.4f}) — all entries blocked"],
                self.validator.confluence(proposal.features, proposal.side),
                {"walk_forward_decay": _wf_decay},
            )
            self.store.log_proposal(proposal, result)
            self.operations.agent_event(
                "RiskController", "validate_proposal", "validation_decision",
                "proposal_rejected_walk_forward_decay",
                {"symbol": symbol, "ic": _wf_decay.get("current_ic", 0),
                 "status": _wf_decay.get("status", "DEAD")},
                run_id=self.run_id, correlation_id=proposal.setup_key(), severity="WARNING",
            )
            return proposal, result

        # --- Execution Cost Gate (Almgren-Chriss) ---
        _exec_hold = toon.get("execution_cost_hold_bias", False)
        _exec_cost = toon.get("optimal_execution_cost", {})
        if _exec_hold or (isinstance(_exec_cost, dict) and not _exec_cost.get("cost_aware_should_enter", True)):
            proposal.features["execution_cost_gate"] = True
            proposal.features["execution_cost_hold_bias"] = True
            result = ValidationResult(
                False,
                [f"Execution cost gate: costs exceed edge (net_edge={_exec_cost.get('net_edge', 0):.6f}) — entry blocked"],
                self.validator.confluence(proposal.features, proposal.side),
                {"optimal_execution_cost": _exec_cost},
            )
            self.store.log_proposal(proposal, result)
            self.operations.agent_event(
                "RiskController", "validate_proposal", "validation_decision",
                "proposal_rejected_execution_cost",
                {"symbol": symbol, "net_edge": _exec_cost.get("net_edge", 0),
                 "algorithm": _exec_cost.get("algorithm", "TWAP")},
                run_id=self.run_id, correlation_id=proposal.setup_key(), severity="WARNING",
            )
            return proposal, result

        # --- Execution Cost Position Scaling ---
        if isinstance(_exec_cost, dict) and isinstance(_exec_cost.get("cost_aware_position_scale"), (int, float)):
            _exec_scale = float(_exec_cost["cost_aware_position_scale"])
            if _exec_scale < 1.0:
                proposal.lots = round(proposal.lots * _exec_scale, 2)
                proposal.features["execution_cost_position_scale"] = _exec_scale
                if proposal.lots <= 0:
                    result = ValidationResult(
                        False, [f"Execution cost: position scaled to 0.0 (scale={_exec_scale})"],
                        self.validator.confluence(proposal.features, proposal.side),
                        {"execution_cost_scale": _exec_scale},
                    )
                    self.store.log_proposal(proposal, result)
                    return proposal, result

        # --- Meta-Adaptive Position Scale ---
        _meta_override = toon.get("meta_adaptive_override", {})
        if isinstance(_meta_override, dict) and _meta_override.get("position_scale", 1.0) != 1.0:
            _pos_scale = float(_meta_override["position_scale"])
            if _pos_scale < 1.0:
                proposal.lots = round(proposal.lots * _pos_scale, 2)
                proposal.features["meta_adaptive_position_scale"] = _pos_scale
                if proposal.lots <= 0:
                    result = ValidationResult(
                        False, [f"Meta-adaptive: position scaled to 0.0 (scale={_pos_scale})"],
                        self.validator.confluence(proposal.features, proposal.side),
                        {"meta_adaptive_scale": _pos_scale},
                    )
                    self.store.log_proposal(proposal, result)
                    return proposal, result

        # --- Multimodal Pattern Confluence Boost ---
        _mm = toon.get("multimodal_patterns", {})
        if isinstance(_mm, dict) and isinstance(_mm.get("confluence_score"), (int, float)):
            mm_score = float(_mm["confluence_score"])
            mm_dir = _mm.get("direction", "NEUTRAL")
            # If pattern confluence aligns with trade direction, boost features
            if (side == "BUY" and mm_dir == "BULLISH") or (side == "SELL" and mm_dir == "BEARISH"):
                proposal.features["multimodal_confluence_boost"] = abs(mm_score)
                proposal.features["multimodal_pattern_aligned"] = True
            elif mm_dir != "NEUTRAL":
                # Pattern opposes trade direction — reduce confidence
                proposal.features["multimodal_confluence_opposition"] = abs(mm_score)
                proposal.features["multimodal_pattern_aligned"] = False

        # --- TwinAlgo Expectancy & SMC Gate (P06, Thophet 2026) ---
        try:
            from core.twinalgo_gates import (
                calibrate_confidence,
                clamped_confidence,
                loss_clamp_active,
                breakout_ok,
                TwinGates,
            )
            smc_ctx = toon.get("smc_structure", {})
            has_choch = bool(smc_ctx.get("has_choch", False))
            in_prem = bool(smc_ctx.get("in_premium", False)) if side == "BUY" else False
            mtf_ok = bool(features.get("mtf_alignment", 0) != 0)

            risk_dist = abs(float(entry) - float(sl))
            reward_dist = abs(float(tp) - float(entry))
            rr_ratio = (reward_dist / risk_dist) if risk_dist > 1e-9 else 0.0

            raw_conf_10 = int(round(proposal.confidence * 10)) if proposal.confidence <= 1.0 else int(round(proposal.confidence))
            calibrated_10 = calibrate_confidence(
                raw_conf_10,
                choch=has_choch,
                premium=in_prem,
                mtf_aligned=mtf_ok,
                rr=rr_ratio,
            )

            loss_history = getattr(self.perf, "recent_outcomes", [])
            if not isinstance(loss_history, list):
                loss_history = []
            if not loss_history:
                _cons_losses = getattr(self.risk, "consecutive_losses", 0)
                if isinstance(_cons_losses, (int, float)) and _cons_losses >= 3:
                    loss_history = [-1.0, -1.0, -1.0]

            if loss_history:
                calibrated_10 = clamped_confidence(calibrated_10, loss_history)

            proposal.features["twinalgo_calibrated_conf"] = calibrated_10
            proposal.features["twinalgo_choch_flag"] = has_choch
            proposal.features["twinalgo_premium_flag"] = in_prem

            import dataclasses
            proposal = dataclasses.replace(proposal, confidence=round(calibrated_10 / 10.0, 3) if proposal.confidence <= 1.0 else float(calibrated_10))

            if "breakout" in str(setup.get("setup_id", "")).lower() or features.get("asian_range_breakout"):
                _c_bar = toon.get("ohlc", [{}])[-2] if len(toon.get("ohlc", [])) >= 2 else {}
                _c_open = float(_c_bar.get("open", 0) or 0)
                _c_close = float(_c_bar.get("close", 0) or 0)
                _c_high = float(_c_bar.get("high", 0) or 0)
                _c_low = float(_c_bar.get("low", 0) or 0)
                _c_vol = float(_c_bar.get("volume", _c_bar.get("tick_volume", 0)) or 0)
                _avg_vol = float(features.get("feature_values", {}).get("avg_volume_20", 0) or 1.0)

                body_size = abs(_c_close - _c_open)
                total_range = abs(_c_high - _c_low)
                if total_range > 0 and _avg_vol > 0:
                    if not breakout_ok(body_size, total_range, _c_vol, _avg_vol):
                        proposal.features["twinalgo_breakout_rejected"] = True
                        result = ValidationResult(
                            False,
                            ["TwinAlgo breakout filter: low body ratio (<50%) or insufficient breakout volume (<1.3x avg)"],
                            self.validator.confluence(proposal.features, proposal.side),
                            {"twinalgo_breakout": {"passed": False, "body_ratio": body_size / total_range, "vol_ratio": _c_vol / _avg_vol}},
                        )
                        self.store.log_proposal(proposal, result)
                        self.operations.agent_event(
                            "RiskController", "validate_proposal", "validation_decision",
                            "proposal_rejected_twinalgo_breakout",
                            {"symbol": symbol, "body_ratio": body_size / total_range, "vol_ratio": _c_vol / _avg_vol},
                            run_id=self.run_id, correlation_id=proposal.setup_key(), severity="WARNING",
                        )
                        return proposal, result
        except Exception as _tw_exc:
            logging.getLogger("LLMOrchestrator").debug("TwinAlgo validation gate skipped: %s", _tw_exc)

        # --- TensorLogic Boolean Constraint Gate (PROVABLY CORRECT) ---
        # This is the FINAL deterministic gate. Every proposal passes through
        # ALL 14 Boolean constraint rules. The LLM cannot override these.
        try:
            from core.trading_constraints import TradingConstraintEngine, ConstraintVerdict
            constraint_engine = TradingConstraintEngine()
            # --- Meta-Labeling live check (if not already in toon, compute on the fly from prices) ---
            _meta_should_filter = False
            _meta_proba = None
            _meta_events = None
            try:
                _meta_src = None
                # Try toon, toon_lite live_advisory_hub, or direct live computation
                for _src in [toon.get("live_advisory_hub", {}).get("meta_labeling"), toon.get("meta_label_advisory"), toon.get("live_advisory_hub", {}).get("meta_label_advisory")]:
                    if isinstance(_src, dict) and _src:
                        _meta_src = _src
                        break
                if _meta_src is None:
                    # Compute live from ohlc closes
                    _closes = [float(b.get("close", 0) or 0) for b in toon.get("ohlc", []) if isinstance(b, dict) and b.get("close")]
                    if len(_closes) >= 30:
                        from core.live_advisory_hub import meta_label_advisory
                        _meta_src = meta_label_advisory(_closes)
                if isinstance(_meta_src, dict):
                    _meta_should_filter = bool(_meta_src.get("should_filter", False))
                    _proba_list = _meta_src.get("proba", [])
                    _meta_proba = round(float(_proba_list[-1]), 3) if _proba_list else None
                    _meta_events = _meta_src.get("events", 0)
            except Exception:
                pass
            constraint_context = {
                "risk_per_trade": getattr(self.risk, "risk_per_trade", None),
                "spread": toon.get("dom", {}).get("spread"),
                "recent_close": toon.get("ohlc", [{}])[-2].get("close") if len(toon.get("ohlc", [])) >= 2 else None,
                "last_trade_time": self.risk.last_trade_time if hasattr(self.risk, "last_trade_time") else None,
                "active_trade_count": len(self.risk.open_positions) if hasattr(self.risk, "open_positions") else 0,
                "daily_pnl_pct": self.perf.calc_daily_pnl_pct() if hasattr(self.perf, "calc_daily_pnl_pct") else None,
                "consecutive_losses": getattr(self.risk, "consecutive_losses", 0),
                "crash_detector_hold_bias": toon.get("crash_detector_hold_bias", False),
                "crash_detector_trend": toon.get("crash_detector_signal", {}).get("trend", "OSCILLATION"),
                "cross_market_hold_bias": toon.get("cross_market_hold_bias", False),
                "cross_market_score": toon.get("cross_market_robustness", {}).get("composite_score"),
                "cpcv_position_scale": toon.get("auto_quant_tools", {}).get("cpcv_escalation", {}).get("recommended_position_scale", 1.0),
                "cpcv_degradation_streak": toon.get("auto_quant_tools", {}).get("cpcv_escalation", {}).get("degradation_streak", 0),
                "meta_label_hold_bias": _meta_should_filter,
                "meta_label_proba": _meta_proba,
                "meta_label_events": _meta_events,
            }
            constraint_eval = constraint_engine.evaluate(
                {"action": side, "entry": entry, "stop_loss": sl, "take_profit": tp,
                 "lots": proposal.lots, "symbol": symbol},
                constraint_context,
            )
            proposal.features["constraint_engine_verdict"] = constraint_eval.overall_verdict.value
            proposal.features["constraint_engine_rejects"] = constraint_eval.reject_reasons
            proposal.features["constraint_engine_warnings"] = constraint_eval.warnings
            if constraint_eval.overall_verdict == ConstraintVerdict.REJECT:
                result = ValidationResult(
                    False,
                    [f"Boolean constraint gate: {reason}" for reason in constraint_eval.reject_reasons],
                    self.validator.confluence(proposal.features, proposal.side),
                    {"constraint_engine": constraint_eval.to_dict()},
                )
                self.store.log_proposal(proposal, result)
                self.operations.agent_event(
                    "RiskController", "validate_proposal", "validation_decision",
                    "proposal_rejected_boolean_constraints",
                    {"symbol": symbol, "reject_reasons": constraint_eval.reject_reasons[:5]},
                    run_id=self.run_id, correlation_id=proposal.setup_key(), severity="CRITICAL",
                )
                return proposal, result
            if constraint_eval.overall_verdict == ConstraintVerdict.REDUCE:
                # Apply reduce instructions
                for instr in constraint_eval.reduce_instructions:
                    if instr.get("action") == "REDUCE" and instr.get("field") == "lots":
                        new_lots = round(float(instr.get("to", proposal.lots)), 2)
                        if new_lots > 0:
                            proposal.lots = new_lots
                        else:
                            result = ValidationResult(
                                False,
                                [f"Boolean constraint: reduced lots to {new_lots}"],
                                self.validator.confluence(proposal.features, proposal.side),
                                {"constraint_engine": constraint_eval.to_dict()},
                            )
                            self.store.log_proposal(proposal, result)
                            return proposal, result
                logging.getLogger("LLMOrchestrator").info("Boolean constraint: reduced lots to %.2f", proposal.lots)
        except Exception as constraint_exc:
            logging.getLogger("LLMOrchestrator").debug("Boolean constraint engine failed: %s", constraint_exc)

        info = mt5.symbol_info(symbol) if mt5 and not paper_mode else None
        tick = mt5.symbol_info_tick(symbol) if mt5 and not paper_mode else None
        raw_tick_millis = (getattr(tick, "time_msc", 0) or (getattr(tick, "time", 0) or 0) * 1000) if tick else 0
        tick_time = raw_tick_millis / 1000 if raw_tick_millis else 0
        clock_health = (self.operations.record_clock_observation("mt5_tick", tick_time, max_offset_seconds=5.0,
                                                                 payload={"symbol": symbol}) if not paper_mode else
                        {"valid": True, "status": "PAPER_SIMULATION", "offset_seconds": 0.0})
        toon.setdefault("context", {})["clock_health"] = clock_health
        if tick_time:
            toon["context"]["provider_time"] = datetime.fromtimestamp(
                tick_time, tz=timezone.utc
            ).isoformat()
        quality = self.data_quality.validate(
            toon,
            require_live_quote=not paper_mode,
            require_dom=setup["setup_id"] == "liquidity_sweep_reversal_v1",
        )
        if not quality.passed:
            result = ValidationResult(False, quality.reasons, self.validator.confluence(proposal.features, proposal.side), {
                "data_quality": {"passed": False, "reasons": quality.reasons},
            })
            self.store.log_proposal(proposal, result)
            self.operations.agent_event("TechnicalNet", "calculate_features", "data_quality_observation",
                                        "proposal_rejected_data_quality", {"reasons": quality.reasons},
                                        run_id=self.run_id, severity="WARNING")
            return proposal, result
        point = getattr(info, "point", 0.0) or 0.0
        execution_policy = None
        if not paper_mode:
            try:
                execution_policy = load_execution_policy(self.store, symbol)
            except ValueError as exc:
                result = ValidationResult(False, [f"execution policy unavailable: {exc}"], self.validator.confluence(proposal.features, proposal.side), {
                    "execution_policy_available": {"passed": False, "reason": str(exc)},
                })
                self.store.log_proposal(proposal, result)
                self.operations.agent_event("RiskController", "validate_proposal", "validation_decision",
                                            "proposal_rejected_execution_policy", {"symbol": symbol, "reason": str(exc)},
                                            run_id=self.run_id, correlation_id=proposal.setup_key(), severity="WARNING")
                return proposal, result
        tick_age = (time.time() - tick_time) * 1000 if tick_time else 10**9
        lag_envelope = self._ingest_lag_envelope(
            symbol, tick=tick, tick_age_ms=tick_age, atr=float(atr or 0),
            source="mt5_proposal",
            mid_price=((float(getattr(tick, "bid", 0) or 0) + float(getattr(tick, "ask", 0) or 0)) / 2.0
                       if tick and float(getattr(tick, "bid", 0) or 0) > 0 else float(entry or 0) or None),
        )
        toon.setdefault("context", {})["lag_envelope"] = lag_envelope
        calendar_source = str(toon.get("context", {}).get("calendar_source", "UNAVAILABLE"))
        # A calendar object existing in memory is not evidence that this
        # snapshot obtained a usable release feed.  Bind the live gate to the
        # source captured for the current analysis; wrapper errors and missing
        # broker calendars therefore fail closed instead of trading on stale
        # assumptions.  Paper/replay fixtures may explicitly override the
        # simulated gate below, but never masquerade as live calendar evidence.
        calendar_ready = calendar_source in {"configured_calendar", "mt5_calendar"}
        if not calendar_ready:
            # Fail-closed below, but a dead feed is otherwise indistinguishable
            # from a quiet news day, so make it visible instead of silent.
            log = logging.getLogger(__name__)
            if not getattr(self, "_warned_calendar_unavailable", False):
                log.warning(
                    "Economic calendar unavailable (source=%s); news gate is "
                    "fail-closed and every proposal will be blocked as "
                    "high_impact_news until a provider returns events.",
                    calendar_source)
                self._warned_calendar_unavailable = True
        eval_req_fn = _resolve_symbol("evaluate_data_requirements", evaluate_data_requirements)
        data_requirements = eval_req_fn(setup["setup_id"], toon, calendar_available=calendar_ready)
        toon["data_requirements"] = data_requirements
        # Proposal snapshots retain the exact requirement decision so research
        # can distinguish a no-trade caused by missing evidence from a signal
        # that failed on market structure.
        proposal.features["data_requirements"] = data_requirements
        event_state = evaluate_event_policy(symbol, toon.get("news", []))
        # Opportunity fusion: deterministic TA ∩ causal ∩ event metadata.
        # Macro setups fail closed here; non-macro keep advisory WEAK/PASS only.
        try:
            adx_feat = float((proposal.features.get("adx") or point_features.get("adx_14") or 0) or 0)
        except (TypeError, ValueError):
            adx_feat = 0.0
        prop = toon.get("causal_propagation") if isinstance(toon.get("causal_propagation"), dict) else {}
        fusion = score_opportunity(
            setup_id=str(setup.get("setup_id") or ""),
            side=side,
            symbol=symbol,
            event_state=event_state,
            causal_edges=(
                toon.get("macro_causal_context_regime")
                or toon.get("macro_causal_context")
                or []
            ),
            causal_propagation=prop,
            ta_eligible=bool(setup.get("eligible")),
            ta_confidence=float(decision.get("confidence", 0) or 0),
            adx=adx_feat,
        )
        proposal.features["opportunity_fusion"] = fusion
        toon["opportunity_fusion"] = fusion
        # Attach advisory causal path id when present (research metadata only).
        if prop.get("path_id"):
            proposal.features["causal_path_id"] = prop.get("path_id")
            proposal.features["causal_path_bias"] = prop.get("bias")
            proposal.features["causal_path_score"] = prop.get("score")
        if fusion.get("causal_path_id"):
            proposal.features["fusion_causal_path_id"] = fusion.get("causal_path_id")
        if str(fusion.get("status", "")).upper() == "BLOCK":
            block_reasons = fusion_block_reasons(fusion)
            result = ValidationResult(
                False,
                block_reasons,
                self.validator.confluence(proposal.features, proposal.side),
                {
                    "opportunity_fusion": {
                        "passed": False,
                        "status": fusion.get("status"),
                        "score": fusion.get("score"),
                        "reasons": fusion.get("reasons"),
                        "execution_authority": False,
                    },
                },
            )
            self.store.log_proposal(proposal, result)
            self.operations.agent_event(
                "RiskController", "validate_proposal", "validation_decision",
                "proposal_rejected_opportunity_fusion", {
                    "symbol": symbol, "strategy": strategy,
                    "setup_id": setup.get("setup_id"), "fusion": fusion,
                },
                run_id=self.run_id, correlation_id=proposal.setup_key(),
                severity="WARNING",
            )
            return proposal, result
        eval_cond_fn = _resolve_symbol("evaluate_market_conditions", evaluate_market_conditions)
        conditions = eval_cond_fn(
            volume_ratio=toon.get("volume_analysis", {}).get("volume_ratio"),
            dom_depth=toon.get("dom", {}).get("dom_depth"),
            dom_available=bool(toon.get("dom", {}).get("available", False)),
            require_dom=setup["setup_id"] == "liquidity_sweep_reversal_v1",
        )
        hub = toon.get("live_advisory_hub") or {}
        hub_bocpd = hub.get("bocpd") or {}
        hub_cp = hub_bocpd.get("changepoint_probability")
        local_cp = self._regime_changepoint_probability(toon)
        cp_candidates = [float(c) for c in (hub_cp, local_cp) if c is not None and isinstance(c, (int, float)) and math.isfinite(float(c))]
        cp_prob = max(cp_candidates) if cp_candidates else local_cp

        copula_tail = hub.get("copula_tail") or {}
        copula_state = copula_tail.get("tail_risk_state")
        copula_heat = copula_tail.get("adjusted_heat_budget_r")

        if copula_heat is not None and isinstance(copula_heat, (int, float)):
            try:
                copula_heat_val = float(copula_heat)
                if math.isfinite(copula_heat_val):
                    base_heat = getattr(self.risk, "_base_heat_budget_r", None)
                    if base_heat is None:
                        base_heat = getattr(self.risk, "max_portfolio_heat_r", 4.0)
                        setattr(self.risk, "_base_heat_budget_r", base_heat)
                    target_heat = min(base_heat, copula_heat_val)
                    if target_heat < getattr(self.risk, "max_portfolio_heat_r", 4.0):
                        self.risk.set_heat_budget_r(target_heat)
                        logging.getLogger("LLMOrchestrator").info(
                            "Copula tail risk compressed heat budget to %.2fR (state=%s)",
                            target_heat, copula_state,
                        )
            except Exception as _copula_exc:
                logging.getLogger("LLMOrchestrator").debug("Copula heat compression skipped: %s", _copula_exc)

        market = {
            "market_open": bool(tick and getattr(tick, "ask", 0) > 0 and getattr(tick, "bid", 0) > 0),
            "tick_age_ms": tick_age, "max_tick_age_ms": 5000,
            "clock_ok": bool(clock_health.get("valid", False)),
            "symbol_tradable": bool(info and getattr(info, "trade_mode", 0) != 0),
            "spread": (tick.ask - tick.bid) if tick else float("inf"),
            "max_spread": (execution_policy["max_spread_price"] if execution_policy else (max(point * 30, point * max(getattr(info, "spread", 0), 1) * 2) if point else 0)),
            "execution_price": (tick.ask if side == "BUY" else tick.bid) if tick else 0.0,
            "entry_tolerance": (max(float(atr or 0) * float(execution_policy["entry_tolerance_atr_fraction"]), float(execution_policy["entry_tolerance_min_price"]))
                                if execution_policy else (max(float(atr or 0) * .10, point * 3) if point else float(atr or 0) * .10)),
            "completed_candle": True, "breakout_event": False,
            "stop_distance": abs(float(entry) - float(sl)),
            "min_stop_distance": (execution_policy["min_stop_distance_price"] if execution_policy else (point * getattr(info, "trade_stops_level", 0) if point else float("inf"))),
            # Missing calendar data is deliberately fail-closed.
            # EventPolicy is currency-aware.  Do not add an all-currency MT5
            # calendar check here, otherwise an unrelated event could override
            # the symbol-specific policy.  Calendar availability itself remains
            # fail-closed.
            "high_impact_news": (not calendar_ready) or event_state["blackout"],
            "event_state": event_state,
            "setup_cooldown_seconds": 900,
            "setup_min_rr": setup.get("spec", {}).get("min_rr", self.validator.min_rr),
            "setup_event_exclusion": setup.get("spec", {}).get("event_exclusion", True),
            "session_allowed": conditions.allowed,
            "liquidity_ok": conditions.allowed,
            "market_condition_reasons": conditions.reasons,
            "microstructure_ok": bool(toon.get("microstructure", {}).get("allowed", False)),
            "microstructure_reasons": toon.get("microstructure", {}).get("flags", []),
            "required_data_ok": data_requirements["available"],
            "required_data_reasons": data_requirements["reasons"],
            "lag_confidence": lag_envelope.get("lag_confidence"),
            "min_lag_confidence": 0.35,
            "lag_envelope": lag_envelope,
            # Net-of-friction EV floor + BOCPD regime-break veto evidence.
            "friction_r": self._estimate_friction_r(entry, sl, tick, execution_policy),
            "changepoint_probability": cp_prob,
            "copula_tail_risk": copula_state,
            "copula_heat_budget_r": copula_heat,
        }
        if paper_mode:
            # Explicit simulation may exercise the gate without pretending that
            # synthetic data is executable broker data.  Only quote mechanics
            # are supplied by the paper fixture; calendar, data-quality,
            # session, liquidity, and clock evidence must still be present.
            market.update({"market_open": True, "tick_age_ms": 0, "symbol_tradable": True,
                           "spread": 0.0, "max_spread": 1.0, "execution_price": float(entry), "entry_tolerance": 1.0, "min_stop_distance": 0.0,
                           "lag_confidence": 1.0, "min_lag_confidence": 0.0})
        result = self.validator.validate(proposal, market)
        self.store.log_proposal(proposal, result)
        self.operations.agent_event("RiskController", "validate_proposal", "validation_decision",
                                    "proposal_validated", {
                                        "symbol": symbol, "strategy": strategy, "setup_key": proposal.setup_key(),
                                        "approved": result.approved, "reasons": result.reasons,
                                        "confluence_score": result.confluence_score,
                                    }, run_id=self.run_id, correlation_id=proposal.setup_key(),
                                    severity="INFO" if result.approved else "WARNING")
        try:
            from local_agent_memory import maybe_remember_trade_cycle
            maybe_remember_trade_cycle(
                cycle_key=str(proposal.setup_key()),
                stage="risk_decision" if result.approved else "reject",
                payload={
                    "symbol": symbol,
                    "strategy": strategy,
                    "approved": result.approved,
                    "reasons": list(result.reasons or []),
                    "confluence_score": result.confluence_score,
                },
                symbol=str(symbol or ""),
                side=str(getattr(proposal, "side", "") or ""),
                run_id=self.run_id or "",
                memory_links={"L0": "validation_decision"},
            )
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug("Validation decision event failed: %s", exc)
        return proposal, result

    # ------------------------------------------------------------------
    # P8: Portfolio allocation layer (AUTONOMY_MASTER_PLAN §6)
    # ------------------------------------------------------------------
    def apply_portfolio_allocation_cap(self, symbol: str, strategy: str,
                                       action: str, volume: float,
                                       toon: Dict[str, Any],
                                       timeframe: str = "M15") -> Dict[str, Any]:
        """Deterministic, reduction-only portfolio cap consulted after validate.

        Constraint set (plan §6):
          1. Regime/strategy allocation multiplier (HierarchicalMetaPortfolio
             engine: regime router + heat headroom; cross-strategy ρ halving).
          2. Per-strategy drawdown state (factory registry: max_dd >= 20%
             halves the weight; >= 35% zeroes it).
          3. Pairwise ρ>0.7 notional overlap cap 40% (portfolio_agent
             correlation_headroom over the open book).

        Output feeds _build_execution_plan's volume. This method can only
        REDUCE size (min of constraints); it never increases, never grants
        permission, and fails open to the uncapped volume on any internal
        error so the arbiter/DeterministicValidator remain the sole
        authority. Autonomy-level caps (L0..L3) come from cap_size in the
        V2 path; here the level is derived from paper/live mode.
        """
        result: Dict[str, Any] = {
            "capped_volume": float(volume),
            "constraints": [],
            "allocator_multiplier": 1.0,
            "authority": "PortfolioAgent",
            "order_authority": False,
            "execution_authority": False,
        }
        vol = float(volume)
        if vol <= 0:
            return result
        try:
            capped = vol

            # --- 1. Regime/strategy allocation multiplier -----------------
            alloc_mult = 1.0
            alloc_reasons: list = []
            if self.portfolio_allocator is not None:
                try:
                    ohlc = (toon.get("ohlc") or {}) if isinstance(toon, dict) else {}
                    closes = ohlc.get("close") if isinstance(ohlc, dict) else None
                    if closes is not None and len(closes) >= 30:
                        import numpy as _np
                        prices = _np.asarray(list(closes)[-260:], dtype=float)
                        evalr = self.portfolio_allocator.evaluate(
                            prices=prices,
                            strategy_type=str(strategy or "scalp").upper(),
                            open_portfolio_r=float(self.risk.open_portfolio_heat_r() or 0.0),
                        )
                        alloc_mult = float(evalr.get("applied_risk_multiplier", 1.0))
                        alloc_reasons = [str(r) for r in (evalr.get("reasons") or [])[:3]]
                except Exception as exc:
                    logging.getLogger("LLMOrchestrator").debug(
                        "P8 allocator evaluate skipped: %s", exc)
            if alloc_mult < 1.0:
                capped = capped * max(0.0, alloc_mult)
                result["constraints"].append(
                    f"regime_allocation x{alloc_mult:.2f}" +
                    (f" ({'; '.join(alloc_reasons)})" if alloc_reasons else ""))
            result["allocator_multiplier"] = round(alloc_mult, 3)

            # --- 2. Per-strategy drawdown state (factory registry) --------
            if self.strategy_factory is not None and strategy:
                try:
                    for rec in self.strategy_factory.all():
                        if str(rec.get("id", "")) == str(strategy) or \
                                str(rec.get("name", "")) == str(strategy):
                            max_dd = float(rec.get("max_dd") or 0.0)
                            if max_dd >= 0.35:
                                capped = 0.0
                                result["constraints"].append(
                                    f"strategy_dd_halt max_dd={max_dd:.0%}")
                            elif max_dd >= 0.20:
                                capped *= 0.5
                                result["constraints"].append(
                                    f"strategy_dd_half max_dd={max_dd:.0%}")
                            break
                except Exception as exc:
                    logging.getLogger("LLMOrchestrator").debug(
                        "P8 factory DD lookup skipped: %s", exc)

            # --- 3. ρ>0.7 notional overlap cap (40%) -----------------------
            try:
                from operations.agents.portfolio_agent import (
                    RHO_OVERLAP_LIMIT, correlation_headroom,
                )
                positions = []
                try:
                    for row in self.store.open_trade_rows():
                        positions.append({
                            "symbol": str(row["symbol"]),
                            "notional": float(row["risk_amount"] or 0.0),
                            "side": str(row["side"]),
                        })
                except Exception:
                    positions = []
                if positions:
                    symbols = {symbol} | {p["symbol"] for p in positions}
                    corr_map: Dict[tuple, float] = {}
                    pairs = self.correlation_risk.pair_correlations(
                        symbols, timeframe, allow_stale=False)
                    for key, info in (pairs or {}).items():
                        a, b = key.split("|", 1)
                        corr_map[(a, b)] = float(info.get("raw_correlation", 0.0))
                        corr_map[(b, a)] = corr_map[(a, b)]
                    if corr_map:
                        # Convert the capped risk-$ volume into a comparable
                        # notional proxy: risk-$ of this trade vs book risk-$.
                        allowed_notional, why = correlation_headroom(
                            symbol, capped, positions, corr_map)
                        if allowed_notional < capped:
                            # Scale volume proportionally to the allowed share.
                            if capped > 0:
                                capped = capped * (allowed_notional / capped)
                            result["constraints"].append(why)
                result["rho_overlap_limit"] = RHO_OVERLAP_LIMIT
            except Exception as exc:
                logging.getLogger("LLMOrchestrator").debug(
                    "P8 overlap cap skipped: %s", exc)

            # Reduction-only guarantee.
            capped = min(vol, max(0.0, capped))
            result["capped_volume"] = round(capped, 8)
            if result["constraints"]:
                try:
                    self.operations.event(
                        "PortfolioAgent", "allocation_cap_applied",
                        {"symbol": symbol, "strategy": strategy,
                         "volume_before": round(vol, 8),
                         "volume_after": result["capped_volume"],
                         "constraints": result["constraints"]},
                        run_id=self.run_id, severity="INFO",
                    )
                except Exception:
                    pass
            return result
        except Exception as exc:
            # Fail open: the uncapped volume proceeds under the existing
            # arbiter/validator authority. The cap layer never blocks.
            logging.getLogger("LLMOrchestrator").debug(
                "P8 allocation cap failed open: %s", exc)
            result["error"] = str(exc)[:200]
            return result

    # ------------------------------------------------------------------
    # Live-trading lockout (2026-09-28): absence of --paper is not an
    # operator decision, so live order placement additionally requires an
    # explicit env confirmation: REQUIRE_LIVE_CONFIRMATION in
    # {1,true,yes,on} AND LIVE_CONFIRMATION_TOKEN set to the exact token
    # below (a two-step "type the sentence" acknowledgment). The gate sits
    # at the START of _build_execution_plan's live branch — before ANY
    # live-broker call (quote fetch, margin, policy load) — and raises the
    # same ValueError the pre-submit path already converts into an auditable
    # HOLD + REJECTED lifecycle row. Emergency exits / risk-reducing actions
    # live outside this file and are NOT gated: the lockout blocks NEW risk
    # only.
    # ------------------------------------------------------------------
    LIVE_CONFIRMATION_ENV = "REQUIRE_LIVE_CONFIRMATION"
    _LIVE_CONFIRMATION_VALUES = frozenset({"1", "true", "yes", "on"})
    LIVE_CONFIRMATION_TOKEN_ENV = "LIVE_CONFIRMATION_TOKEN"
    LIVE_CONFIRMATION_TOKEN_VALUE = "I_UNDERSTAND_LIVE_ORDERS_WILL_BE_PLACED"

    def _live_orders_authorized(self) -> Tuple[bool, str]:
        """Operator intent gate for live order placement (env-level).

        Returns (True, "") only when BOTH the confirmation flag is enabled
        AND the acknowledgment token matches exactly. Fail-closed: unset,
        malformed, or partially-configured environments are all lockouts.
        Read at call time so an operator can flip it without a restart.
        """
        raw = str(os.getenv(self.LIVE_CONFIRMATION_ENV, "") or "").strip().lower()
        if raw not in self._LIVE_CONFIRMATION_VALUES:
            return False, (
                f"{self.LIVE_CONFIRMATION_ENV} is not enabled (set it to one of "
                f"{sorted(self._LIVE_CONFIRMATION_VALUES)} to confirm live trading)")
        token = str(os.getenv(self.LIVE_CONFIRMATION_TOKEN_ENV, "") or "").strip()
        if token != self.LIVE_CONFIRMATION_TOKEN_VALUE:
            # SECURITY: the reason must NOT contain the literal token — it is
            # persisted into journal/lifecycle rows and operator logs. Point
            # at the constant instead of quoting it.
            return False, (
                f"{self.LIVE_CONFIRMATION_ENV} is enabled but "
                f"{self.LIVE_CONFIRMATION_TOKEN_ENV} is missing or incorrect "
                "(set it to the exact acknowledgment sentence documented in "
                "LIVE_CONFIRMATION_TOKEN_VALUE, intelligence/llm_orchestrator.py)")
        return True, ""

    @staticmethod
    def _live_order_lockout_notice(reason: str) -> Dict[str, Any]:
        """Audit payload for a lockout rejection (journal + lifecycle row)."""
        return {
            "reason": str(reason)[:300],
            "env_flag": LLMOrchestrator.LIVE_CONFIRMATION_ENV,
            "token_env": LLMOrchestrator.LIVE_CONFIRMATION_TOKEN_ENV,
            "execution_authority": False,
            "paper_mode": True,
        }

    def _build_execution_plan(self, proposal: TradeProposal, toon: Dict[str, Any], paper_mode: bool, volume: float):
        """Revalidate the broker quote and freeze a pending-entry plan before send."""
        if not paper_mode:
            # Live-trading lockout: the env gate fires BEFORE any live-broker
            # call. The ValueError is converted by the pre-submit caller into
            # an auditable HOLD/REJECTED and the cycle continues in safety.
            authorized, lockout_reason = self._live_orders_authorized()
            if not authorized:
                notice = self._live_order_lockout_notice(lockout_reason)
                try:
                    self.store.log_lifecycle_event(
                        None, proposal.setup_key(), "REJECTED",
                        "live_order_lockout", notice)
                except Exception as lifecycle_exc:
                    logging.getLogger("LLMOrchestrator").debug(
                        "live lockout lifecycle row failed: %s", lifecycle_exc)
                try:
                    self.operations.event(
                        "RiskController", "live_order_lockout", notice,
                        run_id=self.run_id, severity="WARNING")
                except Exception as event_exc:
                    logging.getLogger("LLMOrchestrator").debug(
                        "live lockout event failed: %s", event_exc)
                logging.getLogger("LLMOrchestrator").warning(
                    "LIVE ORDER LOCKOUT engaged: %s", lockout_reason)
                raise ValueError(f"live order lockout: {lockout_reason}")
        indicators = toon.get("indicators", {})
        point_features = toon.get("feature_snapshot", {}).get("values", {})
        atr = point_features.get("atr_14", (indicators.get("atr_14") or [0])[-2] if len(indicators.get("atr_14") or []) >= 2 else 0)
        if paper_mode:
            dom = toon.get("dom", {})
            bid, ask = float(dom.get("best_bid", 0) or 0), float(dom.get("best_ask", 0) or 0)
            tolerance = max(float(atr or 0) * .10, abs(ask - bid) * 2, 1e-9)
            plan = build_pending_entry(
                symbol=proposal.symbol, side=proposal.side, entry=proposal.entry, bid=bid, ask=ask,
                quote_time_msc=int(time.time() * 1000), entry_tolerance=tolerance,
                min_entry_distance=0.0, max_spread=max(ask - bid, 1e-9), expires_seconds=900,
            )
            return plan, {
                "mode": "paper_simulation",
                "estimated_margin": None,
                "predicted_slippage": (
                    ((toon.get("context") or {}).get("lag_envelope") or {}).get("smith_prediction") or {}
                ).get("predicted_slippage"),
                "lag_delay_sec": (
                    (((toon.get("context") or {}).get("lag_envelope") or {}).get("smith_prediction") or {}).get("expected_delay_ms", 0) or 0
                ) / 1000.0,
                "atr_at_submit": atr,
            }
        info = mt5.symbol_info(proposal.symbol)
        tick = mt5.symbol_info_tick(proposal.symbol)
        execution_policy = load_execution_policy(self.store, proposal.symbol)
        point = float(getattr(info, "point", 0) or 0)
        max_spread = float(execution_policy["max_spread_price"])
        ok, reason, quote = validate_quote(tick=tick, info=info, max_tick_age_ms=int(execution_policy["max_tick_age_ms"]), max_spread=max_spread)
        if not ok:
            raise ValueError(reason)
        tolerance = max(float(atr or 0) * float(execution_policy["entry_tolerance_atr_fraction"]), float(execution_policy["entry_tolerance_min_price"]))
        min_distance = float(execution_policy["min_stop_distance_price"])
        plan = build_pending_entry(
            symbol=proposal.symbol, side=proposal.side, entry=proposal.entry,
            bid=quote["bid"], ask=quote["ask"], quote_time_msc=int(quote["time_msc"]),
            entry_tolerance=tolerance, min_entry_distance=min_distance,
            max_spread=max_spread, expires_seconds=int(execution_policy["expires_seconds"]),
        )
        # MT5 margin calculation accepts deal direction, not every pending
        # subtype. Directional margin is recalculated immediately before the
        # pending request is sent.
        margin_action = mt5.ORDER_TYPE_BUY if proposal.side == "BUY" else mt5.ORDER_TYPE_SELL
        margin = mt5.order_calc_margin(margin_action, proposal.symbol, volume, plan.entry)
        account = mt5.account_info()
        if account is None:
            raise ValueError("broker account margin snapshot unavailable")
        free_margin = float(getattr(account, "margin_free", 0) or 0)
        if margin is None or float(margin) < 0:
            raise ValueError("broker could not calculate pending-order margin")
        if float(margin) > free_margin:
            raise ValueError("insufficient free margin at submission")
        cost_assessment = assess_costs(
            side=proposal.side, entry=plan.entry, stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit, volume=volume,
            bid=float(quote["bid"]), ask=float(quote["ask"]),
            tick_size=float(getattr(info, "trade_tick_size", 0) or 0),
            tick_value=float(getattr(info, "trade_tick_value", 0) or 0),
            commission_per_volume=float(execution_policy["commission_per_volume"]),
            expected_slippage_price=float(execution_policy["expected_slippage_price"]),
            expected_swap=float(execution_policy["expected_swap_money"]),
            min_net_rr=2.0,
            require_calibrated=True,
        )
        account_snapshot = {
            "equity": getattr(account, "equity", None),
            "margin": getattr(account, "margin", None),
            "margin_free": getattr(account, "margin_free", None),
            "margin_level": getattr(account, "margin_level", None),
            "margin_so_mode": getattr(account, "margin_so_mode", None),
            "margin_so_call": getattr(account, "margin_so_call", None),
            "margin_so_so": getattr(account, "margin_so_so", None),
        }
        return plan, {
            "mode": "mt5", "estimated_margin": float(margin), "free_margin": free_margin,
            "execution_policy": execution_policy,
            "submitted_at_msc": int(time.time() * 1000),
            "spread_at_submit": float(quote["ask"] - quote["bid"]),
            "account_snapshot": account_snapshot,
            "position_margin_estimates": self._fresh_position_margin_estimates(),
            "cost_assessment": cost_assessment.as_dict(),
            "predicted_slippage": (
                ((toon.get("context") or {}).get("lag_envelope") or {}).get("smith_prediction") or {}
            ).get("predicted_slippage"),
            "lag_delay_sec": (
                (((toon.get("context") or {}).get("lag_envelope") or {}).get("smith_prediction") or {}).get("expected_delay_ms", 0) or 0
            ) / 1000.0,
            "atr_at_submit": atr,
        }

    def _fresh_position_margin_estimates(self) -> List[Dict[str, Any]]:
        """Return conservative gross margin estimates for every broker position.

        This intentionally observes manual positions for portfolio protection
        while continuing to leave their management solely to their owner.  A
        failed estimate is a live-execution failure, not a zero-margin guess.
        """
        positions = mt5.positions_get()
        if positions is None:
            raise ValueError("broker position snapshot unavailable for margin concentration")
        durable_risk = {int(row["ticket"]): float(row["risk_amount"] or 0) for row in self.store.open_trade_rows()}
        estimates: List[Dict[str, Any]] = []
        for position in positions:
            symbol = str(getattr(position, "symbol", "") or "")
            volume = float(getattr(position, "volume", 0) or 0)
            if not symbol or volume <= 0:
                raise ValueError("invalid broker position in margin concentration snapshot")
            position_type = getattr(position, "type", None)
            is_buy = position_type == getattr(mt5, "POSITION_TYPE_BUY", 0)
            action = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
            side = "BUY" if is_buy else "SELL"
            tick = mt5.symbol_info_tick(symbol)
            price = float(getattr(tick, "ask" if is_buy else "bid", 0) or 0)
            calculated = mt5.order_calc_margin(action, symbol, volume, price)
            if calculated is None or float(calculated) < 0:
                raise ValueError(f"broker could not calculate open-position margin for {symbol}")
            ticket = int(getattr(position, "ticket", 0) or 0)
            estimates.append({
                "ticket": ticket, "symbol": symbol, "side": side, "margin": float(calculated),
                "buckets": sorted(exposure_buckets(symbol, side)),
                "risk_amount": durable_risk.get(ticket, 0.0),
            })
        return estimates

    def _completed_candle_candidate(self, symbol: str, timeframe: str, strategy: str) -> Optional[str]:
        """Return a completed-bar identity without advancing durable state.

        The caller must claim the idempotent analysis task before committing
        this checkpoint.  That ordering prevents a crash/provider failure
        between observation and task claim from silently skipping a candle.
        """
        try:
            fetch_fn = _get_fetch_ohlc()
            bars = fetch_fn(symbol, timeframe, count=3)
            if len(bars) < 2:
                return None
            closed_bar = str(bars.index[-2])
            key = f"last_closed_bar:{strategy}:{symbol}:{timeframe}"
            if self.store.get_state(key) == closed_bar:
                return None
            return closed_bar
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").warning("Cannot confirm completed candle: %s", exc)
            return None

    def _new_completed_candle(self, symbol: str, timeframe: str, strategy: str) -> bool:
        """Compatibility helper that advances the checkpoint after observation."""
        closed_bar = self._completed_candle_candidate(symbol, timeframe, strategy)
        if closed_bar is None:
            return False
        self.store.set_state(f"last_closed_bar:{strategy}:{symbol}:{timeframe}", closed_bar)
        return True

    def _emit_boot_event(self, event_type: str, payload: Dict[str, Any],
                         *, severity: str = "WARNING") -> None:
        """Journal a boot-watchdog outcome (best-effort; never fatal)."""
        try:
            self.operations.event(
                "orchestrator", event_type, payload, severity=severity)
        except Exception:
            logging.getLogger("LLMOrchestrator").debug(
                "boot watchdog event emit failed: %s", event_type)

    def _emit_provider_scorecard(self) -> None:
        """Journal the per-boot provider scorecard (trend vs previous boot).

        Best-effort: scorecard/trend/journal failures degrade to a debug log,
        never a boot failure.
        """
        log = logging.getLogger("LLMOrchestrator")
        try:
            from provider_scorecard import (
                KNOWN_API_SOURCES, build_event, load_recent_summaries,
                render, scorecard, trend_vs_previous)
            current = scorecard(
                get_datasheet().records(), known_sources=KNOWN_API_SOURCES)
            previous = None
            con_factory = getattr(getattr(self, "operations", None), "_con", None)
            if callable(con_factory):
                with con_factory() as con:
                    recent = load_recent_summaries(con, limit=2)
                if recent:
                    previous = recent[0].get("summary")
            trend = trend_vs_previous(current, previous)
            severity, payload = build_event(current, trend)
            self._emit_boot_event("provider_scorecard", payload, severity=severity)
            log.info("provider scorecard:\n%s", render(current, trend))
        except Exception:
            log.debug("provider scorecard failed", exc_info=True)

    def pre_pulse_macro_ingest(self, symbol: str, paper_mode: bool = False) -> Dict[str, Any]:
        """Hoist calendar / FRED / news ingestion once per pulse before parallel strategy execution.

        Caches the result in self._pulse_macro_cache[symbol] so concurrent strategy
        threads reuse the ingested graph observations and avoid SQLite WAL lock contention.
        Every fetch stage runs under a per-source deadline (boot_fetch_watchdog):
        a hung provider is abandoned at its budget and the pulse proceeds on
        degraded data instead of stalling the first cycle (fail-open).
        """
        import time as _time
        boot_budgets = stage_budgets()
        cache_entry: Dict[str, Any] = {
            "ts": _time.time(),
            "symbol": symbol,
            "macro_source_ids": [],
            "macro_event_sources": [],
            "macro_entities": [symbol],
            "data_source_pipeline": {},
            "macro_observations_ingest": {},
            "realtime_news_ingest": {},
            "boot_stage_outcomes": {},
        }
        try:
            # 1. Collect calendar news events from data_provider if available
            # (bounded by the boot-fetch watchdog; hung calendar bridge fails open)
            news_events: list = []
            if hasattr(self, "data_provider") and self.data_provider is not None:
                def _collect_calendar() -> list:
                    from data.market_data_provider import collect_calendar_event_rows
                    calendar = getattr(self.data_provider, "calendar", None)
                    client = getattr(self, "client", None) or getattr(self.data_provider, "client", None)
                    event_rows, _ = collect_calendar_event_rows(calendar, client)
                    return event_rows or []

                outcome = run_stage_with_deadline(
                    "calendar_collect", _collect_calendar,
                    budgets=boot_budgets, emit_event=self._emit_boot_event)
                cache_entry["boot_stage_outcomes"]["calendar_collect"] = {
                    k: outcome[k] for k in ("ok", "timed_out", "elapsed_s", "budget_s")}
                if outcome["ok"] and outcome["result"]:
                    news_events = [e for e in outcome["result"] if isinstance(e, dict)]

            # 2. Bulk ingest calendar events into macro graph (bounded)
            if news_events and getattr(self, "macro_graph", None) is not None:
                def _bulk_ingest() -> list:
                    bulk_items = [
                        {
                            "title": str(event.get("event") or event.get("title") or "").strip() or "calendar event",
                            "payload": event,
                            "source_url": str(event.get("source_url", "")),
                            "publisher": str(event.get("source", "runtime calendar")),
                            "published_at": str(event.get("time", "")),
                            "rights_note": "runtime calendar observation; candidate source requires review before causal promotion",
                        }
                        for event in news_events
                    ]
                    # Coerce to a plain list inside the bounded worker so a
                    # non-iterable result is contained as a stage failure
                    # instead of crashing the fold below.
                    return list(self.macro_graph.ingest_sources_bulk(bulk_items) or [])

                outcome = run_stage_with_deadline(
                    "bulk_calendar_ingest", _bulk_ingest,
                    budgets=boot_budgets, emit_event=self._emit_boot_event)
                cache_entry["boot_stage_outcomes"]["bulk_calendar_ingest"] = {
                    k: outcome[k] for k in ("ok", "timed_out", "elapsed_s", "budget_s")}
                if outcome["ok"] and outcome["result"]:
                    source_ids = outcome["result"]
                    cache_entry["macro_source_ids"] = source_ids
                    cache_entry["macro_event_sources"] = list(zip(source_ids, news_events))
                    for _sid, event in cache_entry["macro_event_sources"]:
                        if isinstance(event, dict) and event.get("currency"):
                            cache_entry["macro_entities"].append(str(event["currency"]))

            # 3. Process cycle data sources (FRED, NewsAPI) — bounded
            if getattr(self, "macro_graph", None) is not None:
                def _run_pipeline() -> Dict[str, Any]:
                    api_mgr = getattr(self.data_provider, "api_manager", None) if hasattr(self, "data_provider") else None
                    pipeline = process_cycle_data_sources(
                        macro_graph=self.macro_graph,
                        journal_path=str(getattr(self.store, "path", None) or "trading_state.db"),
                        symbol=symbol,
                        calendar_events=news_events,
                        api_news=[],
                        hot_topics=[],
                        macro_data={},
                        api_manager=api_mgr,
                        observed_at="",
                        include_optional_providers=(not paper_mode),
                    )
                    return {"pipeline": pipeline, "api_manager": api_mgr}

                outcome = run_stage_with_deadline(
                    "data_source_pipeline", _run_pipeline,
                    budgets=boot_budgets, emit_event=self._emit_boot_event)
                cache_entry["boot_stage_outcomes"]["data_source_pipeline"] = {
                    k: outcome[k] for k in ("ok", "timed_out", "elapsed_s", "budget_s")}
                if outcome["ok"] and outcome["result"]:
                    try:
                        pipeline = outcome["result"]["pipeline"]
                        api_mgr = outcome["result"]["api_manager"]
                        cache_entry["data_source_pipeline"] = pipeline
                        cache_entry["macro_observations_ingest"] = pipeline.get("observations") or {}
                        for entity in pipeline.get("macro_entities_extra") or []:
                            if entity and entity not in cache_entry["macro_entities"]:
                                cache_entry["macro_entities"].append(str(entity))
                        try:
                            self._emit_datasource_refresh(api_manager=api_mgr, pipeline=pipeline)
                        except Exception:
                            pass
                    except Exception as _pipe_exc:
                        logging.getLogger("LLMOrchestrator").debug("Pre-pulse data sources skipped: %s", _pipe_exc)

            # 4. Realtime news ingest (bounded)
            if getattr(self, "macro_graph", None) is not None:
                def _run_realtime_news() -> Dict[str, Any]:
                    api_mgr = getattr(self.data_provider, "api_manager", None) if hasattr(self, "data_provider") else None
                    news_ingest = run_realtime_news_ingest(
                        graph=self.macro_graph,
                        api_manager=api_mgr,
                        memory=getattr(self, "trade_memory", None),
                        llm=None,
                        symbol=symbol,
                        keywords=self._get_symbol_keywords(symbol) if hasattr(self, "_get_symbol_keywords") else [symbol],
                        include_rss=False,
                        extract_claims=False,
                    )
                    return {"news_ingest": news_ingest, "api_manager": api_mgr}

                outcome = run_stage_with_deadline(
                    "realtime_news_ingest", _run_realtime_news,
                    budgets=boot_budgets, emit_event=self._emit_boot_event)
                cache_entry["boot_stage_outcomes"]["realtime_news_ingest"] = {
                    k: outcome[k] for k in ("ok", "timed_out", "elapsed_s", "budget_s")}
                if outcome["ok"] and outcome["result"]:
                    news_ingest = outcome["result"]["news_ingest"]
                    cache_entry["realtime_news_ingest"] = news_ingest
                    for entity in (news_ingest.get("macro_entities_extra") or []):
                        if entity and entity not in cache_entry["macro_entities"]:
                            cache_entry["macro_entities"].append(str(entity))

        except Exception as _p_exc:
            logging.getLogger("LLMOrchestrator").warning("Pre-pulse macro ingest failed: %s", _p_exc)

        # Boot datasheet: fold the watchdog outcomes into the single boot-
        # health table (bootstrap steps + provider degradation markers are
        # already recorded) and render it exactly once, at the first pulse.
        # The first render also fires the per-boot provider scorecard event
        # (fresh/stale/no-data verdicts + trend vs the previous boot).
        if boot_datasheet_enabled():
            _sheet = get_datasheet()
            for _stage, _out in (cache_entry.get("boot_stage_outcomes") or {}).items():
                _sheet.record_pulse_stage(_stage, _out)
            _table = _sheet.maybe_render_once(
                emit_event=lambda _et, _p: self._emit_boot_event(
                    _et, _p, severity="INFO"))
            if _table is not None:
                self._emit_provider_scorecard()

        if not hasattr(self, "_pulse_macro_cache"):
            self._pulse_macro_cache = {}
        self._pulse_macro_cache[symbol] = cache_entry
        return cache_entry

    def _ingest_lag_envelope(
        self,
        symbol: str,
        *,
        tick=None,
        tick_age_ms: Optional[float] = None,
        atr: float = 0.0,
        source: str = "mt5",
        mid_price: Optional[float] = None,
        recent_returns_slope: float = 0.0,
    ) -> Dict[str, Any]:
        """Apply three-timestamp lag tracking + confidence decay to a live quote.

        Advisory for management paths; fail-closed for proposal validation via
        ``lag_confidence`` consumed by DeterministicValidator.
        """
        now_iso = utcnow_iso()
        t_event = now_iso
        tick_msc = int(getattr(tick, "time_msc", 0) or 0) if tick is not None else 0
        if tick_msc > 0:
            try:
                t_event = datetime.fromtimestamp(tick_msc / 1000.0, tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                t_event = now_iso
        if tick_age_ms is None and tick_msc > 0:
            tick_age_ms = max(0.0, float(int(time.time() * 1000) - tick_msc))
        elif tick_age_ms is None:
            tick_age_ms = 10**9
        event = TimestampedEvent(
            event_id=f"{source}:{symbol}:{tick_msc or now_iso}",
            source=source,
            t_event=t_event,
            t_ingest=now_iso,
            t_proc=now_iso,
            payload={"symbol": symbol, "tick_age_ms": tick_age_ms},
            data_type="price_tick",
        )
        hb = getattr(self, "heartbeat_tracker", None)
        heartbeat = hb.update_source(event) if hb is not None else None
        vix_level = 20.0
        try:
            macro = (getattr(self.data_provider, "api_manager", None) and
                     self.data_provider.api_manager.get_fred_data(["VIXCLS"])) or {}
            if macro.get("VIXCLS", {}).get("value") is not None:
                vix_level = float(macro["VIXCLS"]["value"])
        except Exception:
            vix_level = 20.0
        confidence = ConfidenceDecayEngine.calculate_confidence(
            float(tick_age_ms), data_type="price_tick", vix_level=vix_level,
        )
        price = mid_price
        if price is None and tick is not None:
            bid = float(getattr(tick, "bid", 0) or 0)
            ask = float(getattr(tick, "ask", 0) or 0)
            if bid > 0 and ask >= bid:
                price = (bid + ask) / 2.0
        prediction = None
        if price is not None and math.isfinite(float(price)) and float(price) > 0:
            prediction = SmithPredictorEngine.predict_state(
                asset=symbol,
                observed_price=float(price),
                staleness_ms=float(tick_age_ms),
                recent_returns_slope=float(recent_returns_slope or 0.0),
                current_atr=float(atr or max(float(price) * 0.001, 1e-6)),
            )
        envelope = {
            "event_id": event.event_id,
            "t_event": event.t_event,
            "t_ingest": event.t_ingest,
            "t_proc": event.t_proc,
            "staleness_ms": round(event.staleness_ms, 2),
            "pipeline_latency_ms": round(event.pipeline_latency_ms, 2),
            "tick_age_ms": float(tick_age_ms),
            "lag_confidence": round(float(confidence), 4),
            "heartbeat": heartbeat,
            "smith_prediction": (
                {
                    "predicted_price": prediction.predicted_price,
                    "predicted_slippage": prediction.predicted_slippage,
                    "order_type_recommendation": prediction.order_type_recommendation,
                    "confidence": prediction.confidence,
                    "expected_delay_ms": prediction.expected_delay_ms,
                } if prediction is not None else None
            ),
            "execution_authority": False,
        }
        return envelope

    def _run_causal_advisory(self, symbol: str, macro_entities: List[str]) -> Dict[str, Any]:
        """Validate candidate edges and discover advisory setups (no order authority)."""
        result: Dict[str, Any] = {
            "validated": [],
            "setups": [],
            "execution_authority": False,
        }
        try:
            last_iso = self.store.get_state("last_causal_auto_validate", "")
            should_validate = True
            if last_iso:
                try:
                    last_dt = datetime.fromisoformat(str(last_iso).replace("Z", "+00:00"))
                    should_validate = (datetime.now(timezone.utc) - last_dt).total_seconds() >= 900
                except (TypeError, ValueError):
                    should_validate = True
            if should_validate:
                validated = self.causal_validator.auto_validate_all_candidates()
                result["validated"] = validated
                result["reinforcement"] = self._reinforce_causal_edges_from_reactions()
                self.store.set_state("last_causal_auto_validate", datetime.now(timezone.utc).isoformat())
            entities = [str(e) for e in (macro_entities or []) if e]
            if not entities:
                entities = [symbol[:3], symbol[3:6], symbol] if len(symbol) >= 6 else [symbol]
            result["setups"] = self.causal_setup_analyzer.discover_emerging_setups(symbol, entities)
        except Exception as exc:
            result["error"] = str(exc)[:500]
            logging.getLogger("LLMOrchestrator").warning("Causal advisory failed: %s", exc)
        return result

    def _capture_intermarket_proxy_returns(self, timeframe: str, *, exclude_symbol: str = "") -> Dict[str, Any]:
        """Warm CorrelationRisk with DXY/Gold/Oil/Equity/VIX proxy bars when available.

        Advisory only: never invents bars, never grants execution authority, and
        is rate-limited so the market pulse stays cheap.
        """
        from intermarket_regime import ENTITY_SYMBOL_PROXIES

        summary: Dict[str, Any] = {
            "recorded": {},
            "skipped": [],
            "execution_authority": False,
        }
        try:
            last_iso = self.store.get_state("last_intermarket_proxy_capture", "")
            if last_iso:
                last_dt = datetime.fromisoformat(str(last_iso).replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - last_dt).total_seconds() < 900:
                    summary["status"] = "RATE_LIMITED"
                    return summary
        except (TypeError, ValueError):
            pass

        known = {s.upper() for s in self.correlation_risk.known_symbols(timeframe)}
        exclude = str(exclude_symbol or "").upper()
        for entity, aliases in ENTITY_SYMBOL_PROXIES.items():
            # Prefer an already-known alias; otherwise probe aliases in order.
            candidates = [a for a in aliases if str(a).upper() in known] or list(aliases)
            recorded_any = False
            for preferred in candidates:
                if str(preferred).upper() == exclude:
                    continue
                try:
                    df = fetch_ohlc(str(preferred), timeframe, count=80)
                    bars = ohlc_records(df) if hasattr(df, "to_dict") else []
                    if len(bars) < 3:
                        continue
                    recorded = self.correlation_risk.record_completed_bars(
                        str(preferred), timeframe, bars[:-1],
                    )
                    summary["recorded"][entity] = {
                        "symbol": preferred, "returns_recorded": int(recorded),
                    }
                    known.add(str(preferred).upper())
                    recorded_any = True
                    break
                except Exception as exc:
                    last_err = str(exc)[:160]
                    continue
            if not recorded_any:
                summary["skipped"].append({
                    "entity": entity,
                    "reason": locals().get("last_err", "unavailable"),
                })
        try:
            self.store.set_state(
                "last_intermarket_proxy_capture",
                datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug("Intermarket proxy capture event failed: %s", exc)
        summary["status"] = "CAPTURED" if summary["recorded"] else "NO_PROXIES"
        return summary

    def _apply_causal_trade_feedback(self, ticket: int, pnl: float, risk_amount: float, label: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Close the trade→causal-edge learning loop via revise_edge + auto-validate."""
        feedbacks: List[Dict[str, Any]] = []
        try:
            payload = self.store.trade_payload(int(ticket)) if hasattr(self.store, "trade_payload") else {}
            features = payload.get("proposal_features") or {}
            edge_ids = features.get("causal_edge_ids") or payload.get("causal_edge_ids") or []
            if not isinstance(edge_ids, list):
                edge_ids = []
            r_multiple = float(label.get("r_multiple", 0.0) or 0.0)
            if not edge_ids and risk_amount > 0:
                # No linked edges — nothing to revise.
                return feedbacks
            for edge_id in edge_ids:
                try:
                    fb = self.macro_graph.apply_trade_outcome_feedback(
                        int(edge_id),
                        r_multiple=r_multiple,
                        ticket=int(ticket),
                        label=str(label.get("label", "")),
                    )
                    reval = self.causal_validator.validate_candidate_edge(int(edge_id))
                    fb["revalidation"] = reval
                    feedbacks.append(fb)
                except Exception as edge_exc:
                    feedbacks.append({"edge_id": edge_id, "error": str(edge_exc)[:300]})
            if feedbacks:
                self.operations.event(
                    "causal", "trade_outcome_feedback",
                    {"ticket": ticket, "pnl": pnl, "feedbacks": feedbacks},
                    run_id=self.run_id, correlation_id=str(ticket),
                )
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").warning(
                "Causal trade feedback failed for #%s: %s", ticket, exc,
            )
        return feedbacks

    def _run_reflection_graph(self, *, ticket: int, symbol: str, strategy: str,
                               open_time: str, close_time: str, entry_price: float,
                               close_price: float, realized_pnl: float,
                               realized_r_multiple: float, outcome_label: str) -> None:
        """Invoke the Slow-Lane Memory Reflection Graph after a trade closes.

        Updates equity state (streak counters, R-history), runs QuantitativeCoach
        to label the trade into L1/L2 memory, consolidates scenario playbooks,
        and applies Bayesian macro feedback to causal edges.
        """
        try:
            journal_db = str(getattr(self.store, "path", None) or "trading_state.db")
            if self._reflection_graph is None:
                self._reflection_graph = build_reflection_graph(db_path=journal_db)
            state: MemoryReflectionState = {
                "deal_ticket": int(ticket),
                "symbol": str(symbol),
                "strategy": str(strategy),
                "open_time": str(open_time),
                "close_time": str(close_time),
                "entry_price": float(entry_price),
                "close_price": float(close_price),
                "realized_pnl": float(realized_pnl),
                "realized_r_multiple": float(realized_r_multiple),
                "outcome_label": str(outcome_label),
                "distilled_atomic_facts": [],
                "scenario_key": None,
                "scenario_updated": False,
                "macro_dynamics_feedback": None,
                "audit_trace": [f"Reflection invoked for trade {ticket}"],
                "updated_at": utcnow_iso(),
            }
            config = {"configurable": {"thread_id": f"reflection_{ticket}"}}
            self._reflection_graph.invoke(state, config=config)
            logging.getLogger("LLMOrchestrator").info(
                "Reflection graph completed for trade %s (%s)", ticket, symbol,
            )
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").warning(
                "Reflection graph failed for trade %s: %s", ticket, exc,
            )

    def _monitor_owned_positions_tick(self, symbol: str, timeframe: str, *, paper_mode: bool) -> Dict[str, Any]:
        """Manage owned positions between candle triggers using fresh quotes.

        Entry analysis remains candle/event driven, but protective management
        must not wait for a new M5/M15/H1 candle. This path uses only a fresh
        MT5 quote and the last completed-candle ATR; it never invokes the LLM
        and never manages positions whose magic number is not bot-owned.
        """
        mt5 = _get_mt5()
        if not getattr(self.data_provider, "trade_manager", None):
            return {"status": "SKIPPED", "reason": "manager_unavailable"}
        if not paper_mode and not self.client:
            return {"status": "SKIPPED", "reason": "client_unavailable"}
        try:
            tick = mt5.symbol_info_tick(symbol) if mt5 is not None else None
            bid = float(getattr(tick, "bid", 0) or 0) if tick else 0.0
            ask = float(getattr(tick, "ask", 0) or 0) if tick else 0.0
            tick_msc = int(getattr(tick, "time_msc", 0) or 0) if tick else 0
            atr_state = self.store.get_state(f"position_atr:{symbol}:{timeframe}", {}) or {}
            atr = float(atr_state.get("atr", 0) or 0)

            # In paper mode without live MT5 ticks, use last candle close from state or paper positions
            if paper_mode and not (bid > 0 and ask >= bid):
                close_p = float(atr_state.get("close", 0) or 0)
                if close_p <= 0 and self.paper_broker:
                    # check if any paper position has open price
                    for p in self.paper_broker.positions.values():
                        if p.get("symbol") == symbol:
                            close_p = float(p.get("price_open", 0) or 0)
                            break
                if close_p > 0:
                    bid = close_p
                    ask = close_p
                    tick_msc = int(time.time() * 1000)

            if not (math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask >= bid):
                if hasattr(self.store, "record_market_tick"):
                    self.store.record_market_tick(
                        symbol, bid=None, ask=None, quote_time_msc=tick_msc or None,
                        source="mt5_position_attention" if not paper_mode else "paper_position_attention",
                        quality="MISSING_OR_INVALID",
                        payload={"position_management": True},
                    )
                self.operations.event("PositionManager", "position_tick_missing", {"symbol": symbol},
                                      run_id=self.run_id, severity="WARNING")
                return {"status": "SKIPPED", "reason": "missing_or_invalid_quote"}
            now_msc = int(time.time() * 1000)
            tick_age_ms = max(0, now_msc - tick_msc) if tick_msc else None
            lag_envelope = self._ingest_lag_envelope(
                symbol, tick=tick, tick_age_ms=tick_age_ms, atr=atr if atr > 0 else 0.0,
                source="mt5_position_attention" if not paper_mode else "paper_position_attention",
                mid_price=(bid + ask) / 2.0,
            )
            if hasattr(self.store, "record_market_tick"):
                self.store.record_market_tick(
                    symbol, bid=bid, ask=ask, quote_time_msc=tick_msc or None,
                    tick_age_ms=tick_age_ms, source="mt5_position_attention" if not paper_mode else "paper_position_attention",
                    quality="FRESH" if tick_age_ms is not None and tick_age_ms <= 5000 else "STALE",
                    payload={"position_management": True, "lag_envelope": lag_envelope},
                )
            if not paper_mode and (tick_age_ms is None or tick_age_ms > 5000):
                self.operations.event("PositionManager", "position_tick_stale", {
                    "symbol": symbol, "tick_age_ms": tick_age_ms,
                    "lag_confidence": lag_envelope.get("lag_confidence"),
                }, run_id=self.run_id, severity="WARNING")
                return {"status": "SKIPPED", "reason": "stale_quote", "tick_age_ms": tick_age_ms,
                        "lag_envelope": lag_envelope}
            if not math.isfinite(atr) or atr <= 0:
                atr = (bid * 0.001) if bid > 0 else 0.0010  # defensive fallback ATR for paper/uncalculated state

            positions = self.data_provider.trade_manager._get_positions(symbol=symbol)
            if not positions:
                return {"status": "NO_POSITIONS", "tick_age_ms": tick_age_ms}
            midpoint = (bid + ask) / 2.0
            self.data_provider.trade_manager.monitor_positions(symbol, midpoint, atr)
            # --- Rule-based PositionManager (bypasses LLM for management) ---
            if self.position_manager is not None:
                try:
                    for pos in positions:
                        ticket = int(getattr(pos, "ticket", 0) or 0)
                        if ticket and ticket not in self.position_manager.positions:
                            _side = "BUY" if getattr(pos, "type", 0) == 0 else "SELL"
                            _sl = float(getattr(pos, "sl", 0) or 0)
                            _tp = float(getattr(pos, "tp", 0) or 0)
                            _comment = str(getattr(pos, "comment", "") or "").lower()
                            _magic = int(getattr(pos, "magic", 0) or 0)
                            _is_neocortex = (
                                _magic == 460003  # NEOCORTEX_MAGIC
                                or "neocortex" in _comment
                                or "macro" in _comment
                                or bool(getattr(pos, "is_neocortex", False))
                            )
                            self.position_manager.register_position(
                                ticket=ticket, symbol=symbol, side=_side,
                                entry_price=float(getattr(pos, "price_open", 0) or 0),
                                sl=_sl, tp=_tp,
                                volume=float(getattr(pos, "volume", 0) or 0),
                                atr=atr,
                                is_neocortex=_is_neocortex,
                            )
                    _pm_actions = self.position_manager.manage(
                        current_price=midpoint, atr=atr,
                    )
                    for _act in _pm_actions:
                        if _act.action == "MODIFY_SL" and _act.new_sl:
                            logger.info("PositionManager: MODIFY_SL #%d -> %.5f (%s)",
                                        _act.ticket, _act.new_sl, _act.reason)
                            try:
                                self.data_provider.trade_manager._modify_sl(
                                    _act.ticket, _act.new_sl, _act.reason)
                            except Exception as _mod_exc:
                                logger.debug("PM SL modify failed: %s", _mod_exc)
                        elif _act.action == "PARTIAL_CLOSE":
                            logger.info("PositionManager: PARTIAL_CLOSE #%d frac=%.2f (%s)",
                                        _act.ticket, _act.close_fraction or 0, _act.reason)
                            try:
                                self.data_provider.trade_manager._execute_partial_close(
                                    self.data_provider.trade_manager._find_pos(_act.ticket),
                                    fraction=_act.close_fraction or 0.5)
                            except Exception as _pc_exc:
                                logger.debug("PM partial close failed: %s", _pc_exc)
                        elif _act.action == "FULL_CLOSE":
                            logger.info("PositionManager: FULL_CLOSE #%d (%s)",
                                        _act.ticket, _act.reason)
                            try:
                                self.data_provider.trade_manager._close_position(
                                    _act.ticket, _act.symbol, _act.reason[:31])
                            except Exception as _fc_exc:
                                logger.debug("PM full close failed: %s", _fc_exc)
                except Exception as _pm_exc:
                    logger.debug("PositionManager tick failed: %s", _pm_exc)
            observed = 0
            for position in positions:
                ticket = int(getattr(position, "ticket", 0) or 0)
                if not ticket or self.store.trade_risk(ticket) <= 0:
                    continue
                is_buy = getattr(position, "type", None) == getattr(mt5, "POSITION_TYPE_BUY", getattr(mt5, "ORDER_TYPE_BUY", 0))
                mark = bid if is_buy else ask
                self.store.record_position_observation(
                    ticket, mark, float(getattr(position, "profit", 0) or 0),
                    source="mt5_tick", quote_time_msc=tick_msc or None,
                    tick_age_ms=tick_age_ms, spread=ask - bid,
                    mark_quality="FRESH", observed_at=datetime.now(timezone.utc),
                )
                observed += 1
            return {"status": "MANAGED", "positions": len(positions), "observed": observed,
                    "tick_age_ms": tick_age_ms, "spread": ask - bid}
        except Exception as exc:
            self.operations.event("PositionManager", "position_tick_management_error",
                                  {"symbol": symbol, "error": str(exc)[:500]},
                                  run_id=self.run_id, severity="ERROR")
            return {"status": "ERROR", "reason": str(exc)[:500]}

    def _record_advisory_management_suggestions(
        self,
        decision: Dict[str, Any],
        *,
        symbol: str,
        strategy: str = "",
        cycle_key: str = "",
    ) -> Dict[str, Any]:
        """Journal LLM management hypotheses without granting order authority.

        Max-efficiency split:
        - Analysis lane (candle/event): LLM may propose entry + advisory exit prefs.
        - Management lane (every pulse): TradeManager.monitor_positions / tick path acts.
        - Research lane (slow): memory/distillation only.

        Raw management_decisions must never call execute_modify_sl / partial /
        close helpers. That was the architecture leak vs \"LLM only proposes\".
        """
        raw = decision.get("management_decisions") or []
        if not isinstance(raw, list):
            raw = []
        suggestions: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", "") or "").upper()
            if action not in {"UPDATE_SL", "PARTIAL_CLOSE", "CLOSE_FULL", "HOLD", ""}:
                action = "HOLD"
            ticket = item.get("ticket")
            try:
                ticket_i = int(ticket) if ticket not in (None, "") else None
            except (TypeError, ValueError):
                ticket_i = None
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            suggestions.append({
                "ticket": ticket_i,
                "action": action or "HOLD",
                "params": params,
                "reasoning": str(item.get("reasoning", "") or "")[:500],
                "execution_authority": False,
                "status": "ADVISORY_ONLY",
            })
        envelope = {
            "symbol": symbol,
            "strategy": strategy,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "suggestions": suggestions,
            "count": len(suggestions),
            "execution_authority": False,
            "limitation": (
                "LLM management suggestions are research/journal context only; "
                "deterministic PositionManager owns SL/trail/partial/close."
            ),
        }
        decision["management_advisory"] = envelope
        # Strip executable semantics from the raw field so downstream UI/tools
        # cannot treat the LLM array as an order batch.
        decision["management_decisions"] = suggestions
        try:
            self.store.set_state(f"management_advisory:{symbol}", envelope)
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").warning(
                "Failed to persist management advisory for %s: %s", symbol, exc,
            )
        if hasattr(self.operations, "put_working_memory") and suggestions:
            try:
                self.operations.put_working_memory(
                    f"trading:{strategy}:{symbol}" if strategy else f"trading:{symbol}",
                    "management_advisory",
                    envelope,
                    kind="advisory",
                    priority=40,
                    run_id=self.run_id,
                )
            except Exception as exc:
                self.operations.event(
                    "memory", "management_advisory_memory_failed",
                    {"symbol": symbol, "error": str(exc)[:400]},
                    run_id=self.run_id, correlation_id=cycle_key or symbol,
                    severity="WARNING",
                )
        if suggestions:
            self.operations.event(
                "MarketModel", "management_suggestions_advisory",
                {"symbol": symbol, "count": len(suggestions), "execution_authority": False},
                run_id=self.run_id, correlation_id=cycle_key or symbol,
            )
            for item in suggestions:
                ticket = item.get("ticket")
                self.store.log_lifecycle_event(
                    ticket if ticket is not None else None,
                    "",
                    "ADVISORY",
                    "llm_management_suggestion",
                    {
                        "action": item.get("action"),
                        "params": item.get("params"),
                        "reason": item.get("reasoning"),
                        "execution_authority": False,
                    },
                )
        return envelope

    def _record_advisory_conditional_scenarios(
        self,
        decision: Dict[str, Any],
        *,
        symbol: str,
        strategy: str = "",
        cycle_key: str = "",
    ) -> Dict[str, Any]:
        """Journal LLM conditional_scenarios without arming TradeManager closes."""
        raw = decision.get("conditional_scenarios") or []
        if not isinstance(raw, list):
            raw = []
        suggestions: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            suggestions.append({
                "condition": str(item.get("condition", "") or "")[:300],
                "action": str(item.get("action", "") or "")[:120],
                "execution_authority": False,
                "status": "ADVISORY_ONLY",
            })
        envelope = {
            "symbol": symbol,
            "strategy": strategy,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "scenarios": suggestions,
            "count": len(suggestions),
            "execution_authority": False,
            "limitation": (
                "LLM conditional_scenarios are research/watchlist context only; "
                "deterministic PositionManager owns exits."
            ),
        }
        decision["conditional_advisory"] = envelope
        decision["conditional_scenarios"] = suggestions
        try:
            self.store.set_state(f"conditional_advisory:{symbol}", envelope)
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").warning(
                "Failed to persist conditional advisory for %s: %s", symbol, exc,
            )
        # Keep TradeManager watchlist in sync as advisory-only (non-executable).
        if self.data_provider.trade_manager and suggestions:
            try:
                self.data_provider.trade_manager.register_conditions(symbol, suggestions)
            except Exception as exc:
                logging.getLogger("LLMOrchestrator").warning(
                    "Failed to sync advisory conditions for %s: %s", symbol, exc,
                )
        if suggestions:
            self.operations.event(
                "MarketModel", "conditional_scenarios_advisory",
                {"symbol": symbol, "count": len(suggestions), "execution_authority": False},
                run_id=self.run_id, correlation_id=cycle_key or symbol,
            )
        return envelope
    
    def _rl_learning_allowed(self) -> bool:
        """Allow Q-learning on reconciled closes for paper/demo; LIVE needs explicit opt-in.

        Decision influence remains separately gated by ``rl_offline_updates_enabled`` /
        paper_mode in the analysis cycle. Learning from outcomes must still run for
        paper/demo so the Q-table is not permanently disconnected from trade closes.
        """
        if not self.enable_rl or not self.rl_trainer:
            return False
        if self.rl_offline_updates_enabled:
            # The runtime process is never an offline trainer. A LIVE scope
            # must learn only from a separately controlled offline job.
            mode = str(getattr(getattr(self.store, "scope", None), "mode", "") or "").upper()
            if mode == "LIVE":
                return False
            return True
        mode = str(getattr(getattr(self.store, "scope", None), "mode", "") or "").upper()
        if mode in {"PAPER", "DEMO", "SIM", "SIMULATION"}:
            return True
        learn_env = os.getenv("TRADING_RL_LEARN_ON_CLOSE", "true").lower() in {"1", "true", "yes"}
        if mode == "LIVE":
            return False
        return learn_env

    def _research_influence_allowed(self, model_kind: str, *, paper_mode: bool) -> bool:
        """Permit adaptive-model influence only in paper or approved research state."""
        if paper_mode:
            return True
        key = str(model_kind or "all").lower()
        now = time.monotonic()
        cached = self._research_influence_cache.get(key)
        if cached and now - cached[0] < 30.0:
            return cached[1]
        allowed = research_influence_allows(
            getattr(self.store, "path", "trading_state.db"), model_kind,
        )
        self._research_influence_cache[key] = (now, bool(allowed))
        return bool(allowed)

    def update_rl_from_trade(self, trade_result: Dict[str, Any]):
        """Update RL model from a broker-reconciled closed trade."""
        if not self._rl_learning_allowed():
            logging.getLogger("LLMOrchestrator").info(
                "RL update refused: learning disabled for this journal scope "
                "(offline updates switch plus research-influence approval are required for LIVE)"
            )
            return

        log = logging.getLogger("LLMOrchestrator")

        try:
            ticket_i = None
            ticket = trade_result.get("ticket")
            if ticket is not None:
                try:
                    ticket_i = int(ticket)
                except (TypeError, ValueError):
                    ticket_i = None
                if ticket_i is not None and ticket_i in self._rl_updated_tickets:
                    return

            state = trade_result.get("rl_state")
            action = str(trade_result.get("rl_action") or trade_result.get("action") or "").upper()
            if action not in {"BUY", "SELL", "HOLD"}:
                action = ""

            if not state or not action:
                log.warning("Trade missing RL state/action, skipping RL update")
                return

            if not trade_result.get("broker_reconciled", False):
                log.warning("RL update blocked: close is not marked broker-reconciled")
                return

            try:
                from reward_attribution import enrich_trade_result_attribution
                trade_result = enrich_trade_result_attribution(trade_result, self.store)
            except Exception as exc:
                logging.getLogger("LLMOrchestrator").debug("Reward attribution enrichment failed: %s", exc)

            breakdown = self.rl_trainer.calculate_reward_breakdown(trade_result)
            if breakdown.get("status") != "OK":
                log.warning(
                    "RL update blocked: reward status=%s reason=%s",
                    breakdown.get("status"),
                    breakdown.get("reason"),
                )
                return
            reward = float(breakdown["reward"])
            next_state = trade_result.get("exit_state") or {
                "position": "FLAT", "r_multiple": breakdown.get("r_net", trade_result.get("r_multiple")),
                "realized_pnl": trade_result.get("pnl", 0.0),
                "max_drawdown": trade_result.get("max_drawdown", 0.0),
                "duration_seconds": trade_result.get("duration_seconds", 0.0),
            }

            self.rl_trainer.update(state, action, reward, next_state, done=True)
            if ticket_i is not None:
                self._rl_updated_tickets.add(ticket_i)
                if len(self._rl_updated_tickets) > 2000:
                    self._rl_updated_tickets = set(sorted(self._rl_updated_tickets)[-1000:])
            try:
                from reward_ledger import emit_terminal_trade_rewards
                lifecycle_lookup = None
                try:
                    from edge_lifecycle import current_lifecycle
                    graph = getattr(self, "macro_graph", None)
                    if graph is not None:
                        def lifecycle_lookup(edge_id, _graph=graph):
                            return current_lifecycle(_graph, int(edge_id)).get("lifecycle")
                except Exception:
                    lifecycle_lookup = None
                emit_terminal_trade_rewards(
                    getattr(self.store, "path", "trading_state.db"),
                    trade_result,
                    breakdown,
                    run_id=getattr(self, "run_id", None),
                    lifecycle_lookup=lifecycle_lookup,
                )
            except Exception as reward_exc:
                log.warning("reward_events emit failed: %s", str(reward_exc)[:300])
            log.info(
                "RL Updated: Action=%s, Reward=%.3f, R_net=%s, PnL=%.2f",
                action,
                reward,
                breakdown.get("r_net"),
                float(trade_result.get("pnl", 0) or 0),
            )
            if self.rl_trainer.total_updates % 10 == 0:
                self.rl_trainer.save_model()
            if self.rl_trainer.total_updates % 50 == 0:
                self.rl_trainer.decay_epsilon()

        except Exception as e:
            log.error(f"RL update failed: {e}")

    def _estimate_friction_r(self, entry: float, sl: float, tick: Any,
                             execution_policy: Any = None) -> float:
        """Round-trip trading cost expressed in R units.

        Approx 1.5 x spread (half-spread entry + full-spread exit) plus the
        learned expected slippage, divided by the stop distance. Feeds the
        validator net-expectancy floor so a gross edge cannot silently pass
        as positive once costs are paid.
        """
        try:
            dist = abs(float(entry) - float(sl))
            if not math.isfinite(dist) or dist <= 0:
                return 0.05
            ask = float(getattr(tick, "ask", 0.0) or 0.0)
            bid = float(getattr(tick, "bid", 0.0) or 0.0)
            spread = abs(ask - bid) if ask > 0 and bid > 0 else 0.0
            slip = 0.0
            policy = execution_policy if isinstance(execution_policy, dict) else {}
            predicted = policy.get("expected_slippage_price")
            if isinstance(predicted, (int, float)) and math.isfinite(predicted):
                slip = abs(float(predicted))
            return (1.5 * max(spread, 0.0) + slip) / dist
        except Exception:
            return 0.05

    def _regime_changepoint_probability(self, toon: Dict[str, Any]) -> Optional[float]:
        """BOCPD regime-break probability, fed ONCE per cycle (newest bar only).

        Uses percent log-returns so the detector variance floors are meaningful
        at FX bar scale. State persists on the orchestrator instance; a restart
        simply rebuilds run-length history from the next bars.
        """
        import math as _math
        detector = getattr(self, "_regime_bocpd", None)
        if detector is None:
            try:
                from core.bocpd import BayesianOnlineChangepointDetector
            except Exception:
                try:
                    from bocpd import BayesianOnlineChangepointDetector
                except Exception:
                    return None
            detector = BayesianOnlineChangepointDetector(
                hazard_lambda=250.0, mean_prior=0.0, var_prior=0.01)
            self._regime_bocpd = detector
        closes: list = []
        for row in (toon.get("ohlc") or []):
            if isinstance(row, dict):
                val = row.get("close")
                if isinstance(val, (int, float)) and val > 0:
                    closes.append(float(val))
        if not closes:
            try:
                return float(detector.changepoint_probability)
            except Exception:
                return None
        last_close = closes[-1]
        prev_close = getattr(self, "_bocpd_last_close", None)
        self._bocpd_last_close = last_close
        if prev_close and prev_close > 0 and prev_close != last_close:
            try:
                detector.update(100.0 * _math.log(last_close / prev_close))
            except Exception:
                return None
        try:
            return float(detector.changepoint_probability)
        except Exception:
            return None

    def _get_win_probability_model(self):
        """Lazily train/retrain the calibrated P(win) model from the journal."""
        counter = getattr(self, "_win_model_calls", 0) + 1
        self._win_model_calls = counter
        model = getattr(self, "_win_probability_model", None)
        if model is not None and counter % 200 != 1:
            return model
        try:
            from intelligence.win_probability_model import WinProbabilityModel
        except Exception:
            try:
                from win_probability_model import WinProbabilityModel
            except Exception:
                return model
        try:
            trained = WinProbabilityModel.train_from_store(
                str(getattr(self.store, "path", "trading_state.db")), min_samples=30)
            if trained is not None:
                self._win_probability_model = trained
                self.operations.event(
                    "risk", "win_probability_model_ready",
                    {"sample_count": trained.sample_count,
                     "train_win_rate": round(trained.train_win_rate, 4)},
                    run_id=self.run_id,
                )
                return trained
        except Exception:
            return model
        return model

    def _sizing_confidence(self, decision: Dict[str, Any]) -> float:
        """Sizing-only confidence: blend raw LLM confidence with calibrated P(win).

        The calibrated estimate can only DOWNSIZE: the blend is capped at the
        raw confidence so an unproven model can never inflate risk. Execution
        authority is untouched; this only scales position size.
        """
        raw = decision.get("confidence", 0.0)
        try:
            base = max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError, OverflowError):
            base = 0.0
        model = self._get_win_probability_model()
        if model is None:
            return base
        try:
            features = dict(decision.get("features") or {})
            p_win = model.predict_calibrated(features)
            if p_win is None:
                return base
            blended = 0.5 * base + 0.5 * float(p_win)
            return max(0.0, min(blended, base))
        except Exception:
            return base

    def _journal_closed_r_multiples(self, limit: int = 120) -> List[float]:
        """Realised R-multiples of reconciled closed trades, newest → oldest.

        Uses only labelled closed trades carrying R-multiples (the same
        durable-journal source as the validator's rolling EV).  Any journal
        problem degrades to an empty sample rather than blocking the cycle.
        """
        try:
            with self.store._connect() as con:
                rows = con.execute(
                    """SELECT l.r_multiple AS r_multiple
                       FROM trade_labels l JOIN trades t ON t.ticket = l.ticket
                       WHERE t.closed_at IS NOT NULL AND l.r_multiple IS NOT NULL
                       ORDER BY t.closed_at DESC LIMIT ?""",
                    (int(limit),),
                ).fetchall()
            return [float(r["r_multiple"]) for r in rows if r["r_multiple"] is not None]
        except Exception:
            return []

    def _attach_quant_survival_audit(self, toon: Dict[str, Any]) -> None:
        """Attach bounded advisory survival/edge evidence for this cycle.

        Composes Expected Value, risk-of-ruin, quarter-Kelly, run-theory L_max
        and the Hurst regime character of the completed-bar closes into
        ``toon['quant_audit']`` so the decision model reasons over realised
        journal statistics instead of assumptions.  Purely advisory: outputs
        are descriptive evidence and can never grant execution authority,
        raise a deterministic cap, or bypass validation/risk arbitration.
        """
        try:
            from core.metrics import calculate_survival_edge_audit, calculate_hurst_exponent
        except Exception:
            try:
                from metrics import calculate_survival_edge_audit, calculate_hurst_exponent
            except Exception:
                return
        try:
            audit = calculate_survival_edge_audit(self._journal_closed_r_multiples())
            hurst = None
            try:
                closes = [
                    float(bar.get("close"))
                    for bar in (toon.get("ohlc") or [])[:-1]
                    if isinstance(bar, dict) and bar.get("close") is not None
                ]
                if len(closes) >= 40:
                    hurst = calculate_hurst_exponent(closes)
            except Exception:
                hurst = None
            if isinstance(hurst, (int, float)) and hurst == hurst and abs(hurst) != float("inf"):
                audit["hurst_exponent"] = round(float(hurst), 4)
            else:
                audit["hurst_exponent"] = None

            if getattr(self, "metrics_service", None) is not None:
                try:
                    self.metrics_service.sync_from_journal(self.store)
                    eq_val = (toon.get("account") or {}).get("equity") or getattr(self.risk, "equity", None)
                    if eq_val and isinstance(eq_val, (int, float)) and eq_val > 0:
                        self.metrics_service.update_equity(float(eq_val))
                    audit["institutional_metrics"] = self.metrics_service.get_metrics_summary()
                except Exception as _ms_exc:
                    logging.getLogger("LLMOrchestrator").debug("Metrics service cycle sync skipped: %s", _ms_exc)

            toon["quant_audit"] = audit
            self.operations.agent_event(
                "QuantitativeCoach", "quant_survival_audit", "research_report",
                "cycle_survival_audit_attached", dict(audit),
                run_id=self.run_id, severity="INFO",
            )
        except Exception as exc:
            self.operations.event(
                "quant", "cycle_survival_audit_failed", {"error": str(exc)[:400]},
                run_id=self.run_id, severity="WARNING",
            )

    def _apply_advanced_auto_tools(self, toon: Dict[str, Any], paper_mode: bool = True) -> None:
        """Wire the 6 advanced auto-tool outputs into the cycle's decision context.

        This is called ONCE per cycle, BEFORE the LLM call, so that:
        - meta_adaptive adjusts risk_manager params (risk_multiplier, position_scale)
        - self_improvement applies validated parameter changes
        - evolutionary injects best-gene SL/TP bounds as suggestions
        - cross_market sets a robustness gate (fragile strategy → HOLD bias)
        - alpha_discovery enriches the alpha ensemble with newly validated alphas
        - multimodal injects chart-pattern confluence into the toon for the LLM

        All outputs are ADVISORY: they modify context and suggestions, never
        execution authority. Deterministic gates in _validate_proposal remain
        the final arbiter.
        """
        try:
            aqt = toon.get("auto_quant_tools", {})
            results = aqt.get("results", {})
            if not results:
                return
            log = logging.getLogger("LLMOrchestrator")

            # --- 1. META-ADAPTIVE: adjust risk parameters ---
            meta = results.get("meta_adaptive", {})
            if meta.get("status") != "ERROR" and isinstance(meta.get("adapted_profile"), dict):
                profile = meta["adapted_profile"]
                risk_mult = float(profile.get("risk_multiplier", 1.0))
                pos_scale = float(profile.get("position_scale", 1.0))
                if risk_mult != 1.0:
                    # Apply to risk_per_trade (bounded 0.002 - 0.02)
                    base_risk = float(getattr(self.risk, "risk_per_trade", 0.01))
                    adapted_risk = max(0.002, min(0.02, base_risk * risk_mult))
                    adaptive_allowed = self._research_influence_allowed("adaptive", paper_mode=paper_mode)
                    if adaptive_allowed:
                        self.risk.risk_per_trade = adapted_risk
                        log.info(
                            "Meta-adaptive: risk_per_trade %.4f -> %.4f (mult=%.2f, regime=%s)",
                            base_risk, adapted_risk, risk_mult, meta.get("current_regime", "?"),
                        )
                    else:
                        log.info(
                            "Meta-adaptive advisory only: suggested risk %.4f -> %.4f (mult=%.2f); unmutated without research approval",
                            base_risk, adapted_risk, risk_mult,
                        )
                    toon["meta_adaptive_override"] = {
                        "risk_multiplier": risk_mult,
                        "adapted_risk_per_trade": adapted_risk,
                        "position_scale": pos_scale,
                        "regime": meta.get("current_regime", "UNKNOWN"),
                        "transition_detected": meta.get("transition_detected", False),
                        "applied": adaptive_allowed,
                    }

            # --- 2. SELF-IMPROVEMENT: apply validated param changes ---
            si = results.get("self_improvement", {})
            if si.get("improvement_applied") and isinstance(si.get("applied_params"), dict):
                params = si["applied_params"]
                if "entry_confidence_threshold" in params:
                    toon["self_improvement_entry_threshold"] = float(params["entry_confidence_threshold"])
                if "risk_per_trade" in params:
                    new_risk = float(params["risk_per_trade"])
                    # Only apply if within sane bounds
                    if 0.002 <= new_risk <= 0.02:
                        si_allowed = self._research_influence_allowed("adaptive", paper_mode=paper_mode)
                        if si_allowed:
                            self.risk.risk_per_trade = new_risk
                            log.info("Self-improvement: risk_per_trade -> %.4f", new_risk)
                        else:
                            log.info("Self-improvement advisory only: suggested risk -> %.4f; unmutated without research approval", new_risk)
                        toon["self_improvement_risk_override"] = new_risk
                if "max_drawdown_pct" in params:
                    toon["self_improvement_max_dd"] = float(params["max_drawdown_pct"])
                log.info(
                    "Self-improvement applied: %s (+%.1f%% composite)",
                    si.get("proposal_type", "unknown"), si.get("improvement_pct", 0),
                )

            # --- 3. EVOLUTIONARY: inject best gene as SL/TP/entry bounds ---
            evo = results.get("evolutionary", {})
            if isinstance(evo.get("best_gene"), dict):
                gene = evo["best_gene"]
                params = gene.get("params", {})
                toon["evolutionary_suggestions"] = {
                    "risk_per_trade": params.get("risk_per_trade"),
                    "entry_confidence": params.get("entry_confidence_threshold"),
                    "sl_atr_mult": params.get("sl_atr_multiplier"),
                    "tp_atr_mult": params.get("tp_atr_multiplier"),
                    "fitness": gene.get("fitness"),
                    "generation": evo.get("generations_run", 0),
                    "generation_evaluated": evo.get("generation_evaluated", 0),
                }
                log.info(
                    "Evolutionary: best gene fitness=%.4f, risk=%.4f, sl_atr=%.2f, tp_atr=%.2f",
                    gene.get("fitness", 0), params.get("risk_per_trade", 0),
                    params.get("sl_atr_multiplier", 0), params.get("tp_atr_multiplier", 0),
                )

            # --- 4. CROSS-MARKET: robustness gate ---
            xmarket = results.get("cross_market", {})
            if isinstance(xmarket.get("recommendation"), dict):
                rec = xmarket["recommendation"]
                composite = float(xmarket.get("composite_score", 0.5))
                strength = rec.get("strength", "MODERATE")
                toon["cross_market_robustness"] = {
                    "composite_score": composite,
                    "strength": strength,
                    "best_instrument": xmarket.get("leaderboard", [{}])[0].get("symbol") if xmarket.get("leaderboard") else None,
                    "correlation_warnings": rec.get("correlation_warnings", []),
                    "regime_warnings": rec.get("regime_warnings", []),
                }
                # Fragile strategy: inject HOLD bias into context
                if strength == "WEAK" or composite < 0.4:
                    toon["cross_market_hold_bias"] = True
                    log.warning(
                        "Cross-market: strategy fragile (score=%.2f, strength=%s). HOLD bias injected.",
                        composite, strength,
                    )

            # --- 5. ALPHA-DISCOVERY: enrich ensemble with new alphas ---
            adisc = results.get("alpha_discovery", {})
            if adisc.get("validated_count", 0) > 0 and isinstance(adisc.get("validated_alphas"), list):
                toon["alpha_discovery_enrichment"] = {
                    "new_alphas": adisc["validated_alphas"],
                    "validated_count": adisc["validated_count"],
                    "rejected_count": adisc.get("rejected_count", 0),
                    "rejection_reasons": adisc.get("rejection_reasons", []),
                }
                log.info(
                    "Alpha discovery: %d new alphas validated, %d rejected",
                    adisc["validated_count"], adisc.get("rejected_count", 0),
                )

            # --- 6. MULTIMODAL: inject pattern confluence into toon ---
            multi = results.get("multimodal", {})
            if isinstance(multi.get("confluence"), dict):
                confluence = multi["confluence"]
                toon["multimodal_patterns"] = {
                    "confluence_score": confluence.get("score", 0),
                    "direction": confluence.get("direction", "NEUTRAL"),
                    "pattern_count": confluence.get("pattern_count", 0),
                    "patterns": multi.get("patterns", []),
                    "trend": multi.get("trend", {}),
                    "support_resistance": multi.get("support_resistance", {}),
                    "volume_patterns": multi.get("volume_patterns", []),
                }
                # Boost or reduce confidence based on pattern confluence
                cs = float(confluence.get("score", 0))
                if cs >= 0.7:
                    log.info(
                        "Multimodal: strong pattern confluence (score=%.2f, dir=%s)",
                        cs, confluence.get("direction"),
                    )
                elif cs <= -0.7:
                    log.info(
                        "Multimodal: strong bearish pattern confluence (score=%.2f, dir=%s)",
                        cs, confluence.get("direction"),
                    )

            # --- 7. CRASH DETECTOR (Logic-Q neuro-symbolic) ---
            crash = results.get("crash_detector", {})
            if isinstance(crash, dict) and crash.get("trend") and crash.get("status") != "ERROR":
                trend = crash.get("trend", "OSCILLATION")
                risk_mult = float(crash.get("risk_multiplier", 1.0))
                entries_ok = crash.get("entries_allowed", True)
                crash_streak = int(crash.get("crash_streak", 0))

                toon["crash_detector_signal"] = {
                    "trend": trend,
                    "risk_multiplier": risk_mult,
                    "position_scale": float(crash.get("position_scale", 1.0)),
                    "entries_allowed": entries_ok,
                    "volatility": crash.get("volatility", 0),
                    "downside_risk": crash.get("downside_risk", 0),
                    "growth_rate": crash.get("growth_rate", 0),
                    "confidence_score": crash.get("confidence_score", 0),
                    "crash_streak": crash_streak,
                }

                # Apply crash detector risk multiplier (layered on top of meta-adaptive)
                if risk_mult != 1.0:
                    base_risk = float(getattr(self.risk, "risk_per_trade", 0.01))
                    crash_risk = max(0.001, min(0.02, base_risk * risk_mult))
                    self.risk.risk_per_trade = crash_risk
                    log.info(
                        "Crash detector: risk_per_trade -> %.4f (trend=%s, streak=%d)",
                        crash_risk, trend, crash_streak,
                    )

                # CRITICAL: Block entries on RAPID_DESCEND or crash streak >= 3
                if trend == "RAPID_DESCEND" or crash_streak >= 3:
                    toon["crash_detector_hold_bias"] = True
                    log.warning(
                        "Crash detector: HOLD bias active (trend=%s, streak=%d)",
                        trend, crash_streak,
                    )

            # --- 8. CAUSAL FILTER (CRL-ABSO) ---
            causal = results.get("causal_filter", {})
            if isinstance(causal, dict) and causal.get("status") not in ("ERROR", "INSUFFICIENT_DATA", "NO_FEATURES", None):
                toon["causal_filter_report"] = {
                    "causal_features": causal.get("causal_features", []),
                    "spurious_features": causal.get("spurious_features", []),
                    "confounders": causal.get("confounders", []),
                    "causal_density": causal.get("causal_density", 0),
                    "top_scores": causal.get("top_scores", []),
                }
                # If high spurious count, inject a warning for the LLM
                spurious_count = int(causal.get("spurious_count", 0))
                if spurious_count > 2:
                    log.info(
                        "Causal filter: %d spurious features detected, confounders: %s",
                        spurious_count, causal.get("confounders", []),
                    )

            # --- 9. WALK-FORWARD VALIDATOR (Author-C rolling decay detection) ---
            wf = results.get("walk_forward", {})
            if isinstance(wf, dict) and wf.get("status") not in ("ERROR", "INSUFFICIENT_DATA", None):
                toon["walk_forward_decay"] = {
                    "status": wf.get("status", "HEALTHY"),
                    "current_ic": wf.get("current_ic", 0),
                    "mean_ic": wf.get("mean_ic", 0),
                    "ic_trend": wf.get("ic_trend", 0),
                    "decay_velocity": wf.get("decay_velocity", 0),
                    "half_life": wf.get("half_life"),
                    "bars_below_threshold": wf.get("bars_below_threshold", 0),
                    "consecutive_declines": wf.get("consecutive_declines", 0),
                    "alerts": wf.get("alerts", []),
                    "recommendations": wf.get("recommendations", []),
                    "wfe": wf.get("wfe"),
                }
                # WFE gate: curve-fit rejection when out-of-sample edge collapses.
                _wfe = wf.get("wfe")
                if isinstance(_wfe, dict) and _wfe.get("n_folds", 0) >= 2:
                    toon["walk_forward_wfe"] = _wfe
                    if not _wfe.get("passed", False) and _wfe.get("mean_test_sharpe", 0) <= 0:
                        # OOS leg loses money outright — treat like DEAD alpha.
                        toon["walk_forward_hold_bias"] = True
                        log.warning(
                            "Walk-forward WFE FAIL: %s — HOLD bias injected.",
                            _wfe.get("reason", "unknown"),
                        )
                # DEAD alpha: inject HOLD bias — alpha is harmful
                if wf.get("status") == "DEAD":
                    toon["walk_forward_hold_bias"] = True
                    log.warning(
                        "Walk-forward: DEAD alpha (IC=%.4f). HOLD bias injected.",
                        wf.get("current_ic", 0),
                    )
                elif wf.get("status") == "DECAYING":
                    log.warning(
                        "Walk-forward: DECAYING alpha (IC=%.4f, half_life=%s).",
                        wf.get("current_ic", 0), wf.get("half_life"),
                    )

            # --- 10b. DATA-DRIFT DETECTOR (PSI/KS feature-distribution drift) ---
            drift = results.get("data_drift", {})
            if isinstance(drift, dict) and drift.get("status") in ("SHIFT", "DRIFT"):
                toon["data_drift"] = {
                    "status": drift.get("status"),
                    "psi_major_count": drift.get("psi_major_count", 0),
                    "psi_shift_count": drift.get("psi_shift_count", 0),
                    "ks_significant_count": drift.get("ks_significant_count", 0),
                    "drift_fraction": drift.get("drift_fraction", 0),
                    "drifted_features": drift.get("drifted_features", []),
                }
                if drift.get("status") == "DRIFT":
                    toon["data_drift_hold_bias"] = True
                    log.warning(
                        "Data drift DRIFT: %d major PSI features (%.0f%% of checked) "
                        "— models may be stale. HOLD bias injected.",
                        drift.get("psi_major_count", 0),
                        (drift.get("drift_fraction", 0) or 0) * 100,
                    )

            # --- 11. EWC FORGETTING PREVENTION (catastrophic forgetting in RL) ---
            ewc = results.get("ewc_status", {})
            if isinstance(ewc, dict) and ewc.get("available") and ewc.get("status") != "ERROR":
                toon["ewc_forgetting_status"] = {
                    "snapshots_count": ewc.get("snapshots_count", 0),
                    "total_states_protected": ewc.get("total_states_protected", 0),
                    "average_importance": ewc.get("average_importance", 0),
                    "ewc_lambda": ewc.get("ewc_lambda", 0),
                    "regimes_protected": ewc.get("regimes_protected", []),
                }
                # High importance = Q-values depend heavily on past regimes
                avg_imp = float(ewc.get("average_importance", 0))
                if avg_imp > 0.5:
                    log.info(
                        "EWC: high importance (%.3f) — Q-values heavily depend on %d past regimes.",
                        avg_imp, ewc.get("snapshots_count", 0),
                    )
            # --- EWC auto-snapshot on regime change ---
            try:
                _cp = toon.get("live_advisory_hub", {}).get("bocpd", {})
                if isinstance(_cp, dict) and _cp.get("should_hold"):
                    from core.ewc_forgetting_prevention import EWCForgettingPrevention
                    _ewc_inst = EWCForgettingPrevention(state_file="ewc_state.json")
                    _regime = toon.get("regime", "UNKNOWN")
                    if hasattr(_ewc_inst, "snapshot_regime"):
                        _ewc_inst.snapshot_regime(_regime, {}, {})
                        log.info("EWC: auto-snapshot on regime '%s' (BOCPD hold detected)", _regime)
            except Exception as _ewc_snap_exc:
                log.debug("EWC auto-snapshot skipped: %s", _ewc_snap_exc)

            # --- 12. OPTIMAL EXECUTION (Almgren-Chriss cost-aware scheduling) ---
            oex = results.get("optimal_execution", {})
            if isinstance(oex, dict) and oex.get("status") not in ("ERROR", "INSUFFICIENT_DATA", None):
                toon["optimal_execution_cost"] = {
                    "algorithm": oex.get("algorithm", "TWAP"),
                    "total_expected_cost": oex.get("total_expected_cost", 0),
                    "total_expected_slippage": oex.get("total_expected_slippage", 0),
                    "num_slices": oex.get("num_slices", 1),
                    "cost_aware_should_enter": oex.get("cost_aware_should_enter", True),
                    "cost_aware_position_scale": oex.get("cost_aware_position_scale", 1.0),
                    "net_edge": oex.get("net_edge", 0),
                }
                # If costs eat all edge, inject HOLD bias
                if not oex.get("cost_aware_should_enter", True):
                    toon["execution_cost_hold_bias"] = True
                    log.info(
                        "Optimal execution: costs exceed edge (net_edge=%.6f). HOLD bias.",
                        oex.get("net_edge", 0),
                    )
                # Scale down position if costs are high
                cost_scale = float(oex.get("cost_aware_position_scale", 1.0))
                if cost_scale < 1.0:
                    log.info(
                        "Optimal execution: position scaled to %.0f%% (net_edge=%.6f).",
                        cost_scale * 100, oex.get("net_edge", 0),
                    )

            # Store enrichment summary for the LLM prompt
            # Extract key fields from auto_quant_tools.results for quick access
            aqt_results = toon.get("auto_quant_tools", {}).get("results", {})
            bocpd = aqt_results.get("bocpd", {})
            kalman = aqt_results.get("kalman", {})
            cpcv = aqt_results.get("cpcv", {})
            regime_ac = aqt_results.get("regime_autocorr", {})
            meta_lp = aqt_results.get("meta_loop", {})
            alpha_stab = aqt_results.get("alpha_stability", {})
            canon = aqt_results.get("canonical_mastery", {})
            rl_a = aqt_results.get("rl_analytics", {})
            toon["advanced_auto_tools_summary"] = {
                # Core tools (per-cycle)
                "bocpd_shift": bool(bocpd.get("has_recent_structural_shift")),
                "bocpd_changepoints": bocpd.get("detected_changepoints_count", 0),
                "kalman_z_score": kalman.get("spread_z_score", 0),
                "kalman_regime": kalman.get("current_regime", ""),
                "cpcv_pbo": cpcv.get("probability_of_backtest_overfitting_pbo", 0),
                "cpcv_dsr": cpcv.get("deflated_sharpe_ratio", 0),
                "cpcv_status": cpcv.get("status", "never_run"),
                # Advanced tools (wired into toon)
                "meta_adaptive_active": meta.get("status") not in ("ERROR", None),
                "self_improvement_applied": bool(si.get("improvement_applied")),
                "evolutionary_best_fitness": evo.get("best_gene", {}).get("fitness") if isinstance(evo.get("best_gene"), dict) else None,
                "cross_market_score": xmarket.get("composite_score") if isinstance(xmarket.get("composite_score"), (int, float)) else None,
                "cross_market_strength": xmarket.get("recommendation", {}).get("strength") if isinstance(xmarket.get("recommendation"), dict) else None,
                "alpha_discovery_new_count": adisc.get("validated_count", 0),
                "multimodal_confluence": multi.get("confluence", {}).get("score") if isinstance(multi.get("confluence"), dict) else None,
                "crash_detector_trend": toon.get("crash_detector_signal", {}).get("trend"),
                "crash_detector_hold_bias": bool(toon.get("crash_detector_hold_bias", False)),
                "causal_density": toon.get("causal_filter_report", {}).get("causal_density", 0),
                "causal_spurious_count": len(toon.get("causal_filter_report", {}).get("spurious_features", [])),
                "walk_forward_status": toon.get("walk_forward_decay", {}).get("status"),
                "walk_forward_ic": toon.get("walk_forward_decay", {}).get("current_ic", 0),
                "walk_forward_wfe": (toon.get("walk_forward_wfe", {}) or {}).get("wfe"),
                "walk_forward_wfe_passed": (toon.get("walk_forward_wfe", {}) or {}).get("passed"),
                "data_drift_status": (toon.get("data_drift", {}) or {}).get("status"),
                "data_drift_hold_bias": bool(toon.get("data_drift_hold_bias", False)),
                "walk_forward_hold_bias": bool(toon.get("walk_forward_hold_bias", False)),
                "execution_cost_hold_bias": bool(toon.get("execution_cost_hold_bias", False)),
                "execution_cost_algorithm": oex.get("algorithm") if isinstance(oex, dict) else None,
                "ewc_snapshots": toon.get("ewc_forgetting_status", {}).get("snapshots_count", 0),
                "ewc_states_protected": toon.get("ewc_forgetting_status", {}).get("total_states_protected", 0),
                # Research modules
                "regime_autocorr_transition": bool(regime_ac.get("regime_transition_detected")),
                "regime_autocorr_risk": regime_ac.get("microstructure_risk", ""),
                "meta_loop_proposals_applied": meta_lp.get("proposals_applied", 0),
                "meta_loop_patterns_found": meta_lp.get("patterns_found", 0),
                "alpha_stability_fragile": alpha_stab.get("fragile_alphas", []),
                "alpha_stability_robust": alpha_stab.get("robust_alphas", []),
                "canonical_reflexivity": canon.get("reflexivity", {}).get("rdi", 0) if isinstance(canon.get("reflexivity"), dict) else 0,
                "canonical_minsky_fragility": canon.get("reflexivity", {}).get("minsky_fragility", 0) if isinstance(canon.get("reflexivity"), dict) else 0,
                "rl_analytics_states": rl_a.get("n_states", 0),
            }

        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug(
                "_apply_advanced_auto_tools failed: %s", exc,
            )

    def _apply_alpha_mining_enrichment(self, toon: Dict[str, Any]) -> None:
        """Wire alpha mining results into the cycle's decision context.

        Enriches the decision context with:
        - Active alpha strategies from the strategy factory
        - MegaAlpha composite signal
        - Alpha diversity score
        - Alpha decay alerts

        This is called ONCE per cycle, BEFORE the LLM call, so that the LLM
        can use alpha mining intelligence in its decision-making.
        """
        try:
            from research.alpha_miner.strategy_bridge import AlphaStrategyBridge
            from operations.strategy_factory import get_factory
            
            log = logging.getLogger("LLMOrchestrator")
            
            # Get strategy factory
            try:
                factory = get_factory()
            except Exception:
                log.debug("Alpha mining enrichment: strategy factory not available")
                return
            
            # Initialize bridge
            bridge = AlphaStrategyBridge(factory)
            
            # Get active alpha strategies
            active_alphas = bridge.get_active_alpha_strategies()
            
            if not active_alphas:
                log.debug("Alpha mining enrichment: no active alpha strategies")
                return
            
            # Calculate alpha diversity score
            diversity_score = bridge.get_diversity_score()
            
            # Check for alpha decay
            decay_alerts = []
            for alpha in active_alphas:
                alpha_id = alpha.get("strategy_id", "")
                ic_history = alpha.get("ic_history", [])
                if ic_history and len(ic_history) >= 3:
                    # Simple decay check: is current IC declining?
                    recent_ics = ic_history[-5:]
                    if len(recent_ics) >= 3:
                        slope = (recent_ics[-1] - recent_ics[0]) / len(recent_ics)
                        if slope < -0.001:  # Declining
                            decay_alerts.append({
                                "alpha_id": alpha_id,
                                "current_ic": recent_ics[-1],
                                "trend": slope,
                            })
            
            # Compute MegaAlpha composite signal (simple average of active alphas)
            alpha_composites = [a.get("ic_mean", 0.0) for a in active_alphas if a.get("ic_mean")]
            mega_alpha_composite = sum(alpha_composites) / len(alpha_composites) if alpha_composites else 0.0
            
            # Enrich context
            toon["alpha_mining_results"] = {
                "active_alpha_strategies": active_alphas[:10],  # Limit to 10 for context size
                "mega_alpha_composite": round(mega_alpha_composite, 4),
                "alpha_diversity_score": round(diversity_score, 4),
                "alpha_decay_alerts": decay_alerts,
                "total_active_alphas": len(active_alphas),
            }
            
            # Add microstructure signals from latest alpha mining sprint
            try:
                from operations.agents.research_agent import handle_alpha_mining_sprint
                # Check if we have cached microstructure results
                micro_cache = getattr(self, '_microstructure_cache', {})
                if micro_cache:
                    toon["alpha_mining_results"]["microstructure"] = {
                        "fvg": micro_cache.get("fvg"),
                        "kat": micro_cache.get("kat"),
                        "spoofing": micro_cache.get("spoofing"),
                        "rl_signal": micro_cache.get("rl_signal"),
                        "pairs": micro_cache.get("pairs"),
                        "breakout": micro_cache.get("breakout"),
                        "walk_forward": micro_cache.get("walk_forward"),
                    }
            except Exception:
                pass
            
            log.info(
                "Alpha mining enrichment: %d active alphas, composite=%.4f, diversity=%.4f, decay_alerts=%d",
                len(active_alphas), mega_alpha_composite, diversity_score, len(decay_alerts),
            )
            
        except ImportError:
            logging.getLogger("LLMOrchestrator").debug(
                "Alpha mining enrichment: research.alpha_miner not available"
            )
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug(
                "_apply_alpha_mining_enrichment failed: %s", exc,
            )

    def _record_slippage_feedback(
        self,
        *,
        symbol: str,
        predicted_slippage: Optional[float],
        realized_slippage: Optional[float],
        atr: Optional[float] = None,
        delay_sec: Optional[float] = None,
    ) -> None:
        """Feed realized vs predicted slippage into the Smith predictor calibrator."""
        if predicted_slippage is None or realized_slippage is None:
            return
        try:
            atr_v = float(atr) if atr is not None else None
        except (TypeError, ValueError, OverflowError):
            atr_v = None
        if atr_v is None or atr_v <= 0:
            atr_v = max(abs(float(predicted_slippage)) * 10.0, 1e-6)
        try:
            delay = float(delay_sec) if delay_sec is not None else 0.05
        except (TypeError, ValueError, OverflowError):
            delay = 0.05
        result = self.slippage_calibrator.record(
            predicted_slippage=float(predicted_slippage),
            realized_slippage=float(realized_slippage),
            atr=atr_v,
            delay_sec=max(0.01, delay),
            symbol=symbol,
        )
        if result.get("recorded"):
            try:
                self.store.set_state("slippage_calibrator", self.slippage_calibrator.to_state())
            except Exception as exc:
                logging.getLogger("LLMOrchestrator").debug("Slippage calibrator state save failed: %s", exc)
            self.operations.event(
                "execution", "slippage_calibrated", result,
                run_id=self.run_id, correlation_id=symbol, severity="INFO",
            )

    def reconcile_open_positions(self):
        """
        Sync internal PerformanceMonitor history with actual MT5 status.
        Detects trades closed by SL/TP and updates history.
        """
        if not self.perf: return
        if not self.client and not self.paper_broker: return
        
        # Get actual positions (paper or MT5)
        if self.paper_broker:
            actual_tickets = set(self.paper_broker.positions.keys())
        else:
            actual_positions = mt5.positions_get() if mt5 is not None else []
            actual_tickets = {p.ticket for p in actual_positions} if actual_positions else set()

        # SQLite is authoritative after a restart; include the in-memory
        # monitor only for compatibility with trades created before migration.
        durable = {int(row["ticket"]): row for row in self.store.open_trade_rows()}
        for trade in self.perf.history:
            if trade.get("status") == "OPEN":
                durable.setdefault(int(trade["ticket"]), trade)
        if not durable:
            return

        for ticket, trade in durable.items():
            
            # If ticket is NOT in actual positions, it MUST be closed (or never existed)
            if ticket not in actual_tickets:
                if self.paper_broker:
                    deals = [d for d in getattr(self.paper_broker, "history", []) if int(d.get("ticket", 0)) == int(ticket)]
                    if deals:
                        last_deal = deals[-1]
                        gross_pnl = float(last_deal.get("profit", 0.0))
                        swap = 0.0
                        commission = 0.0
                        total_pnl = gross_pnl
                        exit_price = float(last_deal.get("price_close", 0.0))
                        close_time = datetime.fromtimestamp(float(last_deal.get("close_time", time.time())), tz=timezone.utc)
                    else:
                        gross_pnl = 0.0
                        swap = 0.0
                        commission = 0.0
                        total_pnl = 0.0
                        exit_price = float(trade.get("open_price", 0.0))
                        close_time = datetime.now(timezone.utc)
                else:
                    # Retrieve history to find PnL (Lookback 7 days to be safe)
                    from_date = datetime.now() - timedelta(days=7)
                    deals = mt5.history_deals_get(position=ticket, date_from=from_date) if mt5 is not None else []
                    if not deals:
                        continue
                    # Calculate total PnL for this position (entry + exit deals)
                    gross_pnl = sum(float(getattr(d, "profit", 0) or 0) for d in deals)
                    swap = sum(float(getattr(d, "swap", 0) or 0) for d in deals)
                    commission = sum(float(getattr(d, "commission", 0) or 0) for d in deals)
                    total_pnl = gross_pnl + swap + commission
                    exit_price = deals[-1].price
                    close_time = None
                    last_deal = deals[-1]
                    time_msc = getattr(last_deal, "time_msc", None)
                    deal_time = getattr(last_deal, "time", None)
                    try:
                        if time_msc:
                            close_time = datetime.fromtimestamp(float(time_msc) / 1000.0, tz=timezone.utc)
                        elif deal_time:
                            close_time = datetime.fromtimestamp(float(deal_time), tz=timezone.utc)
                    except (TypeError, ValueError, OverflowError, OSError):
                        close_time = None

                logging.getLogger("LLMOrchestrator").info(f"🔎 Detected external/SLTP close for #{ticket}. PnL: {total_pnl}")
                for monitor_item in self.perf.history:
                    if int(monitor_item.get("ticket", 0)) == int(ticket):
                        monitor_item["broker_reconciled"] = True
                self.perf.close_trade(ticket, exit_price, total_pnl)
                self.store.record_close(ticket, exit_price, total_pnl, "History reconciliation",
                                        gross_pnl=gross_pnl, commission=commission, swap=swap,
                                        closed_at=close_time)
                if hasattr(self.risk, "record_trade_outcome"):
                    self.risk.record_trade_outcome(total_pnl)
                    closing_deals = [deal for deal in deals if (deal.get("entry") if isinstance(deal, dict) else getattr(deal, "entry", None)) == getattr(mt5, "DEAL_ENTRY_OUT", 1)] or [deals[-1]]
                    for deal in closing_deals:
                        is_d = isinstance(deal, dict)
                        order_t = deal.get("order", 0) if is_d else getattr(deal, "order", 0)
                        deal_t = deal.get("ticket", 0) if is_d else getattr(deal, "ticket", 0)
                        pos_t = deal.get("position_id", ticket) if is_d else getattr(deal, "position_id", ticket)
                        f_price = deal.get("price_close", deal.get("price", exit_price)) if is_d else getattr(deal, "price", exit_price)
                        f_vol = deal.get("volume", 0) if is_d else getattr(deal, "volume", 0)
                        comm = deal.get("commission", 0.0) if is_d else getattr(deal, "commission", 0.0)
                        swp = deal.get("swap", 0.0) if is_d else getattr(deal, "swap", 0.0)
                        pft = deal.get("profit", 0.0) if is_d else getattr(deal, "profit", 0.0)
                        self.store.record_execution_report(
                            event_type="CLOSED", order_ticket=int(order_t or 0) or None,
                            deal_ticket=int(deal_t or 0) or None,
                            position_ticket=int(pos_t or ticket),
                            filled_price=float(f_price or 0) or None,
                            filled_volume=float(f_vol or 0) or None,
                            commission=float(comm or 0),
                            swap=float(swp or 0),
                            payload={"profit": float(pft or 0), "reason": "History reconciliation"},
                        )
                    risk_amount = self.store.trade_risk(ticket)
                    label = self.trade_memory.label_closed_trade(ticket, total_pnl, risk_amount, "MT5 history reconciliation")
                    self.trade_memory.resolve_retrieval_outcome(ticket, label["label"])
                    self._apply_causal_trade_feedback(ticket, total_pnl, risk_amount, label)
                    self.trade_memory.save_narrative({"ticket": ticket, "label": label["label"]}, f"Trade {ticket}: {label['label']} at {label['r_multiple']:.2f}R. {label['rationale']}")
                    self.trade_memory.rebuild_scenarios()
                    # Distill lesson from this trade outcome
                    if self.lesson_distiller:
                        try:
                            self.lesson_distiller.distill_from_trace({
                                "symbol": trade.get("symbol", ""),
                                "strategy": trade.get("strategy", ""),
                                "regime": trade.get("regime", ""),
                                "outcome": label["label"],
                                "r_multiple": label.get("r_multiple"),
                                "setup_description": label.get("rationale", ""),
                                "close_reason": "MT5 history reconciliation",
                            })
                            self.lesson_distiller.save()
                        except Exception as _ld_err:
                            logging.getLogger("LLMOrchestrator").debug("Lesson distill failed: %s", _ld_err)
                    # GAP-10: ensure RL learns from reconciled closes even if the
                    # PerformanceMonitor callback path missed state fields.
                    try:
                        decision = {}
                        for monitor_item in self.perf.history:
                            if int(monitor_item.get("ticket", 0)) == int(ticket):
                                decision = monitor_item.get("decision") or {}
                                break
                        self.update_rl_from_trade({
                            "ticket": ticket,
                            "pnl": total_pnl,
                            "risk": risk_amount,
                            "r_multiple": label.get("r_multiple"),
                            "confluence_score": decision.get("confluence_score", 0),
                            "news_aligned": decision.get("news_aligned", False),
                            "max_drawdown": 0.0,
                            "rl_state": decision.get("rl_state"),
                            "rl_action": decision.get("rl_action") or decision.get("action") or trade.get("action"),
                            "action": trade.get("action"),
                            "broker_reconciled": True,
                            "exit_state": {"position": "FLAT", "realized_pnl": total_pnl,
                                           "r_multiple": label.get("r_multiple")},
                        })
                    except Exception as exc:
                        logging.getLogger("LLMOrchestrator").warning("RL close update failed: %s", exc)
                    self.operations.enqueue_memory("closed_trade", str(ticket), {
                        "ticket": ticket, "pnl": total_pnl, "r_multiple": label["r_multiple"],
                        "label": label["label"], "reason": label["rationale"],
                    })
                    self.operations.event("reconciler", "trade_closed", {"ticket": ticket, "pnl": total_pnl, "label": label["label"]}, run_id=self.run_id, correlation_id=str(ticket))
                    try:
                        from local_agent_memory import maybe_remember_trade_cycle
                        maybe_remember_trade_cycle(
                            cycle_key=str(ticket),
                            stage="closed",
                            payload={
                                "ticket": ticket,
                                "pnl": total_pnl,
                                "r_multiple": label["r_multiple"],
                                "label": label["label"],
                                "reason": label["rationale"],
                            },
                            ticket=ticket,
                            run_id=self.run_id or "",
                            memory_links={
                                "L1": str(ticket),
                                "L2": "scenario_rebuild",
                                "narrative": str(ticket),
                                "verified_memory": str(ticket),
                            },
                        )
                    except Exception as exc:
                        logging.getLogger("LLMOrchestrator").debug("Trade memory event for ticket %s failed: %s", ticket, exc)
                    # Slow-Lane Memory Reflection: update equity, label trade,
                    # consolidate scenarios, and apply macro causal feedback.
                    self.velocity.submit_slow(
                        f"reflection_{ticket}",
                        self._run_reflection_graph,
                        ticket=ticket,
                        symbol=str(trade.get("symbol", "")),
                        strategy=str(trade.get("strategy", "")),
                        open_time=str(trade.get("opened_at", "")),
                        close_time=str(close_time or utcnow_iso()),
                        entry_price=float(trade.get("open_price", 0.0)),
                        close_price=float(exit_price),
                        realized_pnl=float(total_pnl),
                        realized_r_multiple=float(label.get("r_multiple", 0.0)),
                        outcome_label=str(label.get("label", "UNKNOWN")),
                    )
                else:
                    # Do not fabricate a zero-PnL outcome.  Keep it open and
                    # retry history lookup on the next cycle.
                    logging.getLogger("LLMOrchestrator").warning(
                        f"⚠️ Trade #{ticket} is absent from positions but no MT5 history is available; keeping it unresolved.")

    def reconcile_daily_broker_history(self, day_start: Optional[datetime] = None) -> Dict[str, Any]:
        """Persist a bot-owned MT5 deal-versus-journal daily comparison.

        It reports, rather than hides, missing tickets and P&L differences.
        Manual/unowned broker activity is deliberately kept outside the bot
        comparison and only counted for operational awareness.
        """
        now = datetime.now(timezone.utc)
        start = (day_start or now.replace(hour=0, minute=0, second=0, microsecond=0)).astimezone(timezone.utc)
        end = min(now, start + timedelta(days=1))

        is_paper = getattr(self, "paper_broker", None) is not None or mt5 is None
        if is_paper and getattr(self, "paper_broker", None) is not None:
            from operations.daily_reconciliation import adapt_paper_deals
            deals = adapt_paper_deals(self.paper_broker.history)
            require_timestamps = False
        elif mt5 is not None:
            deals = mt5.history_deals_get(start, end)
            if deals is None:
                raise RuntimeError("MT5 deal history unavailable for daily reconciliation")
            require_timestamps = True
        else:
            raise RuntimeError("Neither authenticated MT5 terminal nor PaperBroker available for daily reconciliation")

        # Authenticated MT5 history is timestamped; strict filtering prevents
        # a broad/incorrect history response from being counted as this day.
        report = compare_broker_day(self.store, deals, start, end, require_timestamps=require_timestamps)
        severity = "INFO" if report["status"] == "CLEAN" else "WARNING"
        self.operations.event("reconciler", "daily_broker_reconciliation", report,
                              run_id=self.run_id, correlation_id=start.date().isoformat(), severity=severity)
        return report

    def reconcile_pending_orders(self, paper_mode: bool) -> None:
        """Expire/cancel planned orders; never invent a fill without broker proof."""
        # Unresolved broker outcomes are retried as bounded, read-only history
        # lookups.  The retry claim never resubmits an order; operator review
        # remains mandatory after the retry budget is exhausted.
        due_retries = {item["intent_key"] for item in self.operations.claim_due_execution_review_retries()}
        for intent in self.operations.order_intents(("PENDING", "UNRESOLVED")):
            intent_key = str(intent.get("intent_key", ""))
            intent_status = str(intent.get("status", "")).upper()
            retry_state = self.operations.execution_review_retry_state(intent_key)
            if intent_status == "UNRESOLVED" and intent_key not in due_retries:
                continue
            if intent_status == "PENDING" and retry_state == "WAIT":
                continue
            if retry_state == "EXHAUSTED":
                continue
            payload = intent["payload"]
            ticket = intent.get("mt5_ticket")
            expires_text = payload.get("expires_at")
            try:
                expired = bool(expires_text) and datetime.now(timezone.utc) >= datetime.fromisoformat(expires_text)
            except (TypeError, ValueError):
                expired = False
            exists = False
            if paper_mode and self.paper_broker:
                exists = bool(ticket and int(ticket) in self.paper_broker.orders)
            elif not paper_mode and ticket:
                exists = bool(mt5.orders_get(ticket=int(ticket)))
            if exists and expired:
                try:
                    intent_magic = int(payload.get("magic", 0))
                except (TypeError, ValueError, OverflowError):
                    intent_magic = 0
                cancelled = cancel_pending_order(
                    int(ticket), self.paper_broker if paper_mode else None, intent_magic
                )
                if cancelled:
                    outcome = {"expired_at": datetime.now(timezone.utc).isoformat(), "reason": "cancel-if-unfilled expiry"}
                    self.operations.resolve_order_intent(intent["intent_key"], "CANCELLED", ticket=int(ticket), result=outcome)
                    self.store.transition_latest_setup(str(payload.get("setup_key", "")), "MISSED", ticket=int(ticket), reason="pending order expired and was cancelled")
                    self.store.record_execution_report(event_type="CANCELLED", intent_key=intent["intent_key"],
                                                       order_ticket=int(ticket), requested_price=payload.get("entry"),
                                                       requested_volume=payload.get("volume"), payload=outcome)
                    self.store.log_lifecycle_event(int(ticket), "", "CANCELLED", "reconciler", {"intent_key": intent["intent_key"], **outcome})
                    self.store.log_lifecycle_event(int(ticket), "", "EXPIRED", "reconciler", {"intent_key": intent["intent_key"], **outcome})
                else:
                    self.operations.resolve_order_intent(intent["intent_key"], "UNRESOLVED", ticket=int(ticket), error="pending order expired but cancellation was not confirmed")
                    self.operations.flag_execution_review(intent["intent_key"], "pending order expired but broker cancellation was not confirmed", severity="CRITICAL")
                    self.store.log_lifecycle_event(int(ticket), "", "UNRESOLVED", "reconciler", {"intent_key": intent["intent_key"], "reason": "expiry cancellation not confirmed"})
                continue
            if not exists:
                if not paper_mode and ticket:
                    try:
                        now = datetime.now()
                        recent = mt5.history_deals_get(now - timedelta(days=7), now) or []
                        matching = [deal for deal in recent if int(getattr(deal, "order", 0) or 0) == int(ticket)]
                    except Exception as exc:
                        matching = []
                        logging.getLogger("LLMOrchestrator").warning("Cannot inspect pending-order deal history for %s: %s", ticket, exc)
                    if matching:
                        entry_deals = [deal for deal in matching if getattr(deal, "entry", None) != getattr(mt5, "DEAL_ENTRY_OUT", 1)]
                        if not entry_deals:
                            entry_deals = [matching[0]]
                        entry_deal = entry_deals[0]
                        position_id = int(getattr(entry_deal, "position_id", 0) or ticket)
                        fill_volume = sum(float(getattr(deal, "volume", 0) or 0) for deal in entry_deals)
                        weighted_value = sum(float(getattr(deal, "price", 0) or 0) * float(getattr(deal, "volume", 0) or 0) for deal in entry_deals)
                        fill_price = weighted_value / fill_volume if fill_volume > 0 else float(getattr(entry_deal, "price", payload.get("entry", 0)) or 0)
                        requested_volume = float(payload.get("volume", 0) or 0)
                        partial = fill_volume > 0 and requested_volume > 0 and fill_volume < requested_volume - 1e-9
                        preflight = payload.get("margin_preflight", {}) or {}
                        spread_at_fill = None
                        if not paper_mode and mt5:
                            fill_tick = mt5.symbol_info_tick(str(payload.get("symbol", "")))
                            fill_bid, fill_ask = getattr(fill_tick, "bid", None), getattr(fill_tick, "ask", None)
                            if fill_bid is not None and fill_ask is not None:
                                spread_at_fill = float(fill_ask) - float(fill_bid)
                        submitted_msc = preflight.get("submitted_at_msc")
                        latency_ms = max(0.0, time.time() * 1000 - float(submitted_msc)) if submitted_msc else None
                        proposal_payload = self.store.latest_proposal(str(payload.get("setup_key", "")))
                        if proposal_payload:
                            original = TradeProposal(**proposal_payload)
                            filled = replace(original, entry=fill_price)
                            self.store.record_open_trade(
                                position_id, filled, fill_volume,
                                float(payload.get("risk_amount", 0) or 0),
                                {"intent_key": intent["intent_key"], "broker_order": int(ticket),
                                 "fill_deal": int(getattr(entry_deal, "ticket", 0) or 0), "fill_price": fill_price,
                                 "proposal_features": original.features,
                                 "feature_schema_version": original.features.get("feature_schema_version"),
                                 "feature_schema_hash": original.features.get("feature_schema_hash"),
                                 "setup_id": original.features.get("setup_id")},
                            )
                            # PerformanceMonitor receives a fill only after
                            # broker history proves the order was executed.
                            # This prevents phantom open trades and makes its
                            # close callback eligible for reconciled RL updates.
                            monitor_decision = {
                                "risk_amount": float(payload.get("risk_amount", 0) or 0),
                                "confluence_score": original.features.get("confluence_score", 0),
                                "rl_state": payload.get("rl_state"),
                                "rl_action": payload.get("rl_action"),
                                "metrics": {"expected_price": payload.get("entry")},
                                "feature_snapshot": original.features.get("feature_values", {}),
                            }
                            if not any(int(item.get("ticket", 0)) == position_id for item in self.perf.history):
                                self.perf.log_trade(position_id, str(payload.get("symbol", "")),
                                                     str(payload.get("side", "")), fill_volume, fill_price,
                                                     monitor_decision)
                                for item in self.perf.history:
                                    if int(item.get("ticket", 0)) == position_id:
                                        item["broker_reconciled"] = True
                                self.perf.save_history()
                            self.trade_memory.link_retrievals_to_trade(payload.get("memory_retrieval_ids", {}), position_id)
                        fill_result = {
                            "broker_order": int(ticket), "position_id": position_id,
                            "fill_deal": int(getattr(entry_deal, "ticket", 0) or 0),
                            "fill_deals": [int(getattr(deal, "ticket", 0) or 0) for deal in entry_deals],
                            "fill_price": fill_price, "fill_volume": fill_volume, "requested_volume": requested_volume,
                        }
                        fill_status = "PARTIAL" if partial else "FILLED"
                        self.operations.resolve_order_intent(intent["intent_key"], fill_status, ticket=position_id, result=fill_result)
                        self.store.transition_latest_setup(str(payload.get("setup_key", "")), "FILLED", ticket=position_id,
                                                           reason="broker fill reconciled", payload={"fill_result": fill_result})
                        for deal in entry_deals:
                            shortfall = (
                                ((float(getattr(deal, "price", 0) or 0) - float(payload.get("entry"))) if str(payload.get("side", "BUY")).upper() == "BUY" else
                                 (float(payload.get("entry")) - float(getattr(deal, "price", 0) or 0)))
                                if payload.get("entry") is not None and float(getattr(deal, "price", 0) or 0) > 0 else None
                            )
                            self.store.record_execution_report(
                                event_type=fill_status, intent_key=intent["intent_key"], order_ticket=int(ticket),
                                deal_ticket=int(getattr(deal, "ticket", 0) or 0) or None,
                                position_ticket=position_id,
                                requested_price=payload.get("entry"), filled_price=float(getattr(deal, "price", 0) or 0) or None,
                                requested_volume=requested_volume,
                                filled_volume=float(getattr(deal, "volume", 0) or 0) or None,
                                commission=float(getattr(deal, "commission", 0) or 0),
                                swap=float(getattr(deal, "swap", 0) or 0),
                                execution_latency_ms=latency_ms,
                                spread_at_submit=preflight.get("spread_at_submit"),
                                spread_at_fill=spread_at_fill,
                                implementation_shortfall=shortfall,
                                requote_count=int(payload.get("requote_count", 0) or 0),
                                payload={"fill_result": fill_result, "deal_entry": getattr(deal, "entry", None)},
                            )
                            # GAP-21: calibrate Smith predictor from predicted vs realized slippage
                            self._record_slippage_feedback(
                                symbol=str(payload.get("symbol", "")),
                                predicted_slippage=preflight.get("predicted_slippage"),
                                realized_slippage=abs(float(shortfall)) if shortfall is not None else None,
                                atr=preflight.get("atr_at_submit"),
                                delay_sec=preflight.get("lag_delay_sec") or (
                                    (float(latency_ms) / 1000.0) if latency_ms is not None else None
                                ),
                            )
                        self.store.log_lifecycle_event(position_id, str(payload.get("setup_key", "")), fill_status, "reconciler", {
                            "intent_key": intent["intent_key"], "broker_order": int(ticket),
                            **fill_result,
                        })
                        continue
                # A pending order may have filled, been cancelled externally, or
                # disappeared due to broker history latency.  Leave it for
                # explicit MT5 history reconciliation rather than guessing.
                status = "UNRESOLVED"
                reason = "pending ticket absent from active orders; broker-history reconciliation required"
                self.operations.resolve_order_intent(intent["intent_key"], status, ticket=int(ticket) if ticket else None, error=reason)
                self.operations.flag_execution_review(intent["intent_key"], reason, severity="WARNING")
                self.store.log_lifecycle_event(int(ticket) if ticket else None, "", status, "reconciler", {"intent_key": intent["intent_key"], "reason": reason})

    # Soft budget for the whole batch (worker-side warning).  240s (raised
    # from 120s): the 2026-09-26 live audit measured a ~27% duty cycle —
    # 5,665 schedule_due events in a 4.4h session vs ~21k expected — because
    # legitimately network-heavy batches (event_monitor calendar+news,
    # market_health scan) repeatedly exceeded 120s and were abandoned,
    # starving the fast 60s schedules (alert_dispatch, incident_sweep) behind
    # them.  240s covers the worst observed healthy batch (~130s) with
    # headroom; per-handler budgets still bound individual misbehavior, and
    # the pulse itself only ever blocks _SCHEDULE_WAIT (15s).
    _SCHEDULE_TIMEOUT: float = 240.0
    _SCHEDULE_WAIT: float = 15.0      # how long a pulse blocks on the batch before proceeding

    # Per-handler soft budgets (seconds). Python cannot kill a stuck thread,
    # so the guard cannot hard-cancel a handler; the wave loop samples the
    # guard between dispatches and records the overrun when a handler exceeds
    # its budget, then CONTINUES with the next handler (the overrun defers
    # only its own dispatch window). Only the wave deadline (_SCHEDULE_TIMEOUT)
    # defers the remainder. A hung handler now costs its own budget slice
    # instead of silently consuming the whole 60s batch every pulse.
    _SCHEDULE_HANDLER_BUDGETS: Dict[str, float] = {
        "market_health": 25.0,       # health scan + opportunity scan over the watchlist
        "event_monitor": 60.0,       # calendar collection + news ingest (allows for multi-source network latency)
        "event_triage": 10.0,
        "position_reconcile": 10.0,
        "incident_sweep": 5.0,
        "alloc_rebalance": 10.0,
        "causal_edge_validate": 15.0,
        "catalog_measurement_pass": 20.0,
        "lifecycle_evaluation": 20.0,
        "session_review": 30.0,
        "daily_review": 60.0,
        "weekly_research": 300.0,    # CPCV/DSR sprints are legitimately heavy
        "self_improvement": 30.0,
        "macro_post_event_review": 30.0,
        "macro_event_evidence": 15.0,
        "economic_dynamics_seed": 15.0,
        "historical_regime_research": 30.0,
        "candidate_sprint": 120.0,
        "alpha_mining_sprint": 120.0,
        "monthly_memory_archive": 60.0,
        "ledger_housekeeping": 30.0,
    }
    _SCHEDULE_DEFAULT_HANDLER_BUDGET: float = 45.0
    # Session-skip watchdog: once a schedule has been flagged as missed, do
    # not re-alert more often than this during the same starvation episode.
    _SCHEDULE_SKIP_REALERT_S: float = 21600.0  # 6h

    class _HandlerGuard:
        """Per-handler soft-budget watchdog for the schedule batch.

        ``_recorded`` is the exactly-once latch for overrun telemetry: a
        tripped budget is persisted once (inline at the abandonment point or
        by the batch-end catch-up), never twice.
        """

        __slots__ = ("handler", "budget", "_started", "tripped", "_recorded")

        def __init__(self) -> None:
            self.handler = ""
            self.budget = 0.0
            self._started = 0.0
            self.tripped = False
            self._recorded = False

        def begin(self, schedule_name: str, budget_s: float) -> None:
            self.handler = schedule_name
            self.budget = max(0.5, float(budget_s))
            self._started = time.time()
            # Each dispatch window owns its exactly-once overrun latch: a wave
            # that continues past an overrun must still record the NEXT
            # handler's overrun, so the latch resets per window.
            self.tripped = False
            self._recorded = False

        def elapsed(self) -> float:
            """Seconds since begin() (0.0 when never started)."""
            return (time.time() - self._started) if self._started else 0.0

        def check(self) -> bool:
            if (not self.tripped and self.handler
                    and (time.time() - self._started) > self.budget):
                self.tripped = True
            return self.tripped

    def _run_schedules_bounded(self, symbol: str, paper_mode: bool) -> None:
        """Dispatch the maintenance schedule batch off the pulse thread.

        - single-flight: at most one batch runs at a time. A pulse that
          arrives while a batch is still running skips dispatch entirely —
          the running batch already owns the claimed schedules, and the old
          unguarded behavior stacked duplicate workers until the loop was
          pacing itself on the 60s timeout.
        - bounded wait: the pulse blocks at most ``_SCHEDULE_WAIT`` seconds
          for the batch to finish, then proceeds; the batch keeps running in
          the background and later pulses skip until it completes.
        - per-handler budgets: see ``_SCHEDULE_HANDLER_BUDGETS``.
        """
        import threading as _th
        _log = logging.getLogger("LLMOrchestrator")
        with self._schedule_thread_lock:
            if self._schedule_batch_running:
                _log.debug(
                    "schedule batch still running; pulse proceeds without re-dispatch")
                return
            self._schedule_batch_running = True
        _done = _th.Event()
        _err: list = []

        def _worker():
            try:
                started = time.time()
                self._process_due_schedules(symbol, paper_mode)
                elapsed = time.time() - started
                if elapsed > self._SCHEDULE_TIMEOUT:
                    _log.warning(
                        "schedule batch exceeded %.1fs budget (%.1fs actual)",
                        self._SCHEDULE_TIMEOUT, elapsed)
            except Exception as exc:
                _err.append(exc)
            finally:
                # The worker owns the single-flight flag: it always releases,
                # so an abandoned (timed-out) batch unblocks the next pulse
                # exactly when its work is done, never before, never leaked.
                with self._schedule_thread_lock:
                    self._schedule_batch_running = False
                _done.set()

        _th.Thread(target=_worker, name="sched-bounded", daemon=True).start()
        if not _done.wait(timeout=self._SCHEDULE_WAIT):
            # Batch continues in the background; single-flight stays held by
            # the worker until it actually finishes.
            return
        if _err:
            _log.warning("schedule batch failed: %s", _err[0])

    def _fast_lane_order(
            self, claimed_batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Dispatch order for a claimed wave: cheapest cadence first.

        The wave deadline (``_SCHEDULE_TIMEOUT``) defers whatever is still
        undispatched when it trips, so dispatch order decides WHICH claims
        eat the deferral. Claim order can list heavy, slow-cadence handlers
        ahead of the 60s fast lanes (the profiler's CLAIM_ORDER_TAIL), and a
        deadline hit then defers exactly the schedules that lose the most
        catch-up time per missed turn. Ordering the wave ascending by
        registry interval (fallback: handler budget, then claim order — the
        sort is stable) puts the fast lanes at the front, where the deadline
        cannot cut them off. Pure reordering of the dispatch queue: claims,
        leases, and due points are untouched, and the batch-end release is
        set-based, so bookkeeping stays order-independent.
        """
        if len(claimed_batch) < 2:
            return list(claimed_batch)
        intervals: Dict[str, float] = {}
        try:
            intervals = {
                str(item.get("schedule_name")):
                    float(item.get("interval_seconds") or 0)
                for item in (self.operations.list_schedules() or [])
                if item.get("schedule_name")
            }
        except Exception:
            intervals = {}
        budgets = self._SCHEDULE_HANDLER_BUDGETS
        default_budget = float(self._SCHEDULE_DEFAULT_HANDLER_BUDGET)

        def _lane(item: Dict[str, Any], idx: int) -> Tuple[float, float, int]:
            name = str(item.get("schedule_name") or "")
            interval = intervals.get(name) or 0.0
            if interval <= 0:
                # Unregistered/malformed rows ride the handler-budget lane
                # instead of jumping the queue ahead of known cadences.
                interval = float(budgets.get(name, default_budget))
            return (interval, float(budgets.get(name, default_budget)), idx)

        return [item for _, item in
                sorted(enumerate(claimed_batch),
                       key=lambda pair: _lane(pair[1], pair[0]))]

    # ------------------------------------------------------------------
    # Laya DecisionBundle → cycle prompts (2026-09-28): the MacroAnalyst
    # graph node publishes its typed-decision bundle (regime D3, liquidity
    # D4, arbitration D8, kill switch D9, narrative decay D12, …) to the
    # shared ui_state; project a compact advisory view onto the cycle toon
    # so the scalp/swing candle engines see macro state DIRECTLY instead of
    # inferring it from causal edges. Read-only mirror of advisory context:
    # never grants order permission (execution_authority stays False), never
    # gates risk (RiskController keeps its own blackout/kill-switch gates),
    # and every failure path degrades to "UNAVAILABLE" without breaking the
    # cycle.
    # ------------------------------------------------------------------
    _BUNDLE_DECISION_IDS = (
        "D1_economic_surprise", "D2_blackout_gate", "D3_macro_regime",
        "D4_liquidity_stance", "D5a_escalation_ladder", "D8_opportunity_arbitration",
        "D9_emergency_kill_switch", "D12_narrative_decay",
    )
    _BUNDLE_MAX_HEAD_PROB_ENTRIES = 4

    @classmethod
    def _project_decision_bundle(cls, bundle_dict: Any) -> Optional[Dict[str, Any]]:
        """Compact advisory projection of a DecisionBundle.to_dict() payload.

        Emits per-head selected_option + top-k probability entries + confidence,
        plus the bundle-level arbitration verdict and risk multiplier. All
        string values are hard-truncated to keep the prompt budget tight.
        Returns None when the payload is missing/malformed.
        """
        if not isinstance(bundle_dict, dict):
            return None
        decisions_in = bundle_dict.get("decisions")
        if not isinstance(decisions_in, dict):
            return None
        heads: Dict[str, Any] = {}
        for qid in cls._BUNDLE_DECISION_IDS:
            rec = decisions_in.get(qid)
            if not isinstance(rec, dict):
                continue
            probs_raw = rec.get("probabilities")
            probs: Dict[str, float] = {}
            if isinstance(probs_raw, dict):
                try:
                    pairs = sorted(
                        ((str(k), float(v)) for k, v in probs_raw.items()
                         if isinstance(v, (int, float))),
                        key=lambda kv: kv[1], reverse=True,
                    )[:cls._BUNDLE_MAX_HEAD_PROB_ENTRIES]
                    probs = {k: round(v, 4) for k, v in pairs}
                except Exception:
                    probs = {}
            try:
                conf = float(rec.get("confidence") or rec.get("entropy") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            heads[qid] = {
                "selected_option": str(rec.get("selected_option") or "")[:60],
                "probabilities": probs,
                "confidence": round(conf, 4),
                "is_veto": bool(rec.get("is_veto")),
            }
        if not heads:
            return None
        try:
            risk_mult = float(bundle_dict.get("risk_multiplier") or 0.0)
        except (TypeError, ValueError):
            risk_mult = 0.0
        return {
            "bundle_id": str(bundle_dict.get("bundle_id") or "")[:40],
            "bundle_timestamp": str(bundle_dict.get("timestamp") or "")[:32],
            "heads": heads,
            "arbitration_verdict": str(bundle_dict.get("arbitration_verdict") or "")[:40],
            "risk_multiplier": risk_mult,
            "veto_enforced": bool(bundle_dict.get("veto_enforced")),
            "execution_authority": False,
            "source": "laya_decision_bundle",
        }

    @classmethod
    def _attach_decision_bundle_advisory(cls, toon: Dict[str, Any]) -> None:
        """Attach the Laya bundle advisory to the cycle toon (fail-soft).

        Staleness policy: the bundle is a per-cycle artifact; one older than
        10 minutes is treated as stale and surfaced as UNAVAILABLE rather
        than presented as current macro state.
        """
        UNAVAILABLE = {
            "status": "UNAVAILABLE", "reason": "no bundle published this session",
            "execution_authority": False, "source": "laya_decision_bundle",
        }
        try:
            from core.shared_state import ui_state
        except Exception:
            toon["laya_decision_bundle"] = dict(UNAVAILABLE, reason="shared_state_unavailable")
            return
        try:
            bundle = getattr(ui_state, "last_decision_bundle", None)
        except Exception:
            toon["laya_decision_bundle"] = dict(UNAVAILABLE, reason="ui_state_unavailable")
            return
        if not isinstance(bundle, dict) or not bundle.get("decisions"):
            toon["laya_decision_bundle"] = UNAVAILABLE
            return
        try:
            from datetime import datetime as _dt
            ts_raw = str(bundle.get("timestamp") or "")
            if not ts_raw:
                raise ValueError("missing timestamp")
            ts = _dt.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                # Producers stamp UTC; a naive stamp is interpreted as UTC.
                ts = ts.replace(tzinfo=timezone.utc)
            age_s = (datetime.now(timezone.utc) - ts).total_seconds()
        except Exception:
            age_s = None
        if age_s is not None and age_s > 600:
            toon["laya_decision_bundle"] = {
                "status": "UNAVAILABLE", "reason": "stale_bundle",
                "bundle_age_s": int(age_s), "bundle_id": str(bundle.get("bundle_id") or "")[:40],
                "execution_authority": False, "source": "laya_decision_bundle",
            }
            return
        projection = cls._project_decision_bundle(bundle)
        if projection is None:
            toon["laya_decision_bundle"] = dict(UNAVAILABLE, reason="malformed_bundle")
            return
        projection["bundle_age_s"] = int(age_s) if age_s is not None else None
        projection["status"] = "AVAILABLE"
        toon["laya_decision_bundle"] = projection

    def evaluate_institutional_workflow(self, symbol: str, timeframe: str, toon: Dict[str, Any], paper_mode: bool = True) -> Optional[Dict[str, Any]]:
        """Autonomously evaluate the appropriate institutional quant workflow.

        Dispatches to the 7 institutional quant workflows based on live market conditions:
        - Macro event window / release -> MacroEventAlphaWorkflow
        - Cointegration spread divergence (|Z| >= 2.0) -> StatisticalArbitrageWorkflow
        - Stationary / mean-reverting regime -> MeanReversionWorkflow
        - Microstructure toxicity / volume breakout -> MicrostructureBreakoutWorkflow
        - Neural foundation high conviction -> NeuralFoundationWorkflow
        - Otherwise -> RetailQuantModelsWorkflow
        """
        if getattr(self, "quant_workflow_engine", None) is None:
            try:
                from core.harness.workflows import QuantWorkflowEngine
                self.quant_workflow_engine = QuantWorkflowEngine()
            except Exception:
                return None

        try:
            point_values = toon.get("feature_snapshot", {}).get("values", {})
            price = point_values.get("close") or toon.get("dom", {}).get("mid_price") or 1.0850
            atr = toon.get("indicators", {}).get("atr_14", 0.0015)
            if isinstance(atr, list):
                atr = atr[-1] if atr else 0.0015
            atr = float(atr or 0.0015)
            if atr <= 0:
                atr = 0.0015

            bid = float(toon.get("dom", {}).get("bid") or price)
            ask = float(toon.get("dom", {}).get("ask") or (price + 0.0002))
            regime = str((toon.get("intermarket_regime") or {}).get("regime_id") or "EXPANSION")

            news_events = [e for e in toon.get("news", []) if isinstance(e, dict) and int(e.get("importance", 0) or 0) >= 3]
            kalman_res = toon.get("auto_quant_tools", {}).get("results", {}).get("kalman", {})
            z_score = float(kalman_res.get("z_score", 0.0) or 0.0) if isinstance(kalman_res, dict) else 0.0
            micro = toon.get("microstructure", {})
            vpin = float(micro.get("vpin", 0.0) or 0.0) if isinstance(micro, dict) else 0.0
            kronos = toon.get("kronos_advisory", {})
            kronos_conf = float(kronos.get("confidence", 0.0) or 0.0) if isinstance(kronos, dict) else 0.0

            if news_events:
                selected_wf = "macro_event_alpha"
            elif abs(z_score) >= 2.0:
                selected_wf = "statarb"
            elif vpin > 0.60:
                selected_wf = "microstructure"
            elif kronos_conf > 0.75:
                selected_wf = "neural_kronos"
            elif "RANGE" in regime or "MEAN_REV" in regime:
                selected_wf = "mean_reversion"
            else:
                selected_wf = "retail_quant"

            snapshot = {
                "symbol": symbol,
                "price": float(price),
                "bid": bid,
                "ask": ask,
                "atr": atr,
                "quote_spread": ask - bid,
                "macro_regime": regime,
                "z_score": z_score,
                "vpin": vpin,
                "confidence": kronos_conf,
            }

            h_ctx = getattr(self, "harness_ctx", None)
            if h_ctx is None:
                try:
                    from core.harness.kernel import TradingContext
                    from core.harness.plugins import (
                        RiskManagementPlugin, ExecutionSafetyPlugin,
                        CointegrationGovernancePlugin, QuantWorkflowsPlugin,
                        MacroEventAlphaPlugin,
                    )
                    h_ctx = TradingContext(name="AutonomousQuantContext")
                    h_ctx.plugin(RiskManagementPlugin, balance=100000.0, max_risk_pct=0.01)
                    h_ctx.plugin(ExecutionSafetyPlugin)
                    h_ctx.plugin(CointegrationGovernancePlugin)
                    h_ctx.plugin(MacroEventAlphaPlugin)
                    h_ctx.plugin(QuantWorkflowsPlugin)
                    self.harness_ctx = h_ctx
                except Exception:
                    pass

            res = self.quant_workflow_engine.execute_workflow(
                selected_wf, ctx=h_ctx, symbol=symbol, market_snapshot=snapshot
            )
            return res.to_dict()
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug("Autonomous workflow evaluation skipped: %s", exc)
            return None

    def _process_due_schedules(self, symbol: str, paper_mode: bool) -> None:
        """Run restart-safe maintenance schedules on every pulse.

        Schedule cadence is independent of analysis-candle cadence.  A quiet
        market, a weekend, or a duplicate-candle guard must not prevent
        reconciliation, health, event review, or memory maintenance from
        running.  These jobs are observational/research actions and never
        grant order permission.

        Each handler runs under a per-handler soft budget
        (``_SCHEDULE_HANDLER_BUDGETS``): an overrunning handler is recorded
        and the wave CONTINUES — the overrun defers only its own dispatch
        window, never the rest of the wave (batch-boundary latency removed
        2026-09-27). The only remaining abandonment is the wave deadline
        (``_SCHEDULE_TIMEOUT``): when a whole wave outlives it, the remaining
        handlers are deferred — their pre-claim due points restored so the
        next wave re-claims them.

        Dispatch is fast-lane ordered (2026-09-28): the claimed wave runs
        shortest-cadence-first (registry interval, then handler budget, then
        claim order), so a wave-deadline hit defers the SLOWEST-cadence
        handlers — never the cheap 60s schedules that lose the most catch-up
        time per deferral (the 2026-09-26 audit's CLAIM_ORDER_TAIL).

        Per-handler overruns are persisted as ``schedule_handler_overrun``
        operational events — recorded at the next loop turn and at batch end
        (so a wave-ending overrun is not silently dropped) — and rolled up
        into ``self._schedule_overrun_stats`` (per-schedule counts, worst and
        total overrun seconds, last tripped budget): the durable evidence for
        tuning ``_SCHEDULE_HANDLER_BUDGETS``.
        """
        batch_started = time.time()
        guard = self._HandlerGuard()
        # Session-skip watchdog: flag schedules that have not even been
        # DISPATCHED for far beyond their cadence (the 2026-09-26 audit found
        # position_reconcile starved for a whole session this way — claimed
        # each wave, abandoned before dispatch every time).
        try:
            self._run_schedule_skip_watchdog(batch_started)
        except Exception as exc:  # noqa: BLE001 - watchdog must never break the batch
            logging.getLogger("LLMOrchestrator").debug(
                "schedule skip watchdog failed: %s", exc)
        # claim_due_schedules returns the full claimed batch WITHOUT touching
        # cadence (advancement moved to dispatch completion), so the skip set
        # at an abandonment point is the whole claim minus what actually
        # dispatched.
        claimed_batch = list(self.operations.claim_due_schedules())
        # Fast lane: dispatch cheapest-cadence claims first so a wave-deadline
        # deferral lands on the slow cadences, not the 60s handlers.
        claimed_batch = self._fast_lane_order(claimed_batch)
        claimed_names = [str(item["schedule_name"]) for item in claimed_batch]
        dispatched_names: List[str] = []

        def _release_unclaimed(items: List[Dict[str, Any]]) -> None:
            """Clear leases on claimed-but-never-dispatched schedules.

            Claims no longer advance cadence, so there is nothing to roll
            back: releasing the lease lets the next wave re-claim the row at
            its original due point. MALFORMED_RECOVERED rows keep their
            operator-review stamp (their claim was cadence-moving by design).
            """
            try:
                records = [{
                    "schedule_name": str(item.get("schedule_name") or ""),
                    "claimed_at": str(item.get("claimed_at") or ""),
                } for item in items or []
                    if item.get("schedule_state") != "MALFORMED_RECOVERED"]
                released = self.operations.release_schedule_claims(records)
                if released:
                    logging.getLogger("LLMOrchestrator").info(
                        "released %d claimed-but-undispatched schedule claim(s)", released)
            except Exception as exc:  # noqa: BLE001 - release must never break the batch
                logging.getLogger("LLMOrchestrator").debug(
                    "schedule claim release failed: %s", exc)

        for scheduled in claimed_batch:
            if guard.check():
                # Telemetry: the previous handler ran past its soft budget.
                # Persist it (event + rolling stats) and CONTINUE the wave —
                # the overrun defers only its own handler's window, not the
                # remainder (the old abandon-the-batch behavior wasted every
                # remaining dispatch slot behind one slow handler).
                self._record_schedule_overrun(guard)
            if dispatched_names and (
                    time.time() - batch_started) > self._SCHEDULE_TIMEOUT:
                # Wave deadline: the only remaining abandonment. Everything
                # already dispatched is done; the rest is deferred — recorded
                # for starvation telemetry, restored to their pre-claim due
                # points at batch end, and re-claimed by the next wave.
                try:
                    self._record_claimed_but_skipped(claimed_names, dispatched_names)
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger("LLMOrchestrator").debug(
                        "schedule skip record failed: %s", exc)
                logging.getLogger("LLMOrchestrator").warning(
                    "schedule wave exceeded its %.0fs deadline after %.1fs: "
                    "%d handler(s) deferred to the next wave",
                    self._SCHEDULE_TIMEOUT, time.time() - batch_started,
                    len(claimed_names) - len(dispatched_names),
                )
                break
            self.operations.event("scheduler", "schedule_due", {"schedule": scheduled["schedule_name"], **scheduled["payload"]}, run_id=self.run_id, correlation_id=scheduled["schedule_name"])
            schedule_name = scheduled["schedule_name"]
            # Dispatch attempt begins: note it for the session-skip watchdog
            # (which clears any active starvation flag for this schedule).
            try:
                self._schedule_dispatch_note(schedule_name, time.time())
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("LLMOrchestrator").debug(
                    "schedule dispatch note failed: %s", exc)
            dispatched_names.append(schedule_name)
            # Dispatch accepted: advance cadence NOW — the claim no longer
            # did it. This is the exactly-once boundary: a mid-handler crash
            # still consumed this dispatch turn rather than double-running
            # the schedule after lease expiry. Guarded by this claim's token;
            # fail-soft like the rest of the bookkeeping (a failed completion
            # leaves the lease to expire, which re-claims the row later).
            try:
                self.operations.complete_schedule_dispatch(
                    schedule_name, str(scheduled.get("claimed_at") or ""),
                    "COMPLETED")
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("LLMOrchestrator").debug(
                    "schedule dispatch completion failed for %s: %s",
                    schedule_name, exc)
            guard.begin(
                schedule_name,
                self._SCHEDULE_HANDLER_BUDGETS.get(
                    schedule_name, self._SCHEDULE_DEFAULT_HANDLER_BUDGET),
            )
            # --- multi-agent executor registry: principal-owned handlers first ---
            try:
                from operations.executors import try_handle as _executors_try
                if _executors_try(schedule_name, scheduled.get("payload") or {}, context=self):
                    continue
            except Exception as exc:
                logging.getLogger("LLMOrchestrator").debug("Schedule executor '%s' failed: %s", schedule_name, exc)
            # --------------------------------------------------------------------
            if schedule_name == "market_health":
                try:
                    data_status = self.data_provider.check_data_sources()
                    alerts = evaluate_health_alerts(getattr(self.store, "path", "trading_state.db"))
                    alert_outbox = enqueue_alerts(
                        getattr(self.store, "path", "trading_state.db"),
                        alerts.get("alerts", []),
                        channel="operator",
                    )
                    # Operator-visible alert-pipeline backlog (PENDING/EXPIRED
                    # counts + classification); fail-soft by contract.
                    try:
                        outbox_state = alert_outbox_health(
                            getattr(self.store, "path", "trading_state.db"))
                    except Exception as outbox_exc:
                        outbox_state = {"status": "ERROR", "error": str(outbox_exc)[:200],
                                        "execution_authority": False}
                    opportunity_bus = {"status": "SKIPPED", "execution_authority": False}
                    try:
                        if MarketScanner and self.client:
                            scanner = MarketScanner(
                                self.client,
                                fetch_func=getattr(self.data_provider, "fetch_data", None),
                                api_manager=getattr(self.data_provider, "api_manager", None),
                                macro_graph=self.macro_graph,
                            )
                            scan_rows = scanner.scan(timeframe="H1") or []
                            opportunity_bus = publish_from_scan(
                                self.store, scan_rows, timeframe="H1", agent="TechnicalNet",
                            )
                        else:
                            opportunity_bus = {
                                "status": "SKIPPED",
                                "reason": "scanner_or_client_unavailable",
                                "bus": bus_snapshot(self.store),
                                "execution_authority": False,
                            }
                    except Exception as scan_exc:
                        opportunity_bus = {
                            "status": "ERROR",
                            "error": str(scan_exc)[:400],
                            "execution_authority": False,
                        }
                    health = {"captured_at": datetime.now(timezone.utc).isoformat(),
                              "data_status": data_status, "alerts": alerts,
                              "alert_outbox": alert_outbox,
                              "outbox_health": outbox_state,
                              "opportunity_bus": opportunity_bus,
                              "execution_authority": False}
                    self.store.set_state("last_market_health", health)
                    self.operations.agent_event("TechnicalNet", "monitor_market_health", "health_report",
                                                "market_health_checked", health, run_id=self.run_id,
                                                correlation_id=schedule_name,
                                                severity="ERROR" if alerts.get("status") == "CRITICAL" else "INFO")
                except Exception as exc:
                    self.operations.event("scheduler", "market_health_failed", {"error": str(exc)[:500]},
                                          run_id=self.run_id, correlation_id=schedule_name, severity="ERROR")
            elif schedule_name == "event_monitor":
                event_rows = []
                source_ids = []
                status = "UNAVAILABLE"
                error = ""
                pipeline = {"status": "SKIPPED", "execution_authority": False}
                realtime_news = {"status": "SKIPPED", "execution_authority": False}
                try:
                    # Prefer the same calendar collector used by fetch_data (configured + MT5).
                    event_rows, calendar_source = collect_calendar_event_rows(
                        getattr(self.data_provider, "calendar", None),
                        getattr(self, "client", None) or getattr(self.data_provider, "client", None),
                    )
                    if not event_rows:
                        calendar = self.data_provider.calendar
                        if calendar is not None:
                            events = calendar.get_events(limit=20, min_importance=2,
                                                         currencies=["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"])
                            event_rows = [event.to_dict() if hasattr(event, "to_dict") else dict(event)
                                          for event in (events or [])]
                            calendar_source = "configured_calendar"
                        else:
                            calendar_source = "none"
                            error = "calendar provider unavailable"
                    for event in event_rows:
                        try:
                            source_ids.append(self.macro_graph.ingest_source(
                                title=str(event.get("event", event.get("title", "calendar event"))),
                                payload={"kind": "scheduled_calendar_observation", "event": event},
                                source_url=str(event.get("source_url", "")),
                                publisher=str(event.get("source", "runtime calendar")),
                                published_at=str(event.get("time", "")),
                                rights_note="runtime calendar observation; candidate source requires review before causal promotion",
                            ))
                        except Exception as exc:
                            self.operations.event("macro", "calendar_source_ingestion_failed",
                                                  {"error": str(exc)[:300]}, run_id=self.run_id,
                                                  correlation_id=schedule_name, severity="WARNING")
                    status = "AVAILABLE" if event_rows else ("UNAVAILABLE" if error else "EMPTY")
                    # Sub-cadence gate (2026-09-28): the heavy pipeline block
                    # (FRED + news + hot topics + graph ingest + realtime
                    # news) cost 60-281s per dispatch in the 09-27 live
                    # session (37 overruns, all dispatches) because it polls
                    # sources FASTER than they update (NewsAPI free tier
                    # refreshes per 15min, FRED per day) against a 229k-row
                    # macro graph. Calendar fetch + ingest above stay on the
                    # 60s cadence (blackout gating freshness); the heavy
                    # block runs every EVENT_MONITOR_FULL_PIPELINE_MIN_S
                    # (default 300s). Budget raise is not an option: the
                    # applier caps at interval/2 = 30s < 60s (AT_CAP).
                    _em_full_every_s = float(os.getenv(
                        "EVENT_MONITOR_FULL_PIPELINE_MIN_S", "300"))
                    _em_now = time.time()
                    _em_last = float(getattr(self, "_event_monitor_last_full_ts", 0.0) or 0.0)
                    _em_run_full = (_em_now - _em_last) >= _em_full_every_s
                    # Alternating-window split (2026-09-28 NEXT LEVER, default
                    # OFF): EVENT_MONITOR_SPLIT_WINDOWS=1 halves the heavy
                    # block - even windows run the graph pipeline (FRED + news
                    # + hot topics + ingest), odd windows run realtime-news+RSS
                    # - so a full pass costs ~half and each window lands inside
                    # the 60s budget. _event_monitor_window_no advances ONLY
                    # when a window ran, so a failed/deferred window retries
                    # its parity; on restart the counter resets and the first
                    # window is the pipeline half (mirrors the stamp reset).
                    _em_split = os.getenv("EVENT_MONITOR_SPLIT_WINDOWS", "") in {
                        "1", "true", "yes", "on"}
                    _em_rn_window = False
                    if _em_run_full:
                        _em_window_no = int(getattr(self, "_event_monitor_window_no", 0) or 0)
                        _em_rn_window = bool(_em_split and _em_window_no % 2 == 1)
                    if _em_run_full:
                        try:
                            manager = getattr(self.data_provider, "api_manager", None)
                            if not _em_rn_window:
                                fred = {}
                                api_news = []
                                hot_topics = []
                                if manager is not None:
                                    try:
                                        fred = manager.get_fred_data(catalog_fred_series()) or {}
                                    except Exception:
                                        fred = {}
                                    try:
                                        hot_topics = manager.get_hot_topics(limit=5) or []
                                    except Exception:
                                        hot_topics = []
                                    try:
                                        api_news = manager.get_news(
                                            self._get_symbol_keywords(symbol), page_size=5,
                                        ) or []
                                    except Exception:
                                        api_news = []
                                pipeline = process_cycle_data_sources(
                                    macro_graph=self.macro_graph,
                                    journal_path=str(getattr(self.store, "path", None) or "trading_state.db"),
                                    symbol=symbol,
                                    calendar_events=event_rows,
                                    api_news=api_news,
                                    hot_topics=hot_topics,
                                    macro_data=fred,
                                    api_manager=manager,
                                    # Optional providers (SeekingAlpha, OpenBB, Portfolio123)
                                    # make external HTTP calls that can block for 60+s on
                                    # retries/backoff.  Skip them in paper mode so the
                                    # maintenance pulse never hangs on unauthenticated feeds.
                                    include_optional_providers=not paper_mode,
                                )
                            extract_llm = getattr(self, "llm", None)
                            extract_claims = os.getenv("TRADING_REALTIME_NEWS_EXTRACT", "false").lower() in {
                                "1", "true", "yes",
                            }
                            if _em_split and not _em_rn_window:
                                realtime_news = {
                                    "status": "DEFERRED",
                                    "pipeline_mode": "realtime_deferred",
                                    "reason": "split_windows",
                                    "execution_authority": False,
                                }
                            else:
                                realtime_news = run_realtime_news_ingest(
                                    graph=self.macro_graph,
                                    api_manager=manager,
                                    memory=self.trade_memory,
                                    llm=extract_llm if extract_claims else None,
                                    symbol=symbol,
                                    keywords=self._get_symbol_keywords(symbol),
                                    # RSS feeds make blocking HTTP calls that can stall
                                    # the maintenance pulse.  Skip in paper mode where
                                    # external news has no execution value.
                                    include_rss=not paper_mode,
                                    extract_claims=extract_claims,
                                )
                                if _em_split and isinstance(realtime_news, dict):
                                    realtime_news.setdefault("pipeline_mode", "realtime_window")
                                    realtime_news.setdefault("split_windows", True)
                            # Stamp ONLY on a full pass that returned (a network
                            # error leaves it unstamped so the next dispatch
                            # retries the heavy block instead of waiting).
                            self._event_monitor_last_full_ts = _em_now
                            if _em_split:
                                self._event_monitor_window_no = _em_window_no + 1
                        except Exception as pipe_exc:
                            pipeline = {
                                "status": "ERROR",
                                "error": str(pipe_exc)[:300],
                                "execution_authority": False,
                            }
                            realtime_news = {
                                "status": "ERROR",
                                "error": str(pipe_exc)[:300],
                                "execution_authority": False,
                            }
                        else:
                            pipeline["pipeline_mode"] = (
                                "realtime_window" if _em_rn_window
                                else ("pipeline_window" if _em_split else "full"))
                            pipeline["split_windows"] = bool(_em_split)
                            pipeline["full_every_s"] = _em_full_every_s
                    else:
                        _em_wait = max(0.0, _em_full_every_s - (_em_now - _em_last))
                        pipeline = {
                            "status": "DEFERRED", "pipeline_mode": "calendar_only",
                            "reason": "sub_cadence_sources_poll_faster_than_they_update",
                            "next_full_in_s": round(_em_wait, 1),
                            "full_every_s": _em_full_every_s,
                            "execution_authority": False,
                        }
                        realtime_news = {
                            "status": "DEFERRED", "pipeline_mode": "calendar_only",
                            "reason": "sub_cadence",
                            "execution_authority": False,
                        }
                except Exception as exc:
                    status, error = "ERROR", str(exc)[:500]
                    calendar_source = "error"
                    realtime_news = {"status": "ERROR", "error": str(exc)[:300], "execution_authority": False}
                if "realtime_news" not in locals():
                    realtime_news = {"status": "SKIPPED", "execution_authority": False}
                event_state = {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "status": status,
                    "events": event_rows,
                    "source_ids": source_ids,
                    "calendar_source": calendar_source if "calendar_source" in locals() else "none",
                    "high_impact_count": sum(int(item.get("importance", 0) or 0) >= 3 for item in event_rows),
                    "data_source_pipeline": pipeline,
                    "realtime_news_ingest": realtime_news,
                    "error": error,
                    "execution_authority": False,
                }
                self.store.set_state(f"event_monitor:{symbol}", event_state)
                if isinstance(realtime_news, dict) and realtime_news.get("status") not in {"SKIPPED", "ERROR"}:
                    self.store.set_state("last_realtime_news_ingest", realtime_news)
                if hasattr(self.operations, "put_working_memory"):
                    try:
                        self.operations.put_working_memory(
                            f"trading:event:{symbol}", "latest_calendar_window", event_state,
                            kind="event", priority=85 if event_state["high_impact_count"] else 55,
                            source_ids=source_ids, run_id=self.run_id,
                        )
                    except Exception as exc:
                        self.operations.event("memory", "event_working_memory_failed",
                                              {"symbol": symbol, "error": str(exc)[:400]},
                                              run_id=self.run_id, correlation_id=schedule_name,
                                              severity="WARNING")
                self.operations.agent_event("MacroAnalyst", "monitor_event_window", "macro_event_state",
                                            "event_window_checked", {"symbol": symbol, **event_state},
                                            run_id=self.run_id, correlation_id=schedule_name,
                                            severity="WARNING" if status != "AVAILABLE" else "INFO")
            elif schedule_name == "position_reconcile":
                try:
                    self.reconcile_pending_orders(paper_mode)
                    if not paper_mode:
                        self.reconcile_open_positions()
                    review_escalation = self.operations.escalate_execution_reviews()
                    self.operations.agent_event(
                        "RiskController", "review_execution_exceptions", "reconciliation_report",
                        "execution_reviews_escalated", review_escalation, run_id=self.run_id,
                        correlation_id=schedule_name,
                        severity="CRITICAL" if review_escalation["escalated"] else "INFO",
                    )
                    self.operations.agent_event("RiskController", "reconcile_positions", "reconciliation_report",
                                                "position_reconciliation_checked", {"paper_mode": paper_mode},
                                                run_id=self.run_id, correlation_id=schedule_name)
                except Exception as exc:
                    self.operations.event("reconciler", "position_reconciliation_failed", {"error": str(exc)[:500]},
                                          run_id=self.run_id, correlation_id=schedule_name, severity="ERROR")
            elif schedule_name == "lifecycle_evaluation":
                try:
                    lifecycle_report = evaluate_strategy_lifecycle(
                        getattr(self.store, "path", "trading_state.db"),
                        registry_specs={str(key): value for key, value in SETUPS.items()},
                        registry_fingerprint=setup_registry_fingerprint(),
                    )
                    self.operations.agent_event(
                        "QuantitativeCoach", "evaluate_strategy_lifecycle", "research_report",
                        "strategy_lifecycle_evaluated",
                        {"sample_size": lifecycle_report.get("dataset_sample_size"),
                         "seeded": lifecycle_report.get("seeded"),
                         "transition_count": len(lifecycle_report.get("transitions", [])),
                         "alerts_enqueued": lifecycle_report.get("alerts_enqueued"),
                         "registry_drift": lifecycle_report.get("registry_drift"),
                         "execution_authority": False},
                        run_id=self.run_id, correlation_id=schedule_name,
                        severity="CRITICAL" if lifecycle_report.get("transitions") else "INFO",
                    )
                except Exception as exc:
                    self.operations.event("lifecycle", "lifecycle_evaluation_failed",
                                          {"error": str(exc)[:500]},
                                          run_id=self.run_id, correlation_id=schedule_name,
                                          severity="ERROR")
            elif schedule_name == "session_review":
                self._execute_session_review(scheduled.get("payload") or {})
            elif schedule_name == "daily_review":
                self._execute_daily_review(scheduled.get("payload") or {}, paper_mode=paper_mode)
            elif scheduled["schedule_name"] == "weekly_research":
                self._execute_weekly_research(scheduled.get("payload") or {})
            elif scheduled["schedule_name"] == "monthly_memory_archive":
                archived = self.trade_memory.monthly_memory_maintenance()
                self.operations.agent_event("QuantitativeCoach", "review_session", "research_report",
                                            "monthly_candidates_archived", archived, run_id=self.run_id,
                                            correlation_id=scheduled["schedule_name"])
            elif scheduled["schedule_name"] == "self_improvement":
                self._execute_self_improvement(scheduled.get("payload") or {})
            elif schedule_name == "macro_event_evidence":
                # Missing pipeline stage (2026-09-26 audit): turn collected
                # macro observations into macro_events + event_reactions on a
                # maintenance cadence instead of only inside trading cycles.
                try:
                    self._run_macro_event_evidence(symbol, paper_mode)
                except Exception as exc:
                    self.operations.event(
                        "macro", "macro_event_evidence_failed",
                        {"error": str(exc)[:500]}, run_id=self.run_id,
                        correlation_id=schedule_name, severity="WARNING")
            elif scheduled["schedule_name"] == "macro_post_event_review":
                review_key = f"macro_post_event_reviewed:{symbol}"
                reviewed = set(self.store.get_state(review_key, []) or [])
                due = self.macro_graph.due_post_event_reviews(symbol, hours=int(scheduled["payload"].get("hours", 48)))
                candidates = []
                for reaction in due:
                    reaction_id = str(reaction.get("id"))
                    if reaction_id in reviewed:
                        continue
                    source_id = str(reaction.get("event_source_id", ""))
                    narrative_id = self.trade_memory.save_narrative(
                        {"kind": "macro_post_event_review", "symbol": symbol, "reaction_id": reaction_id},
                        json.dumps({"rule": "Candidate lesson only; review before retrieval.", "reaction": reaction}, default=str),
                        source_ids=[source_id] if source_id else (), status="CANDIDATE")
                    self.operations.enqueue_memory("event_reaction", reaction_id, {
                        "reaction_id": reaction_id, "symbol": symbol, "event_source_id": source_id,
                        "reaction": reaction,
                    })
                    candidates.append({"reaction_id": reaction_id, "narrative_id": narrative_id})
                    reviewed.add(reaction_id)
                self.store.set_state(review_key, sorted(reviewed))
                self.operations.agent_event("MacroAnalyst", "review_event", "macro_research_report",
                                            "post_event_review_candidates", {"symbol": symbol, "candidates": candidates},
                                            run_id=self.run_id, correlation_id=scheduled["schedule_name"])
            elif schedule_name == "causal_edge_validate":
                try:
                    validated = self.causal_validator.auto_validate_all_candidates()
                    reinforcement = self._reinforce_causal_edges_from_reactions()
                    replay_floors: List[Dict[str, Any]] = []
                    try:
                        from autonomous_causal_engine import CausalEdgeBacktestOptimizer
                        bars = self.store.get_state("causal_replay_bars", []) or []
                        if not isinstance(bars, list):
                            bars = []
                        if bars:
                            optimizer = CausalEdgeBacktestOptimizer(self.macro_graph)
                            replay_floors = optimizer.apply_replay_floors(bars)
                    except Exception as floor_exc:
                        replay_floors = [{
                            "status": "ERROR",
                            "error": str(floor_exc)[:300],
                            "execution_authority": False,
                        }]
                    payload = {
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "validated_count": len(validated or []),
                        "approved_count": sum(1 for row in (validated or []) if row.get("approved")),
                        "reinforced_count": reinforcement.get("reinforced_count", 0),
                        "reinforcement_confirmations": reinforcement.get("confirmations", 0),
                        "reinforcement_misses": reinforcement.get("misses", 0),
                        "reinforcement": reinforcement,
                        "replay_floors": replay_floors,
                        "replay_pass_count": sum(
                            1 for row in replay_floors
                            if isinstance(row, dict) and row.get("to") == "REPLAY_PASS"
                        ),
                        "execution_authority": False,
                    }
                    self.store.set_state("last_causal_edge_validate", payload)
                    self.store.set_state("last_causal_auto_validate", payload["captured_at"])
                    self.operations.agent_event(
                        "MacroAnalyst", "validate_causal_edges", "macro_research_report",
                        "causal_edges_validated", payload, run_id=self.run_id,
                        correlation_id=schedule_name,
                    )
                except Exception as exc:
                    self.operations.event(
                        "scheduler", "causal_edge_validate_failed",
                        {"error": str(exc)[:500]}, run_id=self.run_id,
                        correlation_id=schedule_name, severity="WARNING",
                    )
            elif schedule_name == "economic_dynamics_seed":
                try:
                    from economic_dynamics_seed import seed_economic_dynamics
                    seed_result = seed_economic_dynamics(graph=self.macro_graph)
                    payload = {
                        **{k: seed_result.get(k) for k in (
                            "catalog_version", "created_count", "skipped_duplicates",
                            "source_id", "status", "execution_authority",
                        )},
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    }
                    self.store.set_state("economic_dynamics_seed", payload)
                    self.operations.agent_event(
                        "MacroAnalyst", "seed_economic_dynamics", "macro_research_report",
                        "economic_dynamics_seeded", payload, run_id=self.run_id,
                        correlation_id=schedule_name,
                    )
                except Exception as exc:
                    self.operations.event(
                        "scheduler", "economic_dynamics_seed_failed",
                        {"error": str(exc)[:500]}, run_id=self.run_id,
                        correlation_id=schedule_name, severity="WARNING",
                    )
            elif schedule_name == "catalog_measurement_pass":
                try:
                    from economic_dynamics_measure import run_catalog_measurement_pass
                    payload = run_catalog_measurement_pass(
                        self.macro_graph, validator=self.causal_validator,
                    )
                    payload["captured_at"] = datetime.now(timezone.utc).isoformat()
                    self.store.set_state("last_catalog_measurement_pass", payload)
                    self.operations.agent_event(
                        "MacroAnalyst", "validate_causal_edges", "macro_research_report",
                        "catalog_measurement_complete", payload, run_id=self.run_id,
                        correlation_id=schedule_name,
                    )
                except Exception as exc:
                    self.operations.event(
                        "scheduler", "catalog_measurement_failed",
                        {"error": str(exc)[:500]}, run_id=self.run_id,
                        correlation_id=schedule_name, severity="WARNING",
                    )
            elif schedule_name == "historical_regime_research":
                try:
                    from historical_regime_replay import regime_research_package
                    from historical_regimes import template_registry, research_status
                    packages = []
                    for template in template_registry()[:8]:
                        rid = str(template.get("regime_id") or "")
                        if not rid:
                            continue
                        packages.append(regime_research_package(self.macro_graph, rid))
                    payload = {
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "templates": len(template_registry()),
                        "research_status": research_status(self.macro_graph),
                        "packages": packages,
                        "execution_authority": False,
                        "status": "REGIME_RESEARCH_SNAPSHOT",
                    }
                    self.store.set_state("last_historical_regime_research", payload)
                    self.operations.agent_event(
                        "QuantitativeCoach", "run_research", "research_report",
                        "historical_regime_research", payload, run_id=self.run_id,
                        correlation_id=schedule_name,
                    )
                except Exception as exc:
                    self.operations.event(
                        "scheduler", "historical_regime_research_failed",
                        {"error": str(exc)[:500]}, run_id=self.run_id,
                        correlation_id=schedule_name, severity="WARNING",
                    )
        # Batch-end catch-up: a handler that trips its budget on the LAST
        # claimed schedule never reaches a later check(), so its ``tripped``
        # flag would stay unset — poll the elapsed time directly. After an
        # inline abandonment the exactly-once latch (``_recorded``) makes this
        # a no-op: exactly one overrun event per overrun, either way.
        if (guard.handler and not guard._recorded
                and (guard.tripped or guard.elapsed() > guard.budget)):
            self._record_schedule_overrun(guard)
        # Batch-end release: schedules claimed but never dispatched (a wave-
        # deadline breach, a tripped last handler, or a swallowed dispatch)
        # get their lease cleared — claims no longer consume cadence, so this
        # is pure bookkeeping and the next wave re-claims them at their
        # original due point. Dispatched items completed inline.
        if len(dispatched_names) < len(claimed_names):
            _release_unclaimed([item for item in claimed_batch
                                if str(item["schedule_name"]) not in set(dispatched_names)])

    def _record_schedule_overrun(self, guard: "LLMOrchestrator._HandlerGuard") -> None:
        """Persist one schedule-handler overrun (event + rolling stats).

        Fail-soft by contract: telemetry must never break the schedule batch,
        so every failure path is swallowed after logging. Latches the guard's
        ``_recorded`` flag (exactly-once semantics between the in-loop record
        and the batch-end catch-up) and clears ``tripped``; handler/budget/
        timestamp stay intact for the abandonment log line that follows.
        """
        try:
            if guard._recorded:
                return
            guard._recorded = True
            guard.tripped = False
            name = str(guard.handler or "unknown")
            elapsed = float(guard.elapsed())
            budget = float(guard.budget or 0.0)
            over_by = max(0.0, elapsed - budget)
            if elapsed <= 0:
                return  # never began; nothing to record
            self.operations.event(
                "scheduler", "schedule_handler_overrun",
                {"schedule": name, "budget_s": round(budget, 3),
                 "elapsed_s": round(elapsed, 3), "over_by_s": round(over_by, 3),
                 "execution_authority": False},
                run_id=getattr(self, "run_id", None), correlation_id=name,
                severity="WARNING",
            )
            stats = getattr(self, "_schedule_overrun_stats", None)
            if not isinstance(stats, dict):
                stats = {}
                self._schedule_overrun_stats = stats
            item = stats.setdefault(
                name, {"overruns": 0, "worst_over_by_s": 0.0, "total_over_by_s": 0.0,
                       "last_budget_s": 0.0})
            item["overruns"] = int(item.get("overruns", 0)) + 1
            item["worst_over_by_s"] = max(float(item.get("worst_over_by_s", 0.0)), over_by)
            item["total_over_by_s"] = float(item.get("total_over_by_s", 0.0)) + over_by
            item["last_budget_s"] = budget
        except Exception as exc:  # noqa: BLE001 - telemetry must never break the batch
            try:
                logging.getLogger("LLMOrchestrator").debug(
                    "schedule overrun telemetry failed: %s", exc)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Session-skip watchdog: schedule starvation that no longer has a
    # handler behind it.  claim_due_schedules advances next_run_at at CLAIM
    # time, so a batch abandoned before dispatch silently consumes cadence
    # turns — repeatedly claiming a schedule without ever running it can
    # starve it for an entire session (2026-09-26 audit: position_reconcile
    # 0 dispatches in 4.4h).  These helpers make that starvation visible:
    # per-batch skip telemetry plus a session-level alert when a schedule
    # has had NO dispatch attempt for far beyond its cadence.
    # ------------------------------------------------------------------

    _SKIP_MIN_SILENCE_S: float = 1800.0          # floor: 30min without dispatch
    _SKIP_CADENCE_MULTIPLIER: float = 10.0       # ...or 10x its interval
    _SKIP_WARMUP_S: float = 600.0                # ignore the first 10min of a session

    def _schedule_dispatch_note(self, schedule_name: str, now_mono: float) -> None:
        """Record a dispatch attempt (called just before the handler runs).

        Dispatching a schedule that was previously flagged as skipped ends
        the starvation episode: one INFO recovery event + one PENDING INFO
        operator alert (the operator-visible counterpart of the CRITICAL
        skip alert), emitted only for the FIRST dispatch after the flag —
        never per healthy dispatch.
        """
        if not isinstance(getattr(self, "_schedule_last_dispatch", None), dict):
            self._schedule_last_dispatch = {}
        self._schedule_last_dispatch[schedule_name] = float(now_mono)
        if schedule_name in getattr(self, "_schedule_skip_alerted", {}):
            self._schedule_skip_alerted.pop(schedule_name, None)
            try:
                self.operations.event(
                    "scheduler", "schedule_skips_cleared",
                    {"schedule": schedule_name, "execution_authority": False},
                    run_id=getattr(self, "run_id", None),
                    correlation_id=schedule_name, severity="INFO",
                )
                from alert_outbox import enqueue as _enqueue
                journal_path = getattr(getattr(self, "store", None), "path", "trading_state.db")
                _enqueue(journal_path, [{
                    "code": f"SCHEDULE_SESSION_SKIP_{schedule_name}",
                    "severity": "INFO",
                    "details": {
                        "reason": "schedule dispatching again after session-skip alert",
                    },
                }], channel="operator")
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("LLMOrchestrator").debug(
                    "schedule skip clear failed: %s", exc)

    def _schedule_skip_threshold(self, interval_s: int) -> float:
        """No-dispatch silence that counts as 'skipping a session part'."""
        return max(self._SKIP_MIN_SILENCE_S, self._SKIP_CADENCE_MULTIPLIER * interval_s)

    def _run_schedule_skip_watchdog(self, now_mono: float) -> None:
        """Flag schedules with no dispatch attempt far beyond their cadence.

        Silence is measured from the schedule's last dispatch attempt, or —
        for a schedule never dispatched this session — from process start
        (the first watchdog run). Threshold is ``max(30min, 10x interval)``;
        alerts re-fire at most once per ``_SCHEDULE_SKIP_REALERT_S`` while
        the starvation persists.
        """
        now_mono = float(now_mono)
        if not getattr(self, "_schedule_watchdog_ready", False):
            try:
                registry = self.operations.list_schedules()
            except Exception:
                registry = []
            self._schedule_thresholds = {
                item["schedule_name"]: self._schedule_skip_threshold(
                    int(item.get("interval_seconds") or 0))
                for item in registry}
            self._schedule_watchdog_started = now_mono
            self._schedule_watchdog_ready = True
        if now_mono - float(getattr(self, "_schedule_watchdog_started", now_mono)) < self._SKIP_WARMUP_S:
            return
        thresholds = getattr(self, "_schedule_thresholds", {}) or {}
        for name, threshold_s in thresholds.items():
            last = self._schedule_last_dispatch.get(name)
            silence = (now_mono - last) if last is not None else (
                now_mono - float(self._schedule_watchdog_started))
            if silence < threshold_s:
                continue
            if (name in self._schedule_skip_alerted
                    and now_mono - self._schedule_skip_alerted[name] < self._SCHEDULE_SKIP_REALERT_S):
                continue
            self._raise_schedule_skip_alert(name, silence, threshold_s)

    def _raise_schedule_skip_alert(self, name: str, silence_s: float,
                                   threshold_s: float) -> None:
        """Emit one starvation notice: operational event + operator alert."""
        self._schedule_skip_alerted[name] = time.time()
        self.operations.event(
            "scheduler", "schedule_session_skip",
            {"schedule": name, "silence_s": round(silence_s, 1),
             "threshold_s": round(threshold_s, 1),
             "execution_authority": False},
            run_id=getattr(self, "run_id", None), correlation_id=name,
            severity="WARNING",
        )
        try:
            from alert_outbox import enqueue as _enqueue
            journal_path = getattr(getattr(self, "store", None), "path", "trading_state.db")
            _enqueue(journal_path, [{
                "code": f"SCHEDULE_SESSION_SKIP_{name}",
                "severity": "CRITICAL",
                "details": {
                    "reason": "schedule skipped a full session part: no dispatch attempt far beyond cadence",
                    "silence_s": round(silence_s, 1),
                    "threshold_s": round(threshold_s, 1),
                    "cadence_reference": "interval x10 (min 30min)",
                },
            }], channel="operator")
        except Exception as exc:  # noqa: BLE001 - alerting must never break the batch
            logging.getLogger("LLMOrchestrator").debug(
                "schedule skip operator alert failed: %s", exc)

    def _record_claimed_but_skipped(self, claimed_names: List[str],
                                    dispatched_names: List[str],
                                    reason: str = "wave_deadline") -> None:
        """Persist one aggregate event for claimed-but-undispatched schedules.

        ``reason`` tags the deferral source so the dashboard and the tuning
        report can distinguish wave-deadline breaches from other starvation
        paths (currently the deadline is the only deferral source; the field
        keeps the event self-describing as new ones appear).
        """
        skipped = [n for n in claimed_names if n not in set(dispatched_names)]
        if not skipped:
            return
        self.operations.event(
            "scheduler", "schedule_skipped",
            {"schedules": skipped, "dispatched": len(dispatched_names),
             "claimed": len(claimed_names), "reason": str(reason or "wave_deadline"),
             "execution_authority": False},
            run_id=getattr(self, "run_id", None),
            correlation_id="scheduler", severity="WARNING",
        )

    def _run_macro_event_evidence(self, symbol: str, paper_mode: bool) -> Dict[str, Any]:
        """Scheduled stage: turn collected macro observations into event evidence.

        The 2026-09-26 audit found the macro evidence chain severed: 229k
        macro_sources / 397k macro_observations accumulated from the 60s
        event_monitor ingestion while macro_events, event_reaction_trackers
        and event_reactions stayed at ZERO — the only reaction-scheduling
        path lived inside full trading cycles, which barely ran. This stage
        closes the loop on the maintenance cadence:

        1. Calendar rows persisted by event_monitor become MacroGraph
           sources (content-hash deduped, so per-cadence re-ingest is free).
        2. COMPLETED releases (numeric actual present) are recorded into
           trade memory's macro_events — exactly once per release, gated on
           the release's atomic-fact key (record_events is append-only, so
           without the gate every pulse would spam a row).
        3. Reaction trackers are scheduled per completed release with a
           live mid-price anchor (idempotent INSERT OR IGNORE), and due
           trackers are captured into event_reactions — the same primitive
           the trading cycle uses, now also fed on cadence.

        Observational only: it never grants order permission.
        """
        graph = self.macro_graph
        calendar: List[Dict[str, Any]] = []
        try:
            state = self.store.get_state(f"event_monitor:{symbol}", {}) or {}
            if isinstance(state, dict):
                calendar = [e for e in (state.get("events") or []) if isinstance(e, dict)]
        except Exception as exc:  # noqa: BLE001 - empty calendar just skips stage 1
            logging.getLogger("LLMOrchestrator").debug(
                "macro_event_evidence calendar read failed: %s", exc)

        # Stage 1: calendar -> macro graph sources (deduped by content hash).
        pairs: List[tuple] = []
        ingest_errors = 0
        for event in calendar:
            try:
                sid = graph.ingest_source(
                    title=str(event.get("event", event.get("title", "calendar event"))),
                    payload={"kind": "scheduled_calendar_observation", "event": event},
                    source_url=str(event.get("source_url", "")),
                    publisher=str(event.get("source", "runtime calendar")),
                    published_at=str(event.get("time", "")),
                    rights_note="scheduled macro evidence stage; candidate source requires review before causal promotion",
                )
            except Exception:  # noqa: BLE001 - one bad row must not stop the stage
                ingest_errors += 1
                continue
            if sid:
                pairs.append((sid, event))

        # Stage 2: completed releases -> trade-memory macro_events, once each.
        recorded = 0
        try:
            completed = [e for e in calendar if e.get("actual") not in (None, "")]
            fact_keys = [
                f"macro_event:{e.get('time', '')}:{str(e.get('currency', '')).upper()}:"
                f"{e.get('event', e.get('title', ''))}"
                for e in completed
            ]
            if fact_keys and self.trade_memory is not None:
                known = set()
                try:
                    known = {item.get("fact_key") for item in
                             self.trade_memory.retrieve_facts(fact_keys, limit=len(fact_keys))}
                except Exception:  # noqa: BLE001 - treat as empty, re-record safely
                    known = set()
                fresh = [e for e, key in zip(completed, fact_keys) if key not in known]
                if fresh:
                    self.trade_memory.record_events(fresh)
                    recorded = len(fresh)
        except Exception as exc:  # noqa: BLE001 - telemetry below still emits
            self.operations.event(
                "macro", "macro_event_record_failed", {"error": str(exc)[:300]},
                run_id=getattr(self, "run_id", None),
                correlation_id="macro_event_evidence", severity="WARNING")

        # Stage 3: reaction trackers + capture with a live point-in-time anchor.
        tracked, capture = 0, {"scheduled": 0, "measured": 0, "completed": 0}
        anchor_price = None
        if mt5 is not None and not paper_mode:
            try:
                tick = mt5.symbol_info_tick(symbol)
            except Exception:  # noqa: BLE001
                tick = None
            if tick is not None:
                mid = (float(getattr(tick, "bid", 0) or 0) + float(getattr(tick, "ask", 0) or 0)) / 2.0
                anchor_price = mid if mid > 0 else None
        if anchor_price:
            currencies = symbol_currencies(symbol)
            for source_id, event in pairs:
                currency = str(event.get("currency", "")).upper()
                if currency and currency not in currencies:
                    continue
                try:
                    if graph.schedule_event_reaction(
                            source_id, symbol, event,
                            anchor_price=float(anchor_price), observed_at=None):
                        tracked += 1
                except Exception:  # noqa: BLE001
                    continue
            try:
                capture = graph.capture_due_event_reactions(symbol, float(anchor_price)) or {}
            except Exception as exc:  # noqa: BLE001
                capture = {"error": str(exc)[:300]}

        summary = {
            "symbol": symbol,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "calendar_events": len(calendar),
            "sources_ingested": len(pairs),
            "ingest_errors": ingest_errors,
            "macro_events_recorded": recorded,
            "trackers_scheduled": tracked,
            "reaction_capture": capture,
            "anchor": "live_tick" if anchor_price else "unavailable",
            "execution_authority": False,
        }
        self.operations.agent_event(
            "MacroAnalyst", "collect_macro_evidence", "macro_event_evidence",
            "macro_event_evidence_updated", summary, run_id=getattr(self, "run_id", None),
            correlation_id="macro_event_evidence", severity="INFO")
        return summary

    def _reinforce_causal_edges_from_reactions(self) -> Dict[str, Any]:
        """Fold measured event reactions into approved causal edges (governance-only).

        ``auto_validate_all_candidates`` only ever touches CANDIDATE edges, so
        approved edges previously never absorbed the evidence the macro
        evidence stage accumulates. This pass nudges approved-edge confidence
        by bounded, exactly-once deltas from new measured reactions.
        Failures are contained: the validation loop must keep running.
        """
        try:
            outcome = self.causal_validator.reinforce_approved_edges_from_reactions()
        except Exception as exc:  # noqa: BLE001 - validation must not fail with reinforcement
            logging.getLogger("LLMOrchestrator").warning(
                "Approved-edge reaction reinforcement failed: %s", exc)
            return {"edges_examined": 0, "reinforced_count": 0, "confirmations": 0,
                    "misses": 0, "edges": [], "errors": [str(exc)[:200]],
                    "execution_authority": False}
        return {
            "edges_examined": outcome.get("edges_examined", 0),
            "reinforced_count": outcome.get("reinforced_count", 0),
            "confirmations": outcome.get("confirmations", 0),
            "misses": outcome.get("misses", 0),
            "edges": [
                {"edge_id": row.get("edge_id"), "direction": row.get("direction"),
                 "confidence_delta": row.get("confidence_delta"),
                 "new_confidence": row.get("new_confidence")}
                for row in (outcome.get("reinforced") or [])
            ],
            "errors": outcome.get("errors") or [],
            "execution_authority": False,
        }

    def _execute_session_review(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute session review: memory metrics, growth, and strategy accuracy scorecard."""
        try:
            metrics = self.trade_memory.memory_quality_metrics() if self.trade_memory else {}
            equity = float(getattr(self.risk, "balance", 10_000.0) or 10_000.0)
            growth = self.growth_controller.refresh(starting_equity=max(equity, 1.0)) if hasattr(self, "growth_controller") and self.growth_controller else {}
            scorecard = build_strategy_accuracy_scorecard(self.store.path) if self.store else {}
            res = {
                **metrics,
                "account_growth": growth,
                "strategy_accuracy": {
                    "groups": scorecard.get("groups"),
                    "sample_size": scorecard.get("sample_size"),
                    "execution_authority": False,
                },
                "execution_authority": False,
            }
            if hasattr(self, "operations") and self.operations:
                self.operations.agent_event("QuantitativeCoach", "review_session", "research_report",
                                            "session_memory_reviewed", res, run_id=self.run_id,
                                            correlation_id="session_review")
            return res
        except Exception as exc:
            if hasattr(self, "operations") and self.operations:
                self.operations.event("scheduler", "session_review_failed", {"error": str(exc)[:500]},
                                      run_id=self.run_id, correlation_id="session_review", severity="ERROR")
            raise

    def _execute_daily_review(self, payload: Optional[Dict[str, Any]] = None, paper_mode: bool = True) -> Dict[str, Any]:
        """Execute daily narrative and broker reconciliation."""
        summary = self.trade_memory.generate_daily_narrative() if self.trade_memory else {}
        if hasattr(self, "operations") and self.operations:
            self.operations.agent_event("QuantitativeCoach", "review_session", "research_report",
                                        "daily_narrative_generated", summary, run_id=self.run_id,
                                        correlation_id="daily_review")
        daily_reconciliation = None
        if not paper_mode:
            try:
                daily_reconciliation = self.reconcile_daily_broker_history()
                if hasattr(self, "operations") and self.operations:
                    self.operations.agent_event("QuantitativeCoach", "review_session", "research_report",
                                                "daily_broker_reconciliation", daily_reconciliation, run_id=self.run_id,
                                                correlation_id="daily_review",
                                                severity="INFO" if daily_reconciliation.get("status") == "CLEAN" else "WARNING")
            except Exception as exc:
                if hasattr(self, "operations") and self.operations:
                    self.operations.event("reconciler", "daily_broker_reconciliation_failed", {"error": str(exc)},
                                          run_id=self.run_id, correlation_id="daily_review", severity="ERROR")
        return {"summary": summary, "daily_reconciliation": daily_reconciliation, "execution_authority": False}

    def _execute_weekly_research(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Weekly CPCV/DSR/PBO robustness evaluation over realized journal R-multiples."""
        scenarios = self.trade_memory.rebuild_scenarios() if self.trade_memory else []
        weekly_memory = self.trade_memory.consolidate_weekly_core() if self.trade_memory else {}
        sprint = {"status": "SKIPPED", "execution_authority": False}
        try:
            sprint = plan_research_sprints(self.store.path)
        except Exception as sprint_exc:
            sprint = {
                "status": "ERROR",
                "error": str(sprint_exc)[:400],
                "execution_authority": False,
            }
        if self.store:
            self.store.set_state("last_research_sprint", sprint)
        cpcv_report: Dict[str, Any] = {
            "status": "SKIPPED", "execution_authority": False,
        }
        try:
            with self.store._connect() as con:
                label_rows = con.execute(
                    """SELECT l.r_multiple AS r_multiple
                       FROM trade_labels l JOIN trades t ON t.ticket = l.ticket
                       WHERE t.closed_at IS NOT NULL AND l.r_multiple IS NOT NULL
                       ORDER BY t.closed_at ASC"""
                ).fetchall()
            realized_rs = [
                float(r["r_multiple"]) for r in label_rows if r["r_multiple"] is not None
            ]
            if len(realized_rs) >= 40:
                from core.cpcv_backtester import run_combinatorial_purged_backtest
                cpcv_report = run_combinatorial_purged_backtest(realized_rs)
                cpcv_report["execution_authority"] = False
            else:
                cpcv_report = {
                    "status": "INSUFFICIENT_SAMPLES_FOR_CPCV",
                    "sample_size": len(realized_rs),
                    "minimum_sample": 40,
                    "execution_authority": False,
                }
        except Exception as cpcv_exc:
            cpcv_report = {
                "status": "ERROR", "error": str(cpcv_exc)[:400],
                "execution_authority": False,
            }
        if hasattr(self, "operations") and self.operations:
            self.operations.agent_event("QuantitativeCoach", "run_cpcv_robustness", "research_report",
                                        "weekly_cpcv_robustness_evaluated", cpcv_report,
                                        run_id=self.run_id, correlation_id="weekly_research",
                                        severity="INFO" if cpcv_report.get("status") == "OK" else "WARNING")
            self.operations.agent_event("QuantitativeCoach", "run_research", "research_report",
                                        "weekly_scenarios_rebuilt", {
                                            "scenario_documents": scenarios,
                                            "weekly_memory": weekly_memory,
                                            "research_sprint": sprint,
                                            "execution_authority": False,
                                        },
                                        run_id=self.run_id, correlation_id="weekly_research")
        return {
            "scenario_documents": scenarios,
            "weekly_memory": weekly_memory,
            "research_sprint": sprint,
            "cpcv_report": cpcv_report,
            "execution_authority": False,
        }

    def _execute_self_improvement(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Self-improvement cycle: memory consolidation, metrics, and profitability review."""
        improvement: Dict[str, Any] = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "execution_authority": False,
        }
        if self.trade_memory:
            try:
                improvement["memory_quality"] = self.trade_memory.memory_quality_metrics()
            except Exception as exc:
                improvement["memory_quality_error"] = str(exc)[:400]
            try:
                improvement["weekly_memory"] = self.trade_memory.consolidate_weekly_core()
            except Exception as exc:
                improvement["weekly_memory_error"] = str(exc)[:400]
        kronos_rl_enabled = os.getenv("TRADING_KRONOS_RL_OFFLINE", "false").lower() in {
            "1", "true", "yes",
        }
        if kronos_rl_enabled and self.enable_rl and self.rl_trainer:
            try:
                improvement["kronos_rl"] = self.rl_trainer.train_on_kronos_simulator(
                    episodes=int(os.getenv("TRADING_KRONOS_RL_EPISODES", "2")),
                    max_steps=int(os.getenv("TRADING_KRONOS_RL_STEPS", "20")),
                    save=False,
                )
            except Exception as exc:
                improvement["kronos_rl_error"] = str(exc)[:400]
        else:
            improvement["kronos_rl"] = {
                "status": "SKIPPED",
                "reason": "set TRADING_KRONOS_RL_OFFLINE=true to enable",
                "execution_authority": False,
            }
        try:
            improvement["profitability"] = profitability_report(
                str(getattr(self.store, "path", None) or "trading_state.db"),
                operator_base=float(getattr(self.risk, "risk_per_trade", 0.01) or 0.01),
            )
        except Exception as exc:
            improvement["profitability_error"] = str(exc)[:400]
        if self.trade_memory:
            try:
                improvement["memory_review_queue"] = self.trade_memory.candidate_review_queue(limit=20)
            except Exception as exc:
                improvement["memory_review_queue_error"] = str(exc)[:400]
        if self.store:
            self.store.set_state("last_self_improvement", improvement)
        if hasattr(self, "operations") and self.operations:
            self.operations.agent_event(
                "QuantitativeCoach", "run_research", "research_report",
                "self_improvement_reviewed", improvement, run_id=self.run_id,
                correlation_id="self_improvement",
            )
        return improvement

    def pulse_maintenance(self, symbol: str = "", *, paper_mode: bool = True,
                          timeframe: str = "H1", strategy: str = "scalp") -> Dict[str, Any]:
        """Run reconcile/schedules/position attention without claiming a candle or calling the LLM.

        Used when AUTO scanning finds no eligible opportunity so the loop never
        invents a default symbol like EURUSD merely to keep the process alive.
        """
        symbol = str(symbol or "").strip().upper()
        self._poll_velocity_results()
        self.reconcile_pending_orders(paper_mode)
        if not paper_mode:
            self.reconcile_open_positions()
        memory_key = f"memory:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        memory_dispatch = self.velocity.submit_slow(memory_key, self.process_pending_memory, limit=3)
        if memory_dispatch.get("status") == "BACKPRESSURE":
            self.operations.event(
                "scheduler", "slow_lane_backpressure", memory_dispatch,
                run_id=self.run_id, correlation_id=memory_key, severity="WARNING",
            )
        if symbol:
            self._monitor_owned_positions_tick(symbol, timeframe, paper_mode=paper_mode)
        self.operations.heartbeat(self.run_id, {
            "symbol": symbol or "NONE", "timeframe": timeframe,
            "phase": "maintenance_pulse", "strategy": strategy,
        })
        self._run_schedules_bounded(symbol or "NONE", paper_mode)
        result = {
            "action": "HOLD",
            "reasoning": "Maintenance pulse only; no scanner opportunity selected",
            "execution_authority": False,
            "symbol": symbol or None,
        }
        self.operations.event(
            "scheduler", "maintenance_pulse", result,
            run_id=self.run_id, correlation_id=symbol or "NONE",
        )
        return result

    def _bus_emit(self, agent: str, action: str, stage: str,
                   payload: dict = None, cycle_num: int = None, symbol: str = None):
        """Emit a structured event to the Agent Event Bus for TUI streaming."""
        try:
            from core.agent_event_bus import get_bus
            get_bus().emit(agent, action, stage, payload or {}, cycle_num, symbol)
        except Exception:
            pass

    def _emit_datasource_refresh(self, *, api_manager, pipeline) -> None:
        """Push a DATA_SOURCE_REFRESH event for the command deck data-sources screen.

        Best-effort only: a transient emit failure must never block the cycle.
        Derives per-provider health from the live orchestrator data_status path plus
        the just-completed pipeline summary.
        """
        try:
            from ui.gui_api import emit_datasource_refresh
        except Exception:
            return
        try:
            data_status = None
            if pipeline and isinstance(pipeline, dict):
                for _r in pipeline.get("data_status_chain") or []:
                    if isinstance(_r, dict) and "data_status" in _r:
                        data_status = _r["data_status"]
                        break
            api_ok = bool(api_manager) and getattr(api_manager, "healthy", True)
            for _name, _meta in (pipeline.get("provider_health") or {}).items():
                try:
                    emit_datasource_refresh(
                        source_id=str(_name),
                        health="healthy" if bool(_meta.get("reachable", api_ok)) else "down",
                        fetch_count=int(_meta.get("fetch_count", 0) or 0),
                        error_count=int(_meta.get("error_count", 0) or 0),
                        latency_ms=int(_meta.get("latency_ms", 0) or 0),
                        last_fetch_ts=int(_meta.get("last_fetch_ts", 0) or 0),
                    )
                except Exception:
                    pass
            if data_status and isinstance(data_status, dict):
                try:
                    emit_datasource_refresh(
                        source_id="data_quality_gate",
                        health="healthy" if data_status.get("passed", True) else "flagged",
                        fetch_count=0,
                        error_count=len(data_status.get("reasons") or []),
                        latency_ms=int(data_status.get("latency_ms", 0) or 0),
                        last_fetch_ts=int(data_status.get("checked_at_ms", 0) or 0),
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _emit_validation_step(self, *, stage: str, status: str, metrics: dict = None,
                              notes: str = "", correlation_id: str = "", trace_id: str = "") -> None:
        """Push a VALIDATION_STEP event for the command deck pipeline screen.

        Derives per-stage rows from the deterministic validator and downstream
        control decisions. Best-effort only.
        """
        try:
            from ui.gui_api import emit_validation_step
        except Exception:
            return
        try:
            emit_validation_step(
                stage=stage,
                status=status,
                metrics=metrics or {},
                notes=notes,
                correlation_id=correlation_id,
                trace_id=trace_id,
            )
        except Exception:
            pass

    def _guardian_progress(self, stage: str = "") -> None:
        """Signal REAL progress to the Ψ Guardian (liveness relay).

        The cycle-start heartbeat alone cannot cover a multi-minute cycle:
        Ψ's 120s timeout would trip on a *healthy* loop that is merely deep
        in LLM/provider calls (observed live: HEARTBEAT trip at 121.5s while
        cycle 1 was progressing normally).  This relay is called between
        long-running stages, so the heartbeat reflects actual forward
        progress of the main thread — a genuinely hung loop still goes
        silent and trips exactly as designed.
        """
        try:
            from core.external_guardian import get_guardian
            get_guardian().record_heartbeat()
        except Exception:
            pass  # Guardian is optional — progress signal must never block

    def _run_maintenance_prefix(self, symbol: str, timeframe: str, *,
                                paper_mode: bool = True) -> None:
        """Maintenance half of every pulse, extracted for PIPELINE_V2 staging.

        When the V2 pipeline has already executed these steps as declared
        stages, it sets a skip token that is consumed here exactly once, so
        both paths execute identical work without duplication.
        """
        try:
            from core.harness.pipeline_v2 import consume_skip_maintenance
            if consume_skip_maintenance():
                return
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug("Pipeline skip-maintenance check failed: %s", exc)

        self._poll_velocity_results()

        # Reconciliation Step (Sync pending orders/closed positions) before
        # any new proposal is allowed to consume risk.
        self.reconcile_pending_orders(paper_mode)
        if not paper_mode:
             self.reconcile_open_positions()
        # Memory distillation is deliberately outside the decision/permission
        # path and uses the same configured provider under a separate budget.
        # Dispatch it to the bounded slow lane so a provider timeout cannot
        # block the five-second market pulse. The durable outbox remains the
        # source of truth and the trigger key deduplicates the hourly batch.
        memory_key = f"memory:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        memory_dispatch = self.velocity.submit_slow(memory_key, self.process_pending_memory, limit=3)
        if memory_dispatch.get("status") == "BACKPRESSURE":
            self.operations.event("scheduler", "slow_lane_backpressure", memory_dispatch,
                                  run_id=self.run_id, correlation_id=memory_key, severity="WARNING")

        # Protective management and fresh position marks run on every pulse,
        # not only when a new analysis candle is available. This closes the
        # gap where a fast move could wait for the next H1/M15 trigger before
        # trailing or recording MFE/MAE evidence.
        self._monitor_owned_positions_tick(symbol, timeframe, paper_mode=paper_mode)

        # Keep the durable worker heartbeat alive even when no completed
        # candle/event is eligible for an LLM call. This prevents an active
        # continuous process from being mistaken for an abandoned run.
        self.operations.heartbeat(self.run_id, {
            "symbol": symbol, "timeframe": timeframe, "phase": "market_pulse",
        })

    @staticmethod
    def _v2_stage_start(stage_key: str) -> None:
        """PIPELINE_V2: announce that a declared analysis stage begins now.

        No-op on the legacy path. With the V2 envelope active, the runner
        resolves the matching declared stage through this thread-local marker
        before execute_cycle re-runs the same work inline, so the code inside
        execute_cycle stays the single source of truth.
        """
        try:
            from core.harness.pipeline_v2 import mark_analysis_stage_start
            mark_analysis_stage_start(stage_key)
        except Exception:
            pass

    @staticmethod
    def _v2_seam(seam_key: str) -> None:
        """PIPELINE_V2: mark a seam point reached inside the cycle body.

        No-op unless a V2 replay recorder is attached to this thread (golden
        capture). Never changes control flow on the legacy path.
        """
        try:
            from core.harness.pipeline_v2 import mark_cycle_seam
            mark_cycle_seam(seam_key)
        except Exception:
            pass

    _CYCLE_STATE_UNSET = object()

    def _cycle_key(self) -> str:
        """Which strategy owns the state of the calling thread.

        Set at the top of execute_cycle. Anything running outside a cycle
        (startup, the main thread) shares one "_default" slot, which is the
        old single-buffer behaviour rather than a new hazard.
        """
        return getattr(self._cycle_local, "strategy", None) or "_default"

    def _new_context_buffer(self):
        try:
            from brain.memory import ContextBuffer
            return ContextBuffer(max_messages=10)
        except (ImportError, Exception) as _mem_err:
            logging.getLogger("LLMOrchestrator").warning(
                "ContextBuffer not available (%s). Using stateless mode.", _mem_err)
            return None

    @property
    def memory(self):
        """The ContextBuffer for the running strategy, created on first use."""
        key = self._cycle_key()
        with self._cycle_state_lock:
            buf = self._memory_by_strategy.get(key, self._CYCLE_STATE_UNSET)
            if buf is self._CYCLE_STATE_UNSET:
                buf = self._new_context_buffer()
                self._memory_by_strategy[key] = buf
            return buf

    @memory.setter
    def memory(self, value) -> None:
        with self._cycle_state_lock:
            self._memory_by_strategy[self._cycle_key()] = value

    @property
    def _last_llm_trace(self) -> Dict[str, Any]:
        """The usage/parse trace for the running strategy.

        Callers mutate the returned dict in place, so the getter must hand
        back the stored object rather than a copy.
        """
        key = self._cycle_key()
        with self._cycle_state_lock:
            trace = self._trace_by_strategy.get(key)
            if trace is None:
                trace = {}
                self._trace_by_strategy[key] = trace
            return trace

    @_last_llm_trace.setter
    def _last_llm_trace(self, value) -> None:
        with self._cycle_state_lock:
            self._trace_by_strategy[self._cycle_key()] = value

    def _apply_ablation_context_strips(self, toon: Dict[str, Any]) -> None:
        """Strip ablated components from the decision context (no_macro).

        Unit-testable helper: removes prompt-facing macro provenance keys so
        the decision model never sees that component during a no_macro
        measurement run. Strips are durably evented for the report engine.
        """
        if "no_macro" not in self.ablation_modes or not isinstance(toon, dict):
            return
        removed = [k for k in ("macro_data", "zero_lag_macro")
                   if toon.pop(k, None) is not None]
        if removed:
            try:
                self.operations.event(
                    "ablation", "ablation_macro_context_stripped",
                    {"keys": removed}, run_id=getattr(self, "run_id", ""))
            except Exception:
                pass
            log.info("ABLATION no_macro: stripped %s from decision context", removed)

    def execute_cycle(self, symbol: str, timeframe: str, cycle_num: int, total_cycles: int, paper_mode: bool = True, display_instance=None, strategy: str = "scalp"):
        # Claim this thread's per-strategy state before anything reads
        # self.memory or self._last_llm_trace.
        self._cycle_local.strategy = strategy
        # Cycle Separator for CLI
        if not display_instance:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print_bordered(None, f"🚀 CYCLE {cycle_num}/{total_cycles} START", f"Symbol: {symbol} | TF: {timeframe} | Time: {timestamp}", style="bold white")

        log = logging.getLogger("LLMOrchestrator")
        log.info(f"DEBUG: execute_cycle called with display_instance={display_instance}")
        start_time = time.time()

        # --- autonomy gate + lifecycle telemetry (supervisor kernel) ---
        try:
            from operations.event_bridge import emit as _emit
            from operations.autonomy import get_autonomy as _get_autonomy
            from operations.supervisor import get_supervisor as _get_supervisor

            _supervisor = _get_supervisor()
            _supervisor.heartbeat()

            # Ψ Guardian heartbeat: signal Γ is alive so Ψ doesn't trip on timeout
            self._guardian_progress("cycle_start")

            _autonomy = _get_autonomy()
            for _ctl in _autonomy.drain_controls():
                _emit("CONTROL_EXECUTED", {
                    "action": _ctl.get("action"),
                    "actor": _ctl.get("actor"),
                    "source": _ctl.get("source"),
                })
            _allowed, _why = _autonomy.trading_allowed()
            if not _allowed:
                _emit("CYCLE_SKIPPED_AUTONOMY", {"symbol": symbol, "strategy": strategy, "reason": _why})
                return {"action": "HOLD", "reasoning": f"autonomy gate: {_why}"}, 0.0

            try:
                from operations.metrics_exporter import write_metrics
                write_metrics()
            except Exception as exc:
                logging.getLogger("LLMOrchestrator").debug("Metrics export failed: %s", exc)

            _emit("CYCLE_STARTED", {
                "symbol": symbol, "timeframe": timeframe,
                "strategy": strategy, "cycle_num": cycle_num,
            })
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug("Cycle started event emission failed: %s", exc)
        # ---------------------------------------------------------------

        # Ablation boot marker: emitted on the first executed cycle so the
        # PU-0150 report engine can attribute the window to a measurement run.
        if self.ablation_modes and not getattr(self, "_ablation_boot_emitted", False):
            try:
                self.operations.event(
                    "ablation", "ablation_session_modes",
                    {"modes": list(self.ablation_modes)},
                    run_id=getattr(self, "run_id", ""))
                self._ablation_boot_emitted = True
                log.warning("ABLATION MODE ACTIVE: %s — measurement run, not production config",
                            ",".join(self.ablation_modes))
            except Exception as _abl_exc:
                log.debug("Ablation boot event failed: %s", _abl_exc)

        # Use the passed display instance or a global one if available
        display = display_instance

        # Maintenance prefix (velocity/reconcile/memory/protect/heartbeat) —
        # extracted so PIPELINE_V2 can own it as declared, observable stages.
        self._run_maintenance_prefix(symbol, timeframe, paper_mode=paper_mode)
        self._v2_stage_start("analysis.context")
        # Retrieve bounded short-term context on every pulse so a restart or
        # candle gap cannot silently lose the active plan. This is a read-only
        # context operation; memory writes occur only at lifecycle/review
        # events and never enter the permission path.
        try:
            working_context = self.operations.working_memory_context(
                f"trading:{strategy}:{symbol}",
                include_namespaces=[f"trading:event:{symbol}"],
                limit=20, max_chars=6000,
            )
            self.store.set_state(f"working_memory:{strategy}:{symbol}", working_context)
        except Exception as exc:
            self.operations.event("memory", "working_memory_pulse_failed",
                                  {"symbol": symbol, "strategy": strategy, "error": str(exc)[:500]},
                                  run_id=self.run_id, severity="WARNING")

        # Maintenance cadence is independent of new-candle eligibility.  This
        # must run before the early HOLD return below.
        self._run_schedules_bounded(symbol, paper_mode)
        self._guardian_progress("post_maintenance")

        closed_bar_candidate = self._completed_candle_candidate(symbol, timeframe, strategy)
        if closed_bar_candidate is None:
            log.info("⏸️ [CYCLE %s] HOLD %s %s (%s): waiting for next bar close (monitoring price action)", cycle_num, symbol, timeframe, strategy)
            return {"action": "HOLD", "reasoning": "No new completed candle for strategy (waiting for next bar close)"}, 0.0

        cycle_key = f"{strategy}:{symbol}:{timeframe}:{closed_bar_candidate}"
        if not self.operations.claim_task(cycle_key, strategy, "analyse_completed_candle", {"symbol": symbol, "timeframe": timeframe, "cycle": cycle_num}, self.run_id):
            log.info("⏸️ [CYCLE %s] HOLD %s %s (%s): candle %s already analyzed (duplicate trigger)", cycle_num, symbol, timeframe, strategy, closed_bar_candidate)
            return {"action": "HOLD", "reasoning": f"Duplicate analysis trigger (candle {closed_bar_candidate} already analyzed)"}, 0.0
        # The task claim is the authoritative checkpoint.  Do not advance a
        # separate candle marker here: if the process crashes after this claim
        # but before the task is completed, the lease/retry path must be able
        # to reclaim the same candle rather than silently skipping it.
        closed_bar = closed_bar_candidate
        self.operations.heartbeat(self.run_id, {"strategy": strategy, "symbol": symbol, "timeframe": timeframe})
        # Update Header
        if display:
            log.info(f"DEBUG: calling display.update_header")
            display.update_header(symbol, cycle_num, total_cycles, self.model_name)
        else:
            log.warning("DEBUG: display instance is None, SKIPPING update_header")
        
        # --- NEWS & HOLIDAY SAFEGUARDS ---
        if self.data_provider.api_manager:
            if self.data_provider.api_manager.is_holiday():
                 log.warning("⛔ MARKET HOLIDAY DETECTED. Trading Halted.")
                 # Holiday detected: release the task lease rather than completing it,
                 # allowing the candle to be evaluated properly once market opens.
                 self.operations.release_task(cycle_key, reason="market_holiday")
                 return {"action": "HOLD", "reasoning": "Market Holiday"}, 0.0
            
            # Currency-aware calendar handling occurs after data collection,
            # when the normalized source-bound event set is available to
            # EventPolicy. The legacy headline helper is not a permission gate.
        # ---------------------------------
        
        self._v2_seam("cycle.seams.data_ready")
        # 1. Data Collection
        self._bus_emit("δ", "DATA_COLLECT", "fetching", {"symbol": symbol, "tf": timeframe}, cycle_num, symbol)
        log.info(f"Fetching data for {symbol}...")
        try:
            toon = self.data_provider.fetch_data(symbol, timeframe, cycle_num=cycle_num)
            self._bus_emit("δ", "DATA_COLLECT", "fetched", {"bars": len(toon.get("ohlc", []))}, cycle_num, symbol)
            self._guardian_progress("post_data_fetch")
            self._apply_ablation_context_strips(toon)
        except Exception as exc:
            self._bus_emit("δ", "DATA_COLLECT", "error", {"error": str(exc)[:200]}, cycle_num, symbol)
            self.operations.fail_task(cycle_key, f"fetch_data failed: {exc}")
            raise

        # --- G2: Data Quality Pre-Analysis Gate ---
        # Reject stale, malformed, or revision-contaminated market data
        # BEFORE any downstream analysis, indicators, or LLM calls.
        try:
            _dq_result = self.data_quality.validate(
                toon, require_live_quote=(not paper_mode),
            )
            self._bus_emit("δ", "DATA_QUALITY", "checked", {"passed": _dq_result.passed, "reasons": _dq_result.reasons[:3]}, cycle_num, symbol)
            if not _dq_result.passed:
                _dq_reasons = "; ".join(_dq_result.reasons[:3])
                self._bus_emit("δ", "DATA_QUALITY", "rejected", {"reasons": _dq_result.reasons[:3]}, cycle_num, symbol)
                log.warning("DATA QUALITY REJECTED: %s — %s", symbol, _dq_reasons)
                self.operations.event(
                    "data_quality", "gate_rejected",
                    {"symbol": symbol, "timeframe": timeframe,
                     "reasons": _dq_result.reasons[:5]},
                    run_id=self.run_id, severity="WARNING",
                )
                self.operations.complete_task(
                    cycle_key, {"action": "HOLD", "reason": f"data_quality: {_dq_reasons}"},
                )
                return {"action": "HOLD",
                        "reasoning": f"Data quality gate rejected: {_dq_reasons}"}, 0.0
        except Exception as _dq_exc:
            log.debug("DataQualityGate check skipped (fail-open): %s", _dq_exc)

        # --- NEWS TRADING GATE ---
        # Block entries during pre-spike / spike blackout phases
        # Also fetch live FRED indicators for NFP/CPI predictions and ESI
        try:
            from core.news_trading import NewsTradingGate, get_news_trading_context
            # Wire correlation engine for dynamic blackout windows
            _corr_engine = None
            try:
                from core.news_regime_correlation import NewsRegimeCorrelation
                if not hasattr(self, '_news_corr_engine'):
                    self._news_corr_engine = NewsRegimeCorrelation()
                _corr_engine = self._news_corr_engine
            except Exception as _corr_exc:
                log.debug("NewsRegimeCorrelation init skipped: %s", _corr_exc)
            _news_gate = NewsTradingGate(correlation_engine=_corr_engine)
            _news_events = toon.get("news", [])
            if not _news_events and self.data_provider.api_manager:
                try:
                    _news_events = self.data_provider.api_manager.get_calendar() or []
                except Exception:
                    _news_events = []
            if "no_news" in self.ablation_modes:
                # Ablation: the news gate component is removed for this
                # measurement run. Events still flow downstream for the prompt,
                # but nothing is blocked and the bypass is durably measurable.
                from types import SimpleNamespace
                _news_result = SimpleNamespace(
                    allowed=True, phase="ablated_no_news",
                    block_reason="ablation: news gate disabled for measurement run")
                toon["news_gate"] = {"phase": _news_result.phase, "allowed": True,
                                     "ablated": True}
                self.operations.event(
                    "ablation", "ablation_news_gate_bypassed",
                    {"symbol": symbol, "strategy": strategy},
                    run_id=self.run_id)
            else:
                _news_result = _news_gate.evaluate(symbol=symbol, now=datetime.now(timezone.utc), events=_news_events)
            if not _news_result.allowed:
                self._bus_emit("ω", "NEWS_GATE", "blocked", {
                    "phase": _news_result.phase,
                    "reason": _news_result.block_reason[:200],
                }, cycle_num, symbol)
                log.warning("NEWS GATE BLOCKED: %s — %s", symbol, _news_result.block_reason)
                self.operations.event(
                    "news_gate", "blocked",
                    {"symbol": symbol, "phase": _news_result.phase,
                     "reason": _news_result.block_reason[:200]},
                    run_id=self.run_id, severity="WARNING",
                )
                # Release (not complete) the claimed candle: a news blackout is
                # a transient market state, exactly like the holiday gate
                # above. Completing here would permanently consume the
                # candle's analysis trigger without an LLM attempt (session-8
                # wart: a candle marked analyzed during an ISM blackout was
                # never re-analyzed after the gate lifted). The claim is
                # re-created fresh on the next cycle, so once the blackout
                # ends the candle reaches the LLM decision stage.
                self.operations.release_task(
                    cycle_key, reason=f"news_gate:{_news_result.phase}",
                )
                return {"action": "HOLD",
                        "reasoning": f"News trading gate: {_news_result.block_reason}"}, 0.0
            # Create live indicator fetcher from API manager (cached across cycles)
            _live_fetcher = None
            if self.data_provider.api_manager:
                try:
                    from core.news_indicator_fetcher import LiveIndicatorFetcher
                    if not hasattr(self, '_live_indicator_fetcher'):
                        self._live_indicator_fetcher = LiveIndicatorFetcher(
                            api_manager=self.data_provider.api_manager,
                        )
                    _live_fetcher = self._live_indicator_fetcher
                except Exception as _fetcher_exc:
                    log.debug("LiveIndicatorFetcher init skipped: %s", _fetcher_exc)
            # Store news context in toon for LLM prompt (with live indicators)
            _news_ctx = get_news_trading_context(
                symbol=symbol, events=_news_events,
                live_indicator_fetcher=_live_fetcher,
            )
            toon["news_trading_context"] = _news_ctx
            toon["news_gate"] = {"phase": _news_result.phase, "allowed": True}
            # ── News Execution Engine (Blind Spot #10) ───────────────────────
            try:
                if not hasattr(self, '_news_exec_engine') and NewsExecutionEngine is not None:
                    self._news_exec_engine = NewsExecutionEngine()
                if hasattr(self, '_news_exec_engine') and _news_events:
                    for _evt in _news_events:
                        _evt_actual = _evt.get("actual") or _evt.get("value")
                        _evt_consensus = _evt.get("forecast") or _evt.get("consensus")
                        if _evt_actual is not None and _evt_consensus is not None:
                            try:
                                _news_plan = self._news_exec_engine.create_execution_plan(
                                    event_name=_evt.get("title", _evt.get("event", "")),
                                    event_time=_evt.get("datetime", datetime.now(timezone.utc)),
                                    actual=float(_evt_actual),
                                    consensus=float(_evt_consensus),
                                    previous=float(_evt.get("previous", _evt_consensus)),
                                    market_context={
                                        "current_prices": {symbol: float(toon.get("context", {}).get("midpoint", 0))},
                                        "macro_regime": toon.get("regime", {}).get("regime_id", "EXPANSION"),
                                    },
                                    account_balance=float(self.store.get_account_info().get("balance", 100000)),
                                )
                                toon["news_execution_plan"] = _news_plan.to_dict()
                                if _news_plan.blackout_active:
                                    log.info("NEWS EXEC BLACKOUT: %s — %s", symbol, _news_plan.reasoning[:200])
                            except Exception as _nexe:
                                log.debug("News execution plan skipped: %s", _nexe)
            except Exception as _ne_exc:
                log.debug("NewsExecutionEngine init skipped: %s", _ne_exc)
            # Store NFP/CPI predictions for position sizing and LLM prompt
            if _news_ctx.get("nfp_prediction"):
                toon["nfp_prediction"] = _news_ctx["nfp_prediction"]
            if _news_ctx.get("cpi_prediction"):
                toon["cpi_prediction"] = _news_ctx["cpi_prediction"]
            if _news_ctx.get("esi"):
                toon["esi"] = _news_ctx["esi"]
            self._bus_emit("ω", "NEWS_GATE", "checked", {
                "phase": _news_result.phase,
                "is_live": _news_ctx.get("is_live", False),
                "esi_score": _news_ctx.get("esi", {}).get("esi_score", 50),
                "nfp_bias": _news_ctx.get("nfp_prediction", {}).get("directional_bias", "N/A") if _news_ctx.get("nfp_prediction") else "N/A",
                "cpi_bias": _news_ctx.get("cpi_prediction", {}).get("directional_bias", "N/A") if _news_ctx.get("cpi_prediction") else "N/A",
            }, cycle_num, symbol)
        except Exception as _news_exc:
            log.debug("NewsTradingGate check skipped (fail-open): %s", _news_exc)

        self._v2_seam("cycle.seams.causal_ready")

        # --- AUTO-RECORD EVENT OUTCOMES FOR CORRELATION ENGINE ---
        # After data fetch, check if any recent events need outcome recording
        if _corr_engine is not None:
            try:
                _now_utc = datetime.now(timezone.utc)
                _ohlc = toon.get("ohlc", [])
                if _ohlc and _news_events:
                    from core.news_trading import classify_event_tier, is_relevant_to_symbol
                    for _ev in _news_events:
                        _ev_time = None
                        for _k in ("time", "event_time", "date"):
                            _raw = _ev.get(_k)
                            if _raw:
                                if isinstance(_raw, datetime):
                                    _ev_time = _raw.astimezone(timezone.utc) if _raw.tzinfo else _raw.replace(tzinfo=timezone.utc)
                                elif isinstance(_raw, str):
                                    try:
                                        _ev_time = datetime.fromisoformat(_raw.replace("Z", "+00:00"))
                                    except ValueError:
                                        pass
                                break
                        if _ev_time is None:
                            continue
                        # Only record events from the last 2 hours
                        _age_min = (_now_utc - _ev_time).total_seconds() / 60.0
                        if _age_min < 0 or _age_min > 120:
                            continue
                        _ev_currency = _ev.get("currency", _ev.get("country", ""))
                        if not is_relevant_to_symbol(_ev_currency, symbol):
                            continue
                        _tier = classify_event_tier(_ev.get("title", ""))
                        if _tier == 0:
                            continue
                        _corr_engine.auto_record_from_price_data(
                            event_title=_ev.get("title", "UNKNOWN"),
                            tier=_tier, currency=_ev_currency,
                            event_time=_ev_time, bars=_ohlc, symbol=symbol,
                        )
            except Exception as _rec_exc:
                log.debug("Auto-record event outcome skipped: %s", _rec_exc)

        toon["microstructure"] = self.microstructure.observe(symbol, toon.get("dom", {}))
        # Record this completed bar before constructing the feature snapshot,
        # then retrieve only other-symbol observations known at the same
        # completed-bar cutoff.  Missing/stale cross-market data stays absent.
        toon["correlation_capture"] = {
            "returns_recorded": self.correlation_risk.record_completed_bars(symbol, timeframe, toon.get("ohlc", [])[:-1]),
        }
        # Best-effort: keep a thin set of intermarket proxy returns warm so
        # regime classification can run. Failures never block the cycle.
        toon["correlation_capture"]["intermarket_proxies"] = self._capture_intermarket_proxy_returns(
            timeframe, exclude_symbol=symbol,
        )
        completed_bar = toon.get("ohlc", [])[-2] if len(toon.get("ohlc", [])) >= 2 and isinstance(toon.get("ohlc", [])[-2], dict) else {}
        completed_bar_time = str(next((completed_bar.get(key) for key in ("timestamp", "time", "date") if completed_bar.get(key) not in (None, "")), ""))
        toon["cross_market_data"] = self.correlation_risk.point_in_time_observations(
            timeframe, as_of=completed_bar_time, exclude_symbol=symbol,
        )
        toon["feature_snapshot"] = build_feature_snapshot(
            toon.get("ohlc", []), toon.get("indicators", {}), timeframe=timeframe,
            source=toon.get("context", {}).get("market_data_source", "unknown"),
            captured_at=toon.get("context", {}).get("captured_at", ""),
            event_times=toon.get("news", []),
            cross_market=toon.get("cross_market_data", {}),
            # The snapshot records the symbol's possible driver identities;
            # the side-specific exposure set remains a validator/arbiter
            # decision and is persisted with the proposal.
            driver_buckets=(exposure_buckets(symbol, "BUY") | exposure_buckets(symbol, "SELL")),
        )
        _n_features = len((toon.get("feature_snapshot") or {}).get("values") or {})
        self._bus_emit("σ", "SIGNAL_BUILD", "feature_snapshot", {"features": _n_features, "tf": timeframe}, cycle_num, symbol)
        # SMC Market Structure Detection (Paper P06 - TwinAlgo, Thophet 2026)
        try:
            from core.smc_structure import (
                detect_structure,
                find_order_blocks,
                liquidity_pools,
                is_premium,
                range_position,
            )
            _ohlc_bars = toon.get("ohlc", [])
            if len(_ohlc_bars) >= 5:
                _h = [float(b.get("high", 0) or 0) for b in _ohlc_bars if isinstance(b, dict)]
                _l = [float(b.get("low", 0) or 0) for b in _ohlc_bars if isinstance(b, dict)]
                _c = [float(b.get("close", 0) or 0) for b in _ohlc_bars if isinstance(b, dict)]
                _o = [float(b.get("open", 0) or 0) for b in _ohlc_bars if isinstance(b, dict)]

                _smc_events = detect_structure(_h, _l, _c)
                _obs = find_order_blocks(_o, _h, _l, _c)
                _pools = liquidity_pools(_h, _l)

                _range_low = min(_l[-20:]) if len(_l) >= 20 else min(_l)
                _range_high = max(_h[-20:]) if len(_h) >= 20 else max(_h)
                _curr_price = _c[-1] if _c else 0.0
                _in_premium = is_premium(_curr_price, _range_low, _range_high)

                toon["smc_structure"] = {
                    "events": [e.__dict__ for e in _smc_events[-5:]],
                    "last_event": _smc_events[-1].__dict__ if _smc_events else None,
                    "has_choch": any("CHOCH" in e.kind for e in _smc_events[-3:]),
                    "order_blocks": [ob.to_dict() for ob in _obs[-3:]],
                    "liquidity_pools": _pools,
                    "in_premium": _in_premium,
                    "range_position": range_position(_curr_price, _range_low, _range_high),
                }
        except Exception as _smc_err:
            log.debug("SMC structure calculation skipped: %s", _smc_err)

        # Opportunity bus: TechnicalNet may publish eligible setup candidates
        # with zero execution authority. Acceptance still requires RiskController.
        try:
            snap_values = dict((toon.get("feature_snapshot") or {}).get("values") or {})
            # Provide registry-facing booleans from snapshot when present.
            feature_view = {
                **snap_values,
                "adx": snap_values.get("adx_14", 0),
                "ema_alignment": 1 if snap_values.get("close", 0) > snap_values.get("ema_50", 0) else -1,
                "mtf_alignment": 0,
                "near_key_level": bool(snap_values.get("near_key_level", False)),
                "confirmed_candle": True,
                "volume_confirmed": float(snap_values.get("tick_volume_ratio_20", 0) or 0) >= 1.1,
                "liquidity_sweep": bool(
                    snap_values.get("liquidity_sweep_confirmation_low")
                    or snap_values.get("liquidity_sweep_confirmation_high")
                ),
                "asian_range_breakout": bool(
                    snap_values.get("asian_range_breakout_up")
                    or snap_values.get("asian_range_breakout_down")
                ),
                # See the proposal feature site: post-event continuation window.
                "macro_continuation": str(
                    (toon.get("news_gate") or {}).get("phase") or "").upper()
                in ("POST_NEWS_ENTRY", "TREND_CONTINUATION"),
                "completed_bar_time": (toon.get("feature_snapshot") or {}).get("completed_bar_time"),
                "feature_values_hash": (toon.get("feature_snapshot") or {}).get("values_hash"),
            }
            candidate = opportunity_from_features(
                feature_view, symbol=symbol, timeframe=timeframe,
                session=str(self._get_market_session() if hasattr(self, "_get_market_session") else ""),
            )
            if candidate:
                toon["opportunity_bus"] = publish_opportunities(
                    self.store, [candidate], agent="TechnicalNet",
                )
        except Exception as bus_exc:
            toon["opportunity_bus"] = {
                "status": "ERROR",
                "error": str(bus_exc)[:300],
                "execution_authority": False,
            }
        completed_closes = [bar.get("close") for bar in toon.get("ohlc", [])[:-1] if isinstance(bar, dict)]
        adaptive_regime = self.adaptive_research.update_from_closes(
            symbol, timeframe, completed_closes,
            completed_bar_time=str(toon.get("feature_snapshot", {}).get("completed_bar_time", "")),
        )
        adaptive_allowed = self._research_influence_allowed("adaptive", paper_mode=paper_mode)
        if not adaptive_allowed:
            toon["advisory_regime"] = {
                "available": False,
                "advisory_only": True,
                "influence_allowed": False,
                "reason": "adaptive HMM/GARCH influence gate is not approved",
                "completed_bar_time": adaptive_regime.get("completed_bar_time") if isinstance(adaptive_regime, dict) else "",
            }
        else:
            toon["advisory_regime"] = {
                **(adaptive_regime if isinstance(adaptive_regime, dict) else {}),
                "influence_allowed": True,
                "advisory_only": True,
            }
        # Kronos alpha/vol is prompt/research context only — never a permission signal.
        toon["kronos_advisory"] = self._kronos_advisory_snapshot(symbol, toon)
        atr_value = toon.get("indicators", {}).get("atr_14", 0)
        if isinstance(atr_value, list):
            atr_value = atr_value[-1] if atr_value else 0
        try:
            atr_value = float(atr_value or 0)
        except (TypeError, ValueError):
            atr_value = 0.0
        if math.isfinite(atr_value) and atr_value > 0:
            self.store.set_state(f"position_atr:{symbol}:{timeframe}", {
                "atr": atr_value,
                "completed_bar": str(toon.get("feature_snapshot", {}).get("completed_bar_time", "")),
                "captured_at": toon.get("context", {}).get("captured_at", ""),
            })
        self.operations.agent_event("TechnicalNet", "monitor_market_health", "market_snapshot",
                                    "snapshot_collected", {
                                        "symbol": symbol, "timeframe": timeframe,
                                        "spread": toon.get("dom", {}).get("spread"),
                                        "news_count": len(toon.get("news", [])),
                                    }, run_id=self.run_id, correlation_id=cycle_key)
        self.trade_memory.record_events(toon.get("news", []))
        toon["event_state"] = evaluate_event_policy(symbol, toon.get("news", []))
        try:
            from economic_dynamics_catalog import EVENT_TEMPLATES
            toon["event_playbooks"] = [
                {**dict(template), "execution_authority": False}
                for template in EVENT_TEMPLATES
            ]
        except Exception:
            toon["event_playbooks"] = []
        macro_source_ids = []
        macro_event_sources = []
        macro_entities = [symbol]
        _cached_pulse = getattr(self, "_pulse_macro_cache", {}).get(symbol)
        _use_cached_pulse = (
            _cached_pulse is not None
            and (time.time() - float(_cached_pulse.get("ts", 0))) < 30.0
            and bool(_cached_pulse.get("macro_source_ids") or _cached_pulse.get("data_source_pipeline"))
        )

        if _use_cached_pulse:
            macro_source_ids = list(_cached_pulse.get("macro_source_ids") or [])
            macro_event_sources = list(_cached_pulse.get("macro_event_sources") or [])
            for _e in (_cached_pulse.get("macro_entities") or []):
                if _e and _e not in macro_entities:
                    macro_entities.append(str(_e))
            toon["data_source_pipeline"] = _cached_pulse.get("data_source_pipeline") or {}
            toon["macro_observations_ingest"] = _cached_pulse.get("macro_observations_ingest") or {}
            toon["realtime_news_ingest"] = _cached_pulse.get("realtime_news_ingest") or {}
        else:
            # Bulk-ingest every calendar event in ONE transaction instead of one
            # SQLite connection per event; content-hash dedupe semantics unchanged.
            try:
                news_events = [event for event in (toon.get("news") or []) if isinstance(event, dict)]
                bulk_items = [
                    {
                        "title": str(event.get("event") or event.get("title") or "").strip() or "calendar event",
                        "payload": event,
                        "source_url": str(event.get("source_url", "")),
                        "publisher": str(event.get("source", "runtime calendar")),
                        "published_at": str(event.get("time", "")),
                        "rights_note": "runtime calendar observation; candidate source requires review before causal promotion",
                    }
                    for event in news_events
                ]
                macro_source_ids = self.macro_graph.ingest_sources_bulk(bulk_items)
                macro_event_sources = list(zip(macro_source_ids, news_events))
            except Exception as bulk_ingest_exc:
                toon["macro_bulk_ingest"] = {
                    "status": "ERROR", "error": str(bulk_ingest_exc)[:300],
                    "execution_authority": False,
                }
                macro_source_ids = []
                macro_event_sources = []
            for _sid, event in macro_event_sources:
                if isinstance(event, dict) and event.get("currency"):
                    macro_entities.append(str(event["currency"]))
            # Process FRED / NewsAPI / optional providers into observations + graph.
            # Calendar remains the only event-reaction scheduler (below).
            try:
                pipeline = process_cycle_data_sources(
                    macro_graph=self.macro_graph,
                    journal_path=str(getattr(self.store, "path", None) or "trading_state.db"),
                    symbol=symbol,
                    calendar_events=toon.get("news") or [],
                    api_news=toon.get("api_news") or [],
                    hot_topics=toon.get("hot_topics") or [],
                    macro_data=toon.get("macro_data") or {},
                    api_manager=getattr(self.data_provider, "api_manager", None),
                    observed_at=str(toon.get("context", {}).get("captured_at") or ""),
                    include_optional_providers=(not paper_mode),
                )
                toon["data_source_pipeline"] = pipeline
                try:
                    self._emit_datasource_refresh(
                        api_manager=getattr(self.data_provider, "api_manager", None),
                        pipeline=pipeline,
                    )
                except Exception as _ds_emit_exc:
                    log.debug("Datasource refresh emit skipped: %s", _ds_emit_exc)
                toon["macro_observations_ingest"] = pipeline.get("observations") or {}
                for entity in pipeline.get("macro_entities_extra") or []:
                    if entity and entity not in macro_entities:
                        macro_entities.append(str(entity))
                try:
                    toon["realtime_news_ingest"] = run_realtime_news_ingest(
                        graph=self.macro_graph,
                        api_manager=getattr(self.data_provider, "api_manager", None),
                        memory=self.trade_memory,
                        llm=None,
                        symbol=symbol,
                        keywords=self._get_symbol_keywords(symbol),
                        include_rss=False,
                        extract_claims=False,
                    )
                    for entity in (toon["realtime_news_ingest"].get("macro_entities_extra") or []):
                        if entity and entity not in macro_entities:
                            macro_entities.append(str(entity))
                except Exception as news_exc:
                    toon["realtime_news_ingest"] = {
                        "status": "ERROR",
                        "error": str(news_exc)[:300],
                        "execution_authority": False,
                    }
            except Exception as pipe_exc:
                toon["data_source_pipeline"] = {
                    "status": "ERROR",
                    "error": str(pipe_exc)[:300],
                    "execution_authority": False,
                }
        toon["macro_source_ids"] = macro_source_ids
        toon["macro_entities"] = list(dict.fromkeys(macro_entities))
        point_values = toon.get("feature_snapshot", {}).get("values", {})
        anchor_price = point_values.get("close") or toon.get("dom", {}).get("mid_price")
        observed_at = toon.get("context", {}).get("captured_at")
        try:
            toon["working_memory"] = self.operations.working_memory_context(
                f"trading:{strategy}:{symbol}", as_of=observed_at or None,
                include_namespaces=[f"trading:event:{symbol}"],
                limit=20, max_chars=6000,
            )
        except Exception as exc:
            # Missing/invalid short-term context must never block market data
            # collection, but the failure remains auditable and cannot create
            # execution authority.
            toon["working_memory"] = {
                "namespace": f"trading:{strategy}:{symbol}", "as_of": observed_at,
                "items": [], "count": 0, "prompt_chars": 0,
                "status": "UNAVAILABLE", "reason": str(exc)[:300],
                "execution_authority": False,
            }
            self.operations.event("memory", "working_memory_retrieval_failed",
                                  {"symbol": symbol, "strategy": strategy, "error": str(exc)[:500]},
                                  run_id=self.run_id, correlation_id=cycle_key,
                                  severity="WARNING")
        tracked = 0
        skipped_schedule = []
        if anchor_price:
            relevant_currencies = symbol_currencies(symbol)
            for source_id, event in macro_event_sources:
                currency = str(event.get("currency", "")).upper()
                if currency and currency not in relevant_currencies:
                    skipped_schedule.append({"source_id": source_id, "reason": "currency_mismatch"})
                    continue
                if self.macro_graph.schedule_event_reaction(source_id, symbol, event,
                                                            anchor_price=float(anchor_price), observed_at=observed_at):
                    tracked += 1
                else:
                    skipped_schedule.append({
                        "source_id": source_id,
                        "reason": "missing_actual_forecast_or_time",
                        "has_actual": event.get("actual") not in (None, ""),
                        "has_forecast": event.get("forecast") not in (None, ""),
                        "has_previous": event.get("previous") not in (None, ""),
                        "has_time": any(event.get(k) not in (None, "") for k in ("time", "timestamp", "datetime", "scheduled_at", "date")),
                    })
            capture = self.macro_graph.capture_due_event_reactions(symbol, float(anchor_price), observed_at=observed_at)
            toon["event_reaction_capture"] = {
                "new_trackers": tracked,
                "skipped_schedule": skipped_schedule[:20],
                "calendar_events_considered": len(macro_event_sources),
                **capture,
                "execution_authority": False,
            }
        else:
            toon["event_reaction_capture"] = {
                "new_trackers": 0, "scheduled": 0, "measured": 0,
                "completed": 0, "reason": "no point-in-time anchor price",
                "skipped_schedule": skipped_schedule[:20],
                "execution_authority": False,
            }
        toon["recent_event_reactions"] = self.macro_graph.recent_event_reactions(symbol, as_of=observed_at or None)
        reference_event = next((event for event in toon.get("news", [])
                                if event.get("actual") not in (None, "") or event.get("forecast") not in (None, "")), {})
        toon["similar_event_reactions"] = self.macro_graph.similar_event_reactions(
            symbol, actual=reference_event.get("actual"), forecast=reference_event.get("forecast"),
            previous=reference_event.get("previous"), limit=5, as_of=observed_at or None)
        toon["macro_causal_context"] = self.macro_graph.approved_edges(
            toon.get("macro_entities") or macro_entities,
            as_of=observed_at or None,
        )
        # T1: classify live intermarket regime from stored cross-asset returns.
        # Fail-closed to UNKNOWN/INSUFFICIENT_* — never grants order permission.
        try:
            toon["intermarket_regime"] = build_intermarket_regime_snapshot(
                self.correlation_risk,
                timeframe,
                approved_edges=toon.get("macro_causal_context") or [],
            )
        except Exception as regime_exc:
            toon["intermarket_regime"] = {
                "regime_id": "UNKNOWN",
                "confidence": 0.0,
                "status": "ERROR",
                "error": str(regime_exc)[:300],
                "execution_authority": False,
            }
        regime_id = str((toon.get("intermarket_regime") or {}).get("regime_id") or "UNKNOWN")
        self._bus_emit("μ", "REGIME_DETECT", "regime_classified", {"regime": regime_id, "confidence": (toon.get("intermarket_regime") or {}).get("confidence", 0)}, cycle_num, symbol)
        toon["macro_causal_context_regime"] = filter_edges_for_live_regime(
            toon.get("macro_causal_context") or [], regime_id,
        )
        # Mirror measured reactions onto catalog seed, then emit STAT_PASS
        # opportunities only (advisory bus — no order authority).
        try:
            from economic_dynamics_measure import mirror_recent_reactions
            recent_rows = toon.get("recent_event_reactions") or []
            if isinstance(recent_rows, dict):
                recent_rows = recent_rows.get("reactions") or recent_rows.get("items") or []
            toon["catalog_reaction_mirror"] = mirror_recent_reactions(
                self.macro_graph,
                symbol=symbol,
                news_events=toon.get("news") or [],
                recent_reactions=recent_rows if isinstance(recent_rows, list) else [],
            )
        except Exception as mirror_exc:
            toon["catalog_reaction_mirror"] = {
                "mirrored_count": 0,
                "status": "ERROR",
                "error": str(mirror_exc)[:300],
                "execution_authority": False,
            }
        try:
            from edge_lifecycle import current_lifecycle
            lifecycle_by_edge = {}
            for edge in (toon.get("macro_causal_context_regime") or []):
                if not isinstance(edge, dict) or edge.get("edge_id") is None:
                    continue
                try:
                    lifecycle_by_edge[edge["edge_id"]] = current_lifecycle(
                        self.macro_graph, int(edge["edge_id"]),
                    ).get("lifecycle") or edge.get("status")
                except Exception:
                    lifecycle_by_edge[edge["edge_id"]] = edge.get("status")
            toon["macro_opportunity_bus"] = publish_from_stat_pass_edges(
                self.store,
                toon.get("macro_causal_context_regime") or [],
                symbol=symbol,
                timeframe=timeframe,
                regime_id=regime_id,
                lifecycle_by_edge=lifecycle_by_edge,
                agent="MacroAnalyst",
            )
        except Exception as bus_exc:
            toon["macro_opportunity_bus"] = {
                "status": "ERROR",
                "error": str(bus_exc)[:300],
                "execution_authority": False,
            }
        causal_advisory = self._run_causal_advisory(symbol, macro_entities)
        toon["causal_validation"] = {
            "validated_count": len(causal_advisory.get("validated") or []),
            "validated": causal_advisory.get("validated") or [],
            "error": causal_advisory.get("error"),
            "execution_authority": False,
        }
        toon["causal_setup_proposals"] = causal_advisory.get("setups") or []
        # Multi-hop causal propagation first so fusion preview can consume bias.
        try:
            toon["causal_propagation"] = propagate_causal_paths(
                toon.get("macro_causal_context_regime") or [],
                seed_entities=toon.get("macro_entities") or macro_entities,
                symbol=symbol,
            )
        except Exception as prop_exc:
            toon["causal_propagation"] = {
                "status": "ERROR",
                "path_id": None,
                "error": str(prop_exc)[:300],
                "execution_authority": False,
            }
        # Advisory fusion preview for both sides (research/prompt only).
        # Authoritative BLOCK for macro setups is applied in _validate_proposal.
        try:
            event_for_fusion = toon.get("event_state") or evaluate_event_policy(symbol, toon.get("news", []))
            prop_for_fusion = toon.get("causal_propagation") if isinstance(toon.get("causal_propagation"), dict) else {}
            preview = {
                side_name: score_opportunity(
                    setup_id="macro_continuation_v1",
                    side=side_name,
                    symbol=symbol,
                    event_state=event_for_fusion,
                    causal_edges=toon.get("macro_causal_context_regime") or [],
                    causal_propagation=prop_for_fusion,
                    ta_eligible=True,
                    ta_confidence=0.0,
                    adx=0.0,
                )
                for side_name in ("BUY", "SELL")
            }
            toon["opportunity_fusion_preview"] = {
                **preview,
                "execution_authority": False,
                "limitation": "Preview only; RiskController fusion gate runs after the LLM proposal.",
            }
            # Default prompt field until a sided proposal is validated.
            toon["opportunity_fusion"] = {
                "status": "PREVIEW",
                "score": max(float(preview["BUY"].get("score") or 0), float(preview["SELL"].get("score") or 0)),
                "buy": preview["BUY"],
                "sell": preview["SELL"],
                "execution_authority": False,
            }
        except Exception as fusion_exc:
            toon["opportunity_fusion"] = {
                "status": "ERROR",
                "error": str(fusion_exc)[:300],
                "execution_authority": False,
            }
        # Structured macro state (replaces headline fundamental_score on TOON).
        try:
            obs_ingest = toon.get("macro_observations_ingest") or {}
            fred_obs = obs_ingest.get("fred") if isinstance(obs_ingest, dict) else {}
            from macro_observations import recent as recent_macro_obs
            recent_obs = recent_macro_obs(self.store.path, limit=30)
            toon["macro_state_vector"] = build_macro_state_vector(
                macro_observations=recent_obs.get("items") or [],
                intermarket_regime=toon.get("intermarket_regime") or {},
                opportunity_fusion=toon.get("opportunity_fusion") or {},
                causal_propagation=toon.get("causal_propagation") or {},
                legacy_news_score=int((toon.get("confluence") or {}).get("fundamental_score") or 0),
            )
            toon["macro_state_vector"]["fred_ingest_status"] = (
                fred_obs.get("status") if isinstance(fred_obs, dict) else None
            )
        except Exception as msv_exc:
            toon["macro_state_vector"] = {
                "status": "ERROR",
                "error": str(msv_exc)[:300],
                "execution_authority": False,
            }
        # Laya DecisionBundle advisory: regime/liquidity/kill-switch context
        # from the MacroAnalyst graph node, projected onto the cycle prompt.
        try:
            self._attach_decision_bundle_advisory(toon)
        except Exception as bundle_exc:  # noqa: BLE001 - advisory must never break the cycle
            toon["laya_decision_bundle"] = {
                "status": "ERROR", "error": str(bundle_exc)[:300],
                "execution_authority": False, "source": "laya_decision_bundle",
            }
        # One auditable CycleBrief per pulse (never grants order permission).
        try:
            try:
                open_reviews = self.operations.execution_reviews(include_resolved=False) or []
            except Exception:
                open_reviews = []
            kill_active = False
            try:
                kill_active = bool(self.store.is_kill_switch_active())
            except Exception:
                kill_active = False
            brief = build_cycle_brief(
                toon,
                symbol=symbol,
                timeframe=timeframe,
                strategy=strategy,
                cycle_key=str(cycle_key),
                kill_switch_active=kill_active,
                open_reviews=open_reviews if isinstance(open_reviews, list) else [],
                fusion=toon.get("opportunity_fusion"),
                propagation=toon.get("causal_propagation"),
            )
            toon["cycle_brief"] = brief
            persist_cycle_brief(
                self.store, brief, operations=self.operations, run_id=self.run_id,
            )
        except Exception as brief_exc:
            toon["cycle_brief"] = {
                "status": "ERROR",
                "error": str(brief_exc)[:300],
                "execution_authority": False,
            }

        # Autonomous Institutional Quant Workflow evaluation
        try:
            workflow_eval = self.evaluate_institutional_workflow(symbol, timeframe, toon, paper_mode=paper_mode)
            if workflow_eval:
                toon["institutional_workflow"] = workflow_eval
                self.operations.event("quant_workflow", "workflow_evaluated", {
                    "symbol": symbol,
                    "workflow": workflow_eval.get("workflow_name"),
                    "decision": workflow_eval.get("decision"),
                    "latency_ms": workflow_eval.get("total_latency_ms"),
                }, run_id=self.run_id, correlation_id=cycle_key)
        except Exception as _wf_eval_exc:
            logging.getLogger("LLMOrchestrator").debug("Workflow eval in cycle skipped: %s", _wf_eval_exc)

        toon["macro_memory_as_of"] = toon["similar_event_reactions"].get("retrieval_as_of")
        technical = toon.get("technical_analysis", {})
        toon["playbook"] = self.trade_memory.current_playbook(symbol, toon.get("news", []), technical)
        tags = {"symbol": symbol, "strategy": strategy, "regime": technical.get("primary_trend", "UNKNOWN")}
        memory_context = self.trade_memory.retrieve_for_proposal(
            tags, core_namespaces=["TRADING_PERSONALITY", "RISK_POLICY", "STRATEGY_RULE", "STABLE_LESSON"],
            core_limit=8, as_of=observed_at or None)
        toon["similar_past_regimes"] = memory_context["similar_past_regimes"]
        toon["verified_memories"] = memory_context["verified_memories"]
        toon["scenario_memory"] = memory_context["scenario_memory"]
        toon["core_memory"] = memory_context["core_memory"]
        toon["memory_retrieval_ids"] = memory_context["retrieval_ids"]
        toon["memory_retrieval_as_of"] = memory_context.get("retrieval_as_of")
        toon["learned_patterns"] = self.trade_memory.pattern_summary()
        # Preserve the exact point-in-time evidence that reaches the LLM. This
        # allows an offline replay to distinguish observed data from hindsight.
        playbook_version = self.operations.version_configuration("playbook", toon["playbook"])
        policy_version = self.operations.version_configuration("risk_policy", {
            "risk_per_trade": self.risk.risk_per_trade,
            "strategy": strategy,
            "validator_min_rr": self.validator.min_rr,
            "validator_min_confluence": self.validator.min_confluence,
        })
        feature_version = self.operations.version_configuration("feature_spec", {
            "version": toon.get("feature_snapshot", {}).get("version"),
            "schema_hash": toon.get("feature_snapshot", {}).get("schema_hash"),
        })
        strategy_version = self.operations.version_configuration("strategy_spec", {
            "strategy": strategy, "timeframe": timeframe, "setup_registry": "setup_registry_v1",
            "setup_registry_sha256": setup_registry_fingerprint(),
        })
        model_version = self.operations.version_configuration("model_version", {"model": self.model_name})
        prompt_version = self.operations.version_configuration("prompt_template", {
            "model": self.model_name, "template": "trade_decision_v2", "role": "signal_proposer",
            "template_sha256": stable_content_hash(SIGNAL_SYSTEM_PROMPT_TEMPLATE),
        })
        snapshot_payload = {
            "dom": toon.get("dom", {}), "ohlc": toon.get("ohlc", []),
            "indicators": toon.get("indicators", {}), "technical_analysis": technical,
            "feature_snapshot": toon.get("feature_snapshot", {}),
            "microstructure": toon.get("microstructure", {}),
            "event_state": toon.get("event_state", {}),
            "macro_memory_as_of": toon.get("macro_memory_as_of"),
            "multi_timeframe": toon.get("multi_timeframe", {}), "news": toon.get("news", []),
            "account": toon.get("account", {}), "playbook": toon["playbook"],
            "memory_tags": tags, "memory_retrieval_as_of": toon.get("memory_retrieval_as_of"),
            "memory_retrieval_ids": toon.get("memory_retrieval_ids", {}),
            "working_memory": toon.get("working_memory", {}),
            "playbook_version": playbook_version,
            "policy_version": policy_version,
            "feature_version": feature_version, "strategy_version": strategy_version,
            "model_version": model_version, "prompt_version": prompt_version,
        }
        snapshot_id = self.operations.store_snapshot("proposal_input", snapshot_payload, run_id=self.run_id,
                                                     symbol=symbol, timeframe=timeframe, candle_id=str(closed_bar))
        toon["journal_context"] = {"snapshot_id": snapshot_id, "playbook_version": playbook_version,
                                   "policy_version": policy_version, "feature_version": feature_version,
                                   "strategy_version": strategy_version, "model_version": model_version,
                                   "prompt_version": prompt_version, "candle_id": str(closed_bar),
                                   "memory_retrieval_as_of": toon.get("memory_retrieval_as_of")}
        
        # Inject Open Positions for Analysis
        # Inject Open Positions for Analysis (Detailed Context)
        open_pos_list = []
        if getattr(self.data_provider, "trade_manager", None):
            if paper_mode and self.paper_broker:
                for t, p in getattr(self.paper_broker, "positions", {}).items():
                    if str(p.get("symbol", "")) == str(symbol):
                        open_pos_list.append({
                            "ticket": int(t), "symbol": symbol, "type": p.get("side", "BUY"),
                            "volume": float(p.get("volume", 0.0) or 0.0),
                            "price_open": float(p.get("price_open", 0.0) or 0.0),
                            "sl": float(p.get("sl", 0.0) or 0.0),
                            "tp": float(p.get("tp", 0.0) or 0.0),
                            "profit": float(p.get("profit", 0.0) or 0.0),
                            "magic": int(p.get("magic", 0) or 0),
                            "paper": True,
                        })
            elif not paper_mode:
                # Use TradeManager to get enriched context (R-multiple, etc.)
                raw_positions = positions_overview()
                for p in raw_positions:
                    if p['symbol'] == symbol:
                        ctx = self.data_provider.trade_manager.get_position_context(p['ticket'])
                        if ctx: open_pos_list.append(ctx)
        
        toon["open_positions"] = open_pos_list
        
        # Update Dashboard Panels
        if display:
            display.update_market_data(toon)
            display.update_technicals(toon.get("indicators", {}))
            display.update_multi_timeframe(toon.get("multi_timeframe", {}))
            display.update_news(toon.get("news", []))
            
        # Add Contextual Memory & RL State
        # Calculate Real RL Metrics from History
        closed_trades = [t for t in self.perf.history if t["status"] == "CLOSED"]
        recent_20 = closed_trades[-20:] if closed_trades else []
        
        win_rate = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        profit_factor = 0.0
        max_dd = 0.0
        
        if recent_20:
            wins = [t for t in recent_20 if t["pnl"] > 0]
            win_rate = len(wins) / len(recent_20)
            
        if closed_trades:
            # Profit Factor
            gross_profit = sum(t["pnl"] for t in closed_trades if t["pnl"] > 0)
            gross_loss = abs(sum(t["pnl"] for t in closed_trades if t["pnl"] < 0))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.9
            
            # Avg Win/Loss
            all_wins = [t for t in closed_trades if t["pnl"] > 0]
            all_losses = [t for t in closed_trades if t["pnl"] <= 0]
            avg_win = sum(t["pnl"] for t in all_wins) / len(all_wins) if all_wins else 0
            avg_loss = sum(t["pnl"] for t in all_losses) / len(all_losses) if all_losses else 0
            
            # Calculate Max DD dynamically
            max_dd = self.perf.calc_max_drawdown()

        if display:
             display.update_metrics({
                 "win_rate": win_rate * 100,
                 "drawdown": max_dd,
                 "session_pnl": sum(t['pnl'] for t in closed_trades) if closed_trades else 0.0
             })
            

            
        avg_reward = 0.0
        if closed_trades:
            total_pnl = sum(t["pnl"] for t in closed_trades)
            avg_reward = total_pnl / len(closed_trades)
            
        rl_state = {
            "win_rate_20": round(win_rate, 2),
            "avg_reward": round(avg_reward, 2),
            "strategy_weight": 1.0 + (win_rate - 0.5) # Dynamic weight based on performance
        }
        
        # Advanced Metrics for Display
        system_metrics = {
            "Win Rate (20)": f"{win_rate:.1%}",
            "Profit Factor": f"{profit_factor:.2f}",
            "Avg Win": f"${avg_win:.2f}",
            "Avg Loss": f"${avg_loss:.2f}",
            "Rolling Sharpe": f"{self.perf.calc_rolling_sharpe(window=20):.3f}",
            "Rolling Sortino": f"{self.perf.calc_rolling_sortino(window=20):.3f}",
            "Max Drawdown": f"${max_dd:.2f}",
            "Total Trades": len(closed_trades),
            "Latency": f"{time.time() - start_time:.2f}s"
        }
        toon["system_metrics"] = system_metrics
        
        toon["memory"] = {
            "recent_trades": self.perf.history[-5:] if self.perf.history else [],
            "portfolio_exposure": self.risk.open_positions,
            "rl_state": rl_state
        }
        
        # Build RL State and Get RL Suggestion
        rl_market_state = None
        rl_suggested_action = None
        rl_was_exploration = False
        rl_metrics = {}
        
        # The RL suggestion is available to simulated/research cycles only.
        # A live broker run must remain deterministic and model-independent.
        # RL state is captured for PAPER and DEMO modes (non-LIVE) to enable
        # the online learning loop. Action influence requires explicit opt-in.
        # Live mode is detected by checking if execution authority is enabled
        # on the data provider (paper brokers don't have execution authority).
        has_paper_broker = getattr(self.data_provider, "paper", None) is not None
        has_execution_authority = getattr(self.data_provider, "execution_authority", False)
        non_live = has_paper_broker or not has_execution_authority
        rl_learning_allowed = non_live and (
            paper_mode
            or self.rl_offline_updates_enabled
            or self._research_influence_allowed("rl", paper_mode=paper_mode)
        )
        if self.enable_rl and self.rl_trainer and rl_learning_allowed:
            try:
                rl_market_state = self._build_rl_state(toon)
                rl_suggested_action, rl_was_exploration = self.rl_trainer.select_action(
                    rl_market_state, explore=True
                )
                log.info(f"RL Suggestion: {rl_suggested_action} ({'exploration' if rl_was_exploration else 'exploitation'})")
                
                rl_metrics = {
                    "q_table_size": len(self.rl_trainer.q_table),
                    "epsilon": self.rl_trainer.epsilon,
                    "updates": self.rl_trainer.total_updates,
                    "win_rate": win_rate
                }
                
                toon["rl_suggestion"] = {
                    "action": rl_suggested_action,
                    "exploration": rl_was_exploration,
                    "q_table_size": len(self.rl_trainer.q_table)
                }
                # GAP-11: bridge discrete RL action → continuous execution params
                bridged = bridge_rl_action(rl_suggested_action, toon=toon)
                toon["rl_bridged_params"] = bridged.as_dict()
                rl_metrics["bridged_action"] = bridged.action
                rl_metrics["bridged_entry"] = bridged.entry
            except Exception as e:
                log.warning(f"RL state building failed: {e}")
        
        # Advisory quantitative survival/edge evidence (EV, risk-of-ruin bound,
        # quarter-Kelly, run-theory L_max, Hurst) is refreshed once per
        # completed candle from the durable journal so the decision model
        # reasons over realised statistics.  Advisory only - never a
        # permission path; deterministic gates still bind.
        self._attach_quant_survival_audit(toon)

        # Apply advanced auto-tool outputs (meta-adaptive, self-improvement,
        # evolutionary, cross-market, alpha-discovery, multimodal) into the
        # cycle's decision context.  Advisory only — deterministic gates remain.
        self._apply_advanced_auto_tools(toon, paper_mode=paper_mode)

        # Apply alpha mining enrichment (active alphas, MegaAlpha composite,
        # diversity score, decay alerts) into the decision context.
        self._apply_alpha_mining_enrichment(toon)

        self._v2_seam("cycle.seams.llm_ready")
        # 2. LLM Decision
        log.info("Requesting LLM decision...")
        llm_request_id = ""
        circuit_available, circuit_reason = self.provider_circuit.can_call()
        if not circuit_available:
            self.operations.complete_task(cycle_key, {"action": "HOLD", "reason": circuit_reason})
            return {"action": "HOLD", "reasoning": circuit_reason}, 0.0
        if "no_llm" in self.ablation_modes:
            # Ablation: the LLM component is removed for this measurement run.
            # Settle BEFORE reserving budget — no provider call may be made and
            # the deterministic stack alone defines the baseline behavior.
            _abl_reason = "ablation no_llm: LLM component removed for measurement run"
            self.operations.complete_task(cycle_key, {"action": "HOLD", "reason": _abl_reason})
            return {"action": "HOLD", "reasoning": _abl_reason}, 0.0
        provider, budget = self._llm_provider_and_budget(strategy)
        allowed, llm_request_id = self.operations.reserve_llm(
            strategy=strategy, provider=provider,
            model=self.model_name, trigger_key=f"llm:{cycle_key}", run_id=self.run_id,
            **budget)
        if not allowed:
            self.operations.complete_task(cycle_key, {"action": "HOLD", "reason": llm_request_id})
            return {"action": "HOLD", "reasoning": f"LLM request not permitted: {llm_request_id}"}, 0.0
        # κ Audit: log LLM call start
        if self._audit is not None:
            try:
                from core.compliance_audit import EventType
                self._audit.log(
                    EventType.LLM_CALL,
                    symbol=symbol,
                    cycle_num=cycle_num,
                    agent="ω",
                    payload={"phase": "decision_request", "strategy": strategy},
                )
            except Exception:
                pass

        request_started = time.perf_counter()
        try:
            if getattr(self, "enable_bounded_autonomy", False) and self.bounded_pipeline is not None:
                from core.bounded_autonomy_pipeline import PortfolioState
                prices = [float(bar["close"]) for bar in toon.get("ohlc", []) if "close" in bar]
                acc = toon.get("account", {}) or {}
                equity = float(acc.get("equity", getattr(self.risk, "balance", 0.0)) or 0.0)
                cash = float(acc.get("balance", getattr(self.risk, "balance", 0.0)) or 0.0)
                holdings = {}
                if hasattr(self.client, "get_positions"):
                    try:
                        pos_list = self.client.get_positions() or []
                        for pos in pos_list:
                            s = getattr(pos, "symbol", "")
                            v = float(getattr(pos, "volume", 0.0) or 0.0)
                            ptype = getattr(pos, "type", 0)
                            holdings[s] = holdings.get(s, 0.0) + (v if ptype == 0 else -v)
                    except Exception:
                        pass
                portfolio = PortfolioState(cash=cash, equity=equity, holdings=holdings)
                entry_price = float(toon.get("dom", {}).get("best_ask", prices[-1] if prices else 1.0) or (prices[-1] if prices else 1.0))
                atr_val = toon.get("indicators", {}).get("atr_14", 0.0010)
                if isinstance(atr_val, list) and atr_val:
                    atr_val = atr_val[-1]
                stop_dist = float(atr_val) * 1.5 if atr_val else None

                context = {
                    "strategy": strategy,
                    "timeframe": timeframe,
                    "indicators": toon.get("indicators", {}),
                    "news_count": len(toon.get("news", [])),
                }

                exec_dec = self.bounded_pipeline.step(
                    symbol=symbol,
                    prices=prices,
                    portfolio=portfolio,
                    context=context,
                    market={"market_open": True, "execution_price": entry_price},
                    entry_price=entry_price,
                    stop_distance_points=stop_dist,
                )

                decision = {
                    "action": exec_dec.action,
                    "decision": exec_dec.action,
                    "entry": exec_dec.entry_price,
                    "stop_loss": exec_dec.stop_loss,
                    "take_profit": exec_dec.take_profit,
                    "position_size": exec_dec.quantity,
                    "confidence": (exec_dec.veto_verdict.confidence if exec_dec.veto_verdict else 1.0),
                    "reasoning": (
                        f"[BoundedAutonomy] Proposer: {exec_dec.candidate.action} "
                        f"({exec_dec.candidate.rationale}) | "
                        f"Veto: {'Approved' if (exec_dec.veto_verdict and exec_dec.veto_verdict.approved) else 'Vetoed'} "
                        f"({exec_dec.veto_verdict.rationale if exec_dec.veto_verdict else 'N/A'}) | "
                        f"Shield: {'Approved' if exec_dec.risk_approved else 'Rejected'}"
                    ),
                    "bounded_autonomy": exec_dec.to_dict(),
                }
            else:
                decision = self.get_decision(toon)
            self._guardian_progress("post_llm_decision")
            # GAP-11: fill missing continuous params from RL bridge (advisory only).
            # If Bounded Autonomy is active, Proposition 1 Monotone Exposure forbids RL mutation/origination!
            # A provider outage means there is NO model judgement at all. Letting
            # the RL suggestion stand in for it would open new risk with nothing in
            # the loop, which is exactly what deterministic_hold_fallback forbids.
            # The safety HOLD must stay absolute.
            provider_outage = bool(decision.get("deterministic_fallback"))
            # If LLM is down but an institutional quant workflow passed all mathematical/deterministic
            # safety stages with an EXECUTE decision, promote it so trading doesn't stall on LLM API outage.
            inst_wf = toon.get("institutional_workflow")
            if provider_outage and isinstance(inst_wf, dict) and inst_wf.get("decision") == "EXECUTE" and inst_wf.get("proposal"):
                prop = inst_wf["proposal"]
                decision = {
                    "action": prop.get("side", "HOLD"),
                    "decision": prop.get("side", "HOLD"),
                    "entry": prop.get("entry_price", point_values.get("close")),
                    "stop_loss": prop.get("stop_loss"),
                    "take_profit": prop.get("take_profit"),
                    "confidence": 0.85,
                    "reasoning": f"[AutonomousWorkflow] {inst_wf.get('workflow_name')} generated deterministic signal during LLM outage.",
                    "workflow_directed": True,
                }
                logger.info("⚡ Autonomous institutional workflow proposal promoted during LLM outage: %s %s (%s)", decision["action"], symbol, inst_wf.get("workflow_name"))
                provider_outage = False

            if provider_outage:
                logger.warning(
                    "RL bridge suppressed: LLM providers are down and the decision "
                    "is a deterministic safety HOLD; no new risk may be taken")
            if not getattr(self, "enable_bounded_autonomy", False) and not provider_outage:
                bridged_payload = toon.get("rl_bridged_params") or {}
                if bridged_payload.get("action") in {"BUY", "SELL"}:
                    from rl_action_bridge import BridgedExecutionParams
                    bridged_obj = BridgedExecutionParams.from_dict(bridged_payload)
                    decision = merge_bridge_into_decision(decision, bridged_obj, fill_missing_only=True)
            if llm_request_id:
                trace = getattr(self, "_last_llm_trace", {}) or {}
                usage = getattr(self.llm, "last_usage", {}) or {}
                trace["provider_usage"] = usage
                trace["tokens"], trace["cost"], usage_error = self._normalize_llm_usage(usage)
                trace["usage_status"] = "INVALID" if usage_error else "VALID"
                trace["usage_error"] = usage_error
                # A safety HOLD is not automatically a successful provider
                # call. NO_RESPONSE, PARSE_ERROR and ERROR all mean that the
                # selected model failed to produce a usable decision. Keep
                # the safe HOLD behaviour, but record the failure so durable
                # telemetry and the persistent circuit breaker can learn.
                llm_success, parse_status, llm_error = self._llm_trace_outcome(trace)
                self.operations.finish_llm(
                    llm_request_id, success=llm_success,
                    latency_ms=(time.perf_counter() - request_started) * 1000,
                    prompt_hash=str(trace.get("prompt_hash", "")),
                    response_hash=str(trace.get("response_hash", "")),
                    tokens=trace["tokens"],
                    cost=trace["cost"],
                    attempt_count=int(trace.get("attempts", 0) or 0),
                    parse_status=parse_status or "UNKNOWN",
                    response_chars=int(trace.get("response_chars", 0) or 0),
                    error=llm_error,
                )
                state = self.provider_circuit.record(llm_success, llm_error)
                self.operations.event(
                    "provider", "circuit_health", state, run_id=self.run_id,
                    correlation_id=llm_request_id,
                    severity="INFO" if llm_success else "WARNING",
                )
        except KeyboardInterrupt:
            if llm_request_id:
                try:
                    self.operations.finish_llm(
                        llm_request_id, success=False,
                        latency_ms=(time.perf_counter() - request_started) * 1000,
                        parse_status="INTERRUPTED",
                        error="KeyboardInterrupt",
                    )
                except Exception:
                    pass
            self.operations.fail_task(cycle_key, "Interrupted by user (KeyboardInterrupt)")
            raise
        except Exception as exc:
            if llm_request_id:
                trace = getattr(self, "_last_llm_trace", {}) or {}
                usage = getattr(self.llm, "last_usage", {}) or {}
                error_tokens, error_cost, usage_error = self._normalize_llm_usage(usage)
                self.operations.finish_llm(
                    llm_request_id, success=False,
                    latency_ms=(time.perf_counter() - request_started) * 1000,
                    prompt_hash=str(trace.get("prompt_hash", "")),
                    response_hash=str(trace.get("response_hash", "")),
                    tokens=error_tokens,
                    cost=error_cost,
                    attempt_count=int(trace.get("attempts", 0) or 0),
                    parse_status=str(trace.get("parse_status", "ERROR")),
                    response_chars=int(trace.get("response_chars", 0) or 0),
                    error=(f"{exc}; {usage_error}" if usage_error else str(exc)),
                )
                state = self.provider_circuit.record(False, str(exc))
                self.operations.event("provider", "circuit_health", state, run_id=self.run_id, correlation_id=llm_request_id, severity="WARNING")
            self.operations.fail_task(cycle_key, str(exc))
            raise
        # κ Audit: log LLM decision outcome
        if self._audit is not None:
            try:
                from core.compliance_audit import EventType
                self._audit.log(
                    EventType.LLM_DECISION,
                    symbol=symbol,
                    cycle_num=cycle_num,
                    agent="ω",
                    payload={
                        "action": decision.get("action", "HOLD"),
                        "reasoning": str(decision.get("reasoning", ""))[:200],
                        "strategy": strategy,
                    },
                )
            except Exception:
                pass

        # causal main driver for REPL feed banner (causal graph → decision, no gap)
        _main = toon.get("causal_propagation") if isinstance(toon.get("causal_propagation"), dict) and toon.get("causal_propagation", {}).get("path_id") else toon.get("opportunity_fusion")
        proposal_action = "propose_no_trade" if str(decision.get("action", "HOLD")).upper() == "HOLD" else "propose_trade"
        self._bus_emit("ω", "LLM_DECISION", "proposal_received", {
            "action": decision.get("action", "HOLD"),
            "confidence": decision.get("confidence", 0),
            "strategy": strategy,
            "symbol": symbol,
            "summary": str(decision.get("reasoning", ""))[:300],
            "mainDriver": {"source": (_main or {}).get("source_name") or str((_main or {}).get("path_id") or ""), "effect": str((_main or {}).get("target_name") or (_main or {}).get("terminal") or symbol), "strength": str((_main or {}).get("confidence") or (_main or {}).get("score") or ""), "tier": str((_main or {}).get("status") or "PRIMARY"), "decay": str(round(float((_main or {}).get("confidence") or 0.8)*100)) if str((_main or {}).get("confidence") or "").replace(".","",1).isdigit() else "85", "bias": str(decision.get("action", "HOLD")).upper(), "path": str((_main or {}).get("path_id") or (_main or {}).get("relation") or "")},
            "causal_main_driver": _main,
        }, cycle_num, symbol)
        try:
            from operations.event_bridge import emit as _eb_emit
            _eb_emit("LLM_DECISION", {"side": decision.get("action", "HOLD"), "symbol": symbol, "summary": str(decision.get("reasoning", ""))[:300], "mainDriver": {"source": (_main or {}).get("source_name") or str((_main or {}).get("path_id") or ""), "effect": str((_main or {}).get("target_name") or symbol)}, "confidence": decision.get("confidence", 0)}, correlation_id=str(cycle_key), actor="MarketModel")
        except Exception:
            pass
        proposal_artifact = "no_trade_proposal" if proposal_action == "propose_no_trade" else "trade_proposal"
        self.operations.agent_event("MarketModel", proposal_action, proposal_artifact,
                                    "proposal_received", {"action": decision.get("action"), "confidence": decision.get("confidence")},
                                    run_id=self.run_id, correlation_id=cycle_key)
        try:
            from local_agent_memory import maybe_remember_trade_cycle
            maybe_remember_trade_cycle(
                cycle_key=str(cycle_key),
                stage="proposal",
                payload={
                    "action": decision.get("action"),
                    "confidence": decision.get("confidence"),
                    "symbol": symbol,
                    "timeframe": timeframe,
                },
                symbol=str(symbol or ""),
                side=str(decision.get("action", "") or ""),
                run_id=self.run_id or "",
                memory_links={"L0": "proposal_received"},
            )
        except Exception as exc:
            logging.getLogger("LLMOrchestrator").debug("Proposal received event failed: %s", exc)
        decision_snapshot_id = self.operations.store_snapshot("proposal_output", decision, run_id=self.run_id,
                                                              symbol=symbol, timeframe=timeframe, candle_id=str(closed_bar))
        decision["journal_context"] = {**toon.get("journal_context", {}), "decision_snapshot_id": decision_snapshot_id}
        self.operations.complete_task(cycle_key, {"action": decision.get("action", "HOLD")})
        
        # Update Decision Panel
        if display:
            display.update_decision(decision)
            
        action = decision.get("action", "HOLD").upper()
        
        # Display Action After Decision
        details = f"Action: {action}"
        if action != "HOLD":
            details += f"\nEntry: {decision.get('entry')}\nSL: {decision.get('stop_loss')}\nTP: {decision.get('take_profit')}"
        else:
            details += f"\nReason: {decision.get('reasoning', 'No signal')}"
            
        if display:
            display.update_footer(f"Status: {action} | Win Rate: {win_rate:.1%} | PnL: ${sum(t['pnl'] for t in closed_trades):.2f}")
            
        # LLM conditional_scenarios stay advisory. Never arm executable closes.
        if isinstance(decision, dict):
            self._record_advisory_conditional_scenarios(
                decision, symbol=symbol, strategy=strategy, cycle_key=cycle_key,
            )

        # Log canonical decision for observability
        log.info("⚡ [DECISION] Cycle %s: %s %s (%s) -> ACTION: %s | %s",
                 cycle_num, symbol, timeframe, strategy, action, str(decision.get('reasoning', ''))[:200])

        # Colorful CLI Output (Independent of TradeManager logic)
        # Ensure we don't display if TUI is active and suppressing stdout
        if not display: 
            color = "green" if action == "BUY" else "red" if action == "SELL" else "yellow"
            
            # 1. Header & Account Info
            acc = toon.get("account", {})
            dom = toon.get("dom", {})
            open_positions = positions_overview() if not paper_mode else toon.get("open_positions", [])
            pos_text = "\n".join([f"{p['symbol']} {p.get('type_name', p.get('type', p.get('side', '')))} {p['volume']} (PnL: {float(p.get('profit', 0.0)):.2f})" for p in open_positions]) or "No Open Positions"
            
            # Mission Control Progress
            filled_blocks = min(10, max(0, int(round((cycle_num / max(1, total_cycles)) * 10))))
            progress_bar = "█" * filled_blocks + "░" * (10 - filled_blocks)
            
            header_text = f"""[bold]Mission:[/bold] Cycle {cycle_num}/{total_cycles} [{progress_bar}]
[bold]Market:[/bold] Bid: {dom.get('best_bid', 0):.5f} | Ask: {dom.get('best_ask', 0):.5f} | Spread: {dom.get('spread', 0):.5f}
[bold]Account:[/bold] ${acc.get('balance', 0):.2f} (Eq: ${acc.get('equity', 0):.2f})
[bold]Open Trades:[/bold] {len(open_positions)}
{pos_text}"""
            print_bordered(None, f"💼 Account & Positions - {symbol}", header_text, style="blue")

            # 2. Reasoning & Confluence
            from rich.markup import escape
            reasoning = escape(str(decision.get('reasoning', 'N/A')))
            r_text = f"[bold]LLM Logic:[/bold]\n{reasoning}\n\n"
            
            # News Headlines
            if toon.get("news"):
                r_text += "[bold]Top News:[/bold]\n"
                for n in toon["news"][:3]: # Show top 3
                    title = escape(str(n.get('title', 'N/A')))
                    r_text += f"• {title}\n"
                r_text += "\n"
            
            if toon.get("technical_analysis", {}).get("confluence"):
                conf = toon["technical_analysis"]["confluence"]
                r_text += f"[bold]Confluence Analysis:[/bold]\nScore: {conf.get('score')}/{conf.get('max_score')}\n"
                r_text += f"News Bonus: +{conf['breakdown'].get('news_bonus', 0)} | Volume Bonus: +{conf['breakdown'].get('volume_bonus', 0)}"

            print_bordered(None, f"🧠 Cycle {cycle_num} Logic", r_text, style="cyan")
            
            # 2.5 Multi-Timeframe Analysis (NEW)
            mtf_text = ""
            mtf_data = toon.get("multi_timeframe", {})
            for tf, data in mtf_data.items():
                trend = data.get("trend", "NEUTRAL")
                rsi = data.get("rsi", 50.0)
                t_color = "green" if trend == "BULLISH" else "red" if trend == "BEARISH" else "white"
                mtf_text += f"{tf}: [{t_color}]{trend}[/{t_color}] (RSI: {rsi:.1f})\n"
            
            if mtf_text:
                print_bordered(None, f"🕰️ Multi-Timeframe Analysis", mtf_text.strip(), style="yellow")
            
            # 2.6 News & Events Panel (NEW)
            news_text = ""
            if toon.get("news"):
                for n in toon["news"][:3]:
                    news_text += f"• {n.get('time', '')[:16]} {n.get('event', 'N/A')} ({n.get('importance', '')})\n"
            if toon.get("hot_topics"):
                news_text += "\n[bold]Hot Topics:[/bold]\n"
                for t in toon["hot_topics"][:3]:
                    title = escape(str(t.get('title', '')))
                    news_text += f"• {title} ({t.get('sentiment', 'NEUTRAL')})\n"
            
            if news_text:
                 print_bordered(None, f"📰 News & Economic Events", news_text.strip(), style="white")

            # 4. Trade Decision & Planning
            decision_text = f"ACTION: {action}"
            if action != "HOLD":
                decision_text += f"\nEntry: {decision.get('entry', 0):.5f}\nSL: {decision.get('stop_loss', 0):.5f}\nTP: {decision.get('take_profit', 0):.5f}"
            
            decision_text += f"\n\n[bold]Current Condition:[/bold] {decision.get('waiting_for', 'Monitoring price action')}"
            
            # Show Conditional Scenarios (The "Plan")
            scenarios = decision.get("conditional_scenarios", [])
            if scenarios:
                decision_text += "\n\n[bold]Active Plans (Watchlist):[/bold]"
                for s in scenarios:
                    s_cond = s.get('condition', 'N/A')
                    s_act = s.get('action', 'ACTION')
                    decision_text += f"\n• IF {s_cond} THEN {s_act}"
            else:
                 decision_text += "\n\n(No specific conditional orders set this cycle)"

            # Color-coded border for decision
            print_bordered(None, "⚡ Trade Decision & Planning", decision_text, style=color)

            # 4. Systems & RL Metrics
            # Calculate descriptive metrics from durable closed-trade history;
            # never display placeholder zeroes when observations exist.
            sharpe = self.perf.calc_rolling_sharpe(window=20)
            sortino = self.perf.calc_rolling_sortino(window=20)
                
            rl_text = f"""[bold]RL State:[/bold]
Action: {rl_suggested_action or 'N/A'} ({'Exploration' if rl_was_exploration else 'Exploitation'})
Confidence: {rl_metrics.get('confidence', 'N/A')}
Epsilon: {(self.rl_trainer.epsilon if self.rl_trainer else 0.0):.4f} | Q-Table: {(len(self.rl_trainer.q_table) if self.rl_trainer else 0)}

[bold]Performance:[/bold]
Win Rate (20): {win_rate:.1%} | Profit Factor: {profit_factor:.2f}
Rolling Sharpe: {sharpe:.3f} | Rolling Sortino: {sortino:.3f}
Total PnL: ${sum(t['pnl'] for t in closed_trades):.2f} | Max DD: ${max_dd:.2f}
Total Trades: {len(closed_trades)}"""
            print_bordered(None, f"🤖 System Intelligence", rl_text, style="magenta")

            # End of Cycle Border
            print_bordered(None, f"🏁 CYCLE {cycle_num} COMPLETE", "Waiting for next interval...", style="bold white")

        # === DASHBOARD UPDATES ===
        # Moved out of the CLI-only branch: these updates were previously
        # nested under ``if not display:`` and therefore unreachable when a
        # TUI/display instance was active.
        if display:
            # 1. Update RL Stats
            if self.enable_rl and self.rl_trainer:
                display.update_rl_stats(self.rl_trainer)

            # 2. Update Decision (Reasoning & Scenarios)
            decision_payload = decision.copy()
            decision_payload["reasoning"] = decision.get("reasoning", "No reasoning provided.")
            decision_payload["confidence"] = decision.get("confidence", 0.0)
            display.update_decision(decision_payload)

            # 3. Update Narrative
            narrative = f"Action: {action} on {symbol} | Confidence: {decision.get('confidence', 0):.2f}"
            if action == "HOLD":
                narrative += f" | Waiting for: {decision.get('waiting_for', 'setup')}"
            display.state.active_narrative = narrative

        
        self._v2_seam("cycle.seams.decision_ready")
        safe_vol = 0.0
        
        if action in ["BUY", "SELL"]:
            proposal, validation = self._validate_proposal(decision, toon, symbol, strategy, paper_mode=paper_mode)
            try:
                from operations.event_bridge import emit as _emit
                from operations.supervisor import get_supervisor as _get_sup

                _sup_dv = _get_sup()
                _sup_dv.note_principal("MarketModel", ok=True, detail="decision proposed")
                _emit("DECISION_PROPOSED", {
                    "symbol": symbol, "strategy": strategy, "side": action,
                    "confidence": decision.get("confidence"),
                }, actor="MarketModel")
                _approved = bool(proposal and validation.approved)
                self._bus_emit("ρ", "RISK_CHECK", "validated", {
                    "approved": _approved,
                    "reasons": [str(r) for r in getattr(validation, "reasons", [])][:3],
                }, cycle_num, symbol)
                _sup_dv.note_principal("RiskController", ok=_approved,
                                       detail="validated" if _approved else "rejected")
                _emit("VALIDATION_RESULT", {
                    "symbol": symbol,
                    "approved": _approved,
                    "reasons": [str(r) for r in getattr(validation, "reasons", [])][:5],
                }, actor="RiskController")
                try:
                    self._emit_validation_step(
                        stage="deterministic_validator",
                        status="APPROVED" if _approved else "REJECTED",
                        metrics={
                            "symbol": symbol,
                            "confidence": decision.get("confidence", 0.0),
                            "setup_key": proposal.setup_key() if proposal else None,
                        },
                        notes="; ".join(getattr(validation, "reasons", [])[:3]) or "",
                    )
                except Exception as _val_emit_exc:
                    log.debug("Validation step emit skipped: %s", _val_emit_exc)
            except Exception as exc:
                logging.getLogger("LLMOrchestrator").debug("Risk controller event failed: %s", exc)
            if not proposal or not validation.approved:
                reason = "; ".join(validation.reasons)
                log.warning("Deterministic validator rejected %s %s: %s", action, symbol, reason)
                decision["action"] = "HOLD"
                decision["reasoning"] = f"Deterministic gate rejected proposal: {reason}"
                return decision, 0.0
            if not self.risk.check_cooldown(symbol):
                log.warning("Cooldown active. Skipping trade.")
                self.operations.event("risk", "skip_cooldown",
                                      {"symbol": symbol, "strategy": strategy},
                                      run_id=self.run_id, severity="INFO")
                return decision, 0.0
            
            if not self.risk.check_rate_limit():
                log.warning("Rate limit reached. Skipping trade.")
                self.operations.event("risk", "skip_rate_limit",
                                      {"symbol": symbol, "strategy": strategy},
                                      run_id=self.run_id, severity="INFO")
                return decision, 0.0
            
            if not self.risk.check_active_trade_limit():
                log.warning("Max active trades limit reached. Skipping trade.")
                self.operations.event("risk", "skip_active_trade_limit",
                                      {"symbol": symbol, "strategy": strategy},
                                      run_id=self.run_id, severity="INFO")
                return decision, 0.0
            if hasattr(self.risk, "check_consecutive_loss_breaker") and not self.risk.check_consecutive_loss_breaker():
                log.warning(
                    "Consecutive loss circuit breaker active (%s/%s). Skipping trade.",
                    getattr(self.risk, "consecutive_losses", "?"),
                    getattr(self.risk, "max_consecutive_losses", "?"),
                )
                self.operations.event("risk", "skip_consecutive_loss_breaker",
                                      {"symbol": symbol, "strategy": strategy,
                                       "consecutive_losses": getattr(self.risk, "consecutive_losses", None)},
                                      run_id=self.run_id, severity="WARNING")
                return decision, 0.0
                
            self._v2_seam("cycle.seams.validated")
            # Recalculate size based on strict risk
            sl = decision.get("stop_loss")
            entry = decision.get("entry") or (toon["dom"].get("best_ask") if action == "BUY" else toon["dom"].get("best_bid"))
            
            if sl and entry:
                # Extract alpha ensemble composite for quantitative sizing
                _alpha_ens = toon.get("indicators", {}).get("alpha_ensemble", {})
                _alpha_composite = float(_alpha_ens.get("composite_value", 0.0))
                _alpha_ic = float(_alpha_ens.get("overall_ic", 0.0))
                _alpha_conf = str(_alpha_ens.get("composite_confidence", "unknown"))

                safe_vol = self.risk.calc_position_size(
                    symbol, entry, sl,
                    confidence=self._sizing_confidence(decision),
                    alpha_composite=_alpha_composite,
                    trade_direction=action,
                    alpha_overall_ic=_alpha_ic,
                    alpha_confidence=_alpha_conf,
                )
                
                # Cap position size
                MAX_FOREX_LOT = 1.0
                MAX_GOLD_LOT = 0.1
                max_lot = MAX_GOLD_LOT if ("XAU" in symbol or "GOLD" in symbol) else MAX_FOREX_LOT
                
                if safe_vol > max_lot:
                    log.warning(f"Position size {safe_vol} exceeds max {max_lot}, capping to {max_lot}")
                    safe_vol = max_lot

                if getattr(self, "enable_bounded_autonomy", False):
                    bounded_size = float(decision.get("position_size", 0.0) or 0.0)
                    if bounded_size > 0.0:
                        safe_vol = min(safe_vol, bounded_size)
                
                if safe_vol <= 0:
                    # A zero volume must be an auditable rejection, never a
                    # silent drop: sizing can refuse for daily caps, the
                    # confidence floor, circuit breakers, or minimum-lot rules.
                    reason = ("position sizing returned zero volume "
                              "(daily cap, confidence floor, circuit breaker, or minimum-lot refusal)")
                    self.store.log_lifecycle_event(None, proposal.setup_key(), "REJECTED", "risk_sizing", {
                        "reason": reason, "symbol": symbol, "side": action,
                        "entry": entry, "stop_loss": sl,
                    })
                    self.operations.event("risk", "zero_volume_rejected",
                                          {"symbol": symbol, "strategy": strategy, "reason": reason},
                                          run_id=self.run_id, correlation_id=proposal.setup_key(),
                                          severity="WARNING")
                    decision["action"] = "HOLD"
                    decision["reasoning"] = f"Deterministic gate rejected proposal: {reason}"
                    log.warning(reason)
                    return decision, 0.0

                log.info(f"Calculated safe volume: {safe_vol:.2f} (LLM suggested: {decision.get('position_size')})")
                self._bus_emit("ρ", "POSITION_SIZE", "sized", {
                    "volume": safe_vol, "entry": entry, "sl": sl,
                    "alpha_composite": _alpha_composite,
                }, cycle_num, symbol)
                
                if safe_vol > 0:
                    equity = float(toon.get("account", {}).get("equity", self.risk.balance))
                    if self._research_influence_allowed("kelly", paper_mode=paper_mode):
                        risk_fraction = self.growth_controller.effective_risk_fraction(self.risk.risk_per_trade)
                    else:
                        # Kelly/growth output is descriptive until the
                        # journal sample, OOS evidence, and operator approval
                        # are all present. Base risk remains deterministic.
                        risk_fraction = float(self.risk.risk_per_trade)
                        toon["growth_influence"] = {
                            "allowed": False,
                            "reason": "Kelly/growth influence gate is not approved",
                            "execution_authority": False,
                        }
                    proposed_risk = equity * risk_fraction
                    primary_bucket = sorted(exposure_buckets(symbol, action))[0]
                    correlation_assessment = self.correlation_risk.correlated_open_risk(self.store, symbol, action, timeframe)
                    toon["correlation_assessment"] = correlation_assessment
                    toon["account_growth_risk_fraction"] = risk_fraction
                    permitted, permit_reason = self.arbiter.permit(
                        equity, proposed_risk, bucket=primary_bucket,
                        correlated_risk=float(correlation_assessment["correlated_open_risk"]),
                    )
                    if not permitted:
                        log.warning("Portfolio risk arbiter rejected %s %s: %s", action, symbol, permit_reason)
                        return decision, 0.0
                    # P8: portfolio allocation cap — deterministic, reduction-only
                    # (regime allocation + strategy DD + rho>0.7 overlap). Runs
                    # after arbiter permit, before the execution plan; a cap to
                    # zero is an auditable rejection like zero-volume sizing.
                    try:
                        _alloc = self.apply_portfolio_allocation_cap(
                            symbol, strategy, action, safe_vol, toon, timeframe)
                        _capped = float(_alloc.get("capped_volume", safe_vol))
                        if _alloc.get("constraints"):
                            toon["portfolio_allocation"] = _alloc
                            if _capped <= 0:
                                reason = "portfolio allocation cap zeroed volume: " + \
                                    "; ".join(_alloc["constraints"][:3])
                                self.store.log_lifecycle_event(None, proposal.setup_key(), "REJECTED", "portfolio_allocation", {
                                    "reason": reason, "symbol": symbol, "side": action,
                                    "constraints": _alloc["constraints"],
                                })
                                self.operations.event("PortfolioAgent", "zero_volume_rejected",
                                                      {"symbol": symbol, "strategy": strategy,
                                                       "reason": reason},
                                                      run_id=self.run_id, severity="WARNING")
                                decision["action"] = "HOLD"
                                decision["reasoning"] = f"Deterministic gate rejected proposal: {reason}"
                                log.warning(reason)
                                return decision, 0.0
                            if _capped < safe_vol:
                                log.info(
                                    "P8 allocation cap: %.4f -> %.4f (%s)",
                                    safe_vol, _capped, "; ".join(_alloc["constraints"][:2]))
                                safe_vol = _capped
                    except Exception as _alloc_cap_exc:
                        logging.getLogger("LLMOrchestrator").debug(
                            "P8 allocation cap call failed open: %s", _alloc_cap_exc)
                    try:
                        execution_plan, margin_preflight = self._build_execution_plan(proposal, toon, paper_mode, safe_vol)
                    except ValueError as exc:
                        reason = f"pre-submit execution plan rejected: {exc}"
                        self.store.log_lifecycle_event(None, proposal.setup_key(), "REJECTED", "execution_gate", {"reason": reason})
                        self.operations.event("execution", "pre_submit_rejected", {"reason": reason}, run_id=self.run_id, severity="WARNING")
                        log.warning(reason)
                        return {"action": "HOLD", "reasoning": reason}, 0.0
                    if not paper_mode:
                        if mt5 is not None:
                            margin_assessment = self.margin_safety.assess_from_mt5(
                                mt5_module=mt5,
                                symbol=symbol,
                                side=action,
                                volume=safe_vol,
                                price=float(execution_plan.entry),
                                new_risk=proposed_risk,
                                driver_buckets=exposure_buckets(symbol, action),
                                position_margin_estimates=margin_preflight.get("position_margin_estimates", []),
                            )
                        else:
                            margin_assessment = self.margin_safety.assess(
                                account=margin_preflight.get("account_snapshot", {}),
                                new_margin=margin_preflight.get("estimated_margin"),
                                new_risk=proposed_risk,
                                symbol=symbol,
                                driver_buckets=exposure_buckets(symbol, action),
                                position_margin_estimates=margin_preflight.get("position_margin_estimates", []),
                                mode=margin_preflight.get("mode", "unknown"),
                            )
                        margin_preflight["safety_assessment"] = margin_assessment.as_dict()
                        if not margin_assessment.approved:
                            reason = "margin safety gate rejected: " + "; ".join(margin_assessment.reasons)
                            self.store.log_lifecycle_event(None, proposal.setup_key(), "REJECTED", "margin_safety", {
                                "reason": reason, "assessment": margin_assessment.as_dict(),
                            })
                            self.operations.event("risk", "margin_safety_rejected", {
                                "symbol": symbol, "strategy": strategy, "assessment": margin_assessment.as_dict(),
                            }, run_id=self.run_id, correlation_id=proposal.setup_key(), severity="WARNING")
                            log.warning(reason)
                            return {"action": "HOLD", "reasoning": reason}, 0.0
                    cost_assessment = margin_preflight.get("cost_assessment", {})
                    if not paper_mode and not cost_assessment.get("approved", False):
                        reason = "cost-aware net-RR gate rejected: " + "; ".join(cost_assessment.get("reasons", ["cost estimate unavailable"]))
                        self.store.log_lifecycle_event(None, proposal.setup_key(), "REJECTED", "cost_safety", {
                            "reason": reason, "cost_assessment": cost_assessment,
                        })
                        self.operations.event("risk", "cost_safety_rejected", {
                            "symbol": symbol, "strategy": strategy, "cost_assessment": cost_assessment,
                        }, run_id=self.run_id, correlation_id=proposal.setup_key(), severity="WARNING")
                        log.warning(reason)
                        return {"action": "HOLD", "reasoning": reason}, 0.0
                    self._v2_seam("cycle.seams.sized")
                    intent_key = f"{proposal.setup_key()}:{proposal.magic}"
                    created, _intent = self.operations.create_order_intent(intent_key, {
                        "symbol": symbol, "side": action, "entry": entry, "volume": safe_vol,
                        "stop_loss": decision.get("stop_loss"), "take_profit": decision.get("take_profit"),
                        "magic": proposal.magic, "paper": paper_mode,
                        "setup_key": proposal.setup_key(), "strategy": proposal.strategy,
                        "risk_amount": proposed_risk,
                        "rl_state": rl_market_state,
                        "rl_action": action,
                        "correlation_assessment": correlation_assessment,
                        "memory_retrieval_ids": toon.get("memory_retrieval_ids", {}),
                        "order_type": execution_plan.order_type, "expires_at": execution_plan.expires_at,
                        "pre_submit_quote": execution_plan.as_dict(),
                        "margin_preflight": margin_preflight,
                    }, run_id=self.run_id, actor="RiskController")
                    if not created:
                        log.warning("Duplicate order intent blocked: %s", intent_key)
                        return {"action": "HOLD", "reasoning": "Duplicate order intent blocked"}, 0.0
                    self.operations.record_raw_trace(
                        "mt5_order_request",
                        {"intent_key": intent_key, "symbol": symbol, "side": action, "entry": entry,
                         "volume": safe_vol, "stop_loss": decision.get("stop_loss"),
                         "take_profit": decision.get("take_profit"), "magic": proposal.magic},
                        run_id=self.run_id, correlation_id=intent_key,
                    )
                    self.store.log_lifecycle_event(None, proposal.setup_key(), "SUBMITTED", "execution", {
                        "intent_key": intent_key, "symbol": symbol, "side": action,
                        "volume": safe_vol, "planned_entry": entry,
                        "order_type": execution_plan.order_type, "expires_at": execution_plan.expires_at,
                    })
                    # Display Execution
                    if display:
                        display.update_execution_log([f"EXECUTING {action} {safe_vol} @ {entry}"])
                    
                    decision["metrics"] = {
                        "decision_latency": 0.0,
                        "total_latency": time.time() - start_time,
                        "expected_price": entry,
                        "rl_feedback": "PENDING"
                    }
                    
                    if self.enable_rl and rl_market_state:
                        decision["rl_state"] = rl_market_state
                        decision["rl_action"] = action
                        self.rl_state_history.append({
                            "state": rl_market_state,
                            "action": action,
                            "symbol": symbol,
                            "timestamp": time.time()
                        })
                        if len(self.rl_state_history) > 1000:
                            self.rl_state_history = self.rl_state_history[-1000:]

                    # EXECUTE TRADE
                    try:
                        # Extract ATR for validation
                        atr_val = toon["indicators"].get("atr_14", 0.0010)
                        if isinstance(atr_val, list): atr_val = atr_val[-1]

                        # --- UrgencySOR pre-execution routing ---
                        _use_urgency = False
                        _urgency_score = 0.5
                        try:
                            from core.urgency_sor import calculate_alpha_decay_urgency_score
                            _tick = _symbol_tick(symbol)
                            if _tick and _tick.bid and _tick.ask:
                                _mid = (_tick.bid + _tick.ask) / 2.0
                                _spread_pct = abs(_tick.ask - _tick.bid) / _mid if _mid else 0.001
                                _urgency_score = calculate_alpha_decay_urgency_score(
                                    instantaneous_volatility=_spread_pct,
                                    order_flow_imbalance=float(getattr(_tick, "volume", 1) or 1),
                                    order_book_depth=100.0,
                                )
                                _use_urgency = True
                                log.info("UrgencySOR score=%.3f for %s %s", _urgency_score, symbol, action)
                        except Exception as _urg_exc:
                            log.debug("UrgencySOR pre-route skipped: %s", _urg_exc)

                        # Route via UrgencySOR if available and order is market-like
                        if _use_urgency and execution_plan.order_type.upper() in ("MARKET", "BUY", "SELL"):
                            res = place_urgency_routed_order(
                                symbol=symbol, side=action, volume=safe_vol,
                                sl=decision.get("stop_loss"), tp=decision.get("take_profit"),
                                comment="llm_auto",
                                paper=self.paper_broker if paper_mode else None,
                                atr=atr_val, magic=proposal.magic,
                            )
                        else:
                            expires_at = datetime.fromisoformat(execution_plan.expires_at)
                            log.info("Executing planned %s %s order at %s; expires %s", execution_plan.order_type, action, entry, execution_plan.expires_at)
                            res = self.data_provider.place_pending_order(
                                symbol=symbol,
                                otype=execution_plan.order_type,
                                price=execution_plan.entry,
                                volume=safe_vol,
                                sl=decision.get("stop_loss"),
                                tp=decision.get("take_profit"),
                                comment="llm_auto",
                                paper=self.paper_broker if paper_mode else None,
                                magic=proposal.magic,
                                expires_at=expires_at,
                            )
                        self.operations.record_raw_trace(
                            "mt5_order_response", {"intent_key": intent_key, "result": res or {}},
                            run_id=self.run_id, correlation_id=intent_key,
                        )
                        # κ Audit: log order submission
                        if self._audit is not None:
                            try:
                                from core.compliance_audit import EventType
                                self._audit.log(
                                    EventType.ORDER_SUBMITTED,
                                    symbol=symbol,
                                    cycle_num=cycle_num,
                                    agent="ε",
                                    payload={
                                        "side": action, "volume": safe_vol,
                                        "entry": entry, "sl": decision.get("stop_loss"),
                                        "tp": decision.get("take_profit"),
                                        "order_type": execution_plan.order_type,
                                        "ticket": int(res.get("order", 0)) if res else 0,
                                        "paper": paper_mode,
                                    },
                                )
                            except Exception as audit_exc:
                                logging.getLogger("main").error("Failed to write compliance audit log: %s", audit_exc)
                                self._bus_emit("κ", "AUDIT_LOG_ERROR", "audit_log_failed", {"error": str(audit_exc)[:300]})

                        # Update Dashboard with latest positions (Live or Paper)
                        if display and self.data_provider.trade_manager:
                            # We need a unified way to get positions. 
                            # For now, we try to access trade_manager's internal list for paper, or MT5 for live.
                            all_positions = []
                            if paper_mode:
                                all_positions = self.data_provider.trade_manager.positions
                            else:
                                all_positions = positions_overview()
                            
                            display.update_positions(all_positions)
                            display.update_history(self.data_provider.trade_manager.history)
                        if res and res.get("order"):
                             self._bus_emit("ε", "ORDER_SUBMIT", "order_placed", {
                                 "ticket": int(res["order"]), "type": execution_plan.order_type,
                                 "side": action, "volume": safe_vol, "entry": entry,
                             }, cycle_num, symbol)
                             # A placed pending order is not a filled position.
                             # Reconciliation owns the later PENDING->FILLED/CANCELLED/EXPIRED transition.
                             self.operations.resolve_order_intent(intent_key, "PENDING", ticket=int(res["order"]), result=res)
                             self.store.transition_latest_setup(proposal.setup_key(), "ACCEPTED", ticket=int(res["order"]), reason="broker accepted pending order")
                             self.store.record_execution_report(
                                 event_type="ACCEPTED", intent_key=intent_key, order_ticket=int(res["order"]),
                                 retcode=res.get("retcode"), requested_price=execution_plan.entry,
                                 accepted_price=res.get("accepted_price"), requested_volume=safe_vol,
                                 accepted_volume=res.get("accepted_volume"),
                                 execution_latency_ms=max(0.0, time.time() * 1000 - float(margin_preflight.get("submitted_at_msc", time.time() * 1000))),
                                 spread_at_submit=margin_preflight.get("spread_at_submit"),
                                 implementation_shortfall=(
                                     ((float(res.get("accepted_price")) - float(execution_plan.entry)) if proposal.side == "BUY" else
                                      (float(execution_plan.entry) - float(res.get("accepted_price"))))
                                     if res.get("accepted_price") is not None else None
                                 ),
                                 requote_count=1 if int(res.get("retcode", 0) or 0) in {
                                     int(getattr(mt5, "TRADE_RETCODE_REQUOTE", -1)),
                                     int(getattr(mt5, "TRADE_RETCODE_PRICE_CHANGED", -2)),
                                 } else 0,
                                 payload={"response": res, "order_type": execution_plan.order_type,
                                          "expires_at": execution_plan.expires_at},
                             )
                             self.store.log_lifecycle_event(int(res["order"]), proposal.setup_key(), "ACCEPTED", "broker", {
                                 "intent_key": intent_key, "broker_result": res, "expires_at": execution_plan.expires_at,
                             })
                             self.store.log_lifecycle_event(int(res["order"]), proposal.setup_key(), "PENDING", "broker", {
                                 "intent_key": intent_key, "order_type": execution_plan.order_type,
                                 "expires_at": execution_plan.expires_at,
                             })
                             # Start cooldown/rate accounting only after the
                             # broker (or explicit paper broker) accepted the
                             # pending order. The state is journal-backed via
                             # RiskManager.set_store().
                             self.risk.register_trade(symbol)
                             log.info("Planned order accepted: Ticket %s", res["order"])
                             if display: display.update_execution_log([f"✅ PENDING: {res['order']}"])
                        else:
                             self.operations.resolve_order_intent(intent_key, "REJECTED", result=res or {}, error="broker returned no order ticket")
                             self.store.transition_latest_setup(proposal.setup_key(), "MISSED", reason="broker returned no order ticket")
                             self.store.record_execution_report(event_type="REJECTED", intent_key=intent_key,
                                                                retcode=(res or {}).get("retcode"), requested_price=execution_plan.entry,
                                                                requested_volume=safe_vol, payload={"response": res or {}})
                             self.store.log_lifecycle_event(None, proposal.setup_key(), "REJECTED", "execution", {"intent_key": intent_key, "broker_result": res or {}})
                    except Exception as e:
                        self.operations.record_raw_trace(
                            "mt5_order_error", {"intent_key": intent_key, "error": str(e)},
                            run_id=self.run_id, correlation_id=intent_key,
                        )
                        self.operations.resolve_order_intent(intent_key, "ERROR", error=str(e))
                        self.store.transition_latest_setup(proposal.setup_key(), "MISSED", reason=str(e))
                        self.store.record_execution_report(event_type="ERROR", intent_key=intent_key,
                                                           requested_price=execution_plan.entry, requested_volume=safe_vol,
                                                           payload={"error": str(e)})
                        self.store.log_lifecycle_event(None, proposal.setup_key(), "ERROR", "execution", {"intent_key": intent_key, "error": str(e)})
                        log.error(f"Order Execution Failed: {e}")
                        if display: display.update_execution_log([f"❌ FAILED: {str(e)}"])
            else:
                # An approved BUY/SELL without a usable stop/entry must be a
                # journalled rejection, not a silent no-op.
                reason = "approved proposal missing stop_loss or entry price; nothing was sized or submitted"
                self.store.log_lifecycle_event(None, proposal.setup_key(), "REJECTED", "execution_gate", {
                    "reason": reason, "symbol": symbol, "side": action,
                })
                self.operations.event("execution", "proposal_incomplete",
                                      {"symbol": symbol, "strategy": strategy,
                                       "action": action, "reason": reason},
                                      run_id=self.run_id, correlation_id=proposal.setup_key(),
                                      severity="WARNING")
                decision["action"] = "HOLD"
                decision["reasoning"] = f"Deterministic gate rejected proposal: {reason}"
                log.warning(reason)

        # 3. LLM management suggestions stay advisory. Deterministic
        # TradeManager.monitor_positions (+ pulse tick path) owns all SL /
        # trail / partial / close actions. Never call execute_* from LLM JSON.
        if isinstance(decision, dict):
            self._record_advisory_management_suggestions(
                decision, symbol=symbol, strategy=strategy, cycle_key=cycle_key,
            )
            
            # Continue with automated monitoring (trailing stops, etc.) as safety net
            # ...
        
        if self.data_provider.trade_manager:
            atr = toon["indicators"].get("atr_14", 0.0010)
            if isinstance(atr, list): atr = atr[-1]
            
            current_price = toon["ohlc"][-1]["close"] if toon["ohlc"] else 0.0
            log.info(f"Managing positions for {symbol}...")
            self.data_provider.trade_manager.monitor_positions(symbol, current_price, atr, indicators=toon.get("indicators"))

        # Broker-quote position marks make MFE/MAE research possible without
        # allowing memory/LLM output to manage positions.  Stale marks remain
        # journalled for operations evidence but do not qualify excursions.
        if not paper_mode and mt5:
            for position in positions_overview():
                if position["symbol"] != symbol or self.store.trade_risk(int(position["ticket"])) <= 0:
                    continue
                is_buy = position["type"] == getattr(mt5, "POSITION_TYPE_BUY", getattr(mt5, "ORDER_TYPE_BUY", 0))
                tick = mt5.symbol_info_tick(position["symbol"])
                mark = getattr(tick, "bid" if is_buy else "ask", None)
                if mark:
                    tick_msc = int(getattr(tick, "time_msc", 0) or 0)
                    tick_age_ms = max(0, int(time.time() * 1000) - tick_msc) if tick_msc else None
                    spread = None
                    bid, ask = getattr(tick, "bid", None), getattr(tick, "ask", None)
                    if bid is not None and ask is not None:
                        spread = float(ask) - float(bid)
                    self.store.record_position_observation(
                        int(position["ticket"]), float(mark), float(position.get("profit", 0.0)),
                        source="mt5_position_quote", quote_time_msc=tick_msc or None,
                        tick_age_ms=tick_age_ms, spread=spread,
                        mark_quality="BROKER_FRESH" if tick_age_ms is not None and tick_age_ms <= 5_000 else "STALE",
                    )
        self._v2_seam("cycle.seams.executed")
        return decision, safe_vol



def resolve_strategy_cycles(timeframe: str, strategies: str) -> List[Tuple[str, str]]:
    """Resolve independent swing/scalp engines without collapsing ``both``.

    ``AUTO`` is only one way to request the two engines.  An explicit M5/M15
    or H1/H4/D1 timeframe must not silently disable the other state machine.
    Each engine still receives its own completed-candle trigger and shared
    portfolio arbiter downstream.
    """
    timeframe = str(timeframe or "AUTO").upper()
    strategies = str(strategies or "scalp").lower()
    if strategies == "both":
        swing_tf = timeframe if timeframe in {"H1", "H4", "D1"} else "H1"
        scalp_tf = timeframe if timeframe in {"M1", "M5", "M15"} else "M15"
        return [("swing", swing_tf), ("scalp", scalp_tf)]
    return [(strategies, timeframe if timeframe != "AUTO" else ("H1" if strategies == "swing" else "M15"))]


