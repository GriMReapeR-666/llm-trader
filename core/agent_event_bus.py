"""
Agent Event Bus — cross-process structured event stream for the UAQP graph.

Provides a SQLite-backed append-only log of every agent transition,
pipeline stage, and decision in the trading loop. The unified TUI reads
from this bus in real-time to show live agent transitions.

Architecture:
  Writer (trading loop): emit() → INSERT INTO agent_events
  Reader (TUI):          poll() → SELECT * WHERE id > last_read_id

The bus is a single SQLite file (agent_events.db) with WAL mode for
concurrent read/write from separate processes.

Usage:
    from core.agent_event_bus import get_bus, AgentEvent

    bus = get_bus()
    bus.emit("ω", "CYCLE_START", "cycle_started", {"symbol": "EURUSD", "cycle": 42})
    bus.emit("δ", "DATA_COLLECT", "data_fetched", {"bars": 100})
    bus.emit("σ", "SIGNAL_BUILD", "feature_snapshot", {"features": 45})
    bus.emit("ω", "LLM_DECISION", "proposal_received", {"action": "BUY", "confidence": 0.82})
    bus.emit("ρ", "RISK_CHECK", "position_sized", {"volume": 0.05, "risk": 15.0})
    bus.emit("ε", "ORDER_SUBMIT", "order_placed", {"ticket": 12345, "type": "LIMIT"})
    bus.emit("μ", "MONITOR", "decay_check", {"health": "HEALTHY"})
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    agent       TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    stage       TEXT    NOT NULL,
    payload     TEXT    NOT NULL DEFAULT '{}',
    cycle_num   INTEGER,
    symbol      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON agent_events(id);
CREATE INDEX IF NOT EXISTS idx_events_agent ON agent_events(agent);
"""


@dataclass
class AgentEvent:
    """A single agent transition event."""
    id: int = 0
    timestamp: str = ""
    agent: str = ""
    action: str = ""
    stage: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    cycle_num: Optional[int] = None
    symbol: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "agent": self.agent,
            "action": self.action,
            "stage": self.stage,
            "payload": self.payload,
            "cycle_num": self.cycle_num,
            "symbol": self.symbol,
        }


# ---------------------------------------------------------------------------
# Agent Event Bus (singleton per process)
# ---------------------------------------------------------------------------

_DEFAULT_DB = "agent_events.db"
_bus_instance: Optional["AgentEventBus"] = None
_bus_lock = threading.Lock()


class AgentEventBus:
    """SQLite-backed append-only event bus for agent transitions."""

    def __init__(self, db_path: str = _DEFAULT_DB):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _init_db(self):
        """Create schema if not exists."""
        con = sqlite3.connect(self.db_path, timeout=5)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.executescript(_SCHEMA)
        con.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection with performance pragmas."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, timeout=5)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn.execute("PRAGMA cache_size=-32000")
            self._local.conn.execute("PRAGMA temp_store=MEMORY")
        return self._local.conn

    def emit(
        self,
        agent: str,
        action: str,
        stage: str,
        payload: Optional[Dict[str, Any]] = None,
        cycle_num: Optional[int] = None,
        symbol: Optional[str] = None,
    ) -> int:
        """Emit an event to the bus. Returns the event id."""
        import json
        ts = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload or {}, default=str)

        try:
            con = self._get_conn()
            cur = con.execute(
                "INSERT INTO agent_events (timestamp, agent, action, stage, payload, cycle_num, symbol) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, agent, action, stage, payload_json, cycle_num, symbol),
            )
            con.commit()
            return cur.lastrowid
        except Exception:
            return -1

    def poll(self, since_id: int = 0, limit: int = 50) -> List[AgentEvent]:
        """Read events since the given id."""
        import json
        try:
            con = self._get_conn()
            rows = con.execute(
                "SELECT id, timestamp, agent, action, stage, payload, cycle_num, symbol "
                "FROM agent_events WHERE id > ? ORDER BY id ASC LIMIT ?",
                (since_id, limit),
            ).fetchall()
            events = []
            for r in rows:
                try:
                    payload = json.loads(r[5])  # r[5] = payload column
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                events.append(AgentEvent(
                    id=r[0], timestamp=r[1], agent=r[2], action=r[3],
                    stage=r[4], payload=payload,
                    cycle_num=r[6], symbol=r[7],
                ))
            return events
        except Exception:
            return []

    def latest_id(self) -> int:
        """Get the latest event id."""
        try:
            con = self._get_conn()
            row = con.execute("SELECT MAX(id) FROM agent_events").fetchone()
            return row[0] if row and row[0] else 0
        except Exception:
            return 0

    def clear(self):
        """Clear all events (for testing)."""
        try:
            con = self._get_conn()
            con.execute("DELETE FROM agent_events")
            con.commit()
        except Exception:
            pass

    def count(self) -> int:
        """Count total events."""
        try:
            con = self._get_conn()
            row = con.execute("SELECT COUNT(*) FROM agent_events").fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def cycle_list(self, symbol: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Return list of distinct cycles with event counts and time range."""
        try:
            con = self._get_conn()
            where = "WHERE cycle_num IS NOT NULL"
            params: list = []
            if symbol:
                where += " AND symbol = ?"
                params.append(symbol)
            rows = con.execute(
                f"SELECT cycle_num, symbol, MIN(timestamp) as start_ts, "
                f"MAX(timestamp) as end_ts, COUNT(*) as n_events "
                f"FROM agent_events {where} "
                f"GROUP BY cycle_num, symbol ORDER BY cycle_num DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [
                {
                    "cycle_num": r[0], "symbol": r[1],
                    "start": r[2], "end": r[3], "n_events": r[4],
                }
                for r in rows
            ]
        except Exception:
            return []

    def events_for_cycle(self, cycle_num: int, symbol: str = None) -> List[AgentEvent]:
        """Return all events for a specific cycle."""
        import json
        try:
            con = self._get_conn()
            where = "WHERE cycle_num = ?"
            params: list = [cycle_num]
            if symbol:
                where += " AND symbol = ?"
                params.append(symbol)
            rows = con.execute(
                f"SELECT id, timestamp, agent, action, stage, payload, cycle_num, symbol "
                f"FROM agent_events {where} ORDER BY id ASC",
                params,
            ).fetchall()
            events = []
            for r in rows:
                try:
                    payload = json.loads(r[5])
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                events.append(AgentEvent(
                    id=r[0], timestamp=r[1], agent=r[2], action=r[3],
                    stage=r[4], payload=payload,
                    cycle_num=r[6], symbol=r[7],
                ))
            return events
        except Exception:
            return []

    def latest_cycle_num(self) -> Optional[int]:
        """Return the most recent cycle number."""
        try:
            con = self._get_conn()
            row = con.execute(
                "SELECT MAX(cycle_num) FROM agent_events WHERE cycle_num IS NOT NULL"
            ).fetchone()
            return row[0] if row and row[0] is not None else None
        except Exception:
            return None


def get_bus(db_path: str = _DEFAULT_DB) -> AgentEventBus:
    """Get or create the singleton bus instance."""
    global _bus_instance
    if _bus_instance is None or _bus_instance.db_path != db_path:
        with _bus_lock:
            if _bus_instance is None or _bus_instance.db_path != db_path:
                _bus_instance = AgentEventBus(db_path)
    return _bus_instance
