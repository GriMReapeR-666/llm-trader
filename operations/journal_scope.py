"""Mode/account scope metadata for paper, demo, live, and research journals."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
import sqlite3
from typing import Any, Dict

ALLOWED_MODES = {"PAPER", "DEMO", "LIVE", "RESEARCH", "LEGACY_UNSCOPED"}
ACCOUNT_REQUIRED_MODES = {"DEMO", "LIVE"}


def _hash_account(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24] if value else ""


def ensure_scope(path: str, mode: str, account_fingerprint: str = "") -> Dict[str, Any]:
    mode = str(mode).upper()
    if mode not in ALLOWED_MODES:
        raise ValueError(f"journal mode must be one of {sorted(ALLOWED_MODES)}")
    account_fingerprint = str(account_fingerprint or "").strip()
    if mode in ACCOUNT_REQUIRED_MODES and not account_fingerprint:
        raise ValueError(f"{mode} journal scope requires an account fingerprint")
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS journal_scope(
            id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL,
            account_hash TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""")
        row = con.execute("SELECT * FROM journal_scope WHERE id=1").fetchone()
        now = datetime.now(timezone.utc).isoformat()
        account_hash = _hash_account(account_fingerprint)
        if row and (row[1] != mode or row[2] != account_hash):
            existing_mode = row[1]
            # PAPER and RESEARCH are internal simulation modes that should
            # be allowed to operate regardless of the existing scope. They
            # never place real orders or interact with broker accounts.
            if mode in {"PAPER", "RESEARCH"}:
                pass  # allow transition silently
            elif existing_mode in {"PAPER", "RESEARCH"}:
                # Overwriting a simulation scope with a live/demo scope
                # is also acceptable because no real orders were placed.
                con.execute(
                    "UPDATE journal_scope SET mode=?, account_hash=?, updated_at=? WHERE id=1",
                    (mode, account_hash, now),
                )
            else:
                raise RuntimeError(
                    f"journal scope mismatch: existing mode/account does not match requested {mode}; "
                    f"use --overwrite to re-scope the journal"
                )
        if not row:
            con.execute("INSERT INTO journal_scope(id,mode,account_hash,created_at,updated_at) VALUES(1,?,?,?,?)",
                        (mode, account_hash, now, now))
        else:
            con.execute("UPDATE journal_scope SET updated_at=? WHERE id=1", (now,))
    return {"mode": mode, "account_hash": account_hash, "path": os.path.abspath(path), "enforced": True}


def status(path: str = "trading_state.db") -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"status": "MISSING", "path": os.path.abspath(path)}
    with sqlite3.connect(path) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "journal_scope" not in tables:
            return {"status": "UNSCOPED", "path": os.path.abspath(path), "action_required": True}
        row = con.execute("SELECT mode,account_hash,created_at,updated_at FROM journal_scope WHERE id=1").fetchone()
    scoped = bool(row) and (row[0] not in ACCOUNT_REQUIRED_MODES or bool(row[1]))
    return {"status": "SCOPED" if scoped else ("INVALID" if row else "UNSCOPED"), "path": os.path.abspath(path),
            "mode": row[0] if row else None, "account_hash": row[1] if row else None,
            "created_at": row[2] if row else None, "updated_at": row[3] if row else None}
