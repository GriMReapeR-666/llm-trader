"""Read-only Brain cockpit payloads: memory, regime, causal edges, macro observations.

All surfaces are advisory and never grant execution authority.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:
    from intelligence.economic_dynamics_catalog import coverage_report
except ImportError:
    from intelligence.economic_dynamics_catalog import coverage_report

try:
    from intelligence.macro_graph import MacroGraph
except ImportError:
    from intelligence.macro_graph import MacroGraph

try:
    from intelligence.macro_observations import recent as recent_observations
except ImportError:
    from intelligence.macro_observations import recent as recent_observations

try:
    from intelligence.trading_memory import TradingMemory
except ImportError:
    from intelligence.trading_memory import TradingMemory

try:
    from core.trading_safety import TradingStore
except ImportError:
    from core.trading_safety import TradingStore

try:
    from intelligence.cycle_brief import load_latest
except ImportError:
    from intelligence.cycle_brief import load_latest


def _journal_path() -> str:
    return os.getenv("LLM_TRADER_JOURNAL_PATH", "trading_state.db")


def memory_health(*, journal_path: str = "", limit: int = 20) -> Dict[str, Any]:
    path = journal_path or _journal_path()
    memory = TradingMemory(path)
    quality = memory.memory_quality_metrics()
    queue = memory.candidate_review_queue(limit=limit)
    approved_lessons_preview = memory.format_approved_lessons(limit=3)
    return {
        "status": "OK",
        "memory_quality": {**quality, "execution_authority": False},
        "review_queue": queue,
        "approved_lessons_preview": approved_lessons_preview[:500],
        "execution_authority": False,
    }


def regime_snapshot(*, journal_path: str = "", symbol: str = "", strategy: str = "") -> Dict[str, Any]:
    path = journal_path or _journal_path()
    store = TradingStore(path)
    brief = load_latest(store, symbol=symbol, strategy=strategy)
    regime = brief.get("regime") if isinstance(brief.get("regime"), dict) else {}
    fusion = brief.get("fusion") if isinstance(brief.get("fusion"), dict) else {}
    propagation = brief.get("propagation") if isinstance(brief.get("propagation"), dict) else {}
    improvement = store.get_state("last_self_improvement", {}) or {}
    return {
        "status": brief.get("status", "EMPTY"),
        "regime": regime,
        "fusion": fusion,
        "propagation": {
            k: propagation.get(k)
            for k in ("status", "path_id", "bias", "score", "horizon_seconds", "execution_authority")
        } if propagation else {},
        "cycle_key": brief.get("cycle_key"),
        "symbol": brief.get("symbol") or symbol,
        "strategy": brief.get("strategy") or strategy,
        "captured_at": brief.get("captured_at"),
        "self_improvement_at": improvement.get("captured_at") if isinstance(improvement, dict) else None,
        "execution_authority": False,
    }


def top_causal_edges(
    *,
    journal_path: str = "",
    entity_names: Optional[List[str]] = None,
    limit: int = 12,
) -> Dict[str, Any]:
    path = journal_path or _journal_path()
    graph = MacroGraph(path)
    seeds = entity_names or ["GOLD", "DXY", "POLICY_RATE", "VIX", "EUR", "USD"]
    edges = graph.approved_edges(seeds, limit=limit)
    validate_state = TradingStore(path).get_state("last_causal_edge_validate", {}) or {}
    rows: List[Dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        rows.append({
            "edge_id": edge.get("edge_id"),
            "source_name": edge.get("source_name"),
            "target_name": edge.get("target_name"),
            "relation": edge.get("relation"),
            "sign": edge.get("sign"),
            "confidence": edge.get("confidence"),
            "status": edge.get("status"),
        })
    return {
        "status": "OK" if rows else "EMPTY",
        "count": len(rows),
        "edges": rows,
        "last_validate": {
            k: validate_state.get(k)
            for k in (
                "captured_at", "validated_count", "approved_count",
                "replay_pass_count", "execution_authority",
            )
        } if isinstance(validate_state, dict) else {},
        "execution_authority": False,
    }


def macro_observations_summary(*, journal_path: str = "", limit: int = 15) -> Dict[str, Any]:
    path = journal_path or _journal_path()
    payload = recent_observations(path, limit=limit)
    entities = sorted({
        str(row.get("entity") or "")
        for row in (payload.get("items") or [])
        if isinstance(row, dict) and row.get("entity")
    })
    return {
        "status": payload.get("status", "OK"),
        "count": payload.get("count", 0),
        "entities_seen": entities[:20],
        "items": [
            {
                "entity": row.get("entity"),
                "provider": row.get("provider"),
                "series": row.get("series"),
                "value": row.get("value"),
                "surprise": row.get("surprise"),
                "captured_at": row.get("captured_at"),
            }
            for row in (payload.get("items") or [])
            if isinstance(row, dict)
        ][:limit],
        "execution_authority": False,
    }


def economic_dynamics_health() -> Dict[str, Any]:
    report = coverage_report()
    central_bank_aliases = []
    try:
        from intelligence.economic_dynamics_catalog import ENTITIES
        for entity in ENTITIES:
            if str(entity.get("name")) == "CENTRAL_BANK":
                central_bank_aliases = list(entity.get("aliases") or [])
                break
    except Exception:
        central_bank_aliases = []
    return {
        "status": report.get("status", "UNKNOWN"),
        "coverage": report,
        "central_bank_aliases": central_bank_aliases,
        "execution_authority": False,
        "limitation": (
            "Machine SoT covers critical doc chains and free-data bindings; "
            "the prose dump is not executable truth."
        ),
    }


def realtime_news_summary(*, journal_path: str = "", symbol: str = "") -> Dict[str, Any]:
    """Latest governed realtime news ingest payload (advisory only)."""
    path = journal_path or _journal_path()
    store = TradingStore(path)
    payload = store.get_state("last_realtime_news_ingest", {}) or {}
    if symbol:
        symbol_state = store.get_state(f"event_monitor:{symbol}", {}) or {}
        if isinstance(symbol_state, dict) and symbol_state.get("realtime_news_ingest"):
            payload = symbol_state.get("realtime_news_ingest") or payload
    ingest = payload.get("ingest") if isinstance(payload.get("ingest"), dict) else {}
    extraction = payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {}
    return {
        "status": payload.get("status", "EMPTY"),
        "captured_at": payload.get("captured_at"),
        "article_count": payload.get("article_count", 0),
        "ingested": ingest.get("ingested", 0),
        "skipped": ingest.get("skipped", 0),
        "classified_count": len(ingest.get("classified") or []),
        "macro_entities_extra": list(payload.get("macro_entities_extra") or []),
        "extraction_status": extraction.get("status"),
        "candidate_edge_ids": extraction.get("edge_ids") or [],
        "execution_authority": False,
        "limitation": (
            "Realtime news is candidate research evidence only; "
            "calendar releases remain the sole event-reaction measurement path."
        ),
    }


def full_observability(*, journal_path: str = "", symbol: str = "", strategy: str = "") -> Dict[str, Any]:
    path = journal_path or _journal_path()
    return {
        "status": "OK",
        "memory": memory_health(journal_path=path),
        "regime": regime_snapshot(journal_path=path, symbol=symbol, strategy=strategy),
        "causal_edges": top_causal_edges(journal_path=path),
        "macro_observations": macro_observations_summary(journal_path=path),
        "economic_dynamics": economic_dynamics_health(),
        "realtime_news": realtime_news_summary(journal_path=path, symbol=symbol),
        "execution_authority": False,
    }
