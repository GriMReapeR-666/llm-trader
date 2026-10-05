"""Idempotent migration of the legacy ``trading_rewards.json`` journal.

The source file is never modified.  Records are copied into the durable SQLite
journal and can be audited/re-run safely; existing tickets are skipped.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sqlite3
from typing import Any, Dict

from intelligence.trading_memory import TradingMemory
from core.trading_safety import SCALP_MAGIC, SWING_MAGIC, TradingStore


def _iso(value: Any) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except ValueError:
        return text


def migrate(source: str = "trading_rewards.json", db_path: str = "trading_state.db") -> Dict[str, Any]:
    result: Dict[str, Any] = {"source": source, "database": db_path, "found": False,
                              "imported": 0, "skipped": 0, "closed_imported": 0, "errors": []}
    if not os.path.exists(source):
        result["note"] = "Legacy journal file not found; no changes made."
        return result
    result["found"] = True
    try:
        with open(source, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        rows = data if isinstance(data, list) else data.get("trades", [])
        if not isinstance(rows, list):
            raise ValueError("expected a JSON array or an object with a trades array")
    except Exception as exc:
        result["errors"].append(f"read: {exc}")
        return result

    # Ensure both authoritative trade tables and memory/label tables exist.
    # TradingMemory owns memory tables; TradingStore owns trades/proposals.
    TradingStore(db_path)
    TradingMemory(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        for legacy in rows:
            try:
                ticket = int(legacy["ticket"])
                if con.execute("SELECT 1 FROM trades WHERE ticket=?", (ticket,)).fetchone():
                    result["skipped"] += 1
                    continue
                decision = legacy.get("decision") or {}
                if not isinstance(decision, dict):
                    decision = {}
                side = str(legacy.get("action") or decision.get("action") or "").upper()
                side = "BUY" if side in {"BUY", "LONG"} else "SELL"
                strategy = str(decision.get("strategy") or legacy.get("strategy") or "legacy")
                magic = int(decision.get("magic") or (SWING_MAGIC if strategy == "swing" else SCALP_MAGIC))
                payload = {"source": source, "legacy": legacy, "migration_version": 1}
                opened = _iso(legacy.get("open_time"))
                con.execute("""INSERT INTO trades
                    (ticket,magic,strategy,symbol,side,volume,entry,stop_loss,take_profit,
                     risk_amount,opened_at,payload)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    ticket, magic, strategy, str(legacy.get("symbol", "")), side,
                    float(legacy.get("volume") or 0), float(legacy.get("open_price") or 0),
                    decision.get("stop_loss"), decision.get("take_profit"),
                    float(legacy.get("risk_amount") or 0), opened, json.dumps(payload, default=str)))
                # Preserve the original proposal as an auditable, already-executed proposal.
                setup_key = f"legacy-{ticket}"
                con.execute("""INSERT INTO proposals
                    (created_at,setup_key,strategy,symbol,side,payload,approved,reasons)
                    VALUES (?,?,?,?,?,?,1,?)""", (opened, setup_key, strategy,
                    str(legacy.get("symbol", "")), side, json.dumps(decision, default=str),
                    json.dumps(["imported legacy execution"])))
                result["imported"] += 1

                status = str(legacy.get("status", "")).upper()
                if status != "OPEN" and legacy.get("pnl") is not None:
                    closed_at = _iso(legacy.get("close_time") or legacy.get("exit_time"))
                    exit_price = legacy.get("close_price", legacy.get("exit_price", legacy.get("open_price", 0)))
                    reason = str(legacy.get("exit_reason") or status or "legacy close")
                    con.execute("UPDATE trades SET closed_at=?,exit_price=?,pnl=?,exit_reason=? WHERE ticket=?",
                                (closed_at, float(exit_price or 0), float(legacy.get("pnl") or 0), reason, ticket))
                    result["closed_imported"] += 1
            except Exception as exc:
                result["errors"].append(f"ticket {legacy.get('ticket', '?')}: {exc}")

    # Label only imported closed trades after the transaction has committed.
    memory = TradingMemory(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        closed = con.execute("SELECT ticket,pnl,risk_amount,exit_reason FROM trades WHERE payload LIKE ? AND closed_at IS NOT NULL",
                             (f'%"source": "{source}"%',)).fetchall()
    for row in closed:
        memory.label_closed_trade(int(row["ticket"]), float(row["pnl"] or 0), float(row["risk_amount"] or 0), row["exit_reason"] or "legacy close")
    result["labelled_closed"] = len(closed)
    result["passed"] = not result["errors"]
    return result


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2))
