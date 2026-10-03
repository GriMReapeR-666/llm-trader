import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from market_microstructure import MicrostructureMonitor
from event_policy import symbol_currencies, evaluate as evaluate_event_policy
from trading_safety import TradingStore


def test_microstructure_quote_flicker_detection(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    monitor = MicrostructureMonitor(store, spread_spike_cooldown=3)

    now = datetime.now(timezone.utc)
    
    # Observe multiple rapid spread changes (quote flicker)
    for i in range(4):
        t = (now + timedelta(seconds=i * 0.2)).isoformat()
        spread_val = 0.0001 if i % 2 == 0 else 0.0005
        res = monitor.observe("EURUSD", {
            "captured_at": t, "available": True, "spread": spread_val,
            "dom_depth": 10, "imbalance": 0.1
        })

    assert res["flicker_detected"] is True
    assert any("flicker" in f for f in res["flags"])


def test_microstructure_retail_dom_spoofable_warning(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    monitor = MicrostructureMonitor(store)
    now = datetime.now(timezone.utc).isoformat()

    # Shallow DOM (depth = 3) produces WARNING: SPOOFABLE_RETAIL_DOM but allowed remains True
    res = monitor.observe("EURUSD", {
        "captured_at": now, "available": True, "spread": 0.0001,
        "dom_depth": 3, "imbalance": 0.1
    })
    assert res["spoofable_dom"] is True
    assert any("SPOOFABLE_RETAIL_DOM" in f for f in res["flags"])
    assert res["allowed"] is True  # WARNING level does not block trade on its own

    # Extreme imbalance (> 0.8) also triggers spoofable_dom warning
    res2 = monitor.observe("GBPUSD", {
        "captured_at": now, "available": True, "spread": 0.0001,
        "dom_depth": 10, "imbalance": 0.95
    })
    assert res2["spoofable_dom"] is True


def test_microstructure_critical_depth_rejection(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    monitor = MicrostructureMonitor(store)
    now = datetime.now(timezone.utc).isoformat()

    # Critical DOM (depth < 2) sets allowed = False
    res = monitor.observe("EURUSD", {
        "captured_at": now, "available": True, "spread": 0.0001,
        "dom_depth": 1, "imbalance": 0.1
    })
    assert res["allowed"] is False
    assert any("CRITICAL" in f for f in res["flags"])


def test_event_policy_symbol_currencies():
    assert symbol_currencies("EURUSD") == {"EUR", "USD"}
    assert symbol_currencies("GBPJPY") == {"GBP", "JPY"}
    assert symbol_currencies("XAUUSD") == {"USD"}
    assert symbol_currencies("AUDCAD") == {"AUD", "CAD"}


def test_event_policy_pre_and_post_event_blackout():
    now = datetime(2026, 8, 9, 14, 0, 0, tzinfo=timezone.utc)
    
    # High-impact USD event at 14:15 UTC (within 30-min pre-release blackout)
    events = [{
        "currency": "USD",
        "name": "CPI YoY",
        "time": "2026-08-09T14:15:00+00:00",
        "importance": 3
    }]
    
    res = evaluate_event_policy("EURUSD", events, now=now, pre_minutes=30, post_minutes=15)
    assert res["blackout"] is True
    assert len(res.get("relevant_events", [])) == 1

    # Event 60 minutes in the future is outside blackout
    far_events = [{
        "currency": "USD",
        "name": "CPI YoY",
        "time": "2026-08-09T16:00:00+00:00",
        "importance": 3
    }]
    res2 = evaluate_event_policy("EURUSD", far_events, now=now, pre_minutes=30, post_minutes=15)
    assert res2["blackout"] is False
