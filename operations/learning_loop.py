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


def _resolve_broker_deals(context: Any = None, payload: Optional[Dict[str, Any]] = None):
    """Return (deals, require_timestamps, source) for reconciliation, or None.

    Preference order is deliberate: an explicit ``paper_history`` in the payload
    beats a live paper broker on the orchestrator, which beats the MT5 terminal.
    Journal-derived input is refused downstream by
    :func:`operations.daily_reconciliation.compare_day`, so it is not second-guessed here.
    """
    supplied = (payload or {}).get("paper_history")
    if supplied:
        return list(supplied), False, "paper_broker"
    broker = getattr(context, "paper_broker", None)
    if broker is not None and getattr(broker, "history", None):
        return list(broker.history), False, "paper_broker"
    try:
        # The canonical lazy accessor: reads an already-loaded mt5_trader module
        # if there is one and never imports the CLI here.
        from core.order_execution import _get_mt5
        mt5_inst = _get_mt5()
    except Exception:
        mt5_inst = None
    if mt5_inst is not None:
        return mt5_inst, True, "mt5"
    return None


def _reconcile_and_attribute(db_path: str, context: Any = None,
                             payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one daily broker reconciliation and rotate the reward ledger.

    This used to be unreachable: the standalone executor returned
    ``status=COMPLETED`` without doing anything, and the orchestrator branch
    skipped it entirely in paper mode -- the mode this system actually runs in.
    Both halves of the evidence chain (broker-reconciled closes, and the L2
    reward events PPO/GRPO replay) hang off it.
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    from operations.daily_reconciliation import adapt_paper_deals, compare_day

    resolved = _resolve_broker_deals(context, payload)
    if resolved is None:
        return {"broker_source": None,
                "daily_reconciliation": {"status": "SKIPPED_NO_BROKER_SOURCE",
                                         "reason": "no paper history, paper broker, or MT5 terminal "
                                                   "available; reconciliation evidence is unavailable, "
                                                   "not merely unrecorded",
                                         "execution_authority": False},
                "reward_attribution": {"status": "NOT_RUN", "execution_authority": False}}
    deals, require_timestamps, source = resolved
    now = _dt.now(_tz.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = min(now, start + _td(days=1))

    from core.trading_safety import TradingStore

    store = TradingStore(db_path)
    if isinstance(deals, list):
        broker_deals = adapt_paper_deals(deals, source="paper_broker" if source == "paper_broker" else None)
    else:
        history = deals.history_deals_get(start, end)
        if history is None:
            return {"broker_source": source,
                    "daily_reconciliation": {"status": "SKIPPED_NO_BROKER_HISTORY",
                                             "reason": "MT5 deal history unavailable for the period",
                                             "execution_authority": False},
                    "reward_attribution": {"status": "NOT_RUN", "execution_authority": False}}
        broker_deals = history
    report = compare_day(store, broker_deals, start, end, require_timestamps=require_timestamps)

    # Only rotate the reward ledger for evidence this pass actually produced.
    attribution: Dict[str, Any] = {"status": "NOT_RUN", "execution_authority": False}
    if report.get("reconciled_execution_evidence", {}).get("written"):
        try:
            from operations.reward_attribution import attribute_reconciled_history
            attribution = attribute_reconciled_history(db_path, run_id=(payload or {}).get("run_id"))
        except Exception as exc:
            attribution = {"status": "ERROR", "error": str(exc)[:300], "execution_authority": False}
    return {"broker_source": source, "daily_reconciliation": report,
            "reward_attribution": attribution, "execution_authority": False}


def run_daily_review(payload: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
    """Execute daily review: daily narrative generation and broker reconciliation."""
    db_path = os.getenv("LLM_TRADER_JOURNAL_PATH", "trading_state.db")

    delegated = None
    if context is not None and hasattr(context, "_execute_daily_review"):
        delegated = context._execute_daily_review(payload)

    result: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_authority": False,
    }
    if delegated is not None:
        result.update({k: v for k, v in delegated.items() if k != "execution_authority"})
        # The orchestrator branch only reconciles in live mode; do not duplicate
        # a report it already produced.
        if delegated.get("daily_reconciliation") is None:
            produced = _reconcile_and_attribute(db_path, context, payload)
            result["broker_source"] = produced["broker_source"]
            result["daily_reconciliation"] = produced["daily_reconciliation"]
            result["reward_attribution"] = produced["reward_attribution"]
        return result

    result.update(_reconcile_and_attribute(db_path, context, payload))
    result["status"] = "COMPLETED"
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
        # Admission goes through ``labelled_dataset`` (broker evidence +
        # deterministic label + feature provenance + not-a-fixture), the same
        # reader the promotion gate uses. Reading ``trade_labels`` directly let
        # the quarantined synthetic fixtures -- the rows a prior audit marked
        # ``synthetic: true / synthetic_fixture_legacy`` -- satisfy the >= 40
        # threshold and enter CPCV as if they were realized R-multiples. The
        # threshold itself is unchanged; only the evidence is honest now.
        from research.research_validation import labelled_dataset
        dataset = labelled_dataset(db_path)
        realized_rs = [float(r["r_multiple"]) for r in dataset.get("rows", [])
                       if r.get("r_multiple") is not None]
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
