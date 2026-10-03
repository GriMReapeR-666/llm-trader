"""Per-cycle narrative brief for observability (advisory only).

import logging

logger = logging.getLogger(__name__)
Assembles regime, drivers, edge, TA, event, risk, and open review tasks into
one auditable object. Never grants execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = "cycle_brief_v1"


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def build(
    toon: Dict[str, Any],
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    cycle_key: str = "",
    kill_switch_active: bool = False,
    open_reviews: Optional[List[Dict[str, Any]]] = None,
    fusion: Optional[Dict[str, Any]] = None,
    propagation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a CycleBrief from execute_cycle TOON fields."""
    toon = _safe_dict(toon)
    regime = _safe_dict(toon.get("intermarket_regime"))
    drivers = _safe_list(toon.get("macro_causal_context_regime") or toon.get("macro_causal_context"))
    feature = _safe_dict(toon.get("feature_snapshot"))
    technical = _safe_dict(toon.get("technical_analysis"))
    event_state = _safe_dict(toon.get("event_state"))
    opportunity = _safe_dict(toon.get("opportunity_bus"))
    macro_opportunity = _safe_dict(toon.get("macro_opportunity_bus"))
    account = _safe_dict(toon.get("account"))
    reviews = open_reviews if isinstance(open_reviews, list) else []
    fusion_payload = _safe_dict(fusion)
    prop = _safe_dict(propagation)

    top_drivers = []
    for edge in drivers[:8]:
        if not isinstance(edge, dict):
            continue
        top_drivers.append({
            "edge_id": edge.get("edge_id"),
            "relation": edge.get("relation"),
            "sign": edge.get("sign"),
            "confidence": edge.get("confidence"),
            "source_name": edge.get("source_name"),
            "target_name": edge.get("target_name"),
            "status": edge.get("status"),
        })

    brief = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "symbol": str(symbol or ""),
        "timeframe": str(timeframe or ""),
        "strategy": str(strategy or ""),
        "cycle_key": str(cycle_key or ""),
        "regime": {
            "regime_id": regime.get("regime_id", "UNKNOWN"),
            "confidence": regime.get("confidence", 0.0),
            "status": regime.get("status", "UNAVAILABLE"),
            "advisory_bias": regime.get("advisory_bias"),
        },
        "drivers": top_drivers,
        "working_edge": {
            "technical_bus": {
                "status": opportunity.get("status"),
                "candidate_ids": opportunity.get("candidate_ids") or [],
            },
            "macro_bus": {
                "status": macro_opportunity.get("status"),
                "candidate_ids": macro_opportunity.get("candidate_ids") or [],
            },
            "fusion": fusion_payload,
            "propagation": {
                "path_id": prop.get("path_id"),
                "bias": prop.get("bias"),
                "score": prop.get("score"),
                "horizon_seconds": prop.get("horizon_seconds"),
                "hops": prop.get("hops"),
            },
        },
        "technical": {
            "primary_trend": technical.get("primary_trend"),
            "confluence": technical.get("confluence"),
            "feature_ready": bool(feature.get("ready")),
            "completed_bar_time": feature.get("completed_bar_time"),
            "values_hash": feature.get("values_hash"),
        },
        "event": {
            "status": event_state.get("status") or event_state.get("state"),
            "blocked": bool(event_state.get("blocked") or event_state.get("in_blackout")),
            "reasons": event_state.get("reasons") or event_state.get("reason") or [],
            "high_impact": bool(event_state.get("high_impact") or event_state.get("high_impact_news")),
        },
        "risk": {
            "kill_switch_active": bool(kill_switch_active),
            "equity": account.get("equity"),
            "balance": account.get("balance"),
            "margin_free": account.get("margin_free"),
        },
        "open_review_tasks": [
            {
                "intent_key": item.get("intent_key"),
                "status": item.get("status"),
                "severity": item.get("severity"),
                "reason": item.get("reason") or item.get("last_reason"),
            }
            for item in reviews[:20]
            if isinstance(item, dict)
        ],
        "execution_authority": False,
        "limitation": "Observability narrative only; never grants order permission.",
    }
    return brief


def persist(store: Any, brief: Dict[str, Any], *, operations: Any = None, run_id: str = "") -> Dict[str, Any]:
    """Persist latest brief and optionally journal an agent event."""
    if not isinstance(brief, dict):
        raise ValueError("cycle brief must be an object")
    symbol = str(brief.get("symbol") or "UNKNOWN")
    strategy = str(brief.get("strategy") or "UNKNOWN")
    key = f"cycle_brief:{strategy}:{symbol}"
    if store is not None and hasattr(store, "set_state"):
        store.set_state(key, brief)
        store.set_state("last_cycle_brief", brief)
    if operations is not None and hasattr(operations, "agent_event"):
        try:
            operations.agent_event(
                "TechnicalNet", "monitor_market_health", "market_snapshot",
                "cycle_brief_recorded",
                {
                    "symbol": symbol,
                    "strategy": strategy,
                    "regime_id": (brief.get("regime") or {}).get("regime_id"),
                    "cycle_key": brief.get("cycle_key"),
                    "execution_authority": False,
                },
                run_id=run_id,
                correlation_id=str(brief.get("cycle_key") or symbol),
            )
        except Exception as exc:
            logger.debug("event emission failed: %s", exc)
    return {"persisted": True, "state_key": key, "execution_authority": False}


def load_latest(store: Any, *, symbol: str = "", strategy: str = "") -> Dict[str, Any]:
    """Load the latest brief for a strategy/symbol or the global last brief."""
    if store is None or not hasattr(store, "get_state"):
        return {"status": "UNAVAILABLE", "execution_authority": False}
    if symbol and strategy:
        brief = store.get_state(f"cycle_brief:{strategy}:{symbol}", {}) or {}
        if brief:
            return {**brief, "status": "OK"}
    brief = store.get_state("last_cycle_brief", {}) or {}
    if brief:
        return {**brief, "status": "OK"}
    return {"status": "EMPTY", "execution_authority": False}
