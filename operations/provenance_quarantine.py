"""Explicit quarantine of journal rows whose provenance was never declared.

Rows written before the ``trades.provenance`` constraint existed carry NULL.
The audit recorded that residual honestly: those rows are *excluded from
promotion by the undeclared-row rule rather than retro-labelled*.  Correct,
but silent — an undeclared row was never told apart from a row that merely
failed to write, and nothing in the journal said "this row is unusable".

This module makes the separation explicit and auditable:

* a row with no provenance declaration is recorded in ``provenance_quarantine``
  with the operator, the reason and the time;
* **the trade row is never modified.**  Its ``provenance`` stays NULL, because
  "we do not know where this came from" must not become "we know it came from
  the broker" — or from a fixture — by administrative act.  A quarantine is a
  statement about the row's *usability*, not about its origin;
* every admission reader excludes quarantined tickets, so a quarantined row
  can never contribute evidence: not to promotion, not to walk-forward, not
  to the live comparison.

The module is idempotent and safe to re-run: a row that is already
quarantined is re-stamped, never duplicated.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

#: The operator must type this to run a bulk quarantine.  Quarantining a row
#: removes it from the evidence pool, so it is an operator act, not a
#: background job.
CONFIRMATION = "QUARANTINE_UNDECLARED_PROVENANCE"

#: Why a row lands in the quarantine.  There is exactly one reason: the row
#: never declared where its evidence came from.
REASON = "undeclared_provenance"

_LIMITATION = (
    "Quarantine marks a row unusable; it does not establish where the row "
    "came from. The trade row is left untouched and can only re-enter the "
    "evidence pool through a broker-reconciled write that declares its own "
    "provenance."
)


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS provenance_quarantine (
        ticket INTEGER PRIMARY KEY, quarantined_at TEXT NOT NULL,
        operator TEXT NOT NULL, note TEXT NOT NULL, reason TEXT NOT NULL
    )""")


def _table_exists(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='provenance_quarantine'"
    ).fetchone()
    return row is not None


def quarantined_tickets(path: str = "trading_state.db") -> Set[int]:
    """Tickets recorded as quarantined (empty set when there are none).

    Never raises for a missing database or a missing table: "nothing is
    quarantined" is a valid answer, and a reader must not fail closed on a
    journal that simply has no quarantine ledger yet.
    """
    if not path or not os.path.exists(path):
        return set()
    try:
        con = sqlite3.connect(path, timeout=2)
    except sqlite3.Error:
        return set()
    try:
        if not _table_exists(con):
            return set()
        rows = con.execute("SELECT ticket FROM provenance_quarantine").fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    tickets: Set[int] = set()
    for row in rows:
        try:
            tickets.add(int(row[0]))
        except (TypeError, ValueError):
            continue
    return tickets


def undeclared_tickets(con: sqlite3.Connection) -> List[int]:
    """Tickets whose ``provenance`` was never declared (NULL or blank)."""
    columns = {row[1] for row in con.execute("PRAGMA table_info(trades)").fetchall()}
    if "provenance" not in columns:
        # A journal without the column predates the constraint entirely:
        # every row is undeclared.
        rows = con.execute("SELECT ticket FROM trades").fetchall()
    else:
        rows = con.execute(
            "SELECT ticket FROM trades WHERE provenance IS NULL OR TRIM(provenance) = ''"
        ).fetchall()
    tickets: List[int] = []
    for row in rows:
        try:
            tickets.append(int(row[0]))
        except (TypeError, ValueError):
            continue
    return tickets


def report(path: str = "trading_state.db") -> Dict[str, Any]:
    """What is undeclared, what is already quarantined, what is left over."""
    if not os.path.exists(path):
        return {"status": "DATABASE_MISSING", "undeclared": 0, "quarantined": 0,
                "pending": 0, "tickets": []}
    con = sqlite3.connect(path, timeout=2)
    try:
        _ensure_table(con)
        undeclared = undeclared_tickets(con)
        quarantined = {int(row[0]) for row in
                       con.execute("SELECT ticket FROM provenance_quarantine").fetchall()}
    finally:
        con.close()
    pending = [ticket for ticket in undeclared if ticket not in quarantined]
    return {
        "status": "CLEAN" if not pending else "UNDECLARED_ROWS_PENDING",
        "undeclared": len(undeclared),
        "quarantined": len([t for t in undeclared if t in quarantined]),
        "pending": len(pending),
        "tickets": sorted(pending),
        "reason": REASON,
        "limitation": _LIMITATION,
    }


def quarantine_legacy_rows(
    path: str = "trading_state.db",
    operator: str = "",
    note: str = "",
    confirmation: str = "",
) -> Dict[str, Any]:
    """Quarantine every journal row that never declared its provenance.

    The trade rows are NOT modified: ``provenance`` stays NULL.  What is
    written is the quarantine record — who decided the row is unusable, when,
    and why — so the exclusion is auditable instead of invisible.

    Raises:
        ValueError: on a missing confirmation token, a missing operator or
            note, or a missing journal.
    """
    if confirmation != CONFIRMATION:
        raise ValueError(f"quarantine requires confirmation token {CONFIRMATION}")
    operator = str(operator or "").strip()
    note = str(note or "").strip()
    if not operator or not note:
        raise ValueError("quarantine requires operator and note")
    if not os.path.exists(path):
        raise ValueError("journal database is missing")

    at = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(path, timeout=5)
    try:
        _ensure_table(con)
        already = {int(row[0]) for row in
                   con.execute("SELECT ticket FROM provenance_quarantine").fetchall()}
        undeclared = undeclared_tickets(con)
        newly = [ticket for ticket in undeclared if ticket not in already]
        con.executemany(
            "INSERT INTO provenance_quarantine"
            "(ticket,quarantined_at,operator,note,reason) VALUES(?,?,?,?,?) "
            "ON CONFLICT(ticket) DO UPDATE SET quarantined_at=excluded.quarantined_at,"
            "operator=excluded.operator,note=excluded.note,reason=excluded.reason",
            [(ticket, at, operator[:200], note[:2000], REASON) for ticket in undeclared],
        )
        con.commit()
    finally:
        con.close()
    return {
        "status": "QUARANTINED",
        "quarantined_at": at,
        "operator": operator[:200],
        "undeclared_rows": len(undeclared),
        "newly_quarantined": len(newly),
        "already_quarantined": len(undeclared) - len(newly),
        "tickets": sorted(undeclared),
        "reason": REASON,
        "provenance_modified": False,
        "limitation": _LIMITATION,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: ``python -m operations.provenance_quarantine --report|--quarantine``."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Quarantine journal rows that never declared a provenance")
    parser.add_argument("--db", default="trading_state.db", help="journal database")
    parser.add_argument("--report", action="store_true", help="report only (default)")
    parser.add_argument("--quarantine", action="store_true", help="perform the quarantine")
    parser.add_argument("--operator", default="", help="who is quarantining")
    parser.add_argument("--note", default="", help="why")
    parser.add_argument("--confirmation", default="", help="must equal %s" % CONFIRMATION)
    args = parser.parse_args(argv)

    if args.quarantine:
        result = quarantine_legacy_rows(
            args.db, operator=args.operator, note=args.note, confirmation=args.confirmation)
    else:
        result = report(args.db)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
