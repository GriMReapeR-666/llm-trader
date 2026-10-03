from datetime import datetime, timezone

from event_policy import evaluate


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def test_malformed_relevant_importance_fails_closed():
    result = evaluate(
        "EURUSD",
        [{"currency": "USD", "importance": "high", "time": NOW.isoformat()}],
        now=NOW,
    )
    assert result["blackout"] is True
    assert "malformed importance" in result["reasons"][0]


def test_non_object_calendar_record_fails_closed():
    result = evaluate("EURUSD", ["not-an-event"], now=NOW)
    assert result["blackout"] is True
    assert "not an object" in result["reasons"][0]


def test_irrelevant_malformed_event_does_not_change_symbol_policy():
    result = evaluate(
        "EURUSD",
        [{"currency": "JPY", "importance": "high", "time": NOW.isoformat()}],
        now=NOW,
    )
    assert result["blackout"] is False
    assert result["relevant_events"] == []
