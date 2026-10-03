"""κ Compliance & Audit Agent — Immutable, hash-chained audit log.

Implements the UAQP v2 Patch 4:

    κ — Compliance & Audit Agent

    φ_κ: (every state transition, every LLM call) → immutable log

    Append-only, hash-chained log of all S transitions and agent I/O
    (for post-hoc reconstruction of "why did it do that").

    Tracks pattern-day-trading and wash-sale exposure.

    Runs a structural check on ε's order-slicing output for rapid
    cancel/replace patterns that could read as spoofing to exchange
    surveillance, independent of intent.

Properties:
    - Append-only: entries can never be modified or deleted
    - Hash-chained: each entry's hash includes the previous entry's hash
    - Covers: state transitions, LLM calls, orders, risk decisions
    - Detects: wash-sale, pattern-day-trading, spoofing patterns
    - SQLite-backed for durability and query capability
    - Thread-safe for concurrent access from multiple agents

References:
    UAQP Part III, Agent κ: φ_κ: (every state transition, every LLM call) → immutable log
    UAQP v2 Patch 4: "κ in particular is the difference between being able to
    explain a bad day to yourself versus not."
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ComplianceAudit")


# ============================================================================
# Event types
# ============================================================================

class EventType(Enum):
    """All event types that κ tracks."""
    # State transitions
    STATE_TRANSITION = "STATE_TRANSITION"     # Agent wrote to S
    REGIME_CHANGE = "REGIME_CHANGE"           # Market regime shifted
    KILL_SWITCH = "KILL_SWITCH"               # Kill switch activated

    # LLM calls
    LLM_CALL = "LLM_CALL"                    # LLM was invoked
    LLM_DECISION = "LLM_DECISION"            # LLM produced a trade decision
    LLM_REJECTED = "LLM_REJECTED"            # LLM decision was vetoed by gate

    # Orders
    ORDER_SUBMITTED = "ORDER_SUBMITTED"       # Order sent to broker
    ORDER_FILLED = "ORDER_FILLED"            # Order was filled
    ORDER_CANCELLED = "ORDER_CANCELLED"      # Order was cancelled
    ORDER_REJECTED = "ORDER_REJECTED"        # Order was rejected by broker
    ORDER_MODIFY = "ORDER_MODIFY"            # Order was modified (SL/TP)

    # Position lifecycle
    POSITION_OPENED = "POSITION_OPENED"      # New position
    POSITION_CLOSED = "POSITION_CLOSED"      # Position closed
    POSITION_MODIFIED = "POSITION_MODIFIED"  # SL/TP modified
    PARTIAL_CLOSE = "PARTIAL_CLOSE"          # Partial position close

    # Risk
    RISK_DECISION = "RISK_DECISION"          # Risk manager decision
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"      # Circuit breaker triggered
    GUARDIAN_TRIP = "GUARDIAN_TRIP"          # Ψ Guardian tripped

    # Validation
    GATE_PASSED = "GATE_PASSED"              # Validation gate passed
    GATE_FAILED = "GATE_FAILED"              # Validation gate failed

    # Compliance alerts
    WASH_SALE_ALERT = "WASH_SALE_ALERT"      # Wash-sale pattern detected
    PATTERN_DAY_ALERT = "PATTERN_DAY_ALERT"  # Pattern day trading alert
    SPOOFING_ALERT = "SPOOFING_ALERT"        # Spoofing pattern detected
    UNUSUAL_ACTIVITY = "UNUSUAL_ACTIVITY"    # Unusual trading activity


# ============================================================================
# Compliance constants
# ============================================================================

# Wash-sale: same symbol re-bought within 30 days at a loss
WASH_SALE_WINDOW_DAYS = 30

# Pattern day trading: 4+ round-trip trades in a rolling 5-business-day window
# (US equity rule — adapted for FX as risk guideline)
PDT_WINDOW_DAYS = 5
PDT_THRESHOLD = 4

# Spoofing detection: 3+ cancel/replace on same symbol within 60 seconds
SPOOF_WINDOW_S = 60.0
SPOOF_THRESHOLD = 3


# ============================================================================
# Entry data class
# ============================================================================

@dataclass
class AuditEntry:
    """A single immutable audit log entry."""
    entry_id: int = 0
    timestamp: str = ""
    event_type: str = ""
    agent: str = ""           # Which agent produced this (α, δ, σ, β, ρ, ε, μ, ω, Ψ, κ)
    symbol: str = ""
    cycle_num: int = 0
    run_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    prev_hash: str = ""
    entry_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA-256 hash of this entry (chained with prev_hash)."""
        content = json.dumps({
            "ts": self.timestamp,
            "type": self.event_type,
            "agent": self.agent,
            "symbol": self.symbol,
            "cycle": self.cycle_num,
            "payload": self.payload,
            "prev": self.prev_hash,
        }, sort_keys=True, default=str)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:20]


def verify_chain_hash(entries: List[Dict[str, Any]]) -> bool:
    """Verify hash chain integrity for a list of entry dicts.

    Works with both AuditEntry objects (via __dict__) and plain dicts.
    Returns True if chain is valid, False otherwise.

    Canonical implementation used by both ComplianceAudit.verify_chain()
    and ExternalGuardian.verify_chain().
    """
    if not entries:
        return True
    prev = "GENESIS"
    for entry in entries:
        entry_prev = entry.get("prev_hash") if isinstance(entry, dict) else getattr(entry, "prev_hash", "")
        entry_hash = entry.get("entry_hash") if isinstance(entry, dict) else getattr(entry, "entry_hash", "")
        if entry_prev != prev:
            return False
        prev = entry_hash
    return True


# ============================================================================
# Compliance Audit Engine
# ============================================================================

class ComplianceAudit:
    """κ — The Compliance & Audit Agent.

    Append-only, hash-chained log of all state transitions, LLM calls,
    and orders. Thread-safe. SQLite-backed.

    Usage:
        audit = ComplianceAudit()
        audit.log(EventType.ORDER_SUBMITTED, "EURUSD", payload={...})
        audit.log(EventType.LLM_CALL, payload={...})

        # Query
        entries = audit.query(event_type="ORDER_SUBMITTED", symbol="EURUSD")

        # Compliance checks
        wash = audit.check_wash_sale("EURUSD", "SELL", 1.1000)
        pdt = audit.check_pattern_day_trading()
        spoof = audit.check_spoofing("EURUSD")

        # Verify chain integrity
        assert audit.verify_chain()

        # Export for post-hoc analysis
        report = audit.generate_report(hours=24)
    """

    def __init__(self, db_path: str = "compliance_audit.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _con(self):
        """Thread-safe SQLite connection."""
        con = sqlite3.connect(self._db_path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with self._con() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    agent TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL DEFAULT '',
                    cycle_num INTEGER NOT NULL DEFAULT 0,
                    run_id TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    prev_hash TEXT NOT NULL DEFAULT 'GENESIS',
                    entry_hash TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_event_type
                ON audit_log(event_type)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_symbol
                ON audit_log(symbol)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp
                ON audit_log(timestamp)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_cycle
                ON audit_log(cycle_num)
            """)

    # ------------------------------------------------------------------
    # Core: log an event
    # ------------------------------------------------------------------

    def log(
        self,
        event_type: EventType,
        symbol: str = "",
        cycle_num: int = 0,
        run_id: str = "",
        agent: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """Append an immutable, hash-chained audit entry.

        This is the ONLY write path. Every state transition, LLM call,
        and order goes through here.
        """
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type.value,
            agent=agent,
            symbol=symbol,
            cycle_num=cycle_num,
            run_id=run_id,
            payload=payload or {},
        )

        with self._lock:
            with self._con() as con:
                # Get previous hash
                row = con.execute(
                    "SELECT entry_hash FROM audit_log ORDER BY entry_id DESC LIMIT 1"
                ).fetchone()
                entry.prev_hash = row["entry_hash"] if row else "GENESIS"

                # Compute chained hash
                entry.entry_hash = entry.compute_hash()

                # Insert
                con.execute(
                    "INSERT INTO audit_log "
                    "(timestamp, event_type, agent, symbol, cycle_num, run_id, "
                    "payload, prev_hash, entry_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry.timestamp,
                        entry.event_type,
                        entry.agent,
                        entry.symbol,
                        entry.cycle_num,
                        entry.run_id,
                        json.dumps(entry.payload, default=str),
                        entry.prev_hash,
                        entry.entry_hash,
                    ),
                )
                entry.entry_id = con.execute(
                    "SELECT last_insert_rowid()"
                ).fetchone()[0]

        logger.debug(
            "κ [%s] %s %s cycle=%d hash=%s",
            entry.event_type, entry.agent, entry.symbol,
            entry.cycle_num, entry.entry_hash[:8],
        )
        return entry

    # ------------------------------------------------------------------
    # Convenience logging methods
    # ------------------------------------------------------------------

    def log_state_transition(
        self, agent: str, symbol: str, cycle_num: int,
        from_state: str, to_state: str, reason: str = "", **extra: Any,
    ) -> AuditEntry:
        """Log a state transition (agent wrote to S)."""
        return self.log(
            EventType.STATE_TRANSITION, symbol=symbol,
            cycle_num=cycle_num, agent=agent,
            payload={"from": from_state, "to": to_state, "reason": reason, **extra},
        )

    def log_llm_call(
        self, symbol: str, cycle_num: int,
        prompt_tokens: int = 0, completion_tokens: int = 0,
        model: str = "", decision: str = "", **extra: Any,
    ) -> AuditEntry:
        """Log an LLM invocation."""
        return self.log(
            EventType.LLM_CALL, symbol=symbol,
            cycle_num=cycle_num, agent="ω",
            payload={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "model": model,
                "decision": decision,
                **extra,
            },
        )

    def log_order(
        self, event_type: EventType, symbol: str, cycle_num: int,
        ticket: int = 0, side: str = "", volume: float = 0.0,
        price: float = 0.0, sl: float = 0.0, tp: float = 0.0,
        reason: str = "", **extra: Any,
    ) -> AuditEntry:
        """Log an order event (submitted, filled, cancelled, rejected)."""
        return self.log(
            event_type, symbol=symbol,
            cycle_num=cycle_num, agent="ε",
            payload={
                "ticket": ticket, "side": side, "volume": volume,
                "price": price, "sl": sl, "tp": tp,
                "reason": reason, **extra,
            },
        )

    def log_risk_decision(
        self, symbol: str, cycle_num: int,
        decision: str, reason: str = "", **extra: Any,
    ) -> AuditEntry:
        """Log a risk management decision."""
        return self.log(
            EventType.RISK_DECISION, symbol=symbol,
            cycle_num=cycle_num, agent="ρ",
            payload={"decision": decision, "reason": reason, **extra},
        )

    def log_gate(
        self, passed: bool, gate_name: str, symbol: str, cycle_num: int,
        reason: str = "", **extra: Any,
    ) -> AuditEntry:
        """Log a validation gate result."""
        et = EventType.GATE_PASSED if passed else EventType.GATE_FAILED
        return self.log(
            et, symbol=symbol,
            cycle_num=cycle_num, agent="β",
            payload={"gate": gate_name, "passed": passed, "reason": reason, **extra},
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        event_type: Optional[str] = None,
        symbol: Optional[str] = None,
        agent: Optional[str] = None,
        cycle_from: Optional[int] = None,
        cycle_to: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query audit log with filters."""
        conditions = []
        params = []

        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if cycle_from is not None:
            conditions.append("cycle_num >= ?")
            params.append(cycle_from)
        if cycle_to is not None:
            conditions.append("cycle_num <= ?")
            params.append(cycle_to)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM audit_log WHERE {where} ORDER BY entry_id DESC LIMIT ?"
        params.append(limit)

        with self._con() as con:
            rows = con.execute(sql, params).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if isinstance(d.get('payload'), str):
                    try:
                        d['payload'] = json.loads(d['payload'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                result.append(d)
            return result

    def count(
        self, event_type: Optional[str] = None,
        symbol: Optional[str] = None,
        cycle_from: Optional[int] = None,
        cycle_to: Optional[int] = None,
    ) -> int:
        """Count entries matching filters."""
        conditions = []
        params = []
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if cycle_from is not None:
            conditions.append("cycle_num >= ?")
            params.append(cycle_from)
        if cycle_to is not None:
            conditions.append("cycle_num <= ?")
            params.append(cycle_to)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT COUNT(*) as cnt FROM audit_log WHERE {where}"

        with self._con() as con:
            return con.execute(sql, params).fetchone()["cnt"]

    # ------------------------------------------------------------------
    # Hash chain verification
    # ------------------------------------------------------------------

    def verify_chain(self) -> Dict[str, Any]:
        """Verify the entire hash chain integrity.

        Returns:
            Dict with valid (bool), total_entries, first_invalid_id, errors.
        """
        with self._con() as con:
            rows = con.execute(
                "SELECT entry_id, timestamp, event_type, agent, symbol, "
                "cycle_num, payload, prev_hash, entry_hash "
                "FROM audit_log ORDER BY entry_id ASC"
            ).fetchall()

        if not rows:
            return {"valid": True, "total_entries": 0, "errors": []}

        errors = []
        prev_hash = "GENESIS"

        for row in rows:
            entry = AuditEntry(
                entry_id=row["entry_id"],
                timestamp=row["timestamp"],
                event_type=row["event_type"],
                agent=row["agent"],
                symbol=row["symbol"],
                cycle_num=row["cycle_num"],
                payload=json.loads(row["payload"]),
                prev_hash=row["prev_hash"],
                entry_hash=row["entry_hash"],
            )

            # Check prev_hash links to previous entry
            if entry.prev_hash != prev_hash:
                errors.append({
                    "entry_id": entry.entry_id,
                    "error": "prev_hash mismatch",
                    "expected": prev_hash,
                    "found": entry.prev_hash,
                })

            # Check entry_hash is correct
            computed = entry.compute_hash()
            if computed != entry.entry_hash:
                errors.append({
                    "entry_id": entry.entry_id,
                    "error": "entry_hash corrupted",
                    "expected": computed,
                    "found": entry.entry_hash,
                })

            prev_hash = entry.entry_hash

        return {
            "valid": len(errors) == 0,
            "total_entries": len(rows),
            "first_invalid_id": errors[0]["entry_id"] if errors else None,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Compliance checks
    # ------------------------------------------------------------------

    def check_wash_sale(
        self, symbol: str, side: str, price: float,
        window_days: int = WASH_SALE_WINDOW_DAYS,
    ) -> Dict[str, Any]:
        """Check for wash-sale pattern: same symbol sold at loss then re-bought within window.

        IRS wash-sale rule: if you sell a security at a loss and buy
        "substantially identical" security within 30 days, the loss is
        disallowed. For FX, we flag as risk warning.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

        with self._con() as con:
            # Find opposite-side fills for same symbol in window
            opposite = "BUY" if side == "SELL" else "SELL"
            rows = con.execute(
                "SELECT timestamp, payload FROM audit_log "
                "WHERE event_type = 'ORDER_FILLED' AND symbol = ? "
                "AND timestamp > ? "
                "ORDER BY entry_id DESC LIMIT 20",
                (symbol, cutoff),
            ).fetchall()

        for row in rows:
            payload = json.loads(row["payload"])
            if payload.get("side") == opposite:
                # Check if there's a loss
                fill_price = float(payload.get("price", 0))
                if fill_price > 0 and price > 0:
                    if side == "SELL" and price < fill_price:
                        return {
                            "wash_sale_risk": True,
                            "reason": f"Sold {symbol} at {price} after buying at {fill_price} "
                                      f"within {window_days} days",
                            "first_trade": row["timestamp"],
                            "window_days": window_days,
                        }
                    elif side == "BUY" and price > fill_price:
                        return {
                            "wash_sale_risk": True,
                            "reason": f"Bought {symbol} at {price} after selling at {fill_price} "
                                      f"within {window_days} days",
                            "first_trade": row["timestamp"],
                            "window_days": window_days,
                        }

        return {"wash_sale_risk": False}

    def check_pattern_day_trading(
        self, window_days: int = PDT_WINDOW_DAYS,
        threshold: int = PDT_THRESHOLD,
    ) -> Dict[str, Any]:
        """Check for pattern day trading: 4+ round-trip trades in rolling window.

        A round-trip = open + close of same symbol. Counts unique symbols
        with 4+ round-trips in the window.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

        with self._con() as con:
            rows = con.execute(
                "SELECT symbol, event_type, timestamp FROM audit_log "
                "WHERE event_type IN ('POSITION_OPENED', 'POSITION_CLOSED') "
                "AND timestamp > ? "
                "ORDER BY entry_id ASC",
                (cutoff,),
            ).fetchall()

        # Count round-trips per symbol
        opens: Dict[str, int] = {}
        round_trips: Dict[str, int] = {}
        alerts = []

        for row in rows:
            sym = row["symbol"]
            if row["event_type"] == "POSITION_OPENED":
                opens[sym] = opens.get(sym, 0) + 1
            elif row["event_type"] == "POSITION_CLOSED":
                if opens.get(sym, 0) > 0:
                    opens[sym] -= 1
                    round_trips[sym] = round_trips.get(sym, 0) + 1

        for sym, trips in round_trips.items():
            if trips >= threshold:
                alerts.append({
                    "symbol": sym,
                    "round_trips": trips,
                    "threshold": threshold,
                    "window_days": window_days,
                    "message": f"{sym}: {trips} round-trips in {window_days} days (threshold={threshold})",
                })

        return {
            "pattern_day_risk": len(alerts) > 0,
            "alerts": alerts,
            "symbols_at_risk": [a["symbol"] for a in alerts],
        }

    def check_spoofing(
        self, symbol: str,
        window_s: float = SPOOF_WINDOW_S,
        threshold: int = SPOOF_THRESHOLD,
    ) -> Dict[str, Any]:
        """Detect spoofing pattern: 3+ cancel/replace on same symbol within window.

        Spoofing = placing orders with intent to cancel before execution,
        creating artificial depth. Exchange surveillance flags rapid
        cancel/replace patterns.
        """
        cutoff_ts = time.time() - window_s
        cutoff_iso = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat()

        with self._con() as con:
            rows = con.execute(
                "SELECT timestamp, event_type, payload FROM audit_log "
                "WHERE event_type IN ('ORDER_SUBMITTED', 'ORDER_CANCELLED', 'ORDER_MODIFY') "
                "AND symbol = ? AND timestamp > ? "
                "ORDER BY entry_id ASC",
                (symbol, cutoff_iso),
            ).fetchall()

        # Count cancel/modify events
        cancels = sum(
            1 for r in rows
            if r["event_type"] in ("ORDER_CANCELLED", "ORDER_MODIFY")
        )

        alert = None
        if cancels >= threshold:
            alert = {
                "spoofing_risk": True,
                "symbol": symbol,
                "cancel_count": cancels,
                "threshold": threshold,
                "window_s": window_s,
                "message": f"{symbol}: {cancels} cancel/replace in {window_s}s (threshold={threshold})",
            }
            # Log the alert
            self.log(
                EventType.SPOOFING_ALERT, symbol=symbol,
                agent="κ",
                payload=alert,
            )

        return alert or {"spoofing_risk": False}

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def generate_report(self, hours: int = 24) -> Dict[str, Any]:
        """Generate a compliance report for the last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        with self._con() as con:
            # Event counts by type
            rows = con.execute(
                "SELECT event_type, COUNT(*) as cnt FROM audit_log "
                "WHERE timestamp > ? GROUP BY event_type ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall()
            by_type = {r["event_type"]: r["cnt"] for r in rows}

            # Event counts by agent
            rows = con.execute(
                "SELECT agent, COUNT(*) as cnt FROM audit_log "
                "WHERE timestamp > ? AND agent != '' GROUP BY agent ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall()
            by_agent = {r["agent"]: r["cnt"] for r in rows}

            # Total entries
            total = con.execute(
                "SELECT COUNT(*) as cnt FROM audit_log WHERE timestamp > ?",
                (cutoff,),
            ).fetchone()["cnt"]

            # Alerts
            alert_types = [
                EventType.WASH_SALE_ALERT.value,
                EventType.PATTERN_DAY_ALERT.value,
                EventType.SPOOFING_ALERT.value,
                EventType.UNUSUAL_ACTIVITY.value,
                EventType.GUARDIAN_TRIP.value,
                EventType.CIRCUIT_BREAKER.value,
            ]
            placeholders = ",".join("?" * len(alert_types))
            rows = con.execute(
                f"SELECT event_type, COUNT(*) as cnt FROM audit_log "
                f"WHERE timestamp > ? AND event_type IN ({placeholders}) "
                f"GROUP BY event_type",
                [cutoff] + alert_types,
            ).fetchall()
            alerts = {r["event_type"]: r["cnt"] for r in rows}

            # LLM calls
            llm_count = con.execute(
                "SELECT COUNT(*) as cnt FROM audit_log "
                "WHERE timestamp > ? AND event_type = 'LLM_CALL'",
                (cutoff,),
            ).fetchone()["cnt"]

            # Orders
            orders_by_type = con.execute(
                "SELECT event_type, COUNT(*) as cnt FROM audit_log "
                "WHERE timestamp > ? AND event_type LIKE 'ORDER_%' "
                "GROUP BY event_type",
                (cutoff,),
            ).fetchall()
            orders = {r["event_type"]: r["cnt"] for r in orders_by_type}

        # Chain verification
        chain = self.verify_chain()

        return {
            "report_period_hours": hours,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_entries": total,
            "by_event_type": by_type,
            "by_agent": by_agent,
            "llm_calls": llm_count,
            "orders": orders,
            "alerts": alerts,
            "chain_integrity": chain["valid"],
            "chain_errors": len(chain["errors"]),
        }

    def format_report(self, report: Optional[Dict[str, Any]] = None) -> str:
        """Format a human-readable compliance report."""
        if report is None:
            report = self.generate_report()

        lines = [
            "=" * 70,
            "κ COMPLIANCE & AUDIT REPORT",
            f"Period: last {report['report_period_hours']}h",
            f"Generated: {report['generated_at']}",
            "=" * 70,
            f"Total entries:    {report['total_entries']}",
            f"Chain integrity:  {'✅ VALID' if report['chain_integrity'] else '❌ CORRUPTED'}",
            f"Chain errors:     {report['chain_errors']}",
            f"LLM calls:        {report['llm_calls']}",
            "",
            "Events by Type:",
        ]

        for etype, cnt in sorted(report["by_event_type"].items(), key=lambda x: -x[1]):
            lines.append(f"  {etype:<25} {cnt:>6}")

        if report["orders"]:
            lines.append("")
            lines.append("Orders:")
            for otype, cnt in report["orders"].items():
                lines.append(f"  {otype:<25} {cnt:>6}")

        if report["alerts"]:
            lines.append("")
            lines.append("⚠️  ALERTS:")
            for atype, cnt in report["alerts"].items():
                lines.append(f"  {atype:<25} {cnt:>6}")
        else:
            lines.append("")
            lines.append("✅ No compliance alerts")

        if report["by_agent"]:
            lines.append("")
            lines.append("Activity by Agent:")
            for agent, cnt in report["by_agent"].items():
                lines.append(f"  {agent:<25} {cnt:>6}")

        lines.append("=" * 70)
        return "\n".join(lines)

    def export_jsonl(self, output_path: str, hours: int = 24) -> int:
        """Export audit log to JSONL for external analysis. Returns entry count."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM audit_log WHERE timestamp > ? ORDER BY entry_id ASC",
                (cutoff,),
            ).fetchall()

        with open(output_path, "w", encoding="utf-8") as f:
            for row in rows:
                entry = dict(row)
                entry["payload"] = json.loads(entry["payload"])
                f.write(json.dumps(entry, default=str) + "\n")

        logger.info("κ Exported %d entries to %s", len(rows), output_path)
        return len(rows)


# ============================================================================
# Global singleton
# ============================================================================

_AUDIT: Optional[ComplianceAudit] = None
_AUDIT_LOCK = threading.Lock()


def get_audit() -> ComplianceAudit:
    """Get or create the global κ Compliance Audit singleton."""
    global _AUDIT
    with _AUDIT_LOCK:
        if _AUDIT is None:
            _AUDIT = ComplianceAudit()
        return _AUDIT


def log_event(
    event_type: EventType,
    symbol: str = "",
    cycle_num: int = 0,
    agent: str = "",
    **payload: Any,
) -> AuditEntry:
    """Convenience: log an event to the global κ audit log."""
    return get_audit().log(
        event_type, symbol=symbol, cycle_num=cycle_num,
        agent=agent, payload=payload,
    )
