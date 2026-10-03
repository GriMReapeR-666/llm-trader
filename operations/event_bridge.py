"""Process-safe event bridge between the trading engine and every observer.

The engine calls emit() at lifecycle choke points; the bridge fans events out
to (a) the durable harness EventStream (SQLite) and (b) any registered
in-process subscribers such as the GUI websocket broadcaster. Safe to call
before init() — emissions become no-ops with a debug log instead of raising.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_stream = None  # EventStream | None
_subscribers: List[Callable[[Any], None]] = []
_stats = {"emitted": 0, "dropped": 0}


def init(db_path: Optional[str] = None) -> None:
    global _stream
    with _lock:
        if _stream is not None and getattr(_stream, "db_path", None) == db_path:
            return
        from core.harness.event_stream import EventStream
        _stream = EventStream(db_path) if db_path else EventStream()
        logger.info("event_bridge initialized (db=%s)", db_path or ":memory:")


def subscribe(handler: Callable[[Any], None]) -> Callable[[], None]:
    with _lock:
        _subscribers.append(handler)

    def unsubscribe() -> None:
        with _lock:
            if handler in _subscribers:
                _subscribers.remove(handler)

    return unsubscribe


def emit(event_type: str, payload: Optional[Dict[str, Any]] = None,
         correlation_id: str = "", session_id: str = "trading",
         actor: str = "", trace_id: str = "") -> None:
    """Emit a lifecycle event.

    Envelope v2: `actor` names the executing principal and is stamped into the
    payload; `trace_id` chains parent→child tasks and flows into the durable
    HarnessEvent so the deck can render span trees per principal.
    """
    with _lock:
        stream = _stream
        subs = list(_subscribers)
    if stream is None:
        _stats["dropped"] += 1
        logger.debug("event_bridge dropped %s (not initialized)", event_type)
        return
    body = dict(payload or {})
    if actor and "actor" not in body:
        body["actor"] = actor
    try:
        event = stream.emit(event_type, body, session_id=session_id,
                            correlation_id=correlation_id, trace_id=trace_id)
    except Exception as exc:
        logger.error("event_bridge persist failed for %s: %s", event_type, exc)
        return
    _stats["emitted"] += 1
    for handler in subs:
        try:
            handler(event)
        except Exception as exc:
            logger.error("event_bridge subscriber failed for %s: %s", event_type, exc)


def stats() -> Dict[str, int]:
    return dict(_stats)
