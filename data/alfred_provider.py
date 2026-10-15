"""ALFRED point-in-time provider (Archival Federal Reserve Economic Data).

Why this exists
---------------
The economic calendar cannot supply release outcomes: its feed publishes
forward-looking rows only, so ``actual`` is never populated for any event (see
``docs/CALENDAR_ACTUAL_GAP.md``). Without a release outcome,
``MacroGraph.schedule_event_reaction`` cannot start a tracker, so the entire
causal-validation path accumulates nothing.

ALFRED is FRED's vintage store. Querying with ``realtime_start`` /
``realtime_end`` returns, for each observation period, the value **as it was
known at that moment** -- including the first print, before any revision. That
is genuine point-in-time data, and it is what the spec's "Point-in-Time
Vintage Data" requirement asks for.

What this provider does NOT do
------------------------------
It does not invent a consensus forecast. A release surprise is
``actual - forecast``; ALFRED supplies the actual and the prior value, not a
market expectation. So ``forecast`` is left ``None`` here and populated only by
a source that genuinely carries consensus. A reaction whose ``surprise`` is
unknown is skipped by ``AutonomousCausalValidator`` rather than being coerced
into a directional sample -- manufacturing that number would be fabricating the
exact evidence the validator exists to verify.

Every method fails soft: this is evidence gathering, never a trading gate.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

try:  # pragma: no cover - import env dependent
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore[assignment]

log = logging.getLogger("ALFREDProvider")

ALFRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
CACHE_FILE = "artifacts/alfred_pit_cache.json"

#: FRED publishes '.' for periods with no observation (holidays, gaps).
_MISSING_TOKENS = {".", "", "-", "NaN"}

_ONE_DAY = timedelta(days=1)

#: Series whose *release* is the event of interest, mapped to the catalog's
#: EVENT_CLASS entity. Chosen because these are the releases the catalog's
#: causal edges are written against, not because they are FRED's most popular.
#: Titles are written so ``classify_runtime_event``'s lexical token map can
#: route them -- a bare series id like "CPIAUCSL" is not a headline.
DEFAULT_SERIES: Dict[str, str] = {
    "CPIAUCSL": "CPI_RELEASE",      # CPI, all urban consumers
    "CPILFESL": "CPI_RELEASE",      # CPI, all items less food and energy
    "CPITAYSL": "CPI_RELEASE",      # CPI, transportation
    "PAYEMS": "NFP_RELEASE",        # Total nonfarm payroll employment
    "UNRATE": "NFP_RELEASE",        # Unemployment rate
    "FEDFUNDS": "CB_MEETING",       # Effective federal funds rate
    "DFF": "CB_MEETING",            # Federal funds effective rate
}

#: Release headlines, used verbatim as ``title`` so downstream classification
#: resolves to the catalog EVENT_CLASS the series stands in for.
SERIES_TITLES: Dict[str, str] = {
    "CPIAUCSL": "US CPI All Items Index",
    "CPILFESL": "US Core CPI All Items Less Food and Energy",
    "CPITAYSL": "US CPI Transportation Index",
    "PAYEMS": "US Non-Farm Employment Change",
    "UNRATE": "US Unemployment Rate",
    "FEDFUNDS": "Fed Funds Effective Rate Decision",
    "DFF": "Federal Reserve Policy Rate Decision",
}


def _finite(raw: Any) -> Optional[float]:
    """Parse a FRED value, rejecting the '.' placeholder it uses for gaps."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text in _MISSING_TOKENS:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


class ALFREDProvider:
    """Point-in-time release harvesting from ALFRED."""

    def __init__(self, cache_path: str = CACHE_FILE, timeout: int = 20):
        self.cache_path = cache_path
        self.timeout = timeout
        self.last_source = "unknown"

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _api_key(self) -> Optional[str]:
        key = (os.environ.get("FRED_API_KEY") or "").strip()
        return key or None

    def fetch_vintages(
        self,
        series_id: str,
        *,
        realtime_start: str,
        realtime_end: Optional[str] = None,
        observation_start: Optional[str] = None,
        observation_end: Optional[str] = None,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        """Return observations as known within a vintage window.

        Each row carries ``realtime_start`` -- the date the value became
        publicly known -- alongside ``date`` (the period it describes).

        ``observation_start``/``observation_end`` bound *which periods* come
        back. They are not optional in practice: a bare vintage window answers
        "what was known as of 2023-01-01", which includes every period since
        1947 and would label them all as published on 2023-01-01.
        """
        key = self._api_key()
        if not key or _requests is None:
            self.last_source = "no_credentials"
            return []
        end = _safe_realtime_end(realtime_end)
        params = {
            "series_id": series_id,
            "realtime_start": realtime_start,
            "file_type": "json",
            "sort_order": "asc",
            "api_key": key,
        }
        # Omitting realtime_end entirely also works and asks for the real-time
        # maximum, but an explicit clamped bound keeps responses small.
        if end:
            params["realtime_end"] = end
        if observation_start:
            params["observation_start"] = str(observation_start)[:10]
        if observation_end:
            params["observation_end"] = str(observation_end)[:10]
        if limit:
            params["limit"] = str(int(limit))
        try:
            resp = _requests.get(ALFRED_BASE, params=params, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - evidence path is advisory
            log.warning("ALFRED fetch failed for %s: %s", series_id, exc)
            self.last_source = f"fetch_failed:{type(exc).__name__}"
            return []
        if resp.status_code != 200:
            log.warning("ALFRED HTTP %s for %s", resp.status_code, series_id)
            self.last_source = f"http_{resp.status_code}"
            return []
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("ALFRED bad JSON for %s: %s", series_id, exc)
            self.last_source = "json_failed"
            return []
        self.last_source = "live"
        rows = payload.get("observations") or []
        out: List[Dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            value = _finite(r.get("value"))
            if value is None:
                continue
            published = str(r.get("realtime_start") or "")[:10]
            period = str(r.get("date") or "")[:10]
            if not published or not period:
                continue
            out.append({
                "series_id": series_id,
                "period": period,
                "published_at": published,
                "value": value,
                "vintage_end": str(r.get("realtime_end") or "")[:10] or None,
            })
        return out

    # ------------------------------------------------------------------
    # release extraction
    # ------------------------------------------------------------------

    def first_publication(self, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Keep only each period's first-known value.

        Later vintages are revisions. This provider harvests the *initial*
        print, because that is what an event-driven system could have traded
        on; revisions are a different (also legitimate) study and are not
        mixed in here.
        """
        first: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            period = str(r.get("period") or "")
            if not period:
                continue
            cur = first.get(period)
            if cur is None or str(r.get("published_at") or "") < str(cur.get("published_at") or ""):
                first[period] = r
        return sorted(first.values(), key=lambda r: str(r.get("published_at") or ""))

    def build_releases(
        self,
        series_id: str,
        *,
        entity_class: str,
        realtime_start: str,
        realtime_end: Optional[str] = None,
        title: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Turn a series' first publications into release records.

        Each record carries ``actual`` and ``previous`` -- both genuinely
        observed -- and deliberately leaves ``forecast`` unset. See the module
        docstring: a release surprise needs a consensus we do not have.

        ``previous`` is the previously published value of the same series, so
        only periods inside the harvested window carry one; the first does not.
        """
        end = _safe_realtime_end(realtime_end) or (
            datetime.now(timezone.utc) - _ONE_DAY).strftime("%Y-%m-%d")
        rows = self.fetch_vintages(
            series_id,
            realtime_start=realtime_start,
            realtime_end=end,
            # A release event cannot predate the window it was harvested for.
            observation_start=realtime_start,
            observation_end=end,
        )
        firsts = self.first_publication(rows)
        releases: List[Dict[str, Any]] = []
        prev_value: Optional[float] = None
        for r in firsts:
            actual = _finite(r.get("value"))
            if actual is None:
                continue
            published = str(r.get("published_at"))
            if not (realtime_start <= published <= end):
                continue  # never assert a publication outside the window
            try:
                published_dt = datetime.strptime(published, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            releases.append({
                "series_id": series_id,
                "entity_class": entity_class,
                "title": title or SERIES_TITLES.get(series_id) or series_id,
                # The event happened when the number was published.
                "event_time": published_dt.isoformat(),
                "period": str(r.get("period")),
                "actual": actual,
                "previous": prev_value,
                # NOT INVENTED. No consensus source is wired here.
                "forecast": None,
                "currency": "USD",
                "source": "alfred",
                "point_in_time": True,
                "execution_authority": False,
            })
            prev_value = actual
        return releases

    def harvest(
        self,
        *,
        realtime_start: str,
        realtime_end: Optional[str] = None,
        series: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Harvest release records across the configured series."""
        end = _safe_realtime_end(realtime_end) or \
            (datetime.now(timezone.utc) - _ONE_DAY).strftime("%Y-%m-%d")
        table = series or DEFAULT_SERIES
        releases: List[Dict[str, Any]] = []
        per_series: Dict[str, int] = {}
        errors: Dict[str, str] = {}
        for series_id, entity_class in table.items():
            before = self.last_source
            try:
                got = self.build_releases(
                    series_id,
                    entity_class=entity_class,
                    realtime_start=realtime_start,
                    realtime_end=end,
                )
            except Exception as exc:  # noqa: BLE001 - one bad series must not stop the rest
                errors[series_id] = f"{type(exc).__name__}: {exc}"
                log.warning("ALFRED series %s failed: %s", series_id, exc)
                continue
            per_series[series_id] = len(got)
            releases.extend(got)
            # A transport failure is swallowed inside fetch_vintages and returns
            # an empty list, so an exception never fires and a naive caller sees
            # only "0 releases" -- indistinguishable from a quiet series. Record
            # the reason explicitly or the harvest looks healthy while empty.
            if not got and self.last_source != before and self.last_source != "live":
                errors[series_id] = self.last_source
        report = {
            "realtime_start": realtime_start,
            "realtime_end": end,
            "releases": releases,
            "release_count": len(releases),
            "per_series": per_series,
            "errors": errors,
            "last_source": self.last_source,
            "forecast_source": None,
            "note": (
                "actual and previous are observed ALFRED values. forecast is None "
                "by design -- ALFRED carries no consensus, and a reaction without "
                "a surprise is skipped by the validator rather than coerced."
            ),
            "execution_authority": False,
        }
        try:
            self._save_cache(report)
        except Exception as exc:  # noqa: BLE001
            log.debug("ALFRED cache write failed: %s", exc)
        return report

    # ------------------------------------------------------------------
    # cache
    # ------------------------------------------------------------------

    def _save_cache(self, report: Dict[str, Any]) -> None:
        directory = os.path.dirname(self.cache_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)

    def load_cache(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.cache_path):
            return None
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001
            log.debug("ALFRED cache unreadable: %s", exc)
            return None


def vintage_key(release: Dict[str, Any]) -> str:
    """Stable identity for one release, so re-harvesting never duplicates."""
    return "|".join([
        str(release.get("series_id") or ""),
        str(release.get("period") or ""),
        str(release.get("published_at") or release.get("event_time") or ""),
    ])


def default_realtime_window(years: int = 3) -> tuple:
    """A vintage window covering ``years`` back, ending safely in the past.

    FRED rejects ``realtime_end`` later than its own idea of today ("Variable
    realtime_end can not be after today's date ... unless it's equal to the
    real-time max date"). Using the machine's local date breaks that whenever
    local time runs ahead of the FRED clock, so this pins to UTC and backs off
    a day, which is always <= the real-time maximum.
    """
    now = datetime.now(timezone.utc)
    year = now.year - max(1, int(years))
    end = (now - _ONE_DAY).strftime("%Y-%m-%d")
    return f"{year}-01-01", end


def _safe_realtime_end(realtime_end: Optional[str]) -> Optional[str]:
    """Clamp a caller-supplied ``realtime_end`` into the past.

    Sending today or a future date returns HTTP 400 for the whole request, so a
    one-day-late local clock would otherwise silently harvest nothing.
    """
    if realtime_end is None:
        return None
    text = str(realtime_end).strip()[:10]
    if not text:
        return None
    try:
        requested = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    latest = datetime.now(timezone.utc) - _ONE_DAY
    return min(requested, latest).strftime("%Y-%m-%d")
