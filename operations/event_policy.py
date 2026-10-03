"""Currency-aware, fail-closed macro-event timing policy."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List


def symbol_currencies(symbol: str) -> set[str]:
    clean = symbol.upper().replace(".", "").replace("_", "")
    if clean.startswith("XAU") or "GOLD" in clean:
        return {"USD"}
    if len(clean) >= 6:
        return {clean[:3], clean[3:6]}
    return set()


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _event_time(event: Dict[str, Any]) -> datetime:
    """Accept the normalized calendar field and common provider aliases.

    Calendar adapters do not agree on whether the scheduled instant is called
    ``time``, ``timestamp``, or ``datetime``.  A relevant event whose time is
    absent or malformed must still fail closed, so aliases are resolved here
    rather than silently treating it as an event outside the blackout window.
    """
    for key in ("time", "timestamp", "datetime", "scheduled_at", "date"):
        value = event.get(key)
        if value not in (None, ""):
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value, tz=timezone.utc)
            return _time(value)
    raise ValueError("event has no scheduled time")


def evaluate(symbol: str, events: Iterable[Dict[str, Any]], *, now: datetime | None = None,
             pre_minutes: int = 30, post_minutes: int = 15, minimum_importance: int = 2) -> Dict[str, Any]:
    """Return the relevant event state; malformed relevant records fail closed."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    currencies = symbol_currencies(symbol)
    relevant: List[Dict[str, Any]] = []
    reasons: List[str] = []
    for event in events or []:
        if not isinstance(event, dict):
            reasons.append("macro event record is not an object")
            continue
        currency = str(event.get("currency", "")).upper()
        if currency not in currencies:
            continue
        try:
            importance = int(event.get("importance", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            reasons.append("relevant macro event has malformed importance")
            continue
        if importance < minimum_importance:
            continue
        try:
            when = _event_time(event)
        except (TypeError, ValueError):
            reasons.append("relevant macro event has malformed time")
            continue
        delta = (when - now).total_seconds()
        if -post_minutes * 60 <= delta <= pre_minutes * 60:
            phase = "PRE_RELEASE" if delta > 0 else "POST_RELEASE"
            relevant.append({
                "time": when.isoformat(), "currency": currency,
                "event": event.get("event", event.get("title", "")), "phase": phase,
                "seconds_to_event": delta, "actual": event.get("actual"),
                "forecast": event.get("forecast"), "previous": event.get("previous"),
                "revision": event.get("revision"),
            })
    return {"symbol": symbol, "currencies": sorted(currencies), "blackout": bool(relevant or reasons),
            "reasons": reasons, "relevant_events": relevant, "policy": {"pre_minutes": pre_minutes, "post_minutes": post_minutes}}
