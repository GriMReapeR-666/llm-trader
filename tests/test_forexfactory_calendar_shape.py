"""P0-4: ForexFactory calendar timestamp-shape regression tests.

The live FF feed puts a full ISO offset timestamp in "date" with no separate
"time" field, which used to leave ``datetime_utc=None`` on every row while
``_is_upcoming()`` still called them upcoming - so the get_events staleness
guard refused the calendar and macro_events rows lost event_time. These pin the
fixed behavior, including the two ways the fix can silently lie: emitting a
non-UTC string in a field named ``datetime_utc``, and dropping rows with no
timestamp at all.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.forexfactory_provider import (  # noqa: E402
    ForexFactoryEvent, ForexFactoryProvider, clock_to_24h, parse_utc,
)


class _FakeNoNetwork(ForexFactoryProvider):
    """Provider stub that never touches the network or the cache file."""

    def __init__(self, cache_rows=None, source: str = "live"):
        self.cache_rows = cache_rows or []
        self.last_source = source
        self.cache_path = ""
        self.timeout = 8
        self.unparsed_rows = 0

    def _fetch_raw_calendar(self):
        self.last_source = "live" if self.last_source != "cache" else "cache"
        return self.cache_rows

    def _save_cache(self, data):
        pass

    def _load_cache(self):
        return self.cache_rows if self.last_source == "cache" else None


NOW = datetime.now(timezone.utc)


def _future_iso(offset_hours: int = 3, tz_offset_hours: int = -4) -> str:
    """An instant expressed in a non-UTC offset, as the modern feed publishes."""
    # Anchored to the module-level NOW constant: some tests assert exact
    # instant equality between two _future_iso() calls. Window-boundary tests
    # build their rows from a fresh per-test anchor instead (see
    # TestUpcomingHighImpactWindow) — a collection-time anchor would let an
    # event elapse in a multi-hour suite run.
    return (NOW + timedelta(hours=offset_hours)).astimezone(
        timezone(timedelta(hours=tz_offset_hours))).isoformat()


def _high(country="USD", **extra) -> dict:
    row = {"title": "Test FOMC", "country": country,
           "date": _future_iso(3), "impact": "High",
           "forecast": "", "previous": ""}
    row.update(extra)
    return row


class TestModernFeedShape:
    """The audited broken shape: date=full ISO with offset, no time field."""

    def test_datetime_utc_populated_without_time_field(self):
        prov = _FakeNoNetwork([_high()])
        results = prov.fetch_calendar(min_impact="HIGH")
        assert results, "modern-shape row filtered out entirely"
        assert results[0].datetime_utc is not None
        assert parse_utc(results[0].datetime_utc) is not None

    def test_datetime_utc_is_actually_utc(self):
        """A field named datetime_utc must not carry a -04:00 offset.

        macro_events.event_time and the get_events sort both read it as UTC, so
        handing the raw offset string through makes every one of them wrong by
        the offset.
        """
        prov = _FakeNoNetwork([_high()])
        value = prov.fetch_calendar()[0].datetime_utc
        assert value.endswith("+00:00")
        assert parse_utc(value).utcoffset() == timedelta(0)
        # Same instant as the source row, just re-expressed.
        assert parse_utc(value) == parse_utc(_future_iso(3))

    def test_is_upcoming_parses_offset_aware_iso(self):
        ev = ForexFactoryEvent(title="x", country="USD", currency="USD",
                               date=_future_iso(2), time="", impact="HIGH",
                               forecast="", previous="",
                               datetime_utc=_future_iso(2))
        assert ForexFactoryProvider._is_upcoming(ev) is True

    def test_get_events_does_not_raise_on_upcoming_modern_rows(self):
        prov = _FakeNoNetwork([_high(forecast="3.1")])
        events = prov.get_events(limit=5, min_importance=2,
                                 currencies=["USD", "EUR", "GBP", "JPY"])
        assert len(events) == 1
        assert events[0].datetime_utc is not None

    def test_mixed_offset_rows_sort_by_instant_not_by_string(self):
        """09:15 at -04:00 is 13:15Z; 09:00 at +00:00 is 09:00Z.

        Lexicographically the -04:00 row sorts FIRST, but it is the later
        instant, so a string sort hands the gate the wrong event first.
        """
        rows = [
            dict(_high(title="string_first_but_later"),
                 date="2099-01-02T05:15:00-04:00"),
            dict(_high(title="string_second_but_earlier"),
                 date="2099-01-02T09:00:00+00:00"),
        ]
        prov = _FakeNoNetwork(rows)
        events = prov.get_events(limit=2, min_importance=2, currencies=["USD"])
        assert [e.title for e in events] == [
            "string_second_but_earlier", "string_first_but_later"]


class TestLegacyFeedShape:
    @pytest.mark.parametrize("clock,expected_hour", [
        ("8:30am", 8), ("1:05 PM", 13), ("12:30am", 0), ("12:30pm", 12),
    ])
    def test_legacy_12h_clock_pair_normalizes(self, clock, expected_hour):
        """The legacy feed shipped 12-hour clocks; ':00Z' used to corrupt them."""
        rows = [{"title": "NFP", "country": "USD", "date": "2099-01-02",
                 "time": clock, "impact": "High", "forecast": "", "previous": ""}]
        ev = _FakeNoNetwork(rows).fetch_calendar(min_impact="HIGH")[0]
        parsed = parse_utc(ev.datetime_utc)
        assert parsed.hour == expected_hour
        assert parsed.date().isoformat() == "2099-01-02"
        assert ForexFactoryProvider._is_upcoming(ev) is True

    def test_legacy_date_time_pair_still_works(self):
        rows = [{"title": "NFP", "country": "USD", "date": "2099-01-02",
                 "time": "12:30", "impact": "High",
                 "forecast": "180K", "previous": "200K"}]
        results = _FakeNoNetwork(rows).fetch_calendar(min_impact="HIGH")
        assert parse_utc(results[0].datetime_utc).hour == 12
        assert ForexFactoryProvider._is_upcoming(results[0]) is True

    def test_clock_carrying_its_own_offset_is_not_double_shifted(self):
        rows = [{"title": "NFP", "country": "USD", "date": "2099-01-02",
                 "time": "12:30:00-05:00", "impact": "High"}]
        ev = _FakeNoNetwork(rows).fetch_calendar()[0]
        assert parse_utc(ev.datetime_utc).hour == 17   # 12:30 at -05:00

    def test_clock_to_24h_passes_through_unrecognized_text(self):
        assert clock_to_24h("All Day") == "All Day"
        assert clock_to_24h("12:30") == "12:30"


class TestStalenessSemantics:
    def test_missing_raw_returns_not_upcoming(self):
        class Empty:      # no datetime_utc, no date
            pass

        assert ForexFactoryProvider._is_upcoming(Empty()) is False
        ev = ForexFactoryEvent(title="x", country="USD", currency="USD",
                               date="garbage", time="", impact="HIGH",
                               forecast="", previous="", datetime_utc="garbage")
        assert ForexFactoryProvider._is_upcoming(ev) is False

    def test_stale_cache_rows_raise_runtimeerror(self):
        past = (NOW - timedelta(days=14)).isoformat()
        prov = _FakeNoNetwork([{"title": "old", "country": "USD", "date": past,
                                "impact": "High", "forecast": "", "previous": ""}])
        prov.last_source = "cache"
        with pytest.raises(RuntimeError):
            prov.get_events(limit=5, min_importance=2, currencies=["USD"])


class TestUnreadableRowsAreNotSilent:
    """Failing closed must not become a second silent-degradation path."""

    def test_unparseable_rows_are_counted_and_logged(self, caplog):
        rows = [_high(), {"title": "broken", "country": "USD",
                          "date": "garbage", "impact": "High"}]
        prov = _FakeNoNetwork(rows)
        with caplog.at_level("WARNING", logger="ForexFactoryProvider"):
            results = prov.fetch_calendar()
        assert prov.unparsed_rows == 1
        assert len(results) == 2                       # kept, but flagged
        assert results[1].datetime_utc is None
        assert any("no parseable timestamp" in r.message for r in caplog.records)

    def test_get_events_refuses_when_no_row_is_readable(self):
        """Every row unreadable is an unreadable calendar, not an empty one.

        Returning them as a ready calendar is the fail-open the P0-4 fix was
        supposed to remove.
        """
        prov = _FakeNoNetwork([{"title": "broken", "country": "USD",
                                "date": "garbage", "impact": "High"}])
        with pytest.raises(RuntimeError, match="parseable timestamp"):
            prov.get_events(limit=5, min_importance=2, currencies=["USD"])

    def test_get_events_never_returns_unreadable_rows(self):
        """One readable-but-elapsed row must not drag unreadable rows back in."""
        prov = _FakeNoNetwork([
            {"title": "elapsed", "country": "USD",
             "date": (NOW - timedelta(days=3)).isoformat(), "impact": "High"},
            {"title": "broken", "country": "USD", "date": "garbage", "impact": "High"},
        ])
        events = prov.get_events(limit=5, min_importance=2, currencies=["USD"])
        assert [e.title for e in events] == ["elapsed"]

    def test_high_impact_gate_refuses_an_unreadable_calendar(self):
        """market_data_provider blocks trading when this raises."""
        prov = _FakeNoNetwork([{"title": "broken", "country": "USD",
                                "date": "garbage", "impact": "High"}])
        with pytest.raises(RuntimeError, match="parseable timestamp"):
            prov.get_upcoming_high_impact_events(window_minutes=60)


class TestUpcomingHighImpactWindow:
    # Fresh anchor taken at test-execution time, not the module-level NOW
    # (frozen at collection): in a multi-hour serial suite run the test body
    # executes long after collection, and a collection-time anchor would push
    # the "+1h" event OUT of the 60-minute window by then.
    LIVE_NOW = datetime.now(timezone.utc)

    def _live_future(self, hours: float) -> str:
        return (self.LIVE_NOW + timedelta(hours=hours)).isoformat()

    def test_window_minutes_is_honored(self):
        """It used to return the whole week regardless of time, so the caller
        (market_data_provider._has_upcoming_high_impact_event) saw an imminent
        blackout for a week."""
        prov = _FakeNoNetwork([
            {"title": "soon", "country": "USD", "date": self._live_future(1),
             "impact": "High"},
            {"title": "later", "country": "USD", "date": self._live_future(72),
             "impact": "High"},
            {"title": "elapsed", "country": "USD",
             "date": (self.LIVE_NOW - timedelta(days=1)).isoformat(), "impact": "High"},
        ])
        assert [e.title for e in prov.get_upcoming_high_impact_events(60)] == ["soon"]

    def test_no_events_in_window_is_not_an_error(self):
        prov = _FakeNoNetwork([
            {"title": "later", "country": "USD", "date": self._live_future(72),
             "impact": "High"},
        ])
        assert prov.get_upcoming_high_impact_events(60) == []


class TestImpactNormalization:
    def test_medium_and_holiday_spellings_map(self):
        rows = [_high(title="PMI", country="EUR", impact="Medium"),
                _high(title="Holiday", country="CNY", impact="Holiday")]
        results = _FakeNoNetwork(rows).fetch_calendar()
        impacts = {r.title: r.impact for r in results}
        assert impacts["PMI"] == "MEDIUM"
        assert impacts["Holiday"] == "HOLIDAY"
