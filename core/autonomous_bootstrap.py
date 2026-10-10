"""Autonomous Bootstrap — master startup orchestrator.

Phases:
  1. VALIDATE   — backtest, walk-forward, overfit gate, strategy scoring
  2. PREPARE    — data sources, macro graph, memory, RL, guardian
  3. TRADE      — delegated to mt5_trader.py main loop
  4. SUSTAIN    — delegated to mt5_trader.py periodic tasks

Phase 1 is fail-closed: any blocker halts trading.
Phase 2 is best-effort: degradation allowed but logged.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from boot_fetch_watchdog import run_stage_with_deadline
from boot_datasheet import boot_datasheet_enabled, get_datasheet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PhaseResult:
    phase: str
    passed: bool
    duration_seconds: float
    details: Dict[str, Any] = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)


@dataclass
class BootstrapResult:
    ready: bool
    phase: str
    duration_seconds: float
    quality_score: float
    blockers: List[str]
    phase_results: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(fn, label: str, *args, **kwargs):
    """Run fn, returning result or a fallback error dict."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.warning("Bootstrap %s failed: %s", label, exc)
        return {"status": "ERROR", "error": str(exc)[:500]}


_PREPARE_STEP_DETAILS = {
    "2a_data_sources": "data_sources", "2b_macro_seed": "macro_graph",
    "2c_news": "news_ingest", "2c2_macro_bridge": "global_macro_bridge",
    "2c3_alt_data": "alternative_data", "2c4_yahoo": "yahoo_finance",
    "2c5_cot": "cftc_cot", "2c6_eia": "eia_petroleum",
    "2c7_forexfactory": "forexfactory", "2c8_exness": "exness",
}
_PREPARE_STEP_WARNING_TOKEN = {
    "2a_data_sources": "Data source init", "2b_macro_seed": "Macro graph",
    "2c_news": "News", "2c2_macro_bridge": "GlobalMacroBridge",
    "2c3_alt_data": "AltData", "2c4_yahoo": "YahooFinance",
    "2c5_cot": "CFTC", "2c6_eia": "EIA",
    "2c7_forexfactory": "ForexFactory", "2c8_exness": "Exness",
}


def record_prepare_rows(details: Dict[str, Any], step_elapsed: Dict[str, float],
                        warnings: List[str]) -> None:
    """Record one datasheet row per PREPARE step (status + elapsed + note).

    Bounded-stage timeouts surface in ``warnings`` (``details`` only keeps a
    bare SKIPPED marker), so each warning is matched back to its step for the
    note and an honest TIMEOUT status.
    """
    if not boot_datasheet_enabled():
        return
    _sheet = get_datasheet()
    for _sname, _sel in sorted(step_elapsed.items()):
        _det = details.get(_PREPARE_STEP_DETAILS.get(_sname, ""), {})
        _status = _det.get("status", "?") if isinstance(_det, dict) else "?"
        if _status == "INITIALIZED":
            _status = "OK"
        _note_parts: List[str] = []
        if isinstance(_det, dict):
            for _k in ("entities", "articles", "events", "reports",
                       "fred_series", "indices", "live_observations",
                       "total_observations"):
                if _k in _det:
                    _note_parts.append("%s=%s" % (_k, _det[_k]))
            if _det.get("error"):
                _note_parts.append(str(_det["error"]))
        _tok = _PREPARE_STEP_WARNING_TOKEN.get(_sname)
        for _w in warnings:
            if _tok and _w.startswith(_tok + ":"):
                # Strip the redundant "Token:" prefix — the row already
                # names the step — so the reason fits the note column.
                _note_parts.append(_w[len(_tok) + 1:])
                if "budget" in _w:
                    _status = "TIMEOUT"
                break
        _sheet.record_step(_sname, _sel, _status, "; ".join(_note_parts))


def _boot_stage(stage: str, fn: Callable[[], Any], budget_s: float) -> Any:
    """Run a PREPARE fetch stage under a deadline; fail open on timeout.

    A provider that hangs (e.g. requests with no effective timeout inside a
    third-party client) is abandoned at ``budget_s`` seconds and the stage
    returns a degraded marker instead of silently eating minutes of boot.
    Threads are not killed (impossible in Python) — the budget bounds waiting.
    """
    outcome = run_stage_with_deadline(stage, fn, budgets={stage: budget_s})
    if outcome["ok"]:
        return outcome["result"]
    if outcome["timed_out"]:
        logger.warning(
            "Bootstrap %s exceeded %.0fs budget; failing open (hung provider?)",
            stage, budget_s)
        return {"status": "TIMEOUT", "error": str(outcome["error"])[:200]}
    logger.warning("Bootstrap %s failed: %s", stage, outcome["error"])
    return {"status": "ERROR", "error": str(outcome["error"])[:200]}


def _has_measured_evidence(strategy: Dict[str, Any]) -> bool:
    """True only when a registry entry carries a measured return series.

    A win_rate / profit_factor / sharpe triple in a JSON file is a claim, not a
    measurement, and the journal's own order-intent and broker-execution tables
    are the independent record. Entries without a return series are refused
    rather than scored.
    """
    if not isinstance(strategy, dict):
        return False
    if str(strategy.get("metrics_provenance", "")).strip().lower() == "measured":
        return True
    returns = strategy.get("returns")
    if isinstance(returns, (list, tuple)) and len([r for r in returns if r is not None]) >= 30:
        return True
    metrics = strategy.get("metrics")
    if isinstance(metrics, dict):
        inner = metrics.get("returns")
        if isinstance(inner, (list, tuple)) and len([r for r in inner if r is not None]) >= 30:
            return True
    return False


def _load_strategy_registry() -> List[Dict[str, Any]]:
    """Load strategies from strategy_registry.json.

    The file's ``_EVIDENCE_INVALID`` marker is authoritative on this read. It
    exists to say the promoted metrics are unbacked, so while it is present no
    row may enter the runtime as FULL/EVALUATION — that stage is the whole claim
    being disputed. Rows come back as copies at CANDIDATE, which drops them out
    of ``active_strategies`` here without touching the file on disk.
    """
    registry_path = os.path.join(os.path.dirname(__file__), "..", "strategy_registry.json")
    registry_path = str(Path(registry_path).resolve())
    if not os.path.exists(registry_path):
        return []
    try:
        from operations.strategy_factory import unvalidated_view
        with open(registry_path, "r") as f:
            data = json.load(f)
        return unvalidated_view(data)
    except Exception as exc:
        logger.warning("Failed to load strategy registry: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Phase 1: VALIDATE
# ---------------------------------------------------------------------------

def _phase_validate(
    *,
    store: Any,
    client: Any,
    config: Any,
    orchestrator: Any,
    paper_mode: bool = False,
) -> PhaseResult:
    """Run all validation gates before first trade. Fail-closed.

    Live-authority gates (MT5 authorization, broker preflight, tiny-live
    rollout approval, research influence) are hard blockers for live/demo
    starts. Paper starts record them as DEFERRED so continuous simulation
    autonomy cannot be hard-blocked by evidence only a live deployment
    produces — without ever fabricating a pass.
    """
    t0 = time.time()
    blockers: List[str] = []
    details: Dict[str, Any] = {}

    # --- 1a. Crash recovery ---
    logger.info("[Bootstrap/VALIDATE] 1a. Crash recovery check")
    try:
        from core.crash_recovery import startup_recovery
        recovery = startup_recovery()
        details["crash_recovery"] = {
            "action": str(getattr(recovery, "action", "UNKNOWN")),
            "crash_detected": getattr(recovery, "crash_detected", False),
        }
        if str(getattr(recovery, "action", "")).upper() == "HALT_AND_ALERT":
            if not paper_mode:
                blockers.append("Crash recovery: hash chain broken, HALT_AND_ALERT")
            else:
                # Paper simulation: checkpoint-chain artifacts must not kill
                # unattended continuity, but the signal is recorded, not hidden.
                details["crash_recovery"]["paper_disposition"] = "ADVISORY"
                logger.warning(
                    "[Bootstrap/VALIDATE] 1a. Checkpoint chain HALT_AND_ALERT "
                    "downgraded to advisory for paper mode (live still blocked)"
                )
    except Exception as exc:
        logger.warning("Crash recovery check skipped: %s", exc)
        details["crash_recovery"] = {"status": "SKIPPED", "error": str(exc)[:200]}

    # --- 1b. MT5 connection + account verification ---
    logger.info("[Bootstrap/VALIDATE] 1b. MT5 connection verification")
    if client is not None:
        try:
            acc_info = None
            if hasattr(client, "account_info"):
                acc_info = client.account_info()
            elif hasattr(client, "get_account_info"):
                acc_info = client.get_account_info()
            if acc_info is None:
                try:
                    import MetaTrader5 as _mt5
                    acc_info = _mt5.account_info()
                except Exception:
                    acc_info = None

            if acc_info:
                if isinstance(acc_info, dict):
                    balance = float(acc_info.get("balance", 0) or 0)
                    leverage = acc_info.get("leverage", 0)
                    server = acc_info.get("server", "unknown")
                else:
                    balance = float(getattr(acc_info, "balance", 0) or 0)
                    leverage = getattr(acc_info, "leverage", 0)
                    server = getattr(acc_info, "server", "unknown")
                details["mt5_account"] = {
                    "balance": balance,
                    "leverage": leverage,
                    "server": server,
                    "status": "CONNECTED",
                }
                if balance <= 0:
                    blockers.append("MT5 account balance is zero or negative")
            else:
                details["mt5_account"] = {"status": "NO_INFO"}
                blockers.append("MT5 account_info returned None")
        except Exception as exc:
            details["mt5_account"] = {"status": "ERROR", "error": str(exc)[:200]}
            blockers.append(f"MT5 account verification failed: {exc}")
    else:
        details["mt5_account"] = {"status": "PAPER_MODE"}

    # --- 1c. Readiness report (9 gates) ---
    logger.info("[Bootstrap/VALIDATE] 1c. Readiness report")
    # The readiness report is a LIVE-deployment review: a paper journal cannot
    # carry MT5 authorization, broker preflight, tiny-live approval, or
    # research-influence approval, so BLOCKED is expected in paper mode. Paper
    # boots record every blocker (deferred vs advisory) for the operator and
    # the deck — never faked as passing — but do not hard-block simulation.
    _live_authority_markers = (
        "MT5 terminal/account authorization",
        "broker preflight",
        "tiny-live rollout approval",
        "learned/adaptive research influence",
    )
    try:
        from operations.readiness_report import build as build_readiness
        journal_path = store.path if hasattr(store, "path") else "trading_state.db"
        readiness = build_readiness(journal_path)
        readiness_blockers = list(readiness.get("blockers", []))
        deferred = [
            str(b) for b in readiness_blockers
            if any(marker in str(b) for marker in _live_authority_markers)
        ]
        advisory = [str(b) for b in readiness_blockers if str(b) not in deferred]
        details["readiness"] = {
            "status": readiness.get("status"),
            "blockers": readiness_blockers,
            "paper_mode": paper_mode,
            "deferred_live_authority_blockers": deferred,
            "advisory_blockers": advisory,
        }
        if not paper_mode:
            for b in readiness_blockers:
                blockers.append(f"Readiness: {b}")
    except Exception as exc:
        logger.warning("Readiness report skipped: %s", exc)
        details["readiness"] = {"status": "SKIPPED", "error": str(exc)[:200]}

    # --- 1d. Backtest all active strategies ---
    logger.info("[Bootstrap/VALIDATE] 1d. Backtest active strategies")
    strategies = _load_strategy_registry()
    # Empty is the expected result while the registry carries
    # _EVIDENCE_INVALID: the loader has already demoted every row, because
    # backtesting a strategy off metrics the file itself calls unbacked would
    # launder the claim through a fresh run.
    active_strategies = [
        s for s in strategies
        if s.get("stage") in ("FULL", "EVALUATION")
    ]
    backtest_results: Dict[str, Any] = {}
    if active_strategies:
        try:
            from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
            pipeline = ContinuousBacktestPipeline()
            for strat in active_strategies:
                strat_id = strat.get("id", "unknown")
                logger.info("  Backtesting strategy: %s", strat_id)
                result = _safe(
                    pipeline.run_validation_cycle,
                    f"backtest_{strat_id}",
                    symbol="EURUSD",
                    strategy_name=strat_id,
                    timeframe="H1",
                    bars=500,
                )
            backtest_results[strat_id] = {
                "passed": result.get("verdict", {}).get("passed", False) if isinstance(result, dict) else False,
                "sharpe": result.get("stages", {}).get("backtest", {}).get("sharpe", 0) if isinstance(result, dict) else 0,
                "max_dd": result.get("stages", {}).get("backtest", {}).get("max_drawdown_pct", 0) if isinstance(result, dict) else 0,
                # Measured per-bar returns, so the WFE / overfit gates below can
                # validate evidence rather than a series synthesised from the
                # Sharpe claim. None -> those gates report NOT_VALIDATED.
                "returns": result.get("stages", {}).get("backtest", {}).get("returns") if isinstance(result, dict) else None,
            }

        except Exception as exc:
            logger.warning("Backtest pipeline skipped: %s", exc)
    details["backtest"] = backtest_results

    # --- 1e. Walk-forward validation ---
    logger.info("[Bootstrap/VALIDATE] 1e. Walk-forward validation")
    wfe_results: Dict[str, Any] = {}
    try:
        from core.walk_forward_validator import WalkForwardValidator
        import numpy as np
        wfv = WalkForwardValidator(state_file=None)
        for strat_id, bt_data in backtest_results.items():
            if not bt_data.get("passed"):
                logger.info("  Skipping WFE for %s (backtest failed)", strat_id)
                wfe_results[strat_id] = {"status": "SKIPPED", "reason": "backtest_failed"}
                continue
            # Only measured evidence may be gated. This used to synthesise
            # np.random.normal(sharpe/252, 0.01, 200) from the backtest's own
            # Sharpe claim, so the walk-forward gate evaluated a series built
            # to its target instead of anything the strategy produced. A
            # backtest reporting a Sharpe but no return series is
            # NOT_VALIDATED, not a licence to manufacture one.
            measured_returns = bt_data.get("returns")
            if not measured_returns or len(measured_returns) < 120:
                wfe_results[strat_id] = {
                    "status": "NOT_VALIDATED",
                    "reason": "backtest reported a Sharpe but no measured return series",
                }
                continue
            verdict = wfv.walk_forward_efficiency_gate(
                np.asarray(measured_returns, dtype=float), train_size=100, test_size=20)
            wfe_results[strat_id] = {
                "wfe": round(getattr(verdict, "wfe", 0), 4),
                "passed": getattr(verdict, "passed", False),
                "n_folds": getattr(verdict, "n_folds", 0),
            }
            if not getattr(verdict, "passed", False):
                # Synthetic-return WFE/overfit probes are LIVE-deployment
                # promotion gates; in paper mode they advise, they do not
                # block continuous simulation autonomy.
                if not paper_mode:
                    blockers.append(f"WFE gate REJECT for {strat_id}: WFE={getattr(verdict, 'wfe', 0):.2f}")
                else:
                    logger.info("  WFE REJECT (paper: advisory only) for %s", strat_id)
    except Exception as exc:
        logger.warning("Walk-forward validation skipped: %s", exc)
    details["walk_forward"] = wfe_results

    # --- 1f. Overfit gate seeding ---
    logger.info("[Bootstrap/VALIDATE] 1f. Overfit gate seeding")
    overfit_results: Dict[str, Any] = {}
    try:
        from core.overfit_gate import OverfitGate
        gate = OverfitGate(store=store)
        for strat_id, bt_data in backtest_results.items():
            if not bt_data.get("passed"):
                continue
            # Only measured evidence may be validated. This used to synthesise
            # np.random.normal(sharpe/252, 0.01, 200) from the backtest's own
            # Sharpe claim, which handed the overfit detector a series built to
            # its target: a random walk then scored grade C / MODERATE and
            # cleared the DSR bar, while an identical real series was rejected
            # F / CRITICAL. A backtest that reports a Sharpe but no return
            # series is NOT_VALIDATED, not a licence to manufacture one.
            returns = bt_data.get("returns")
            if returns is None:
                overfit_results[strat_id] = {
                    "status": "NOT_VALIDATED",
                    "risk_level": "UNKNOWN",
                    "grade": "N/A",
                    "reason": "backtest reported a Sharpe but no measured return series",
                }
                continue
            gate_result = gate.validate_strategy(
                strategy=strat_id,
                returns=list(returns),
                n_strategies_tested=max(1, len(active_strategies)),
                returns_provenance="measured",
            )
            overfit_results[strat_id] = {
                "status": gate_result.status,
                "risk_level": gate_result.risk_level,
                "grade": gate_result.grade,
            }
            if gate_result.status == "REJECT" and not paper_mode:
                blockers.append(f"Overfit gate REJECT for {strat_id}: risk={gate_result.risk_level}")
    except Exception as exc:
        logger.warning("Overfit gate seeding skipped: %s", exc)
    details["overfit_gate"] = overfit_results

    # --- 1g. Strategy scoring + registry hygiene ---
    logger.info("[Bootstrap/VALIDATE] 1g. Strategy scoring")
    scoring_results: Dict[str, Any] = {}
    try:
        from core.progressive_gates import ProgressiveGateSystem
        pgs = ProgressiveGateSystem()
        for strat in strategies:
            strat_id = strat.get("id", "unknown")
            current_stage = strat.get("stage", "CANDIDATE")
            if current_stage not in ("FULL", "EVALUATION"):
                continue
            # Registry metrics are a summary someone wrote into a JSON file;
            # they are not evidence. Scoring them straight into a DEPLOYMENT
            # verdict let placeholder rows (e.g. profit_factor 2.45 over 342
            # trades, for strategies with no order intents and no broker
            # execution reports) drive a promotion decision. Only a strategy
            # carrying a measured return series may be scored.
            if not _has_measured_evidence(strat):
                scoring_results[strat_id] = {
                    "passed": False,
                    "quality_score": 0.0,
                    "current_stage": current_stage,
                    "status": "NOT_VALIDATED",
                    "reason": ("no measured return series in the registry; the "
                               "recorded win_rate/profit_factor/sharpe are "
                               "unattributed and cannot support a DEPLOYMENT "
                               "verdict"),
                }
                continue
            metrics = {
                "trades": strat.get("trades", 0),
                "win_rate": strat.get("win_rate", 0),
                "profit_factor": strat.get("profit_factor", 0),
                "sharpe": strat.get("sharpe", 0),
                "max_dd": strat.get("max_dd", 100),
            }
            verdict = pgs.evaluate(metrics, current_stage="DEPLOYMENT", strategy_id=strat_id)
            scoring_results[strat_id] = {
                "passed": verdict.passed,
                "quality_score": verdict.quality_score,
                "current_stage": current_stage,
            }
    except Exception as exc:
        logger.warning("Strategy scoring skipped: %s", exc)
    details["strategy_scoring"] = scoring_results

    # --- 1h. Macro fundamental studies ---
    logger.info("[Bootstrap/VALIDATE] 1h. Macro fundamental study validation")
    macro_results: Dict[str, Any] = {}
    try:
        from data.data_source_pipeline import catalog_fred_series
        fred_series = catalog_fred_series()
        macro_results["fred_series_count"] = len(fred_series) if isinstance(fred_series, list) else 0
        macro_results["status"] = "CATALOGED"
    except Exception as exc:
        logger.warning("Macro fundamental catalog skipped: %s", exc)
        macro_results["status"] = "SKIPPED"
    details["macro_fundamentals"] = macro_results

    duration = time.time() - t0
    passed = len(blockers) == 0

    logger.info(
        "[Bootstrap/VALIDATE] Complete: passed=%s duration=%.1fs blockers=%d",
        passed, duration, len(blockers),
    )

    return PhaseResult(
        phase="VALIDATE",
        passed=passed,
        duration_seconds=duration,
        details=details,
        blockers=blockers,
    )


# ---------------------------------------------------------------------------
# Phase 2: PREPARE
# ---------------------------------------------------------------------------

def _phase_prepare(
    *,
    store: Any,
    client: Any,
    config: Any,
    orchestrator: Any,
    velocity: Any = None,
) -> PhaseResult:
    """Prepare data, memory, RL, and safety threads. Best-effort (no hard blockers)."""
    t0 = time.time()
    warnings: List[str] = []
    details: Dict[str, Any] = {}

    step_elapsed: Dict[str, float] = {}

    # --- 2a. Data source initialization ---
    logger.info("[Bootstrap/PREPARE] 2a. Data source initialization")
    _t_step = time.time()
    try:
        from data.data_source_pipeline import catalog_fred_series
        fred_catalog = catalog_fred_series()
        details["data_sources"] = {
            "fred_series": len(fred_catalog) if isinstance(fred_catalog, list) else 0,
            "status": "INITIALIZED",
        }
    except Exception as exc:
        logger.warning("Data source init skipped: %s", exc)
        warnings.append(f"Data source init: {exc}")
        details["data_sources"] = {"status": "SKIPPED"}
    finally:
        step_elapsed["2a_data_sources"] = time.time() - _t_step

    # --- 2b. Macro graph seed + first ingestion ---
    # (bounded: this step runs the FRED/NewsAPI pipeline — the 94.5s silent
    # offender on the 2026-09-30 session-5 boot)
    logger.info("[Bootstrap/PREPARE] 2b. Macro graph seed")
    _t_step = time.time()
    try:
        macro_graph = None
        if orchestrator is not None and hasattr(orchestrator, "macro_graph"):
            macro_graph = orchestrator.macro_graph
        if macro_graph is not None:
            from data.data_source_pipeline import process_cycle_data_sources

            def _macro_seed() -> Any:
                return process_cycle_data_sources(
                    macro_graph=macro_graph,
                    journal_path=store.path if hasattr(store, "path") else "trading_state.db",
                    symbol="EURUSD",
                    include_optional_providers=False,
                    include_alternative_nowcast=True,
                )

            macro_result = _boot_stage(
                "bootstrap_macro_seed", _macro_seed,
                float(os.getenv("BOOT_STAGE_BUDGET_S", "45")))
            if isinstance(macro_result, dict) and macro_result.get("status") in ("TIMEOUT", "ERROR"):
                raise RuntimeError(macro_result.get("error", "bounded stage failed"))
            details["macro_graph"] = {
                "status": macro_result.get("status", "UNKNOWN"),
                "entities": len(macro_result.get("macro_entities_extra", [])),
            }
        else:
            details["macro_graph"] = {"status": "SKIPPED_NO_GRAPH"}
    except Exception as exc:
        logger.warning("Macro graph seed skipped: %s", exc)
        warnings.append(f"Macro graph: {exc}")
        details["macro_graph"] = {"status": "ERROR", "error": str(exc)[:200]}
    finally:
        step_elapsed["2b_macro_seed"] = time.time() - _t_step

    # --- 2c. News ingestion (first pass) ---
    logger.info("[Bootstrap/PREPARE] 2c. News ingestion")
    _t_step = time.time()
    try:
        macro_graph = None
        if orchestrator is not None and hasattr(orchestrator, "macro_graph"):
            macro_graph = orchestrator.macro_graph
        llm_router = None
        try:
            from data.llm_router import get_router
            llm_router = get_router()
        except Exception:
            pass
        news_result = _safe(
            lambda: __import__("data.realtime_news_ingest", fromlist=["run"]).run(
                graph=macro_graph,
                llm=llm_router,
                symbol="EURUSD",
                include_rss=True,
                extract_claims=False,  # skip heavy extraction at boot
            ),
            "news_ingest",
        )
        details["news_ingest"] = {
            "articles": news_result.get("article_count", 0) if isinstance(news_result, dict) else 0,
            "status": news_result.get("status", "UNKNOWN") if isinstance(news_result, dict) else "ERROR",
        }
    except Exception as exc:
        logger.warning("News ingestion skipped: %s", exc)
        warnings.append(f"News: {exc}")
        details["news_ingest"] = {"status": "SKIPPED"}
    finally:
        step_elapsed["2c_news"] = time.time() - _t_step

    # --- 2c2. Global Macro Bridge (FF + Exness + CFTC + EIA + SA + AltData) ---
    logger.info("[Bootstrap/PREPARE] 2c2. Global Macro Bridge")
    _t_step = time.time()
    try:
        from data.global_macro_bridge import GlobalMacroBridge

        def _gmb_snapshot() -> Any:
            gmb = GlobalMacroBridge()
            return gmb.get_macro_snapshot("EURUSD")

        snapshot = _boot_stage("bootstrap_global_macro_bridge", _gmb_snapshot,
                               float(os.getenv("BOOT_STAGE_BUDGET_S", "45")))
        if isinstance(snapshot, dict) and snapshot.get("status") in ("TIMEOUT", "ERROR"):
            raise RuntimeError(snapshot.get("error", "bounded stage failed"))
        details["global_macro_bridge"] = {
            "status": "INITIALIZED",
            "calendar_events": getattr(snapshot, "calendar_events_count", 0),
            "high_impact_near": getattr(snapshot, "high_impact_near", False),
            "cot_sentiment": getattr(snapshot, "cot_sentiment_bias", "unknown"),
            "energy_bias": getattr(snapshot, "energy_oil_bias", "unknown"),
            "macro_regime": getattr(snapshot, "macro_regime", "unknown"),
        }
    except Exception as exc:
        logger.warning("Global Macro Bridge init skipped: %s", exc)
        warnings.append(f"GlobalMacroBridge: {exc}")
        details["global_macro_bridge"] = {"status": "SKIPPED"}
    finally:
        step_elapsed["2c2_macro_bridge"] = time.time() - _t_step

    # --- 2c3. Alternative Data Provider (Nowcast: zero_lag + yahoo + eia + cot) ---
    logger.info("[Bootstrap/PREPARE] 2c3. Alternative Data Provider")
    _t_step = time.time()
    try:
        from data.alternative_data_provider import AlternativeDataProvider

        def _alt_nowcast() -> Any:
            alt = AlternativeDataProvider()
            return alt.fetch_nowcast_snapshot(include_stubs=False)

        nowcast = _boot_stage("bootstrap_alternative_data", _alt_nowcast,
                              float(os.getenv("BOOT_STAGE_BUDGET_S", "45")))
        if isinstance(nowcast, dict) and nowcast.get("status") in ("TIMEOUT", "ERROR"):
            raise RuntimeError(nowcast.get("error", "bounded stage failed"))
        live_count = sum(1 for obs in (nowcast if isinstance(nowcast, list) else [])
                         if isinstance(obs, dict) and obs.get("status") == "LIVE")
        details["alternative_data"] = {
            "status": "INITIALIZED",
            "total_observations": len(nowcast) if isinstance(nowcast, list) else 0,
            "live_observations": live_count,
        }
    except Exception as exc:
        logger.warning("Alternative Data Provider init skipped: %s", exc)
        warnings.append(f"AltData: {exc}")
        details["alternative_data"] = {"status": "SKIPPED"}
    finally:
        step_elapsed["2c3_alt_data"] = time.time() - _t_step

    # --- 2c4. Yahoo Finance provider ---
    logger.info("[Bootstrap/PREPARE] 2c4. Yahoo Finance")
    _t_step = time.time()
    try:
        from data.yahoo_finance_provider import YahooFinanceProvider

        def _yf_indices() -> Any:
            yf_prov = YahooFinanceProvider()
            return yf_prov.get_market_indices()

        indices = _boot_stage("bootstrap_yahoo_finance", _yf_indices,
                              float(os.getenv("BOOT_STAGE_BUDGET_S", "30")))
        if isinstance(indices, dict) and indices.get("status") in ("TIMEOUT", "ERROR"):
            raise RuntimeError(indices.get("error", "bounded stage failed"))
        details["yahoo_finance"] = {
            "status": "INITIALIZED",
            "indices": list(indices.keys())[:5] if isinstance(indices, dict) else [],
        }
    except Exception as exc:
        logger.warning("Yahoo Finance init skipped: %s", exc)
        warnings.append(f"YahooFinance: {exc}")
        details["yahoo_finance"] = {"status": "SKIPPED"}
    finally:
        step_elapsed["2c4_yahoo"] = time.time() - _t_step

    # --- 2c5. CFTC COT provider ---
    logger.info("[Bootstrap/PREPARE] 2c5. CFTC COT")
    _t_step = time.time()
    try:
        from data.cftc_cot_provider import CFTCCOTProvider

        def _cot_report() -> Any:
            cot = CFTCCOTProvider()
            return cot.fetch_cot_report()

        cot_report = _boot_stage("bootstrap_cftc_cot", _cot_report,
                                 float(os.getenv("BOOT_STAGE_BUDGET_S", "30")))
        if isinstance(cot_report, dict) and cot_report.get("status") in ("TIMEOUT", "ERROR"):
            raise RuntimeError(cot_report.get("error", "bounded stage failed"))
        details["cftc_cot"] = {
            "status": "INITIALIZED",
            "reports": len(cot_report) if isinstance(cot_report, list) else 0,
        }
    except Exception as exc:
        logger.warning("CFTC COT init skipped: %s", exc)
        warnings.append(f"CFTC: {exc}")
        details["cftc_cot"] = {"status": "SKIPPED"}
    finally:
        step_elapsed["2c5_cot"] = time.time() - _t_step

    # --- 2c6. EIA Petroleum provider (bounded; was the last unbounded fetch) ---
    logger.info("[Bootstrap/PREPARE] 2c6. EIA Petroleum")
    _t_step = time.time()
    try:
        from data.eia_provider import EIAProvider

        def _eia_report() -> Any:
            return EIAProvider().fetch_latest_report()

        eia_report = _boot_stage(
            "bootstrap_eia_petroleum", _eia_report,
            float(os.getenv("BOOT_STAGE_BUDGET_S", "30")))
        if isinstance(eia_report, dict) and eia_report.get("status") in ("TIMEOUT", "ERROR"):
            raise RuntimeError(eia_report.get("error", "bounded stage failed"))
        details["eia_petroleum"] = {
            "status": "INITIALIZED",
            "has_data": eia_report is not None,
        }
    except Exception as exc:
        logger.warning("EIA Petroleum init skipped: %s", exc)
        warnings.append(f"EIA: {exc}")
        details["eia_petroleum"] = {"status": "SKIPPED"}
    finally:
        step_elapsed["2c6_eia"] = time.time() - _t_step

    # --- 2c7. ForexFactory calendar ---
    logger.info("[Bootstrap/PREPARE] 2c7. ForexFactory")
    _t_step = time.time()
    try:
        from data.forexfactory_provider import ForexFactoryProvider

        def _ff_calendar() -> Any:
            ff = ForexFactoryProvider()
            return ff.fetch_calendar()

        ff_events = _boot_stage("bootstrap_forexfactory", _ff_calendar,
                                float(os.getenv("BOOT_STAGE_BUDGET_S", "30")))
        if isinstance(ff_events, dict) and ff_events.get("status") in ("TIMEOUT", "ERROR"):
            raise RuntimeError(ff_events.get("error", "bounded stage failed"))
        details["forexfactory"] = {
            "status": "INITIALIZED",
            "events": len(ff_events) if isinstance(ff_events, list) else 0,
        }
    except Exception as exc:
        logger.warning("ForexFactory init skipped: %s", exc)
        warnings.append(f"ForexFactory: {exc}")
        details["forexfactory"] = {"status": "SKIPPED"}
    finally:
        step_elapsed["2c7_forexfactory"] = time.time() - _t_step

    # --- 2c8. Exness market insights ---
    logger.info("[Bootstrap/PREPARE] 2c8. Exness")
    _t_step = time.time()
    try:
        from data.exness_provider import ExnessProvider

        def _exness_insights() -> Any:
            exness = ExnessProvider()
            return exness.fetch_insights()

        insights = _boot_stage("bootstrap_exness", _exness_insights,
                               float(os.getenv("BOOT_STAGE_BUDGET_S", "30")))
        if isinstance(insights, dict) and insights.get("status") in ("TIMEOUT", "ERROR"):
            raise RuntimeError(insights.get("error", "bounded stage failed"))
        details["exness"] = {
            "status": "INITIALIZED",
            "insights": len(insights) if isinstance(insights, list) else 0,
        }
    except Exception as exc:
        logger.warning("Exness init skipped: %s", exc)
        warnings.append(f"Exness: {exc}")
        details["exness"] = {"status": "SKIPPED"}
    finally:
        step_elapsed["2c8_exness"] = time.time() - _t_step

    # --- 2c9. SEC EDGAR filings ---
    logger.info("[Bootstrap/PREPARE] 2c9. SEC EDGAR")
    try:
        from data.sec_edgar_provider import SECEDGARProvider
        sec = SECEDGARProvider()
        sec_data = sec.fetch_filings()
        details["sec_edgar"] = {
            "status": "INITIALIZED",
            "filings": len(sec_data) if isinstance(sec_data, list) else 0,
        }
    except Exception as exc:
        logger.warning("SEC EDGAR init skipped: %s", exc)
        warnings.append(f"SEC: {exc}")
        details["sec_edgar"] = {"status": "SKIPPED"}

    # --- 2c10. OpenBB data bridge ---
    logger.info("[Bootstrap/PREPARE] 2c10. OpenBB Bridge")
    try:
        from data.openbb_data_bridge import OpenBBDataBridge
        openbb = OpenBBDataBridge()
        details["openbb_bridge"] = {"status": "INITIALIZED"}
    except Exception as exc:
        logger.warning("OpenBB Bridge init skipped: %s", exc)
        warnings.append(f"OpenBB: {exc}")
        details["openbb_bridge"] = {"status": "SKIPPED"}

    # --- 2c11. Zero-Lag Macro provider ---
    logger.info("[Bootstrap/PREPARE] 2c11. Zero-Lag Macro")
    try:
        from data.zero_lag_macro_provider import get_zero_lag_provider
        zl = get_zero_lag_provider()
        details["zero_lag_macro"] = {"status": "INITIALIZED" if zl else "UNAVAILABLE"}
    except Exception as exc:
        logger.warning("Zero-Lag Macro init skipped: %s", exc)
        warnings.append(f"ZeroLag: {exc}")
        details["zero_lag_macro"] = {"status": "SKIPPED"}

    # --- 2c12. Sentiment Veto Engine ---
    logger.info("[Bootstrap/PREPARE] 2c12. Sentiment Veto Engine")
    try:
        from core.sentiment_veto import SentimentVetoEngine
        sve = SentimentVetoEngine()
        details["sentiment_veto"] = {"status": "INITIALIZED"}
    except Exception as exc:
        logger.warning("Sentiment Veto init skipped: %s", exc)
        details["sentiment_veto"] = {"status": "SKIPPED"}

    # --- 2c13. Enhanced MT5 Calendar ---
    logger.info("[Bootstrap/PREPARE] 2c13. Enhanced MT5 Calendar")
    try:
        from data.mt5_calendar_enhanced import EnhancedMT5CalendarWrapper
        cal = EnhancedMT5CalendarWrapper()
        details["mt5_calendar"] = {"status": "INITIALIZED"}
    except Exception as exc:
        logger.warning("Enhanced MT5 Calendar init skipped: %s", exc)
        details["mt5_calendar"] = {"status": "SKIPPED"}

    # --- 2c14. News Triage (Layer-0 credibility filter) ---
    logger.info("[Bootstrap/PREPARE] 2c14. News Triage")
    try:
        from data.news_triage import triage_mode, triage_articles
        mode = triage_mode()
        details["news_triage"] = {"status": "INITIALIZED", "mode": mode}
    except Exception as exc:
        logger.warning("News Triage init skipped: %s", exc)
        warnings.append(f"NewsTriage: {exc}")
        details["news_triage"] = {"status": "SKIPPED"}

    # --- 2c15. Kill-Switch Latch (confirmation-laddered D9 state) ---
    logger.info("[Bootstrap/PREPARE] 2c15. Kill-Switch Latch")
    try:
        from data.kill_switch_latch import KillSwitchLatch, get_kill_switch_latch
        ks = get_kill_switch_latch()
        snap = ks.snapshot()
        details["kill_switch_latch"] = {
            "status": "INITIALIZED",
            "latched": snap.get("latched", False),
        }
    except Exception as exc:
        logger.warning("Kill-Switch Latch init skipped: %s", exc)
        warnings.append(f"KillSwitchLatch: {exc}")
        details["kill_switch_latch"] = {"status": "SKIPPED"}

    # --- 2c16. Multi-Disciplinary Domain Knowledge (12 geopolitical pillars) ---
    logger.info("[Bootstrap/PREPARE] 2c16. Domain Knowledge Pillars")
    try:
        import intelligence.multi_disciplinary_domain_knowledge as mdvk
        pillar_count = sum(1 for name in dir(mdvk) if name.startswith("evaluate_"))
        details["domain_knowledge"] = {"status": "INITIALIZED", "pillars": pillar_count}
    except Exception as exc:
        logger.warning("Domain Knowledge init skipped: %s", exc)
        warnings.append(f"DomainKnowledge: {exc}")
        details["domain_knowledge"] = {"status": "SKIPPED"}

    # --- 2c17. Sanctions & Financial Plumbing (OFAC/SWIFT/shadow fleet) ---
    logger.info("[Bootstrap/PREPARE] 2c17. Sanctions & Financial Plumbing")
    try:
        import intelligence.sanctions_financial_plumbing_and_evasion as sfpe
        screening_count = sum(1 for name in dir(sfpe) if name.startswith("evaluate_"))
        details["sanctions_plumbing"] = {"status": "INITIALIZED", "screeners": screening_count}
    except Exception as exc:
        logger.warning("Sanctions & Financial Plumbing init skipped: %s", exc)
        warnings.append(f"SanctionsPlumbing: {exc}")
        details["sanctions_plumbing"] = {"status": "SKIPPED"}

    # --- 2c18. Brain Observability (cockpit payloads) ---
    logger.info("[Bootstrap/PREPARE] 2c18. Brain Observability")
    try:
        from intelligence.brain_observability import full_observability
        details["brain_observability"] = {"status": "INITIALIZED"}
    except Exception as exc:
        logger.warning("Brain Observability init skipped: %s", exc)
        warnings.append(f"BrainObservability: {exc}")
        details["brain_observability"] = {"status": "SKIPPED"}

    # --- 2c19. FRED Backtest Data (historical for news gate) ---
    logger.info("[Bootstrap/PREPARE] 2c19. FRED Backtest Data")
    try:
        from research.fred_backtest_data import FredBacktestData, NFP_SERIES, CPI_SERIES, ESI_SERIES
        fb = FredBacktestData()
        series_count = len(NFP_SERIES) + len(CPI_SERIES) + len(ESI_SERIES)
        details["fred_backtest_data"] = {
            "status": "INITIALIZED",
            "series_count": series_count,
        }
    except Exception as exc:
        logger.warning("FRED Backtest Data init skipped: %s", exc)
        warnings.append(f"FredBacktestData: {exc}")
        details["fred_backtest_data"] = {"status": "SKIPPED"}

    # --- 2d. Memory consolidation + knowledge warmup ---
    logger.info("[Bootstrap/PREPARE] 2d. Memory warmup")
    try:
        if orchestrator is not None and hasattr(orchestrator, "memory_orchestrator"):
            mem = orchestrator.memory_orchestrator
            if mem is not None:
                consolidate = _safe(mem.consolidate, "memory_consolidate")
                decay = _safe(mem.decay, "memory_decay")
                details["memory"] = {
                    "consolidate": consolidate if isinstance(consolidate, dict) else {},
                    "decay": decay if isinstance(decay, dict) else {},
                    "status": "WARMED",
                }
            else:
                details["memory"] = {"status": "NO_ORCHESTRATOR"}
        else:
            details["memory"] = {"status": "SKIPPED"}
    except Exception as exc:
        logger.warning("Memory warmup skipped: %s", exc)
        warnings.append(f"Memory: {exc}")
        details["memory"] = {"status": "ERROR"}

    # --- 2e. Knowledge retrieval warmup query ---
    logger.info("[Bootstrap/PREPARE] 2e. Knowledge retrieval warmup")
    try:
        if orchestrator is not None and hasattr(orchestrator, "knowledge_retrieval"):
            kr = orchestrator.knowledge_retrieval
            if kr is not None:
                kr_result = _safe(
                    lambda: kr.query("market regime", limit=3),
                    "knowledge_warmup",
                )
                details["knowledge_retrieval"] = {
                    "results": len(kr_result.get("results", [])) if isinstance(kr_result, dict) else 0,
                    "status": "WARMED",
                }
            else:
                details["knowledge_retrieval"] = {"status": "NO_RETRIEVAL"}
        else:
            details["knowledge_retrieval"] = {"status": "SKIPPED"}
    except Exception as exc:
        logger.warning("Knowledge warmup skipped: %s", exc)
        warnings.append(f"Knowledge: {exc}")
        details["knowledge_retrieval"] = {"status": "ERROR"}

    # --- 2f. RL model load + baseline ---
    logger.info("[Bootstrap/PREPARE] 2f. RL model baseline")
    try:
        from research.rl_trainer import RLTrainer
        rl = RLTrainer()
        model_path = os.path.join(os.path.dirname(__file__), "..", "rl_model.json")
        if os.path.exists(str(Path(model_path).resolve())):
            rl.load_model()
            q_table_size = len(getattr(rl, "q_table", {}))
            details["rl_model"] = {
                "loaded": True,
                "q_table_entries": q_table_size,
                "status": "LOADED",
            }
        else:
            details["rl_model"] = {"loaded": False, "status": "FRESH_START"}
    except Exception as exc:
        logger.warning("RL model load skipped: %s", exc)
        warnings.append(f"RL: {exc}")
        details["rl_model"] = {"status": "SKIPPED"}

    # --- 2g. LLM provider health check ---
    logger.info("[Bootstrap/PREPARE] 2g. LLM provider health check")
    try:
        from data.llm_router import get_router
        router = get_router()
        llm_status = router.get_status()
        providers_online = sum(
            1 for p, cfg in llm_status.get("providers", {}).items()
            if cfg.get("keys", 0) > 0
        )
        agents_registered = len(llm_status.get("agents", {}))
        details["llm_providers"] = {
            "providers_with_keys": providers_online,
            "agents_registered": agents_registered,
            "providers": {
                name: {"keys": cfg.get("keys", 0), "status": cfg.get("status", "UNKNOWN")}
                for name, cfg in llm_status.get("providers", {}).items()
            },
            "status": "HEALTHY" if providers_online > 0 else "NO_PROVIDERS",
        }
        if providers_online == 0:
            warnings.append("No LLM providers have API keys configured")
    except Exception as exc:
        logger.warning("LLM provider check skipped: %s", exc)
        warnings.append(f"LLM: {exc}")
        details["llm_providers"] = {"status": "SKIPPED"}

    # --- 2h. Guardian + safety threads ---
    logger.info("[Bootstrap/PREPARE] 2h. Guardian startup")
    if not getattr(config, "paper", True):
        try:
            from core.external_guardian import start_guardian
            guardian = start_guardian()
            details["guardian"] = {"status": "STARTED"}
        except Exception as exc:
            logger.warning("Guardian start skipped: %s", exc)
            warnings.append(f"Guardian: {exc}")
            details["guardian"] = {"status": "SKIPPED"}
    else:
        details["guardian"] = {"status": "PAPER_MODE_SKIPPED"}

    # --- 2i. Alpha discovery (if CANDIDATE strategies exist) ---
    logger.info("[Bootstrap/PREPARE] 2i. Alpha discovery check")
    try:
        strategies = _load_strategy_registry()
        candidates = [s for s in strategies if s.get("stage") == "CANDIDATE"]
        details["alpha_discovery"] = {
            "candidate_count": len(candidates),
            "status": "CHECKED",
        }
    except Exception as exc:
        details["alpha_discovery"] = {"status": "SKIPPED"}

    # --- 2j. S.Q task queue hydration ---
    logger.info("[Bootstrap/PREPARE] 2j. Task queue hydration")
    try:
        from core.task_queue import TaskQueue, migrate_from_intervals
        from core.auto_quant_tools import DEFAULT_INTERVALS
        tq = migrate_from_intervals(DEFAULT_INTERVALS)
        metrics = tq.metrics()
        details["task_queue"] = {
            "total_tasks": getattr(metrics, "total_tasks", 0) if hasattr(metrics, "__dict__") else 0,
            "status": "HYDRATED",
        }
    except Exception as exc:
        logger.warning("Task queue hydration skipped: %s", exc)
        details["task_queue"] = {"status": "SKIPPED"}

    # --- 2k. Compliance audit init ---
    logger.info("[Bootstrap/PREPARE] 2k. Compliance audit")
    try:
        from core.compliance_audit import get_audit
        audit = get_audit()
        details["compliance_audit"] = {"status": "INITIALIZED"}
    except Exception as exc:
        logger.warning("Compliance audit init skipped: %s", exc)
        details["compliance_audit"] = {"status": "SKIPPED"}

    duration = time.time() - t0

    # Disclose per-step elapsed times in the completion log so silent slow
    # fetches (38-94s each on the 2026-09-30 session-5 boot) are visible.
    step_times = " ".join(
        "%s=%.1fs" % (k.split("_")[0], v) for k, v in sorted(step_elapsed.items()))
    logger.info(
        "[Bootstrap/PREPARE] Complete: duration=%.1fs warnings=%d%s",
        duration, len(warnings), (" steps=" + step_times) if step_times else "",
    )

    # Datasheet rows: one per PREPARE step (status + elapsed), merged later
    # with boot-watchdog outcomes and APIManager degradation markers.
    record_prepare_rows(details, step_elapsed, warnings)

    return PhaseResult(
        phase="PREPARE",
        passed=True,  # best-effort: never blocks
        duration_seconds=duration,
        details=details,
        blockers=warnings,  # warnings, not blockers
    )


# ---------------------------------------------------------------------------
# Master orchestrator
# ---------------------------------------------------------------------------

class AutonomousBootstrap:
    """Master startup orchestrator. Composes all 4 phases."""

    def __init__(
        self,
        store: Any = None,
        client: Any = None,
        config: Any = None,
        orchestrator: Any = None,
        velocity: Any = None,
        paper_mode: bool = False,
    ):
        self.store = store
        self.client = client
        self.config = config
        self.orchestrator = orchestrator
        self.velocity = velocity
        # Simulation scope: MT5 authorization / broker preflight / tiny-live
        # rollout / research-influence are LIVE-execution gates. In paper mode
        # they are recorded as deferred (never faked as passing); live boots
        # still fail closed on them.
        self.paper_mode = bool(paper_mode)

    def run(self) -> BootstrapResult:
        """Execute all bootstrap phases and return aggregated result."""
        t0 = time.time()
        all_blockers: List[str] = []
        phase_results: Dict[str, Any] = {}

        # Fresh datasheet per boot: all layers (bootstrap steps, boot-fetch
        # watchdog outcomes, APIManager degradation markers) record into this
        # one collector; the orchestrator renders the table exactly once.
        if boot_datasheet_enabled():
            get_datasheet().reset()

        logger.info("=" * 70)
        logger.info("AUTONOMOUS BOOTSTRAP STARTING")
        logger.info("=" * 70)

        # Phase 1: VALIDATE (fail-closed; paper defers live-authority gates)
        logger.info("--- Phase 1: VALIDATE ---")
        validate_result = _phase_validate(
            store=self.store,
            client=self.client,
            config=self.config,
            orchestrator=self.orchestrator,
            paper_mode=self.paper_mode,
        )
        phase_results["VALIDATE"] = {
            "passed": validate_result.passed,
            "duration": validate_result.duration_seconds,
            "blockers": validate_result.blockers,
            "details": validate_result.details,
        }
        all_blockers.extend(validate_result.blockers)

        if not validate_result.passed:
            logger.error(
                "Bootstrap BLOCKED at VALIDATE phase: %d blockers",
                len(validate_result.blockers),
            )
            for b in validate_result.blockers:
                logger.error("  BLOCKER: %s", b)
            return BootstrapResult(
                ready=False,
                phase="VALIDATE_FAILED",
                duration_seconds=time.time() - t0,
                quality_score=0.0,
                blockers=all_blockers,
                phase_results=phase_results,
            )

        # Phase 2: PREPARE (best-effort)
        logger.info("--- Phase 2: PREPARE ---")
        prepare_result = _phase_prepare(
            store=self.store,
            client=self.client,
            config=self.config,
            orchestrator=self.orchestrator,
            velocity=self.velocity,
        )
        phase_results["PREPARE"] = {
            "passed": prepare_result.passed,
            "duration": prepare_result.duration_seconds,
            "warnings": prepare_result.blockers,
            "details": prepare_result.details,
        }

        # Calculate quality score from phase details
        quality_score = self._compute_quality_score(phase_results)

        total_duration = time.time() - t0

        logger.info("=" * 70)
        logger.info(
            "BOOTSTRAP COMPLETE: ready=True duration=%.1fs quality=%.2f",
            total_duration, quality_score,
        )
        logger.info("=" * 70)

        # Persist bootstrap state
        self._persist_state(phase_results, quality_score)

        return BootstrapResult(
            ready=True,
            phase="READY",
            duration_seconds=total_duration,
            quality_score=quality_score,
            blockers=[],
            phase_results=phase_results,
        )

    def _compute_quality_score(self, phase_results: Dict[str, Any]) -> float:
        """Compute composite quality score (0.0 - 1.0)."""
        scores = []

        # Validation quality: backtest pass rate
        v_details = phase_results.get("VALIDATE", {}).get("details", {})
        backtests = v_details.get("backtest", {})
        if backtests:
            bt_passed = sum(1 for b in backtests.values() if b.get("passed"))
            scores.append(bt_passed / max(1, len(backtests)))

        # WFE pass rate
        wfe = v_details.get("walk_forward", {})
        if wfe:
            wfe_passed = sum(1 for w in wfe.values() if isinstance(w, dict) and w.get("passed"))
            scores.append(wfe_passed / max(1, len(wfe)))

        # Overfit pass rate
        overfit = v_details.get("overfit_gate", {})
        if overfit:
            of_passed = sum(1 for o in overfit.values() if isinstance(o, dict) and o.get("status") == "PASS")
            scores.append(of_passed / max(1, len(overfit)))

        # Preparation quality
        p_details = phase_results.get("PREPARE", {}).get("details", {})

        # LLM providers
        if p_details.get("llm_providers", {}).get("status") == "HEALTHY":
            scores.append(1.0)
        else:
            scores.append(0.5)

        # Macro graph
        if p_details.get("macro_graph", {}).get("status") == "PROCESSED":
            scores.append(1.0)
        else:
            scores.append(0.3)

        # Data source coverage (new: counts initialized providers)
        data_sources_initialized = sum(
            1 for key in (
                "global_macro_bridge", "alternative_data", "yahoo_finance",
                "cftc_cot", "eia_petroleum", "forexfactory", "exness",
                "sec_edgar", "openbb_bridge", "zero_lag_macro",
            )
            if isinstance(p_details.get(key), dict)
            and p_details[key].get("status") == "INITIALIZED"
        )
        scores.append(min(1.0, data_sources_initialized / 10.0))

        return sum(scores) / max(1, len(scores))

    def _persist_state(self, phase_results: Dict[str, Any], quality_score: float) -> None:
        """Persist bootstrap state to disk."""
        try:
            state_dir = os.path.join(os.path.dirname(__file__), "..", "artifacts")
            os.makedirs(state_dir, exist_ok=True)
            state_path = os.path.join(state_dir, "bootstrap_state.json")
            state = {
                "last_bootstrap": time.time(),
                "quality_score": quality_score,
                "phases": {
                    name: {
                        "passed": data.get("passed"),
                        "duration": data.get("duration"),
                    }
                    for name, data in phase_results.items()
                },
                "execution_authority": False,
            }
            with open(state_path, "w") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as exc:
            logger.debug("Bootstrap state persistence failed: %s", exc)
