"""Append-only reward ledger and terminal R_net computation (Phase A/B).

Rewards are research/learning signals only. Emitting a reward event never
grants ``execution_authority`` and never places an order.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import sqlite3
from typing import Any, Dict, List, Optional


R_CLIP = 5.0
PROCESS_CAP_FRACTION = 0.15
COST_BUFFER_R_DEFAULT = 0.0

# Attribution shares of |R_net| for offline credit (informational; RL uses full terminal).
CREDIT_SHARES = {
    "MacroAnalyst": 0.15,
    "CausalValidator": 0.10,
    "TechnicalNet": 0.15,
    "MarketModel": 0.35,
    "RiskController": 0.15,
    "QuantitativeCoach": 0.10,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def ensure_reward_schema(path: str) -> None:
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS reward_events (
              id INTEGER PRIMARY KEY,
              created_at TEXT NOT NULL,
              layer TEXT NOT NULL,
              agent TEXT NOT NULL,
              action TEXT NOT NULL,
              value REAL NOT NULL,
              ticket INTEGER,
              edge_id INTEGER,
              candidate_id TEXT,
              run_id TEXT,
              correlation_id TEXT,
              payload TEXT NOT NULL,
              execution_authority INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_reward_events_ticket
              ON reward_events(ticket, created_at);
            CREATE INDEX IF NOT EXISTS idx_reward_events_layer
              ON reward_events(layer, created_at);
            CREATE TRIGGER IF NOT EXISTS reward_events_immutable_update
              BEFORE UPDATE ON reward_events BEGIN
                SELECT RAISE(ABORT, 'reward events are append-only');
              END;
            CREATE TRIGGER IF NOT EXISTS reward_events_immutable_delete
              BEFORE DELETE ON reward_events BEGIN
                SELECT RAISE(ABORT, 'reward events are append-only');
              END;
            """
        )


def emit_reward_event(
    path: str,
    *,
    layer: str,
    agent: str,
    action: str,
    value: float,
    ticket: Optional[int] = None,
    edge_id: Optional[int] = None,
    candidate_id: Optional[str] = None,
    run_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append one reward event. Always ``execution_authority=False``."""
    if not _finite(value):
        raise ValueError("reward value must be finite")
    layer_key = str(layer or "").strip().upper()
    agent_key = str(agent or "").strip()
    action_key = str(action or "").strip()
    if layer_key not in {"L0", "L1", "L2", "L3", "PROCESS", "INTERMEDIATE", "TERMINAL", "SUSTAINABILITY"}:
        raise ValueError("invalid reward layer")
    if not agent_key or not action_key:
        raise ValueError("agent and action are required")

    # Normalize aliases
    layer_norm = {
        "PROCESS": "L0",
        "INTERMEDIATE": "L1",
        "TERMINAL": "L2",
        "SUSTAINABILITY": "L3",
    }.get(layer_key, layer_key)

    ensure_reward_schema(path)
    body = dict(payload or {})
    body["execution_authority"] = False
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    created = _now()
    with sqlite3.connect(path) as con:
        cur = con.execute(
            """INSERT INTO reward_events(
                created_at,layer,agent,action,value,ticket,edge_id,candidate_id,
                run_id,correlation_id,payload,execution_authority
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)""",
            (
                created,
                layer_norm,
                agent_key[:120],
                action_key[:120],
                float(value),
                int(ticket) if ticket is not None else None,
                int(edge_id) if edge_id is not None else None,
                str(candidate_id)[:120] if candidate_id else None,
                str(run_id)[:120] if run_id else None,
                str(correlation_id)[:200] if correlation_id else None,
                encoded,
            ),
        )
        event_id = int(cur.lastrowid)
    return {
        "id": event_id,
        "created_at": created,
        "layer": layer_norm,
        "agent": agent_key,
        "action": action_key,
        "value": float(value),
        "ticket": ticket,
        "execution_authority": False,
    }


def compute_terminal_reward(
    trade_result: Dict[str, Any],
    *,
    r_clip: float = R_CLIP,
    process_cap_fraction: float = PROCESS_CAP_FRACTION,
    cost_buffer_r: float = COST_BUFFER_R_DEFAULT,
) -> Dict[str, Any]:
    """Compute clipped cost-aware terminal reward from a reconciled close.

    Primary signal is ``R_net``. Bounded process terms cannot overwhelm a −1R stop.
    """
    if not isinstance(trade_result, dict):
        raise ValueError("trade_result must be an object")
    if not trade_result.get("broker_reconciled", False):
        return {
            "status": "BLOCKED",
            "reason": "close is not broker-reconciled",
            "reward": 0.0,
            "r_net": None,
            "execution_authority": False,
        }

    try:
        pnl = float(trade_result.get("pnl", 0.0) or 0.0)
        risk = float(trade_result.get("risk") or trade_result.get("risk_amount") or 0.0)
        commission = float(trade_result.get("commission") or 0.0)
        swap = float(trade_result.get("swap") or 0.0)
        financing = float(trade_result.get("financing") or 0.0)
        buffer = float(cost_buffer_r)
    except (TypeError, ValueError, OverflowError):
        return {
            "status": "INVALID",
            "reason": "non-finite trade economics",
            "reward": 0.0,
            "r_net": None,
            "execution_authority": False,
        }

    if not all(math.isfinite(v) for v in (pnl, risk, commission, swap, financing, buffer)):
        return {
            "status": "INVALID",
            "reason": "non-finite trade economics",
            "reward": 0.0,
            "r_net": None,
            "execution_authority": False,
        }
    if risk <= 0:
        return {
            "status": "INVALID",
            "reason": "risk_amount must be positive",
            "reward": 0.0,
            "r_net": None,
            "execution_authority": False,
        }

    explicit = trade_result.get("r_multiple")
    if explicit is not None and _finite(explicit):
        r_gross = float(explicit)
    else:
        # Net of explicit broker costs when present; pnl is already often net.
        costs = max(0.0, commission) + max(0.0, swap) + max(0.0, financing)
        # If pnl already net, do not double-subtract when costs were applied upstream.
        # Prefer provided net pnl; apply only cost_buffer_r as research haircut.
        r_gross = pnl / risk
        if trade_result.get("pnl_is_gross"):
            r_gross = (pnl - costs) / risk
    r_net = r_gross - max(0.0, buffer)
    r_clipped = max(-float(r_clip), min(float(r_clip), r_net))

    # Bounded process terms (cannot dominate a full stop-out).
    process = 0.0
    process_parts: Dict[str, float] = {}
    confluence = trade_result.get("confluence_score", 0)
    try:
        confluence_i = int(confluence or 0)
    except (TypeError, ValueError):
        confluence_i = 0
    if confluence_i >= 6:
        part = 0.08 if r_net > 0 else -0.05
        process += part
        process_parts["confluence"] = part
    if trade_result.get("news_aligned") and r_net > 0:
        process += 0.05
        process_parts["news_aligned"] = 0.05
    try:
        max_dd = float(trade_result.get("max_drawdown") or 0.0)
    except (TypeError, ValueError):
        max_dd = 0.0
    if math.isfinite(max_dd) and max_dd > 0.02:
        process -= 0.05
        process_parts["drawdown"] = -0.05

    cap = process_cap_fraction * max(1.0, abs(r_clipped))
    process_capped = max(-cap, min(cap, process))
    reward = r_clipped + process_capped

    return {
        "status": "OK",
        "reward": float(reward),
        "r_gross": float(r_gross),
        "r_net": float(r_net),
        "r_clipped": float(r_clipped),
        "process": float(process_capped),
        "process_parts": process_parts,
        "process_cap": float(cap),
        "cost_buffer_r": float(buffer),
        "risk": float(risk),
        "pnl": float(pnl),
        "execution_authority": False,
        "kind": "terminal_trade_reward",
    }


def emit_terminal_trade_rewards(
    path: str,
    trade_result: Dict[str, Any],
    breakdown: Optional[Dict[str, Any]] = None,
    *,
    run_id: Optional[str] = None,
    emit_credit_shares: bool = True,
    lifecycle_lookup: Optional[Any] = None,
) -> Dict[str, Any]:
    """Persist L2 terminal reward (+ optional attribution rows) for a closed trade.

    ``lifecycle_lookup(edge_id) -> stage`` optionally gates CausalValidator /
    MacroAnalyst credit at lifecycle ≥ REPLAY_PASS (Phase C).
    """
    breakdown = breakdown or compute_terminal_reward(trade_result)
    if breakdown.get("status") != "OK":
        return {**breakdown, "events": [], "execution_authority": False}

    ticket = trade_result.get("ticket")
    try:
        ticket_i = int(ticket) if ticket is not None else None
    except (TypeError, ValueError):
        ticket_i = None

    from reward_attribution import causal_credit_shares, eligible_edge_ids

    raw_edges = trade_result.get("causal_edge_ids") or []
    if not isinstance(raw_edges, list):
        raw_edges = []
    edge_ids = eligible_edge_ids(raw_edges, lifecycle_lookup=lifecycle_lookup)
    all_linked = eligible_edge_ids(raw_edges, lifecycle_lookup=None)
    candidate_id = trade_result.get("candidate_id") or trade_result.get("opportunity_candidate_id")

    events: List[Dict[str, Any]] = []
    events.append(
        emit_reward_event(
            path,
            layer="L2",
            agent="MarketModel",
            action="terminal_trade_close",
            value=float(breakdown["reward"]),
            ticket=ticket_i,
            edge_id=int(all_linked[0]) if all_linked else None,
            candidate_id=str(candidate_id) if candidate_id else None,
            payload={
                "breakdown": breakdown,
                "rl_action": trade_result.get("rl_action") or trade_result.get("action"),
                "broker_reconciled": True,
                "causal_edge_ids": all_linked,
                "eligible_causal_edge_ids": edge_ids,
            },
            run_id=run_id,
            correlation_id=f"ticket:{ticket_i}" if ticket_i is not None else None,
        )
    )

    if emit_credit_shares:
        r_net = float(breakdown.get("r_net") or 0.0)
        for agent, share in CREDIT_SHARES.items():
            if agent == "CausalValidator":
                for row in causal_credit_shares(r_net, edge_ids, agent_share=share):
                    events.append(
                        emit_reward_event(
                            path,
                            layer="L2",
                            agent=agent,
                            action="causal_edge_credit",
                            value=float(row["value"]),
                            ticket=ticket_i,
                            edge_id=int(row["edge_id"]),
                            candidate_id=None,
                            run_id=run_id,
                            correlation_id=(
                                f"ticket:{ticket_i}:credit:{agent}:edge:{row['edge_id']}"
                                if ticket_i is not None
                                else None
                            ),
                            payload={
                                "share": row["share"],
                                "r_net": r_net,
                                "lifecycle_floor": "REPLAY_PASS",
                                "note": "attribution only; not added again to RL scalar",
                            },
                        )
                    )
                continue
            if agent == "MacroAnalyst":
                if not edge_ids:
                    continue
                credit = share * r_net
                events.append(
                    emit_reward_event(
                        path,
                        layer="L2",
                        agent=agent,
                        action="terminal_credit_share",
                        value=float(credit),
                        ticket=ticket_i,
                        edge_id=int(edge_ids[0]),
                        run_id=run_id,
                        correlation_id=f"ticket:{ticket_i}:credit:{agent}" if ticket_i is not None else None,
                        payload={
                            "share": share,
                            "r_net": r_net,
                            "eligible_edges": edge_ids,
                            "note": "attribution only; not added again to RL scalar",
                        },
                    )
                )
                continue
            if agent == "TechnicalNet" and not candidate_id:
                continue
            credit = share * r_net
            events.append(
                emit_reward_event(
                    path,
                    layer="L2",
                    agent=agent,
                    action="terminal_credit_share",
                    value=float(credit),
                    ticket=ticket_i,
                    edge_id=None,
                    candidate_id=str(candidate_id) if candidate_id and agent == "TechnicalNet" else None,
                    run_id=run_id,
                    correlation_id=f"ticket:{ticket_i}:credit:{agent}" if ticket_i is not None else None,
                    payload={"share": share, "r_net": r_net, "note": "attribution only; not added again to RL scalar"},
                )
            )

    return {
        "status": "RECORDED",
        "reward": float(breakdown["reward"]),
        "events": events,
        "breakdown": breakdown,
        "eligible_causal_edge_ids": edge_ids,
        "execution_authority": False,
    }


def list_reward_events(
    path: str,
    *,
    ticket: Optional[int] = None,
    layer: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    import os

    if not os.path.exists(path):
        return []
    ensure_reward_schema(path)
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT * FROM reward_events
               WHERE (? IS NULL OR ticket=?)
                 AND (? IS NULL OR layer=?)
               ORDER BY id DESC LIMIT ?""",
            (
                ticket,
                ticket,
                str(layer).upper() if layer else None,
                str(layer).upper() if layer else None,
                max(0, int(limit)),
            ),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.get("payload") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["payload"] = {}
        item["execution_authority"] = False
        out.append(item)
    return out
