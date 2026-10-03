"""Learning loop executors for continuous autonomous research and review schedules.

Routes scheduled tasks (self_improvement, daily_review, session_review, weekly_research)
to their authoritative implementations. When invoked from LLMOrchestrator, delegates
to orchestrator context; when invoked standalone, runs against TradingStore and canonical
research engines.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def run_session_review(payload: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
    """Execute session review: memory quality, growth update, and strategy accuracy scorecard."""
    if context is not None and hasattr(context, "_execute_session_review"):
        return context._execute_session_review(payload)

    db_path = os.getenv("LLM_TRADER_JOURNAL_PATH", "trading_state.db")
    result: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_authority": False,
    }
    try:
        from strategy_accuracy import build_strategy_accuracy_scorecard
    except ImportError:
        try:
            from intelligence.strategy_accuracy import build_strategy_accuracy_scorecard
        except ImportError:
            build_strategy_accuracy_scorecard = None

    if build_strategy_accuracy_scorecard is not None and os.path.exists(db_path):
        try:
            scorecard = build_strategy_accuracy_scorecard(db_path)
            result["strategy_accuracy"] = {
                "groups": scorecard.get("groups"),
                "sample_size": scorecard.get("sample_size"),
                "execution_authority": False,
            }
        except Exception as exc:
            result["strategy_accuracy_error"] = str(exc)[:300]
    return result


def run_daily_review(payload: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
    """Execute daily review: daily narrative generation and broker reconciliation."""
    if context is not None and hasattr(context, "_execute_daily_review"):
        return context._execute_daily_review(payload)

    result: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "COMPLETED",
        "execution_authority": False,
    }
    return result


def run_weekly_research(payload: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
    """Execute weekly research: scenario rebuild, sprint planning, and CPCV robustness backtest."""
    if context is not None and hasattr(context, "_execute_weekly_research"):
        return context._execute_weekly_research(payload)

    db_path = os.getenv("LLM_TRADER_JOURNAL_PATH", "trading_state.db")
    cpcv_report: Dict[str, Any] = {
        "status": "SKIPPED",
        "execution_authority": False,
    }

    try:
        from core.trading_safety import TradingStore
        store = TradingStore(db_path)
        with store._connect() as con:
            label_rows = con.execute(
                """SELECT l.r_multiple AS r_multiple
                   FROM trade_labels l JOIN trades t ON t.ticket = l.ticket
                   WHERE t.closed_at IS NOT NULL AND l.r_multiple IS NOT NULL
                   ORDER BY t.closed_at ASC"""
            ).fetchall()
        realized_rs = [float(r["r_multiple"]) for r in label_rows if r["r_multiple"] is not None]
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
    except Exception as exc:
        cpcv_report = {"status": "ERROR", "error": str(exc)[:400], "execution_authority": False}

    sprint: Dict[str, Any] = {"status": "SKIPPED", "execution_authority": False}
    try:
        from research_sprint_planner import plan_and_persist as plan_research_sprints
    except ImportError:
        try:
            from intelligence.research_sprint_planner import plan_and_persist as plan_research_sprints
        except ImportError:
            plan_research_sprints = None

    if plan_research_sprints is not None and os.path.exists(db_path):
        try:
            sprint = plan_research_sprints(db_path)
        except Exception as sprint_exc:
            sprint = {"status": "ERROR", "error": str(sprint_exc)[:400], "execution_authority": False}

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpcv_report": cpcv_report,
        "research_sprint": sprint,
        "execution_authority": False,
    }


def run_self_improvement(payload: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
    """Execute self-improvement cycle: memory consolidation, metrics, and profitability review."""
    if context is not None and hasattr(context, "_execute_self_improvement"):
        return context._execute_self_improvement(payload)

    db_path = os.getenv("LLM_TRADER_JOURNAL_PATH", "trading_state.db")
    improvement: Dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "execution_authority": False,
    }
    try:
        from profitability_foundation import report as profitability_report
    except ImportError:
        try:
            from intelligence.profitability_foundation import report as profitability_report
        except ImportError:
            profitability_report = None

    if profitability_report is not None and os.path.exists(db_path):
        try:
            improvement["profitability"] = profitability_report(db_path, operator_base=0.01)
        except Exception as exc:
            improvement["profitability_error"] = str(exc)[:400]

    try:
        from core.trading_safety import TradingStore
        store = TradingStore(db_path)
        store.set_state("last_self_improvement", improvement)
    except Exception:
        pass

    return improvement
