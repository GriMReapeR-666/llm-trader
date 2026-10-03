"""Analytics rollups for the command deck (master-plan P7).

Pure functions over the journal database + reward artifacts; no MT5 imports,
no orchestrator dependency, safe to call from the API process.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _reward_history(rewards_path: str) -> List[Dict[str, Any]]:
    """PerformanceMonitor artifact: list of trade records if present."""
    data = _read_json(rewards_path, None)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("history", "trades", "records"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


def compute_analytics(db_path: str,
                      registry: Optional[List[Dict[str, Any]]] = None,
                      rewards_path: str = os.path.join(ROOT, "trading_rewards.json"),
                      ) -> Dict[str, Any]:
    """Aggregate everything the ANALYTICS screen renders, JSON-safe."""
    registry = registry or []

    equity_curve: List[float] = []
    r_multiples: List[float] = []
    monthly_r = [0.0] * 12
    total_trades = wins = 0
    gross_win = gross_loss = 0.0
    max_dd_pct = 0.0

    history = _reward_history(rewards_path)
    if history:
        equity = 0.0
        peak = 0.0
        for rec in history:
            pnl = float(rec.get("pnl", rec.get("profit", 0.0)) or 0.0)
            equity += pnl
            equity_curve.append(round(equity, 2))
            peak = max(peak, equity)
            if peak > 0:
                max_dd_pct = max(max_dd_pct, (peak - equity) / peak * 100)
            risk = float(rec.get("risk", rec.get("risk_amount", 0.0)) or 0.0)
            if risk > 0:
                r_multiples.append(round(pnl / risk, 3))
            when = rec.get("time") or rec.get("ts") or rec.get("closed_at")
            month_idx = _month_index(when)
            if month_idx is not None:
                monthly_r[month_idx] += _approx_r(pnl, risk)
            total_trades += 1
            if pnl > 0:
                wins += 1
                gross_win += pnl
            elif pnl < 0:
                gross_loss += abs(pnl)

    if not equity_curve:
        fallback = _curve_from_trades_table(db_path)
        equity_curve, r_multiples, monthly_r, totals = fallback
        total_trades, wins, gross_win, gross_loss = totals

    profit_factor = round(gross_win / gross_loss, 3) if gross_loss > 0 else (
        round(gross_win, 3) if gross_win > 0 else 0.0)
    win_rate = round(wins / total_trades * 100, 2) if total_trades else 0.0

    per_strategy: List[Dict[str, Any]] = []
    for s in registry:
        per_strategy.append({
            "id": s.get("id"),
            "name": s.get("name", s.get("id")),
            "stage": s.get("stage", "CANDIDATE"),
            "trades": int(s.get("trades", 0)),
            "win_rate": float(s.get("winRate", s.get("win_rate", 0.0))),
            "profit_factor": float(s.get("profitFactor", s.get("profit_factor", 0.0))),
            "sharpe": float(s.get("sharpe", s.get("sharpe", 0.0))),
            "max_dd": float(s.get("maxDD", s.get("max_dd", 0.0))),
        })

    return {
        "v": 1,
        "equity_curve": equity_curve[-240:],
        "r_multiples": r_multiples[-120:],
        "monthly_r": [round(m, 3) for m in monthly_r],
        "totals": {
            "trades": total_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_dd_pct, 3),
            "net": round(equity_curve[-1], 2) if equity_curve else 0.0,
        },
        "per_strategy": per_strategy,
    }


def _month_index(when: Any) -> Optional[int]:
    try:
        if isinstance(when, (int, float)):
            return datetime.fromtimestamp(float(when)).month - 1
        text = str(when)
        if len(text) >= 7:
            return int(text[5:7]) - 1     # ISO yyyy-mm-...
    except Exception as exc:
        logger.debug("equity check failed: %s", exc)
    return None


def _approx_r(pnl: float, risk: float) -> float:
    return round(pnl / risk, 3) if risk > 0 else round(pnl / 100.0, 3)


def _curve_from_trades_table(db_path: str):
    """Fallback: build curve/R series directly from the trades table."""
    curve: List[float] = []
    r_multiples: List[float] = []
    monthly = [0.0] * 12
    total = wins = 0
    gross_win = gross_loss = 0.0
    try:
        import sqlite3
        # Equity/win-rate shown on the ANALYTICS screen is a performance
        # claim: only broker-evidenced tickets may feed it. Local import
        # because research_validation imports ``audit`` from journal_audit.
        try:
            from research_validation import broker_reconciled_tickets
        except ImportError:
            from research.research_validation import broker_reconciled_tickets
        tickets = sorted(broker_reconciled_tickets(db_path))
        if not tickets:
            return [], [], monthly, (0, 0, 0.0, 0.0)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            marks = ",".join("?" for _ in tickets)
            rows = conn.execute(
                "SELECT pnl, COALESCE(risk,0) AS risk, COALESCE(close_time,'') AS ct "
                "FROM trades WHERE ticket IN (%s) ORDER BY ticket" % marks,
                tuple(tickets),
            ).fetchall()
    except Exception as exc:
        logger.debug("analytics trades fallback unavailable: %s", exc)
        return [], [], monthly, (0, 0, 0.0, 0.0)

    equity = 0.0
    for row in rows:
        pnl = float(row["pnl"] or 0.0)
        equity += pnl
        curve.append(round(equity, 2))
        risk = float(row["risk"] or 0.0)
        if risk > 0:
            r_multiples.append(round(pnl / risk, 3))
        idx = _month_index(row["ct"]) if row["ct"] else None
        if idx is not None:
            monthly[idx] += _approx_r(pnl, risk)
        total += 1
        if pnl > 0:
            wins += 1
            gross_win += pnl
        elif pnl < 0:
            gross_loss += abs(pnl)
    return curve, r_multiples, monthly, (total, wins, gross_win, gross_loss)
