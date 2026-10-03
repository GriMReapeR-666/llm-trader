"""Read-only report for legacy open records lacking modern provenance."""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List


CONFIRMATION = "QUARANTINE_LEGACY"
RETIRE_CONFIRMATION = "RETIRE_LEGACY_NO_BROKER_MATCH"


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS legacy_quarantine (
        ticket INTEGER PRIMARY KEY, quarantined_at TEXT NOT NULL,
        operator TEXT NOT NULL, note TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS legacy_retirements (
        ticket INTEGER PRIMARY KEY, retired_at TEXT NOT NULL,
        operator TEXT NOT NULL, note TEXT NOT NULL, evidence_hash TEXT NOT NULL
    )""")


def report(path: str = "trading_state.db") -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"status": "DATABASE_MISSING", "records": [], "operator_action_required": True}
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        _ensure_table(con)
        columns = {row["name"] for row in con.execute("PRAGMA table_info(trades)").fetchall()}
        setup_expr = "t.setup_id" if "setup_id" in columns else "NULL AS setup_id"
        rows = con.execute("""SELECT t.ticket,t.magic,t.strategy,t.symbol,t.side,t.risk_amount,
            t.opened_at,t.payload,""" + setup_expr + """,q.quarantined_at,q.operator AS quarantine_operator
            FROM trades t LEFT JOIN legacy_quarantine q ON q.ticket=t.ticket
            LEFT JOIN legacy_retirements r ON r.ticket=t.ticket
            WHERE t.closed_at IS NULL AND r.ticket IS NULL ORDER BY t.opened_at""").fetchall()
    records: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        features = payload.get("proposal_features") or payload.get("features") or {}
        reasons = []
        if not row["setup_id"] or str(row["setup_id"]).startswith("legacy"):
            reasons.append("missing deterministic setup ID")
        if float(row["risk_amount"] or 0) <= 0:
            reasons.append("missing positive risk amount")
        if not features.get("feature_schema_version") or not features.get("feature_schema_hash"):
            reasons.append("missing feature schema provenance")
        if reasons:
            item = {"ticket": row["ticket"], "symbol": row["symbol"], "magic": row["magic"], "opened_at": row["opened_at"], "reasons": reasons}
            if row["quarantined_at"]:
                item["quarantined_at"] = row["quarantined_at"]
                item["quarantine_operator"] = row["quarantine_operator"]
                item["status"] = "QUARANTINED_PENDING_RECONCILIATION"
            else:
                item["status"] = "REVIEW_REQUIRED"
            records.append(item)
    with sqlite3.connect(path) as con:
        retired_count = int(con.execute("SELECT COUNT(*) FROM legacy_retirements").fetchone()[0])
    return {"status": "REVIEW_REQUIRED" if any(item["status"] == "REVIEW_REQUIRED" for item in records) else ("QUARANTINED_PENDING_RECONCILIATION" if records else "CLEAN"), "records": records,
            "count": len(records), "retired_count": retired_count, "operator_action_required": bool(records),
            "limitation": "Retirement is a recoverable evidence marker, not deletion or a fabricated close; only authenticated broker evidence may authorize it."}


def quarantine(path: str, ticket: int, operator: str, note: str, confirmation: str) -> Dict[str, Any]:
    """Record operator quarantine provenance without mutating the trade row."""
    if confirmation != CONFIRMATION:
        raise ValueError(f"quarantine requires confirmation token {CONFIRMATION}")
    operator = str(operator or "").strip()
    note = str(note or "").strip()
    if not operator or not note:
        raise ValueError("quarantine requires operator and note")
    if not os.path.exists(path):
        raise ValueError("journal database is missing")
    with sqlite3.connect(path) as con:
        _ensure_table(con)
        row = con.execute("SELECT ticket FROM trades WHERE ticket=? AND closed_at IS NULL", (int(ticket),)).fetchone()
        if not row:
            raise ValueError("ticket is not an open journal trade")
        at = datetime.now(timezone.utc).isoformat()
        con.execute("INSERT INTO legacy_quarantine(ticket,quarantined_at,operator,note) VALUES(?,?,?,?) "
                    "ON CONFLICT(ticket) DO UPDATE SET quarantined_at=excluded.quarantined_at,operator=excluded.operator,note=excluded.note",
                    (int(ticket), at, operator[:200], note[:2000]))
    return {"status": "QUARANTINED_PENDING_RECONCILIATION", "ticket": int(ticket),
            "quarantined_at": at, "operator": operator[:200], "execution_authority": False,
            "limitation": "Quarantine is a journal review marker only; MT5 reconciliation is still required."}


def retire_unmatched(path: str, ticket: int, operator: str, note: str,
                    confirmation: str, broker_match_verified: bool,
                    evidence: str) -> Dict[str, Any]:
    """Retire an orphaned legacy row only after authenticated no-match evidence."""
    if confirmation != RETIRE_CONFIRMATION:
        raise ValueError(f"retirement requires confirmation token {RETIRE_CONFIRMATION}")
    operator, note, evidence = str(operator or "").strip(), str(note or "").strip(), str(evidence or "").strip()
    if not operator or not note or not evidence:
        raise ValueError("retirement requires operator, note, and broker evidence")
    if broker_match_verified is not False:
        raise ValueError("retirement is only allowed when broker_match_verified is explicitly False")
    if not os.path.exists(path):
        raise ValueError("journal database is missing")
    evidence_hash = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    with sqlite3.connect(path) as con:
        _ensure_table(con)
        row = con.execute("SELECT ticket FROM trades WHERE ticket=? AND closed_at IS NULL", (int(ticket),)).fetchone()
        if not row:
            raise ValueError("ticket is not an open journal trade")
        at = datetime.now(timezone.utc).isoformat()
        con.execute("""INSERT INTO legacy_retirements(ticket,retired_at,operator,note,evidence_hash)
                      VALUES(?,?,?,?,?) ON CONFLICT(ticket) DO UPDATE SET
                      retired_at=excluded.retired_at,operator=excluded.operator,
                      note=excluded.note,evidence_hash=excluded.evidence_hash""",
                    (int(ticket), at, operator[:200], note[:2000], evidence_hash))
    return {"status": "RETIRED_NO_BROKER_MATCH", "ticket": int(ticket),
            "retired_at": at, "operator": operator[:200], "evidence_hash": evidence_hash,
            "execution_authority": False,
            "limitation": "Original legacy row is preserved; no close/P&L/label was fabricated."}
