"""Deterministic labels for non-trade setup and broker-intent outcomes.

The labels are journal evidence only.  They never grant order permission and
are intentionally derived from the durable state transition, not from an LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any, Dict


SETUP_LABELS_DDL = """
CREATE TABLE IF NOT EXISTS setup_labels (
  subject_type TEXT NOT NULL, subject_key TEXT PRIMARY KEY, label TEXT NOT NULL,
  rationale TEXT NOT NULL, label_rule TEXT NOT NULL, created_at TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'AUTO', reviewed_by TEXT, reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_setup_labels_label ON setup_labels(label, created_at);
"""


def classify_setup_outcome(state: Any, reason: Any = "") -> Dict[str, str]:
    state_text = str(state or "").upper()
    reason_text = str(reason or "")
    if state_text in {"REJECTED", "MISSED"} and any(token in reason_text.lower() for token in (
            "stale", "missing data", "data missing", "invalid quote", "dom missing",
            "calendar unavailable", "news data missing", "tick unavailable")):
        return {"label": "DATA_QUALITY_BLOCKED",
                "rationale": f"required market data was missing, stale, or invalid; {reason_text[:300]}".rstrip("; "),
                "label_rule": "setup_state_data_quality_v1"}
    mapping = {
        "REJECTED": ("REJECTED_VALIDATOR", "deterministic validator rejected the setup", "setup_state_rejected_v1"),
        "MISSED": ("MISSED_UNFILLED", "validated setup did not become a broker fill", "setup_state_missed_v1"),
        "CANCELLED": ("CANCELLED_UNFILLED", "pending order was cancelled without a valid fill", "order_state_cancelled_v1"),
        "EXPIRED": ("EXPIRED_UNFILLED", "pending order expired without a valid fill", "order_state_expired_v1"),
        "INVALIDATED": ("INVALIDATED_SETUP", "setup invalidated before a valid fill", "setup_state_invalidated_v1"),
        "UNRESOLVED": ("UNRESOLVED_BROKER", "broker/order outcome requires reconciliation", "setup_state_unresolved_v1"),
        "DATA_QUALITY": ("DATA_QUALITY_BLOCKED", "required market data was missing, stale, or invalid", "setup_state_data_quality_v1"),
        "MISSING_DATA": ("DATA_QUALITY_BLOCKED", "required market data was missing", "setup_state_data_quality_v1"),
        "STALE_DATA": ("DATA_QUALITY_BLOCKED", "required market data was stale", "setup_state_data_quality_v1"),
    }
    label, rationale, rule = mapping.get(state_text,
                                         ("OTHER_SETUP_STATE", "non-terminal setup state", "setup_state_other_v1"))
    suffix = str(reason or "")[:300]
    return {"label": label, "rationale": f"{rationale}; {suffix}".rstrip("; "), "label_rule": rule}


def ensure_setup_label_table(con: sqlite3.Connection) -> None:
    con.executescript(SETUP_LABELS_DDL)
    columns = {row["name"] for row in con.execute("PRAGMA table_info(setup_labels)").fetchall()}
    if "reviewed_by" not in columns:
        con.execute("ALTER TABLE setup_labels ADD COLUMN reviewed_by TEXT")
    if "reviewed_at" not in columns:
        con.execute("ALTER TABLE setup_labels ADD COLUMN reviewed_at TEXT")


def persist_setup_label(con: sqlite3.Connection, subject_type: str, subject_key: str,
                        state: Any, reason: Any = "", *, created_at: str | None = None) -> Dict[str, str]:
    outcome = classify_setup_outcome(state, reason)
    ensure_setup_label_table(con)
    con.execute("""INSERT OR IGNORE INTO setup_labels(
        subject_type,subject_key,label,rationale,label_rule,created_at,review_status
    ) VALUES(?,?,?,?,?,?,?)""", (
        str(subject_type), str(subject_key), outcome["label"], outcome["rationale"],
        outcome["label_rule"], created_at or datetime.now(timezone.utc).isoformat(), "AUTO",
    ))
    return outcome
