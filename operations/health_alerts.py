"""Read-only operational alert evaluation over the durable journal."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import math
import os
import re
import sqlite3
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Alert code → incident runbook mapping (P7 incidents pane).
#
# Every alert this module can emit MUST have an entry here pointing at a
# real `## heading` in docs/INCIDENT_RUNBOOKS.md — tests pin that invariant,
# so a new alert code cannot ship without operator procedure documentation.
# `first_response` is the immediate actionable step shown on the deck card.
# ---------------------------------------------------------------------------

RUNBOOK_PATH = "docs/INCIDENT_RUNBOOKS.md"


def _slug(heading: str) -> str:
    """GitHub-style anchor for a markdown heading."""
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


RUNBOOK_INDEX: Dict[str, Dict[str, str]] = {
    "DATABASE_MISSING": {
        "section": "Universal first response",
        "first_response": "Journal DB missing — enable kill switch, stop writers, verify paths before any restart.",
    },
    "UNRESOLVED_ORDER_INTENTS": {
        "section": "Stuck, duplicate, or unresolved order",
        "first_response": "Do NOT submit a compensating order — open an execution review and compare with MT5 history.",
    },
    "STALE_POSITION_MARKS": {
        "section": "Bad/stale/manipulated market data",
        "first_response": "Stop proposals on affected symbols; never substitute a candle close for a missing quote.",
    },
    "MISSING_JOURNAL_FIELDS": {
        "section": "Database corruption or failed integrity check",
        "first_response": "Stop all writers, copy DB+WAL for evidence, run `journal integrity` before anything else.",
    },
    "ABNORMAL_SLIPPAGE": {
        "section": "Stuck, duplicate, or unresolved order",
        "first_response": "Halt new entries on the affected symbol; reconcile fills vs broker reports before resuming.",
    },
    "MODEL_SCHEMA_DRIFT": {
        "section": "Universal first response",
        "first_response": "Approved model without pinned feature schema — quarantine from promotion data, review registry.",
    },
    "MEMORY_SERVICE_FAILURE": {
        "section": "Universal first response",
        "first_response": "Memory outbox FAILED — capture outbox rows, restart memory service, replay pending messages.",
    },
    "UNVERIFIED_MEMORY_BLOCKED": {
        "section": "Universal first response",
        "first_response": "Unverified memory content blocked — operator review required before unblocking.",
    },
    "STALE_AGENT_HEARTBEATS": {
        "section": "Universal first response",
        "first_response": "Agent run heartbeat lost — confirm the process, recover stale leases, restart the agent.",
    },
    "EXPIRED_AGENT_TASK_LEASES": {
        "section": "Universal first response",
        "first_response": "Expired task leases — run lease recovery (`recover_stale_tasks`), then confirm task ownership.",
    },
    "EXHAUSTED_AGENT_TASK_RETRIES": {
        "section": "Universal first response",
        "first_response": "Task exhausted retries — read the last error, fix root cause, requeue manually; do not blind-retry.",
    },
    "EXHAUSTED_EXECUTION_REVIEW_RETRIES": {
        "section": "Stuck, duplicate, or unresolved order",
        "first_response": "Execution review stuck at max retries — resolve against broker evidence only.",
    },
    "CLOCK_DRIFT_OR_MALFORMED": {
        "section": "Universal first response",
        "first_response": "Clock drift/malformed observation — resync the offending source; do not trust stale timestamps.",
    },
}


def runbook_anchor(code: str) -> str:
    """Markdown anchor for the runbook section of an alert code."""
    entry = RUNBOOK_INDEX.get(code)
    return _slug(entry["section"]) if entry else _slug("Universal first response")


def runbook_sections() -> Dict[str, str]:
    """section title → anchor for every mapped runbook section."""
    return {e["section"]: _slug(e["section"]) for e in RUNBOOK_INDEX.values()}


def evaluate(path: str = "trading_state.db", *, stale_seconds: int = 30,
             max_slippage_price: float = 0.0) -> Dict[str, Any]:
    if (not isinstance(stale_seconds, int) or isinstance(stale_seconds, bool)
            or stale_seconds <= 0):
        raise ValueError("stale_seconds must be a positive integer")
    try:
        max_slippage_price = float(max_slippage_price)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("max_slippage_price must be finite and non-negative") from None
    if not math.isfinite(max_slippage_price) or max_slippage_price < 0:
        raise ValueError("max_slippage_price must be finite and non-negative")
    if not os.path.exists(path):
        _dm = RUNBOOK_INDEX["DATABASE_MISSING"]
        return {"status": "CRITICAL", "alerts": [{"code": "DATABASE_MISSING", "severity": "CRITICAL",
                "runbook": _dm["section"], "first_response": _dm["first_response"]}], "execution_authority": False}
    alerts: List[Dict[str, Any]] = []
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {
            table: {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            for table in tables
            if table.isidentifier()
        }
        if "order_intents" in tables:
            unresolved = con.execute("SELECT COUNT(*) FROM order_intents WHERE status IN ('CREATED','ERROR','UNRESOLVED')").fetchone()[0]
            if unresolved:
                alerts.append({"code": "UNRESOLVED_ORDER_INTENTS", "count": unresolved, "severity": "CRITICAL"})
        if "position_observations" in tables:
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
            stale = con.execute("SELECT COUNT(*) FROM position_observations WHERE observed_at < ? OR mark_quality='STALE'", (cutoff,)).fetchone()[0]
            if stale:
                alerts.append({"code": "STALE_POSITION_MARKS", "count": stale, "severity": "WARNING"})
        if "trades" in tables:
            missing = con.execute("SELECT COUNT(*) FROM trades WHERE payload IS NULL OR payload='' OR risk_amount<=0").fetchone()[0]
            if missing:
                alerts.append({"code": "MISSING_JOURNAL_FIELDS", "count": missing, "severity": "CRITICAL"})
        if "broker_execution_reports" in tables and max_slippage_price > 0:
            abnormal = con.execute("""SELECT COUNT(*) FROM broker_execution_reports
                WHERE ABS(COALESCE(implementation_shortfall,0)) > ?""", (max_slippage_price,)).fetchone()[0]
            if abnormal:
                alerts.append({"code": "ABNORMAL_SLIPPAGE", "count": abnormal, "severity": "WARNING"})
        if "model_registry" in tables:
            drift = con.execute("SELECT COUNT(*) FROM model_registry WHERE status='APPROVED' AND feature_schema_version IS NULL").fetchone()[0]
            if drift:
                alerts.append({"code": "MODEL_SCHEMA_DRIFT", "count": drift, "severity": "CRITICAL"})
        if "memory_outbox" in tables:
            failed_memory = con.execute("SELECT COUNT(*) FROM memory_outbox WHERE status='FAILED'").fetchone()[0]
            if failed_memory:
                alerts.append({"code": "MEMORY_SERVICE_FAILURE", "count": failed_memory, "severity": "WARNING"})
            blocked_memory = con.execute("SELECT COUNT(*) FROM memory_outbox WHERE status='BLOCKED'").fetchone()[0]
            if blocked_memory:
                alerts.append({"code": "UNVERIFIED_MEMORY_BLOCKED", "count": blocked_memory, "severity": "WARNING"})
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
        if "agent_runs" in tables and {"status", "heartbeat_at"} <= columns.get("agent_runs", set()):
            stale_runs = con.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE status='RUNNING' AND heartbeat_at < ?",
                (cutoff,),
            ).fetchone()[0]
            if stale_runs:
                alerts.append({"code": "STALE_AGENT_HEARTBEATS", "count": stale_runs, "severity": "CRITICAL"})
        if "agent_tasks" in tables and {"status", "lease_expires_at"} <= columns.get("agent_tasks", set()):
            expired_tasks = con.execute(
                "SELECT COUNT(*) FROM agent_tasks WHERE status IN ('RUNNING','ERROR') AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (cutoff,),
            ).fetchone()[0]
            if expired_tasks:
                alerts.append({"code": "EXPIRED_AGENT_TASK_LEASES", "count": expired_tasks, "severity": "WARNING"})
            if {"attempts", "max_attempts"} <= columns.get("agent_tasks", set()):
                exhausted_tasks = con.execute(
                    "SELECT COUNT(*) FROM agent_tasks WHERE status='ERROR' AND attempts >= max_attempts",
                ).fetchone()[0]
                if exhausted_tasks:
                    alerts.append({"code": "EXHAUSTED_AGENT_TASK_RETRIES", "count": exhausted_tasks, "severity": "CRITICAL"})
        if "execution_reviews" in tables and "retry_count" in columns.get("execution_reviews", set()):
            exhausted_reviews = con.execute(
                "SELECT COUNT(*) FROM execution_reviews WHERE status IN ('OPEN','ACKNOWLEDGED') AND retry_count >= 5",
            ).fetchone()[0]
            if exhausted_reviews:
                alerts.append({"code": "EXHAUSTED_EXECUTION_REVIEW_RETRIES", "count": exhausted_reviews, "severity": "CRITICAL"})
        if "clock_observations" in tables and {"source", "status", "offset_seconds", "max_offset_seconds"} <= columns.get("clock_observations", set()):
            # Evaluate the latest observation per source only; historical
            # skew remains audit evidence but should not create duplicate live
            # alerts after a later healthy observation.
            latest_clock: Dict[str, sqlite3.Row] = {}
            for row in con.execute("SELECT * FROM clock_observations ORDER BY id DESC").fetchall():
                source = str(row["source"] or "")
                if source and source not in latest_clock:
                    latest_clock[source] = row
            drifted = 0
            for row in latest_clock.values():
                try:
                    offset = float(row["offset_seconds"])
                    limit = float(row["max_offset_seconds"])
                    valid = math.isfinite(offset) and math.isfinite(limit) and abs(offset) <= limit and str(row["status"]).upper() == "OK"
                except (TypeError, ValueError, OverflowError):
                    valid = False
                if not valid:
                    drifted += 1
            if drifted:
                alerts.append({"code": "CLOCK_DRIFT_OR_MALFORMED", "count": drifted, "severity": "CRITICAL"})
    # Attach the incident runbook pointer to every alert (P7 incidents pane).
    for item in alerts:
        entry = RUNBOOK_INDEX.get(str(item.get("code", "")))
        if entry:
            item["runbook"] = entry["section"]
            item["first_response"] = entry["first_response"]
    status = "CRITICAL" if any(item.get("severity") == "CRITICAL" for item in alerts) else "WARNING" if alerts else "OK"
    return {"status": status, "alerts": alerts, "checked_at": datetime.now(timezone.utc).isoformat(),
            "execution_authority": False, "limitation": "Alert evaluation is read-only; delivery/escalation still requires operator integration."}
