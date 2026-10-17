"""Opportunity Scanner Bus — discovery without execution authority.

TechnicalNet (or MarketScanner) emits OpportunityCandidates. MarketModel may
attach narrative context. Only RiskController's deterministic validation path
may later convert a TradeProposal into an order intent — this bus cannot.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    from operations.agent_contracts import require, require_write
except ImportError:
    from operations.agent_contracts import require, require_write

try:
    from research.setup_registry import classify as classify_setup
except ImportError:
    from research.setup_registry import classify as classify_setup


STATE_KEY = "opportunity_bus:open"
HISTORY_KEY = "opportunity_bus:recent"
MAX_OPEN = 50
MAX_RECENT = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _lock_authority(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    out["execution_authority"] = False
    return out


def opportunity_from_scan_row(row: Dict[str, Any], *, timeframe: str = "") -> Dict[str, Any]:
    """Normalize a MarketScanner score row into a bus candidate."""
    if not isinstance(row, dict):
        raise ValueError("scan row must be an object")
    symbol = str(row.get("symbol") or "").upper()
    if not symbol:
        raise ValueError("scan row requires symbol")
    score = float(row.get("score") or 0.0)
    setup_hint = str(row.get("setup_id") or row.get("setup") or "unclassified_v1")
    side_hint = str(row.get("side") or row.get("bias") or "NONE").upper()
    return _lock_authority({
        "candidate_id": _candidate_id("scan", symbol, timeframe, setup_hint, round(score, 2), _now()[:13]),
        "source": "TechnicalNet.scan",
        "symbol": symbol,
        "timeframe": timeframe,
        "setup_id": setup_hint,
        "side_hint": side_hint if side_hint in {"BUY", "SELL", "NONE"} else "NONE",
        "score": score,
        "status": "OPEN",
        "features_ref": row.get("features_ref") or row.get("feature_snapshot_id"),
        "raw": {k: row[k] for k in row if k not in {"raw"}},
        "emitted_at": _now(),
        "agent": "TechnicalNet",
    })


def opportunity_from_features(
    features: Dict[str, Any],
    *,
    symbol: str,
    timeframe: str = "",
    session: str = "",
) -> Optional[Dict[str, Any]]:
    """Emit a candidate only when setup_registry classifies an eligible setup."""
    classified = classify_setup(features if isinstance(features, dict) else {}, session=session)
    if not classified.get("eligible"):
        return None
    setup_id = str(classified.get("setup_id") or "unclassified_v1")
    side_hint = "NONE"
    # Directional hints from alignment features when present.
    mtf = features.get("mtf_alignment")
    if mtf in (1, -1):
        side_hint = "BUY" if mtf == 1 else "SELL"
    return _lock_authority({
        "candidate_id": _candidate_id("features", symbol, timeframe, setup_id, features.get("completed_bar_time"), _now()[:13]),
        "source": "TechnicalNet.features",
        "symbol": str(symbol).upper(),
        "timeframe": timeframe,
        "setup_id": setup_id,
        "side_hint": side_hint,
        "score": float(features.get("adx") or 0.0),
        "status": "OPEN",
        "setup_reason": classified.get("reason"),
        "features_ref": features.get("feature_values_hash") or features.get("completed_bar_time"),
        "emitted_at": _now(),
        "agent": "TechnicalNet",
    })


_STAT_PASS_OR_BETTER = frozenset({
    "STAT_PASS", "REPLAY_PASS", "DEMO_MATCH", "TINY_LIVE", "FULL",
})
_APPROVED_STATUSES = frozenset({"APPROVED", "AUTO_APPROVED"})


def opportunity_from_stat_pass_edge(
    edge: Dict[str, Any],
    *,
    symbol: str,
    timeframe: str = "",
    regime_id: str = "",
    lifecycle: str = "",
) -> Optional[Dict[str, Any]]:
    """Build an advisory candidate from a STAT_PASS (or approved) macro edge.

    Never grants execution authority. Transmission-only targets are skipped.
    """
    if not isinstance(edge, dict):
        return None
    status = str(edge.get("status") or "").upper()
    stage = str(lifecycle or status).upper()
    try:
        from research.edge_lifecycle import normalize_lifecycle
        stage = normalize_lifecycle(stage)
    except Exception:
        stage = status
    if stage not in _STAT_PASS_OR_BETTER and status not in _APPROVED_STATUSES:
        return None

    try:
        from intelligence.causal_setup_map import resolve_causal_setup
    except ImportError:
        from intelligence.causal_setup_map import resolve_causal_setup

    mapped = resolve_causal_setup(
        relation=str(edge.get("relation") or ""),
        source_type=str(edge.get("source_type") or ""),
        target_type=str(edge.get("target_type") or ""),
        source_name=str(edge.get("source_name") or ""),
        target_name=str(edge.get("target_name") or ""),
    )
    if not mapped.get("eligible"):
        return None

    sign = str(edge.get("sign") or "UNKNOWN").upper()
    if sign == "POSITIVE":
        side_hint = "BUY"
    elif sign == "NEGATIVE":
        side_hint = "SELL"
    else:
        side_hint = "NONE"
    setup_id = str(mapped.get("setup_id") or "unclassified_v1")
    edge_id = edge.get("edge_id")
    confidence = float(edge.get("confidence") or 0.0)
    return _lock_authority({
        "candidate_id": _candidate_id(
            "macro_stat_pass", symbol, timeframe, setup_id, edge_id, regime_id, _now()[:13],
        ),
        "source": "MacroAnalyst.stat_pass_edge",
        "symbol": str(symbol).upper(),
        "timeframe": timeframe,
        "setup_id": setup_id,
        "side_hint": side_hint,
        "score": confidence,
        "status": "OPEN",
        "edge_id": edge_id,
        "lifecycle": stage,
        "edge_status": status,
        "regime_id": regime_id or "",
        "causal_chain": (
            f"{edge.get('source_name')} --({edge.get('relation')})--> {edge.get('target_name')}"
        ),
        "setup_reason": mapped.get("reason"),
        "emitted_at": _now(),
        "agent": "MacroAnalyst",
    })


def publish_from_stat_pass_edges(
    store: Any,
    edges: Iterable[Dict[str, Any]],
    *,
    symbol: str,
    timeframe: str = "",
    regime_id: str = "",
    lifecycle_by_edge: Optional[Dict[Any, str]] = None,
    agent: str = "MacroAnalyst",
) -> Dict[str, Any]:
    """Publish opportunity-bus candidates only from STAT_PASS/approved macro edges."""
    lifecycle_by_edge = lifecycle_by_edge or {}
    candidates: List[Dict[str, Any]] = []
    skipped = 0
    for edge in edges or []:
        if not isinstance(edge, dict):
            skipped += 1
            continue
        edge_id = edge.get("edge_id")
        candidate = opportunity_from_stat_pass_edge(
            edge,
            symbol=symbol,
            timeframe=timeframe,
            regime_id=regime_id,
            lifecycle=str(lifecycle_by_edge.get(edge_id) or edge.get("lifecycle") or edge.get("status") or ""),
        )
        if candidate is None:
            skipped += 1
            continue
        candidates.append(candidate)
    published = publish(store, candidates, agent=agent) if candidates else _lock_authority({
        "status": "EMPTY",
        "published": 0,
        "open_count": len(load_open(store)),
        "candidate_ids": [],
        "agent": agent,
    })
    published["skipped"] = skipped
    published["source_filter"] = "STAT_PASS_OR_APPROVED"
    return published


def emit_candidates(
    candidates: Sequence[Dict[str, Any]],
    *,
    agent: str = "TechnicalNet",
) -> List[Dict[str, Any]]:
    """Validate contract + authority lock before publishing."""
    require(agent, "emit_opportunity")
    require_write(agent, "opportunity_candidate")
    locked: List[Dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        payload = _lock_authority(dict(item))
        payload["agent"] = agent
        payload.setdefault("status", "OPEN")
        payload.setdefault("emitted_at", _now())
        if not payload.get("candidate_id"):
            payload["candidate_id"] = _candidate_id(payload.get("symbol"), payload.get("setup_id"), payload["emitted_at"])
        locked.append(payload)
    return locked


def publish(store: Any, candidates: Sequence[Dict[str, Any]], *, agent: str = "TechnicalNet") -> Dict[str, Any]:
    """Persist open candidates on the trading store (advisory bus)."""
    locked = emit_candidates(candidates, agent=agent)
    existing = []
    try:
        existing = list(store.get_state(STATE_KEY, []) or [])
    except Exception:
        existing = []
    by_id = {str(item.get("candidate_id")): item for item in existing if isinstance(item, dict)}
    for item in locked:
        by_id[str(item["candidate_id"])] = item
    open_list = list(by_id.values())[-MAX_OPEN:]
    recent = []
    try:
        recent = list(store.get_state(HISTORY_KEY, []) or [])
    except Exception:
        recent = []
    recent = (recent + locked)[-MAX_RECENT:]
    store.set_state(STATE_KEY, open_list)
    store.set_state(HISTORY_KEY, recent)
    return _lock_authority({
        "status": "PUBLISHED",
        "published": len(locked),
        "open_count": len(open_list),
        "candidate_ids": [item["candidate_id"] for item in locked],
        "agent": agent,
    })


def publish_from_scan(
    store: Any,
    scan_rows: Iterable[Dict[str, Any]],
    *,
    timeframe: str = "",
    agent: str = "TechnicalNet",
) -> Dict[str, Any]:
    candidates = []
    for row in scan_rows or []:
        try:
            candidates.append(opportunity_from_scan_row(row, timeframe=timeframe))
        except (TypeError, ValueError):
            continue
    return publish(store, candidates, agent=agent)


def load_open(store: Any) -> List[Dict[str, Any]]:
    try:
        rows = list(store.get_state(STATE_KEY, []) or [])
    except Exception:
        return []
    return [_lock_authority(dict(item)) for item in rows if isinstance(item, dict)]


def narrate_candidate(
    candidate: Dict[str, Any],
    narrative: str,
    *,
    agent: str = "MarketModel",
) -> Dict[str, Any]:
    """Attach LLM/narrative context without changing permission bits."""
    require(agent, "propose_trade")
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    out = _lock_authority(dict(candidate))
    out["narrative"] = str(narrative or "")[:4000]
    out["narrated_by"] = agent
    out["narrated_at"] = _now()
    out["status"] = "NARRATED"
    return out


def handoff_for_validation(
    store: Any,
    candidate_id: str,
    *,
    agent: str = "RiskController",
) -> Dict[str, Any]:
    """Mark a candidate as pending deterministic validation.

    This does **not** create an order intent. RiskController must still run
    ``_validate_proposal`` / arbiter / intent submission separately.
    """
    require(agent, "validate_proposal")
    open_list = load_open(store)
    found = None
    remaining = []
    for item in open_list:
        if str(item.get("candidate_id")) == str(candidate_id):
            found = _lock_authority(dict(item))
            found["status"] = "PENDING_VALIDATION"
            found["handed_off_at"] = _now()
            found["handed_off_by"] = agent
        else:
            remaining.append(item)
    if found is None:
        return _lock_authority({
            "status": "NOT_FOUND",
            "candidate_id": candidate_id,
            "reason": "opportunity candidate not in open bus",
        })
    remaining.append(found)
    store.set_state(STATE_KEY, remaining[-MAX_OPEN:])
    return _lock_authority({
        "status": "PENDING_VALIDATION",
        "candidate": found,
        "note": "Handoff recorded; order intent still requires full RiskController gate.",
    })


def bus_snapshot(store: Any) -> Dict[str, Any]:
    open_list = load_open(store)
    return _lock_authority({
        "open_count": len(open_list),
        "open": open_list,
        "state_key": STATE_KEY,
    })


class BarSynchronizedOpportunityBus:
    """Synchronized Bar Boundary Opportunity Auction Bus.

    Coordinates discrete bar boundary evaluations across registered strategy plugins.
    Implements institutional Dual-Velocity routing:
      - Fast Lane: SCALP opportunities bypass deliberative graph subgraphs and route
        straight to core.execution_planning for immediate limit/stop entry calculation.
      - Deep Reasoning Lane: SWING / LONG_TERM opportunities route through
        intelligence.graph_engine_facade.TradingGraphEngine for multi-perspective
        deliberation, narrative synthesis, and causal validation.
    """

    def __init__(self, store: Any = None, graph_engine: Any = None):
        self.store = store
        self.graph_engine = graph_engine
        self._last_bar_times: Dict[str, Any] = {}

    def is_new_bar(self, symbol: str, timeframe: str, bar_time: Any) -> bool:
        """Determines if a new bar has closed since the last evaluation."""
        key = f"{symbol}:{timeframe}"
        if bar_time is not None and bar_time != self._last_bar_times.get(key):
            self._last_bar_times[key] = bar_time
            return True
        return False

    def run_synchronized_bar_auction(
        self,
        symbol: str,
        timeframe: str = "M15",
        bar_time: Any = None,
        market_data: Any = None,
        context: Optional[Dict[str, Any]] = None,
        strategies: Optional[Dict[str, Any]] = None,
        graph_engine: Optional[Any] = None,
        quote_bid: Optional[float] = None,
        quote_ask: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute the bar boundary opportunity auction across active strategies.

        Routes SCALP winners to core.execution_planning (Fast Lane) and
        SWING/LONG_TERM winners to intelligence.graph_engine_facade (Deliberative Lane).
        """
        import time
        from strategies.base import StrategySignal

        ctx = dict(context or {})
        ctx["symbol"] = symbol
        ctx["timeframe"] = timeframe

        # 1. Resolve current market prices
        price = float(ctx.get("price", 0.0) or 0.0)
        if price <= 0.0 and market_data is not None and hasattr(market_data, "get_latest_price"):
            try:
                p = market_data.get_latest_price(symbol)
                if isinstance(p, (int, float)) and p > 0:
                    price = float(p)
                    ctx["price"] = price
            except Exception:
                pass

        digits = int(ctx.get("digits", 5) or 5)
        point = float(ctx.get("point", 0.0001) or 0.0001)

        bid = float(quote_bid if quote_bid is not None else (ctx.get("bid") or price))
        ask = float(quote_ask if quote_ask is not None else (ctx.get("ask") or (price + 1.5 * point)))

        # 2. Discover strategies if not injected
        if strategies is None:
            try:
                from strategies.loader import StrategyLoader
                strategies = StrategyLoader().discover()
            except Exception:
                strategies = {}

        candidates: List[Dict[str, Any]] = []
        scalp_winners: List[Dict[str, Any]] = []
        swing_winners: List[Dict[str, Any]] = []
        fast_lane_plans: List[Dict[str, Any]] = []
        graph_lane_results: List[Dict[str, Any]] = []

        # 3. Evaluate candidate strategies
        for s_name, strat in (strategies or {}).items():
            try:
                strat_symbols = getattr(strat, "symbols", [])
                if strat_symbols and symbol not in strat_symbols:
                    continue

                sig: StrategySignal = strat.evaluate(ctx)
                if not isinstance(sig, StrategySignal):
                    continue

                if sig.side not in {"BUY", "SELL"} or sig.confidence <= 0.0:
                    continue

                style = getattr(strat, "trading_style", None) or sig.metadata.get("trading_style") or "SWING"
                score_type = getattr(strat, "score_type", None) or sig.metadata.get("score_type") or "CONFIDENCE"

                # Standardize as opportunity bus candidate
                cand_payload = {
                    "candidate_id": _candidate_id("auction", symbol, timeframe, s_name, round(sig.confidence, 3), _now()),
                    "source": f"Auction.{s_name}",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "setup_id": s_name,
                    "side_hint": sig.side,
                    "score": sig.confidence,
                    "status": "OPEN",
                    "features_ref": sig.rationale,
                    "trading_style": style,
                    "score_type": score_type,
                    "signal": sig.to_dict(),
                    "emitted_at": _now(),
                    "agent": "TechnicalNet",
                }
                candidates.append(_lock_authority(cand_payload))

                # 4. Route according to TradingStyle
                if style == "SCALP":
                    scalp_winners.append(cand_payload)
                    # Route to Fast Lane via execution_planning
                    try:
                        from core.execution_planning import build_pending_entry
                        entry_price = float(sig.entry or (bid if sig.side == "BUY" else ask))
                        tolerance = float(2.0 * point)
                        max_spread = float(max(2.5 * point, abs(ask - bid) * 1.5))
                        msc = int(time.time() * 1000)

                        planned = build_pending_entry(
                            symbol=symbol,
                            side=sig.side,
                            entry=entry_price,
                            bid=bid,
                            ask=ask,
                            quote_time_msc=msc,
                            entry_tolerance=tolerance,
                            min_entry_distance=0.0,
                            max_spread=max_spread,
                            expires_seconds=300,
                        )
                        fast_lane_plans.append({
                            "candidate_id": cand_payload["candidate_id"],
                            "strategy": s_name,
                            "planned_entry": planned.as_dict(),
                        })
                    except Exception as plan_err:
                        fast_lane_plans.append({
                            "candidate_id": cand_payload["candidate_id"],
                            "strategy": s_name,
                            "error": str(plan_err),
                        })
                else:
                    # SWING or LONG_TERM -> Deep Reasoning Lane
                    swing_winners.append(cand_payload)
                    engine = graph_engine or self.graph_engine
                    if engine is not None and hasattr(engine, "run_deliberative_cycle"):
                        try:
                            g_res = engine.run_deliberative_cycle(
                                symbol=symbol,
                                timeframe=timeframe,
                                strategy=s_name,
                                market_snapshot=ctx,
                            )
                            graph_lane_results.append({
                                "candidate_id": cand_payload["candidate_id"],
                                "strategy": s_name,
                                "graph_result": getattr(g_res, "final_decision", "EVALUATED"),
                            })
                        except Exception as g_err:
                            graph_lane_results.append({
                                "candidate_id": cand_payload["candidate_id"],
                                "strategy": s_name,
                                "graph_error": str(g_err),
                            })
            except Exception:
                continue

        # 5. Persist candidates to opportunity bus store if provided
        if self.store is not None and candidates:
            try:
                publish(self.store, candidates, agent="TechnicalNet")
            except Exception:
                pass

        return _lock_authority({
            "status": "AUCTION_COMPLETE",
            "symbol": symbol,
            "timeframe": timeframe,
            "bar_time": bar_time,
            "total_strategies": len(strategies or {}),
            "candidates_count": len(candidates),
            "scalp_winners": scalp_winners,
            "swing_winners": swing_winners,
            "fast_lane_plans": fast_lane_plans,
            "graph_lane_results": graph_lane_results,
            "auction_ts": _now(),
        })
