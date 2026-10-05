"""Tests for PR 4.2: Broker time, server epoch correction, and trading day.

Verifies:
- P1-H1: Daily risk limits use broker trading day; restart at 02:00 IST with UTC+2/UTC+3
  servers does not drop daily breach.
- 1a-F10: TradeManager time-stop evaluates elapsed time in broker-corrected UTC time.
- 1a-F14: DOM quote capture timestamp feeds DataQualityGate freshness check; stale DOM (>5s) fails.
- Deal close timestamps at 22:00 UTC correctly map to the broker trading day across timezones.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
import pytest

from core.broker_time import (
    broker_epoch_to_utc,
    broker_trading_day,
    broker_trading_week,
    get_broker_utc_offset_seconds,
    reset_broker_time_cache,
    set_broker_utc_offset_seconds,
    utc_to_broker_time,
)
from core.trade_manager import TradeManager
from data.data_quality import DataQualityGate


@pytest.fixture(autouse=True)
def clean_broker_time():
    reset_broker_time_cache()
    yield
    reset_broker_time_cache()


def test_broker_offset_cache_management():
    """Verify offset caching and manual override."""
    assert get_broker_utc_offset_seconds(None) == 0
    set_broker_utc_offset_seconds(7200)
    assert get_broker_utc_offset_seconds(None) == 7200
    reset_broker_time_cache()
    assert get_broker_utc_offset_seconds(None) == 0


def test_freeze_clock_at_0200_ist_with_utc2_and_utc3_servers():
    """Verify that at 02:00 IST on Day D, broker day is still Day D-1 for UTC+2/UTC+3.
    
    IST is UTC+5:30.
    At 02:00 IST on 2026-03-02:
    True UTC is 2026-03-01 20:30:00 (Day D-1).
    On UTC+2: broker time is 2026-03-01 22:30:00 -> broker day is 2026-03-01.
    On UTC+3: broker time is 2026-03-01 23:30:00 -> broker day is 2026-03-01.
    """
    # 02:00 IST on 2026-03-02 = 20:30 UTC on 2026-03-01
    now_utc = datetime(2026, 3, 1, 20, 30, 0, tzinfo=timezone.utc)

    # Test with UTC+2 server (7200s)
    set_broker_utc_offset_seconds(7200)
    assert broker_trading_day(now_utc) == "2026-03-01"
    b_time = utc_to_broker_time(now_utc)
    assert b_time.hour == 22 and b_time.minute == 30

    # Test with UTC+3 server (10800s)
    set_broker_utc_offset_seconds(10800)
    assert broker_trading_day(now_utc) == "2026-03-01"
    b_time = utc_to_broker_time(now_utc)
    assert b_time.hour == 23 and b_time.minute == 30


def test_close_at_2200_utc_lands_on_correct_trading_day():
    """Verify a deal close at 22:00 UTC lands on server midnight boundary."""
    close_utc = datetime(2026, 3, 1, 22, 0, 0, tzinfo=timezone.utc)

    # UTC+0 server -> still 2026-03-01
    set_broker_utc_offset_seconds(0)
    assert broker_trading_day(close_utc) == "2026-03-01"

    # UTC+2 server -> 2026-03-02 00:00:00 (server midnight = next trading day)
    set_broker_utc_offset_seconds(7200)
    assert broker_trading_day(close_utc) == "2026-03-02"

    # UTC+3 server -> 2026-03-02 01:00:00 (next trading day)
    set_broker_utc_offset_seconds(10800)
    assert broker_trading_day(close_utc) == "2026-03-02"

    # Broker epoch round-trip
    broker_epoch = close_utc.timestamp() + 10800
    converted_utc = broker_epoch_to_utc(broker_epoch)
    assert converted_utc == close_utc


def test_trade_manager_time_stop_server_corrected(tmp_path):
    """Verify 1a-F10: TradeManager time-stop evaluates elapsed time in broker-corrected UTC.
    
    Without broker correction, a position opened 4 hours ago on a UTC+2 server
    would appear to have elapsed only 2 hours (now_utc - (now_utc - 14400 + 7200) = 7200),
    prematurely suppressing the time stop.
    With broker correction, elapsed time is correctly calculated as 14,400s (4 hours)
    and the time stop fires.
    """
    set_broker_utc_offset_seconds(7200)  # UTC+2 server
    
    current_utc_ts = 1700000000.0
    # Position opened 4 hours (14400s) ago in real elapsed time.
    # On MT5, pos.time is stored in broker server epoch (UTC + 7200s).
    pos_broker_open_time = (current_utc_ts - 14400.0) + 7200.0

    pos = NS(
        ticket=1001,
        symbol="EURUSD",
        type=0,  # BUY
        price_open=1.10000,
        sl=1.09500,  # 50 pips risk
        tp=1.11000,
        volume=0.1,
        time=pos_broker_open_time,
    )

    tm = TradeManager(
        client=MagicMock(),
        risk_manager=MagicMock(),
        state_file=str(tmp_path / "tm.json"),
        conditions_file=str(tmp_path / "tc.json"),
    )
    tm.paper_broker = None  # Ensure live mode
    tm.time_stop_seconds = 14400
    tm._close_position = MagicMock()
    tm._authorize_management_action = MagicMock(return_value=(True, "OK"))

    with patch("time.time", return_value=current_utc_ts):
        # Current price = 1.10010 (+0.02R, stagnant trade < 0.20R)
        tm._check_time_stop(pos, current_price=1.10010, atr=0.0010)

    # Verify time stop fired
    tm._close_position.assert_called_once_with(1001, "EURUSD", "TimeStopExit")


def test_trade_manager_time_stop_not_fired_before_threshold(tmp_path):
    """Verify time-stop does not fire if elapsed time is less than threshold."""
    set_broker_utc_offset_seconds(7200)  # UTC+2 server
    
    current_utc_ts = 1700000000.0
    # Position opened 3.5 hours (12600s) ago
    pos_broker_open_time = (current_utc_ts - 12600.0) + 7200.0

    pos = NS(
        ticket=1002,
        symbol="EURUSD",
        type=0,
        price_open=1.10000,
        sl=1.09500,
        tp=1.11000,
        volume=0.1,
        time=pos_broker_open_time,
    )

    tm = TradeManager(
        client=MagicMock(),
        risk_manager=MagicMock(),
        state_file=str(tmp_path / "tm.json"),
        conditions_file=str(tmp_path / "tc.json"),
    )
    tm.paper_broker = None
    tm.time_stop_seconds = 14400
    tm._close_position = MagicMock()
    tm._authorize_management_action = MagicMock(return_value=(True, "OK"))

    with patch("time.time", return_value=current_utc_ts):
        tm._check_time_stop(pos, current_price=1.10010, atr=0.0010)

    # Should not be closed
    tm._close_position.assert_not_called()


def test_dom_captured_at_freshness_gate():
    """Verify 1a-F14: DOM's captured_at timestamp feeds DataQualityGate freshness check."""
    gate = DataQualityGate(max_snapshot_age_seconds=5.0)

    now = datetime.now(timezone.utc)
    # 30 bars of valid OHLC with strictly increasing timestamps in the past
    base_bar_time = now - timedelta(minutes=40)
    valid_ohlc = [
        {
            "open": 1.1000,
            "high": 1.1010,
            "low": 1.0990,
            "close": 1.1005,
            "volume": 100,
            "timestamp": (base_bar_time + timedelta(minutes=i)).isoformat(),
        }
        for i in range(30)
    ]

    base_payload = {
        "symbol": "EURUSD",
        "ohlc": valid_ohlc,
        "context": {
            "symbol": "EURUSD",
            "timeframe": "M1",
            "market_data_source": "mt5",
            "source_identity": {
                "provider": "mt5",
                "server": "MetaQuotes-Demo",
                "account_hash": "a1b2c3d4e5f67890",
            },
            "clock_health": {"valid": True, "offset_seconds": 0.1},
            "provider_time": now.isoformat(),
        },
    }

    # Case 1: Fresh DOM capture (1 second old)
    fresh_payload = dict(base_payload)
    fresh_payload["context"] = dict(base_payload["context"])
    fresh_payload["context"]["captured_at"] = now.isoformat()
    fresh_dom = {
        "captured_at": (now - timedelta(seconds=1.0)).isoformat(),
        "best_bid": 1.1004,
        "best_ask": 1.1005,
        "spread": 0.0001,
        "dom_depth": 5.0,
        "bids": [[1.1004, 10.0]],
        "asks": [[1.1005, 10.0]],
    }
    fresh_payload["dom"] = fresh_dom
    fresh_payload["context"]["dom"] = fresh_dom

    res = gate.validate(fresh_payload, require_live_quote=True)
    assert res.passed is True, f"Expected fresh quote to pass, got reasons: {res.reasons}"

    # Case 2: Stale DOM capture (6 seconds old)
    stale_payload = dict(base_payload)
    stale_payload["context"] = dict(base_payload["context"])
    stale_payload["context"]["captured_at"] = now.isoformat()
    stale_dom = {
        "captured_at": (now - timedelta(seconds=6.0)).isoformat(),
        "best_bid": 1.1004,
        "best_ask": 1.1005,
        "spread": 0.0001,
        "dom_depth": 5.0,
        "bids": [[1.1004, 10.0]],
        "asks": [[1.1005, 10.0]],
    }
    stale_payload["dom"] = stale_dom
    stale_payload["context"]["dom"] = stale_dom

    res = gate.validate(stale_payload, require_live_quote=True)
    assert res.passed is False
    assert any("market snapshot clock drift or stale capture" in r for r in res.reasons)
