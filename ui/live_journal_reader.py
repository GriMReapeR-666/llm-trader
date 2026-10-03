"""Live journal reader for the OpenTUI command deck.

Reads directly from trading_state.db (WAL mode) to provide real-time
position, PnL, equity, and trade-history data without depending on the
trader process pushing updates to /api/internal/update.

Thread-safe: uses short WAL reads with connection-per-call so the main
loop never blocks on SQLite.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "trading_state.db",
)


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=2)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Open positions (from journal trades table)
# ---------------------------------------------------------------------------

def read_open_positions(db_path: str = _DEFAULT_DB) -> List[Dict[str, Any]]:
    """Return open trades from the journal as position dicts for the UI."""
    if not os.path.exists(db_path):
        return []
    try:
        with _connect(db_path) as con:
            rows = con.execute(
                "SELECT ticket, magic, strategy, symbol, side, volume, "
                "entry, stop_loss, take_profit, risk_amount, opened_at, "
                "setup_id, COALESCE(gross_pnl, 0) AS gross_pnl, "
                "COALESCE(commission, 0) AS commission, COALESCE(swap, 0) AS swap "
                "FROM trades WHERE closed_at IS NULL ORDER BY opened_at DESC"
            ).fetchall()
    except Exception:
        return []

    positions = []
    for row in rows:
        ticket = int(row["ticket"])
        # Fetch latest position observation for unrealized PnL
        obs = _latest_observation(con, ticket) if rows else {}
        positions.append({
            "ticket": ticket,
            "symbol": str(row["symbol"]),
            "side": str(row["side"]).upper(),
            "volume": float(row["volume"]),
            "entry": float(row["entry"]),
            "sl": float(row["stop_loss"] or 0),
            "tp": float(row["take_profit"] or 0),
            "pnl": float(obs.get("unrealized_pnl", 0) or row["gross_pnl"] or 0),
            "riskR": float(row["risk_amount"] or 0),
            "unrealizedR": float(obs.get("r_multiple", 0) or 0),
            "ageMin": _age_minutes(str(row["opened_at"])),
            "strategyId": str(row["strategy"]),
            "magic": int(row["magic"]),
            "setupId": str(row["setup_id"] or ""),
            "commission": float(row["commission"] or 0),
            "swap": float(row["swap"] or 0),
            "observedAt": str(obs.get("observed_at", "")),
            "markQuality": str(obs.get("mark_quality", "UNKNOWN")),
        })
    return positions


def _latest_observation(con: sqlite3.Connection, ticket: int) -> Dict[str, Any]:
    """Fetch the latest position observation for a given trade ticket."""
    try:
        row = con.execute(
            "SELECT price, unrealized_pnl, r_multiple, observed_at, "
            "mark_quality, spread, tick_age_ms "
            "FROM position_observations WHERE ticket=? "
            "ORDER BY observed_at DESC LIMIT 1",
            (ticket,),
        ).fetchone()
        if row:
            return dict(row)
    except Exception:
        pass
    return {}


def _age_minutes(opened_at: str) -> float:
    """Compute age in minutes from opened_at ISO timestamp."""
    try:
        dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Trade history (closed trades)
# ---------------------------------------------------------------------------

def read_trade_history(
    db_path: str = _DEFAULT_DB, limit: int = 50
) -> List[Dict[str, Any]]:
    """Return recent closed trades for the journal/history view."""
    if not os.path.exists(db_path):
        return []
    try:
        with _connect(db_path) as con:
            rows = con.execute(
                "SELECT ticket, strategy, symbol, side, volume, entry, "
                "stop_loss, take_profit, risk_amount, opened_at, closed_at, "
                "exit_price, pnl, exit_reason, "
                "COALESCE(gross_pnl, 0) AS gross_pnl, "
                "COALESCE(commission, 0) AS commission, "
                "COALESCE(swap, 0) AS swap "
                "FROM trades WHERE closed_at IS NOT NULL "
                "ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# PnL summary
# ---------------------------------------------------------------------------

def read_pnl_summary(db_path: str = _DEFAULT_DB) -> Dict[str, Any]:
    """Aggregate PnL stats from the journal for the snapshot."""
    if not os.path.exists(db_path):
        return {"total_pnl": 0, "open_pnl": 0, "closed_pnl": 0,
                "win_count": 0, "loss_count": 0, "win_rate": 0.0,
                "total_trades": 0, "open_risk": 0}
    try:
        with _connect(db_path) as con:
            # Open trades summary — use latest position observation for open PnL
            open_row = con.execute(
                "SELECT COUNT(*) AS cnt, "
                "COALESCE(SUM(risk_amount), 0) AS open_risk "
                "FROM trades WHERE closed_at IS NULL"
            ).fetchone()
            # Compute open PnL from latest observations per ticket
            open_obs_rows = con.execute(
                "SELECT t.ticket, "
                "COALESCE(o.unrealized_pnl, t.gross_pnl, 0) AS upnl "
                "FROM trades t LEFT JOIN (" 
                "  SELECT ticket, unrealized_pnl, "
                "  ROW_NUMBER() OVER (PARTITION BY ticket ORDER BY observed_at DESC) AS rn "
                "  FROM position_observations" 
                ") o ON t.ticket = o.ticket AND o.rn = 1 "
                "WHERE t.closed_at IS NULL"
            ).fetchall()
            open_pnl = sum(float(r["upnl"] or 0) for r in open_obs_rows)

            # Closed trades summary (today)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            closed_row = con.execute(
                "SELECT COUNT(*) AS cnt, "
                "COALESCE(SUM(pnl), 0) AS total_pnl, "
                "COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) AS wins "
                "FROM trades WHERE closed_at IS NOT NULL AND closed_at >= ?",
                (today,),
            ).fetchone()

            open_cnt = int(open_row["cnt"] or 0)
            open_risk = float(open_row["open_risk"] or 0)
            closed_cnt = int(closed_row["cnt"] or 0)
            total_pnl = float(closed_row["total_pnl"] or 0)
            wins = int(closed_row["wins"] or 0)
            win_rate = (wins / closed_cnt * 100) if closed_cnt > 0 else 0.0

            return {
                "total_pnl": round(total_pnl, 2),
                "open_pnl": round(open_pnl, 2),
                "closed_pnl": round(total_pnl, 2),
                "open_risk": round(open_risk, 4),
                "open_positions": open_cnt,
                "win_count": wins,
                "loss_count": closed_cnt - wins,
                "win_rate": round(win_rate, 1),
                "total_trades": closed_cnt,
            }
    except Exception:
        return {"total_pnl": 0, "open_pnl": 0, "closed_pnl": 0,
                "win_count": 0, "loss_count": 0, "win_rate": 0.0,
                "total_trades": 0, "open_risk": 0}


# ---------------------------------------------------------------------------
# Account state (from risk_state.json + journal)
# ---------------------------------------------------------------------------

def read_account_state(
    risk_path: str = "", db_path: str = _DEFAULT_DB
) -> Dict[str, Any]:
    """Read account state from risk_state.json + journal for the snapshot."""
    risk_data = {}
    if risk_path and os.path.exists(risk_path):
        try:
            with open(risk_path, "r", encoding="utf-8") as f:
                risk_data = json.load(f)
        except Exception:
            pass

    pnl = read_pnl_summary(db_path)
    positions = read_open_positions(db_path)

    balance = float(risk_data.get("balance", 0) or 0)
    equity = float(risk_data.get("equity", 0) or 0)
    if equity <= 0:
        equity = balance + pnl.get("open_pnl", 0)

    return {
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "drawdown_pct": round(float(risk_data.get("current_drawdown_pct", 0) or 0), 2),
        "heatUsedR": round(float(risk_data.get("heat_used_r", 0) or 0), 2),
        "heatCapR": round(float(risk_data.get("heat_cap_r", 6.0) or 6.0), 2),
        "openRiskPct": round(float(risk_data.get("open_risk_pct", 0) or 0), 2),
        "consecutiveLosses": int(risk_data.get("consecutive_losses", 0) or 0),
        "halted": bool(risk_data.get("halted", False)),
    }


# ---------------------------------------------------------------------------
# Cycle brief history (from journal lifecycle events)
# ---------------------------------------------------------------------------

def read_cycle_briefs(
    db_path: str = _DEFAULT_DB, limit: int = 10
) -> List[Dict[str, Any]]:
    """Read recent cycle decisions from lifecycle events for the stream view."""
    if not os.path.exists(db_path):
        return []
    try:
        with _connect(db_path) as con:
            rows = con.execute(
                "SELECT created_at, setup_key, event_type, actor, payload "
                "FROM trade_lifecycle_events "
                "WHERE event_type IN ('PROPOSED', 'VALIDATED', 'REJECTED', "
                "'FILLED', 'CLOSED') "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception:
        return []
    result = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}
        result.append({
            "ts": str(row["created_at"]),
            "eventType": str(row["event_type"]),
            "actor": str(row["actor"]),
            "symbol": payload.get("symbol", ""),
            "side": payload.get("side", ""),
            "strategy": payload.get("strategy", ""),
            "detail": payload,
        })
    return result


# ---------------------------------------------------------------------------
# Proposal audit trail
# ---------------------------------------------------------------------------

def read_proposals(
    db_path: str = _DEFAULT_DB, limit: int = 20
) -> List[Dict[str, Any]]:
    """Read recent proposals (approved + rejected) for the journal view."""
    if not os.path.exists(db_path):
        return []
    try:
        with _connect(db_path) as con:
            rows = con.execute(
                "SELECT id, created_at, setup_key, strategy, symbol, side, "
                "approved, reasons FROM proposals "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception:
        return []
    result = []
    for row in rows:
        try:
            reasons = json.loads(row["reasons"] or "[]")
        except Exception:
            reasons = []
        result.append({
            "id": int(row["id"]),
            "ts": str(row["created_at"]),
            "setupKey": str(row["setup_key"]),
            "strategy": str(row["strategy"]),
            "symbol": str(row["symbol"]),
            "side": str(row["side"]),
            "approved": bool(row["approved"]),
            "reasons": reasons,
        })
    return result


def read_latest_proposal(
    db_path: str = _DEFAULT_DB,
) -> Optional[Dict[str, Any]]:
    """Return the most recent proposal (approved or rejected), or None."""
    props = read_proposals(db_path, limit=1)
    return props[0] if props else None


# ---------------------------------------------------------------------------
# Market data quotes (from engine-cached state)
# ---------------------------------------------------------------------------

_MARKET_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "market_state.json",
)

_SCANNER_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scanner_state.json",
)


def read_market_quotes(state_path: str = _MARKET_STATE_PATH) -> Dict[str, Any]:
    """Read latest market quotes written by the engine.

    The engine writes market_state.json every cycle with live bid/ask from MT5.
    This avoids the API process needing its own MT5 connection.
    """
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        quotes = data.get("quotes", {})
        age_ms = (time.time() * 1000) - data.get("ts", 0)
        if age_ms > 30000:
            return {}
        return quotes
    except Exception:
        return {}


def read_scanner_results(state_path: str = _SCANNER_STATE_PATH) -> List[Dict[str, Any]]:
    """Read latest scanner results written by the engine.

    The engine writes scanner_state.json every scan cycle with scored opportunities.
    """
    if not os.path.exists(state_path):
        return []
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        age_ms = (time.time() * 1000) - data.get("ts", 0)
        if age_ms > 60000:
            return []
        return data.get("results", [])
    except Exception:
        return []


def read_rl_model_state(model_path: str = "") -> Dict[str, Any]:
    """Read RL model state for the TUI dashboard."""
    if not model_path:
        model_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "rl_model.json",
        )
    if not os.path.exists(model_path):
        return {"q_table_size": 0, "total_updates": 0, "exploration_rate": 0.2}
    try:
        with open(model_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        q_table = data.get("q_table", {})
        return {
            "q_table_size": len(q_table),
            "total_updates": data.get("total_updates", 0),
            "exploration_rate": data.get("epsilon", 0.2),
            "total_reward": data.get("total_reward", 0),
            "best_reward": data.get("best_reward", 0),
            "worst_reward": data.get("worst_reward", 0),
            "avg_reward_100": data.get("avg_reward_100", 0),
        }
    except Exception:
        return {"q_table_size": 0, "total_updates": 0, "exploration_rate": 0.2}


def read_compliance_events(db_path: str = _DEFAULT_DB, limit: int = 50) -> List[Dict[str, Any]]:
    """Read compliance audit events from the immutable audit log."""
    try:
        from core.compliance_audit import get_audit
        audit = get_audit()
        return audit.query(limit=limit)
    except Exception:
        return []
