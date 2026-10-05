"""Tests for market filters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from intelligence.market_filters import (
    RolloverWindow,
    evaluate,
    is_london_ny_overlap,
    is_london_open,
    is_ny_open,
    session_name,
)


def test_weekend_rejection():
    # Saturday
    now = datetime(2023, 1, 7, 12, 0, tzinfo=timezone.utc)
    res = evaluate(now=now)
    assert not res.allowed
    assert "weekend market closure" in res.reasons

    # Sunday morning
    now = datetime(2023, 1, 8, 12, 0, tzinfo=timezone.utc)
    res = evaluate(now=now)
    assert not res.allowed
    assert "weekend market closure" in res.reasons


def test_friday_close_protection():
    # Friday 20:00 UTC
    now = datetime(2023, 1, 6, 20, 0, tzinfo=timezone.utc)
    res = evaluate(now=now)
    assert not res.allowed
    assert "Friday close no-trade window" in res.reasons


def test_rollover_window_rejection():
    # Default 21:55 - 22:10 UTC
    # Wednesday 22:00
    now = datetime(2023, 1, 4, 22, 0, tzinfo=timezone.utc)
    res = evaluate(now=now)
    assert not res.allowed
    assert "broker rollover/maintenance no-trade window" in res.reasons


def test_asian_session_volume_skip():
    # Asian session
    now = datetime(2023, 1, 4, 2, 0, tzinfo=timezone.utc)
    
    # Should block normally
    res = evaluate(now=now, volume_ratio=0.1, symbol="EURUSD")
    assert not res.allowed
    assert "abnormally low tick-volume liquidity" in res.reasons

    # Should allow JPY
    res = evaluate(now=now, volume_ratio=0.1, symbol="USDJPY")
    assert res.allowed

    # Should allow AUD
    res = evaluate(now=now, volume_ratio=0.1, symbol="AUDUSD")
    assert res.allowed


def test_sunday_gap_protection():
    # Sunday open (22:00 UTC by default), gap protection 15m (22:00 - 22:15)
    now = datetime(2023, 1, 8, 22, 5, tzinfo=timezone.utc)
    res = evaluate(now=now)
    assert not res.allowed
    assert "Sunday gap protection window" in res.reasons

    # After gap protection
    now = datetime(2023, 1, 8, 22, 20, tzinfo=timezone.utc)
    res = evaluate(now=now)
    assert res.allowed


def test_london_ny_session_detection():
    # 9:00 UTC
    now = datetime(2023, 1, 4, 9, 0, tzinfo=timezone.utc)
    assert is_london_open(now)
    assert not is_ny_open(now)
    assert not is_london_ny_overlap(now)

    # 14:00 UTC
    now = datetime(2023, 1, 4, 14, 0, tzinfo=timezone.utc)
    assert is_london_open(now)
    assert is_ny_open(now)
    assert is_london_ny_overlap(now)

    # 18:00 UTC
    now = datetime(2023, 1, 4, 18, 0, tzinfo=timezone.utc)
    assert not is_london_open(now)
    assert is_ny_open(now)
    assert not is_london_ny_overlap(now)


def test_event_blackout_window():
    now = datetime(2023, 1, 4, 12, 0, tzinfo=timezone.utc)
    event_time = datetime(2023, 1, 4, 12, 10, tzinfo=timezone.utc)
    
    # 15 min blackout
    res = evaluate(now=now, event_timestamp=event_time, event_blackout_minutes=15)
    assert not res.allowed
    assert any("macro event blackout" in r for r in res.reasons)
    
    # Event is too far in future
    res = evaluate(now=now, event_timestamp=event_time, event_blackout_minutes=5)
    assert res.allowed
    
    # Event in the past
    event_time_past = datetime(2023, 1, 4, 11, 50, tzinfo=timezone.utc)
    res = evaluate(now=now, event_timestamp=event_time_past, event_blackout_minutes=15)
    assert res.allowed


def test_edge_cases():
    # Midnight boundary
    now = datetime(2023, 1, 5, 0, 0, tzinfo=timezone.utc)
    res = evaluate(now=now)
    assert res.allowed
    assert res.session == "ASIAN"
    
    # Rollover window config
    rw = RolloverWindow.for_broker("icmarkets")
    assert rw.start_minute_utc == 21 * 60 + 59
    assert rw.end_minute_utc == 22 * 60 + 5
