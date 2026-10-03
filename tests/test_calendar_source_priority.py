from datetime import datetime, timezone
from types import SimpleNamespace

from mt5_trader import collect_calendar_event_rows


class _EmptyCalendar:
    def get_events(self, **kwargs):
        return []


class _CalendarWithEvent:
    def get_events(self, **kwargs):
        return [SimpleNamespace(to_dict=lambda: {
            "time": "2026-08-07T10:00:00+00:00",
            "currency": "USD",
            "importance": 3,
            "event": "CPI",
        })]


class _FakeMT5:
    @staticmethod
    def calendar_get(start, end):
        assert start.tzinfo is None
        return [SimpleNamespace(
            time=1786096800,
            currency="USD",
            event="NFP",
            importance=3,
            forecast_value=100,
            previous_value=90,
        )]


def test_empty_wrapper_falls_back_to_mt5_calendar_without_fabricating_time():
    rows, source = collect_calendar_event_rows(
        _EmptyCalendar(), object(), _FakeMT5,
        now=datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
    )
    assert source == "mt5_calendar"
    assert rows[0]["event"] == "NFP"
    assert rows[0]["time"].endswith("+00:00")


def test_configured_calendar_has_priority_over_mt5_fallback():
    rows, source = collect_calendar_event_rows(_CalendarWithEvent(), object(), _FakeMT5)
    assert source == "configured_calendar"
    assert rows[0]["event"] == "CPI"


def test_missing_calendar_and_client_is_explicitly_unavailable():
    rows, source = collect_calendar_event_rows(None, None, _FakeMT5)
    assert rows == []
    assert source == "UNAVAILABLE"
