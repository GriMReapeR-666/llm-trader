"""WorkflowAgent persona: systematic institutional quant workflow evaluation.

Runs periodic sweeps of the 7 institutional quant workflows
(statarb, mean_reversion, macro_momentum, microstructure, neural_kronos,
macro_event_alpha, retail_quant) on behalf of TechnicalNet.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def handle_workflow_sweep(payload: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
    """Execute scheduled sweep of institutional quant workflows.

    Executed on TechnicalNet schedule. Tests current market stationarity,
    Kalman cointegration spreads, event windows, and neural confidence.
    """
    t0 = time.time()
    symbol = str(payload.get("symbol", "EURUSD") or "EURUSD")
    symbols = payload.get("symbols") or [symbol]

    # Resolve QuantWorkflowEngine and TradingContext
    engine = None
    harness_ctx = None

    if context is not None:
        engine = getattr(context, "quant_workflow_engine", None)
        harness_ctx = getattr(context, "harness_ctx", None)

    if engine is None:
        try:
            from core.harness.workflows import QuantWorkflowEngine
            engine = QuantWorkflowEngine()
        except Exception as exc:
            logger.warning("Could not initialize QuantWorkflowEngine: %s", exc)
            return {"status": "ERROR", "error": str(exc), "duration_ms": 0.0}

    # Fallback minimal context if harness_ctx is unavailable
    if harness_ctx is None:
        try:
            from core.harness.kernel import TradingContext
            from core.harness.plugins import (
                RiskManagementPlugin, ExecutionSafetyPlugin,
                CointegrationGovernancePlugin, QuantWorkflowsPlugin,
            )
            harness_ctx = TradingContext(name="WorkflowSweepContext")
            harness_ctx.plugin(RiskManagementPlugin, balance=100000.0, max_risk_pct=0.01)
            harness_ctx.plugin(ExecutionSafetyPlugin)
            harness_ctx.plugin(CointegrationGovernancePlugin)
            harness_ctx.plugin(QuantWorkflowsPlugin)
        except Exception as exc:
            logger.debug("Minimal TradingContext creation for sweep: %s", exc)

    all_workflows = ["statarb", "mean_reversion", "macro_momentum",
                     "microstructure", "neural_kronos", "macro_event_alpha", "retail_quant"]
    
    sweep_results: Dict[str, Dict[str, Any]] = {}
    actionable_proposals: List[Dict[str, Any]] = []

    for sym in symbols[:3]:  # Bounded to top 3 symbols for budget safety
        sym_results: Dict[str, Any] = {}
        for wf_name in all_workflows:
            try:
                # Prepare a standard market snapshot
                snapshot = {
                    "symbol": sym,
                    "price": 1.0850,
                    "atr": 0.0015,
                    "bid": 1.0849,
                    "ask": 1.0851,
                    "quote_spread": 0.0002,
                    "macro_regime": "EXPANSION",
                }
                res = engine.execute_workflow(wf_name, ctx=harness_ctx, symbol=sym, market_snapshot=snapshot)
                sym_results[wf_name] = {
                    "decision": res.decision,
                    "latency_ms": round(res.total_latency_ms, 2),
                    "steps_passed": sum(1 for s in res.steps if s.passed),
                    "total_steps": len(res.steps),
                }
                if res.decision == "EXECUTE" and res.proposal:
                    actionable_proposals.append({
                        "symbol": sym,
                        "workflow": wf_name,
                        "proposal": res.proposal,
                    })
            except Exception as exc:
                sym_results[wf_name] = {"decision": "ERROR", "error": str(exc)[:200]}

        sweep_results[sym] = sym_results

    duration_ms = round((time.time() - t0) * 1000.0, 2)
    report = {
        "status": "OK",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "symbols_evaluated": list(sweep_results.keys()),
        "workflows": sweep_results,
        "actionable_count": len(actionable_proposals),
        "duration_ms": duration_ms,
        "execution_authority": False,
    }

    # Record to persistent store if context provides it
    if context is not None and hasattr(context, "store") and hasattr(context.store, "set_state"):
        try:
            context.store.set_state("last_quant_workflow_sweep", report)
        except Exception as exc:
            logger.debug("Failed to persist workflow sweep state: %s", exc)

    logger.info(
        "Quant workflow sweep completed in %.1fms across %d symbols: %d actionable proposals",
        duration_ms, len(sweep_results), len(actionable_proposals),
    )
    return report

_bar_bus_instance: Optional[Any] = None


def get_bar_opportunity_bus(store: Any = None, graph_engine: Any = None) -> Any:
    global _bar_bus_instance
    if _bar_bus_instance is None:
        from intelligence.opportunity_bus import BarSynchronizedOpportunityBus
        _bar_bus_instance = BarSynchronizedOpportunityBus(store=store, graph_engine=graph_engine)
    elif store is not None and getattr(_bar_bus_instance, "store", None) is None:
        _bar_bus_instance.store = store
    return _bar_bus_instance


def handle_bar_synchronized_auction(payload: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
    """Execute scheduled Bar-Synchronized Opportunity Auction for TechnicalNet.

    Evaluates active quant strategies at bar boundaries (M15 default), routing:
      - SCALP candidates -> Fast Lane (core.execution_planning)
      - SWING/LONG_TERM candidates -> Deep Reasoning Lane (intelligence.graph_engine_facade)
    """
    t0 = time.time()
    symbol = str(payload.get("symbol", "EURUSD") or "EURUSD")
    timeframe = str(payload.get("timeframe", "M15") or "M15")
    bar_time = payload.get("bar_time")

    store = getattr(context, "store", None) if context is not None else None
    graph_engine = getattr(context, "graph_engine", None) if context is not None else None
    market_data = getattr(context, "market_data", None) if context is not None else None

    bus = get_bar_opportunity_bus(store=store, graph_engine=graph_engine)

    # Fast skip if bar hasn't rolled over (prevents redundant CPU/GPU load)
    if bar_time is not None and not bus.is_new_bar(symbol, timeframe, bar_time):
        return {
            "status": "SKIPPED_SAME_BAR",
            "symbol": symbol,
            "timeframe": timeframe,
            "bar_time": bar_time,
            "duration_ms": round((time.time() - t0) * 1000.0, 2),
            "execution_authority": False,
        }

    auction_res = bus.run_synchronized_bar_auction(
        symbol=symbol,
        timeframe=timeframe,
        bar_time=bar_time,
        market_data=market_data,
        context=payload.get("context"),
        strategies=payload.get("strategies"),
        graph_engine=graph_engine,
        quote_bid=payload.get("quote_bid"),
        quote_ask=payload.get("quote_ask"),
    )

    duration_ms = round((time.time() - t0) * 1000.0, 2)
    auction_res["duration_ms"] = duration_ms
    auction_res["execution_authority"] = False

    logger.info(
        "Bar synchronized auction completed in %.1fms for %s %s: %d candidates (scalp: %d, swing: %d)",
        duration_ms, symbol, timeframe,
        auction_res.get("candidates_count", 0),
        len(auction_res.get("scalp_winners", [])),
        len(auction_res.get("swing_winners", [])),
    )
    return auction_res

