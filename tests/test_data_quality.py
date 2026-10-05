from datetime import datetime, timedelta, timezone

from data.data_quality import DataQualityGate
import math
import pytest


def _snapshot(*, future_last=False, missing_timestamp=False):
    captured = datetime.now(timezone.utc)
    bars = []
    for index in range(25):
        timestamp = captured - timedelta(minutes=25 - index)
        if future_last and index == 24:
            timestamp = captured + timedelta(minutes=1)
        bar = {
            "timestamp": timestamp.isoformat(),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        }
        if missing_timestamp and index == 12:
            bar.pop("timestamp")
        bars.append(bar)
    return {
        "context": {
            "market_data_source": "mt5",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "source_identity": {
                "provider": "mt5",
                "server": "Test-Demo",
                "account_hash": "0123456789abcdef0123456789abcdef",
            },
            "captured_at": captured.isoformat(),
            "provider_time": captured.isoformat(),
            "clock_health": {"valid": True, "offset_seconds": 0.0},
        },
        "ohlc": bars,
        "dom": {"best_bid": 99.9, "best_ask": 100.1, "spread": 0.2},
    }


def test_live_data_quality_accepts_complete_point_in_time_bars():
    result = DataQualityGate().validate(_snapshot(), require_live_quote=True)
    assert result.passed, result.reasons


def test_live_data_quality_rejects_missing_candle_timestamp():
    result = DataQualityGate().validate(_snapshot(missing_timestamp=True), require_live_quote=True)
    assert not result.passed
    assert any("timestamp missing" in reason for reason in result.reasons)


def test_live_data_quality_rejects_future_candle_timestamp():
    result = DataQualityGate().validate(_snapshot(future_last=True), require_live_quote=True)
    assert not result.passed
    assert any("future-data risk" in reason for reason in result.reasons)


def test_live_data_quality_rejects_malformed_clock_offset():
    snapshot = _snapshot()
    snapshot["context"]["clock_health"]["offset_seconds"] = "not-a-number"
    result = DataQualityGate().validate(snapshot, require_live_quote=True)
    assert not result.passed
    assert any("clock offset is malformed" in reason for reason in result.reasons)


def test_live_data_quality_rejects_provider_time_drift():
    snapshot = _snapshot()
    snapshot["context"]["provider_time"] = (
        datetime.now(timezone.utc) - timedelta(seconds=30)
    ).isoformat()
    result = DataQualityGate().validate(snapshot, require_live_quote=True)
    assert not result.passed
    assert any("provider/local snapshot time differs" in reason for reason in result.reasons)


def test_live_data_quality_rejects_missing_source_identity():
    snapshot = _snapshot()
    snapshot["context"].pop("source_identity")
    result = DataQualityGate().validate(snapshot, require_live_quote=True)
    assert not result.passed
    assert any("source identity" in reason for reason in result.reasons)


def test_data_quality_rejects_in_run_candle_revision():
    gate = DataQualityGate()
    snapshot = _snapshot()
    assert gate.validate(snapshot, require_live_quote=True).passed
    snapshot["ohlc"][10]["close"] = 100.75
    result = gate.validate(snapshot, require_live_quote=True)
    assert not result.passed
    assert any("candle revision detected" in reason for reason in result.reasons)


def test_data_quality_ignores_forming_candle_churn():
    """A still-forming candle changes every poll; that is not provider corruption."""
    gate = DataQualityGate()
    snapshot = _snapshot()
    assert gate.validate(snapshot, require_live_quote=True).passed

    # Last bar is only one minute old, so it is still forming on M15.
    snapshot["ohlc"][-1]["close"] = 100.4
    snapshot["ohlc"][-1]["high"] = 100.9
    result = gate.validate(snapshot, require_live_quote=True)
    assert result.passed, result.reasons
    assert not any("candle revision detected" in reason for reason in result.reasons)


def test_data_quality_policy_rejects_nonfinite_thresholds():
    with pytest.raises(ValueError):
        DataQualityGate(max_clock_offset_seconds=math.inf)


def test_data_quality_rejects_malformed_ohlc_and_dom_containers():
    result = DataQualityGate().validate(
        {"context": {}, "ohlc": {"low": 1}, "dom": "invalid"},
        require_live_quote=False, require_dom=True,
    )
    assert not result.passed
    assert "OHLC container is malformed" in result.reasons
    assert "DOM container is malformed" in result.reasons


def test_data_quality_rejects_nonfinite_quote_and_malformed_dom_depth():
    snapshot = _snapshot()
    snapshot["dom"]["best_bid"] = float("nan")
    snapshot["dom"]["dom_depth"] = "deep"
    result = DataQualityGate().validate(snapshot, require_live_quote=True, require_dom=True)
    assert not result.passed
    assert "DOM unavailable or empty for DOM-required setup" in result.reasons
    assert "invalid or absent live bid/ask" in result.reasons


def _snapshot_with_gap(gap_minutes: float, bar_minutes: int = 1):
    """Snapshot whose bar series contains one injected gap after bar 12."""
    captured = datetime.now(timezone.utc)
    stamps = []
    minutes_back = 0.0
    for index in range(25):
        if index == 13:
            minutes_back += gap_minutes  # inject the gap before this bar
        stamps.append(captured - timedelta(minutes=minutes_back))
        minutes_back += bar_minutes
    stamps.reverse()  # oldest -> newest, ascending like a real feed
    bars = [
        {
            "timestamp": ts.isoformat(),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        }
        for ts in stamps
    ]
    return {
        "context": {
            "market_data_source": "mt5",
            "symbol": "XAUUSD",
            "timeframe": "M15",
            "source_identity": {
                "provider": "mt5",
                "server": "Test-Demo",
                "account_hash": "0123456789abcdef0123456789abcdef",
            },
            "captured_at": captured.isoformat(),
            "provider_time": captured.isoformat(),
            "clock_health": {"valid": True, "offset_seconds": 0.0},
        },
        "ohlc": bars,
        "dom": {"best_bid": 99.9, "best_ask": 100.1, "spread": 0.2},
    }


def test_scheduled_grid_aligned_session_gap_is_accepted():
    """XAUUSD-style daily maintenance: 4x interval gap, exactly grid-aligned."""
    result = DataQualityGate().validate(_snapshot_with_gap(4.0, bar_minutes=1), require_live_quote=True)
    gap_reasons = [r for r in result.reasons if "timestamp gap" in r]
    assert gap_reasons == [], f"grid-aligned session break must not fail: {result.reasons}"


def test_weekend_rollover_gap_is_accepted():
    """FX weekend: 60x interval gap, exactly grid-aligned."""
    result = DataQualityGate().validate(_snapshot_with_gap(60.0, bar_minutes=1), require_live_quote=True)
    gap_reasons = [r for r in result.reasons if "timestamp gap" in r]
    assert gap_reasons == []


def test_off_grid_corruption_gap_is_rejected():
    """Dropped/irregular stamps: 4.3x interval gap breaks the bar grid."""
    result = DataQualityGate().validate(_snapshot_with_gap(4.3, bar_minutes=1), require_live_quote=True)
    assert not result.passed
    assert "abnormal candle timestamp gap" in result.reasons


def test_massively_off_grid_gap_is_rejected():
    """A 7.7x off-grid gap (not a clean multiple) is still corruption."""
    result = DataQualityGate().validate(_snapshot_with_gap(7.7, bar_minutes=1), require_live_quote=True)
    assert not result.passed
    assert "abnormal candle timestamp gap" in result.reasons
