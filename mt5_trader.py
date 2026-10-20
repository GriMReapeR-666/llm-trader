# mt5_trader.py
"""
MT5 Trader Script

Usage examples:
  - Initialize and show account info using config file:
      python mt5_trader.py account-info --config config.ini

  - Fetch OHLC and indicators for EURUSD M15 last 500 bars:
      python mt5_trader.py data --symbol EURUSD --timeframe M15 --count 500 --indicators RSI MACD EMA(20) ATR(14) HMA(21)

  - Order entry: manual CLI order placement is intentionally unavailable.
    New positions are created exclusively by the autonomous cycle, which
    validates each proposal deterministically and journals an immutable
    order intent before submission (validator -> arbiter -> margin/cost
    gates -> intent -> pending limit/stop order).

  - Read-only broker surfaces (require an authenticated terminal):
      python mt5_trader.py positions
      python mt5_trader.py orders
      python mt5_trader.py history --start 2026-01-01 --end 2026-02-01
      python mt5_trader.py test-live        # read-only connectivity probe

  - Performance benchmark:
      python mt5_trader.py benchmark --symbol EURUSD --timeframe H1 --count 2000

Environment variables (override config.ini):
  MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
  LLM_TRADER_ALLOW_LIVE=true  (required in addition to --live and confirmation)

"""

from __future__ import annotations
import argparse
import configparser
import os
import sys
from pathlib import Path
# Force UTF-8 encoding for Windows console to prevent Rich TUI crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Bounded OpenBLAS / MKL / OMP threads to prevent memory arena exhaustion on Windows
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

_root_dir = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = _root_dir  # Alias for backward compatibility with RL training pipeline
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

# Single import root (C1): bare and package-qualified imports of the same
# file resolve to ONE module object (one kill-switch latch, one set of
# singletons).  Installed before the first repo import below.
try:
    from _import_root import install as _install_import_root
    _install_import_root()
except ImportError:
    # sitecustomize may not have run (python -S, an embedded interpreter);
    # the canonical import still installs the finder and re-registers itself
    # under the bare name, so both spellings share one module object.
    try:
        from core._import_root import install as _install_import_root
        _install_import_root()
    except Exception as _ir_exc:  # pragma: no cover - bootstrap must never die here
        import warnings as _warnings
        _warnings.warn(f"single-import-root install failed: {_ir_exc}")
except Exception as _ir_exc:  # pragma: no cover - bootstrap must never die here
    import warnings as _warnings
    _warnings.warn(f"single-import-root install failed: {_ir_exc}")

import time
import json
import logging
import math
import hashlib
import threading
from dataclasses import dataclass, replace
from typing import Optional, List, Dict, Tuple, Any
from datetime import datetime, timedelta, timezone # Added timezone for UTC support

# IMPORT REFACTORED MODULES
from core.risk_manager import RiskManager
from core.trade_manager import TradeManager
from research.rl_trainer import RLTrainer
from core.trading_safety import (DeterministicValidator, PortfolioRiskArbiter,
                            BOT_MAGICS, TradeProposal, TradingStore, ValidationResult,
                            safe_risk_fraction, exposure_buckets)
from intelligence.trading_memory import TradingMemory
from operations.journal_audit import audit as audit_journal, baseline_metrics, qualified_metrics, write_report, write_audit_export
from operations.journal_migration import migrate as migrate_legacy_journal
from operations.agent_operations import AgentOperations
from intelligence.local_llm_memory import process_outbox as process_local_memory_outbox
try:
    from research.research_validation import (promotion_gate as research_promotion_gate,
                                     labelled_dataset, broker_reconciled_tickets)
except ImportError:
    from research.research_validation import (promotion_gate as research_promotion_gate,
                                              labelled_dataset, broker_reconciled_tickets)
from research.promotion_package import write as write_promotion_package, verify as verify_promotion_package
from research.baseline_research import baseline_report
from research.adaptive_research_service import AdaptiveResearchService
from data.data_quality import DataQualityGate
from data.provider_circuit import ProviderCircuitBreaker
try:
    from research.setup_registry import classify as classify_setup
except ImportError:
    from research.setup_registry import classify as classify_setup
from core.broker_preflight import persist_preflight, validate_envelope
from core.order_gateway import get_default_gateway, configure_default_gateway
from core.execution_planning import build_pending_entry, validate_emergency_exit_request, validate_quote
from intelligence.feature_builder import build_snapshot as build_feature_snapshot
try:
    from intelligence.macro_graph import MacroGraph
except ImportError:
    from intelligence.macro_graph import MacroGraph
from intelligence.lag_delay_engine import (
    TimestampedEvent, HeartbeatTracker, ConfidenceDecayEngine, SmithPredictorEngine,
    SlippageCalibrator, utcnow_iso,
)
from research.rl_action_bridge import bridge_rl_action, merge_bridge_into_decision
from intelligence.autonomous_causal_engine import AutonomousCausalValidator, CausalChainSetupAnalyzer
from operations.account_growth import AccountGrowthController
from research.strategy_accuracy import build_strategy_accuracy_scorecard
from research.research_sprint_planner import plan_and_persist as plan_research_sprints
from intelligence.opportunity_bus import (
    publish as publish_opportunities,
    publish_from_scan,
    publish_from_stat_pass_edges,
    opportunity_from_features,
    bus_snapshot,
)
from intelligence.opportunity_fusion import score_opportunity, fusion_block_reasons
from intelligence.cycle_brief import build as build_cycle_brief, persist as persist_cycle_brief
from intelligence.causal_propagate import propagate as propagate_causal_paths
from intelligence.macro_state_vector import build_macro_state_vector
from data.data_source_pipeline import (
    catalog_fred_series,
    process_cycle_data_sources,
)
from data.realtime_news_ingest import run as run_realtime_news_ingest
from research.profitability_foundation import report as profitability_report
from operations.agent_scheduler import bootstrap as bootstrap_schedules
from operations.dual_velocity import DualVelocityExecutor
from intelligence.market_filters import evaluate as evaluate_market_conditions, session_name
from intelligence.market_microstructure import MicrostructureMonitor
from operations.event_policy import evaluate as evaluate_event_policy, symbol_currencies
from intelligence.correlation_risk import CorrelationRisk
from intelligence.intermarket_regime import (
    build_intermarket_regime_snapshot,
    filter_edges_for_live_regime,
)
from core.margin_safety import MarginSafetyGate
from operations.daily_reconciliation import compare_day as compare_broker_day
from data.data_requirements import evaluate as evaluate_data_requirements
from intelligence.cost_estimator import assess_costs
from intelligence.feature_schema import validate_snapshot as validate_feature_schema
from research.model_registry import list_models, register as register_model, review as review_model, drift_report as model_drift_report, activation_status as model_activation_status
from research.research_stress import stress_report
from research.contextual_bandit import bandit_report
from intelligence.semantic_memory import search as semantic_memory_search
from operations.health_alerts import evaluate as evaluate_health_alerts
from operations.alert_outbox import enqueue as enqueue_alerts, claim as claim_alerts, acknowledge as acknowledge_alert, recover_expired as recover_alerts, status as alert_outbox_status
from operations.alert_delivery import deliver_pending as deliver_alerts, resolve_webhook_url
from operations import strategy_lifecycle as lifecycle
from operations.strategy_evaluation import evaluate as evaluate_strategy_lifecycle
try:
    from research.setup_registry import SETUPS as REGISTERED_SETUPS
except ImportError:
    from research.setup_registry import SETUPS as REGISTERED_SETUPS
from intelligence.llm_orchestrator import (
    setup_registry_fingerprint as registered_setup_fingerprint,
    SIGNAL_SYSTEM_PROMPT_TEMPLATE,
)
from operations.legacy_reconciliation import report as legacy_reconciliation_report, quarantine as quarantine_legacy_record
from operations.journal_backup import (
    create_backup, verify_backup, restore_backup,
    create_encrypted_backup, verify_encrypted_backup, restore_encrypted_backup,
    prune_backups,
)
from operations.restart_recovery import smoke as restart_recovery_smoke
from operations.journal_scope import status as journal_scope_status, ensure_scope
from research.historical_regimes import research_status as historical_research_status
from intelligence.macro_extraction import status as macro_extraction_status
from research.model_family_gate import evaluation_plan as model_family_evaluation_plan
from core.execution_policy import load as load_execution_policy, candidate_for_store as execution_policy_candidate, approve_for_store as approve_execution_policy
from research.forward_test_report import compare as compare_forward_test
from operations.security_audit import audit as security_audit
from research.supervised_training import train as train_supervised_baseline
from research.research_runs import record as record_validation_run, list_runs as list_validation_runs
from operations.live_rollout_gate import status as live_rollout_status, approve as approve_live_rollout, revoke as revoke_live_rollout, require as require_live_rollout, verify_evidence as verify_live_evidence
from research.sample_gate import report as sample_gate_report
from operations.mt5_auth_diagnostics import report as mt5_auth_report
from operations.readiness_report import build as readiness_report
from operations.incident_drills import run as run_incident_drills
from operations.research_influence_gate import allows as research_influence_allows, status as research_influence_status, approve as approve_research_influence, revoke as revoke_research_influence, evidence_fingerprint as research_evidence_fingerprint
from ui.dashboard_report import build as dashboard_report
from operations.metrics_persistence import MetricsStore
from operations.alert_rules import AlertRulesEngine
from operations.degradation_detector import DegradationDetector
from research.auto_promotion import AutoPromoter
from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
from research.divergence_monitor import DivergenceMonitor
from operations.prometheus_exporter import start_exporter as start_prometheus_exporter
from operations.memory_orchestrator import MemoryOrchestrator
from intelligence.knowledge_retrieval import KnowledgeRetrieval
from operations.meta_improvement_orchestrator import MetaImprovementOrchestrator
from research.kronos_signal_fusion import KronosSignalFusion
from research.advanced_rl_pipeline import AdvancedRLPipeline
try:
    from operations.improvement_pipeline import ImprovementPipeline
except ImportError:
    ImprovementPipeline = None

# ── Blind spot modules (wired in from audit) ─────────────────────────────────
try:
    from core.distributed_lock import get_trade_lock, TradeLock
except ImportError:
    get_trade_lock = None
    TradeLock = None

try:
    from core.model_staleness_tracker import ModelStalenessTracker
except ImportError:
    ModelStalenessTracker = None

try:
    from core.adv_tracker import ADVTracker as _ADVTracker
except ImportError:
    _ADVTracker = None

# Active session instances
adv_tracker = _ADVTracker() if _ADVTracker is not None else None
model_staleness_tracker = ModelStalenessTracker() if ModelStalenessTracker is not None else None

# ── Demo trading infrastructure (live training pipeline) ────────────────────
try:
    from core.demo_trading_orchestrator import DemoTradingOrchestrator
    from core.live_training_infrastructure import (
        TradeLifecycleManager, RealDataCollector,
        MetaAdaptiveFeedbackLoop, RLLiveTrainingPipeline, LiveValidationGate,
    )
    DEMO_ORCH_AVAILABLE = True
except ImportError:
    DEMO_ORCH_AVAILABLE = False

try:
    from data.llm_router import LLMRouter, get_router, reset_router
    LLM_ROUTER_AVAILABLE = True
except ImportError:
    LLM_ROUTER_AVAILABLE = False

import numpy as np
import pandas as pd

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass # python-dotenv not installed, rely on system env vars

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None


def collect_calendar_event_rows(calendar: Any, client: Any,
                                 mt5_module: Any = None,
                                 now: Optional[datetime] = None) -> Tuple[List[Dict[str, Any]], str]:
    """Return timestamped calendar rows and their authoritative source.

    Only broker/calendar releases may populate event blackout or reaction
    inputs.  Macro observations such as FRED series are deliberately excluded
    from this helper.  An empty or failed wrapper query falls back to the
    authenticated MT5 calendar when available; no current-clock timestamp is
    invented for an observation.
    """
    rows: List[Dict[str, Any]] = []
    source = "UNAVAILABLE"
    if calendar is not None:
        try:
            events = calendar.get_events(
                limit=5,
                min_importance=2,
                currencies=["USD", "EUR", "GBP", "JPY"],
            )
            for event in events or []:
                row = event.to_dict() if hasattr(event, "to_dict") else dict(event)
                if isinstance(row, dict):
                    rows.append(row)
            source = "configured_calendar"
        except Exception:
            source = "ERROR"

    if rows:
        return rows, source

    module = mt5_module if mt5_module is not None else mt5
    if client is not None and module is not None and hasattr(module, "calendar_get"):
        anchor = now or datetime.now(timezone.utc)
        anchor_naive = anchor.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            events = module.calendar_get(anchor_naive, anchor_naive + timedelta(hours=24))
            high_impact = []
            for event in events or []:
                try:
                    importance = int(getattr(event, "importance"))
                except (TypeError, ValueError):
                    continue
                if importance >= 2:
                    high_impact.append(event)
            high_impact.sort(key=lambda event: getattr(event, "time", 0))
            for event in high_impact[:5]:
                try:
                    event_time = datetime.fromtimestamp(float(getattr(event, "time")), tz=timezone.utc).isoformat()
                except (TypeError, ValueError, OSError, OverflowError):
                    continue
                rows.append({
                    "time": event_time,
                    "currency": getattr(event, "currency", ""),
                    "event": getattr(event, "event", ""),
                    "importance": int(getattr(event, "importance")),
                    "forecast": getattr(event, "forecast_value", None),
                    "previous": getattr(event, "previous_value", None),
                })
            source = "mt5_calendar" if rows else source
        except Exception:
            if source == "UNAVAILABLE":
                source = "ERROR"
    return rows, source

# Rich TUI imports
try:
    from rich.console import Console
    from rich.progress import Progress
    from rich.live import Live
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    Console = None

# Custom module imports
try:
    from data.api_manager import APIManager
    API_MANAGER_AVAILABLE = True
except ImportError:
    API_MANAGER_AVAILABLE = False
    APIManager = None

# The Rich-based terminal dashboards (ui/display.py, ui/tui_dashboard.py,
# ui/unified_tui.py, ui/log_monitor.py) are retired.  The single canonical TUI
# is the OpenTUI command deck (tui_opentui/) launched via
# `python mt5_trader.py deck`.  The in-process `dashboard` hook below is a thin
# adapter that mirrors state to the API server (:8000) which the deck and the
# web GUI read; Tkinter gui_launcher still injects its own dashboard here.
DISPLAY_AVAILABLE = False
TradingDisplay = None

try:
    from research.model_selector import ModelSelector
    MODEL_SELECTOR_AVAILABLE = True
except ImportError:
    MODEL_SELECTOR_AVAILABLE = False
    ModelSelector = None

try:
    from ui.web_dashboard_adapter import dashboard
    TUI_AVAILABLE = True
except ImportError:
    TUI_AVAILABLE = False
    dashboard = None

# TradeManager is already imported above in the refactored-modules block.
TRADE_MANAGER_AVAILABLE = True

try:
    from ui.scanner import MarketScanner
except ImportError:
    MarketScanner = None


# ---------- Logging ----------
# Helper for Colorful CLI
def print_bordered(console, title, text, style="white"):
    """Print text inside a colored border"""
    from rich.panel import Panel
    from rich.console import Console
    
    if not console:
        console = Console(force_terminal=True)
        
    # Parse markup explicitly to ensure styles are rendered
    # Use try/except to handle invalid markup (e.g. unescaped brackets from LLM)
    from rich.text import Text
    from rich.errors import MarkupError
    from rich import box
    try:
        content = Text.from_markup(text)
    except MarkupError:
        # Fallback to plain text if markup fails
        content = Text(text)
    
    # Force HEAVY box style for maximum visibility
    console.print(Panel(content, title=title, border_style=style, box=box.HEAVY))

class TUILogHandler(logging.Handler):
    def emit(self, record):
        if TUI_AVAILABLE and dashboard:
            msg = self.format(record)
            dashboard.log(msg, record.levelname)

def setup_logging(level: str = "INFO", debug_modules: bool = False) -> None:
    # Rotating file handler: bounds disk growth (10 MB x 5) so an unattended
    # session can never exhaust the drive or grow memory on log writes.
    from logging.handlers import RotatingFileHandler
    handlers = [
        # stderr, not stdout: journal/JSON subcommands emit pure JSON on
        # stdout, and a single INFO line there corrupts every consumer.
        logging.StreamHandler(sys.stderr),
        RotatingFileHandler("mt5_trader.log", maxBytes=10 * 1024 * 1024,
                            backupCount=5, encoding="utf-8"),
    ]
    if TUI_AVAILABLE:
        handlers.append(TUILogHandler())
        
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )

    # --debug: elevate trading-critical modules to DEBUG without flooding everything
    if debug_modules:
        _CRITICAL_LOGGERS = [
            "intelligence.llm_orchestrator",
            "core.crash_detector",
            "core.walk_forward_validator",
            "core.optimal_execution",
            "core.order_execution",
            "core.trade_manager",
            "core.risk_manager",
            "core.ewc_forgetting_prevention",
            "core.auto_quant_tools",
            "core.causal_feature_filter",
            "core.trading_constraints",
            "core.trading_tools",
            "core.alpha_ensemble",
            "core.alpha_rotation",
            "core.alpha_decay_monitor",
            "core.meta_adaptive_engine",
            "core.self_improvement_loop",
            "core.policy_library",
            "research.rl_trainer",
            "data.market_data_provider",
        ]
        for name in _CRITICAL_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)
        logging.getLogger("LLMOrchestrator").setLevel(logging.DEBUG)
        logging.getLogger("MT5Client").setLevel(logging.DEBUG)
        logging.getLogger("RiskManager").setLevel(logging.DEBUG)
        logging.getLogger("TradeManager").setLevel(logging.DEBUG)
        logging.getLogger("RLTrainer").setLevel(logging.DEBUG)
        logging.getLogger("MT5Client").info(
            "--debug enabled: trading-critical modules now log at DEBUG level"
        )



# REFACTORED CORE MODULES RE-EXPORTED FOR BACKWARD COMPATIBILITY
from core.technical_indicators import (
    ema, rsi, macd, atr, wma, hma, adx, compute_indicators,
)
from core.order_execution import (
    PaperBroker, _symbol_tick, _broker_market_filling_mode,
    validate_order_params, validate_pending_entry_geometry,
    place_market_order, emergency_market_exit, place_pending_order,
    modify_order_sltp, cancel_pending_order,
)
from data.market_data_provider import (
    TIMEFRAME_MAP, tf_const, fetch_ohlc, collect_calendar_event_rows,
    DOMCollector, PerformanceMonitor, MarketDataProvider,
)
from intelligence.llm_orchestrator import (
    LLMOrchestrator,
)

# ---------- Account Management ----------
class AccountManager:
    FILE_PATH = "accounts.json"

    @staticmethod
    def load_accounts() -> Dict[str, Any]:
        if not os.path.exists(AccountManager.FILE_PATH):
            return {"default": None, "accounts": {}}
        try:
            with open(AccountManager.FILE_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load accounts: {e}")
            return {"default": None, "accounts": {}}

    @staticmethod
    def save_accounts(data: Dict[str, Any]) -> None:
        with open(AccountManager.FILE_PATH, "w") as f:
            json.dump(data, f, indent=4)

    @staticmethod
    def add_account(name: str, login: int, password_env: str, server: str, acc_type: str) -> None:
        """Register account metadata without ever persisting a password."""
        env_name = str(password_env or "").strip()
        if not env_name or not (env_name.replace("_", "").isalnum() and env_name.upper() == env_name):
            raise ValueError("password_env must be an uppercase environment-variable name")
        data = AccountManager.load_accounts()
        data["accounts"][name] = {
            "login": login,
            "password_env": env_name,
            "server": server,
            "type": acc_type.upper()
        }
        if data["default"] is None:
            data["default"] = name
        AccountManager.save_accounts(data)
        print(f" Account '{name}' added successfully.")

    @staticmethod
    def add_secure_account(name: str, login: int, password: str, server: str, acc_type: str) -> None:
        """Register account with password stored in OS keyring / encrypted file (never plaintext in JSON)."""
        try:
            from core.secure_store import store_password
        except ImportError:
            from core.secure_store import store_password  # type: ignore
        store_key = store_password(str(login), server, password)
        data = AccountManager.load_accounts()
        data["accounts"][name] = {
            "login": login,
            "password_store_key": store_key,
            "password_env": f"SECURE:{store_key}",  # marker so UIs know it's secure
            "server": server,
            "type": acc_type.upper()
        }
        if data["default"] is None:
            data["default"] = name
        AccountManager.save_accounts(data)
        # scrub local var
        del password
        print(f" Secure account '{name}' added (store_key={store_key}, backend via secure_store).")

    @staticmethod
    def set_default(name: str) -> None:
        data = AccountManager.load_accounts()
        if name not in data["accounts"]:
            raise ValueError(f"Account '{name}' not found.")
        data["default"] = name
        AccountManager.save_accounts(data)
        print(f" Default account set to '{name}'.")

    @staticmethod
    def list_accounts() -> None:
        data = AccountManager.load_accounts()
        default = data.get("default")
        print("\n Available Accounts:")
        print(f"{'Name':<15} {'Type':<10} {'Login':<15} {'Server':<20} {'Status':<10} {'Auth'}")
        print("-" * 80)
        for name, details in data["accounts"].items():
            status = "ACTIVE (Default)" if name == default else ""
            auth = "SECURE" if details.get("password_store_key") else ("ENV:" + details.get("password_env","") if details.get("password_env") else "MISSING")
            print(f"{name:<15} {details['type']:<10} {details['login']:<15} {details['server']:<20} {status:<10} {auth}")
        print("-" * 80 + "\n")

    @staticmethod
    def get_active_account() -> Optional[Dict[str, Any]]:
        data = AccountManager.load_accounts()
        default = data.get("default")
        if default and default in data["accounts"]:
            return data["accounts"][default]
        return None

    @staticmethod
    def password_for(account: Optional[Dict[str, Any]]) -> str:
        """Resolve a password via secure store -> env -> legacy (never logs).

        Priority: ENV override MT5_PASSWORD > secure_store (password_store_key) > password_env env > legacy plaintext.
        """
        item = account if isinstance(account, dict) else {}
        # Explicit env override always wins (for CI)
        if os.environ.get("MT5_PASSWORD"):
            # only if the requested login matches MT5_LOGIN or no MT5_LOGIN set
            env_login = os.environ.get("MT5_LOGIN","").strip()
            if not env_login or str(env_login) == str(item.get("login","")):
                return str(os.environ.get("MT5_PASSWORD") or "")
        # Secure store
        store_key = str(item.get("password_store_key") or "").strip()
        if store_key:
            try:
                try:
                    from core.secure_store import retrieve_password
                except ImportError:
                    from core.secure_store import retrieve_password  # type: ignore
                pw = retrieve_password(store_key)
                if pw:
                    return str(pw)
            except Exception as exc:
                logging.getLogger("mt5_trader").debug("API request failed: %s", exc)
            # also try derived key if store_key is placeholder
            try:
                try:
                    from core.secure_store import retrieve_password as rp2
                except ImportError:
                    from core.secure_store import retrieve_password as rp2  # type: ignore
                pw2 = rp2(None, login=item.get("login"), server=item.get("server"))
                if pw2:
                    return str(pw2)
            except Exception as exc:
                logging.getLogger("mt5_trader").debug("login failed: %s", exc)
        env_name = str(item.get("password_env") or "").strip()
        if env_name and not env_name.startswith("SECURE:"):
            return str(os.environ.get(env_name, "") or "")
        if env_name and env_name.startswith("SECURE:"):
            # secure marker: try to fetch by login/server even if key missing
            try:
                try:
                    from core.secure_store import retrieve_password
                except ImportError:
                    from core.secure_store import retrieve_password  # type: ignore
                pw = retrieve_password(None, login=item.get("login"), server=item.get("server"))
                if pw:
                    return str(pw)
            except Exception as exc:
                logging.getLogger("mt5_trader").debug("data fetch failed: %s", exc)
        return str(os.environ.get("MT5_PASSWORD") or item.get("password") or "")


# ---------- Configuration ----------
@dataclass
class Config:
    login: int
    password: str
    server: str
    risk_per_trade: float
    max_drawdown: float
    default_volume: float
    log_level: str
    cooldown: int
    max_trades_per_hour: int

    @staticmethod
    def from_sources(config_path: str = "config.ini") -> 'Config':
        # 1. Try Account Manager first
        active_acc = AccountManager.get_active_account()
        
        # 2. Load Config File
        import configparser
        cp = configparser.ConfigParser()
        if config_path and os.path.exists(config_path):
            cp.read(config_path)
        elif os.path.exists("config.ini"):
            cp.read("config.ini")

        # Defaults
        risk = 0.01
        dd = 0.10
        vol = 0.1
        lvl = "INFO"
        cd = 300
        max_t = 5

        if "risk" in cp:
            risk = cp["risk"].getfloat("risk_per_trade", 0.01)
            max_dd = cp["risk"].getfloat("max_drawdown", 0.10) # Fix: use max_dd var
            dd = max_dd
        
        if "limits" in cp:
            cd = cp["limits"].getint("cooldown_seconds", 300)
            max_t = cp["limits"].getint("max_trades_per_hour", 5)

        if "general" in cp:
            lvl = cp["general"].get("log_level", "INFO")
        
        if "trading" in cp:
            vol = cp["trading"].getfloat("default_volume", 0.1)

        # Credentials Priority: Env Vars > AccountManager > config.ini
        # 1. Environment Variables (e.g. from UI Configuration)
        login_env = os.environ.get("MT5_LOGIN", "")
        login = int(login_env) if login_env and str(login_env).strip().isdigit() else 0
        password = os.environ.get("MT5_PASSWORD", "")
        server = os.environ.get("MT5_SERVER", "")

        # 2. Account Manager (if Env Vars missing)
        if not login and active_acc:
            login = int(active_acc["login"])
            password = AccountManager.password_for(active_acc)
            server = active_acc["server"]
            logging.getLogger("Config").info(
                "Using Account: %s - %s", active_acc.get("type", "UNKNOWN"), login
            )
        
        # 3. Config File (Fallbacks)
        elif not login and "account" in cp:
            login = cp["account"].getint("login", 0)
            password_env = cp["account"].get("password_env", "MT5_PASSWORD")
            password = os.environ.get(password_env, "") or cp["account"].get("password", "")
            server = cp["account"].get("server", "")

        return Config(login, password, server, risk, dd, vol, lvl, cd, max_t)


# ---------- MT5 Client with reconnection ----------
class MT5Client:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = logging.getLogger("MT5Client")
        # A disconnected terminal must fail closed instead of blocking the
        # continuous loop indefinitely. MetaTrader5 accepts milliseconds;
        # clamp the operator override to a bounded, reviewable range.
        try:
            requested_timeout = int(os.environ.get("MT5_INIT_TIMEOUT_MS", "5000"))
        except (TypeError, ValueError):
            requested_timeout = 5000
        self.initialize_timeout_ms = max(1000, min(requested_timeout, 30000))
        self._last_reconnect_attempt: float = 0.0
        self._reconnect_backoff_sec: float = 5.0

    def ensure_import(self) -> None:
        if mt5 is None:
            raise RuntimeError("MetaTrader5 package not installed. Install dependencies from requirements.txt.")

    def _initialize(self, path: Optional[str], *, login: int, password: str, server: str) -> bool:
        """Initialize MT5 with a bounded timeout and no credential logging."""
        kwargs = {"login": login, "password": password, "server": server,
                  "timeout": self.initialize_timeout_ms}
        if path:
            kwargs["path"] = path
        try:
            return bool(mt5.initialize(**kwargs))
        except TypeError:
            # Compatibility for a test/fake wrapper that predates MT5's
            # timeout keyword. The real MetaTrader5 module supports it.
            kwargs.pop("timeout", None)
            return bool(mt5.initialize(**kwargs))

    def login(self) -> None:
        self.ensure_import()
        
        # Known paths to try if default fails
        paths = [None] # None = default lookup
        known_paths = [
            r"C:\Program Files\MetaTrader 5\terminal64.exe",
            r"C:\Program Files\MetaTrader\terminal64.exe",
            r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe"
        ]
        for p in known_paths:
            if os.path.exists(p):
                paths.append(p)

        # Try to get active account from AccountManager first
        account = AccountManager.get_active_account()
        
        # Environment variables are the explicit operator override.  Falling
        # back to the selected local account preserves existing UX without
        # silently ignoring a deployment's injected credentials.
        login_env = os.environ.get("MT5_LOGIN", "").strip()
        login = int(login_env) if login_env.isdigit() else (int(account["login"]) if account else int(self.cfg.login or 0))
        password = os.environ.get("MT5_PASSWORD") or AccountManager.password_for(account) or self.cfg.password
        server = os.environ.get("MT5_SERVER") or (account["server"] if account else self.cfg.server)
        has_creds = bool(login and password and server)
        
        if account and login:
            self.log.info(f" Using Account: {account['type'] if account else 'ENV'} - {login}")

        # 1) Preferred path: attach to an already-running, already-signed-in
        #    terminal (the same connection the MCP bridge and read-only data
        #    surfaces use).  No credentialed re-login is attempted, so a
        #    healthy terminal never hits "Authorization failed" merely because
        #    an operator logged in interactively first.  Fails closed if the
        #    attached account is not the requested one.
        if login and has_creds or not (login or password or server):
            try:
                if mt5.initialize():
                    ti = mt5.terminal_info()
                    ai = mt5.account_info() if ti is not None else None
                    if ti is not None and ai is not None:
                        attached_login = int(getattr(ai, "login", 0) or 0)
                        attached_server = str(getattr(ai, "server", "") or "").strip()
                        on_target = (
                            (not login) or
                            (attached_login == login and (not server or attached_server.upper() == str(server).upper()))
                        )
                        if on_target:
                            self.log.info(" Attached to running terminal: %s (%s)", attached_login, attached_server)
                            self._report_terminal(ti, ai)
                            return True
                        # Terminal is up but on a different account: try a
                        # credentialed switch before any shutdown/re-init.
                        self.log.warning(
                            "Terminal attached to %s %s; switching to %s %s",
                            attached_login, attached_server, login, server,
                        )
                        if has_creds:
                            try:
                                if mt5.login(login, password=password, server=server):
                                    ti = mt5.terminal_info()
                                    ai = mt5.account_info() if ti is not None else None
                                    if ti is not None and ai is not None:
                                        self.log.info(" Account switched via mt5.login")
                                        self._report_terminal(ti, ai)
                                        return True
                            except Exception as switch_exc:
                                self.log.warning("Account switch failed: %s", switch_exc)
                        self.log.error("Attached terminal is on the wrong account; refusing to proceed")
                        return False
                else:
                    err = mt5.last_error()
                    self.log.warning(f"Bare attach failed: {err}")
            except Exception as attach_exc:
                self.log.warning("Bare attach exception: %s", attach_exc)

        if not has_creds:
            self.log.error("Missing MT5 credentials (set in accounts.json or .env)")
            return False

        # 2) Fallback: nothing attachable — shutdown and perform a full
        #    credentialed initialize() against each known terminal path.
        try:
            mt5.shutdown()
            time.sleep(0.5)
        except Exception as exc:
            logging.getLogger("mt5_trader").debug("shutdown failed: %s", exc)

        # Try paths
        connected = False
        for path in paths:
            try:
                if path:
                    self.log.info(f"Trying path: {path}")
                    init_res = self._initialize(path, login=login, password=password, server=server)
                else:
                    self.log.info("Trying default MT5 initialization...")
                    init_res = self._initialize(None, login=login, password=password, server=server)
                
                if init_res:
                    connected = True
                    break
                else:
                    err = mt5.last_error()
                    self.log.warning(f"Init failed (path={path}): {err}")
            except Exception as e:
                self.log.warning(f"Init exception (path={path}): {e}")
        
        if not connected:
            self.log.error("Failed to connect to MT5 terminal")
            return False

        # Check connection
        ti = mt5.terminal_info()
        ai = mt5.account_info()
        if ti and ai:
            self._report_terminal(ti, ai)
        else:
            self.log.error("Could not get terminal info")
        
        return True

    def _report_terminal(self, ti: Any, ai: Any) -> None:
        """Log terminal + account identity/mode once (no secrets)."""
        if ti:
            self.log.info(f" Connected to {ti.name} ({ti.company})")
            self.log.info(f" Auto-trading is {'ENABLED' if ti.trade_allowed else 'DISABLED'}")
        if ai:
            self.log.info("=== Account Info ===")
            self.log.info(f"Login: {ai.login}")
            self.log.info(f"Server: {ai.server}")
            self.log.info(f"Balance: {ai.balance}")
            self.log.info(f"Equity: {ai.equity}")
            self.log.info(f"Company: {ai.company}")
            self.log.info(f"Name: {ai.name}")
            self.log.info("=====================")

    def get_account_info(self) -> Dict[str, Any]:
        """Fetch current account info safely"""
        self.ensure_import()
        try:
            ai = mt5.account_info()
            if ai:
                return {
                    "login": ai.login,
                    "balance": ai.balance,
                    "equity": ai.equity,
                    "profit": ai.profit,
                    "margin": ai.margin,
                    "margin_free": ai.margin_free,
                    "margin_level": ai.margin_level,
                    "margin_so_mode": getattr(ai, "margin_so_mode", None),
                    "margin_so_call": getattr(ai, "margin_so_call", None),
                    "margin_so_so": getattr(ai, "margin_so_so", None),
                    "leverage": ai.leverage,
                    "currency": ai.currency,
                    "server": ai.server,
                    "trade_mode": getattr(ai, "trade_mode", None),
                }
        except Exception as e:
            self.log.warning(f"Failed to get account info: {e}")
        return {}

    def account_info(self) -> Any:
        """Fetch raw MT5 account info namedtuple safely."""
        self.ensure_import()
        try:
            return mt5.account_info()
        except Exception as e:
            self.log.warning(f"Failed to get raw account info: {e}")
            return None

    def reconnect_if_needed(self) -> bool:
        """Throttled reconnection check with exponential backoff."""
        if mt5 is None:
            return False
        try:
            ti = mt5.terminal_info()
            if ti and getattr(ti, "connected", False):
                self._reconnect_backoff_sec = 5.0
                return True
        except Exception:
            ti = None

        now = time.time()
        if (now - self._last_reconnect_attempt) < self._reconnect_backoff_sec:
            # Backoff interval active; do not spam login
            return False

        self._last_reconnect_attempt = now
        self.log.warning(
            "Terminal disconnected. Attempting reconnection (backoff=%.1fs)...",
            self._reconnect_backoff_sec,
        )
        try:
            success = bool(self.login())
        except Exception as exc:
            self.log.warning("Login exception during reconnection: %s", exc)
            success = False

        if success:
            self.log.info("Reconnection successful. Resetting backoff.")
            self._reconnect_backoff_sec = 5.0
            return True
        else:
            self._reconnect_backoff_sec = min(60.0, self._reconnect_backoff_sec * 2.0)
            return False
    
    def diagnose_terminal(self) -> None:
        """Print detailed terminal diagnostics"""
        ti = mt5.terminal_info()
        if ti:
            self.log.info("=== Terminal Diagnostics ===")
            self.log.info(f"Connected: {ti.connected}")
            self.log.info(f"Trade Allowed: {ti.trade_allowed}")
            self.log.info(f"DLLs Allowed: {ti.dlls_allowed}")
            self.log.info(f"TradeAPI Disabled: {ti.tradeapi_disabled}")
            self.log.info(f"Build: {ti.build}")
            self.log.info(f"Name: {ti.name}")
            self.log.info(f"Company: {ti.company}")
            self.log.info(f"Path: {ti.path}")
            self.log.info(f"Data Path: {ti.data_path}")
            self.log.info(f"Language: {ti.language}")
            self.log.info(f"Connected to Broker: {ti.connected}")
            self.log.info("===========================")
            
            # Check account info too
            ai = mt5.account_info()
            if ai:
                self.log.info("=== Account Info ===")
                self.log.info(f"Login: {ai.login}")
                self.log.info(f"Server: {ai.server}")
                self.log.info(f"Balance: {ai.balance}")
                self.log.info(f"Equity: {ai.equity}")
                self.log.info(f"Company: {ai.company}")
                self.log.info(f"Name: {ai.name}")
                self.log.info("=====================")
        else:
            self.log.error("Could not get terminal info")

    def shutdown(self) -> None:
        try:
            mt5.shutdown()
        except Exception as exc:
            logging.getLogger("mt5_trader").debug("shutdown failed: %s", exc)


# ---------- Live trading safety & nonces ----------
def confirm_live(action_desc: str, auto_yes: bool) -> None:
    if auto_yes:
        return
    resp = input(f"CONFIRM LIVE ACTION: {action_desc} (type 'yes' to proceed): ").strip().lower()
    if resp != "yes":
        raise RuntimeError("Live action cancelled by user.")


def live_trading_explicitly_enabled() -> bool:
    """A command flag alone must never enable unattended live trading."""
    return os.environ.get("LLM_TRADER_ALLOW_LIVE", "").strip().lower() in {"1", "true", "yes"}


def authenticated_account_fingerprint(mode: str, account_info: Dict[str, Any]) -> str:
    """Return the non-secret journal identity for an authenticated account.

    DEMO/LIVE scopes must be bound to the identity returned by MT5 itself. A
    configured login/server pair is not sufficient because it may describe a
    stale account file or a terminal attached to a different account.
    """
    normalized = str(mode or "").upper()
    if normalized in {"PAPER", "RESEARCH"}:
        return ""
    if normalized not in {"DEMO", "LIVE"}:
        raise ValueError(f"unsupported trading journal mode: {mode}")
    info = account_info if isinstance(account_info, dict) else {}
    login = info.get("login")
    server = str(info.get("server") or "").strip()
    if not login or not server:
        raise RuntimeError(
            f"Refusing {normalized} journal startup: authenticated MT5 account identity "
            "(login and server) was not returned by account_info."
        )
    return f"{login}|{server}"


def _ensure_paper_market_data_session() -> bool:
    """Attach paper mode to the running MT5 terminal for READ-ONLY data.

    Paper mode intentionally never performs an authenticated ``MT5Client``
    login (no credentials are loaded and no orders can be sent), but its
    market-data path — candle confirmation, OHLC analysis, tick quotes —
    is real broker data fetched through the module-level MetaTrader5
    connection.  Without an established session every cycle fails closed:
    ``terminal_info()`` returns None and candle confirmation logs
    "MT5 terminal is unavailable for <symbol>/<tf>" on every pulse, so
    paper sessions produce zero analysable candles and zero closed trades
    (which also starves RL updates and the sample gate).

    This calls the bare ``mt5.initialize()`` — attaching to the terminal
    the operator already has signed in — the same connection the read-only
    diagnostic surfaces use (terminal-session sentinel).  It is purely a
    data plane: PaperBroker still simulates all execution, so there is no
    order path from this session.

    Returns True when a terminal session is available (already attached or
    newly initialized); False when no terminal is running, in which case
    the loop should still start — every broker-dependent stage will fail
    closed as designed and non-MT5 pipelines (news, macro) keep working.
    """
    log = logging.getLogger("main")
    try:
        if mt5.terminal_info() is not None:
            log.info("Paper market-data session: terminal already attached")
            return True
    except Exception:
        pass  # not initialized yet — fall through to initialize
    try:
        if mt5.initialize():
            info = mt5.terminal_info()
            if info is None:
                # initialize() returned truthy but the terminal is not
                # answering — treat as unavailable, fail closed downstream.
                log.warning("Paper market-data session: initialize() ok but terminal_info() is None")
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                return False
            log.info(
                "Paper market-data session established (terminal-only, no credential login): %s",
                getattr(info, "name", "MT5 terminal"),
            )
            return True
        log.warning(
            "Paper market-data session: mt5.initialize() failed (%s) — "
            "candle confirmation will fail closed until a terminal is running",
            mt5.last_error(),
        )
        return False
    except Exception as exc:
        log.warning("Paper market-data session could not be established: %s", exc)
        return False


# ---------- Account and Monitoring ----------
def account_info() -> Dict[str, Any]:
    info = mt5.account_info()
    if not info:
        raise RuntimeError("Failed to get account info")
    return {
        "login": info.login, "balance": info.balance, "equity": info.equity,
        "margin": info.margin, "margin_free": info.margin_free,
        "currency": info.currency, "server": info.server
    }

def preflight_symbols(symbols: List[str]) -> Dict[str, str]:
    """Resolve broker symbols and verify they are tradeable before a session."""
    resolved: Dict[str, str] = {}
    all_symbols = mt5.symbols_get() or []
    names = [s.name for s in all_symbols]
    for requested in symbols:
        info = mt5.symbol_info(requested)
        name = requested
        if info is None:
            matches = [candidate for candidate in names if candidate.upper().startswith(requested.upper())]
            if len(matches) != 1:
                raise RuntimeError(f"Broker symbol preflight failed for {requested}; matches={matches[:5]}")
            name, info = matches[0], mt5.symbol_info(matches[0])
        if not info or getattr(info, "trade_mode", 0) == 0:
            raise RuntimeError(f"Broker symbol {name} is not tradeable")
        if not info.visible and not mt5.symbol_select(name, True):
            raise RuntimeError(f"Could not enable broker symbol {name}")
        resolved[requested] = name
    report = persist_preflight(TradingStore(), mt5, resolved.values())
    logging.getLogger("MT5Client").info(
        "Broker preflight captured for %s; specification changed=%s",
        ",".join(resolved.values()), report["changed"],
    )
    return resolved

def positions_overview() -> List[Dict[str, Any]]:
    poses = mt5.positions_get()
    out = []
    for p in poses or []:
        out.append({
            "ticket": p.ticket, "symbol": p.symbol, "type": p.type, "price_open": p.price_open,
            "volume": p.volume, "sl": p.sl, "tp": p.tp, "profit": p.profit
        })
    return out

def orders_overview() -> List[Dict[str, Any]]:
    ords = mt5.orders_get()
    return [{"ticket": o.ticket, "symbol": o.symbol, "type": o.type, "price": o.price, "volume_initial": o.volume_initial} for o in ords or []]

def history_overview(start, end) -> Dict[str, Any]:
    orders = mt5.history_orders_get(start, end)
    deals = mt5.history_deals_get(start, end)
    return {
        "orders_count": len(orders or []),
        "deals_count": len(deals or []),
    }



# ---------- Benchmark ----------
def benchmark(symbol: str, timeframe: str, count: int) -> Dict[str, float]:
    t0 = time.perf_counter()
    df = fetch_ohlc(symbol, timeframe, count=count)
    t1 = time.perf_counter()
    df = compute_indicators(df, ["RSI", "MACD", "EMA(50)", "ATR(14)"])
    t2 = time.perf_counter()
    return {"fetch_sec": t1 - t0, "indicators_sec": t2 - t1, "total_sec": t2 - t0}


def _run_simple_menu(interactive: bool = True) -> Dict[str, Any] | None:
    """Fallback text-based menu when questionary/console is unavailable"""
    print("\n" + "="*60)
    print("  LLM TRADER - Simple Text Configuration")
    print("="*60 + "\n")

    if not interactive:
        print("No interactive console detected. Using default settings...")
        return {
            "mode": "paper",
            "symbols": "EURUSD",
            "timeframe": "AUTO",
            "model": "meta/llama-3.2-11b-vision-instruct",
            "trading_mode": {"mode": "single", "loops": 1},
            "risk": {"risk_per_trade": 0.01},
            "advanced": {"show_metrics": True, "show_rl_metrics": True},
            "tui": False
        }

    mode = input("Select mode [paper/demo/live] (default: paper): ").strip().lower() or "paper"
    if mode not in ("paper", "demo", "live"):
        mode = "paper"

    symbols = input("Enter symbols (comma-separated, e.g. EURUSD,XAUUSD): ").strip() or "EURUSD"

    model = input("Enter model ID (default: meta/llama-3.2-11b-vision-instruct): ").strip() or "meta/llama-3.2-11b-vision-instruct"

    timeframe = "AUTO"

    trading_mode = input("Trading mode [single/continuous] (default: single): ").strip().lower() or "single"
    if trading_mode not in ("single", "continuous"):
        trading_mode = "single"

    loops = 1
    if trading_mode == "single":
        loops_str = input("Number of cycles (default: 1): ").strip() or "1"
        try:
            loops = int(loops_str)
        except ValueError:
            loops = 1

    risk_str = input("Risk per trade % (default: 1.0): ").strip() or "1.0"
    try:
        # Text-menu input is a human percentage; normalize once here.
        risk_per_trade = float(risk_str) / 100.0
    except ValueError:
        risk_per_trade = 0.01

    show_metrics = input("Show metrics at end? [Y/n]: ").strip().lower() != "n"
    show_rl_metrics = input("Show RL metrics? [Y/n]: ").strip().lower() != "n"
    tui = input("Enable complex TUI dashboard? [y/N]: ").strip().lower() == "y"

    providers = []
    print("\nAvailable LLM providers:")
    if os.getenv("OPENROUTER_API_KEY"):
        print("  1. OpenRouter")
    if os.getenv("CEREBRAS_API_KEY"):
        print("  2. Cerebras")
    i = 1
    while os.getenv(f"NVIDIA_KEY_{i}") or (i == 1 and os.getenv("NVIDIA_API_KEY")):
        print(f"  {2+i}. NVIDIA NIM #{i}")
        i += 1
    prov_str = input("Enter provider numbers/names (comma-separated, default: all): ").strip()
    if prov_str:
        providers = [p.strip() for p in prov_str.split(",")]

    print("\n" + "-"*60)
    print(f"Mode:        {mode.upper()}")
    print(f"Symbols:     {symbols}")
    print(f"Timeframe:   {timeframe}")
    print(f"Model:       {model}")
    print(f"Exec Mode:   {trading_mode}" + (f" ({loops} loops)" if trading_mode == "single" else " (24/7)"))
    print(f"Risk:        {risk_per_trade * 100:.2f}%")
    print(f"Providers:   {', '.join(providers) if providers else 'all'}")
    print(f"Metrics:     {show_metrics}")
    print(f"RL Metrics:  {show_rl_metrics}")
    print(f"TUI:         {tui}")
    print("-"*60 + "\n")

    confirm = input("Start trading with these settings? [Y/n]: ").strip().lower() != "n"
    if not confirm:
        print("Cancelled.")
        return None

    result = {
        "mode": mode,
        "symbols": symbols,
        "timeframe": timeframe,
        "model": model,
        "trading_mode": {"mode": trading_mode, "loops": loops},
        "risk": {"risk_per_trade": risk_per_trade},
        "advanced": {"show_metrics": show_metrics, "show_rl_metrics": show_rl_metrics},
        "tui": tui
    }
    if providers:
        result["llm_providers"] = {"providers": providers, "provider_count": len(providers)}
    return result


# ---------- Main Entry Point ----------
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


def _launch_opentui_deck(transport: str = "auto") -> bool:
    """Launch the single canonical TUI — the OpenTUI command deck.

    Blocks until the deck exits (deck/monitor/nexus/settings --tui commands).
    Returns True when launched; False when bun or tui_opentui is unavailable
    so the caller can print install instructions instead.
    """
    import shutil
    import subprocess
    bun = shutil.which("bun")
    deck_dir = os.path.join(_root_dir, "tui_opentui")
    if not (bun and os.path.isdir(deck_dir)):
        return False
    env = os.environ.copy()
    env["TRANSPORT"] = env.get("TRANSPORT", transport)
    subprocess.run([bun, "src/index.tsx"], cwd=deck_dir, env=env)
    return True


def apply_paper_triage_default() -> bool:
    """Default NEWS_TRIAGE_MODE to 'off' for paper launches unless explicitly set.

    Auto-mode CPU Laya triage can silently spend ~25 minutes loading the
    checkpoint at boot (payload-audit session 2 masqueraded as a hang). Paper
    sessions default to deterministic rule triage; an explicit NEWS_TRIAGE_MODE
    (shell, .env, or a launcher passthrough like START_DECK's TRIAGE_MODE=full
    → "auto") always wins. Returns True when the default was applied.
    """
    if os.environ.get("NEWS_TRIAGE_MODE", "").strip():
        return False
    os.environ["NEWS_TRIAGE_MODE"] = "off"
    logging.getLogger("main").info(
        "Paper fast-boot default: NEWS_TRIAGE_MODE=off (deterministic triage; "
        "set NEWS_TRIAGE_MODE=auto|laya explicitly to enable Laya)")
    return True


# ---------------------------------------------------------------------------
# External stop flag (PU-0161 follow-up 4)
# ---------------------------------------------------------------------------
#
# PU-0160 measured that the only external stops available to a guard were
# hard console terminates: in a boot-spawned CREATE_NEW_PROCESS_GROUP the
# trader runs with CTRL_C disabled, so the wind-down ladder degrades to
# CTRL_BREAK, which the OS handles by killing the group WITHOUT running the
# trader's cleanup (exit 0xC000013A). CrashRecovery then replays from the
# last journal checkpoint instead of shutting down cleanly.
#
# A stop flag is the missing graceful path: an external guard creates the
# file, and the trader notices it at the top of the next main-loop iteration
# and leaves the loop NORMALLY, so the `finally` block runs the whole
# cleanup path — Guardian stop, supervisor shutdown, orchestrator shutdown,
# durable run close, and the CrashRecovery shutdown snapshot.

def _stop_file_path() -> str:
    """Path of the external stop flag.

    Override with MT5_TRADER_STOP_FILE (absolute, or relative to BASE_DIR).
    Default: <repo>/stop.flag (or fallback trader_stop.flag)
    """
    override = os.environ.get("MT5_TRADER_STOP_FILE", "").strip()
    if override:
        return override if os.path.isabs(override) else os.path.join(BASE_DIR, override)
    candidate = os.path.join(BASE_DIR, "stop.flag")
    if os.path.exists(candidate):
        return candidate
    return os.path.join(BASE_DIR, "trader_stop.flag")


def _stop_file_requested() -> bool:
    """True when an external stop has been requested. Peek-only, no side effects.

    Kept free of side effects so it is safe to call from the sleep/heartbeat
    loop (which only needs to break out early) as well as from the top of the
    main loop (which actually consumes the request).
    """
    try:
        return os.path.exists(_stop_file_path())
    except OSError:
        return False


def _acknowledge_stop_file() -> str:
    """Read and retire the stop flag; return the operator's reason.

    The flag is one-shot. It is RENAMED to '<path>.consumed' rather than
    deleted so the request stays auditable on disk, and so a restarted trader
    does not immediately re-trigger (boot -> stop -> boot loop). An empty
    string means the file carried no reason text.
    """
    path = _stop_file_path()
    reason = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read(200)
        first = raw.strip().splitlines()
        reason = first[0] if first else ""
    except OSError:
        reason = ""
    try:
        os.replace(path, path + ".consumed")
    except OSError as exc:
        logging.getLogger("main").warning(
            "Stop flag %s could not be retired (%s); it will re-trigger on restart",
            path, exc)
    return reason


def _resolve_drain_broker_client(orch: Any) -> Any:
    """Return orch.client only if it is a listable broker module (B2).

    The orchestrator's `client` is usually a connection wrapper (MT5Client)
    with no positions_get/orders_get. Passing it to the gateway used to
    produce empty snapshots and a false APPLIED. Unlistable clients are
    dropped so the gateway falls back to its bound live client; when even
    that cannot list, the control is marked FAILED.
    """
    candidate = getattr(orch, "client", None)
    if (
        candidate is not None
        and hasattr(candidate, "positions_get")
        and hasattr(candidate, "orders_get")
    ):
        return candidate
    return None


def _drain_operator_controls(orch: Any) -> None:
    """Drain and execute pending operator controls from the journal.

    Drains once per pulse and inside sleep loops so emergency flatten and
    kill switch actions are acknowledged and applied within ~1s.
    """
    if orch is None:
        return
    store = getattr(orch, "store", None)
    if store is None or not hasattr(store, "claim_pending_operator_controls"):
        return
    try:
        pending = store.claim_pending_operator_controls()
    except Exception as exc:
        logging.getLogger("main").warning("Failed to claim pending operator controls: %s", exc)
        return

    for ctrl in pending:
        cid = ctrl.get("id")
        action = str(ctrl.get("action", "")).lower().strip()
        actor = str(ctrl.get("actor", "operator"))
        try:
            if action == "flatten":
                if hasattr(store, "set_kill_switch"):
                    store.set_kill_switch(True, f"operator:{actor}")
                gw = getattr(orch, "order_gateway", None)
                if gw is None:
                    from core.order_gateway import get_default_gateway
                    gw = get_default_gateway()
                owned_magics = getattr(orch, "owned_magics", None) or set(BOT_MAGICS)
                mt5_client = _resolve_drain_broker_client(orch)
                outcome = gw.execute_flatten(owned_magics=owned_magics, mt5_client=mt5_client)
                outcome_dict = outcome.to_dict() if hasattr(outcome, "to_dict") else {
                    "closed": getattr(outcome, "closed", len(outcome)),
                    "cancelled": getattr(outcome, "cancelled", 0),
                    "failed": getattr(outcome, "failed", 0),
                    "remaining": getattr(outcome, "remaining", 0),
                    "sweeps": getattr(outcome, "sweeps", 1),
                }
                # B2: APPLIED only when the broker was listed and nothing
                # owned remains. Anything else is FAILED with the counts.
                final_status = "APPLIED" if getattr(outcome, "applied", outcome_dict.get("remaining") == 0 and not outcome_dict.get("listing_error")) else "FAILED"
                store.complete_operator_control(cid, final_status, outcome_dict)
                logging.getLogger("main").warning(
                    "FLATTEN %s by operator:%s: closed=%s cancelled=%s failed=%s remaining=%s sweeps=%s",
                    final_status, actor, outcome_dict.get("closed"), outcome_dict.get("cancelled"),
                    outcome_dict.get("failed"), outcome_dict.get("remaining"), outcome_dict.get("sweeps")
                )

            elif action == "killswitch":
                if hasattr(store, "set_kill_switch"):
                    store.set_kill_switch(True, f"operator:{actor}")
                gw = getattr(orch, "order_gateway", None)
                if gw is None:
                    from core.order_gateway import get_default_gateway
                    gw = get_default_gateway()
                owned_magics = getattr(orch, "owned_magics", None) or set(BOT_MAGICS)
                mt5_client = _resolve_drain_broker_client(orch)
                try:
                    cancelled_results = gw.cancel_all_owned_pending(owned_magics=owned_magics, mt5_client=mt5_client)
                except Exception as cancel_exc:
                    # B2: unlistable broker (BrokerUnlistableError) or any
                    # cancel failure fails the control loudly.
                    store.complete_operator_control(cid, "FAILED", {"error": str(cancel_exc), "kill_switch": True})
                    logging.getLogger("main").warning(
                        "KILLSWITCH FAILED by operator:%s: %s", actor, cancel_exc
                    )
                    continue
                cancelled_count = sum(1 for r in cancelled_results if getattr(r, "success", False))
                failed_count = sum(1 for r in cancelled_results if not getattr(r, "success", False))
                # Verify against the broker: owned pending must actually be gone.
                remaining, remaining_error = gw.count_owned_pending(mt5_client, owned_magics)
                if remaining_error is not None:
                    store.complete_operator_control(cid, "FAILED", {"cancelled": cancelled_count, "kill_switch": True, "error": remaining_error})
                    logging.getLogger("main").warning(
                        "KILLSWITCH FAILED by operator:%s: %s", actor, remaining_error
                    )
                elif remaining > 0 or failed_count > 0:
                    store.complete_operator_control(cid, "FAILED", {"cancelled": cancelled_count, "failed": failed_count, "remaining": remaining, "kill_switch": True})
                    logging.getLogger("main").warning(
                        "KILLSWITCH FAILED by operator:%s: cancelled %d, failed %d, remaining %d", actor, cancelled_count, failed_count, remaining
                    )
                else:
                    store.complete_operator_control(cid, "APPLIED", {"cancelled": cancelled_count, "kill_switch": True})
                    logging.getLogger("main").warning(
                        "KILLSWITCH applied by operator:%s: cancelled %d pending orders", actor, cancelled_count
                    )

            elif action == "killswitch_off":
                if hasattr(store, "set_kill_switch"):
                    store.set_kill_switch(False, f"operator:{actor}")
                store.complete_operator_control(cid, "APPLIED", {"kill_switch": False})
                logging.getLogger("main").info("KILLSWITCH_OFF applied by operator:%s", actor)

            elif action == "pause":
                if hasattr(store, "set_autonomy_paused"):
                    store.set_autonomy_paused(True, actor=actor, reason=f"operator:{actor}")
                store.complete_operator_control(cid, "APPLIED", {"paused": True})
                logging.getLogger("main").info("PAUSE applied by operator:%s", actor)

            elif action == "resume":
                if hasattr(store, "set_autonomy_paused"):
                    store.set_autonomy_paused(False, actor=actor)
                store.complete_operator_control(cid, "APPLIED", {"paused": False})
                logging.getLogger("main").info("RESUME applied by operator:%s", actor)

            else:
                store.complete_operator_control(cid, "FAILED", {"error": f"unrecognized action: {action}"})

        except Exception as exc:
            logging.getLogger("main").exception("Error applying operator control %s (%s): %s", cid, action, exc)
            try:
                store.complete_operator_control(cid, "FAILED", {"error": str(exc)})
            except Exception:
                pass


def main() -> None:
    """Main entry point for the MT5 LLM Trader"""
    global mt5, pd, np
    
    # Initialize configuration and logging
    cfg = Config.from_sources()
    setup_logging(cfg.log_level)
    log = logging.getLogger("main")
    
    # Check if no arguments provided -> Integrated entry: OpenTUI deck (if bun present) or InteractiveMenu fallback
    # Design: main entry now exposes deck + secure settings via OpenTUI (tui_opentui) with fallback Rich/questionary
    if len(sys.argv) == 1:
        # Prefer OpenTUI deck when explicitly requested via env or when bun is available and user picks it
        # If LLM_TRADER_PREFER_DECK=1 or bun + tui_opentui present, offer choice before falling back to InteractiveMenu
        try:
            import shutil as _sh
            _has_deck = bool(_sh.which("bun")) and os.path.isdir(os.path.join(_root_dir, "tui_opentui"))
            _prefer = os.getenv("LLM_TRADER_PREFER_DECK", "auto").lower()
            if _has_deck and _prefer in ("1","true","yes","auto"):
                if _prefer != "auto":
                    # auto-launch deck directly
                    import subprocess as _sp
                    env2 = os.environ.copy(); env2["TRANSPORT"] = env2.get("TRANSPORT","mcp")
                    print("Launching OpenTUI Command Deck (integrated MT5 via MCP, Settings→Accounts secure entry). Set LLM_TRADER_PREFER_DECK=0 to skip.")
                    _sp.run([_sh.which("bun"), "src/index.tsx"], cwd=os.path.join(_root_dir, "tui_opentui"), env=env2)
                    return
                # auto: ask user with rich fallback
                try:
                    import questionary as _q
                    _choice = _q.select(
                        "Main entry — choose interface:",
                        choices=[
                            _q.Choice("□  OpenTUI Command Deck (React, MT5 via MCP, Settings→Accounts secure masked) [Recommended]", value="deck"),
                            _q.Choice("≡ Classic Interactive Menu (questionary, Python)", value="classic"),
                            _q.Choice("□  Secure Settings (accounts / MCP login / risk / LLM)", value="settings"),
                        ],
                    ).ask()
                    if _choice == "deck":
                        import subprocess as _sp
                        env2 = os.environ.copy(); env2["TRANSPORT"] = env2.get("TRANSPORT","mcp")
                        _sp.run([_sh.which("bun"), "src/index.tsx"], cwd=os.path.join(_root_dir, "tui_opentui"), env=env2)
                        return
                    elif _choice == "settings":
                        # Settings live in the OpenTUI deck (SYSTEM CONFIG screen)
                        print(" Settings are managed from the OpenTUI deck (SYSTEM CONFIG screen): python mt5_trader.py deck")
                        # after settings, re-enter classic menu
                        from ui.interactive_menu import InteractiveMenu as _IM2
                        menu2 = _IM2()
                        config2 = menu2.run()
                        if config2:
                            sys.argv.extend(["llm-trade", "--symbol", config2["symbols"], "--timeframe", config2["timeframe"]])
                            sys.argv.extend(["--model", config2["model"]])
                            sys.argv.extend(["--risk-per-trade", str(config2["risk"]["risk_per_trade"])])
                            if config2["trading_mode"]["mode"] == "continuous":
                                sys.argv.append("--continuous")
                            else:
                                sys.argv.extend(["--loops", str(config2["trading_mode"]["loops"])])
                            if config2["mode"] == "paper": sys.argv.append("--paper")
                            elif config2["mode"] == "demo": sys.argv.append("--demo")
                            elif config2["mode"] == "live": sys.argv.append("--live")
                            if config2["advanced"]["show_metrics"]: sys.argv.append("--show-metrics")
                            if config2["advanced"].get("show_rl_metrics"): sys.argv.append("--show-rl-metrics")
                            if config2.get("tui"): sys.argv.append("--tui")
                            providers = config2.get("llm_config", {}).get("providers", [])
                            if providers:
                                sys.argv.extend(["--llm-providers", ",".join(providers)])
                                provider_models = config2.get("llm_config", {}).get("provider_models", {})
                                if provider_models:
                                    models = [f"{p}:{provider_models[p]}" for p in providers if p in provider_models]
                                    if models: sys.argv.extend(["--llm-models", ",".join(models)])
                        return
                except Exception as exc:
                    logging.getLogger("mt5_trader").debug("metrics export failed: %s", exc)
        except Exception as exc:
            logging.getLogger("mt5_trader").debug("metrics export failed: %s", exc)
        try:
            from ui.interactive_menu import InteractiveMenu
            menu = InteractiveMenu()
            config = menu.run()
            if config:
                # Map config to args-like structure
                sys.argv.extend(["llm-trade", "--symbol", config["symbols"], "--timeframe", config["timeframe"]])
                sys.argv.extend(["--model", config["model"]])
                sys.argv.extend(["--risk-per-trade", str(config["risk"]["risk_per_trade"])])
                
                if config["trading_mode"]["mode"] == "continuous":
                    sys.argv.append("--continuous")
                else:
                    sys.argv.extend(["--loops", str(config["trading_mode"]["loops"])])
                
                if config["mode"] == "paper":
                    sys.argv.append("--paper")
                elif config["mode"] == "demo":
                    sys.argv.append("--demo")
                elif config["mode"] == "live":
                    sys.argv.append("--live")
                    
                if config["advanced"]["show_metrics"]:
                    sys.argv.append("--show-metrics")
                if config["advanced"].get("show_rl_metrics"):
                    sys.argv.append("--show-rl-metrics")
                if config.get("tui"):
                    sys.argv.append("--tui")
                providers = config.get("llm_config", {}).get("providers", [])
                if providers:
                    sys.argv.extend(["--llm-providers", ",".join(providers)])
                    provider_models = config.get("llm_config", {}).get("provider_models", {})
                    if provider_models:
                        models = [f"{p}:{provider_models[p]}" for p in providers if p in provider_models]
                        if models:
                            sys.argv.extend(["--llm-models", ",".join(models)])
                    
        except ImportError:
            print("Interactive menu not available. Please install 'questionary'.")
        except Exception as e:
            is_console_error = "NoConsoleScreenBufferError" in str(type(e).__name__) or "EOFError" in str(type(e).__name__)
            stdin_ok = sys.stdin.isatty() if hasattr(sys.stdin, 'isatty') else False
            stdout_ok = sys.stdout.isatty() if hasattr(sys.stdout, 'isatty') else False
            is_interactive = stdin_ok and stdout_ok
            if is_console_error or not is_interactive:
                print("Console not detected or not interactive. Using default settings...")
                config = _run_simple_menu(interactive=is_interactive)
                if config:
                    sys.argv.extend(["llm-trade", "--symbol", config["symbols"], "--timeframe", config["timeframe"]])
                    sys.argv.extend(["--model", config["model"]])
                    sys.argv.extend(["--risk-per-trade", str(config["risk"]["risk_per_trade"])])
                    if config["trading_mode"]["mode"] == "continuous":
                        sys.argv.append("--continuous")
                    else:
                        sys.argv.extend(["--loops", str(config["trading_mode"]["loops"])])
                    if config["mode"] == "paper":
                        sys.argv.append("--paper")
                    elif config["mode"] == "demo":
                        sys.argv.append("--demo")
                    elif config["mode"] == "live":
                        sys.argv.append("--live")
                    if config["advanced"].get("show_metrics"):
                        sys.argv.append("--show-metrics")
                    if config["advanced"].get("show_rl_metrics"):
                        sys.argv.append("--show-rl-metrics")
                    if config.get("tui"):
                        sys.argv.append("--tui")
                    providers = config.get("llm_providers", {}).get("providers", [])
                    if providers:
                        sys.argv.extend(["--llm-providers", ",".join(providers)])
                        provider_models = config.get("llm_config", {}).get("provider_models", {})
                        if provider_models:
                            models = [f"{p}:{provider_models[p]}" for p in providers if p in provider_models]
                            if models:
                                sys.argv.extend(["--llm-models", ",".join(models)])
            else:
                raise

    ap = argparse.ArgumentParser(description="MT5 LLM Trader - Automated trading with LLM decision-making")
    sub = ap.add_subparsers(dest="cmd", help="Available commands")
    
    # Data command
    ap_data = sub.add_parser("data", help="Fetch OHLC and indicators")
    ap_data.add_argument("--symbol", required=True)
    ap_data.add_argument("--timeframe", required=True)
    ap_data.add_argument("--count", type=int)
    ap_data.add_argument("--start")
    ap_data.add_argument("--end")
    ap_data.add_argument("--indicators", nargs="*", default=[])
    ap_data.add_argument("--paper", action="store_true")

    ap_mo = sub.add_parser("market-order", help="Disabled direct market entry; use pending-order or emergency exit")
    ap_mo.add_argument("--symbol", required=True)
    ap_mo.add_argument("--side", choices=["buy", "sell"], required=True)
    ap_mo.add_argument("--volume", type=float)
    ap_mo.add_argument("--sl", type=float)
    ap_mo.add_argument("--tp", type=float)
    ap_mo.add_argument("--comment", default="mt5_trader")
    ap_mo.add_argument("--live", action="store_true")
    ap_mo.add_argument("--yes", action="store_true")
    ap_mo.add_argument("--paper", action="store_true")

    ap_po = sub.add_parser("pending-order", help="Place pending order")
    ap_po.add_argument("--symbol", required=True)
    ap_po.add_argument("--type", choices=["buy_limit", "sell_limit", "buy_stop", "sell_stop"], required=True)
    ap_po.add_argument("--price", type=float, required=True)
    ap_po.add_argument("--volume", type=float)
    ap_po.add_argument("--sl", type=float)
    ap_po.add_argument("--tp", type=float)
    ap_po.add_argument("--comment", default="mt5_trader")
    ap_po.add_argument("--live", action="store_true")
    ap_po.add_argument("--yes", action="store_true")
    ap_po.add_argument("--paper", action="store_true")

    ap_mod = sub.add_parser("modify-order", help="Modify SL/TP of open positions")
    ap_mod.add_argument("--symbol")
    ap_mod.add_argument("--sl", type=float)
    ap_mod.add_argument("--tp", type=float)
    ap_mod.add_argument("--live", action="store_true")
    ap_mod.add_argument("--yes", action="store_true")
    ap_mod.add_argument("--paper", action="store_true")

    ap_cancel = sub.add_parser("cancel-order", help="Cancel pending order")
    ap_cancel.add_argument("--order-id", type=int, required=True)
    ap_cancel.add_argument("--live", action="store_true")
    ap_cancel.add_argument("--yes", action="store_true")
    ap_cancel.add_argument("--paper", action="store_true")

    sub.add_parser("account-info", help="Show account info")
    ap_preflight = sub.add_parser("broker-preflight", help="Capture and report current broker/account symbol constraints")
    ap_preflight.add_argument("--symbols", required=True, help="Comma-separated broker symbols, e.g. XAUUSD,EURUSD")
    sub.add_parser("diagnose", help="Run terminal diagnostics")
    sub_pos = sub.add_parser("positions", help="Show positions")
    sub.add_parser("orders", help="Show open orders")
    ap_hist = sub.add_parser("history", help="Show trading history counts")
    ap_hist.add_argument("--start", required=True)
    ap_hist.add_argument("--end", required=True)

    ap_bench = sub.add_parser("benchmark", help="Benchmark data and indicators")
    ap_bench.add_argument("--symbol", required=True)
    ap_bench.add_argument("--timeframe", required=True)
    ap_bench.add_argument("--count", type=int, default=2000)
    ap_bench.add_argument("--paper", action="store_true")

    ap_ngb = sub.add_parser("news-gate-backtest",
                            help="Backtest the news trading gate (with-gate vs without-gate)")
    ap_ngb.add_argument("--symbol", default="EURUSD")
    ap_ngb.add_argument("--timeframe", default="M15")
    ap_ngb.add_argument("--days", type=int, default=90, dest="days_back",
                        help="Days of MT5 history to use (ignored with --synthetic)")
    ap_ngb.add_argument("--synthetic", action="store_true",
                        help="Run on synthetic trades/events instead of MT5 history")

    ap_bt = sub.add_parser("backtest", help="Dual-engine backtest (VectorBT screening + MT5 Broker Replay)")
    ap_bt.add_argument("--symbol", default="EURUSD", help="Trading symbol")
    ap_bt.add_argument("--timeframe", default="M15", help="Timeframe (M1...D1)")
    ap_bt.add_argument("--count", type=int, default=500, help="Number of bars to backtest")
    ap_bt.add_argument("--engine", choices=["vectorbt", "mt5", "hybrid"], default="hybrid",
                       help="Backtest engine: vectorbt, mt5, or hybrid")
    ap_bt.add_argument("--fast", type=int, default=10, help="Fast EMA period for crossover benchmark")
    ap_bt.add_argument("--slow", type=int, default=30, help="Slow EMA period for crossover benchmark")
    ap_bt.add_argument("--sweep", action="store_true", help="Run parameter sweep grid")
    ap_bt.add_argument("--sl-pips", type=float, default=25.0, help="Stop loss in pips for broker replay")
    ap_bt.add_argument("--tp-pips", type=float, default=50.0, help="Take profit in pips for broker replay")
    ap_bt.add_argument("--strategy", default="crossover", help="Strategy name/id to evaluate")
    ap_bt.add_argument("--paper", action="store_true", help="Run offline simulation with synthetic price data")

    ap_web = sub.add_parser("web-research", help="Autonomous web research & academic paper extraction (SSRN, ArXiv, Web, Fed)")
    ap_web.add_argument("--query", required=True, help="Research query or topic to search")
    ap_web.add_argument("--source", choices=["all", "ssrn", "arxiv", "web", "fed"], default="all", help="Source to query")
    ap_web.add_argument("--count", type=int, default=3, help="Max results per source")

    ap_llm = sub.add_parser("llm-trade", help="Run LLM Trading Orchestrator")
    ap_llm.add_argument("--symbol", required=True)
    ap_llm.add_argument("--timeframe", default="AUTO", help="Timeframe (AUTO, M1...D1)")
    ap_llm.add_argument("--risk-per-trade", type=float, default=0.01)
    mode_group = ap_llm.add_mutually_exclusive_group()
    mode_group.add_argument("--paper", action="store_true", help="Run in internal simulation mode")
    mode_group.add_argument("--demo", action="store_true", help="Run on Demo MT5 account (Live execution without confirmation)")
    mode_group.add_argument("--live", action="store_true", help="Run on Live MT5 account (Requires confirmation)")
    ap_llm.add_argument("--yes", action="store_true")
    ap_llm.add_argument("--loops", type=int, default=1, help="Number of cycles to run")
    ap_llm.add_argument("--api-sync", type=str, help="URL for GUI API synchronization")
    ap_llm.add_argument("--show-metrics", action="store_true", help="Show detailed performance metrics at end")
    ap_llm.add_argument("--show-rl-metrics", action="store_true", help="Show RL training metrics at end")
    ap_llm.add_argument("--select-model", action="store_true", help="Interactively select LLM model before trading")
    ap_llm.add_argument("--model", type=str, help="LLM model to use (e.g., 'meta/llama-3.2-11b-vision-instruct')")
    ap_llm.add_argument("--continuous", action="store_true", help="Run continuously (infinite loop)")
    ap_llm.add_argument("--poll-interval", type=float, default=5.0,
                        help="Seconds between market/position pulses; LLM calls remain candle/event gated (default: 5)")
    ap_llm.add_argument("--strategies", choices=["swing", "scalp", "both"], default="both", help="Run independent candle-driven swing/scalp engines")
    ap_llm.add_argument("--interactive", action="store_true", help="Launch interactive menu for configuration")
    ap_llm.add_argument("--tui", action="store_true", help="Attach the OpenTUI command deck (single canonical TUI) and mirror state to the API server (see --api-sync)")
    ap_llm.add_argument("--llm-providers", type=str, help="Comma-separated LLM providers/keys to use (e.g., openrouter,cerebras,nvidia_1,nvidia_2)")
    ap_llm.add_argument("--llm-provider-count", type=int, default=0, help="Max number of LLM providers/keys to use (0 = all)")
    ap_llm.add_argument("--no-dashboard", action="store_true", help="Disable auto-generation of alpha health dashboard after each cycle")
    ap_llm.add_argument("--no-pipeline-v2", action="store_true", help="Disable Pipeline V2 (stage-based telemetry); use legacy execute_cycle directly")
    ap_llm.add_argument("--llm-models", type=str, help="Comma-separated provider:model pairs (e.g., openrouter:mistralai/mistral-nemo,nvidia_nim:meta/llama-3.2-11b-vision-instruct)")
    ap_llm.add_argument("--debug", action="store_true", help="Enable DEBUG logging for trading-critical modules (orchestrator, crash detector, walk-forward, execution, risk) without flooding the console with all module output")
    ap_llm.add_argument("--harness", action="store_true", help="Run everything as Harness Plugins via TradingContext + CapabilitySandbox (surgical plugin mode, default off for compat)")
    ap_llm.add_argument("--profile", choices=["paper", "live", "research", "backtest"], default=None,
                        help="Mount named plugin profile (paper/live/research/backtest). Auto-maps from --paper/--live flags if not set.")
    ap_llm.add_argument("--list-profiles", action="store_true", help="List available plugin profiles and exit")
    ap_llm.add_argument("--bounded-autonomy", action="store_true", help="Enable Bounded-Autonomy LLM Agent Pipeline (NZE 2026: R ∘ V_LLM ∘ S)")
    ap_llm.add_argument("--execution-engine", choices=["orchestrator", "graph"], default="orchestrator",
                        help="Primary decision engine: 'orchestrator' (legacy multi-strategy) or 'graph' (modular LangGraph Deliberative Engine)")

    # ── Demo Trading Orchestrator (live training pipeline) ───────────────────
    ap_demo = sub.add_parser("demo-run", help="Autonomous demo trading with live training pipeline")
    ap_demo.add_argument("--symbol", default="EURUSD", help="Symbol to trade")
    ap_demo.add_argument("--timeframe", default="M15", help="Timeframe")
    ap_demo.add_argument("--continuous", action="store_true", help="Run continuously")
    ap_demo.add_argument("--loops", type=int, default=100, help="Number of cycles")
    ap_demo.add_argument("--poll-interval", type=float, default=30.0, help="Seconds between cycles")
    ap_demo.add_argument("--validate-every", type=int, default=100, help="Run validation every N cycles")
    ap_demo.add_argument("--adapt-every", type=int, default=50, help="Run meta-adaptation every N cycles")

    # Account Management Commands (secure)
    parser_accounts = sub.add_parser("accounts", help="Manage MT5 accounts (secure)")
    parser_accounts.add_argument("action", choices=["list", "select", "add", "secure-add", "secure-login", "remove", "deck"], help="Action to perform")
    parser_accounts.add_argument("--name", help="Account name (for select/add)")
    parser_accounts.add_argument("--login", type=int, help="Login ID (for add)")
    parser_accounts.add_argument("--password-env", default="MT5_PASSWORD",
                                 help="Environment variable containing the password (never pass the password itself)")
    parser_accounts.add_argument("--password", help=argparse.SUPPRESS)
    parser_accounts.add_argument("--server", help="Server (for add)")
    parser_accounts.add_argument("--type", choices=["DEMO", "LIVE"], help="Account type (for add)")
    parser_accounts.add_argument("--prompt", action="store_true", help="Prompt for password securely (masked, via keyring/encrypted file)")
    parser_accounts.add_argument("--test-mcp", action="store_true", help="After saving, test login via MCP / MT5Client")
    # Deck launcher (OpenTUI command deck)
    sub.add_parser("deck", help="Launch OpenTUI Command Deck (tui_opentui) — integrated MT5 via MCP, Settings→Accounts secure entry")
    # Unified settings CLI (OpenTUI-inspired) for Python fallback without Node
    parser_settings = sub.add_parser("settings", help="Open settings TUI — accounts, MCP login, risk & LLM providers (secure, masked)")
    parser_settings.add_argument("--section", choices=["accounts", "mcp", "risk", "llm", "all"], default="all", help="Which settings pane to open")
    parser_settings.add_argument("--tui", action="store_true", help="Open the OpenTUI deck (SYSTEM CONFIG screen) for settings")

    parser_kill = sub.add_parser("kill-switch", help="View or change the persistent trading kill switch")
    parser_kill.add_argument("action", choices=["status", "enable", "disable"])
    parser_kill.add_argument("--reason", default="manual operator action")
    parser_kill.add_argument("--journal-path", default="trading_state.db")

    parser_latch = sub.add_parser("latch", help="View or clear the D9 news kill-switch latch")
    parser_latch.add_argument("--status", action="store_true", help="Display latch status")
    parser_latch.add_argument("--clear", action="store_true", help="Operator clear the latch")
    parser_latch.add_argument("--reason", default="", help="Reason for clearing the latch")
    parser_latch.add_argument("--confirm", default="", help="Type CLEAR_LATCH to confirm clear")
    parser_latch.add_argument("--actor", default="operator", help="Actor recording the clear")
    parser_latch.add_argument("--journal-path", default="trading_state.db")

    parser_flatten = sub.add_parser("flatten", help="Offline flatten owned positions and cancel owned pending orders")
    parser_flatten.add_argument("--owned", action="store_true", help="Flatten owned positions and orders only (default: owned-only; manual positions are never touched)")
    parser_flatten.add_argument("--confirm", required=True, help="Typed confirmation: must be exactly FLATTEN")
    parser_flatten.add_argument("--journal-path", default="trading_state.db")
    parser_flatten.add_argument("--paper", action="store_true", help="Flatten paper broker positions")

    parser_provider_circuit = sub.add_parser("provider-circuit", help="Inspect or manually reset the persistent LLM provider circuit")
    parser_provider_circuit.add_argument("action", choices=["status", "reset"])
    parser_provider_circuit.add_argument("--reason", default="manual operator reset")
    parser_provider_circuit.add_argument("--operator", default="operator")
    parser_provider_circuit.add_argument("--journal-path", default="trading_state.db")

    parser_journal = sub.add_parser("journal", help="Inspect durable trade memory and recurring patterns")
    parser_journal.add_argument("action", choices=["patterns", "playbook", "integrity", "metrics", "dashboard-status", "report", "audit-export", "audit-retention-status", "migrate", "migrate-states", "states-status", "operations", "recover", "memory-status", "memory-metrics", "memory-maintenance", "weekly-memory", "monthly-memory", "working-memory-status", "working-memory-context", "working-memory-compact", "setup-labels", "research-status", "validation-runs", "promotion-package", "promotion-verify", "historical-status", "macro-extraction-status", "policy-status", "policy-candidate", "policy-approve", "broker-preflight-status", "auth-status", "readiness-status", "incident-drills", "forward-test-status", "dataset-status", "sample-gate-status", "baseline-status", "supervised-status", "model-family-status", "model-status", "model-register", "model-review", "model-drift", "stress-status", "bandit-status", "semantic-status", "alerts", "alerts-enqueue", "alerts-status", "alerts-claim", "alerts-ack", "alerts-recover", "alerts-deliver", "lifecycle-audit", "lifecycle-board",
"strategy-status", "strategy-transition", "strategy-unfreeze", "strategy-retire", "strategy-tune", "strategy-evaluate", "strategy-acknowledge", "macro-prune", "model-activation-status", "research-influence-status", "research-influence-approve", "research-influence-revoke", "legacy-status", "legacy-quarantine", "security-status", "live-approval", "backup-status", "backup-create", "backup-restore", "backup-prune", "encrypted-backup-status", "encrypted-backup-create", "encrypted-backup-restore", "restart-status", "scope-status", "scope-init", "execution-reviews", "resolve-execution-review", "reconciliation", "reconciliation-run", "db-maintenance", "housekeeping"])
    parser_journal.add_argument("--start", default="", help="UTC ISO start timestamp for reconciliation-run")
    parser_journal.add_argument("--end", default="", help="UTC ISO end timestamp for reconciliation-run")
    parser_journal.add_argument("--paper", action="store_true", help="Reconcile paper broker deals instead of live MT5")
    parser_journal.add_argument("--backup-dir", default="backups", help="Target directory for SQLite maintenance backup")
    parser_journal.add_argument("--source", default="trading_rewards.json", help="Legacy JSON journal to import")
    parser_journal.add_argument("--intent-key", help="Order-intent key for execution-review resolution")
    parser_journal.add_argument("--review-status", choices=["ACKNOWLEDGED", "RESOLVED", "QUARANTINED"])
    parser_journal.add_argument("--note", default="")
    parser_journal.add_argument("--operator", default="operator")
    parser_journal.add_argument("--model-key")
    parser_journal.add_argument("--model-version")
    parser_journal.add_argument("--model-kind", default="supervised")
    parser_journal.add_argument("--feature-schema-version")
    parser_journal.add_argument("--training-data-hash")
    parser_journal.add_argument("--metrics-json", default="{}")
    parser_journal.add_argument("--artifact-uri", default="")
    parser_journal.add_argument("--model-id", type=int)
    parser_journal.add_argument("--model-review-status", choices=["APPROVED", "REJECTED", "RETIRED", "ROLLED_BACK"])
    parser_journal.add_argument("--backup-path", default="")
    parser_journal.add_argument("--backup-destination", default="")
    parser_journal.add_argument("--backup-directory", default=".", help="Directory for backup-prune (must be explicit and non-root)")
    parser_journal.add_argument("--keep-backups", type=int, default=2)
    parser_journal.add_argument("--apply", action="store_true", help="Apply backup-prune deletion; default is a dry run")
    parser_journal.add_argument("--housekeeping-report-age-days", type=int, default=30,
                                help="Age gate for pruning stale remediation/housekeeping JSON reports (default 30)")
    parser_journal.add_argument("--backup-passphrase-env", default="TRADING_BACKUP_PASSPHRASE",
                                help="Environment variable containing the encrypted-backup passphrase (never pass it on the CLI)")
    parser_journal.add_argument("--overwrite", action="store_true")
    parser_journal.add_argument("--journal-mode", choices=["PAPER", "DEMO", "LIVE", "RESEARCH", "LEGACY_UNSCOPED"])
    parser_journal.add_argument("--account-fingerprint", default="")
    parser_journal.add_argument("--journal-path", default="trading_state.db")
    parser_journal.add_argument("--older-than-days", type=int, default=30)
    parser_journal.add_argument("--backtest-report", default="")
    parser_journal.add_argument("--promotion-output", default="", help="Optional JSON path for a fail-closed validation package")
    parser_journal.add_argument("--promotion-package", default="", help="JSON promotion package path for promotion-verify")
    parser_journal.add_argument("--ppo-report", default="", help="Offline PPO training report/artifact JSON for promotion evidence")
    parser_journal.add_argument("--meta-rl-report", default="", help="Offline Meta-RL training report/artifact JSON for promotion evidence")
    parser_journal.add_argument("--audit-output", default="journal_audit.json", help="Output JSON path for audit-export")
    parser_journal.add_argument("--demo-journal", default="trading_state.db")
    parser_journal.add_argument("--minimum-demo-sample", type=int, default=100)
    parser_journal.add_argument("--approval-action", choices=["status", "approve", "revoke", "verify"], default="status")
    parser_journal.add_argument("--confirmation", default="")
    parser_journal.add_argument("--evidence", action="append", default=[])
    parser_journal.add_argument("--risk-fraction-cap", type=float, default=0.001)
    parser_journal.add_argument("--expires-hours", type=float, default=24.0)
    parser_journal.add_argument("--policy-symbol", default="", help="Symbol for execution-policy candidate/approval")
    parser_journal.add_argument("--policy-json", default="", help="JSON file containing a completed execution-policy candidate")
    parser_journal.add_argument("--ticket", type=int, help="Open trade ticket for legacy quarantine")
    parser_journal.add_argument("--label", default="", help="Filter deterministic setup/order-intent labels")
    parser_journal.add_argument("--week-start", default="", help="UTC ISO Monday date for weekly memory summary")
    parser_journal.add_argument("--memory-namespace", default="", help="Working-memory namespace for context/status/compaction")
    parser_journal.add_argument("--include-namespace", action="append", default=[], help="Additional working-memory namespace to merge")
    parser_journal.add_argument("--context-as-of", default="", help="UTC ISO cutoff for working-memory context")
    parser_journal.add_argument("--memory-limit", type=int, default=40)
    parser_journal.add_argument("--memory-max-chars", type=int, default=12000)
    parser_journal.add_argument("--memory-max-active", type=int, default=40)
    parser_journal.add_argument("--run-kind", default="", help="Filter persisted research validation runs")
    parser_journal.add_argument("--run-limit", type=int, default=20, help="Maximum persisted research runs to display")
    parser_journal.add_argument("--alert-channel", default="operator", help="Transport-neutral alert outbox channel")
    parser_journal.add_argument("--alert-limit", type=int, default=20, help="Maximum alert outbox rows to claim")
    parser_journal.add_argument("--alert-id", type=int, help="Alert outbox row id for alerts-ack")
    parser_journal.add_argument("--alert-delivered", action="store_true", help="Mark claimed alert as delivered")
    parser_journal.add_argument("--alert-error", default="", help="Delivery error for alerts-ack retry/block")
    parser_journal.add_argument("--alert-transport", default="file", choices=["file", "webhook"],
                                help="Transport for alerts-deliver (fail closed without configuration)")
    parser_journal.add_argument("--alert-file", default="", help="JSONL output path for alerts-deliver file transport")
    parser_journal.add_argument("--setup-id", default="", help="Registered setup id for strategy lifecycle actions")
    parser_journal.add_argument("--to-state", default="", help="Target lifecycle state for strategy-transition")
    parser_journal.add_argument("--influence-kind", default="all", help="Adaptive model scope for research-influence approval")
    parser_journal.add_argument("--influence-evidence-hash", default="", help="SHA-256 evidence digest for research-influence approval")
    parser_journal.add_argument("--influence-oos-validated", action="store_true", help="Record explicit out-of-sample validation")
    parser_journal.add_argument("--influence-expires-hours", type=float, default=24.0)
    parser_journal.add_argument("--max-age-days", type=int, default=90, help="Maximum age in days for active causal edges before pruning")
    parser_journal.add_argument("--min-confidence", type=float, default=0.20, help="Minimum confidence threshold for active causal edges before pruning")

    parser_core = sub.add_parser("memory-core", help="Propose/review audited L3 core memory (never an execution permission)")
    parser_core.add_argument("action", choices=["list", "propose", "approve", "retire"])
    parser_core.add_argument("--namespace", choices=["TRADING_PERSONALITY", "RISK_POLICY", "STRATEGY_RULE", "STABLE_LESSON", "SKILL", "WIKI", "ACCESS_POLICY"])
    parser_core.add_argument("--key")
    parser_core.add_argument("--content")
    parser_core.add_argument("--source-id", action="append", default=[])
    parser_core.add_argument("--confidence", type=float, default=1.0)
    parser_core.add_argument("--id", type=int)
    parser_core.add_argument("--reviewer", default="operator")
    parser_core.add_argument("--note", default="", help="Audited note/reason for core memory proposal or retirement")
    parser_core.add_argument("--journal-path", default="trading_state.db")

    parser_memory_review = sub.add_parser("memory-review", help="Review source-linked memory candidates")
    parser_memory_review.add_argument("kind", choices=["queue", "narrative", "verified", "scenario", "setup-label", "core"])
    parser_memory_review.add_argument("action", choices=["list", "approve", "retire"])
    parser_memory_review.add_argument("--id", type=int)
    parser_memory_review.add_argument("--scenario-key", default="")
    parser_memory_review.add_argument("--subject-key", default="")
    parser_memory_review.add_argument("--reviewer", default="operator")
    parser_memory_review.add_argument("--journal-path", default="trading_state.db")
    parser_memory_review.add_argument("--limit", type=int, default=20, help="Max items per kind for queue list")
    
    # Real Test Command
    ap_test = sub.add_parser("test-live", help="Run a real connection and execution test (Safe)")
    ap_test.add_argument("--symbol", default="EURUSD", help="Symbol to test with")

    # Benchmark LLM Command
    ap_bench_llm = sub.add_parser("benchmark-llm", help="Run performance benchmark with CoT display")
    ap_bench_llm.add_argument("--symbol", default="EURUSD")
    ap_bench_llm.add_argument("--timeframe", default="M1")
    ap_bench_llm.add_argument("--loops", type=int, default=5)
    ap_bench_llm.add_argument("--paper", action="store_true")
    ap_bench_llm.add_argument("--tui", action="store_true", help="Attach the OpenTUI command deck (single canonical TUI)")

    ap_doctor = sub.add_parser("doctor", help="Run system prerequisite diagnostics")
    ap_doctor.add_argument("--json", action="store_true", help="Output as JSON")

    ap_monitor = sub.add_parser("monitor", help="Launch real-time log monitor TUI (tails mt5_trader.log, surfaces WARNING+)")
    ap_monitor.add_argument("--file", "-f", default="mt5_trader.log", help="Log file to monitor")
    ap_monitor.add_argument("--min-level", "-l", default="WARNING",
                            choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                            help="Minimum log level to display")
    ap_monitor.add_argument("--history", type=int, default=200, help="Max entries in feed buffer")

    ap_nexus = sub.add_parser("nexus", help="Launch the unified QUANT NEXUS TUI — all UAQP agents, live feed, causal graph")
    ap_nexus.add_argument("--live", action="store_true", help="Also start the trading loop in a subprocess")
    ap_nexus.add_argument("--symbol", default="EURUSD", help="Symbol to trade (default: EURUSD)")
    ap_nexus.add_argument("--loops", type=int, default=0, help="Number of trading loops (0=infinite)")

    ap_profiles = sub.add_parser("profiles", help="Show plugin profile details and service counts")
    ap_profiles.add_argument("name", nargs="?", choices=["paper", "live", "research", "backtest"],
                             help="Show details for a specific profile (omit to list all)")
    ap_profiles.add_argument("--mount", action="store_true",
                             help="Actually mount the profile and report live service counts")
    ap_profiles.add_argument("--json", action="store_true", help="Output as JSON")

    ap_demo_samples = sub.add_parser("demo-samples", help="Inject synthetic labelled samples for pipeline testing (not for live trading)")
    ap_demo_samples.add_argument("--count", type=int, default=120, help="Total synthetic trades to inject")
    ap_demo_samples.add_argument("--path", default="trading_state.db", help="Journal database path")

    ap_scrub = sub.add_parser("scrub-history", help="Remove risky credential files from Git history (dry run by default)")
    ap_scrub.add_argument("--apply", action="store_true", help="Actually rewrite Git history (requires review)")
    ap_scrub.add_argument("--path", default=".", help="Repository root path")

    ap_macro = sub.add_parser("macro", help="Autonomous macrofundamental pipeline, nowcasting, case library & research")
    ap_macro.add_argument("action", choices=["analyze", "status", "nowcast", "ratios", "case-match", "learn-case", "case-library", "case-info", "research-case", "supply-shock"], default="analyze", nargs="?")
    ap_macro.add_argument("--symbol", default="EURUSD", help="Symbol to analyze")
    ap_macro.add_argument("--domain", default="MARITIME_CHOKEPOINT", help="Domain for case-match or case-library filter")
    ap_macro.add_argument("--case", default="", help="Case ID for case-info (e.g. case_a_hormuz_blockade)")
    ap_macro.add_argument("--search", default="", help="Search query string for case-library")
    ap_macro.add_argument("--prompt", default="", help="Research prompt or news headline for research-case / supply-shock")
    ap_macro.add_argument("--file", default="", help="Path to JSON or text file for learn-case / research-case")

    ap_res_sim = sub.add_parser("research-sim", help="Quantitative simulation & risk parity")
    ap_res_sim.add_argument("action", choices=["simulate-ar1", "risk-parity", "regime-mc"], default="simulate-ar1", nargs="?")
    ap_res_sim.add_argument("--symbol", default="EURUSD", help="Symbol for simulation")
    ap_res_sim.add_argument("--paths", type=int, default=1000, help="Number of simulated paths")

    ap_meta_audit = sub.add_parser("meta-audit", help="Self-diagnosing architectural health audit")
    ap_meta_audit.add_argument("--json", action="store_true", help="Output raw JSON")
    ap_meta_audit.add_argument("--journal-path", default="trading_state.db", help="Path to SQLite trading journal for empirical audit")

    ap_harness = sub.add_parser("harness", help="DeepSeek Harness runtime modes (run, eval, code, creator, audit, info)")
    ap_harness.add_argument("mode", choices=["run", "eval", "benchmark", "code", "creator", "audit", "info"], default="run", nargs="?")
    ap_harness.add_argument("--symbol", default="EURUSD", help="Symbol to evaluate or trade")
    ap_harness.add_argument("--timeframe", default="M15", help="Timeframe (M15, H1, etc.)")
    ap_harness.add_argument("--bars", type=int, default=100, help="Number of bars for eval/benchmark")
    ap_harness.add_argument("--code", default="", help="Python code snippet for Code Mode")
    ap_harness.add_argument("--skill-name", default="dynamic_alpha_v1", help="Skill name for Creator Mode")
    ap_harness.add_argument("--skill-cat", default="RESEARCH", help="Skill category for Creator Mode")
    ap_harness.add_argument("--continuous", action="store_true", help="Run in continuous 24/7 autonomous loop")
    ap_harness.add_argument("--interval", type=float, default=15.0, help="Interval in seconds between cycles")
    ap_harness.add_argument("--max-cycles", type=int, default=None, help="Maximum number of cycles to execute")
    ap_harness.add_argument("--journal-path", default="trading_state.db", help="Path to SQLite trading store")
    ap_harness.add_argument("--json", action="store_true", help="Output raw JSON")

    ap_wf = sub.add_parser("workflow", help="Run institutional quantitative & algorithmic trading workflows")
    ap_wf.add_argument("type", choices=["statarb", "mean-reversion", "macro-momentum", "microstructure", "neural-kronos", "macro-event", "news", "list"], default="statarb", nargs="?")
    ap_wf.add_argument("--symbol", default="EURUSD", help="Symbol to evaluate")
    ap_wf.add_argument("--pair-asset", default="GBPUSD", help="Paired asset for statarb")
    ap_wf.add_argument("--event", default="NFP", help="Macro event name (NFP, CPI, RATE_DECISION, GDP)")
    ap_wf.add_argument("--actual", type=float, default=220.0, help="Actual reported number")
    ap_wf.add_argument("--consensus", type=float, default=180.0, help="Consensus forecast")
    ap_wf.add_argument("--previous", type=float, default=175.0, help="Previous release value")
    ap_wf.add_argument("--journal-path", default="trading_state.db", help="Path to SQLite trading store")
    ap_wf.add_argument("--json", action="store_true", help="Output raw JSON")

    ap_export_corr = sub.add_parser("export-correlation", help="Export news correlation data, seasonal patterns, and heatmap to JSON")
    ap_export_corr.add_argument("--output", "-o", default=None, help="Output JSON path (default: artifacts/news_correlation_export.json)")
    ap_export_corr.add_argument("--lookback", type=int, default=365, help="Days of history to export (default: 365)")
    ap_export_corr.add_argument("--symbol", default=None, help="Filter by symbol (default: all)")
    ap_export_corr.add_argument("--seasonal", action="store_true", help="Include seasonal pattern analysis")
    ap_export_corr.add_argument("--text", action="store_true", help="Print text report to console instead of JSON")
    ap_export_corr.add_argument("--json", action="store_true", help="Output raw JSON")

    ap_pipeline = sub.add_parser("pipeline-status", help="Autonomous research pipeline: lifecycle board for discovered strategies + evidence verification")
    ap_pipeline.add_argument("--journal-path", default="trading_state.db")
    ap_pipeline.add_argument("--verify", action="store_true", help="Also run one evidence-verification pass against the overfit gate")

    args = ap.parse_args()

    if args.cmd == "market-order":
        raise RuntimeError(
            "direct market-entry CLI is disabled; submit a deterministic pending "
            "limit/stop plan or call the ticket/magic-bound emergency_market_exit path"
        )
    
    # Initialize Rich Console for simple CLI
    from rich.console import Console
    console = Console()
    
    # Initialize paper mode flag (used throughout main())
    paper = getattr(args, 'paper', False)

    # Fast-boot default for paper sessions: never silently spend ~25 minutes
    # in CPU Laya triage (explicit NEWS_TRIAGE_MODE always wins).
    if paper:
        apply_paper_triage_default()

    # Handle export-correlation command
    if args.cmd == "export-correlation":
        from core.news_regime_correlation import NewsRegimeCorrelation, SeasonalPatternDetector
        corr = NewsRegimeCorrelation()
        try:
            if args.text:
                # Print text reports to console
                print(corr.get_heatmap_text(lookback_days=args.lookback, symbol=args.symbol))
                print()
                stats = corr.get_stats()
                print(f"  Total events: {stats['total_events']}  |  Hit rate: {stats['overall_hit_rate']:.1%}")
                print()
                detector = SeasonalPatternDetector(corr)
                print(detector.get_seasonal_text_report(lookback_days=args.lookback, symbol=args.symbol))
            else:
                # Export as JSON
                output_path = args.output
                export_data = {
                    "exported_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                    "lookback_days": args.lookback,
                    "symbol_filter": args.symbol,
                    "summary": corr.get_stats(),
                    "hit_rates": [],
                    "event_outcomes": corr.get_event_history(lookback_days=args.lookback, symbol=args.symbol, limit=5000),
                    "decay_alerts": corr.detect_decay(lookback_days=args.lookback, symbol=args.symbol),
                    "adjusted_windows": corr.get_all_adjusted_windows(symbol=args.symbol or "EURUSD"),
                    "heatmap": corr.get_heatmap_data(lookback_days=args.lookback, symbol=args.symbol),
                    "spike_distributions": {},
                }
                # Hit rates
                for hr in corr.compute_hit_rates(lookback_days=args.lookback, symbol=args.symbol):
                    export_data["hit_rates"].append({
                        "event_title": hr.event_title, "tier": hr.tier,
                        "currency": hr.currency, "n_events": hr.n_events,
                        "hit_rate": hr.hit_rate, "avg_spike_pips": hr.avg_spike_pips,
                        "median_spike_pips": hr.median_spike_pips, "p95_spike_pips": hr.p95_spike_pips,
                        "bullish_rate": hr.bullish_rate, "bearish_rate": hr.bearish_rate,
                        "recommended_before_min": hr.recommended_before_min,
                        "recommended_after_min": hr.recommended_after_min,
                        "confidence": hr.confidence,
                    })
                # Spike distributions
                seen = set()
                for ev in export_data["event_outcomes"]:
                    t = ev.get("event_title", "")
                    if t not in seen:
                        seen.add(t)
                        export_data["spike_distributions"][t] = corr.get_spike_distribution(event_title=t, lookback_days=args.lookback)
                # Seasonal patterns
                if args.seasonal:
                    detector = SeasonalPatternDetector(corr)
                    export_data["seasonal_patterns"] = detector.get_detector_report(lookback_days=args.lookback, symbol=args.symbol)
                # Write JSON
                import json as _json
                if output_path is None:
                    output_path = os.path.join("artifacts", "news_correlation_export.json")
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    _json.dump(export_data, f, indent=2, default=str)
                print(f"Exported to {output_path}")
                print(f"  Events: {len(export_data['event_outcomes'])} records")
                print(f"  Hit rates: {len(export_data['hit_rates'])} event types")
                print(f"  Decay alerts: {len(export_data['decay_alerts'])}")
                if args.seasonal:
                    n_sig = export_data.get("seasonal_patterns", {}).get("n_significant", 0)
                    print(f"  Seasonal patterns: {n_sig} significant")
        finally:
            corr.close()
        sys.exit(0)

    # Handle autonomous research pipeline status
    if args.cmd == "pipeline-status":
        import json as _json
        from operations import autonomous_pipeline as _ap
        from operations import strategy_lifecycle as _slc
        _board = _ap.board(args.journal_path)
        _states = _slc.current_states(args.journal_path)
        _n_all = len(_states)
        print("AUTONOMOUS RESEARCH PIPELINE")
        print(f"  discovered strategies: {_board['total']}  |  lifecycle rows total: {_n_all}")
        if _board["by_state"]:
            print("  states:", ", ".join(f"{k}={v}" for k, v in sorted(_board["by_state"].items())))
        for row in _board["rows"][:20]:
            print(f"    {row['setup_id'][:70]:<70} {row['state']}")
        if _board["total"] > 20:
            print(f"    ... and {_board['total'] - 20} more")
        if args.verify:
            _ver = _ap.verify(args.journal_path,
                              gate_state_file="overfit_gate_state.json")
            print(f"  verification: {_ver['checked']} checked, "
                  f"{_ver['verified']} verified, {len(_ver['flagged'])} flagged")
            for flag in _ver["flagged"][:10]:
                print(f"    FLAG {flag['setup_id'][:60]}: {flag['why'][:80]}")
        print("  authority: CANDIDATE rows cannot trade; promotion is operator-only")
        sys.exit(0)

    # Handle Account Commands immediately (secure + MCP login + OpenTUI deck)
    if args.cmd == "accounts":
        if args.action == "list":
            AccountManager.list_accounts()
        elif args.action == "select":
            if not args.name:
                print(" Error: --name required for select action")
                sys.exit(1)
            AccountManager.set_default(args.name)
        elif args.action == "add":
            if args.password:
                print(" Error: plaintext --password is forbidden; use --password-env or --prompt with secure-add")
                sys.exit(1)
            if not all([args.name, args.login, args.password_env, args.server, args.type]):
                print(" Error: name, login, password-env, server, and type are required for add")
                sys.exit(1)
            AccountManager.add_account(args.name, args.login, args.password_env, args.server, args.type)
        elif args.action == "secure-add":
            # Masked prompt -> keyring/encrypted file, never plaintext in JSON
            import getpass
            if not all([args.name, args.login, args.server, args.type]):
                print(" Error: --name, --login, --server, --type required for secure-add")
                sys.exit(1)
            if args.password:
                print(" Error: do not pass --password on CLI; omit and use masked prompt")
                sys.exit(1)
            # Secure masked entry (no echo)
            try:
                pw = getpass.getpass(f"Enter MT5 password for {args.login}@{args.server} (masked): ")
                pw2 = getpass.getpass("Confirm password: ")
            except Exception:
                import sys as _sys
                print(" Secure prompt failed (non-interactive). Use MT5_PASSWORD env or run via Settings TUI.")
                _sys.exit(1)
            if not pw or pw != pw2:
                print(" Error: empty or mismatch confirmation")
                sys.exit(1)
            AccountManager.add_secure_account(args.name, int(args.login), pw, args.server, args.type)
            del pw, pw2
            if getattr(args, "test_mcp", False):
                # Optional immediate MCP login test
                try:
                    cfg2 = Config.from_sources()
                    c2 = MT5Client(cfg2)
                    ok = c2.login()
                    print(f" MCP login test: {'OK' if ok else 'FAILED'} (see mt5_trader.log)")
                except Exception as e:
                    print(f" MCP login test failed: {e}")
        elif args.action == "secure-login":
            # Read current default account and test login via MT5Client (which now reads secure_store)
            # No credential args needed; just validates secure retrieval + MCP terminal
            try:
                cfg2 = Config.from_sources()
                if not cfg2.login or not cfg2.server:
                    print(" No active account. Use: python mt5_trader.py accounts secure-add --name demo --login ... --server ... --type DEMO")
                    sys.exit(1)
                print(f" Testing MCP login for {cfg2.login}@{cfg2.server} (secure_store backend: ", end="")
                try:
                    try:
                        from core.secure_store import has_store
                    except ImportError:
                        from core.secure_store import has_store
                    print(has_store() + ")")
                except Exception:
                    print("unknown)")
                c2 = MT5Client(cfg2)
                ok = c2.login()
                if ok:
                    info = c2.get_account_info()
                    print(f"  MT5 MCP login OK: {info.get('login')} {info.get('server')} balance {info.get('balance')}")
                else:
                    print("  MT5 login failed (check terminal running, credentials in secure store, and server name). Run: python mt5_trader.py diagnose")
            except Exception as e:
                print(f" secure-login error: {e}")
                sys.exit(1)
        elif args.action == "remove":
            if not args.name:
                print(" Error: --name required for remove")
                sys.exit(1)
            data2 = AccountManager.load_accounts()
            if args.name not in data2.get("accounts", {}):
                print(f" Account '{args.name}' not found")
                sys.exit(1)
            # also remove secure secret
            rec = data2["accounts"][args.name]
            sk = rec.get("password_store_key")
            if sk:
                try:
                    try:
                        from core.secure_store import delete_password
                    except ImportError:
                        from core.secure_store import delete_password
                    delete_password(sk)
                except Exception as exc:
                    logging.getLogger("mt5_trader").debug("exit operation failed: %s", exc)
            data2["accounts"].pop(args.name)
            if data2.get("default") == args.name:
                data2["default"] = next(iter(data2["accounts"].keys()), None)
            AccountManager.save_accounts(data2)
            print(f" Account '{args.name}' removed.")
        elif args.action == "deck":
            # Launch OpenTUI deck (preferred integrated UI)
            import subprocess, shutil, os as _os
            bun = shutil.which("bun")
            deck_dir = os.path.join(_root_dir, "tui_opentui")
            if bun and os.path.isdir(deck_dir):
                print(" Launching OpenTUI Command Deck (MT5 via MCP, Settings→Accounts secure entry) ...")
                env = _os.environ.copy()
                env["TRANSPORT"] = env.get("TRANSPORT", "mcp")
                subprocess.run([bun, "src/index.tsx"], cwd=deck_dir, env=env)
            else:
                print(" Bun or tui_opentui not found; falling back to Python Rich settings TUI. Run: npm i -g bun  or  python mt5_trader.py settings --section accounts")
                # fallback to settings TUI
                try:
                    from ui.interactive_menu import InteractiveMenu
                    # quick accounts submenu
                    m = InteractiveMenu()
                    # reuse secure prompt path
                    print(" Use: python mt5_trader.py settings --section accounts")
                except Exception as e:
                    print(f" fallback failed: {e}")
        return
    if args.cmd == "deck":
        if not _launch_opentui_deck(transport="mcp"):
            print(" OpenTUI deck requires bun. Install: npm i -g bun  &&  cd tui_opentui && bun install")
            print(" Fallback: python mt5_trader.py settings --section accounts")
        return
    if args.cmd == "settings":
        # Unified settings live in the single canonical TUI — the OpenTUI deck
        # (SYSTEM CONFIG screen).  The retired Rich settings shell is gone.
        if getattr(args, "tui", False) or os.getenv("LLM_TRADER_PREFER_DECK", "").lower() in ("1", "true", "yes"):
            if not _launch_opentui_deck(transport="mcp"):
                print(" OpenTUI deck requires bun. Install: npm i -g bun  &&  cd tui_opentui && bun install")
                print(" Fallback: python mt5_trader.py accounts list")
            return
        print(" Settings are managed from the OpenTUI deck (SYSTEM CONFIG screen): python mt5_trader.py deck")
        print(" Secure account management: python mt5_trader.py accounts list")
        return

    if args.cmd == "kill-switch":
        store = TradingStore(args.journal_path)
        if args.action == "status":
            print(json.dumps(store.get_state("kill_switch", {"active": False}), indent=2))
        else:
            store.set_kill_switch(args.action == "enable", args.reason)
            print(f"Kill switch {'ENABLED' if args.action == 'enable' else 'DISABLED'}.")
        return

    if args.cmd == "latch":
        from data.kill_switch_latch import get_kill_switch_latch
        store = TradingStore(args.journal_path)
        latch = get_kill_switch_latch()
        latch.attach_store(store)
        if args.clear:
            if args.confirm != "CLEAR_LATCH":
                print("Error: Confirmation required. Pass --confirm CLEAR_LATCH")
                return
            if not args.reason.strip():
                print("Error: Reason required. Pass --reason <reason>")
                return
            snap = latch.clear(reason=args.reason.strip(), actor=args.actor)
            print(f"Latch CLEARED by {args.actor}. Snapshot:\n{json.dumps(snap, indent=2)}")
            return
        # Default or --status
        print(json.dumps(latch.snapshot(), indent=2))
        return

    if args.cmd == "flatten":
        # NOTE: TradingStore / gateway helpers are module-level; never add a
        # local import here — it would shadow the module names for the whole
        # of main() (UnboundLocalError on earlier uses).
        # H6: typed confirmation (exactly FLATTEN, like deck/API).
        if args.confirm != "FLATTEN":
            print("Error: Confirmation required. Pass --confirm FLATTEN (typed confirmation).")
            return
        # H6: never race a running engine — take the engine lock first.
        from core.file_lock import FileLock as _FlattenFileLock
        _flatten_lock_path = os.environ.get("LLM_TRADER_LOCK_PATH", "").strip() or os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine.lock")
        _flatten_lock = _FlattenFileLock(_flatten_lock_path)
        if not _flatten_lock.acquire(non_blocking=True):
            print("Error: engine is running (engine.lock held). Use the API / deck flatten instead of the offline command.")
            return
        try:
            store = TradingStore(args.journal_path)
            gw_mode = "PAPER" if getattr(args, "paper", False) else "LIVE"
            configure_default_gateway(store, mode=gw_mode)
            gw = get_default_gateway()
            # H6: bind the real broker module when present so live reads see
            # the account (otherwise the gateway fails loudly with
            # listing_error instead of reporting an empty APPLIED).
            try:
                gw.bind_live_client(sys.modules.get("MetaTrader5"))
            except Exception:
                pass
            store.set_kill_switch(True, f"offline_flatten:{args.confirm}")
            # H6: owned-only always — manual positions are never touched.
            outcome = gw.execute_flatten(owned_magics=set(BOT_MAGICS))
            print(json.dumps(outcome.to_dict(), indent=2))
            return
        finally:
            try:
                _flatten_lock.release()
            except Exception:
                pass

    if args.cmd == "provider-circuit":
        circuit = ProviderCircuitBreaker(TradingStore(args.journal_path))
        state = circuit.status() if args.action == "status" else circuit.reset(args.operator, args.reason)
        print(json.dumps(state, indent=2))
        return

    if args.cmd == "memory-core":
        memory = TradingMemory(args.journal_path)
        if args.action == "list":
            print(json.dumps(memory.list_core_memories(), indent=2))
            return
        if args.action == "propose":
            if not args.namespace or not args.key or not args.content or not args.source_id:
                raise RuntimeError("memory-core propose requires --namespace, --key, --content, and at least one --source-id")
            memory_id = memory.propose_core_memory(args.namespace, args.key, args.content, args.source_id, args.confidence)
            print(json.dumps({"id": memory_id, "status": "CANDIDATE"}, indent=2))
            return
        if args.action == "retire":
            if not args.id:
                raise RuntimeError("memory-core retire requires --id")
            note = getattr(args, "note", "") or ""
            res = memory.retire_core_memory(args.id, reviewer=args.reviewer, reason=note)
            print(json.dumps(res, indent=2))
            return
        if not args.id:
            raise RuntimeError("memory-core approve/retire requires --id")
        memory.review_core_memory(args.id, approve=True, reviewer=args.reviewer)
        print(json.dumps({"id": args.id, "status": "APPROVED"}, indent=2))
        return

    if args.cmd == "memory-review":
        memory = TradingMemory(args.journal_path)
        if args.kind == "queue":
            if args.action != "list":
                raise RuntimeError("memory-review queue only supports list")
            print(json.dumps(memory.candidate_review_queue(limit=int(getattr(args, "limit", 20) or 20)), indent=2))
            return
        if args.action == "list":
            if args.kind == "narrative":
                records = memory.list_narratives("CANDIDATE")
            elif args.kind == "verified":
                records = memory.list_verified_memories("CANDIDATE")
            elif args.kind == "setup-label":
                records = memory.list_setup_labels()
            elif args.kind == "core":
                records = memory.list_core_memories("CANDIDATE")
            else:
                records = memory.list_scenarios("CANDIDATE")
            print(json.dumps(records, indent=2))
            return
        if args.kind == "scenario":
            if not args.scenario_key:
                raise RuntimeError("memory-review scenario approve/retire requires --scenario-key")
            memory.review_scenario(args.scenario_key, approve=args.action == "approve", reviewer=args.reviewer)
            print(json.dumps({"kind": "scenario", "scenario_key": args.scenario_key,
                              "status": "APPROVED" if args.action == "approve" else "RETIRED"}, indent=2))
            return
        if args.kind == "setup-label":
            if not args.subject_key:
                raise RuntimeError("memory-review setup-label approve/retire requires --subject-key")
            memory.review_setup_label(args.subject_key, approve=args.action == "approve", reviewer=args.reviewer)
            print(json.dumps({"kind": "setup-label", "subject_key": args.subject_key,
                              "status": "APPROVED" if args.action == "approve" else "RETIRED"}, indent=2))
            return
        if not args.id:
            raise RuntimeError("memory-review approve/retire requires --id")
        if args.kind == "narrative":
            memory.review_narrative(args.id, approve=args.action == "approve", reviewer=args.reviewer)
        elif args.kind == "core":
            memory.review_core_memory(args.id, approve=args.action == "approve", reviewer=args.reviewer)
        else:
            memory.review_verified_memory(args.id, approve=args.action == "approve", reviewer=args.reviewer)
        print(json.dumps({"kind": args.kind, "id": args.id, "status": "APPROVED" if args.action == "approve" else "RETIRED"}, indent=2))
        return

    if args.cmd == "journal":
        if args.action == "migrate":
            print(json.dumps(migrate_legacy_journal(args.source), indent=2))
            return
        if args.action == "migrate-states":
            from core.state_persistence import migrate_json_states_to_store
            print(json.dumps(migrate_json_states_to_store(args.journal_path, os.getcwd()), indent=2))
            return
        if args.action == "states-status":
            from core.state_persistence import state_migration_status
            print(json.dumps(state_migration_status(args.journal_path, os.getcwd()), indent=2))
            return
        if args.action == "audit-export":
            report = write_audit_export(args.journal_path, args.audit_output)
            print(json.dumps({
                "passed": bool(report.get("audit", {}).get("passed")),
                "output": os.path.abspath(args.audit_output),
                "audit_counts": report.get("audit", {}).get("counts", {}),
                "operational_counts": report.get("operational", {}).get("counts", {}),
                "execution_authority": False,
                "limitation": "Audit export is evidence only and never grants order permission.",
            }, indent=2))
            return
        if args.action == "audit-retention-status":
            print(json.dumps(AgentOperations(args.journal_path).retention_status(
                older_than_days=args.older_than_days,
            ), indent=2))
            return
        if args.action in {"operations", "recover", "execution-reviews", "resolve-execution-review"}:
            operations = AgentOperations(args.journal_path)
            bootstrap_schedules(operations)
            if args.action == "execution-reviews":
                print(json.dumps(operations.execution_reviews(), indent=2))
                return
            if args.action == "resolve-execution-review":
                if not args.intent_key or not args.review_status:
                    raise RuntimeError("resolve-execution-review requires --intent-key and --review-status")
                operations.resolve_execution_review(args.intent_key, disposition=args.review_status,
                                                    operator=args.operator, note=args.note)
                print(json.dumps({"intent_key": args.intent_key, "status": args.review_status}, indent=2))
                return
            if args.action == "recover":
                print(json.dumps({"stale_tasks_marked": operations.recover_stale_tasks(), "summary": operations.summary()}, indent=2))
            else:
                print(json.dumps(operations.summary(), indent=2))
            return
        if args.action == "memory-status":
            operations = AgentOperations(args.journal_path)
            print(json.dumps({"backend": "local SQLite + configured trader LLM", **operations.summary()}, indent=2))
            return
        if args.action in {"working-memory-status", "working-memory-context", "working-memory-compact"}:
            operations = AgentOperations(args.journal_path)
            if args.action == "working-memory-status":
                print(json.dumps(operations.working_memory_status(args.memory_namespace), indent=2))
                return
            if not args.memory_namespace:
                raise RuntimeError(f"{args.action} requires --memory-namespace")
            if args.action == "working-memory-context":
                print(json.dumps(operations.working_memory_context(
                    args.memory_namespace, as_of=args.context_as_of or None,
                    include_namespaces=args.include_namespace,
                    limit=args.memory_limit, max_chars=args.memory_max_chars,
                ), indent=2))
                return
            print(json.dumps(operations.compact_working_memory(
                args.memory_namespace, max_active=args.memory_max_active,
            ), indent=2))
            return
        if args.action == "memory-metrics":
            print(json.dumps(TradingMemory(args.journal_path).memory_quality_metrics(), indent=2))
            return
        if args.action == "setup-labels":
            memory = TradingMemory(args.journal_path)
            print(json.dumps({"labels": memory.list_setup_labels(args.label),
                              "execution_authority": False,
                              "limitation": "Labels are deterministic evidence only; they cannot permit orders."}, indent=2))
            return
        if args.action == "memory-maintenance":
            memory = TradingMemory(args.journal_path)
            archived = memory.archive_stale_candidates(args.older_than_days)
            print(json.dumps({"archived": archived, "older_than_days": args.older_than_days,
                              "review_commands": [
                                  "python mt5_trader.py memory-review narrative list",
                                  "python mt5_trader.py memory-review verified list",
                                  "python mt5_trader.py memory-core list",
                              ], "execution_authority": False}, indent=2))
            return
        if args.action == "weekly-memory":
            memory = TradingMemory(args.journal_path)
            print(json.dumps(memory.consolidate_weekly_core(args.week_start), indent=2))
            return
        if args.action == "monthly-memory":
            memory = TradingMemory(args.journal_path)
            print(json.dumps(memory.monthly_memory_maintenance(args.older_than_days), indent=2))
            return
        if args.action == "research-status":
            print(json.dumps(research_promotion_gate(args.journal_path), indent=2))
            return
        if args.action == "validation-runs":
            runs = list_validation_runs(args.journal_path, run_kind=args.run_kind or None, limit=args.run_limit)
            print(json.dumps({
                "runs": runs,
                "run_kind": args.run_kind or None,
                "count": len(runs),
                "execution_authority": False,
            }, indent=2))
            return
        if args.action == "lifecycle-audit":
            from operations.strategy_lifecycle import audit_history
            print(json.dumps(audit_history(args.journal_path), indent=2))
            return
        if args.action == "lifecycle-board":
            from operations.strategy_lifecycle import status_board
            print(json.dumps(status_board(args.journal_path), indent=2, default=str))
            return
        if args.action == "promotion-package":
            package = write_promotion_package(
                args.journal_path,
                backtest_report=args.backtest_report,
                minimum_demo_sample=args.minimum_demo_sample,
                output=args.promotion_output or None,
                ppo_report=args.ppo_report or None,
                meta_rl_report=args.meta_rl_report or None,
            )
            print(json.dumps(package, indent=2, default=str))
            return
        if args.action == "promotion-verify":
            package_path = args.promotion_package or args.promotion_output
            if not package_path:
                raise RuntimeError("promotion-verify requires --promotion-package")
            print(json.dumps(verify_promotion_package(package_path), indent=2))
            return
        if args.action == "historical-status":
            print(json.dumps(historical_research_status(MacroGraph(args.journal_path)), indent=2))
            return
        if args.action == "macro-extraction-status":
            print(json.dumps(macro_extraction_status(MacroGraph(args.journal_path)), indent=2))
            return
        if args.action == "policy-status":
            store = TradingStore(args.journal_path)
            policies = store.get_state("execution_policies", {}) or {}
            preflight = store.get_state("broker_preflight", {}) or {}
            print(json.dumps({"policies": policies, "broker_preflight_fingerprint": preflight.get("fingerprint"),
                              "broker_preflight_validation": preflight.get("validation"), "execution_authority": False,
                              "limitation": "Live/demo execution fails closed unless each symbol has an approved reviewed policy."}, indent=2))
            return
        if args.action == "policy-candidate":
            if not args.policy_symbol:
                raise RuntimeError("policy-candidate requires --policy-symbol")
            store = TradingStore(args.journal_path)
            print(json.dumps(execution_policy_candidate(store, args.policy_symbol), indent=2))
            return
        if args.action == "policy-approve":
            if not args.policy_symbol or not args.policy_json:
                raise RuntimeError("policy-approve requires --policy-symbol and --policy-json")
            with open(args.policy_json, "r", encoding="utf-8") as policy_file:
                policy = json.load(policy_file)
            store = TradingStore(args.journal_path)
            approved = approve_execution_policy(store, args.policy_symbol, policy, args.operator)
            print(json.dumps({"policy": approved, "execution_authority": False,
                              "limitation": "Approval records calibration evidence; live/demo authority still requires all runtime gates."}, indent=2))
            return
        if args.action == "housekeeping":
            from operations.ledger_housekeeping import plan as hk_plan, apply as hk_apply
            _hk_plan = hk_plan("artifacts")
            if args.apply:
                _hk_outcome = hk_apply(
                    _hk_plan,
                    reports_older_than_days=args.housekeeping_report_age_days,
                )
                print(json.dumps({"plan": _hk_plan, "apply": _hk_outcome}, indent=2, default=str))
            else:
                print(json.dumps({"plan": _hk_plan, "note": "dry run; pass --apply to delete"}, indent=2, default=str))
            return
        if args.action == "broker-preflight-status":
            store = TradingStore(args.journal_path)
            envelope = store.get_state("broker_preflight", {}) or {}
            symbols = sorted((envelope.get("report", {}).get("symbols", {}) or {}).keys())
            print(json.dumps({"preflight": envelope, "validation": validate_envelope(envelope, symbols),
                              "execution_authority": False,
                              "limitation": "Authenticated MT5 capture is required; no values are invented."}, indent=2))
            return
        if args.action == "auth-status":
            print(json.dumps(mt5_auth_report(), indent=2))
            return
        if args.action == "readiness-status":
            print(json.dumps(readiness_report(args.journal_path), indent=2))
            return
        if args.action == "dashboard-status":
            print(json.dumps(dashboard_report(args.journal_path), indent=2))
            return
        if args.action == "incident-drills":
            print(json.dumps(run_incident_drills(), indent=2))
            return
        if args.action == "security-status":
            print(json.dumps(security_audit(os.getcwd(), include_untracked=True), indent=2))
            return
        if args.action == "live-approval":
            if args.approval_action == "status":
                print(json.dumps(live_rollout_status(args.journal_path), indent=2))
            elif args.approval_action == "approve":
                print(json.dumps(approve_live_rollout(
                    args.journal_path, operator=args.operator, note=args.note,
                    evidence=args.evidence, risk_fraction_cap=args.risk_fraction_cap,
                    expires_hours=args.expires_hours, confirmation=args.confirmation,
                ), indent=2))
            elif args.approval_action == "revoke":
                print(json.dumps(revoke_live_rollout(args.journal_path, operator=args.operator, note=args.note), indent=2))
            else:
                print(json.dumps(verify_live_evidence(args.journal_path), indent=2))
            return
        if args.action == "forward-test-status":
            if not args.backtest_report:
                raise RuntimeError("forward-test-status requires --backtest-report")
            print(json.dumps(compare_forward_test(args.backtest_report, args.demo_journal,
                                                   minimum_demo_sample=args.minimum_demo_sample), indent=2))
            return
        if args.action == "dataset-status":
            dataset = labelled_dataset(args.journal_path)
            print(json.dumps({
                "sample_size": dataset.get("sample_size", 0),
                "excluded": dataset.get("excluded", {}),
                "point_in_time": dataset.get("point_in_time", False),
                "cost_model": dataset.get("cost_model"),
                "promotion_safe": bool(dataset.get("point_in_time") and dataset.get("sample_size", 0) >= 100),
                "limitation": "Rows require broker-reconciled close, deterministic labels, and current feature schema; no rows means no training.",
            }, indent=2))
            return
        if args.action == "sample-gate-status":
            print(json.dumps(sample_gate_report(args.journal_path), indent=2))
            return
        if args.action == "baseline-status":
            report = baseline_report(args.journal_path)
            record_validation_run(args.journal_path, "baseline", report)
            print(json.dumps(report, indent=2))
            return
        if args.action == "supervised-status":
            report = train_supervised_baseline(args.journal_path)
            record_validation_run(args.journal_path, "supervised", report)
            print(json.dumps(report, indent=2))
            return
        if args.action == "model-family-status":
            print(json.dumps(model_family_evaluation_plan(args.journal_path), indent=2))
            return
        if args.action == "model-status":
            print(json.dumps({"models": list_models(args.journal_path), "execution_authority": False}, indent=2))
            return
        if args.action == "model-drift":
            print(json.dumps(model_drift_report(args.journal_path), indent=2))
            return
        if args.action == "stress-status":
            print(json.dumps(stress_report(args.journal_path), indent=2))
            return
        if args.action == "bandit-status":
            report = bandit_report(args.journal_path)
            record_validation_run(args.journal_path, "bandit", report)
            print(json.dumps(report, indent=2))
            return
        if args.action == "semantic-status":
            print(json.dumps({"results": semantic_memory_search(args.journal_path, query={"scope": "trading"}),
                              "source": "local SQLite approved memory", "execution_authority": False}, indent=2))
            return
        if args.action == "alerts":
            print(json.dumps(evaluate_health_alerts(args.journal_path), indent=2))
            return
        if args.action == "alerts-enqueue":
            report = evaluate_health_alerts(args.journal_path)
            result = enqueue_alerts(args.journal_path, report.get("alerts", []), channel=args.alert_channel)
            print(json.dumps({"health": report, "outbox": result,
                              "execution_authority": False,
                              "limitation": "Enqueue only persists redacted operator alerts; an external notifier must deliver and acknowledge them."}, indent=2))
            return
        if args.action == "alerts-status":
            print(json.dumps(alert_outbox_status(args.journal_path), indent=2))
            return
        if args.action == "alerts-claim":
            print(json.dumps({"alerts": claim_alerts(args.journal_path, limit=args.alert_limit),
                              "execution_authority": False,
                              "transport": "NONE"}, indent=2))
            return
        if args.action == "alerts-ack":
            if args.alert_id is None:
                raise RuntimeError("alerts-ack requires --alert-id")
            print(json.dumps(acknowledge_alert(args.journal_path, args.alert_id,
                                               delivered=args.alert_delivered,
                                               error=args.alert_error), indent=2))
            return
        if args.action == "alerts-recover":
            print(json.dumps(recover_alerts(args.journal_path), indent=2))
            return
        if args.action == "alerts-deliver":
            webhook_url = resolve_webhook_url() if args.alert_transport == "webhook" else None
            result = deliver_alerts(
                args.journal_path,
                transport=args.alert_transport,
                file_path=args.alert_file or None,
                webhook_url=webhook_url,
                limit=args.alert_limit,
            )
            print(json.dumps({
                **result,
                "limitation": "Delivery is transport-only; it never changes trading permission or order state.",
            }, indent=2))
            return
        if args.action == "strategy-status":
            board = lifecycle.status_board(
                args.journal_path,
                registry_specs={str(key): value for key, value in REGISTERED_SETUPS.items()},
                registry_fingerprint=registered_setup_fingerprint(),
            )
            print(json.dumps({"setups": board, "blocking_states": sorted(lifecycle.BLOCKING_STATES),
                              "registry_fingerprint": registered_setup_fingerprint(),
                              "execution_authority": False}, indent=2))
            return
        if args.action == "strategy-transition":
            if not args.setup_id or not args.to_state:
                raise RuntimeError("strategy-transition requires --setup-id and --to-state")
            result = lifecycle.transition(
                args.journal_path, args.setup_id, args.to_state,
                actor=f"operator:{args.operator}", reason=args.note,
            )
            print(json.dumps({**result,
                              "note": "Promotions, unfreezes, and retirements are explicit operator actions."}, indent=2))
            return
        if args.action == "strategy-unfreeze":
            if not args.setup_id:
                raise RuntimeError("strategy-unfreeze requires --setup-id")
            result = lifecycle.transition(
                args.journal_path, args.setup_id, "DEPLOYED",
                actor=f"operator:{args.operator}", reason=args.note or "Operator explicit unfreeze",
            )
            print(json.dumps({**result,
                              "note": "Setup successfully unfrozen and restored to DEPLOYED state."}, indent=2))
            return
        if args.action == "strategy-retire":
            if not args.setup_id:
                raise RuntimeError("strategy-retire requires --setup-id")
            result = lifecycle.transition(
                args.journal_path, args.setup_id, "RETIRED",
                actor=f"operator:{args.operator}", reason=args.note or "Operator explicit retirement",
            )
            print(json.dumps({**result,
                              "note": "Setup successfully moved to terminal RETIRED state."}, indent=2))
            return
        if args.action == "strategy-tune":
            if not args.setup_id:
                raise RuntimeError("strategy-tune requires --setup-id")
            from research.bayesian_walk_forward_sandbox import BayesianWalkForwardSandbox
            sandbox = BayesianWalkForwardSandbox()
            # Try fetching historical R returns from journal
            returns = []
            try:
                import sqlite3
                con = sqlite3.connect(args.journal_path)
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT r_multiple FROM trade_ledger WHERE setup_id=? AND r_multiple IS NOT NULL ORDER BY created_at ASC",
                    (args.setup_id,),
                ).fetchall()
                returns = [float(row["r_multiple"]) for row in rows if row["r_multiple"] is not None]
                con.close()
            except Exception:
                returns = []
            if len(returns) < 20:
                # Default benchmark return sequence for sandbox exploratory calibration
                returns = [1.2, -1.0, 1.8, -0.9, 2.1, -1.0, 1.4, 0.8, -1.0, 2.5,
                           -0.8, 1.3, -1.0, 1.5, -1.0, 2.0, 1.1, -0.7, 1.6, -1.0] * 2
            report = sandbox.tune(setup_id=args.setup_id, returns=returns)
            print(json.dumps(report, indent=2))
            return
        if args.action == "macro-prune":
            # MacroGraph comes from module scope — local import here shadows it
            # for the earlier macro-history/macro-extraction-status branches.
            mg = MacroGraph(args.journal_path)
            max_age = getattr(args, "max_age_days", 90)
            min_conf = getattr(args, "min_confidence", 0.20)
            actor = f"operator:{args.operator}"
            result = mg.prune_stale_edges(max_age_days=max_age, min_confidence=min_conf, actor=actor)
            print(json.dumps(result, indent=2))
            return
        if args.action == "strategy-acknowledge":
            if not args.setup_id:
                raise RuntimeError("strategy-acknowledge requires --setup-id")
            result = lifecycle.acknowledge_registry_fingerprint(
                args.journal_path, args.setup_id, actor=f"operator:{args.operator}",
                fingerprint=registered_setup_fingerprint(),
            )
            print(json.dumps(result, indent=2))
            return
        if args.action == "strategy-evaluate":
            report = evaluate_strategy_lifecycle(
                args.journal_path,
                registry_specs={str(key): value for key, value in REGISTERED_SETUPS.items()},
                registry_fingerprint=registered_setup_fingerprint(),
            )
            print(json.dumps(report, indent=2))
            return
        if args.action == "research-influence-status":
            print(json.dumps({
                **research_influence_status(args.journal_path, model_kind=args.influence_kind),
                "evidence_fingerprint": research_evidence_fingerprint(args.journal_path),
            }, indent=2))
            return
        if args.action == "research-influence-approve":
            if not args.influence_evidence_hash:
                raise RuntimeError("research-influence-approve requires --influence-evidence-hash")
            print(json.dumps(approve_research_influence(
                args.journal_path, model_kind=args.influence_kind, operator=args.operator,
                evidence_hash=args.influence_evidence_hash, note=args.note,
                expires_hours=args.influence_expires_hours,
                oos_validated=args.influence_oos_validated,
            ), indent=2))
            return
        if args.action == "research-influence-revoke":
            print(json.dumps(revoke_research_influence(
                args.journal_path, operator=args.operator, note=args.note,
            ), indent=2))
            return
        if args.action == "legacy-status":
            print(json.dumps(legacy_reconciliation_report(args.journal_path), indent=2))
            return
        if args.action == "legacy-quarantine":
            if args.ticket is None:
                raise RuntimeError("legacy-quarantine requires --ticket")
            print(json.dumps(quarantine_legacy_record(
                args.journal_path, args.ticket, args.operator, args.note, args.confirmation,
            ), indent=2))
            return
        if args.action == "backup-create":
            print(json.dumps(create_backup(args.source, args.backup_destination), indent=2))
            return
        if args.action == "backup-status":
            backup_path = args.backup_path or args.backup_destination
            if not backup_path:
                print(json.dumps({"status": "NO_BACKUP_SELECTED", "hint": "Use --backup-path to verify a backup."}, indent=2))
            else:
                print(json.dumps(verify_backup(backup_path), indent=2))
            return
        if args.action == "backup-restore":
            if not args.backup_path or not args.backup_destination:
                raise RuntimeError("backup-restore requires --backup-path and --backup-destination")
            print(json.dumps(restore_backup(args.backup_path, args.backup_destination, overwrite=args.overwrite), indent=2))
            return
        if args.action == "backup-prune":
            print(json.dumps(prune_backups(
                args.backup_directory,
                older_than_days=args.older_than_days,
                keep_minimum=args.keep_backups,
                apply=args.apply,
            ), indent=2))
            return
        if args.action in {"encrypted-backup-create", "encrypted-backup-status", "encrypted-backup-restore"}:
            passphrase = os.getenv(args.backup_passphrase_env, "")
            if not passphrase:
                raise RuntimeError(
                    f"{args.action} requires a passphrase in environment variable "
                    f"{args.backup_passphrase_env}; never put the passphrase on the command line"
                )
            if args.action == "encrypted-backup-create":
                print(json.dumps(create_encrypted_backup(
                    args.journal_path, args.backup_destination, passphrase=passphrase,
                ), indent=2))
                return
            if not args.backup_path:
                raise RuntimeError(f"{args.action} requires --backup-path")
            if args.action == "encrypted-backup-status":
                print(json.dumps(verify_encrypted_backup(
                    args.backup_path, passphrase=passphrase,
                ), indent=2))
                return
            if not args.backup_destination:
                raise RuntimeError("encrypted-backup-restore requires --backup-destination")
            print(json.dumps(restore_encrypted_backup(
                args.backup_path, args.backup_destination,
                passphrase=passphrase, overwrite=args.overwrite,
            ), indent=2))
            return
        if args.action == "restart-status":
            smoke_path = args.backup_destination or "restart_recovery_smoke.db"
            print(json.dumps(restart_recovery_smoke(smoke_path), indent=2))
            return
        if args.action == "scope-status":
            print(json.dumps(journal_scope_status(args.journal_path), indent=2))
            return
        if args.action == "scope-init":
            if not args.journal_mode:
                raise RuntimeError("scope-init requires --journal-mode")
            print(json.dumps(ensure_scope(args.journal_path, args.journal_mode, args.account_fingerprint), indent=2))
            return
        if args.action == "model-register":
            if not all((args.model_key, args.model_version, args.feature_schema_version, args.training_data_hash)):
                raise RuntimeError("model-register requires --model-key, --model-version, --feature-schema-version, and --training-data-hash")
            try:
                metrics = json.loads(args.metrics_json or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError("--metrics-json must be valid JSON") from exc
            print(json.dumps(register_model(args.journal_path, model_key=args.model_key, version=args.model_version,
                                            kind=args.model_kind, feature_schema_version=args.feature_schema_version,
                                            training_data_hash=args.training_data_hash, metrics=metrics,
                                            artifact_uri=args.artifact_uri), indent=2))
            return
        if args.action == "model-review":
            if not args.model_id or not args.model_review_status:
                raise RuntimeError("model-review requires --model-id and --model-review-status")
            print(json.dumps(review_model(args.journal_path, model_id=args.model_id, status=args.model_review_status,
                                          reviewer=args.operator, note=args.note), indent=2))
            return
        if args.action == "model-activation-status":
            if not args.model_id:
                raise RuntimeError("model-activation-status requires --model-id")
            print(json.dumps(model_activation_status(args.journal_path, model_id=args.model_id), indent=2))
            return
        if args.action == "reconciliation":
            print(json.dumps(TradingStore(args.journal_path).latest_daily_reconciliation() or {
                "status": "NOT_RUN", "limitation": "No authenticated MT5 daily reconciliation has been recorded yet."
            }, indent=2))
            return
        if args.action == "reconciliation-run":
            store = TradingStore(args.journal_path)
            now = datetime.now(timezone.utc)
            if args.start:
                start_dt = datetime.fromisoformat(args.start.replace("Z", "+00:00")).astimezone(timezone.utc)
            else:
                start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if args.end:
                end_dt = datetime.fromisoformat(args.end.replace("Z", "+00:00")).astimezone(timezone.utc)
            else:
                end_dt = min(now, start_dt + timedelta(days=1))

            if args.paper:
                # Paper mode has no independent broker side, so there is
                # nothing honest to reconcile.  The PaperBroker's closed history
                # is in-memory on the trader process (core/order_execution.py:
                # self.history, trimmed to 1000) and is unreachable from this
                # CLI; the only durable thing here is the journal itself.
                # Feeding journal rows back in as "broker" deals compares the
                # journal against itself: it can never disagree, so it can
                # never be evidence.  And because journal rows carry `pnl`
                # while the deal adapter reads `profit`, every non-zero-P&L
                # trade was additionally reported as a spurious mismatch
                # (live: refused_discrepant=5) plus a durable ATTENTION work
                # item per trade, overwriting any real report for the period.
                # compare_day already refuses to mint evidence from these
                # (refused_journal_derived), so nothing reached the gates --
                # but it still wrote false alarms.  Refuse outright.
                #
                # The genuine paper evidence path is the in-process one:
                # operations/learning_loop._resolve_broker_deals reads
                # context.paper_broker.history (real PaperBroker closes) and
                # runs compare_day over it.  That is separate from this CLI.
                report = {
                    "status": "NO_INDEPENDENT_BROKER_SOURCE",
                    "period_start": start_dt.isoformat(),
                    "period_end": end_dt.isoformat(),
                    "reason": (
                        "paper mode supplies no broker-side evidence: the journal "
                        "cannot be reconciled against itself, and the PaperBroker's "
                        "closed history lives in the trader process, not the journal. "
                        "No reconciliation was run and no evidence was written."
                    ),
                    "execution_evidence_written": 0,
                    "execution_authority": False,
                    "hint": ("run without --paper against MetaTrader5 deal history; "
                             "paper closes are reconciled in-process by "
                             "operations/learning_loop.run_daily_review"),
                }
            else:
                # Genuine broker side.  (This used to call an undefined
                # `_get_mt5()`, so the only live path always raised NameError.)
                if mt5 is None:
                    raise RuntimeError(
                        "MetaTrader5 module unavailable for live reconciliation; "
                        "there is no independent broker side without it")
                deals = mt5.history_deals_get(start_dt, end_dt)
                if deals is None:
                    raise RuntimeError("MT5 deal history unavailable for reconciliation")
                from operations.daily_reconciliation import compare_day
                report = compare_day(store, deals, start_dt, end_dt, require_timestamps=True)

            print(json.dumps(report, indent=2))
            return
        if args.action == "db-maintenance":
            store = TradingStore(args.journal_path)
            bdir = getattr(args, "backup_dir", "backups") or "backups"
            report = store.perform_maintenance(backup_dir=bdir)
            print(json.dumps(report, indent=2))
            return
        # Ensure all durable journal tables exist before reporting/auditing them.
        TradingStore(args.journal_path)
        memory = TradingMemory(args.journal_path)
        if args.action == "patterns":
            print(json.dumps(memory.pattern_summary(), indent=2))
        elif args.action == "playbook":
            print(json.dumps(memory.latest_playbook(), indent=2))
        elif args.action == "integrity":
            print(json.dumps(audit_journal(args.journal_path), indent=2))
        elif args.action == "metrics":
            print(json.dumps(qualified_metrics(args.journal_path), indent=2))
        else:
            print(json.dumps(write_report(args.journal_path), indent=2))
        return
    # Operations not requiring MT5

    if args.cmd == "doctor":
        from operations.system_doctor import run as run_doctor
        report = run_doctor()
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, default=str))
        else:
            print(f"System Doctor: {report['overall']}")
            for key, check in report["checks"].items():
                status = "PASS" if check["pass"] else "FAIL"
                print(f"  [{status}] {key}: {check['message']}")
            if report.get("readiness"):
                print("\nReadiness Report:")
                print(f"  Status: {report['readiness']['status']}")
                for blocker in report["readiness"].get("blockers", []):
                    print(f"  BLOCKER: {blocker}")
        return

    if args.cmd == "monitor":
        # Log monitoring lives in the single canonical TUI — the OpenTUI deck
        # (LOG MONITOR screen).  The retired Rich log monitor is gone.
        print(" Log monitoring lives in the OpenTUI deck (LOG MONITOR screen).")
        if not _launch_opentui_deck(transport="auto"):
            print(" OpenTUI deck requires bun. Install: npm i -g bun  &&  cd tui_opentui && bun install")
        return

    if args.cmd == "nexus":
        import subprocess
        _live = getattr(args, 'live', False)
        _proc = None
        if _live:
            # Start the trading loop in a subprocess
            _symbol = getattr(args, 'symbol', 'EURUSD')
            _loops = getattr(args, 'loops', 0)
            _cmd = [sys.executable, "mt5_trader.py", "trade",
                    "--symbol", _symbol,
                    "--timeframe", "M15",
                    "--loops", str(_loops),
                    "--paper"]
            _proc = subprocess.Popen(_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            logging.getLogger("main").info("Trading subprocess started: PID=%s", _proc.pid)
        # Single canonical TUI: the OpenTUI command deck (was the retired Rich unified TUI)
        try:
            if not _launch_opentui_deck(transport="auto"):
                print(" OpenTUI deck requires bun. Install: npm i -g bun  &&  cd tui_opentui && bun install")
        finally:
            if _proc is not None:
                _proc.terminate()
                try:
                    _proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _proc.kill()
        return

    if args.cmd == "demo-samples":
        from research.demo_sample_generator import inject_samples
        result = inject_samples(path=args.path, count=args.count)
        print(json.dumps(result, indent=2, default=str))
        return

    if args.cmd == "profiles":
        from core.harness.quant_models_plugins import list_profiles, load_profile, PROFILE_DEFINITIONS
        if args.name:
            # Show details for one profile
            plugins = load_profile(args.name)
            desc = PROFILE_DEFINITIONS[args.name]["description"]
            if getattr(args, "json", False):
                print(json.dumps({
                    "name": args.name,
                    "description": desc,
                    "n_plugins": len(plugins),
                    "plugins": plugins,
                }, indent=2))
            else:
                print(f"Profile: {args.name}")
                print(f"Description: {desc}")
                print(f"Plugins: {len(plugins)}")
                print()
                for i, p in enumerate(plugins, 1):
                    class_path = p["class_path"]
                    kwargs = p["kwargs"]
                    module_path, class_name = class_path.rsplit(":", 1)
                    kw_str = ", ".join(f"{k}={v!r}" for k, v in kwargs.items()) if kwargs else "(default)"
                    print(f"  {i:2d}. {class_name:<35s} {kw_str}")
            if getattr(args, "mount", False):
                from core.harness.kernel import TradingContext
                from core.harness.quant_models_plugins import mount_profile
                ctx = TradingContext(name=f"{args.name}_probe")
                mount_profile(ctx, args.name)
                print(f"\nMounted: {len(ctx._services)} services, {len(ctx._plugins)} plugins")
                for svc_name in sorted(ctx._services.keys()):
                    svc = ctx._services[svc_name]
                    print(f"  {svc_name}: {type(svc).__name__}")
                ctx.dispose()
        else:
            # List all profiles
            profiles = list_profiles()
            if getattr(args, "json", False):
                out = {}
                for name, desc in profiles.items():
                    plugins = load_profile(name)
                    out[name] = {"description": desc, "n_plugins": len(plugins)}
                print(json.dumps(out, indent=2))
            else:
                print(f"{'Profile':<12s} {'Plugins':>8s}  Description")
                print("-" * 70)
                for name, desc in profiles.items():
                    plugins = load_profile(name)
                    print(f"{name:<12s} {len(plugins):>8d}  {desc}")
                print()
                print("Usage:")
                print("  python mt5_trader.py profiles paper          # Show paper profile details")
                print("  python mt5_trader.py profiles live --mount   # Mount live profile and show services")
                print("  python mt5_trader.py profiles --json         # All profiles as JSON")
                print("  python mt5_trader.py llm-trade --profile paper  # Use profile in trading")
        return

    if args.cmd == "scrub-history":
        from operations.security_scrub import scrub_history
        ok, msg = scrub_history(Path(args.path).resolve(), dry_run=not args.apply)
        print(msg)
        if not ok and not args.apply:
            print("Re-run with --apply after review to perform the scrub.")
        sys.exit(0 if ok else 1)

    if args.cmd == "macro":
        if args.action == "nowcast":
            from data.zero_lag_macro_provider import get_zero_lag_provider
            provider = get_zero_lag_provider()
            snapshot = provider.get_snapshot()
            bundle = provider.get_ten_pillars_bundle()
            print(json.dumps({"zero_lag_snapshot": snapshot.to_dict(), "ten_pillars_bundle": bundle}, indent=2))
            return
        elif args.action == "ratios":
            from data.zero_lag_macro_provider import get_zero_lag_provider
            provider = get_zero_lag_provider()
            ratios = provider.compute_universal_macro_ratios()
            print(json.dumps(ratios.to_dict(), indent=2))
            return
        elif args.action == "learn-case":
            if not args.file:
                print(json.dumps({"error": "Missing --file argument specifying JSON case study path"}, indent=2))
                return
            from intelligence.case_study_learner import get_case_study_learner
            learner = get_case_study_learner()
            res = learner.learn_from_json_file(args.file)
            print(json.dumps(res.to_dict(), indent=2))
            return
        elif args.action == "case-library":
            from intelligence.case_study_library import get_case_study_library
            library = get_case_study_library()
            if args.search:
                cases = library.search_cases(args.search)
            elif args.domain and args.domain != "ALL":
                cases = library.filter_by_domain(args.domain)
            else:
                cases = library.list_all_cases()
            summary = library.get_domains_summary()
            out = [
                {
                    "case_id": c.case_id,
                    "label": c.label,
                    "primary_domain": c.primary_domain,
                    "chokepoints": list(c.chokepoints_affected),
                    "entities": list(c.primary_entities),
                    "tau_horizon": c.tau_substitution_horizon,
                    "spread_distortions": list(c.core_spread_distortions),
                }
                for c in cases
            ]
            print(json.dumps({"total_cases": len(cases), "domains_distribution": summary, "cases": out}, indent=2))
            return
        elif args.action == "case-info":
            if not args.case:
                print(json.dumps({"error": "Missing --case argument specifying case ID (e.g. case_a_hormuz_blockade)"}, indent=2))
                return
            from intelligence.case_study_library import get_case_study_library
            library = get_case_study_library()
            case = library.get_case(args.case)
            if not case:
                print(json.dumps({"error": f"Case ID not found: {args.case}"}, indent=2))
                return
            print(json.dumps({
                "case_id": case.case_id,
                "label": case.label,
                "primary_domain": case.primary_domain,
                "historical_date_window": case.historical_date_window,
                "chokepoints_affected": list(case.chokepoints_affected),
                "primary_entities": list(case.primary_entities),
                "transmission_channels": list(case.transmission_channels),
                "lead_time_days": case.lead_time_days,
                "duration_days": case.duration_days,
                "tau_substitution_horizon": case.tau_substitution_horizon,
                "core_spread_distortions": list(case.core_spread_distortions),
                "prompt_curve_regime": case.prompt_curve_regime,
                "historical_outcomes": case.historical_outcomes,
                "falsification_tripwires": list(case.falsification_tripwires),
                "reference_url": case.reference_url,
                "execution_authority": False,
            }, indent=2))
            return
        elif args.action == "research-case" or args.action == "supply-shock":
            prompt_text = args.prompt
            if not prompt_text and args.file and os.path.exists(args.file):
                with open(args.file, "r", encoding="utf-8") as f:
                    prompt_text = f.read()
            if not prompt_text:
                prompt_text = f"Emerging supply disruption affecting {args.symbol} in {args.domain}"
            from intelligence.case_study_research import get_case_study_research_engine
            engine = get_case_study_research_engine()
            res = engine.research_and_synthesize(
                headline_or_text=prompt_text,
                domain_override=args.domain if args.domain != "MARITIME_CHOKEPOINT" or "maritime" in prompt_text.lower() else None,
                auto_assimilate=True,
            )
            print(json.dumps(res.to_dict(), indent=2))
            return
        elif args.action == "case-match":
            from intelligence.case_study_matcher import get_case_study_matcher
            matcher = get_case_study_matcher()
            matches = matcher.match_event(
                event_domain=args.domain,
                affected_entities=[args.symbol, "WTI_OIL", "LIQUIDITY_STRESS", "INFLATION"],
                top_k=5,
            )
            out = [
                {
                    "case_id": m.matched_case_id,
                    "label": m.label,
                    "similarity_score": m.similarity_score,
                    "tau_horizon": m.tau_horizon,
                    "projected_trajectory": m.projected_trajectory,
                    "falsification_tripwires": list(m.falsification_tripwires),
                }
                for m in matches
            ]
            print(json.dumps({"query_domain": args.domain, "top_matches": out}, indent=2))
            return
        elif args.action == "status":
            from intelligence.regime_classifier import MacroRegimeClassifier
            classifier = MacroRegimeClassifier()
            res = classifier.classify_current_regime()
            print(json.dumps(res.to_dict(), indent=2))
            return
        else:
            from intelligence.macrofundamental_pipeline import AutonomousMacroPipeline
            pipeline = AutonomousMacroPipeline()
            res = pipeline.run_pipeline_cycle(symbols=[args.symbol, "XAUUSD", "USOIL"] if args.symbol == "EURUSD" else [args.symbol])
            print(json.dumps(res, indent=2))
            return

    if args.cmd == "research-sim":
        if args.action == "simulate-ar1":
            from research.synthetic_path_simulator import fit_ar1_model, generate_synthetic_paths, optimize_strategy_ensemble, default_mean_reversion_strategy
            np.random.seed(42)
            prices = 100.0 + np.cumsum(np.random.normal(0, 0.5, 300))
            fit = fit_ar1_model(prices)
            paths = generate_synthetic_paths(fit, num_paths=args.paths, path_length=150)
            param_grid = [{"window": 10, "threshold": 1.0}, {"window": 20, "threshold": 1.5}, {"window": 30, "threshold": 2.0}]
            opt = optimize_strategy_ensemble(default_mean_reversion_strategy, param_grid, paths)
            print(json.dumps({"fit": fit.to_dict(), "optimization": opt}, indent=2))
            return
        elif args.action == "risk-parity":
            from research.risk_parity_engine import RiskParityOptimizer
            cov = np.array([[0.04, 0.01, 0.00], [0.01, 0.09, 0.02], [0.00, 0.02, 0.02]])
            opt = RiskParityOptimizer()
            res = opt.solve(cov, asset_names=["EURUSD", "XAUUSD", "USOIL"])
            print(json.dumps(res.to_dict(), indent=2))
            return
        elif args.action == "regime-mc":
            from research.regime_monte_carlo import RegimeSwitchingMonteCarlo
            mc = RegimeSwitchingMonteCarlo()
            trades = {"LOW_VOL": np.array([0.01, -0.005, 0.012]), "HIGH_VOL_STRESS": np.array([0.02, -0.025, -0.01])}
            res = mc.run_simulation(trades, n_simulations=args.paths, n_trades=50)
            print(json.dumps(res.to_dict(), indent=2))
            return

    if args.cmd == "meta-audit":
        from intelligence.meta_reflector import SystemMetaReflector
        ref = SystemMetaReflector()
        journal_path = getattr(args, "journal_path", "trading_state.db")
        report = ref.audit_from_journal(journal_path)
        print(json.dumps(report.to_dict(), indent=2))
        return

    if args.cmd == "harness":
        from core.harness import TradingContext, EventStream
        from core.harness.plugins import (
            RiskManagementPlugin, TradingMemoryPlugin, BrokerPlugin,
            CausalIntelligencePlugin, DataHubPlugin, DeliberativeGraphPlugin,
            SkillsPlugin,
            JournalAuditPlugin, AnalyticalFrameworksPlugin, KronosIntelligencePlugin,
            ExecutionSafetyPlugin, CointegrationGovernancePlugin,
        )
        from core.harness.extracted_module_plugins import ExtractedModulesPlugin
        from intelligence.harness_agent_loop import HarnessAgentLoop
        from research.eval_harness import TradingEvalHarness
        from dataclasses import asdict

        ctx = TradingContext(name="CLIHarnessContext")
        ctx.plugin(TradingMemoryPlugin, db_path=args.journal_path)
        ctx.plugin(RiskManagementPlugin, db_path=args.journal_path, balance=0.0, max_risk_pct=0.01)
        ctx.plugin(BrokerPlugin, paper_mode=True, db_path=args.journal_path)
        ctx.plugin(CausalIntelligencePlugin, db_path=args.journal_path)
        ctx.plugin(DataHubPlugin)
        ctx.plugin(DeliberativeGraphPlugin, db_path=args.journal_path)

        ctx.plugin(SkillsPlugin, db_path=args.journal_path)
        ctx.plugin(JournalAuditPlugin, db_path=args.journal_path)

        ctx.plugin(AnalyticalFrameworksPlugin)
        ctx.plugin(KronosIntelligencePlugin)
        ctx.plugin(ExecutionSafetyPlugin)
        ctx.plugin(CointegrationGovernancePlugin)
        ctx.plugin(ExtractedModulesPlugin)

        if args.mode == "run":
            stream = EventStream(db_path=args.journal_path)
            loop = HarnessAgentLoop(root_ctx=ctx, event_stream=stream)
            if getattr(args, "continuous", False):
                symbols = [s.strip() for s in args.symbol.split(",") if s.strip()]
                print(f" Launching Continuous Autonomous Harness Loop across {symbols} (interval={args.interval}s)...")
                loop.run_continuous(
                    symbols=symbols,
                    timeframe=args.timeframe,
                    interval_seconds=getattr(args, "interval", 15.0),
                    max_cycles=getattr(args, "max_cycles", None),
                )
            else:
                res = loop.run_cycle(symbol=args.symbol, timeframe=args.timeframe)
                if getattr(args, "json", False):
                    print(json.dumps(asdict(res), indent=2))
                else:
                    print(f"✔ Harness Cycle Completed: Decision={res.decision}, Winner={res.winning_hypothesis}, Latency={res.duration_ms}ms")
        elif args.mode in ("eval", "benchmark"):
            harness = TradingEvalHarness(db_path=args.journal_path)
            sample_dataset = [
                {
                    "close": 1.0850 + (i * 0.0002),
                    "high": 1.0870 + (i * 0.0002),
                    "low": 1.0830 + (i * 0.0002),
                    "atr": 0.0015,
                    "rsi": 30 + (i % 40),
                    "future_close": 1.0860 + (i * 0.0002),
                }
                for i in range(args.bars)
            ]
            eval_res = harness.run_benchmark(dataset=sample_dataset, symbol=args.symbol)
            if getattr(args, "json", False):
                print(json.dumps(eval_res.to_dict(), indent=2))
            else:
                print(f"▣ Harness Benchmark Results ({eval_res.total_bars_evaluated} bars):")
                print(f"   Approval Rate: {eval_res.approval_rate_pct}% ({eval_res.proposals_approved_by_risk}/{eval_res.total_proposals_generated})")
                print(f"   Win Rate:      {eval_res.win_rate_pct}%")
                print(f"   Sharpe Ratio:  {eval_res.sharpe_ratio}")
                print(f"   Avg Latency:   {eval_res.avg_latency_ms}ms")
                print(f"   Session ID:    {eval_res.session_id}")
        elif args.mode == "code":
            interpreter = ctx.get("code_interpreter")
            code_to_run = args.code or "result = {'status': 'CODE_SANDBOX_ACTIVE', 'symbols': ['EURUSD', 'GBPUSD'], 'time': 'UTC'}"
            exec_res = interpreter.execute_code(code_to_run)
            if getattr(args, "json", False):
                print(json.dumps(exec_res.to_dict(), indent=2))
            else:
                print(f"□ Code Mode Execution ({'SUCCESS' if exec_res.success else 'FAILED'}, {exec_res.execution_time_ms}ms):")
                if exec_res.stdout:
                    print(f"   Stdout: {exec_res.stdout.strip()}")
                print(f"   Result: {exec_res.result}")
                if exec_res.error:
                    print(f"   Error:  {exec_res.error}")
        elif args.mode == "creator":
            creator = ctx.get("creator_engine")
            registry = ctx.get("skill_registry")
            synth_code = f"""
def execute_skill(**kwargs):
    return {{'alpha_signal': 'COMPUTED', 'skill': '{args.skill_name}', 'metric': 42.0}}
"""
            synth_res = creator.synthesize_skill(
                skill_name=args.skill_name,
                description=f"Autonomous skill {args.skill_name}",
                category=args.skill_cat,
                code_str=synth_code,
            )
            if synth_res.validation_passed:
                creator.hot_mount_skill(registry, synth_res, ctx=ctx)
            if getattr(args, "json", False):
                print(json.dumps(synth_res.to_dict(), indent=2))
            else:
                print(f"□  Creator Mode Synthesis ({synth_res.status}):")
                print(f"   Skill Name:  {synth_res.skill_name} ({synth_res.category})")
                print(f"   Validated:   {'YES' if synth_res.validation_passed else 'NO'}")
                print(f"   Score:       {synth_res.backtest_score}")
                print(f"   Total Skills in Registry: {len(registry._skills)}")
        elif args.mode == "audit":
            audit_fn = ctx.get("audit_fn")
            audit_res = audit_fn()
            if getattr(args, "json", False):
                print(json.dumps(audit_res, indent=2, default=str))
            else:
                passed = audit_res.get("passed", False)
                print(f"▣  Journal 14-Rule Integrity Audit: {'PASSED' if passed else 'FAILED'}")
                for rule_num, rule_info in audit_res.get("rules", {}).items():
                    status = "✔" if rule_info.get("passed") else ""
                    print(f"   [{status}] Rule {rule_num}: {rule_info.get('name', 'Rule')} ({rule_info.get('checked_records', 0)} checked)")
        elif args.mode == "info":
            print(f" Active Harness Context: {ctx}")
            print(f"   Mounted Services: {list(ctx._services.keys())}")
            print(f"   Mounted Plugins:  {list(ctx._plugins.keys())}")
        ctx.dispose()
        return

    if args.cmd == "workflow":
        from core.harness import TradingContext
        from core.harness.plugins import (
            RiskManagementPlugin, TradingMemoryPlugin, BrokerPlugin,
            CausalIntelligencePlugin, DataHubPlugin, DeliberativeGraphPlugin,
            SkillsPlugin,
            JournalAuditPlugin, AnalyticalFrameworksPlugin, KronosIntelligencePlugin,
            ExecutionSafetyPlugin, CointegrationGovernancePlugin, QuantWorkflowsPlugin,
            MacroEventAlphaPlugin,
        )
        from core.harness.workflows import QuantWorkflowEngine

        ctx = TradingContext(name="WorkflowCLIContext")
        ctx.plugin(TradingMemoryPlugin, db_path=args.journal_path)
        ctx.plugin(RiskManagementPlugin, db_path=args.journal_path, balance=0.0, max_risk_pct=0.01)
        ctx.plugin(BrokerPlugin, paper_mode=True, db_path=args.journal_path)
        ctx.plugin(CausalIntelligencePlugin, db_path=args.journal_path)
        ctx.plugin(DataHubPlugin)
        ctx.plugin(DeliberativeGraphPlugin, db_path=args.journal_path)
        ctx.plugin(SkillsPlugin, db_path=args.journal_path)
        ctx.plugin(AnalyticalFrameworksPlugin)
        ctx.plugin(KronosIntelligencePlugin)
        ctx.plugin(ExecutionSafetyPlugin)
        ctx.plugin(CointegrationGovernancePlugin)
        ctx.plugin(MacroEventAlphaPlugin)
        ctx.plugin(QuantWorkflowsPlugin)

        engine: QuantWorkflowEngine = ctx.get("quant_workflow_engine")

        if args.type == "list":
            print("≡ Available Institutional Quant & Algorithmic Workflows:")
            for wf in engine.list_workflows():
                print(f"   • {wf['name']:<25} : {wf['description']}")
            ctx.dispose()
            return

        normalized_type = args.type.replace("-", "_")
        snapshot = {
            "symbol": args.symbol,
            "pair_asset": getattr(args, "pair_asset", "GBPUSD"),
            "event_name": getattr(args, "event", "NFP"),
            "actual": getattr(args, "actual", 220.0),
            "consensus": getattr(args, "consensus", 180.0),
            "previous": getattr(args, "previous", 175.0),
            "price": 1.0850,
            "atr": 0.0015,
            "bid": 1.0849,
            "ask": 1.0851,
        }

        result = engine.execute_workflow(
            workflow_type=normalized_type,
            ctx=ctx,
            symbol=args.symbol,
            market_snapshot=snapshot,
        )

        if getattr(args, "json", False):
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f" Quant Workflow '{result.workflow_name}' Completed for {result.symbol}:")
            print(f"   Decision: {result.decision} | Ticket: {result.execution_ticket} | Latency: {result.total_latency_ms:.2f}ms")
            print("   --- Workflow Pipeline Stages ---")
            for step in result.steps:
                status = "✔" if step.passed else ""
                reason_str = f" ({step.reason})" if step.reason else ""
                print(f"   [{status}] {step.step_name:<40} {step.duration_ms:>6.2f}ms{reason_str}")
            if result.proposal:
                print(f"   Proposal: {result.proposal['side']} @ {result.proposal['entry_price']:.5f} (SL: {result.proposal['stop_loss']:.5f}, TP: {result.proposal['take_profit']:.5f})")

        ctx.dispose()
        return

    if args.cmd == "data":
        if mt5 is None and not paper:
            raise RuntimeError("MetaTrader5 not installed.")
        if not paper:
            client = MT5Client(cfg)
            client.login()
        else:
            raise RuntimeError(
                "paper data command refuses random candles; use broker_replay.py "
                "or provide an explicit deterministic fixture"
            )

    # Manual order-entry CLIs intentionally do not exist outside the
    # autonomous cycle: every live order requires an immutable order intent
    # produced by validator -> arbiter -> margin/cost gates -> intent ->
    # submission (docs/INCIDENT_RUNBOOKS.md). Refuse before any login.
    if args.cmd in {"pending-order", "modify-order", "cancel-order"}:
        print(json.dumps({
            "status": "REFUSED",
            "command": args.cmd,
            "reason": ("manual CLI order entry is not permitted; orders are only "
                       "created by the autonomous cycle with an immutable order "
                       "intent after deterministic validation and risk gating"),
            "execution_authority": False,
        }, indent=2))
        return
    if args.cmd == "benchmark-llm":
        print(json.dumps({
            "status": "REFUSED",
            "command": "benchmark-llm",
            "reason": ("ad-hoc LLM benchmarking is not exposed on this surface; use "
                       "`journal baseline-status`, `research-sim`, or `harness` for "
                       "evidence-recorded evaluation runs"),
            "execution_authority": False,
        }, indent=2))
        return

    # Initialize MT5 Client (if not paper or if paper needs real data)
    client = None
    if args.cmd == "llm-trade" and not paper and not (getattr(args, "demo", False) or getattr(args, "live", False)):
        raise RuntimeError("Refusing to trade: choose --paper, --demo, or --live explicitly.")
    if getattr(args, "live", False):
        if not live_trading_explicitly_enabled():
            raise RuntimeError("Refusing live trading: set LLM_TRADER_ALLOW_LIVE=true and restart.")
        requested_risk = safe_risk_fraction(args.risk_per_trade) if hasattr(args, "risk_per_trade") else None
        require_live_rollout(risk_fraction=requested_risk)
        if not getattr(args, "yes", False):
            confirm_live("start LIVE trading", auto_yes=False)
    # Read-only surfaces manage their own connection below (terminal-session
    # fallback) and must not hit the strict credential gate.
    if mt5 is not None and not paper and args.cmd not in {
            "account-info", "positions", "orders", "history",
            "diagnose", "test-live", "benchmark"}:
        client = MT5Client(cfg)
        if not client.login():
            sys.exit(1)

    # Paper mode runs all execution through PaperBroker but its ANALYSIS
    # path consumes real broker candles/ticks via the module-level MT5
    # connection.  Attach to the running terminal read-only (no credential
    # login, no order path) so candle confirmation works; without this,
    # every cycle fail-closed HOLDs with "MT5 terminal is unavailable".
    if args.cmd == "llm-trade" and paper:
        _ensure_paper_market_data_session()

    # Read-only broker/diagnostic surfaces: prefer a full authenticated login
    # but fall back to an already-signed-in terminal session so inspection
    # works without stored credentials. These paths never place orders.
    if args.cmd in {"account-info", "positions", "orders", "history",
                    "diagnose", "test-live", "benchmark"}:
        if mt5 is None:
            raise RuntimeError("MetaTrader5 package not installed")
        session_mode = None
        try:
            probe_client = MT5Client(cfg)
            if probe_client.login():
                client = probe_client
                session_mode = "authenticated_login"
        except Exception:
            client = None
        if client is None and mt5.initialize():
            client = True  # module-level connection sentinel for read-only helpers
            session_mode = "terminal_session"
        if not client:
            raise RuntimeError(f"{args.cmd} requires a running MT5 terminal or credentials")

    if args.cmd == "broker-preflight":
        if not client:
            raise RuntimeError("broker-preflight requires an authenticated MT5 terminal")
        requested = [item.strip() for item in args.symbols.split(",") if item.strip()]
        resolved = preflight_symbols(requested)
        print(json.dumps(TradingStore().get_state("broker_preflight", {}), indent=2))
        return

    # Read-only broker/diagnostic surfaces (require an authenticated terminal).
    if args.cmd in {"account-info", "positions", "orders", "history",
                    "diagnose", "test-live", "benchmark"}:
        if mt5 is None or not client:
            raise RuntimeError(f"{args.cmd} requires an authenticated MT5 terminal")

        if args.cmd == "account-info":
            info = mt5.account_info()
            if info is None:
                raise RuntimeError("account_info unavailable; check terminal login")
            payload = info._asdict() if hasattr(info, "_asdict") else vars(info)
            print(json.dumps({"account": payload, "session_mode": session_mode,
                              "execution_authority": False}, indent=2))
            return

        if args.cmd == "positions":
            print(json.dumps({"positions": positions_overview(),
                              "session_mode": session_mode,
                              "execution_authority": False}, indent=2))
            return

        if args.cmd == "orders":
            print(json.dumps({"orders": orders_overview(),
                              "session_mode": session_mode,
                              "execution_authority": False}, indent=2))
            return

        if args.cmd == "history":
            # Local import: a function-scope `import pandas as pd` later in
            # main() shadows the module global, so without this the handler
            # raises UnboundLocalError on first use.
            import pandas as pd
            start = pd.Timestamp(args.start).to_pydatetime()
            end = pd.Timestamp(args.end).to_pydatetime()
            print(json.dumps({**history_overview(start, end),
                              "start": args.start, "end": args.end,
                              "execution_authority": False}, indent=2))
            return

        if args.cmd == "diagnose":
            from operations.system_doctor import run as run_system_doctor
            print(json.dumps(run_system_doctor(), indent=2))
            return

        if args.cmd == "test-live":
            account = mt5.account_info()
            terminal = mt5.terminal_info()
            if account is None or terminal is None:
                raise RuntimeError("terminal/account probe failed; not connected")
            print(json.dumps({
                "probe": "READ_ONLY_CONNECTIVITY",
                "session_mode": session_mode,
                "connected": True,
                "login": getattr(account, "login", None),
                "server": getattr(account, "server", ""),
                "trade_allowed_by_terminal": bool(getattr(terminal, "trade_allowed", False)),
                "account_trade_mode": int(getattr(account, "trade_mode", -1)),
                "trade_permission": bool(getattr(account, "trade_allowed", False)),
                "execution_authority": False,
                "note": ("read-only probe only; it never places, modifies, or closes "
                         "any order"),
            }, indent=2))
            return

        if args.cmd == "benchmark":
            timings = benchmark(args.symbol, args.timeframe, int(args.count))
            print(json.dumps({**timings, "symbol": args.symbol,
                              "timeframe": args.timeframe, "count": int(args.count),
                              "execution_authority": False}, indent=2))
            return
            
    # News Gate Backtest (previously library-only; now reachable via CLI)
    if args.cmd == "news-gate-backtest":
        try:
            from research.news_gate_backtest import NewsGateBacktest
            bt = NewsGateBacktest()
            if getattr(args, "synthetic", False):
                result = bt.run(symbol=args.symbol)
            else:
                # Falls back to synthetic internally when MT5 history is unavailable.
                result = bt.run_from_mt5_history(
                    symbol=args.symbol, timeframe=args.timeframe, days_back=args.days_back)
            print(result.summary())
        except Exception as exc:
            logging.getLogger("main").exception("news-gate-backtest failed: %s", exc)
            print(f"Error: news-gate-backtest failed: {exc}")
            sys.exit(1)
        return

    # Dual-Engine Backtest (VectorBT / MT5 / Hybrid)
    if args.cmd == "backtest":
        try:
            # pd/np come from module scope — local imports here shadow them
            # for the earlier history-overview branch (pd.Timestamp at ~3210).
            bars_df = None
            if not getattr(args, "paper", False) and client and mt5:
                try:
                    from data_fetcher import DataFetcher
                    fetcher = DataFetcher(client)
                    bars_df = fetcher.get_historical_data(args.symbol, args.timeframe, int(args.count))
                except Exception as fetch_err:
                    logging.getLogger("main").warning("Could not fetch MT5 data: %s, falling back to synthetic", fetch_err)

            if bars_df is None or bars_df.empty:
                np.random.seed(42)
                n_bars = int(args.count)
                dt_index = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n_bars, freq="15min")
                returns = np.random.normal(0.0001, 0.0015, n_bars)
                price = 1.0850 * np.exp(np.cumsum(returns))
                highs = price * (1.0 + np.abs(np.random.normal(0, 0.0008, n_bars)))
                lows = price * (1.0 - np.abs(np.random.normal(0, 0.0008, n_bars)))
                opens = price * (1.0 + np.random.normal(0, 0.0004, n_bars))
                bars_df = pd.DataFrame({
                    "open": opens,
                    "high": highs,
                    "low": lows,
                    "close": price,
                    "time": dt_index,
                })

            print("\n" + "=" * 60)
            print(f"  DUAL-ENGINE BACKTEST: {args.engine.upper()} | {args.symbol} {args.timeframe}")
            print(f"  Bars: {len(bars_df)} | Fast EMA: {args.fast} | Slow EMA: {args.slow}")
            print("=" * 60 + "\n")

            if args.engine == "vectorbt":
                from research.vbt_engine import VectorizedBacktestEngine
                vbt_eng = VectorizedBacktestEngine()
                if getattr(args, "sweep", False):
                    sweep_res = vbt_eng.run_grid_sweep(bars_df, symbol=args.symbol, timeframe=args.timeframe)
                    print(f"Grid sweep evaluated {len(sweep_res)} combinations.")
                    print(f"Top Parameter Sets:")
                    for idx, (p, m) in enumerate(sweep_res[:5], 1):
                        print(f"  #{idx}: Fast={p['fast_period']}, Slow={p['slow_period']} -> Sharpe: {m.sharpe_ratio:.2f}, Ret: {m.total_return_pct:.2f}%, MaxDD: {m.max_drawdown_pct:.2f}%, Trades: {m.total_trades}")
                else:
                    metrics = vbt_eng.run_crossover_strategy(bars_df, fast_period=args.fast, slow_period=args.slow, symbol=args.symbol, timeframe=args.timeframe)
                    print(json.dumps(metrics.to_dict(), indent=2))

            elif args.engine == "mt5":
                from research.mt5_backtest_engine import MT5BrokerBacktestEngine
                mt5_eng = MT5BrokerBacktestEngine()
                full_res = mt5_eng.run_full_autonomous_backtest(
                    symbol=args.symbol,
                    timeframe=args.timeframe,
                    count=int(args.count),
                    strategy_name=getattr(args, "strategy", "crossover") or "crossover",
                    fast_period=args.fast,
                    slow_period=args.slow,
                    sl_pips=args.sl_pips,
                    tp_pips=args.tp_pips,
                )
                print(json.dumps(full_res["metrics"], indent=2))
                print("\n" + "=" * 60)
                print("  [1/4] BROKER SPECIFICATIONS & MT5 LIVE ACQUISITION")
                print("=" * 60)
                print(json.dumps(full_res["broker_specs"], indent=2))
                print("\n" + "=" * 60)
                print("  [2/4] DATA IMPROVEMENT (Quality, Regimes, Volatility Profiles)")
                print("=" * 60)
                print(json.dumps(full_res["data_improvement"], indent=2))
                print("\n" + "=" * 60)
                print("  [3/4] STRATEGY IMPROVEMENT (DSR Gate, CPCV, StrategyLifecycle)")
                print("=" * 60)
                print(json.dumps(full_res["strategy_improvement"], indent=2))
                print("\n" + "=" * 60)
                print("  [4/4] JOURNALING & REFLECTION (trading_state.db & Playbook)")
                print("=" * 60)
                print(f"Journal Run ID: {full_res['journaling'].get('run_id')} | Trades Logged: {full_res['journaling'].get('trades_logged')}")
                print(json.dumps(full_res["reflection"], indent=2))

            elif args.engine == "hybrid":
                from research.hybrid_backtest_pipeline import HybridBacktestPipeline
                pipe = HybridBacktestPipeline()
                if getattr(args, "sweep", False):
                    sweep_res = pipe.run_two_tier_sweep(bars_df, symbol=args.symbol, timeframe=args.timeframe)
                    print(f"Two-Tier Funnel Results ({len(sweep_res)} candidates evaluated):")
                    for p, comp in sweep_res:
                        verdict = "PASS" if comp.passes_promotion else "FAIL"
                        print(f"\n  Params: Fast={p['fast_period']}, Slow={p['slow_period']} -> VERDICT: {verdict}")
                        print(f"    Tier 1 VectorBT Sharpe: {comp.vbt_metrics.sharpe_ratio:.2f} | Return: {comp.vbt_metrics.total_return_pct:.2f}%")
                        print(f"    Tier 2 MT5 Replay Sharpe: {comp.mt5_metrics.sharpe_ratio:.2f} | Return: {comp.mt5_metrics.total_return_pct:.2f}%")
                        print(f"    Sharpe Friction Decay:   {comp.sharpe_delta:.1f}%")
                        if comp.rejection_reasons:
                            print(f"    Rejection Reasons: {comp.rejection_reasons}")
                else:
                    close = bars_df["close"]
                    fast_ema = close.ewm(span=args.fast, adjust=False).mean()
                    slow_ema = close.ewm(span=args.slow, adjust=False).mean()
                    entries = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
                    exits = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))
                    comp = pipe.evaluate_strategy(bars_df, entries=entries.fillna(False), exits=exits.fillna(False), symbol=args.symbol, timeframe=args.timeframe, sl_pips=args.sl_pips, tp_pips=args.tp_pips)
                    print(json.dumps(comp.to_dict(), indent=2))
                    print(f"\nHybrid Two-Tier Promotion Verdict: {'PROMOTED' if comp.passes_promotion else 'REJECTED'}")
                    if comp.rejection_reasons:
                        print(f"Reasons: {comp.rejection_reasons}")
        except Exception as exc:
            logging.getLogger("main").exception("backtest failed: %s", exc)
            print(f"Error: backtest execution failed: {exc}")
            sys.exit(1)
        return

    # Autonomous Web & Academic Research (SSRN, ArXiv, Web, Fed)
    if args.cmd == "web-research":
        try:
            from research.autonomous_web_researcher import AutonomousWebResearcher
            researcher = AutonomousWebResearcher()
            print("\n" + "=" * 60)
            print(f"  AUTONOMOUS WEB RESEARCHER: Query='{args.query}' | Source={args.source.upper()}")
            print("=" * 60 + "\n")

            if args.source == "all":
                res = researcher.investigate(args.query, max_per_source=args.count)
                print(json.dumps(res, indent=2))
            elif args.source == "ssrn":
                items = researcher.search_ssrn(args.query, max_results=args.count)
                print(json.dumps([i.to_dict() for i in items], indent=2))
            elif args.source == "arxiv":
                items = researcher.search_arxiv(args.query, max_results=args.count)
                print(json.dumps([i.to_dict() for i in items], indent=2))
            elif args.source == "web":
                items = researcher.search_web(args.query, max_results=args.count)
                print(json.dumps([i.to_dict() for i in items], indent=2))
            elif args.source == "fed":
                items = researcher.fetch_macro_rss(max_entries=args.count)
                print(json.dumps([i.to_dict() for i in items], indent=2))
        except Exception as exc:
            logging.getLogger("main").exception("web-research failed: %s", exc)
            print(f"Error: web-research execution failed: {exc}")
            sys.exit(1)
        return


    # ── Demo Trading Orchestrator (live training pipeline) ───────────────────
    if args.cmd == "demo-run":
        if not DEMO_ORCH_AVAILABLE:
            print("ERROR: DemoTradingOrchestrator not available. Check core/demo_trading_orchestrator.py")
            sys.exit(1)

        print("=" * 60)
        print("AUTONOMOUS DEMO TRADING SESSION")
        print("=" * 60)
        print("Symbol: %s  Timeframe: %s" % (args.symbol, args.timeframe))
        print("Poll interval: %.1fs  Max cycles: %d" % (args.poll_interval, args.loops))
        print("Validate every: %d cycles" % args.validate_every)
        print()

        # Initialize MT5
        import MetaTrader5 as mt5
        if not mt5.initialize():
            print("MT5 initialization failed:", mt5.last_error())
            sys.exit(1)

        mt5_info = mt5.account_info()
        print("MT5 Account: %s (%s)" % (mt5_info.login, mt5_info.server))
        print("Balance: $%.2f  Equity: $%.2f" % (mt5_info.balance, mt5_info.equity))
        print()

        # Create components
        # NOTE: use module-level TradingStore import (line ~66) — a local import
        # here would shadow it for ALL earlier main() CLI branches
        # (UnboundLocalError in journal/reconciliation printers).
        from core.demo_trading_orchestrator import DemoTradingOrchestrator

        store = TradingStore()
        operations = None  # Will be set if operations module available

        # Find data provider
        # NOTE: MarketDataProvider requires a risk_manager since the P0-gates
        # work (deterministic walk-forward/overfit gates read risk state).
        # A demo-scoped RiskManager anchored to the live MT5 balance is used
        # here, with its own state file so demo sessions never write the
        # production risk_state.json. The old bare `except Exception` that
        # swallowed construction errors left data_provider=None and every
        # demo cycle crashed later with `NoneType.fetch_data`.
        try:
            from data.market_data_provider import MarketDataProvider
            from core.risk_manager import RiskManager as _DemoRiskManager
            _demo_risk = _DemoRiskManager(
                balance=float(mt5_info.balance),
                state_file="risk_state_demo.json",
            )
            data_provider = MarketDataProvider(client=mt5, risk_manager=_demo_risk)
        except Exception as _dp_exc:
            data_provider = None
            print(f"WARNING: MarketDataProvider not available ({_dp_exc}), demo will use raw MT5 data")

        # Create orchestrator
        demo_orch = DemoTradingOrchestrator(
            store=store,
            operations=operations,
            data_provider=data_provider,
            risk=None,
            symbol=args.symbol,
            timeframe=args.timeframe,
            poll_interval=args.poll_interval,
            adapt_every_n_cycles=args.adapt_every,
            validate_every_n_cycles=args.validate_every,
        )

        # Callbacks
        def on_validation(result):
            v = result
            print("[VALIDATION] verdict=%s trades=%d can_promote=%s" % (
                v["verdict"], v["trade_count"], v["can_promote"]))
            if v.get("reasons"):
                for r in v["reasons"]:
                    print("  - %s" % r)

        def on_adaptation(result):
            print("[ADAPTATION] cycles=%d regimes=%s recommendations=%d" % (
                result["cycle"],
                list(result["regime_performance"].keys()),
                len(result["recommendations"]),
            ))

        demo_orch.set_callbacks(
            on_validation=on_validation,
            on_adaptation=on_adaptation,
        )

        # Run loop
        print("Starting demo session...")
        print("-" * 60)

        try:
            cycle_count = 0
            while args.continuous or cycle_count < args.loops:
                cycle_count += 1
                result = demo_orch.run_cycle()

                # Print cycle summary
                action = result.get("decision", "HOLD")
                duration = result.get("duration_ms", 0)
                if action != "HOLD":
                    print("[CYCLE %d] %s %s (%.0fms)" % (
                        cycle_count, action, args.symbol, duration))
                elif cycle_count % 10 == 0:
                    print("[CYCLE %d] %d trades collected (%.0fms)" % (
                        cycle_count, len(demo_orch.collector.trade_outcomes), duration))

                time.sleep(max(0.1, args.poll_interval))

        except KeyboardInterrupt:
            print("\nDemo session interrupted by user")
        finally:
            # Final report
            print()
            print("=" * 60)
            print("DEMO SESSION COMPLETE")
            print("=" * 60)
            report = demo_orch.get_readiness_report()
            print("Total cycles: %d" % report["total_cycles"])
            print("Total trades: %d" % report["total_trades"])
            print("Validation verdict: %s" % report["validation"]["verdict"])
            print("Can promote: %s" % report["validation"]["can_promote"])
            print("Deployment ready: %s" % report["deployment_ready"])
            if report["regime_performance"]:
                print("Regime performance:")
                for regime, perf in report["regime_performance"].items():
                    print("  %s: %d trades, WR=%.1f%%, Sharpe=%.2f, P&L=$%.2f" % (
                        regime, perf["count"], perf["win_rate"]*100,
                        perf["sharpe"], perf["total_pnl"]))
            print("RL policy: %d states, %d updates" % (
                report["rl_policy"]["states"], report["rl_policy"]["updates"]))
            print()

            # Persist final state
            try:
                store.set_state("demo_session_last_report", report)
                store.set_state("demo_session_total_trades", report["total_trades"])
            except Exception:
                pass

            mt5.shutdown()
            print("MT5 shutdown complete.")

    # LLM Trading Orchestrator
    if args.cmd == "llm-trade":
# Single-instance engine lock (CC-F9, PR 3.4), kept from origin/main.
        # An OS-level mutex on engine.lock, independent of the PID lease below,
        # so a stale pid file can never be the only thing standing between two
        # engines.
        from core.file_lock import FileLock
        _lock_path = os.environ.get("LLM_TRADER_LOCK_PATH", "").strip() or os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine.lock")
        global _engine_file_lock
        _engine_file_lock = FileLock(_lock_path)
        if not _engine_file_lock.acquire(non_blocking=True):
            _holder_pid = _engine_file_lock.read_pid()
            print(f"[lifecycle] engine.lock held by another process (pid {_holder_pid or 'unknown'}); exiting with rc 3.")
            sys.exit(3)
        _engine_file_lock.write_pid(os.getpid())

        # Single-instance lifecycle: publish PID so the API/deck can guard
        # against double-spawn and offer a real stop. Best-effort only.
        # Lease-backed: the writer stamps {pid, created_at, stamped_at} and a
        # daemon heartbeat re-stamps it, so a crash left behind a PID file is
        # detected by any observer (operations/live_state_probe.py) instead of
        # lying for hours - and a pid Windows later recycles cannot pass for us.
        _pid_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine.pid")
        try:
            from operations.live_state_probe import (claim_pid_lease, read_pid_lease,
                                                     start_pid_lease_heartbeat, sweep_live_state)

            # Single-instance guard as a LEASE: a live holder wins, a dead or
            # stale one is taken over. Refusing here is what stops two engines
            # thrashing the journal DB and racing the broker.
            _claim = claim_pid_lease(_pid_path)
            if not _claim["acquired"]:
                print(f"[lifecycle] REFUSING TO START: another engine (pid {_claim.get('pid')}) "
                      f"already holds engine.pid ({_claim.get('reason')}). "
                      f"Stop it first (delete engine.pid or use the API stop command).")
                sys.exit(2)

            # Recover any stale boot/liveness state from a previous crash:
            # expired schedule claims, ghost agent_runs, supervisor lying
            # about RUNNING. Every finding is logged as a WARNING (P0-1).
            # NOTE: no `orch` here - it is constructed ~380 lines further down,
            # so referencing it raised NameError and skipped the sweep entirely.
            try:
                _sweep = sweep_live_state(getattr(args, "journal_path", None) or "trading_state.db",
                                          _pid_path)
                if _sweep["released_claims"] or _sweep["recovered_runs"] or (_sweep["supervisor"] or {}).get("recovered"):
                    print(f"[lifecycle] live-state sweep: {_sweep['released_claims']} stale claim(s) "
                          f"released, {_sweep['recovered_runs']} ghost run(s) recovered, "
                          f"supervisor recovered={bool((_sweep['supervisor'] or {}).get('recovered'))}")
            except Exception as _sweep_exc:
                logging.getLogger("main").warning("live-state sweep skipped: %s", _sweep_exc)

            import atexit as _atexit

            # Re-stamp the lease while we live. Without this the file is a
            # stale artifact again after PID_LEASE_MAX_AGE_S and every observer
            # (watchdog, GUI, operator) would call a healthy engine dead.
            start_pid_lease_heartbeat(_pid_path)

            def _cleanup_pid():
                try:
                    # Ownership-checked: never delete a lease a successor took.
                    if read_pid_lease(_pid_path).get("pid") == os.getpid():
                        os.remove(_pid_path)
                except OSError:
                    pass
                try:
                    if _engine_file_lock:
                        _engine_file_lock.release()
                except Exception:
                    pass

            _atexit.register(_cleanup_pid)
        except OSError as _pid_exc:
            print(f"[lifecycle] engine.pid write failed (non-fatal): {_pid_exc}")
        except SystemExit:
            raise
        except Exception as _pid_lifecycle_exc:
            # pid lease / sweep are boot-enhancements, never trading blockers.
            logging.getLogger("main").warning("pid lifecycle init failed (non-fatal): %s", _pid_lifecycle_exc)

        # --debug: re-configure logging with trading-critical modules at DEBUG
        if getattr(args, 'debug', False):
            setup_logging(cfg.log_level, debug_modules=True)

        # Handle AUTO symbol
        # Handle AUTO symbol or Multi-Symbol List
        auto_symbol_mode = False
        scanner = None
        watchlist = []
        
        if args.symbol == "AUTO":
            auto_symbol_mode = True
        elif "," in args.symbol:
            # User provided a list (e.g., "XAUUSD,EURUSD") -> Treat as AUTO with restricted watchlist
            watchlist = [s.strip() for s in args.symbol.split(",")]
            auto_symbol_mode = True
            print(f"≡ Multi-symbol mode detected. Watchlist: {watchlist}")

        if client and args.symbol != "AUTO":
            requested_symbols = watchlist if watchlist else [args.symbol]
            resolved_symbols = preflight_symbols(requested_symbols)
            if watchlist:
                watchlist = [resolved_symbols[s] for s in watchlist]
            else:
                args.symbol = resolved_symbols[args.symbol]
        
        if auto_symbol_mode:
            if not MarketScanner:
                raise RuntimeError(
                    "AUTO/watchlist mode requires MarketScanner; refuse silent EURUSD default. "
                    "Pass an explicit --symbol or install/enable the scanner module."
                )
            else:
                print("⌕ AUTO/Watchlist mode selected. Initializing Market Scanner...")
                # Scanner initialized later
        else:
             print(f" Single symbol mode: {args.symbol}")
            
        if args.timeframe == "AUTO":
            print("▣ AUTO schedules independent swing (H1) and scalp (M15) engines.")

        # Select Model Interactively
        model_name = args.model
        if args.select_model and MODEL_SELECTOR_AVAILABLE:
            selector = ModelSelector()
            model_name = selector.select_model()
            print(f"Selected Model: {model_name}")
        
        # Initialize Components
        risk_fraction = safe_risk_fraction(args.risk_per_trade)
        if risk_fraction != args.risk_per_trade:
            logging.getLogger("main").info("Normalized UI risk input %.4f to %.2f%%", args.risk_per_trade, risk_fraction * 100)

        initial_balance = 0.0
        if client and hasattr(client, "account_info"):
            try:
                acc = client.account_info()
                if acc and getattr(acc, "balance", 0) > 0:
                    initial_balance = float(acc.balance)
                    logging.getLogger("main").info("Dynamically seeded live/demo account balance: $%.2f", initial_balance)
            except Exception as exc:
                logging.getLogger("main").warning("Failed to retrieve live MT5 account balance at boot: %s", exc)
        elif hasattr(cfg, "balance") and getattr(cfg, "balance", 0) > 0:
            initial_balance = float(cfg.balance)

        # Fallback / paper balance seed: never allow 0.0 (prevents RiskManager ValueError on boot)
        if initial_balance <= 0.0:
            env_bal = os.getenv("PAPER_EQUITY") or os.getenv("PAPER_BALANCE")
            if env_bal:
                try:
                    initial_balance = float(env_bal)
                except (ValueError, TypeError):
                    initial_balance = 0.0
            if initial_balance <= 0.0:
                try:
                    # NOTE: TradingStore is module-level (line ~66); a local import here
                    # would shadow it for ALL earlier main() CLI branches
                    # (UnboundLocalError in kill-switch/journal/reconciliation printers).
                    _chk_store = TradingStore("trading_state.db")
                    _r_state = _chk_store.get_state("risk_manager_state") or {}
                    _persisted_eq = float(_r_state.get("peak_equity") or _r_state.get("initial_balance") or _r_state.get("start_equity") or 0.0)
                    if _persisted_eq > 0.0:
                        initial_balance = _persisted_eq
                except Exception:
                    pass
            if initial_balance <= 0.0 and hasattr(cfg, "paper_balance") and getattr(cfg, "paper_balance", 0) > 0:
                initial_balance = float(cfg.paper_balance)
            if initial_balance <= 0.0:
                initial_balance = 10000.0
            logging.getLogger("main").info("Seeded paper/fallback account balance: $%.2f", initial_balance)

        perf = PerformanceMonitor("trading_rewards.json")
        
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        # OpenRouter is only mandatory when it is actually part of the selected
        # provider set; Cerebras/NVIDIA-only deployments must be able to start.
        requested_providers = [
            p.strip().lower()
            for p in (getattr(args, "llm_providers", "") or "").split(",")
            if p.strip()
        ]
        openrouter_required = (
            not requested_providers or any(p.startswith("openrouter") for p in requested_providers)
        )
        if openrouter_required and not api_key:
            print("Error: OPENROUTER_API_KEY not found. Set it in .env, or restrict --llm-providers to non-OpenRouter providers")
            sys.exit(1)
        
        llm_router = None
        if LLM_ROUTER_AVAILABLE:
            try:
                llm_router = get_router()
                active_providers = None
                if hasattr(args, 'llm_providers') and args.llm_providers:
                    active_providers = [p.strip() for p in args.llm_providers.split(",") if p.strip()]
                max_keys = getattr(args, 'llm_provider_count', 0) or 0
                provider_models = {}
                if hasattr(args, 'llm_models') and args.llm_models:
                    for pair in args.llm_models.split(","):
                        pair = pair.strip()
                        if ":" in pair:
                            prov, model = pair.split(":", 1)
                            provider_models[prov.strip()] = model.strip()
                if active_providers or max_keys > 0 or provider_models:
                    llm_router.configure(active_providers=active_providers, max_keys_per_provider=max_keys, provider_models=provider_models)
                status = llm_router.get_status()
                provider_count = len([p for p, cfg in status.get("providers", {}).items() if cfg.get("keys", 0) > 0])
                agent_count = len(status.get("agents", {}))
                logging.getLogger("main").info(f"LLM Router initialized: {provider_count} providers, {agent_count} agents")
                if provider_count == 0:
                    # A keyless router means every decision degenerates into a
                    # deterministic HOLD forever; refuse that silent loop.
                    logging.getLogger("main").error(
                        "LLM Router has zero key-configured providers; refusing silent HOLD loop"
                    )
                    print(
                        "Error: No LLM providers have API keys configured "
                        "(every decision would be a deterministic HOLD).\n"
                        "Set at least one provider key in .env "
                        "(OPENROUTER_API_KEY, CEREBRAS_API_KEY, NVIDIA_API_KEY, ROUTESME_KEY, "
                        "OMNIROUTER_KEY)."
                    )
                    sys.exit(1)
            except Exception as e:
                logging.getLogger("main").warning(f"LLM Router initialization failed: {e}. Using legacy mode.")
                llm_router = None
            
        # --- Plugin Profile Mounting (DeepSeek-style named compositions) ---
        # ALL service creation goes through the plugin system.  The profile is
        # auto-selected from --paper/--demo/--live flags unless --profile or
        # --harness overrides it.  This eliminates the duplicate direct-path
        # RiskManager/TradingStore creation that existed before.
        harness_ctx = None
        _profile_name = getattr(args, "profile", None)

        # List profiles and exit
        if getattr(args, "list_profiles", False):
            from core.harness.quant_models_plugins import list_profiles
            print("Available plugin profiles:")
            for name, desc in list_profiles().items():
                print(f"  {name:12s} — {desc}")
            return

        # Auto-detect profile from trading mode flags
        if _profile_name is None:
            if args.live:
                _profile_name = "live"
            elif getattr(args, "demo", False):
                _profile_name = "paper"  # demo uses paper plugins with live broker
            else:
                _profile_name = "paper"  # default: paper mode

        # Always mount the plugin profile — this is the single creation path
        try:
            from core.harness.kernel import TradingContext
            from core.harness.quant_models_plugins import mount_profile
            from core.state_persistence import migrate_json_states_to_store
            # Auto-migrate JSON state files into the store on first boot
            try:
                _migration = migrate_json_states_to_store("trading_state.db", os.getcwd())
                if _migration.get("imported", 0) > 0:
                    logging.getLogger("main").info(
                        "State migration: %d JSON files backfilled into trading_state.db",
                        _migration["imported"],
                    )
            except Exception as _mig_exc:
                logging.getLogger("main").debug("State migration skipped: %s", _mig_exc)
            harness_ctx = TradingContext(name=f"{_profile_name}_ctx")
            mount_profile(harness_ctx, _profile_name)
            risk = harness_ctx.get("risk_manager")
            # Initialize event bridge so engine events reach the API server's WebSocket
            try:
                from operations import event_bridge as _evt_bridge
                _journal_db = os.getenv("LLM_TRADER_JOURNAL_PATH", "trading_state.db")
                _evt_bridge.init(_journal_db if os.path.exists(_journal_db) else None)
                logging.getLogger("main").info("Event bridge initialized for engine process")
                # Publish the exact boot configuration so the deck shows the real
                # triage mode instead of guessing (auto | laya | off).
                from data.news_triage import triage_mode as _tm
                _evt_bridge.emit("ENGINE_CONFIG", {
                    "news_triage_mode": _tm(),
                    "fast_test": os.environ.get("FAST_TEST", ""),
                    "symbol": getattr(args, "symbol", ""),
                    "timeframe": getattr(args, "timeframe", ""),
                    "paper": bool(getattr(args, "paper", False)),
                }, correlation_id="engine-config")
            except Exception as _eb_exc:
                logging.getLogger("main").warning("Event bridge init failed: %s", _eb_exc)
            logging.getLogger("main").info(
                "Profile '%s' mounted via TradingContext — %d services, %d plugins",
                _profile_name, len(harness_ctx._services), len(harness_ctx._plugins),
            )
            # Start Prometheus metrics exporter (unless disabled)
            if os.environ.get("PROMETHEUS_EXPORTER", "1") != "0":
                try:
                    _prom_port = int(os.environ.get("PROMETHEUS_PORT", "9090"))
                    start_prometheus_exporter(port=_prom_port)
                    logging.getLogger("main").info(
                        "Prometheus metrics exporter started on port %d", _prom_port)
                except Exception as _prom_exc:
                    logging.getLogger("main").debug(
                        "Prometheus exporter start skipped: %s", _prom_exc)
            # Initialize autonomous subsystems
            _memory_orch = None
            _knowledge_retrieval = None
            _meta_improvement = None
            _kronos_fusion = None
            _advanced_rl = None
            try:
                _memory_orch = MemoryOrchestrator()
                logging.getLogger("main").info("Memory Orchestrator initialized (L0-L3 hierarchy)")
            except Exception as _mem_exc:
                logging.getLogger("main").debug("Memory Orchestrator init skipped: %s", _mem_exc)
            try:
                _knowledge_retrieval = KnowledgeRetrieval()
                logging.getLogger("main").info("Knowledge Retrieval initialized (macro + cases + domain)")
            except Exception as _kr_exc:
                logging.getLogger("main").debug("Knowledge Retrieval init skipped: %s", _kr_exc)
            try:
                _meta_improvement = MetaImprovementOrchestrator()
                logging.getLogger("main").info("Meta-Improvement Orchestrator initialized")
            except Exception as _mi_exc:
                logging.getLogger("main").debug("Meta-Improvement Orchestrator init skipped: %s", _mi_exc)
            try:
                _kronos_fusion = KronosSignalFusion()
                logging.getLogger("main").info("Kronos Signal Fusion initialized (multi-horizon)")
            except Exception as _ksf_exc:
                logging.getLogger("main").debug("Kronos Signal Fusion init skipped: %s", _ksf_exc)
            try:
                _advanced_rl = AdvancedRLPipeline()
                logging.getLogger("main").info("Advanced RL Pipeline initialized (multi-agent)")
            except Exception as _arl_exc:
                logging.getLogger("main").debug("Advanced RL Pipeline init skipped: %s", _arl_exc)
            _improvement_pipeline = None
            try:
                _improvement_pipeline = ImprovementPipeline()
                logging.getLogger("main").info("Improvement Pipeline initialized (auto-apply low-risk)")
            except Exception as _ip_exc:
                logging.getLogger("main").debug("Improvement Pipeline init skipped: %s", _ip_exc)
        except Exception as e:
            # Absolute fallback: create minimal services inline (legacy path)
            logging.getLogger("main").warning(
                "Profile mount failed (%s); creating minimal inline services", e
            )
            try:
                from core.state_persistence import migrate_json_states_to_store
                _trading_store = TradingStore("trading_state.db")
                migrate_json_states_to_store("trading_state.db", os.getcwd())
            except Exception:
                _trading_store = None
            risk = RiskManager(balance=initial_balance,
                               risk_per_trade=risk_fraction,
                               max_drawdown=cfg.max_drawdown,
                               cooldown_seconds=cfg.cooldown,
                               max_trades_per_hour=cfg.max_trades_per_hour,
                               store=_trading_store if '_trading_store' in dir() else None)
            # Initialize event bridge for fallback path too
            try:
                from operations import event_bridge as _evt_bridge
                _journal_db = os.getenv("LLM_TRADER_JOURNAL_PATH", "trading_state.db")
                _evt_bridge.init(_journal_db if os.path.exists(_journal_db) else None)
                logging.getLogger("main").info("Event bridge initialized (fallback path)")
            except Exception as _eb_exc:
                logging.getLogger("main").warning("Event bridge init failed (fallback): %s", _eb_exc)
            # Force write actual MT5 balance to risk_state.json
            risk.balance = initial_balance
            risk.initial_balance = initial_balance
            risk.peak_equity = initial_balance
            risk.daily_start_equity = initial_balance
            risk.weekly_start_equity = initial_balance
            risk.save_state()
            logging.getLogger("main").info(
                "Risk state initialized with actual MT5 balance: $%.2f", initial_balance
            )

        # Fail-closed capability gate: a live run must never start under a
        # mounted sandbox profile whose default permission level cannot route
        # real orders. Paper/demo profiles are intentionally SIMULATION-scoped.
        if harness_ctx is not None and harness_ctx.has("sandbox") and getattr(args, "live", False):
            try:
                from core.harness.sandbox import PermissionLevel
                _sandbox = harness_ctx.get("sandbox")
                if _sandbox.default_level < PermissionLevel.ORDER_WRITE:
                    logging.getLogger("main").error(
                        "Live mode requested but mounted profile sandbox lacks ORDER_WRITE "
                        "capability (default=%s); refusing to start (fail-closed).",
                        _sandbox.default_level,
                    )
                    print("Error: live mode requires a profile sandbox with ORDER_WRITE capability.")
                    return
            except Exception as _gate_exc:
                # If the gate itself cannot be evaluated, do not guess — refuse live.
                logging.getLogger("main").error(
                    "Sandbox capability gate could not be evaluated (%s); refusing live start.", _gate_exc)
                print("Error: sandbox capability gate unavailable; refusing live start (fail-closed).")
                return

        journal_mode = "PAPER" if args.paper else ("LIVE" if args.live else "DEMO")
        account_info = client.get_account_info() if client and not args.paper else {}
        # Only the stable account identity is passed to the scope layer; the
        # scope stores a hash and never persists credentials.
        account_fingerprint = authenticated_account_fingerprint(journal_mode, account_info)
        # Pass the plugin-provided store so the orchestrator doesn't create a duplicate
        _plugin_store = harness_ctx.get("trading_store") if harness_ctx else None
        bounded_autonomy = getattr(args, "bounded_autonomy", False)
        # Bind the process-wide StateManager to the authoritative store so
        # diagnostics/TUI/tools can read and patch any registered state key
        # without owning module instances (transport only, zero authority).
        try:
            from state_manager import get_state_manager
            get_state_manager(store=_plugin_store if _plugin_store is not None else "trading_state.db")
        except Exception as _sm_exc:
            logging.getLogger("main").debug("StateManager bind failed: %s", _sm_exc)
        orch_kwargs = dict(client=client, risk_manager=risk, perf_monitor=perf, api_key=api_key, model_name=model_name,
                               llm_router=llm_router, agent_name="trader",
                               journal_mode=journal_mode,
                               account_fingerprint=account_fingerprint,
                               store=_plugin_store,
                               bounded_autonomy=bounded_autonomy)
        orch = None
        if harness_ctx is not None and harness_ctx.has("llm_orchestrator_factory"):
            # Plugin-provided factory (core/harness/live_trading_plugin.py):
            # construct the orchestrator through the mounted TradingContext so
            # the harness path is real wiring, not a parallel dead path.
            try:
                orch = harness_ctx.get("llm_orchestrator_factory")(**orch_kwargs)
                logging.getLogger("main").info(
                    "LLMOrchestrator constructed via harness factory (profile=%s)", _profile_name)
            except Exception as _orch_factory_exc:
                logging.getLogger("main").warning(
                    "Harness orchestrator factory failed (%s); falling back to direct construction",
                    _orch_factory_exc)
                orch = None
        if orch is None:
            try:
                orch = LLMOrchestrator(**orch_kwargs)
            except Exception as _orch_ctor_exc:
                logging.getLogger("main").exception("LLMOrchestrator construction failed: %s", _orch_ctor_exc)
                raise
        # H4: fail rows a dead engine left ACKNOWLEDGED. They were claimed
        # but never applied; re-running them blindly could double-apply, so
        # the operator re-issues deliberately.
        try:
            _boot_store = getattr(orch, "store", None)
            if _boot_store is not None and hasattr(_boot_store, "recover_stale_controls"):
                _recovered = _boot_store.recover_stale_controls()
                if _recovered:
                    logging.getLogger("main").warning(
                        "Recovered %d stale ACKNOWLEDGED control(s) as FAILED (previous engine died mid-apply)",
                        _recovered,
                    )
        except Exception as _rec_exc:
            logging.getLogger("main").debug("Stale control recovery skipped: %s", _rec_exc)
        # Wire TradeManager & QuantWorkflowEngine from harness context to orchestrator
        if harness_ctx is not None:
            orch.harness_ctx = harness_ctx
            # Live order authority (PH2-N1): only this launcher, after the
            # fail-closed ORDER_WRITE gate above, hands the profile sandbox to
            # the orchestrator. Paper/demo profiles never set it.
            if getattr(args, "live", False) and harness_ctx.has("sandbox"):
                orch.order_write_sandbox = harness_ctx.get("sandbox")
                orch.order_write_mode = "LIVE"
            elif getattr(args, "demo", False):
                # Demo runs on the paper profile (SIMULATION sandbox), so order
                # authority is granted here, and only when the broker itself
                # reports a demo account. The orchestrator re-checks per order.
                _acct = client.get_account_info() if client else {}
                _demo_code = getattr(sys.modules.get("MetaTrader5"), "ACCOUNT_TRADE_MODE_DEMO", 0)
                if _acct.get("trade_mode") is not None and int(_acct["trade_mode"]) == int(_demo_code):
                    from core.harness.sandbox import CapabilitySandbox, PermissionLevel
                    orch.order_write_sandbox = CapabilitySandbox(default_level=PermissionLevel.ORDER_WRITE)
                    orch.order_write_mode = "DEMO"
                else:
                    logging.getLogger("main").error(
                        "Demo mode requested but the broker account is not a demo account "
                        "(trade_mode=%s); broker entries stay refused.", _acct.get("trade_mode"))
            if harness_ctx.has("quant_workflow_engine"):
                orch.quant_workflow_engine = harness_ctx.get("quant_workflow_engine")
                logging.getLogger("main").info("QuantWorkflowEngine wired from harness context to orchestrator")
            if harness_ctx.has("trade_manager"):
                try:
                    _harness_tm = harness_ctx.get("trade_manager")
                    orch.wire_trade_manager(_harness_tm)
                    logging.getLogger("main").info("TradeManager wired from harness context to orchestrator")
                except Exception as _tm_exc:
                    logging.getLogger("main").warning("TradeManager wiring failed: %s", _tm_exc)
        if bounded_autonomy and hasattr(orch, "wire_bounded_autonomy"):
            audit_log = os.getenv("BOUNDED_AUTONOMY_AUDIT_LOG", "logs/bounded_autonomy_audit.jsonl")
            orch.wire_bounded_autonomy(enabled=True, audit_log_path=audit_log)
            print(f"≡ Bounded-Autonomy Pipeline Active (NZE 2026: R ∘ V_LLM ∘ S) -> Audit: {audit_log}")
        risk.set_store(orch.store)
        
        print(f"Starting LLM Trader on {args.symbol} ({args.timeframe})...")
        print(f"Mode: {journal_mode}")
        print(f"Model: {orch.model_name}")
        
        # Initialize Display — single canonical TUI is the OpenTUI deck
        # (attach from a second terminal: `python mt5_trader.py deck` or
        # START_DECK.bat).  In-process, state is mirrored to the API server
        # via the web adapter so the deck / web GUI show live data.
        display = None
        if args.tui:
            if sys.stdout.isatty():
                print(" OpenTUI console: attach from a second terminal with `python mt5_trader.py deck` (or START_DECK.bat).")
            if TUI_AVAILABLE and dashboard:
                display = dashboard
                if args.api_sync:
                    display.api_url = args.api_sync
                    print(f" GUI Sync Enabled: {args.api_sync}")
        
        if display:
            display.start()
        
        # Initialize Scanner if needed
        if auto_symbol_mode and MarketScanner:
            scanner = MarketScanner(
                client, fetch_func=fetch_ohlc, api_manager=orch.data_provider.api_manager,
                macro_graph=orch.macro_graph,
            )

        # --- Main Trading Loop ---
        run_status = "STOPPED"
        preflighted_symbols: set[str] = set()

        # --- Ψ External Guardian: independent safety thread (UAQP Patch 1) ---
        # Ψ reads broker API directly, never reads graph state S.
        # Hardcoded thresholds, fail-closed on any exception.
        # NOTE: the guardian is STARTED after the autonomous bootstrap below —
        # its 120s heartbeat watchdog would otherwise trip during the
        # multi-minute bootstrap (no trading loop exists yet to feed
        # heartbeats) and falsely FLATTEN the account at cold start.
        _guardian = None

        # --- Supervisor kernel: sequence boot, gate the loop on health ---
        try:
            from operations.supervisor import get_supervisor
            from operations.autonomy import get_autonomy as _gate_autonomy

            _supervisor = get_supervisor()
            _autonomy_gate = _gate_autonomy()

            def _secrets_scan() -> bool:
                # Warn-only gate: findings recorded, boot never blocked here.
                try:
                    from operations.secrets_guard import scan_standard_files
                    findings = scan_standard_files()
                    return len(findings) == 0
                except Exception:
                    return True

            _supervisor.boot_sequence({
                "config_loaded": lambda: cfg is not None,
                "orchestrator_ready": lambda: orch is not None,
                "journal_reachable": lambda: os.path.exists(orch.store.path) if hasattr(orch, "store") else True,
                "autonomy_state": lambda: _gate_autonomy().snapshot() is not None,
                "secrets_hygiene_warnonly": _secrets_scan,
            })
            if all(c.get("ok") for c in _supervisor.overall_health()["components"].values()):
                _supervisor.ready()
            else:
                _supervisor.transition("DEGRADED", "boot checks incomplete; continuing guarded")
        except Exception as sup_exc:
            logging.getLogger("main").warning("supervisor boot skipped: %s", sup_exc)
        # -----------------------------------------------------------------

        # --- κ Compliance Audit: hash-chained immutable log (UAQP Agent κ) ---
        _audit = None
        try:
            from core.compliance_audit import get_audit
            _audit = get_audit()
            logging.getLogger("main").info("κ Compliance Audit started (hash-chained, append-only)")
        except Exception as _ka_exc:
            logging.getLogger("main").warning("κ Compliance Audit init skipped: %s", _ka_exc)
        # -----------------------------------------------------------------

        # --- Crash Recovery: startup check + shutdown hooks (UAQP Patch 1) ---
        _crash_recovery = None
        try:
            from core.crash_recovery import get_recovery, startup_recovery
            _crash_recovery = get_recovery()
            _recovery_result = startup_recovery()
            if _recovery_result.crash_detected:
                logging.getLogger("main").warning(
                    "⚠  Crash detected — last completed cycle: %d (action: %s)",
                    _recovery_result.last_good_cycle,
                    _recovery_result.action.value,
                )
                for reason in _recovery_result.reasons:
                    logging.getLogger("main").warning("  Recovery reason: %s", reason)
            if _recovery_result.restored_snapshot:
                logging.getLogger("main").info(
                    "  Restored state from cycle %d",
                    _recovery_result.restored_snapshot.cycle_num,
                )
            _crash_recovery.enable_shutdown_snapshot()
            logging.getLogger("main").info(
                "Crash recovery started (hash-chained checkpoint journal, atexit+SIGTERM hooks)"
            )
        except Exception as _cr_exc:
            logging.getLogger("main").warning("Crash recovery init skipped: %s", _cr_exc)
        # -----------------------------------------------------------------

        # --- Autonomous Bootstrap: prepare system before trading ---
        try:
            from core.autonomous_bootstrap import AutonomousBootstrap
            _bootstrap = AutonomousBootstrap(
                # Harness path has no bare `store` binding; the authoritative
                # journal store is the plugin-provided one (None is tolerated).
                store=_plugin_store,
                paper_mode=bool(getattr(args, "paper", False) or getattr(args, "demo", False)),
                client=client,
                config=cfg,
                orchestrator=orch,
                velocity=getattr(orch, "velocity", None) if orch is not None else None,
            )
            _bs_result = _bootstrap.run()
            logging.getLogger("main").info(
                "Bootstrap complete: ready=%s phase=%s duration=%.1fs quality=%.2f",
                _bs_result.ready, _bs_result.phase, _bs_result.duration_seconds,
                _bs_result.quality_score,
            )
            try:
                from operations import event_bridge as _bs_bridge
                _bs_details = dict(_bs_result.phase_results.get("PREPARE", {}).get("details") or {})
                _bs_bridge.emit("BOOTSTRAP_COMPLETE", {
                    "ready": bool(_bs_result.ready),
                    "phase": _bs_result.phase,
                    "duration_seconds": round(_bs_result.duration_seconds, 1),
                    "quality_score": round(_bs_result.quality_score, 3),
                    "news_triage": _bs_details.get("news_triage"),
                    "news_ingest": _bs_details.get("news_ingest"),
                }, correlation_id="engine-bootstrap")
            except Exception:
                pass
            if not _bs_result.ready:
                logging.getLogger("main").error(
                    "BOOTSTRAP BLOCKED — cannot start trading. Blockers:\n  %s",
                    "\n  ".join(_bs_result.blockers),
                )
                print("\n=== BOOTSTRAP FAILED — TRADING BLOCKED ===")
                for _b in _bs_result.blockers:
                    print(f"  \u2717 {_b}")
                print("\nFix the above issues and restart.")
                import sys as _sys
                _sys.exit(1)
        except Exception as _bs_exc:
            logging.getLogger("main").warning("Autonomous bootstrap skipped: %s", _bs_exc)

        # --- Startup Training Pipeline: run initial backtest + training before trading ---
        try:
            logging.getLogger("main").info("Startup training pipeline: running initial backtest + training...")
            _startup_results = {}

            # 1. Initial walk-forward validation
            try:
                from core.walk_forward_validator import WalkForwardValidator
                _wfv = WalkForwardValidator(state_file=None)
                logging.getLogger("main").info("  [1/6] Walk-forward validator initialized")
                _startup_results["walk_forward"] = "initialized"
            except Exception as e:
                logging.getLogger("main").debug("  [1/6] Walk-forward init skipped: %s", e)

            # 2. Initial CPCV backtest
            try:
                from core.cpcv_backtester import run_combinatorial_purged_backtest
                logging.getLogger("main").info("  [2/6] CPCV backtester available")
                _startup_results["cpcv"] = "available"
            except Exception as e:
                logging.getLogger("main").debug("  [2/6] CPCV init skipped: %s", e)

            # 3. Alpha ensemble warm-up
            try:
                from core.alpha_ensemble import compute_alpha_quality
                logging.getLogger("main").info("  [3/6] Alpha ensemble warm-up")
                _startup_results["alpha_ensemble"] = "warmed"
            except Exception as e:
                logging.getLogger("main").debug("  [3/6] Alpha ensemble skipped: %s", e)

            # 4. GA alpha mining (quick 1 generation)
            try:
                from research.alpha_miner.ga_engine import GAEngine, GAConfig
                _ga_sym = args.symbol if args.symbol != "AUTO" else "EURUSD"
                _ga_tf = args.timeframe if args.timeframe != "AUTO" else "H1"
                _ga_bars = int(os.getenv("BACKTEST_VALIDATION_BARS", "4000") or 4000)
                _df = fetch_ohlc(_ga_sym, _ga_tf, count=max(_ga_bars, 500))
                if _df is not None and len(_df) > 100:
                    _ga = GAEngine(config=GAConfig(population_size=15, generations=1, min_ic_threshold=0.005))
                    _ga_result = _ga.evolve(_df, verbose=False)
                    _sig = _ga_result.significant_ics
                    logging.getLogger("main").info(
                        "  [4/6] GA miner: %d alphas evaluated, %d significant "
                        "(Bonferroni over %d trials), best=%.4f",
                        len(_ga_result.all_expressions), len(_sig), _ga_result.n_trials,
                        max((abs(ic) for ic in _ga_result.best_ics), default=0)
                    )
                    _startup_results["ga_miner"] = {
                        "evaluated": len(_ga_result.all_expressions),
                        "significant": len(_sig),
                        "n_trials": _ga_result.n_trials,
                    }
            except Exception as e:
                logging.getLogger("main").debug("  [4/6] GA miner skipped: %s", e)

            # 5. Progressive gate baseline
            try:
                from core.progressive_gates import ProgressiveGateSystem
                _pgs = ProgressiveGateSystem()
                logging.getLogger("main").info("  [5/6] Progressive gates initialized")
                _startup_results["progressive_gates"] = "initialized"
            except Exception as e:
                logging.getLogger("main").debug("  [5/6] Progressive gates skipped: %s", e)

            # 6. Training orchestrator quick pass
            try:
                from research.training_orchestrator import TrainingOrchestrator
                _to = TrainingOrchestrator()
                logging.getLogger("main").info("  [6/6] Training orchestrator available")
                _startup_results["training_orchestrator"] = "available"
            except Exception as e:
                logging.getLogger("main").debug("  [6/6] Training orchestrator skipped: %s", e)

            # 7. Capstone validation (scenario stress + benchmarking)
            try:
                from core.capstone_validator import CapstoneValidator
                _cap = CapstoneValidator()
                _sym = args.symbol if args.symbol != "AUTO" else "EURUSD"
                _cap_result = _cap.run_full_validation(symbol=_sym, timeframe=args.timeframe if args.timeframe != "AUTO" else "H1", n_trades_target=100)
                logging.getLogger("main").info(
                    "  [7/7] Capstone: Grade %s (%.0f/100) %d trades ready=%s",
                    _cap_result.benchmark_grade, _cap_result.benchmark_score,
                    _cap_result.total_trades, _cap_result.ready_for_live,
                )
                _startup_results["capstone"] = {"grade": _cap_result.benchmark_grade, "score": _cap_result.benchmark_score, "trades": _cap_result.total_trades}
            except Exception as e:
                logging.getLogger("main").debug("  [7/7] Capstone validation skipped: %s", e)

            # 8. Automated training (per-symbol RL + OOS)
            try:
                from core.automated_training_manager import AutomatedTrainingManager
                _atm = AutomatedTrainingManager()
                _sym = args.symbol if args.symbol != "AUTO" else "EURUSD"
                _train_result = _atm.run_training_cycle(symbol=_sym, quick=True)
                logging.getLogger("main").info(
                    "  [8/8] Training: OOS_Sharpe=%.3f WFE=%.3f gate=%s (%.1fs)",
                    _train_result.oos_sharpe, _train_result.wfe,
                    _train_result.gate_passed, _train_result.duration_seconds,
                )
                _startup_results["automated_training"] = {"oos_sharpe": _train_result.oos_sharpe, "wfe": _train_result.wfe, "gate": _train_result.gate_passed}
            except Exception as e:
                logging.getLogger("main").debug("  [8/8] Automated training skipped: %s", e)

            logging.getLogger("main").info("Startup training pipeline complete: %s", _startup_results)
        except Exception as _stp_exc:
            logging.getLogger("main").debug("Startup training pipeline skipped: %s", _stp_exc)

        # --- Ψ Guardian starts NOW (post-bootstrap, pre-loop): the trading
        # loop it watches begins next, so the heartbeat watchdog measures
        # loop liveness — not startup duration.
        if not args.paper:
            try:
                from core.external_guardian import start_guardian
                _guardian = start_guardian()
                logging.getLogger("main").info("Ψ External Guardian started (hardcoded thresholds, independent of Γ)")
            except Exception as _g_exc:
                logging.getLogger("main").warning("Ψ Guardian start failed: %s", _g_exc)

        try:
            cycle_count = 0
            # --- DEDICATED CONTROL THREAD (H4) ---
            # Drains operator controls every second, independent of strategy
            # cycles: the main thread can be blocked inside execute_cycle for
            # 70-180 s, and a flatten requested mid-cycle must not wait for it
            # (nor be dropped by the expiry). Gateway calls are lock-safe.
            _control_stop = threading.Event()

            def _control_thread_loop() -> None:
                while not _control_stop.is_set():
                    try:
                        _drain_operator_controls(orch)
                    except Exception as _ctl_exc:
                        logging.getLogger("main").debug("Control thread drain failed: %s", _ctl_exc)
                    _control_stop.wait(1.0)

            _control_thread = threading.Thread(
                target=_control_thread_loop, name="operator-controls", daemon=True,
            )
            _control_thread.start()
            while args.continuous or cycle_count < args.loops:

                # --- DRAIN OPERATOR CONTROLS (PR 3.3) ---
                _drain_operator_controls(orch)

                # --- EXTERNAL STOP FLAG (PU-0161 follow-up 4) ---
                # Checked FIRST, before any work in the iteration: an external
                # guard (RSS wind-down ladder, operator, scheduler) requests a
                # graceful stop by creating this file. We then leave the loop
                # NORMALLY so the `finally` block runs the full cleanup path
                # (Guardian stop, supervisor shutdown, orchestrator shutdown,
                # durable run close, CrashRecovery shutdown snapshot) — the
                # graceful stop that a hard CTRL_BREAK terminate cannot give.
                from core.crash_recovery import shutdown_requested
                if _stop_file_requested() or shutdown_requested():
                    _stop_reason = _acknowledge_stop_file() if _stop_file_requested() else "signal"
                    _stop_msg = (
                        "Stop requested — winding down gracefully after "
                        "this checkpoint (reason: {}).".format(_stop_reason or "signal")
                    )
                    logging.getLogger("main").warning(_stop_msg)
                    if display:
                        display.update_footer(_stop_msg)
                        display.update_execution_log(["■ " + _stop_msg])
                    else:
                        print("\n■ " + _stop_msg)
                    run_status = "STOP_FLAG"
                    break

                # --- PRE-CYCLE HOLIDAY CHECK ---
                # A holiday halts new trading but must not terminate a
                # continuous process: reconciliation, health, kill-switch and
                # memory schedules still need to run. The orchestrator's own
                # holiday guard remains the final no-trade gate.
                holiday_active = bool(orch.data_provider.api_manager and
                                      orch.data_provider.api_manager.is_holiday())
                if holiday_active:
                    _now_mono = time.monotonic()
                    if _now_mono - getattr(orch, "_last_holiday_log_ts", 0.0) > 1800.0:
                        setattr(orch, "_last_holiday_log_ts", _now_mono)
                        msg = "⛔ MARKET HOLIDAY DETECTED. Trading halted; maintenance & research routines active."
                        logging.getLogger("main").warning(msg)
                        if display: display.update_footer(msg)
                        else: print(msg)
                # -------------------------------

                cycle_count += 1

                # --- Autonomous research pipeline tick (every 10 loops) ---
                # Placed BEFORE the holiday/skip gate: discovery, evidence
                # verification and lifecycle scheduling are zero-authority
                # research work that must progress 24/7, including weekends
                # and maintenance-only pulses. Execution itself remains
                # blocked by the holiday gate and DeterministicValidator.
                if cycle_count % 10 == 0:
                    try:
                        from operations.autonomous_pipeline import run_pipeline
                        _pipe = run_pipeline(
                            lifecycle_path=getattr(_plugin_store, "path", "trading_state.db"),
                            outbox_path="alerts_outbox.db",
                        )
                        _inc = _pipe.get("incubation", {})
                        logging.getLogger("main").info(
                            "Autonomous pipeline tick: %d discovered, %d registered, %d skipped, %d flagged | Incubation: %s (%d causal edges, %d heuristics, MAML=%s)",
                            _pipe.get("discovery", {}).get("members", 0),
                            _pipe.get("registered", 0),
                            _pipe.get("skipped", 0),
                            _pipe.get("flagged", 0),
                            _inc.get("status", "N/A"),
                            _inc.get("edges", 0),
                            _inc.get("heuristics", 0),
                            _inc.get("maml_profile", "DEFAULT"),
                        )
                    except Exception as _pipe_exc:
                        logging.getLogger("main").warning(
                            "Autonomous pipeline tick FAILED: %s", _pipe_exc
                        )

                # --- Autonomous recursive self-improvement tick (every 25 loops) ---
                if cycle_count % 25 == 0:
                    try:
                        from core.self_improvement_loop import RecursiveSelfImprovementEngine
                        _rsi_store = getattr(orch, "store", None)
                        _rsi_engine = RecursiveSelfImprovementEngine(state_file="artifacts/self_improvement_state.json")
                        _rsi_res = _rsi_engine.run_improvement_cycle(store=_rsi_store)
                        if _rsi_res:
                            _proposals = _rsi_res if isinstance(_rsi_res, list) else [_rsi_res]
                            for _p in _proposals:
                                _st = getattr(_p, "status", "") if not isinstance(_p, dict) else _p.get("status", "")
                                _imp = getattr(_p, "improvement_pct", 0.0) if not isinstance(_p, dict) else _p.get("improvement_pct", 0.0)
                                _cat = getattr(_p, "category", "") if not isinstance(_p, dict) else _p.get("category", "")
                                if _st in ("APPLIED", "VALIDATED"):
                                    logging.getLogger("main").info(
                                        "Recursive self-improvement cycle %d -> %s (+%.2f%%) | %s",
                                        cycle_count, _st, _imp, _cat
                                    )
                    except Exception as _rsi_exc:
                        logging.getLogger("main").debug("Recursive self-improvement tick skipped: %s", _rsi_exc)

                def _execute_research_sprint(_res_sym: str, _res_tf: str, _orch: Any = None, _paper_mode: bool = True) -> None:
                    try:
                        _r_t0 = time.time()
                        _vbars = int(os.getenv("BACKTEST_VALIDATION_BARS", "4000") or 4000)
                        _vdf = None
                        logging.getLogger("main").info(
                            "🔬 [AUTONOMOUS RESEARCH] starting research sprint for %s (%s)...",
                            _res_sym, _res_tf,
                        )
                        # 1. Continuous Backtest Pipeline (TCE, DSR, CPCV, WFE)
                        try:
                            from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
                            from research.strategy_signal_registry import _REGISTRY as _STRAT_REG
                            _pipeline = ContinuousBacktestPipeline()
                            _strat_names = sorted(_STRAT_REG.keys())
                            try:
                                _vdf = fetch_ohlc(_res_sym, _res_tf, count=max(_vbars, 500))
                            except Exception as _vfetch_exc:
                                logging.getLogger("main").debug(
                                    "Research sweep bars fetch failed (%s); pipeline will fetch per strategy",
                                    _vfetch_exc,
                                )
                            for _sname in _strat_names:
                                try:
                                    _bt_res = _pipeline.run_validation_cycle(
                                        symbol=_res_sym, strategy_name=_sname, timeframe=_res_tf,
                                        bars=_vbars, bars_df=_vdf,
                                    )
                                    if _bt_res:
                                        _status = _bt_res.get("verdict", {}).get("status", "COMPLETE")
                                        logging.getLogger("main").debug(
                                            "🔬 [AUTONOMOUS RESEARCH] %s backtest: status=%s",
                                            _sname, _status,
                                        )
                                except Exception as _bt_one:
                                    logging.getLogger("main").debug(
                                        "Research backtest sprint skipped %s: %s", _sname, _bt_one)
                            logging.getLogger("main").info(
                                "🔬 [AUTONOMOUS RESEARCH] Continuous backtest sweep complete: %d strategies",
                                len(_strat_names),
                            )
                        except Exception as _bt_err:
                            logging.getLogger("main").debug("Research backtest sprint skipped: %s", _bt_err)

                        # 2. Alpha Discovery on the same multi-thousand-bar history
                        # as the backtest sweep. The sealed evaluator's IC t-test
                        # needs >=4 independent 20-bar blocks after Bonferroni
                        # deflation; a 200-bar fetch cannot support that claim.
                        try:
                            _hist_df = _vdf
                            if _hist_df is None or len(_hist_df) < 200:
                                _hist_df = fetch_ohlc(_res_sym, _res_tf, count=max(_vbars, 500))
                            if _hist_df is not None and len(_hist_df) >= 200:
                                from core.alpha_discovery import AlphaDiscoveryEngine
                                _discovery = AlphaDiscoveryEngine(state_file="alpha_discovery_state.json")
                                _split = int(len(_hist_df) * 0.7)
                                _val_alphas = _discovery.run_discovery_cycle(_hist_df.iloc[:_split], _hist_df.iloc[_split:])
                                if _val_alphas:
                                    logging.getLogger("main").info(
                                        "🔬 [AUTONOMOUS RESEARCH] Alpha Discovery found %d validated alphas",
                                        len(_val_alphas),
                                    )
                        except Exception as _alpha_err:
                            logging.getLogger("main").debug("Research alpha discovery skipped: %s", _alpha_err)

                        # 3. Macro & COT Ingest
                        try:
                            if _orch and hasattr(_orch, "pre_pulse_macro_ingest"):
                                _orch.pre_pulse_macro_ingest(_res_sym, paper_mode=_paper_mode)
                        except Exception as _macro_err:
                            logging.getLogger("main").debug("Research macro ingest skipped: %s", _macro_err)

                        logging.getLogger("main").info(
                            "🔬 [AUTONOMOUS RESEARCH] Sprint finished in %.2fs", time.time() - _r_t0
                        )
                    except Exception as _wsprint_exc:
                        logging.getLogger("main").warning("Autonomous research sprint error: %s", _wsprint_exc)

                # Auto-Select Symbol
                current_symbol = args.symbol
                skip_analysis = False
                if holiday_active:
                    # Keep continuous process alive without inventing a symbol.
                    skip_analysis = True
                    maint_symbol = ""
                    if watchlist:
                        maint_symbol = str(watchlist[0]).strip().upper()
                    elif str(args.symbol).upper() not in {"", "AUTO"}:
                        maint_symbol = str(args.symbol).strip().upper()
                    orch.pulse_maintenance(
                        maint_symbol,
                        paper_mode=args.paper,
                        timeframe=args.timeframe if args.timeframe != "AUTO" else "H1",
                    )
                    logging.getLogger("main").info(
                        "⏸️ [DECISION] HOLD %s | Market Holiday/Weekend; trading halted, maintenance & research routines active",
                        maint_symbol or "PORTFOLIO"
                    )
                    # --- Weekend / Holiday Autonomous Research Routine ---
                    if cycle_count == 1 or cycle_count % 10 == 0:
                        _res_sym = maint_symbol or (args.symbol if args.symbol and str(args.symbol).upper() != "AUTO" else "EURUSD")
                        _res_tf = args.timeframe if args.timeframe != "AUTO" else "H1"
                        _execute_research_sprint(_res_sym, _res_tf, orch, args.paper)
                elif auto_symbol_mode and scanner:
                    if display: display.update_footer("Scanning markets...")
                    else: print("Scanning markets...")
                    
                    opportunities = scanner.scan(timeframe=args.timeframe if args.timeframe != "AUTO" else "H1", 
                                                 limit_to_watchlist=watchlist)
                    # Cache scanner results for API server (scanner_state.json)
                    try:
                        import tempfile as _tmpfile
                        _scan_state = {
                            "ts": int(time.time() * 1000),
                            "results": [
                                {
                                    "id": opp.get("symbol", ""),
                                    "symbol": opp.get("symbol", ""),
                                    "score": opp.get("score", 0),
                                    "reasons": opp.get("reasons", []),
                                    "spread": opp.get("spread", 0),
                                    "volatility": opp.get("volatility", 0),
                                }
                                for opp in (opportunities if isinstance(opportunities, list) else [])
                            ],
                        }
                        _scan_path = os.path.join(BASE_DIR, "scanner_state.json")
                        _fd, _tmp = _tmpfile.mkstemp(dir=BASE_DIR, suffix=".tmp")
                        with os.fdopen(_fd, "w", encoding="utf-8") as _f:
                            json.dump(_scan_state, _f, separators=(",", ":"))
                        os.replace(_tmp, _scan_path)
                    except Exception as _scan_exc:
                        logging.getLogger("main").debug("Scanner state cache failed: %s", _scan_exc)

                    if opportunities:
                        best = opportunities[0]
                        
                        _last_sym = getattr(scanner, "_last_scanner_symbol", None)
                        if _last_sym and best["symbol"] == _last_sym:
                            has_pos = False
                            if hasattr(orch, "trade_manager") and hasattr(orch.trade_manager, "_get_positions"):
                                try:
                                    has_pos = bool(orch.trade_manager._get_positions(symbol=_last_sym))
                                except Exception:
                                    pass
                            
                            if not has_pos:
                                for opp in opportunities[1:3]:
                                    if opp["symbol"] != _last_sym:
                                        best = opp
                                        break
                                        
                        scanner._last_scanner_symbol = best["symbol"]
                        
                        current_symbol = best["symbol"]
                        score = best["score"]
                        msg = f" Selected {current_symbol} (Score: {score})"
                        if display: display.update_execution_log([msg])
                        else: print(msg)
                    else:
                        skip_analysis = True
                        msg = (
                            "No scanner opportunities; maintenance pulse only "
                            "(refusing silent EURUSD default)."
                        )
                        if display: display.update_execution_log([f"⚠ {msg}"])
                        else: print(f"⚠ {msg}")
                        maint_symbol = ""
                        if watchlist:
                            maint_symbol = str(watchlist[0]).strip().upper()
                        orch.pulse_maintenance(
                            maint_symbol,
                            paper_mode=args.paper,
                            timeframe=args.timeframe if args.timeframe != "AUTO" else "H1",
                        )
                        logging.getLogger("main").info(
                            "⏸️ [DECISION] HOLD %s | No scanner opportunities (score below threshold); maintenance pulse active",
                            maint_symbol or "PORTFOLIO"
                        )
                        if cycle_count == 1 or cycle_count % 10 == 0:
                            _res_sym = maint_symbol or (args.symbol if args.symbol and str(args.symbol).upper() != "AUTO" else "EURUSD")
                            _res_tf = args.timeframe if args.timeframe != "AUTO" else "H1"
                            _execute_research_sprint(_res_sym, _res_tf, orch, args.paper)
                elif str(current_symbol).upper() == "AUTO":
                    # Defensive: never analyse the literal token AUTO.
                    skip_analysis = True
                    orch.pulse_maintenance(
                        "",
                        paper_mode=args.paper,
                        timeframe=args.timeframe if args.timeframe != "AUTO" else "H1",
                    )
                    logging.getLogger("main").info(
                        "⏸️ [DECISION] HOLD | Defensive AUTO symbol pulse",
                    )

                if not skip_analysis:
                    # Scanner/AUTO selections must receive the same broker
                    # contract/specification capture as explicit symbols before
                    # any proposal is allowed. Cache successful captures for the
                    # session so a five-second pulse does not repeatedly mutate
                    # the broker-preflight evidence.
                    if client and current_symbol and current_symbol not in preflighted_symbols:
                        resolved = preflight_symbols([current_symbol])
                        current_symbol = resolved.get(current_symbol, current_symbol)
                        preflighted_symbols.add(current_symbol)

                    # A dropped terminal connection must degrade into a retry on
                    # the next pulse, never terminate the continuous process.
                    if client is not None:
                        try:
                            client.reconnect_if_needed()
                        except Exception as conn_exc:
                            logging.getLogger("main").warning("MT5 reconnection check failed: %s", conn_exc)

                    strategy_cycles = resolve_strategy_cycles(args.timeframe, args.strategies)
                    # Pipeline V2: enabled by default (stage-based telemetry + fallback).
                    # Set PIPELINE_V2=0 or --no-pipeline-v2 to disable.
                    _v2_enabled = os.environ.get("PIPELINE_V2", "1") != "0" and not getattr(args, 'no_pipeline_v2', False)
                    if _v2_enabled:
                        try:
                            from core.harness.pipeline_v2 import install_orchestrator_entrypoint
                            install_orchestrator_entrypoint()
                        except Exception as _v2_exc:
                            logging.getLogger("main").warning(
                                "PIPELINE_V2 entrypoint unavailable (%s); legacy path", _v2_exc)
                            _v2_enabled = False
                    # --- Graph State Checkpoint: snapshot S at cycle start (UAQP persistence) ---
                    _cp_id = None
                    if _crash_recovery is not None:
                        try:
                            _cp_id = _crash_recovery.begin_cycle_checkpoint(cycle_count)
                        except Exception as _cp_exc:
                            logging.getLogger("main").debug("Checkpoint begin skipped: %s", _cp_exc)
                    # ------------------------------------------------------------------------
                    # --- Deliberative Graph shadow evaluation (opt-in, paper-only) ---
                    # DELIBERATIVE_GRAPH_SHADOW=1 + a paper profile with the
                    # DeliberativeGraphPlugin mounted: run the graph engine alongside
                    # the legacy cycle as an observation lane. It uses the profile's
                    # own paper broker/router and never touches the live path.
                    _delib_app = None
                    if (os.environ.get("DELIBERATIVE_GRAPH_SHADOW", "1") != "0"
                            and harness_ctx is not None
                            and harness_ctx.has("deliberative_graph")):
                        _delib_app = harness_ctx.get("deliberative_graph")
                        logging.getLogger("main").info(
                            "Deliberative Graph shadow mode enabled.")
                    if _delib_app is None and getattr(args, "execution_engine", "orchestrator") == "graph":
                        try:
                            from intelligence.deliberative_graph import build_deliberative_graph
                            _delib_app = build_deliberative_graph(
                                db_path=getattr(args, "journal_path", None) or "trading_state.db",
                                router=getattr(orch, "router", None),
                                broker=getattr(orch, "client", None) or getattr(orch, "paper_broker", None),
                            )
                            logging.getLogger("main").info("Primary Deliberative Graph execution engine initialized.")
                        except Exception as _dg_exc:
                            logging.getLogger("main").warning("Deliberative Graph init failed: %s", _dg_exc)

                    # Run strategy cycles — parallel when multiple strategies active
                    def _run_one_strategy(_sname, _stf):
                        _ct0 = time.time()
                        try:
                            if getattr(args, "execution_engine", "orchestrator") == "graph" and _delib_app is not None:
                                from intelligence.graph_state import create_initial_trading_cycle_state as _mk_graph_state
                                from core.shared_state import ui_state as _ui_state
                                _ui_state.reset_graph_pipeline(symbol=current_symbol, timeframe=_stf)
                                _g_init = _mk_graph_state(
                                    symbol=current_symbol,
                                    timeframe=_stf,
                                    strategy=_sname,
                                    paper_mode=args.paper,
                                )
                                _g_tid = f"exec_{current_symbol}_{_stf}_{cycle_count}"
                                _g_final = _delib_app.invoke(_g_init, config={"configurable": {"thread_id": _g_tid}})
                                _g_status = _g_final.get("status", "UNKNOWN")
                                _g_action = _g_final.get("order_intent", {}).get("action", "HOLD") if _g_status == "EXECUTED" else "HOLD"
                                _msg = f"⚡ [GRAPH DECISION] {_g_action} {current_symbol} {_stf} ({_sname}) -> {_g_status}"
                                logging.getLogger("main").info(_msg)
                                if display and hasattr(display, "update_execution_log"):
                                    display.update_execution_log([_msg])
                                try:
                                    from operations.event_bridge import emit as _deck_emit
                                    _deck_emit("CYCLE_COMPLETE", {
                                        "symbol": current_symbol, "strategy": _sname,
                                        "timeframe": _stf,
                                        "duration_ms": int((time.time() - _ct0) * 1000),
                                    }, correlation_id=f"{current_symbol}-{_stf}")
                                except Exception:
                                    pass
                                return

                            _entry = orch.execute_cycle
                            if _v2_enabled and hasattr(orch, "execute_cycle_v2"):
                                _entry = orch.execute_cycle_v2
                            if TradeLock is not None:
                                with TradeLock("mt5_cycle_execution", timeout=15.0):
                                    _res = _entry(
                                        symbol=current_symbol,
                                        timeframe=_stf,
                                        cycle_num=cycle_count,
                                        total_cycles=args.loops if not args.continuous else 9999,
                                        paper_mode=args.paper,
                                        display_instance=display,
                                        strategy=_sname,
                                    )
                            else:
                                _res = _entry(
                                    symbol=current_symbol,
                                    timeframe=_stf,
                                    cycle_num=cycle_count,
                                    total_cycles=args.loops if not args.continuous else 9999,
                                    paper_mode=args.paper,
                                    display_instance=display,
                                    strategy=_sname,
                                )
                            if model_staleness_tracker is not None:
                                model_staleness_tracker.record_usage(_sname)
                            _dec = _res[0] if isinstance(_res, (tuple, list)) and _res else _res
                            if isinstance(_dec, dict):
                                _action = _dec.get("action", "HOLD")
                                _reason = _dec.get("reasoning", "Cycle complete")
                                _conf = _dec.get("confidence")
                                _conf_str = f" (conf: {_conf:.2f})" if _conf is not None else ""
                                if _action != "HOLD":
                                    _entry_p = _dec.get("entry", 0.0)
                                    _sl = _dec.get("stop_loss", 0.0)
                                    _tp = _dec.get("take_profit", 0.0)
                                    _msg = f"⚡ [DECISION] {_action} {current_symbol} {_stf} ({_sname}) @ {_entry_p} (SL: {_sl}, TP: {_tp}){_conf_str} | {_reason}"
                                else:
                                    _msg = f"⏸️ [DECISION] HOLD {current_symbol} {_stf} ({_sname}){_conf_str} | {_reason}"
                                logging.getLogger("main").info(_msg)
                                if display and hasattr(display, "update_execution_log"):
                                    display.update_execution_log([_msg])
                            try:
                                from operations.event_bridge import emit as _deck_emit
                                _deck_emit("CYCLE_COMPLETE", {
                                    "symbol": current_symbol, "strategy": _sname,
                                    "timeframe": _stf,
                                    "duration_ms": int((time.time() - _ct0) * 1000),
                                }, correlation_id=f"{current_symbol}-{_stf}")
                            except Exception as _em_exc:
                                logging.getLogger("mt5_trader").debug("event emission failed: %s", _em_exc)
                        except KeyboardInterrupt:
                            raise
                        except Exception as _cyc_exc:
                            logging.getLogger("main").exception(
                                "Cycle failed (%s %s %s); continuing on next pulse",
                                _sname, current_symbol, _stf,
                            )
                            if display:
                                display.update_execution_log(
                                    [f"\u26a0\ufe0f Cycle error ({_sname}): {str(_cyc_exc)[:200]}"]
                                )

                    # Hoist calendar / news / macro ingestion once per pulse before strategy execution
                    if hasattr(orch, "pre_pulse_macro_ingest"):
                        try:
                            orch.pre_pulse_macro_ingest(current_symbol, paper_mode=args.paper)
                        except Exception as _pulse_ingest_exc:
                            logging.getLogger("main").debug("Pre-pulse macro ingest skipped: %s", _pulse_ingest_exc)

                    # NOTE (H5): no speculative VIX clear here. An earlier revision
                    # read a VIX out of the pulse macro cache, but no producer
                    # writes a timestamped VIX there and orch.macro_provider does
                    # not exist, so the block could never run — while looking like
                    # a live clearing path. Market-data clearing happens where a
                    # real VIX exists (the shadow deliberative graph, opt-in);
                    # the always-available clear is the operator path
                    # (POST /api/latch/clear, `mt5_trader.py latch --clear`).

                    if len(strategy_cycles) > 1:
                        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _AC
                        with _TPE(max_workers=len(strategy_cycles), thread_name_prefix="strat") as _strat_pool:
                            _strat_futs = {
                                _strat_pool.submit(_run_one_strategy, sn, stf): sn
                                for sn, stf in strategy_cycles
                            }
                            for _fut in _AC(_strat_futs):
                                _fut.result()  # propagate exceptions logged inside _run_one_strategy
                    else:
                        for _sn, _stf in strategy_cycles:
                            _run_one_strategy(_sn, _stf)

                    # --- Deliberative Graph shadow cycle (observations only) ---
                    if _delib_app is not None and strategy_cycles:
                        try:
                            from intelligence.graph_state import (
                                create_initial_trading_cycle_state as _mk_graph_state,
                            )
                            from core.shared_state import ui_state as _ui_state
                            _shadow_name, _shadow_tf = strategy_cycles[0]
                            _ui_state.reset_graph_pipeline(symbol=current_symbol, timeframe=_shadow_tf)

                            _shadow_final = {}
                            _stream_iter = _delib_app.stream(
                                _mk_graph_state(
                                    symbol=current_symbol,
                                    timeframe=_shadow_tf,
                                    strategy=_shadow_name,
                                    paper_mode=True,
                                ),
                                config={"configurable": {
                                    "thread_id": f"shadow_{current_symbol}_{_shadow_tf}_{cycle_count}",
                                }},
                            )
                            for _event in _stream_iter:
                                for _node_name, _state_update in _event.items():
                                    _shadow_final.update(_state_update)
                                    _ui_state.update_graph_step(_node_name, _state_update)
                                    try:
                                        from ui.gui_api import emit_deliberation_step as _emit_dstep
                                        _out_str = _state_update if isinstance(_state_update, str) else json.dumps(
                                            _state_update, default=str
                                        )[:400]
                                        _emit_dstep(
                                            node_id=_node_name,
                                            status="done" if _node_name in ("order_execution", "rejection_sink") else "running",
                                            output=_out_str,
                                            node_label=_node_name.replace("_", " ").title(),
                                        )
                                    except Exception:
                                        pass

                            _shadow_summary = {
                                "status": _shadow_final.get("status"),
                                "validation_passed": _shadow_final.get("validation_passed"),
                                "fusion_score": _shadow_final.get("fusion_score"),
                                "rejection_reasons": _shadow_final.get("rejection_reasons"),
                            }
                            logging.getLogger("main").info(
                                "Deliberative shadow %s/%s -> %s",
                                current_symbol, _shadow_tf, _shadow_summary,
                            )
                            try:
                                from operations.event_bridge import emit as _shadow_emit
                                _shadow_emit("DELIBERATIVE_SHADOW", {
                                    "symbol": current_symbol,
                                    "strategy": _shadow_name,
                                    "timeframe": _shadow_tf,
                                    **_shadow_summary,
                                }, correlation_id=f"{current_symbol}-{_shadow_tf}")
                            except Exception:
                                pass
                        except Exception as _sh_exc:
                            logging.getLogger("main").debug(
                                "Deliberative shadow cycle skipped: %s", _sh_exc)

                    # --- Graph State Checkpoint: complete snapshot after all strategies ---
                    if _crash_recovery is not None and _cp_id is not None:
                        try:
                            _crash_recovery.complete_cycle_checkpoint(_cp_id)
                        except Exception as _cp_exc:
                            logging.getLogger("main").debug("Checkpoint complete skipped: %s", _cp_exc)
                    # ------------------------------------------------------------------------

                # --- WebSocket Keepalive Heartbeat (prevents 1011 disconnects) ---
                # Emit a lightweight heartbeat every cycle to keep the API server's
                # WebSocket connections alive. The TUI's live transport expects
                # regular events; silence triggers keepalive timeout.
                try:
                    from operations.event_bridge import emit as _hb_emit
                    _hb_emit("ENGINE_HEARTBEAT", {
                        "cycle": cycle_count,
                        "symbol": current_symbol,
                        "ts": int(time.time() * 1000),
                    }, correlation_id=f"hb-{cycle_count}")
                except Exception:
                    pass

                # --- Memory Orchestrator: consolidate and decay (every 50 cycles) ---
                if _memory_orch is not None and cycle_count % 50 == 0 and cycle_count > 0:
                    try:
                        _mem_consolidated = _memory_orch.consolidate()
                        _mem_decayed = _memory_orch.decay()
                        _total_mem_ops = sum(_mem_consolidated.values()) + sum(_mem_decayed.values())
                        if _total_mem_ops > 0:
                            logging.getLogger("main").info(
                                "Memory maintenance: %d consolidated, %d decayed",
                                sum(_mem_consolidated.values()), sum(_mem_decayed.values()))
                    except Exception as _mem_maint_exc:
                        logging.getLogger("main").debug("Memory maintenance skipped: %s", _mem_maint_exc)

                # --- Knowledge Retrieval: update working context (every 100 cycles) ---
                if _knowledge_retrieval is not None and cycle_count % 100 == 0 and cycle_count > 0:
                    try:
                        _kr_result = _knowledge_retrieval.query(
                            f"current market conditions for {current_symbol}",
                            limit=3,
                        )
                        if _kr_result.get("results"):
                            logging.getLogger("main").debug(
                                "Knowledge retrieval: %d results for %s",
                                len(_kr_result["results"]), current_symbol)
                    except Exception as _kr_exc:
                        logging.getLogger("main").debug("Knowledge retrieval skipped: %s", _kr_exc)

                # --- Meta-Improvement: health check (every 200 cycles) ---
                if _meta_improvement is not None and cycle_count % 200 == 0 and cycle_count > 0:
                    try:
                        _health = _meta_improvement.health_check()
                        if _health.get("overall_status") != "HEALTHY":
                            logging.getLogger("main").warning(
                                "Meta-improvement health: %s", _health.get("overall_status"))
                    except Exception as _mi_exc:
                        logging.getLogger("main").debug("Meta-improvement health check skipped: %s", _mi_exc)

                # --- Kronos Signal Fusion: get regime-adjusted signal (every 30 cycles) ---
                # Never fuse on the literal AUTO token; there is no market context for it.
                if (
                    _kronos_fusion is not None
                    and cycle_count % 30 == 0
                    and cycle_count > 0
                    and str(current_symbol or "").upper() not in ("", "AUTO")
                ):
                    try:
                        _kronos_signal = _kronos_fusion.get_regime_adjusted_signal(current_symbol)
                        if _kronos_signal.get("action") != "HOLD":
                            logging.getLogger("main").info(
                                "Kronos signal [%s]: %s (conf=%.2f, regime=%s)",
                                current_symbol, _kronos_signal["action"],
                                _kronos_signal.get("confidence", 0),
                                _kronos_signal.get("regime", "UNKNOWN"))
                    except Exception as _ksf_exc:
                        logging.getLogger("main").debug("Kronos signal fusion skipped: %s", _ksf_exc)

                # --- Advanced RL: train agents (every 300 cycles) ---
                if _advanced_rl is not None and cycle_count % 300 == 0 and cycle_count > 0:
                    try:
                        _rl_result = _advanced_rl.train_agent("balanced", episodes=10)
                        if _rl_result.get("final_score", 0) > 0:
                            logging.getLogger("main").info(
                                "Advanced RL training: score=%.4f, updates=%d",
                                _rl_result.get("final_score", 0),
                                _rl_result.get("total_updates", 0))
                    except Exception as _arl_exc:
                        logging.getLogger("main").debug("Advanced RL training skipped: %s", _arl_exc)

                # --- RL Metrics → Promotion Pipeline Bridge (every 100 cycles) ---
                # Feeds RL model metrics (epsilon, q_table_size, updates, win_rate)
                # into strategy state so auto_promotion can evaluate RL contribution.
                if cycle_count % 100 == 0 and cycle_count > 0:
                    try:
                        if hasattr(orch, 'rl_trainer') and orch.rl_trainer is not None:
                            _rl_metrics = {
                                "rl_epsilon": float(orch.rl_trainer.epsilon),
                                "rl_q_table_size": len(orch.rl_trainer.q_table),
                                "rl_total_updates": int(orch.rl_trainer.total_updates),
                                "rl_exploration_rate": float(orch.rl_trainer.epsilon),
                                "rl_last_update_cycle": cycle_count,
                            }
                            # Write to strategy state for auto_promotion to read
                            _state_path = os.path.join(os.getcwd(), "strategy_state.json")
                            _existing = {}
                            if os.path.exists(_state_path):
                                try:
                                    with open(_state_path, "r") as f:
                                        _existing = json.load(f)
                                except Exception:
                                    pass
                            _existing.update(_rl_metrics)
                            import tempfile as _tmpfile
                            _fd, _tmp = _tmpfile.mkstemp(dir=os.path.dirname(_state_path), suffix=".tmp")
                            with os.fdopen(_fd, "w", encoding="utf-8") as _f:
                                json.dump(_existing, _f, indent=2, default=str)
                            os.replace(_tmp, _state_path)
                    except Exception as _rl_bridge_exc:
                        logging.getLogger("main").debug("RL metrics bridge skipped: %s", _rl_bridge_exc)

                # --- Improvement Pipeline: auto-apply low-risk + best signal (every 150 cycles) ---
                if _improvement_pipeline is not None and cycle_count % 150 == 0 and cycle_count > 0:
                    try:
                        _ip_result = _improvement_pipeline.run_cycle(cycle_count)
                        _applied = _ip_result.get("improvements_applied", [])
                        if _applied:
                            logging.getLogger("main").info(
                                "Improvement pipeline: %d improvements applied, best signal: %s",
                                len(_applied), _ip_result.get("best_signal_selected", "none"))
                    except Exception as _ip_exc:
                        logging.getLogger("main").debug("Improvement pipeline skipped: %s", _ip_exc)

                # --- G4: Circuit Breaker Evaluation (autonomous safety) ---
                # Evaluate after every cycle; trips halt/demote/pause autonomously.
                try:
                    from operations.autonomy import get_autonomy as _cb_autonomy
                    _risk_snapshot = {}
                    try:
                        _risk_snapshot = {
                            "drawdown_pct": float(risk.current_drawdown_pct) if hasattr(risk, 'current_drawdown_pct') else 0.0,
                            "slippage_breach_streak": int(orch.store.get_state("slippage_streak", 0) or 0) if hasattr(orch, 'store') else 0,
                            "llm_outage_minutes": 0.0,
                            "data_stale_seconds": 0.0,
                            "recon_mismatch_24h": int(orch.store.get_state("recon_mismatch_24h", 0) or 0) if hasattr(orch, 'store') else 0,
                        }
                    except Exception:
                        pass
                    _trips = _cb_autonomy().evaluate_circuits(_risk_snapshot)
                    if _trips:
                        for _trip in _trips:
                            logging.getLogger("main").warning(
                                "CIRCUIT TRIP: %s (severity=%s, action=%s, observed=%s)",
                                _trip.get("key"), _trip.get("severity"),
                                _trip.get("action"), _trip.get("observed"))
                        if display:
                            display.update_execution_log(
                                [f"⚠ Circuit: {t.get('key')} ({t.get('severity')})" for t in _trips[:3]])
                except Exception as _cb_exc:
                    logging.getLogger("main").warning("Circuit breaker evaluation error: %s", _cb_exc)

                # --- G8: Autonomous Recovery — risk reduction on consecutive failures ---
                _consecutive_failures = 0
                try:
                    _consecutive_failures = int(orch.store.get_state("consecutive_cycle_failures", 0) or 0) if hasattr(orch, 'store') else 0
                except Exception:
                    pass
                # If >3 consecutive failures, auto-demote autonomy and reduce risk
                if _consecutive_failures >= 3:
                    try:
                        from operations.autonomy import get_autonomy as _rec_autonomy
                        _rec_autonomy().demote(f"consecutive_cycle_failures={_consecutive_failures}")
                        logging.getLogger("main").warning(
                            "AUTO-RECOVERY: demoted autonomy after %d consecutive failures",
                            _consecutive_failures)
                    except Exception:
                        pass

                # --- κ Audit: log cycle completion ---
                if _audit is not None:
                    try:
                        from core.compliance_audit import EventType
                        _audit.log(
                            EventType.STATE_TRANSITION,
                            symbol=current_symbol,
                            cycle_num=cycle_count,
                            agent="ω",
                            payload={
                                "transition": "cycle_complete",
                                "strategy": strategy_name if 'strategy_name' in dir() else "",
                                "duration_ms": int((time.time() - _cycle_t0) * 1000) if '_cycle_t0' in dir() else 0,
                            },
                        )
                    except Exception as _ka_exc:
                        logging.getLogger("main").debug("κ audit log skipped: %s", _ka_exc)

                # --- Auto-generate alpha health dashboard after each cycle ---
                # Maintenance pulses share the bookkeeping band now; writing two
                # HTML decks every 5s is pure churn, so throttle them.
                if not getattr(args, 'no_dashboard', False) and (not skip_analysis or cycle_count % 10 == 0):
                    try:
                        from ui.alpha_dashboard import generate_alpha_dashboard
                        _dash_path = generate_alpha_dashboard(
                            state_file="alpha_decay_state.json",
                            output_path="alpha_dashboard.html",
                        )
                        if _dash_path:
                            logging.getLogger("main").debug(
                                "Alpha dashboard generated: %s", _dash_path
                            )
                    except Exception as _dash_exc:
                        logging.getLogger("main").debug(
                            "Alpha dashboard auto-gen skipped: %s", _dash_exc
                        )

                # --- Auto-generate compliance dashboard (κ audit) ---
                if not getattr(args, 'no_dashboard', False) and _audit is not None and (not skip_analysis or cycle_count % 10 == 0):
                    try:
                        from ui.compliance_dashboard import generate_compliance_dashboard
                        _comp_path = generate_compliance_dashboard(
                            hours=24,
                            output_path="compliance_dashboard.html",
                        )
                        if _comp_path:
                            logging.getLogger("main").debug(
                                "Compliance dashboard generated: %s", _comp_path
                            )
                    except Exception as _comp_exc:
                        logging.getLogger("main").debug(
                            "Compliance dashboard auto-gen skipped: %s", _comp_exc
                        )

                # --- Automated RL Model Training Pipeline ---
                # Train every 50 cycles (≈25 minutes at 30s intervals)
                _rl_train_interval = int(os.environ.get("RL_TRAIN_INTERVAL", "50"))
                if cycle_count % _rl_train_interval == 0 and cycle_count > 0:
                    try:
                        from research.rl_trainer import RLTrainer
                        from research.model_registry import register as _register_model

                        _rl = RLTrainer(
                            model_path=os.path.join(BASE_DIR, "rl_model.json"),
                        )

                        # Train on simulated episodes from recent trading history
                        try:
                            import sqlite3 as _sqlite3
                            _db_path = os.path.join(BASE_DIR, "trading_state.db")
                            if os.path.exists(_db_path):
                                _con = _sqlite3.connect(_db_path, timeout=2)
                                _con.row_factory = _sqlite3.Row
                                # Broker-reconciled rows only: a self-declared
                                # journal row is not training evidence.
                                _broker_tickets = sorted(broker_reconciled_tickets(_db_path))
                                if _broker_tickets:
                                    _marks = ",".join("?" for _ in _broker_tickets)
                                    _trades = _con.execute(
                                        "SELECT symbol, side, entry, stop_loss, take_profit, "
                                        "gross_pnl, risk_amount, volume, opened_at, closed_at, payload "
                                        "FROM trades WHERE closed_at IS NOT NULL "
                                        "AND ticket IN (%s) "
                                        "ORDER BY closed_at DESC LIMIT 50" % _marks,
                                        _broker_tickets,
                                    ).fetchall()
                                else:
                                    _trades = []
                                _con.close()

                                if _trades:
                                    # Q-learning updates from trade results. The entry
                                    # state is rebuilt from the instrumented
                                    # proposal_features snapshot recorded at entry time;
                                    # trades without a parseable snapshot are SKIPPED —
                                    # fabricated mid-range features would poison the
                                    # Q-table with states the market never produced.
                                    _train_count = 0
                                    _skipped_no_features = 0
                                    for _trade in _trades:
                                        _fv = {}
                                        try:
                                            _pf = (json.loads(_trade["payload"] or "{}") or {}).get("proposal_features") or {}
                                            _fv = _pf.get("feature_values") or {}
                                        except (json.JSONDecodeError, TypeError, ValueError):
                                            _fv = {}
                                        if not _fv:
                                            _skipped_no_features += 1
                                            continue
                                        # Real features only — absent keys fall through
                                        # to RLTrainer's documented .get() defaults.
                                        _state = {"direction": 1 if _trade["side"] == "BUY" else -1}
                                        if _fv.get("rsi_14") is not None:
                                            _state["rsi_14"] = float(_fv["rsi_14"])
                                        if _fv.get("realized_volatility_20") is not None:
                                            _rvol = float(_fv["realized_volatility_20"])
                                            _state["volatility_regime"] = (
                                                "low" if _rvol < 0.01 else
                                                "medium" if _rvol < 0.02 else "high"
                                            )
                                        if _fv.get("adx_14") is not None:
                                            _state["trend_strength"] = min(float(_fv["adx_14"]) / 50.0, 1.0)
                                        _reward = 0.0
                                        if _trade["gross_pnl"] and _trade["risk_amount"]:
                                            _reward = _trade["gross_pnl"] / max(_trade["risk_amount"], 0.01)
                                        _action = "BUY" if _trade["side"] == "BUY" else "SELL"
                                        _rl.update(_state, _action, min(max(_reward, -2.0), 2.0), _state, done=True)
                                        _train_count += 1

                                    # Persist only if something actually trained —
                                    # an all-skipped batch must not overwrite the
                                    # model with an empty registry entry.
                                    if _train_count > 0:
                                        # Decay exploration rate every 100 cycles
                                        if cycle_count % 100 == 0 and _rl.epsilon > 0.05:
                                            _rl.epsilon *= 0.95

                                        # Save model
                                        _rl.save_model()

                                        # Register in model registry
                                        try:
                                            _hash = hashlib.md5(
                                                json.dumps(_rl.get_stats(), sort_keys=True).encode()
                                            ).hexdigest()[:16]
                                            _register_model(
                                                model_key="rl_qlearning_v1",
                                                version=f"auto_{cycle_count}",
                                                kind="Q-learning",
                                                feature_schema_version="v1",
                                                training_data_hash=_hash,
                                                metrics=_rl.get_stats(),
                                                artifact_uri=os.path.join(BASE_DIR, "rl_model.json"),
                                            )
                                        except Exception as _reg_exc:
                                            logging.getLogger("main").debug("Model registry write skipped: %s", _reg_exc)

                                    logging.getLogger("main").info(
                                        "RL training complete: %d updates (%d skipped, no entry snapshot), "
                                        "%d states, eps=%.3f",
                                        _train_count, _skipped_no_features,
                                        len(_rl.q_table), _rl.epsilon,
                                    )
                        except Exception as _train_exc:
                            logging.getLogger("main").warning("RL training data fetch failed: %s", _train_exc)
                    except Exception as _rl_exc:
                        logging.getLogger("main").debug("RL training pipeline skipped: %s", _rl_exc)

                # --- Automated Training Manager: periodic RL + Kronos synthetic retrain (every 500 cycles) ---
                if cycle_count % 500 == 0 and cycle_count > 0:
                    try:
                        from core.automated_training_manager import AutomatedTrainingManager
                        _atm = AutomatedTrainingManager()
                        _atm_sym = current_symbol if (current_symbol and str(current_symbol).upper() != "AUTO") else (watchlist[0] if watchlist else "EURUSD")
                        _atm_res = _atm.run_training_cycle(symbol=_atm_sym, quick=True)
                        logging.getLogger("main").info(
                            "Periodic Automated Training [%s]: OOS_Sharpe=%.3f WFE=%.3f gate=%s (%.1fs)",
                            _atm_sym, _atm_res.oos_sharpe, _atm_res.wfe,
                            _atm_res.gate_passed, _atm_res.duration_seconds,
                        )
                    except Exception as _atm_exc:
                        logging.getLogger("main").debug("Periodic automated training skipped: %s", _atm_exc)

                # --- Automated PPO Training Pipeline ---
                # Train every 200 cycles (≈100 minutes at 30s intervals)
                _ppo_train_interval = int(os.environ.get("PPO_TRAIN_INTERVAL", "200"))
                if cycle_count % _ppo_train_interval == 0 and cycle_count > 0:
                    try:
                        from research.ppo_trainer import train_ppo_offline
                        from research.model_registry import register as _register_model

                        # Run PPO training on Kronos simulator
                        _ppo_result = train_ppo_offline(
                            episodes=4,
                            updates=2,
                            max_steps=20,
                            symbol="EURUSD",
                            regime="volatile",
                            seed=7,
                            simulator="kronos",
                            artifact_path=os.path.join(BASE_DIR, "artifacts", "ppo_position_mgmt.json"),
                            epochs=2,
                            batch_size=32,
                        )

                        if _ppo_result and _ppo_result.get("promotion_safe") is False:
                            # Register in model registry (as CANDIDATE, not approved)
                            try:
                                _hash = hashlib.md5(
                                    json.dumps(_ppo_result.get("config", {}), sort_keys=True).encode()
                                ).hexdigest()[:16]
                                _register_model(
                                    model_key="ppo_position_mgmt_v1",
                                    version=f"auto_{cycle_count}",
                                    kind="PPO+GAE",
                                    feature_schema_version="v1",
                                    training_data_hash=_hash,
                                    metrics=_ppo_result.get("metrics", {}),
                                    artifact_uri=os.path.join(BASE_DIR, "artifacts", "ppo_position_mgmt.json"),
                                )
                            except Exception as _reg_exc:
                                logging.getLogger("main").debug("PPO model registry write skipped: %s", _reg_exc)

                            logging.getLogger("main").info(
                                "PPO training complete: backend=%s, mean_reward=%.3f",
                                _ppo_result.get("backend", "unknown"),
                                _ppo_result.get("metrics", {}).get("mean_reward", 0),
                            )
                    except Exception as _ppo_exc:
                        logging.getLogger("main").debug("PPO training pipeline skipped: %s", _ppo_exc)

                # --- Metrics Persistence: sync closed trades to metrics store ---
                try:
                    from operations.metrics_persistence import MetricsStore
                    _metrics_store = MetricsStore()
                    import sqlite3 as _m_sqlite3
                    _m_db = os.path.join(BASE_DIR, "trading_state.db")
                    if os.path.exists(_m_db):
                        _m_con = _m_sqlite3.connect(_m_db, timeout=2)
                        _m_con.row_factory = _m_sqlite3.Row
                        # Fetch recently closed trades not yet in metrics_trades
                        _m_rows = _m_con.execute(
                            "SELECT t.ticket, t.symbol, t.side, t.entry as entry_price, "
                            "t.exit_price, t.pnl as gross_pnl, t.risk_amount, "
                            "t.opened_at, t.closed_at "
                            "FROM trades t "
                            "WHERE t.closed_at IS NOT NULL "
                            "AND t.ticket NOT IN (SELECT ticket FROM metrics_trades WHERE ticket IS NOT NULL) "
                            "ORDER BY t.closed_at DESC LIMIT 20"
                        ).fetchall()
                        _m_con.close()
                        for _m_row in _m_rows:
                            _metrics_store.record_trade({
                                "ticket": _m_row["ticket"],
                                "symbol": _m_row["symbol"],
                                "side": _m_row["side"],
                                "entry_price": _m_row["entry_price"],
                                "exit_price": _m_row["exit_price"],
                                "gross_pnl": _m_row["gross_pnl"],
                                "risk_amount": _m_row["risk_amount"],
                                "opened_at": _m_row["opened_at"],
                                "closed_at": _m_row["closed_at"],
                            })
                        if _m_rows:
                            logging.getLogger("main").debug(
                                "Metrics sync: recorded %d new trades", len(_m_rows))
                except Exception as _m_sync_exc:
                    logging.getLogger("main").debug("Metrics trade sync skipped: %s", _m_sync_exc)

                # --- Metrics Persistence: record equity snapshot ---
                try:
                    # MetricsStore from module scope — a local import here would
                    # shadow it for the earlier metrics-store block (~4672).
                    _metrics_store = MetricsStore()
                    _eq = 0.0
                    _bal = 0.0
                    _dd = 0.0
                    _open_risk = 0.0
                    try:
                        _bal = float(risk.balance) if hasattr(risk, 'balance') else 0.0
                        _eq = float(risk.equity) if hasattr(risk, 'equity') else _bal
                        _dd = float(risk.current_drawdown_pct) if hasattr(risk, 'current_drawdown_pct') else 0.0
                        _open_risk = float(orch.store.get_state("open_risk_pct", 0) or 0) if hasattr(orch, 'store') else 0.0
                    except Exception:
                        pass
                    _metrics_store.record_equity(_bal, _eq, _dd, _open_risk)
                except Exception as _mp_exc:
                    logging.getLogger("main").debug("Metrics persistence skipped: %s", _mp_exc)

                # --- Alert Rules Engine: evaluate current metrics ---
                try:
                    from operations.alert_rules import AlertRulesEngine
                    _alert_engine = AlertRulesEngine()
                    # rolling_win_rate_samples < 20 → omit performance metrics:
                    # an empty sample must not fire performance alerts (the
                    # writer publishes the sample count next to each rate).
                    try:
                        _wr_n = int(orch.store.get_state("rolling_win_rate_samples", 0) or 0) if hasattr(orch, 'store') else 0
                    except Exception:
                        _wr_n = 0
                    _alert_metrics = {
                        "drawdown_pct": float(risk.current_drawdown_pct) if hasattr(risk, 'current_drawdown_pct') else 0,
                        "consecutive_losses": int(orch.store.get_state("consecutive_losses", 0) or 0) if hasattr(orch, 'store') else 0,
                        "rolling_win_rate_samples": _wr_n,
                    }
                    if _wr_n >= 20:
                        _alert_metrics["rolling_win_rate"] = float(orch.store.get_state("rolling_win_rate", 0) or 0) if hasattr(orch, 'store') else 0
                        _alert_metrics["rolling_expectancy_r"] = float(orch.store.get_state("rolling_expectancy_r", 0) or 0) if hasattr(orch, 'store') else 0
                    _fired = _alert_engine.evaluate(_alert_metrics)
                    if _fired:
                        for _alert in _fired:
                            logging.getLogger("main").warning(
                                "ALERT [%s]: %s (value=%s)",
                                _alert["severity"], _alert["description"], _alert["value"],
                            )
                except Exception as _alert_exc:
                    logging.getLogger("main").debug("Alert rules evaluation skipped: %s", _alert_exc)

                # --- Degradation Detector: check for auto-freeze ---
                try:
                    from operations.degradation_detector import DegradationDetector
                    _detector = DegradationDetector()
                    _det_metrics = {
                        "drawdown_pct": float(risk.current_drawdown_pct) if hasattr(risk, 'current_drawdown_pct') else 0,
                        "consecutive_losses": int(orch.store.get_state("consecutive_losses", 0) or 0) if hasattr(orch, 'store') else 0,
                        "drawdown_duration_hours": float(orch.store.get_state("drawdown_duration_hours", 0) or 0) if hasattr(orch, 'store') else 0,
                        "overall_divergence": float(orch.store.get_state("overall_divergence", 0) or 0) if hasattr(orch, 'store') else 0,
                        "equity": float(risk.equity) if hasattr(risk, 'equity') else 0,
                        "rolling_win_rate_samples": _wr_n,
                    }
                    if _wr_n >= 20:
                        _det_metrics["rolling_win_rate"] = float(orch.store.get_state("rolling_win_rate", 0) or 0) if hasattr(orch, 'store') else 0
                        _det_metrics["rolling_expectancy_r"] = float(orch.store.get_state("rolling_expectancy_r", 0) or 0) if hasattr(orch, 'store') else 0
                    _det_action = _detector.check(_det_metrics)
                    if _det_action:
                        logging.getLogger("main").critical(
                            "CIRCUIT BREAKER [%s]: %s",
                            _det_action["action"], _det_action["reason"],
                        )
                        if display:
                            display.update_execution_log(
                                [f"⚠ CIRCUIT BREAKER: {_det_action['action']} - {_det_action['reason'][:100]}"])
                        # Write action to file for external monitoring
                        try:
                            from operations.event_bridge import emit as _cb_emit
                            _cb_emit("CIRCUIT_BREAKER", {
                                "action": _det_action["action"],
                                "reason": _det_action["reason"],
                                "severity": _det_action["severity"],
                            }, correlation_id=f"cb-{cycle_count}")
                        except Exception:
                            pass
                except Exception as _det_exc:
                    logging.getLogger("main").warning("Degradation detector error: %s", _det_exc)

                # --- Auto Promotion: check strategy promotion status ---
                if cycle_count % 100 == 0 and cycle_count > 0:
                    try:
                        from research.auto_promotion import AutoPromoter
                        _promoter = AutoPromoter()
                        _promos = _promoter.check_all_strategies()
                        for _promo in _promos:
                            if _promo.get("promoted"):
                                logging.getLogger("main").info(
                                    "STRATEGY PROMOTED: %s → %s",
                                    _promo.get("old_status"), _promo.get("new_status"),
                                )
                                try:
                                    from operations.event_bridge import emit as _promo_emit
                                    _promo_emit("STRATEGY_PROMOTED", {
                                        "strategy": _promo["strategy_key"],
                                        "old_status": _promo["old_status"],
                                        "new_status": _promo["new_status"],
                                    }, correlation_id=f"promo-{cycle_count}")
                                except Exception:
                                    pass
                    except Exception as _promo_exc:
                        logging.getLogger("main").warning("Auto promotion check error: %s", _promo_exc)

                # --- Continuous Backtest Pipeline: periodic validation ---
                if cycle_count % 500 == 0 and cycle_count > 0:
                    try:
                        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
                        _pipeline = ContinuousBacktestPipeline()
                        # Strategy-specific CPCV needs a real trade sample. The
                        # 500-bar default yields ~5 trades and every CPCV stage
                        # reports insufficiency; 4000 bars yields enough trades
                        # for a genuine purged split. Env-tunable for slow boxes.
                        _val_bars = int(os.getenv("BACKTEST_VALIDATION_BARS", "4000") or 4000)
                        _val_sym = (
                            current_symbol
                            if current_symbol and str(current_symbol).upper() != "AUTO"
                            else "EURUSD"
                        )
                        _val_tf = args.timeframe if args.timeframe != "AUTO" else "H1"
                        _bt_result = _pipeline.run_validation_cycle(
                            symbol=_val_sym,
                            strategy_name=strategy_name if 'strategy_name' in dir() else "default",
                            timeframe=_val_tf,
                            bars=_val_bars,
                        )
                        if _bt_result:
                            logging.getLogger("main").info(
                                "Backtest validation complete: %s",
                                json.dumps({k: v for k, v in _bt_result.items() if k != "detailed_results"}, default=str)[:200],
                            )
                    except Exception as _bt_exc:
                        logging.getLogger("main").warning("Continuous backtest pipeline error: %s", _bt_exc)
                        try:
                            from operations.event_bridge import emit as _bt_emit
                            _bt_emit("PIPELINE_ERROR", {
                                "pipeline": "continuous_backtest",
                                "symbol": current_symbol,
                                "error": str(_bt_exc),
                            }, correlation_id=f"bt-err-{cycle_count}")
                        except Exception:
                            pass

                # --- Alpha Discovery: periodic factor search & sealed sandbox validation ---
                if cycle_count % 250 == 0 and cycle_count > 0:
                    try:
                        from core.alpha_discovery import AlphaDiscoveryEngine
                        # The IC t-test needs >=4 independent 20-bar blocks after
                        # Bonferroni deflation; the old 500-bar strategy frame
                        # could not support a significance claim.
                        _alpha_sym = (
                            current_symbol
                            if current_symbol and str(current_symbol).upper() != "AUTO"
                            else "EURUSD"
                        )
                        _alpha_tf = args.timeframe if args.timeframe != "AUTO" else "H1"
                        _alpha_bars = int(os.getenv("BACKTEST_VALIDATION_BARS", "4000") or 4000)
                        _alpha_df = fetch_ohlc(_alpha_sym, _alpha_tf, count=max(_alpha_bars, 500))
                        if _alpha_df is not None and len(_alpha_df) >= 200:
                            _discovery = AlphaDiscoveryEngine(state_file="alpha_discovery_state.json")
                            _split = int(len(_alpha_df) * 0.7)
                            _val_alphas = _discovery.run_discovery_cycle(
                                _alpha_df.iloc[:_split], _alpha_df.iloc[_split:]
                            )
                            if _val_alphas:
                                logging.getLogger("main").info(
                                    "Alpha Discovery: %d validated formulaic alphas discovered", len(_val_alphas)
                                )
                                try:
                                    from operations.event_bridge import emit as _alpha_emit
                                    _alpha_emit("ALPHA_DISCOVERED", {
                                        "count": len(_val_alphas),
                                        "alphas": [getattr(a, "expression", str(a)) for a in _val_alphas],
                                    }, correlation_id=f"alpha-{cycle_count}")
                                except Exception:
                                    pass
                    except Exception as _alpha_exc:
                        logging.getLogger("main").warning("Alpha discovery periodic run error: %s", _alpha_exc)

                # --- GA Alpha Miner: periodic evolutionary alpha mining ---
                if cycle_count % 200 == 0 and cycle_count > 0:
                    try:
                        # pd and Path come from module scope — local imports here
                        # shadow them for earlier main() branches (scrub-history
                        # uses Path, history-overview uses pd).
                        from research.alpha_miner.ga_engine import GAEngine, GAConfig
                        _ga_sym = (
                            current_symbol
                            if current_symbol and str(current_symbol).upper() != "AUTO"
                            else "EURUSD"
                        )
                        _ga_tf = args.timeframe if args.timeframe != "AUTO" else "H1"
                        _ga_bars = int(os.getenv("BACKTEST_VALIDATION_BARS", "4000") or 4000)
                        _ga_df = fetch_ohlc(_ga_sym, _ga_tf, count=max(_ga_bars, 500))
                        if _ga_df is not None and len(_ga_df) >= 100:
                            _ga_config = GAConfig(population_size=30, generations=2, min_ic_threshold=0.01)
                            _ga = GAEngine(config=_ga_config)
                            _ga_result = _ga.evolve(_ga_df, verbose=False)
                            _significant = _ga_result.significant_ics
                            if _significant:
                                logging.getLogger("main").info(
                                    "GA Miner: %d/%d frontier alphas significant after "
                                    "Bonferroni over %d trials, best IC=%.4f",
                                    len(_significant), len(_ga_result.best_ics),
                                    _ga_result.n_trials,
                                    max(abs(ic) for ic in _significant),
                                )
                                # Persist for ensemble integration
                                _out = Path("artifacts/mined_alphas.json")
                                _out.parent.mkdir(parents=True, exist_ok=True)
                                # NOTE: use module-level json import — a local `import json`
                                # here would shadow the builtin for ALL earlier main() branches
                                # (UnboundLocalError in strategy-tune/macro-prune printers).
                                _out.write_text(json.dumps({
                                    "expressions": [str(e) for e in _ga_result.significant_expressions[:10]],
                                    "best_ics": [round(float(ic), 4) for ic in _ga_result.significant_ics[:10]],
                                    "n_trials": _ga_result.n_trials,
                                    "cycle": cycle_count,
                                }, indent=2), encoding="utf-8")
                            else:
                                logging.getLogger("main").info(
                                    "GA Miner: no frontier alpha survived Bonferroni "
                                    "deflation over %d trials (best IC=%.4f)",
                                    _ga_result.n_trials,
                                    max((abs(ic) for ic in _ga_result.best_ics), default=0.0),
                                )
                    except Exception as _ga_exc:
                        logging.getLogger("main").debug("GA miner periodic run error: %s", _ga_exc)

                # --- Progressive Gate Evaluation: check strategy quality every 500 cycles ---
                if cycle_count % 500 == 0 and cycle_count > 0:
                    try:
                        from core.progressive_gates import ProgressiveGateSystem
                        from core.walk_forward_validator import WalkForwardValidator
                        _pgs = ProgressiveGateSystem()
                        _wfv = WalkForwardValidator(state_file=None)
                        if 'df' in locals() and df is not None and len(df) >= 200:
                            _close = df["close"].values if "close" in df.columns else None
                            if _close is not None and len(_close) >= 100:
                                _returns = np.diff(_close) / _close[:-1]
                                _returns = _returns[np.isfinite(_returns)]
                                _metrics = {
                                    "oos_sharpe": float(np.mean(_returns) / max(np.std(_returns), 1e-8) * np.sqrt(252 * 24)),
                                    "min_trades": 100,
                                    "min_win_rate": float(np.mean(_returns > 0)),
                                    "min_profit_factor": float(np.sum(_returns[_returns > 0]) / max(abs(np.sum(_returns[_returns < 0])), 1e-8)),
                                    "max_drawdown": float(max(0, 1 - np.min(np.cumsum(_returns)) / max(np.max(np.cumsum(_returns)), 1e-8))),
                                }
                                for _stage in ["SCREENING", "PAPER", "DEPLOYMENT"]:
                                    _verdict = _pgs.evaluate(_metrics, current_stage=_stage)
                                    if _verdict.passed:
                                        logging.getLogger("main").info(
                                            "Progressive Gate %s: PASSED (quality=%.3f)", _stage, _verdict.quality_score
                                        )
                    except Exception as _pg_exc:
                        logging.getLogger("main").debug("Progressive gate eval error: %s", _pg_exc)

                # --- Divergence Monitor: periodic live-vs-backtest check ---
                if cycle_count % 200 == 0 and cycle_count > 0:
                    try:
                        _div_monitor = DivergenceMonitor()
                        for _sym in (watchlist if watchlist else ["EURUSD"]):
                            _div_result = _div_monitor.check_divergence(_sym)
                            if _div_result.get("alert"):
                                logging.getLogger("main").warning(
                                    "DIVERGENCE ALERT [%s]: %s (severity=%s)",
                                    _sym, _div_result.get("reason", ""), _div_result.get("severity"),
                                )
                    except Exception as _div_exc:
                        logging.getLogger("main").debug("Divergence monitor skipped: %s", _div_exc)

                # --- Write engine state for API server (market_state.json, scanner_state.json) ---
                try:
                    _engine_ts = int(time.time() * 1000)
                    _market_state = {"ts": _engine_ts, "quotes": {}}
                    _scanner_state = {"ts": _engine_ts, "results": []}

                    # Fetch live quotes for watchlist symbols via MT5 tick
                    _watchlist_syms = watchlist if watchlist else ["EURUSD", "XAUUSD", "GBPJPY", "US500"]
                    try:
                        import MetaTrader5 as _mt5_mod
                        for _sym in _watchlist_syms:
                            try:
                                _tick = _mt5_mod.symbol_info_tick(_sym)
                                if _tick and _tick.bid and _tick.ask and _tick.ask >= _tick.bid:
                                    _market_state["quotes"][_sym] = {
                                        "bid": round(float(_tick.bid), 6),
                                        "ask": round(float(_tick.ask), 6),
                                        "spread": round(float(_tick.ask - _tick.bid), 6),
                                        "ts": _engine_ts,
                                    }
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # Cache scanner results if available
                    if 'opportunities' in dir() and opportunities:
                        _scanner_state["results"] = [
                            {
                                "id": sym.get("symbol", ""),
                                "symbol": sym.get("symbol", ""),
                                "score": sym.get("score", 0),
                                "reasons": sym.get("reasons", []),
                                "spread": sym.get("spread", 0),
                                "volatility": sym.get("volatility", 0),
                            }
                            for sym in (opportunities if isinstance(opportunities, list) else [])
                        ]

                    # Write states atomically (tmp + rename for crash safety)
                    import tempfile as _tmpfile
                    for _state_path, _state_data in [
                        (os.path.join(BASE_DIR, "market_state.json"), _market_state),
                        (os.path.join(BASE_DIR, "scanner_state.json"), _scanner_state),
                    ]:
                        try:
                            _fd, _tmp = _tmpfile.mkstemp(dir=BASE_DIR, suffix=".tmp")
                            with os.fdopen(_fd, "w", encoding="utf-8") as _f:
                                json.dump(_state_data, _f, separators=(",", ":"))
                            os.replace(_tmp, _state_path)
                        except Exception:
                            try:
                                os.unlink(_tmp)
                            except Exception:
                                pass
                except Exception as _state_exc:
                    logging.getLogger("main").debug("Engine state sync skipped: %s", _state_exc)

                # Sleep between cycles
                if args.continuous or cycle_count < args.loops:
                    sleep_time = max(10.0 if (holiday_active or skip_analysis) else 0.5, float(args.poll_interval))
                    if display:
                        display.update_footer(f"Next market pulse in {sleep_time:g}s (LLM remains event-gated)...")
                        # Heartbeat loop with sub-second-safe polling.
                        # Also polls the stop flag so a graceful-stop request
                        # lands within ~1s instead of after a full poll
                        # interval (the flag is consumed at the top of the
                        # next iteration, not here).
                        deadline = time.monotonic() + sleep_time
                        while time.monotonic() < deadline:
                            display.update_heartbeat()
                            _drain_operator_controls(orch)
                            if _stop_file_requested() or shutdown_requested():
                                break
                            time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))
                    else:
                        print(f"\n Next market pulse in {sleep_time:g} seconds...")
                        # Headless path: same stop-flag poll, same ~1s bound.
                        deadline = time.monotonic() + sleep_time
                        while time.monotonic() < deadline:
                            _drain_operator_controls(orch)
                            if _stop_file_requested() or shutdown_requested():
                                break
                            time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))

        except KeyboardInterrupt:
            print("\n■ Stopping trader...")
        except Exception as e:
            run_status = "FAILED"
            logging.getLogger("main").exception("Critical error in main loop")
        finally:
            # --- Shutdown order (CC-F14): Stop control thread -> cycles -> join sched-bounded -> velocity -> orchestrator -> guardian stop LAST -> CrashRecovery ---
            # 0. Stop the operator-control thread first so no new control
            # applies mid-shutdown, then drain once more for stragglers.
            try:
                if "_control_stop" in locals() and _control_stop is not None:
                    _control_stop.set()
                if "_control_thread" in locals() and _control_thread is not None:
                    _control_thread.join(timeout=5.0)
                if "orch" in locals() and orch is not None:
                    _drain_operator_controls(orch)
            except Exception as _ct_exc:
                logging.getLogger("main").debug("Control thread stop skipped: %s", _ct_exc)
            if 'orch' in locals() and orch is not None:
                # 1. Join bounded scheduler (10 s bound) via the real worker
                # reference (M1: the old bounded_pipeline.join was dead code).
                try:
                    _joiner = getattr(orch, "join_schedule_batch", None)
                    if callable(_joiner):
                        _joiner(timeout=10.0)
                    else:
                        bounded_sched = getattr(orch, "bounded_pipeline", None) or getattr(orch, "scheduler", None)
                        if hasattr(bounded_sched, "join"):
                            bounded_sched.join(timeout=10.0)
                except Exception as _b_exc:
                    logging.getLogger("main").debug("Bounded scheduler join skipped: %s", _b_exc)

                # 2. Velocity shutdown & orchestrator shutdown
                try:
                    orch.shutdown()
                except Exception:
                    logging.getLogger("main").exception("Failed to stop dual-velocity workers")

                # 3. Finish durable agent run
                try:
                    orch.operations.finish_run(
                        orch.run_id,
                        status=run_status,
                        payload={"reason": "main_loop_exit"},
                    )
                except Exception:
                    logging.getLogger("main").exception("Failed to close durable agent run")

            # 4. Supervisor shutdown
            try:
                from operations.supervisor import get_supervisor as _gs
                _sup_exit = _gs()
                _sup_exit.request_shutdown(f"run_status={run_status}")
                _sup_exit.complete_shutdown()
            except Exception as exc:
                logging.getLogger("mt5_trader").debug("shutdown failed: %s", exc)

            # 5. Stop Ψ Guardian LAST (position protection remains alive throughout background shutdown)
            if _guardian is not None:
                try:
                    _guardian.stop()
                    logging.getLogger("main").info("Ψ Guardian stopped")
                except Exception:
                    pass

            # 6. CrashRecovery shutdown snapshot (taken only after all worker threads are idle)
            if _crash_recovery is not None:
                try:
                    _crash_recovery.shutdown()
                    logging.getLogger("main").info(
                        "CrashRecovery shutdown snapshot saved (graceful exit)"
                    )
                except Exception as _cr_exc:
                    logging.getLogger("main").warning(
                        "CrashRecovery shutdown snapshot skipped: %s", _cr_exc)

            if display:
                display.stop()

            if '_engine_file_lock' in globals() and _engine_file_lock is not None:
                try:
                    _engine_file_lock.release()
                except Exception:
                    pass
                
            if args.show_metrics:
                if 'orch' in locals() and orch and orch.perf:
                    print("\n")
                    orch.perf.print_summary()
                else:
                    print("\n[Metrics] Orchestrator not initialized.")
                print(f"Total Trades Logged: {len(perf.history)}")
                print(f"Rolling Sharpe (20): {perf.calc_rolling_sharpe():.4f}")
                print(f"Max Drawdown:        {perf.calc_max_drawdown():.4f}")
                
                # Calculate Win Rate (from closed trade history)
                closed = [t for t in perf.history if t["status"] == "CLOSED"]
                wins = [t for t in closed if t["pnl"] > 0]
                win_rate = (len(wins) / len(closed)) * 100 if closed else 0.0
                print(f"Win Rate (Closed):   {win_rate:.1f}%")
                if dashboard:
                    dashboard.update_metrics({
                        "win_rate": win_rate, 
                        "drawdown": perf.calc_max_drawdown(),
                        "session_pnl": sum(t.get('pnl', 0) for t in closed)
                    })
                print("="*50 + "\n")

            if args.show_rl_metrics and orch.rl_trainer:
                print("\n" + "="*50)
                print("  RL TRAINING METRICS")
                print("="*50)
                print(f"Total Updates: {orch.rl_trainer.total_updates}")
                print(f"Epsilon:       {orch.rl_trainer.epsilon:.4f}")
                print("="*50 + "\n")
                print("="*50 + "\n")
                
                # EXPLICT SAVE ON EXIT
                try:
                    # Check if orch exists in local scope and has trainer
                    if 'orch' in locals() and orch and hasattr(orch, 'rl_trainer') and orch.rl_trainer:
                        orch.rl_trainer.save_model()
                        print("✔ RL Model Saved.")
                except Exception as e:
                    logging.getLogger("main").warning(f"Failed to save RL model on exit: {e}")

            # --- Final alpha dashboard generation on exit ---
            if not getattr(args, 'no_dashboard', False):
                try:
                    from ui.alpha_dashboard import generate_alpha_dashboard, get_dashboard_gen_count
                    _final_dash = generate_alpha_dashboard(
                        state_file="alpha_decay_state.json",
                        output_path="alpha_dashboard.html",
                    )
                    if _final_dash:
                        print(f"\n▣ Alpha dashboard (final): {_final_dash}")
                    total_gens = get_dashboard_gen_count()
                    if total_gens > 0:
                        print(f"   Dashboard auto-generated {total_gens} time(s) during this run.")
                except Exception as exc:
                    logging.getLogger("mt5_trader").debug("exit operation failed: %s", exc)

    if client:
        client.shutdown()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.getLogger("main").exception("Error: %s", e)
        sys.exit(1)
