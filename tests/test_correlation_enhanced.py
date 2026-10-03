import os
import sqlite3
from datetime import datetime, timezone, timedelta
import pytest
from correlation_risk import CorrelationRisk

class DummyStore:
    def __init__(self, rows):
        self.rows = rows
    def open_trade_rows(self):
        return self.rows

import gc

@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "test_corr.db")
    yield db_path
    gc.collect()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass

def generate_bars(count, start_dt, base_price=100.0, trend=0.01):
    bars = []
    price = base_price
    for i in range(count):
        dt = start_dt + timedelta(hours=i)
        bars.append({
            "timestamp": int(dt.timestamp()),
            "close": price
        })
        step_trend = trend if i % 2 == 0 else -trend * 0.5
        price *= (1 + step_trend)
    return bars

def test_configurable_lookback(temp_db):
    risk = CorrelationRisk(temp_db, minimum_samples=10, lookback_bars=20)
    
    start_dt = datetime.now(timezone.utc) - timedelta(hours=50)
    bars_a = generate_bars(40, start_dt, trend=0.01)
    bars_b = generate_bars(40, start_dt, trend=0.01)
    
    risk.record_completed_bars("SYM_A", "H1", bars_a)
    risk.record_completed_bars("SYM_B", "H1", bars_b)
    
    # Should only use 20 samples due to lookback_bars
    res = risk._correlation("SYM_A", "SYM_B", "H1")
    assert res is not None
    assert res["samples"] == 20
    assert res["confidence"] == "HIGH_CONFIDENCE"

def test_low_confidence_band(temp_db):
    risk = CorrelationRisk(temp_db, minimum_samples=10, lookback_bars=50)
    
    start_dt = datetime.now(timezone.utc) - timedelta(hours=20)
    # 15 samples < 2 * 10
    bars_a = generate_bars(16, start_dt, trend=0.01)
    bars_b = generate_bars(16, start_dt, trend=0.01)
    
    risk.record_completed_bars("SYM_A", "H1", bars_a)
    risk.record_completed_bars("SYM_B", "H1", bars_b)
    
    res = risk._correlation("SYM_A", "SYM_B", "H1")
    assert res is not None
    assert res["samples"] == 15
    assert res["confidence"] == "LOW_CONFIDENCE"
    
    store = DummyStore([
        {"ticket": 1, "symbol": "SYM_B", "side": "BUY", "risk_amount": 100.0}
    ])
    
    # In low confidence, threshold drops to 0.5. The correlation is ~1.0
    risk_data = risk.correlated_open_risk(store, "SYM_A", "BUY", "H1")
    assert risk_data["pairs"][0]["source"] == "rolling_return_correlation (LOW_CONFIDENCE)"
    assert risk_data["pairs"][0]["threshold_used"] == 0.5
    assert risk_data["pairs"][0]["related"] is True

def test_staleness_detection(temp_db):
    risk = CorrelationRisk(temp_db, minimum_samples=10, lookback_bars=50, max_staleness_hours=24.0)
    
    # Generate data that is 48 hours old
    start_dt = datetime.now(timezone.utc) - timedelta(hours=100)
    bars_a = generate_bars(20, start_dt, trend=0.01)
    bars_b = generate_bars(20, start_dt, trend=0.01)
    
    risk.record_completed_bars("SYM_A", "H1", bars_a)
    risk.record_completed_bars("SYM_B", "H1", bars_b)
    
    res = risk._correlation("SYM_A", "SYM_B", "H1")
    assert res is not None
    assert res["stale"] is True
    
    store = DummyStore([
        {"ticket": 1, "symbol": "SYM_B", "side": "BUY", "risk_amount": 100.0}
    ])
    
    risk_data = risk.correlated_open_risk(store, "SYM_A", "BUY", "H1")
    assert "stale fallback" in risk_data["pairs"][0]["source"]
    # With stale fallback, it uses conservative bucket. Assuming SYM_A and SYM_B don't overlap bucket:
    # effective should be 0.0
    assert risk_data["pairs"][0]["effective_correlation"] == 0.0

def test_purge_old_returns(temp_db):
    risk = CorrelationRisk(temp_db, minimum_samples=10)
    
    # Manually insert old and new records using captured_at
    old_time = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    new_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    
    with risk._con() as con:
        con.execute("INSERT INTO correlation_returns(symbol, timeframe, bar_time, return_fraction, captured_at) VALUES(?, ?, ?, ?, ?)", 
                    ("SYM_A", "H1", "1", 0.01, old_time))
        con.execute("INSERT INTO correlation_returns(symbol, timeframe, bar_time, return_fraction, captured_at) VALUES(?, ?, ?, ?, ?)", 
                    ("SYM_A", "H1", "2", 0.01, new_time))
        con.commit()
        
    deleted = risk.purge_old_returns(days=90)
    assert deleted == 1
    
    with risk._con() as con:
        count = con.execute("SELECT COUNT(*) FROM correlation_returns").fetchone()[0]
        assert count == 1

def test_portfolio_correlation_summary(temp_db):
    risk = CorrelationRisk(temp_db, minimum_samples=10, lookback_bars=50)
    
    start_dt = datetime.now(timezone.utc) - timedelta(hours=20)
    bars_a = generate_bars(20, start_dt, trend=0.01)
    bars_b = generate_bars(20, start_dt, trend=0.01)
    bars_c = generate_bars(20, start_dt, trend=-0.01) # Inverse correlation
    
    risk.record_completed_bars("SYM_A", "H1", bars_a)
    risk.record_completed_bars("SYM_B", "H1", bars_b)
    risk.record_completed_bars("SYM_C", "H1", bars_c)
    
    store = DummyStore([
        {"ticket": 1, "symbol": "SYM_A", "side": "BUY", "risk_amount": 100.0},
        {"ticket": 2, "symbol": "SYM_B", "side": "BUY", "risk_amount": 150.0},
        {"ticket": 3, "symbol": "SYM_C", "side": "BUY", "risk_amount": 200.0}
    ])
    
    summary = risk.portfolio_correlation_summary(store, "H1")
    
    matrix = summary["matrix"]
    assert len(matrix) == 3
    assert 2 in matrix[1]["correlations"]
    assert matrix[1]["correlations"][2]["related"] is True # SYM_A and SYM_B correlated
    assert matrix[1]["correlations"][3]["related"] is False # SYM_A and SYM_C inverse
    
    # correlated risk: ticket 1 and ticket 2 are correlated with each other. ticket 3 is not.
    # total risk = 100 + 150 = 250
    assert summary["total_correlated_risk"] == 250.0

def test_edge_cases(temp_db):
    risk = CorrelationRisk(temp_db, minimum_samples=10)
    
    # 1. Single position
    store = DummyStore([
        {"ticket": 1, "symbol": "SYM_A", "side": "BUY", "risk_amount": 100.0}
    ])
    summary = risk.portfolio_correlation_summary(store, "H1")
    assert summary["total_correlated_risk"] == 0.0
    assert len(summary["matrix"][1]["correlations"]) == 0
    
    # 2. No data (sparse fallback)
    risk_data = risk.correlated_open_risk(store, "SYM_B", "BUY", "H1")
    assert risk_data["pairs"][0]["source"] == "conservative_driver_bucket"
    
    # 3. Identical symbols (if they happen to be in the matrix)
    store2 = DummyStore([
        {"ticket": 1, "symbol": "SYM_A", "side": "BUY", "risk_amount": 100.0},
        {"ticket": 2, "symbol": "SYM_A", "side": "BUY", "risk_amount": 150.0}
    ])
    summary2 = risk.portfolio_correlation_summary(store2, "H1")
    # For identical symbols without data, overlap bucket should match
    assert summary2["matrix"][1]["correlations"][2]["related"] is True
    assert summary2["total_correlated_risk"] == 250.0


def test_point_in_time_observations_exclude_future_stale_and_current_symbol(temp_db):
    risk = CorrelationRisk(temp_db, max_staleness_hours=48)
    with risk._con() as con:
        con.executemany(
            "INSERT INTO correlation_returns(symbol,timeframe,bar_time,return_fraction,captured_at) VALUES(?,?,?,?,?)",
            [
                ("EURUSD", "H1", "2026-01-07T13:00:00+00:00", 0.01, "2026-01-07T13:01:00+00:00"),
                ("GBPUSD", "H1", "2026-01-07T13:00:00+00:00", -0.02, "2026-01-07T13:01:00+00:00"),
                ("XAUUSD", "H1", "2026-01-07T14:00:00+00:00", 0.50, "2026-01-07T14:01:00+00:00"),
                ("USDJPY", "H1", "2025-12-01T13:00:00+00:00", 0.40, "2025-12-01T13:01:00+00:00"),
            ],
        )
    result = risk.point_in_time_observations(
        "H1", as_of="2026-01-07T13:30:00+00:00", exclude_symbol="EURUSD",
    )
    assert sorted(result) == ["GBPUSD"]
    assert result["GBPUSD"]["return_fraction"] == -0.02


def test_point_in_time_observations_choose_newest_valid_mixed_timestamp(temp_db):
    risk = CorrelationRisk(temp_db, max_staleness_hours=48)
    with risk._con() as con:
        con.executemany(
            "INSERT INTO correlation_returns(symbol,timeframe,bar_time,return_fraction,captured_at) VALUES(?,?,?,?,?)",
            [
                ("DXY", "H1", "2026-01-07T12:00:00+00:00", 0.01, "2026-01-07T12:01:00+00:00"),
                ("DXY", "H1", "1767792600", 0.03, "2026-01-07T13:01:00+00:00"),
            ],
        )
    result = risk.point_in_time_observations(
        "H1", as_of="2026-01-07T13:30:00+00:00", exclude_symbol="EURUSD",
    )
    assert result["DXY"]["return_fraction"] == 0.03


@pytest.mark.parametrize("kwargs", [
    {"minimum_samples": 1},
    {"threshold": 1.1},
    {"lookback_bars": 5, "minimum_samples": 10},
    {"max_staleness_hours": 0},
])
def test_correlation_configuration_fails_closed(temp_db, kwargs):
    with pytest.raises(ValueError):
        CorrelationRisk(temp_db, **kwargs)


def test_unparseable_correlation_timestamp_is_stale_and_uses_fallback(temp_db):
    risk = CorrelationRisk(temp_db, minimum_samples=2, lookback_bars=2)
    with risk._con() as con:
        con.executemany(
            "INSERT INTO correlation_returns(symbol,timeframe,bar_time,return_fraction,captured_at) VALUES(?,?,?,?,?)",
            [
                ("SYM_A", "H1", "not-a-time-1", 0.01, "2026-01-01T00:00:00+00:00"),
                ("SYM_A", "H1", "not-a-time-2", 0.02, "2026-01-01T01:00:00+00:00"),
                ("SYM_B", "H1", "not-a-time-1", 0.01, "2026-01-01T00:00:00+00:00"),
                ("SYM_B", "H1", "not-a-time-2", 0.02, "2026-01-01T01:00:00+00:00"),
            ],
        )
    estimate = risk._correlation("SYM_A", "SYM_B", "H1")
    assert estimate is not None
    assert estimate["stale"] is True


# --- get_multi_asset_returns_matrix tests ---

import numpy as np


def test_get_multi_asset_returns_matrix_basic(temp_db):
    """Verify aligned multi-asset matrix is returned for 3 assets with overlapping bar_times."""
    risk = CorrelationRisk(temp_db, minimum_samples=2, lookback_bars=10)
    # Insert 40 aligned returns for 3 symbols at H1
    with risk._con() as con:
        for i in range(40):
            bt = f"2026-01-{i+1:02d}T00:00:00+00:00"
            con.executemany(
                "INSERT INTO correlation_returns(symbol,timeframe,bar_time,return_fraction,captured_at) VALUES(?,?,?,?,?)",
                [("EURUSD", "H1", bt, 0.001 * (i % 5 - 2), "2026-01-01T00:00:00+00:00"),
                 ("GBPUSD", "H1", bt, 0.002 * (i % 3 - 1), "2026-01-01T00:00:00+00:00"),
                 ("XAUUSD", "H1", bt, 0.003 * (i % 4 - 1), "2026-01-01T00:00:00+00:00")],
            )
    result = risk.get_multi_asset_returns_matrix("H1", lookback=30, min_assets=2)
    assert result is not None
    matrix, assets = result
    assert isinstance(matrix, np.ndarray)
    assert matrix.ndim == 2
    assert matrix.shape[1] == len(assets)
    assert len(assets) >= 2
    assert set(assets).issubset({"EURUSD", "GBPUSD", "XAUUSD"})
    assert matrix.shape[0] == 30  # lookback aligned samples
    # All values must be finite
    assert np.all(np.isfinite(matrix))


def test_get_multi_asset_returns_matrix_insufficient_assets(temp_db):
    """Returns None when only 1 symbol has data (min_assets=2)."""
    risk = CorrelationRisk(temp_db, minimum_samples=2, lookback_bars=10)
    with risk._con() as con:
        for i in range(40):
            bt = f"2026-01-{i+1:02d}T00:00:00+00:00"
            con.execute(
                "INSERT INTO correlation_returns(symbol,timeframe,bar_time,return_fraction,captured_at) VALUES(?,?,?,?,?)",
                ("EURUSD", "H1", bt, 0.001 * (i % 5 - 2), "2026-01-01T00:00:00+00:00"),
            )
    result = risk.get_multi_asset_returns_matrix("H1", lookback=30, min_assets=2)
    assert result is None


def test_get_multi_asset_returns_matrix_insufficient_overlap(temp_db):
    """Returns None when assets have non-overlapping bar_times."""
    risk = CorrelationRisk(temp_db, minimum_samples=2, lookback_bars=10)
    with risk._con() as con:
        # EURUSD has times 1-20, GBPUSD has times 21-40 — no overlap
        for i in range(20):
            con.execute(
                "INSERT INTO correlation_returns(symbol,timeframe,bar_time,return_fraction,captured_at) VALUES(?,?,?,?,?)",
                ("EURUSD", "H1", f"2026-01-{i+1:02d}T00:00:00+00:00", 0.001, "2026-01-01T00:00:00+00:00"),
            )
        for i in range(20):
            con.execute(
                "INSERT INTO correlation_returns(symbol,timeframe,bar_time,return_fraction,captured_at) VALUES(?,?,?,?,?)",
                ("GBPUSD", "H1", f"2026-01-{i+21:02d}T00:00:00+00:00", 0.002, "2026-01-01T00:00:00+00:00"),
            )
    result = risk.get_multi_asset_returns_matrix("H1", lookback=30, min_assets=2)
    assert result is None


def test_get_multi_asset_returns_matrix_empty_db(temp_db):
    """Returns None on empty database."""
    risk = CorrelationRisk(temp_db, minimum_samples=2, lookback_bars=10)
    result = risk.get_multi_asset_returns_matrix("H1")
    assert result is None


def test_get_multi_asset_returns_matrix_finite_check(temp_db):
    """No NaN or Inf values should leak into the copula matrix."""
    risk = CorrelationRisk(temp_db, minimum_samples=2, lookback_bars=10)
    with risk._con() as con:
        for i in range(30):
            bt = f"2026-01-{i+1:02d}T00:00:00+00:00"
            con.executemany(
                "INSERT INTO correlation_returns(symbol,timeframe,bar_time,return_fraction,captured_at) VALUES(?,?,?,?,?)",
                [("A", "M15", bt, 0.01 * ((-1) ** i), "2026-01-01T00:00:00+00:00"),
                 ("B", "M15", bt, -0.005 * ((-1) ** i), "2026-01-01T00:00:00+00:00")],
            )
    result = risk.get_multi_asset_returns_matrix("M15", lookback=25)
    assert result is not None
    matrix, assets = result
    assert np.all(np.isfinite(matrix))
