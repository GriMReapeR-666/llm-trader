import pytest
from datetime import datetime, timezone, timedelta
from intelligence.market_microstructure import MicrostructureMonitor

class MockStore:
    def __init__(self):
        self.state = {}
    def get_state(self, key, default=None):
        return self.state.get(key, default)
    def set_state(self, key, value):
        self.state[key] = value

def test_rolling_median_spread():
    store = MockStore()
    monitor = MicrostructureMonitor(store, spread_rolling_window=5)
    
    # Send 5 observations with spread 1.0
    for i in range(5):
        dom = {
            "available": True,
            "captured_at": (datetime.now(timezone.utc) + timedelta(seconds=i)).isoformat(),
            "spread": 1.0,
            "dom_depth": 10,
        }
        res = monitor.observe("EURUSD", dom)
        assert res["allowed"] is True
    
    # 6th observation with spread 2.1 (abnormal)
    dom = {
        "available": True,
        "captured_at": (datetime.now(timezone.utc) + timedelta(seconds=6)).isoformat(),
        "spread": 2.1,
        "dom_depth": 10,
    }
    res = monitor.observe("EURUSD", dom)
    assert res["allowed"] is False
    assert "abnormal spread expansion" in res["flags"]

def test_consecutive_quote_gap():
    store = MockStore()
    monitor = MicrostructureMonitor(store, max_quote_gap_seconds=5.0)
    
    dt = datetime.now(timezone.utc)
    
    # First obs
    dom = {"available": True, "captured_at": dt.isoformat(), "spread": 1.0, "dom_depth": 10}
    monitor.observe("EURUSD", dom)
    
    # Gap 1 (6 seconds)
    dt += timedelta(seconds=6)
    dom["captured_at"] = dt.isoformat()
    res = monitor.observe("EURUSD", dom)
    assert "quote/DOM gap exceeds threshold" in res["flags"]
    assert "persistent quote gaps" not in res["flags"]
    assert res["allowed"] is False
    
    # Gap 2 (6 seconds)
    dt += timedelta(seconds=6)
    dom["captured_at"] = dt.isoformat()
    res = monitor.observe("EURUSD", dom)
    assert "persistent quote gaps" not in res["flags"]
    
    # Gap 3 (6 seconds)
    dt += timedelta(seconds=6)
    dom["captured_at"] = dt.isoformat()
    res = monitor.observe("EURUSD", dom)
    assert "persistent quote gaps" in res["flags"]
    assert res["allowed"] is False

def test_spread_spike_cooldown():
    store = MockStore()
    monitor = MicrostructureMonitor(store, spread_spike_cooldown=3)
    
    dt = datetime.now(timezone.utc)
    for i in range(3):
        dt += timedelta(seconds=1)
        monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 1.0, "dom_depth": 10})
        
    # Spike
    dt += timedelta(seconds=1)
    res = monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 2.5, "dom_depth": 10})
    assert "abnormal spread expansion" in res["flags"]
    
    # Cooldown 1
    dt += timedelta(seconds=1)
    res = monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 1.0, "dom_depth": 10})
    assert "abnormal spread expansion" in res["flags"]
    
    # Cooldown 2
    dt += timedelta(seconds=1)
    res = monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 1.0, "dom_depth": 10})
    assert "abnormal spread expansion" in res["flags"]
    
    # Cooldown 3
    dt += timedelta(seconds=1)
    res = monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 1.0, "dom_depth": 10})
    assert "abnormal spread expansion" not in res["flags"]

def test_dom_depth_warning_levels():
    store = MockStore()
    monitor = MicrostructureMonitor(store)
    
    dt = datetime.now(timezone.utc)
    
    # Normal
    res = monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 1.0, "dom_depth": 5})
    assert res["allowed"] is True
    
    # Warning < 5
    dt += timedelta(seconds=1)
    res = monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 1.0, "dom_depth": 4})
    assert "WARNING: DOM depth < 5" in res["flags"]
    assert res["allowed"] is True # Only WARNING
    
    # Critical < 2
    dt += timedelta(seconds=1)
    res = monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 1.0, "dom_depth": 1})
    assert "CRITICAL: DOM depth < 2" in res["flags"]
    assert res["allowed"] is False

def test_reset_clears_rolling_state():
    store = MockStore()
    monitor = MicrostructureMonitor(store)
    
    dt = datetime.now(timezone.utc)
    monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 1.0, "dom_depth": 10})
    
    assert store.get_state("microstructure:EURUSD") != {}
    monitor.reset("EURUSD")
    assert store.get_state("microstructure:EURUSD") == {}
    
def test_edge_cases():
    store = MockStore()
    monitor = MicrostructureMonitor(store)
    
    dt = datetime.now(timezone.utc)
    
    # First obs zero spread
    res = monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": 0.0, "dom_depth": 10})
    assert res["allowed"] is True
    
    # Negative spread
    dt += timedelta(seconds=1)
    res = monitor.observe("EURUSD", {"available": True, "captured_at": dt.isoformat(), "spread": -1.0, "dom_depth": 10})
    assert res["allowed"] is False
    assert "invalid spread" in res["flags"]


def test_missing_timestamp_and_nonfinite_dom_values_fail_closed():
    store = MockStore()
    monitor = MicrostructureMonitor(store)
    res = monitor.observe("EURUSD", {
        "available": True, "spread": float("nan"), "dom_depth": float("inf"),
        "imbalance": float("nan"),
    })
    assert res["allowed"] is False
    assert "missing or invalid DOM capture timestamp" in res["flags"]
    assert "invalid spread" in res["flags"]
    assert "invalid DOM depth" in res["flags"]
    assert "invalid DOM imbalance" in res["flags"]
