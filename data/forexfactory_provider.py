"""Forex Factory Data Provider.

Fetches real-time Forex Factory economic calendar data and news feeds with
point-in-time validation, impact classification, and fail-closed local caching.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import requests

log = logging.getLogger("ForexFactoryProvider")

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_FILE = "artifacts/forexfactory_calendar_cache.json"


@dataclass
class ForexFactoryEvent:
    title: str
    country: str
    currency: str
    date: str
    time: str
    impact: str  # "HIGH", "MEDIUM", "LOW", "HOLIDAY", "NON_ECONOMIC"
    forecast: str
    previous: str
    actual: Optional[str] = None
    datetime_utc: Optional[str] = None
    source: str = "forexfactory_json"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Built-in baseline events for offline resilience
FALLBACK_EVENTS: List[Dict[str, Any]] = [
    {
        "title": "Federal Funds Rate & FOMC Statement",
        "country": "USD",
        "currency": "USD",
        "date": "2026-08-15",
        "time": "18:00",
        "impact": "HIGH",
        "forecast": "5.25%",
        "previous": "5.25%",
        "actual": None,
    },
    {
        "title": "Non-Farm Employment Change",
        "country": "USD",
        "currency": "USD",
        "date": "2026-08-16",
        "time": "12:30",
        "impact": "HIGH",
        "forecast": "175K",
        "previous": "206K",
        "actual": None,
    },
    {
        "title": "ECB Main Refinancing Rate",
        "country": "EUR",
        "currency": "EUR",
        "date": "2026-08-17",
        "time": "12:15",
        "impact": "HIGH",
        "forecast": "3.75%",
        "previous": "3.75%",
        "actual": None,
    },
    {
        "title": "BOJ Monetary Policy Statement",
        "country": "JPY",
        "currency": "JPY",
        "date": "2026-08-18",
        "time": "03:00",
        "impact": "HIGH",
        "forecast": "0.25%",
        "previous": "0.10%",
        "actual": None,
    },
]


class ForexFactoryProvider:
    """Manages ingestion of Forex Factory macroeconomic events and news."""

    def __init__(self, cache_path: str = CACHE_FILE, timeout: int = 8):
        self.cache_path = cache_path
        self.last_source = "unknown"
        self.timeout = timeout

    def fetch_calendar(
        self,
        currency: Optional[str] = None,
        min_impact: Optional[str] = None,
    ) -> List[ForexFactoryEvent]:
        """Fetch weekly calendar events from Forex Factory or fallback cache."""
        raw_events = self._fetch_raw_calendar()
        normalized: List[ForexFactoryEvent] = []

        impact_order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "HOLIDAY": 0, "NON_ECONOMIC": 0}
        min_rank = impact_order.get(str(min_impact or "").upper(), 0)

        for item in raw_events:
            title = str(item.get("title") or "").strip()
            curr = str(item.get("country") or item.get("currency") or "USD").upper()
            impact_str = str(item.get("impact") or "LOW").upper()

            if impact_str in ("RED", "HIGH"):
                impact = "HIGH"
            elif impact_str in ("ORANGE", "MEDIUM", "MED"):
                impact = "MEDIUM"
            elif impact_str in ("YELLOW", "LOW"):
                impact = "LOW"
            elif impact_str in ("GRAY", "HOLIDAY"):
                impact = "HOLIDAY"
            else:
                impact = "NON_ECONOMIC"

            if currency and curr != currency.upper():
                continue

            if min_rank > 0 and impact_order.get(impact, 0) < min_rank:
                continue

            date_str = str(item.get("date") or "")
            time_str = str(item.get("time") or "")

            ev = ForexFactoryEvent(
                title=title,
                country=curr,
                currency=curr,
                date=date_str,
                time=time_str,
                impact=impact,
                forecast=str(item.get("forecast") or ""),
                previous=str(item.get("previous") or ""),
                actual=item.get("actual"),
                datetime_utc=f"{date_str}T{time_str}Z" if date_str and time_str else None,
            )
            normalized.append(ev)

        return normalized

    @staticmethod
    def _is_upcoming(event: Any) -> bool:
        """True when the event has not already elapsed (UTC)."""
        import datetime as _dt
        raw = getattr(event, "datetime_utc", None) or getattr(event, "date", None)
        if not raw:
            return True                      # unparseable: let the gate decide
        try:
            when = _dt.datetime.fromisoformat(str(raw))
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        return when >= _dt.datetime.now(_dt.timezone.utc)

    # Impact strings -> the numeric importance the event gate filters on.
    _IMPACT_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1,
                    "HOLIDAY": 1, "NON_ECONOMIC": 0}

    def get_events(self, limit: int = 5, min_importance: int = 2,
                   currencies: Optional[Sequence[str]] = None
                   ) -> List[ForexFactoryEvent]:
        """Duck-type required by collect_calendar_event_rows().

        The collector asks every calendar for
        ``get_events(limit, min_importance, currencies)``. Until now nothing in
        production implemented that shape - MT5's Python package exposes no
        calendar API and the DLL wrapper needs terminal64.dll - so the news gate
        always resolved UNAVAILABLE and fail-closed every proposal.

        Raises on an empty result: a live feed that genuinely has no events and a
        feed that failed and found no cache are indistinguishable here, and
        reporting the latter as "ready, no events" would silently open the
        blackout gate. Failing closed is the safe direction.
        """
        if self.last_source == "fallback":
            raise RuntimeError(
                "forexfactory feed is unavailable and only hardcoded FALLBACK_EVENTS "
                "remain; refusing to gate real trading on synthetic events")
        wanted = {str(c).upper() for c in (currencies or [])}
        events = self.fetch_calendar()
        if self.last_source == "fallback":
            raise RuntimeError(
                "forexfactory feed is unavailable and only hardcoded FALLBACK_EVENTS "
                "remain; refusing to gate real trading on synthetic events")
        rows = []
        for ev in events or []:
            if wanted and str(getattr(ev, "currency", "")).upper() not in wanted:
                continue
            rank = self._IMPACT_RANK.get(str(getattr(ev, "impact", "")).upper(), 0)
            if rank < int(min_importance or 0):
                continue
            rows.append(ev)
        # A cache that no longer covers the present is not a calendar. Failing
        # closed here stops a weeks-old cache from being read as "no upcoming
        # events" while the blackout gate stays open.
        if self.last_source == "cache":
            if not any(self._is_upcoming(ev) for ev in rows):
                raise RuntimeError(
                    "forexfactory cache holds no upcoming events (newest event is "
                    "in the past); refusing to gate trading on a stale calendar")
        if not rows:
            raise RuntimeError(
                "forexfactory calendar returned no qualifying events "
                "(limit=%s min_importance=%s currencies=%s); refusing to report "
                "a ready-but-empty calendar" % (limit, min_importance, sorted(wanted))
            )
        # The weekly feed repeats rows (e.g. the same ECB speech twice), which
        # would otherwise consume the caller's limit with duplicates.
        seen = set()
        unique = []
        for ev in rows:
            key = (str(getattr(ev, "currency", "")),
                   str(getattr(ev, "title", "")).strip().lower(),
                   str(getattr(ev, "datetime_utc", "") or getattr(ev, "date", "")))
            if key in seen:
                continue
            seen.add(key)
            unique.append(ev)
        rows = unique

        # The gate wants UPCOMING events. Sorting ascending and truncating would
        # hand back past events first, so a limit of 5 could return five already-
        # elapsed events and hide the ones that matter.
        rows.sort(key=lambda e: (str(getattr(e, "datetime_utc", "") or
                                   getattr(e, "date", "") or "")))
        if not limit:
            return rows
        upcoming = [e for e in rows if self._is_upcoming(e)]
        return (upcoming or rows)[:int(limit)]

    def get_upcoming_high_impact_events(
        self,
        window_minutes: int = 180,
        currency: Optional[str] = None,
    ) -> List[ForexFactoryEvent]:
        """Return high impact events occurring near current time."""
        events = self.fetch_calendar(currency=currency, min_impact="HIGH")
        return [e for e in events if e.impact == "HIGH"]

    def _fetch_raw_calendar(self) -> List[Dict[str, Any]]:
        """Fetch raw JSON payload with fallback caching."""
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LLMTrader/2.0"}
            resp = requests.get(FF_CALENDAR_URL, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data:
                    self._save_cache(data)
                    self.last_source = "live"
                    return data
        except Exception as exc:
            log.warning("Live Forex Factory fetch failed: %s; reading local cache", exc)

        cached = self._load_cache()
        if cached:
            self.last_source = "cache"
            return cached
        # Hardcoded placeholder events. get_events() refuses to gate trading on
        # these; they are not a substitute for a real feed.
        self.last_source = "fallback"
        return FALLBACK_EVENTS

    def _save_cache(self, data: List[Dict[str, Any]]) -> None:
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            log.debug("Could not write Forex Factory cache: %s", exc)

    def _load_cache(self) -> Optional[List[Dict[str, Any]]]:
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        return data
            except Exception as exc:
                log.debug("JSON operation failed: %s", exc)
        return None
