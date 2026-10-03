"""Append-Only Event Stream & Event Sourcing Engine for Trading Harness.

Provides deterministic event logging, typed subscriptions with reversible
disposal, and replay capabilities for post-mortems and backtest verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import logging
import sqlite3
import uuid
from typing import Any, Callable, Dict, List, Optional, Union

from core.harness.disposable import Disposable

logger = logging.getLogger(__name__)


@dataclass
class HarnessEvent:
    """Immutable discrete event emitted within the trading harness."""
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = "default_session"
    correlation_id: str = ""
    trace_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HarnessEvent':
        return cls(
            event_type=data.get("event_type", "UNKNOWN"),
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            event_id=data.get("event_id", str(uuid.uuid4())),
            session_id=data.get("session_id", "default_session"),
            correlation_id=data.get("correlation_id", ""),
            trace_id=data.get("trace_id", ""),
        )


class EventStream:
    """In-memory event dispatcher with SQLite persistence and time-travel replay."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self._subscribers: Dict[str, List[Callable[[HarnessEvent], None]]] = {}
        self._global_subscribers: List[Callable[[HarnessEvent], None]] = []
        self._in_memory_log: List[HarnessEvent] = []
        if self.db_path:
            self._init_db()

    def _init_db(self) -> None:
        """Initialize append-only SQLite event table."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS harness_events (
                        event_id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        correlation_id TEXT,
                        trace_id TEXT,
                        timestamp TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_harness_events_session ON harness_events(session_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_harness_events_type ON harness_events(event_type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_harness_events_ts ON harness_events(timestamp)")
        except Exception as exc:
            logger.error("Failed to initialize harness_events database table: %s", exc)

    def subscribe(self, event_type: str, handler: Callable[[HarnessEvent], None]) -> Disposable:
        """Subscribe to a specific event type. Returns a Disposable to cleanly unsubscribe."""
        self._subscribers.setdefault(event_type, []).append(handler)

        def unsubscribe():
            handlers = self._subscribers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

        return Disposable(unsubscribe, name=f"Sub_{event_type}_{handler.__name__ if hasattr(handler, '__name__') else 'fn'}")

    def subscribe_all(self, handler: Callable[[HarnessEvent], None]) -> Disposable:
        """Subscribe to all events across the entire stream."""
        self._global_subscribers.append(handler)

        def unsubscribe():
            if handler in self._global_subscribers:
                self._global_subscribers.remove(handler)

        return Disposable(unsubscribe, name=f"GlobalSub_{handler.__name__ if hasattr(handler, '__name__') else 'fn'}")

    def emit(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        session_id: str = "default_session",
        correlation_id: str = "",
        trace_id: str = "",
    ) -> HarnessEvent:
        """Create, persist, and broadcast an event to subscribers."""
        event = HarnessEvent(
            event_type=event_type,
            payload=payload or {},
            session_id=session_id,
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        self._in_memory_log.append(event)

        # Persist to SQLite if configured
        if self.db_path:
            self._persist_event(event)

        # Dispatch to typed subscribers
        for handler in list(self._subscribers.get(event_type, [])):
            try:
                handler(event)
            except Exception as exc:
                logger.error("Error in event handler for '%s': %s", event_type, exc, exc_info=True)

        # Dispatch to global subscribers
        for handler in list(self._global_subscribers):
            try:
                handler(event)
            except Exception as exc:
                logger.error("Error in global event handler: %s", exc, exc_info=True)

        return event

    def _persist_event(self, event: HarnessEvent) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO harness_events (event_id, event_type, session_id, correlation_id, trace_id, timestamp, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        event.session_id,
                        event.correlation_id,
                        event.trace_id,
                        event.timestamp,
                        json.dumps(event.payload, default=str),
                    )
                )
        except Exception as exc:
            logger.error("Failed to persist harness event %s: %s", event.event_id, exc)

    def replay_events(
        self,
        session_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[HarnessEvent]:
        """Fetch historical events in chronological order for time-travel analysis."""
        if not self.db_path:
            events = self._in_memory_log
            if session_id:
                events = [e for e in events if e.session_id == session_id]
            if event_type:
                events = [e for e in events if e.event_type == event_type]
            return events[-limit:]

        query = "SELECT event_id, event_type, session_id, correlation_id, trace_id, timestamp, payload_json FROM harness_events WHERE 1=1"
        params: List[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        events: List[HarnessEvent] = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                for row in conn.execute(query, params):
                    try:
                        payload = json.loads(row[6])
                    except Exception:
                        payload = {}
                    events.append(HarnessEvent(
                        event_id=row[0],
                        event_type=row[1],
                        session_id=row[2],
                        correlation_id=row[3],
                        trace_id=row[4],
                        timestamp=row[5],
                        payload=payload,
                    ))
        except Exception as exc:
            logger.error("Failed to replay harness events: %s", exc)
        return events
