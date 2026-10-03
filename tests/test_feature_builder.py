from datetime import datetime, timedelta, timezone

from feature_builder import build_snapshot
from feature_schema import validate_snapshot


def _bars(at):
    return [
        {
            "timestamp": (at - timedelta(minutes=15 * (25 - index))).isoformat(),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0 + index * 0.01,
            "tick_volume": 100,
        }
        for index in range(25)
    ]


def _indicators():
    values = [100.0 + index * 0.01 for index in range(25)]
    return {
        "ema_50": values,
        "rsi_14": [50.0] * 25,
        "atr_14": [0.5] * 25,
        "adx_14": [25.0] * 25,
        "macd": {
            "line": [0.1] * 25,
            "signal": [0.05] * 25,
            "histogram": [0.05] * 25,
        },
    }


def test_session_liquidity_flags_use_completed_candle_timestamp():
    # 13:45 UTC is in both the London and New York windows.
    snapshot = build_snapshot(
        _bars(datetime(2026, 1, 7, 13, 45, tzinfo=timezone.utc)),
        _indicators(), timeframe="M15", source="mt5",
        captured_at="2026-01-07T13:46:00+00:00",
    )
    assert snapshot["ready"] is True
    assert snapshot["values"]["london_open_window"] is True
    assert snapshot["values"]["new_york_open_window"] is True
    assert snapshot["values"]["london_new_york_overlap"] is True
    assert snapshot["values"]["sma_20"] > 0
    assert snapshot["values"]["atr_percentile_90d"] == 1.0
    assert validate_snapshot(snapshot)["valid"] is True


def test_feature_lineage_rejects_timeframe_or_source_drift():
    snapshot = build_snapshot(
        _bars(datetime(2026, 1, 7, 13, 45, tzinfo=timezone.utc)),
        _indicators(), timeframe="M15", source="mt5",
        captured_at="2026-01-07T13:46:00+00:00",
    )
    snapshot["lineage"]["source"] = "other-source"
    result = validate_snapshot(snapshot)
    assert result["valid"] is False
    assert any("lineage source" in reason for reason in result["reasons"])


def test_event_proximity_uses_timestamped_high_impact_calendar_rows():
    snapshot = build_snapshot(
        _bars(datetime(2026, 1, 7, 13, 45, tzinfo=timezone.utc)),
        _indicators(), timeframe="M15", source="mt5",
        captured_at="2026-01-07T13:46:00+00:00",
        event_times=[{
            "time": "2026-01-07T14:16:00+00:00",
            "importance": 3,
            "event": "CPI",
        }],
    )
    assert snapshot["values"]["high_impact_event_proximity_minutes"] == 30.0
    assert snapshot["values"]["high_impact_event_within_60m"] is True


def test_event_proximity_excludes_past_and_low_impact_rows():
    snapshot = build_snapshot(
        _bars(datetime(2026, 1, 7, 13, 45, tzinfo=timezone.utc)),
        _indicators(), timeframe="M15", source="replay",
        captured_at="2026-01-07T13:46:00+00:00",
        event_times=[
            {"time": "2026-01-07T13:45:00+00:00", "importance": 3},
            {"time": "2026-01-07T14:00:00+00:00", "importance": 2},
        ],
    )
    assert snapshot["values"]["high_impact_event_proximity_minutes"] is None
    assert snapshot["values"]["high_impact_event_within_60m"] is False


def test_session_liquidity_flags_do_not_use_current_wall_clock():
    # 18:00 UTC is New York only; this remains stable regardless of test-run
    # wall-clock time because the completed candle timestamp is the input.
    snapshot = build_snapshot(
        _bars(datetime(2026, 1, 7, 18, 0, tzinfo=timezone.utc)),
        _indicators(), timeframe="M15", source="replay",
        captured_at="2026-01-07T18:01:00+00:00",
    )
    assert snapshot["values"]["london_open_window"] is False
    assert snapshot["values"]["new_york_open_window"] is True
    assert snapshot["values"]["london_new_york_overlap"] is False


def test_cross_market_summary_is_point_in_time_and_rejects_future_values():
    snapshot = build_snapshot(
        _bars(datetime(2026, 1, 7, 13, 45, tzinfo=timezone.utc)),
        _indicators(), timeframe="M15", source="replay",
        captured_at="2026-01-07T13:46:00+00:00",
        cross_market={
            "DXY": {"timestamp": "2026-01-07T13:45:00+00:00", "return_fraction": 0.01},
            "US10Y": {"timestamp": "2026-01-07T13:44:00+00:00", "return_fraction": -0.02},
            "future": {"timestamp": "2026-01-07T13:47:00+00:00", "return_fraction": 9.0},
        },
        driver_buckets=["USD_LONG", "USD_SHORT", "USD_LONG"],
    )
    values = snapshot["values"]
    assert values["cross_market_available"] is True
    assert values["cross_market_observation_count"] == 2
    assert values["cross_market_mean_return"] == -0.005
    assert values["cross_market_future_observations_rejected"] == 1
    assert values["driver_bucket_count"] == 2
    assert snapshot["lineage"]["cross_market_names"] == ["DXY", "US10Y"]
    assert snapshot["lineage"]["driver_buckets"] == ["USD_LONG", "USD_SHORT"]
    assert validate_snapshot(snapshot)["valid"] is True


def test_cross_market_bare_values_are_not_treated_as_point_in_time():
    snapshot = build_snapshot(
        _bars(datetime(2026, 1, 7, 13, 45, tzinfo=timezone.utc)),
        _indicators(), timeframe="M15", source="replay",
        captured_at="2026-01-07T13:46:00+00:00",
        cross_market={"DXY": 0.2},
    )
    assert snapshot["values"]["cross_market_available"] is False
    assert snapshot["values"]["cross_market_observation_count"] == 0
    assert snapshot["values"]["cross_market_mean_return"] is None


def test_microstructure_features_extraction():
    snapshot = build_snapshot(
        _bars(datetime(2026, 1, 7, 13, 45, tzinfo=timezone.utc)),
        _indicators(), timeframe="M15", source="mt5",
        captured_at="2026-01-07T13:46:00+00:00",
        microstructure_context={
            "tick_timestamps": [float(i * 0.05) for i in range(25)],
            "order_flow_imbalance": 150.0,
            "order_book_depth": 200.0,
            "instantaneous_volatility": 0.015,
            "trade_volumes": [10.0] * 20,
            "price_changes": [0.0001] * 10 + [-0.0001] * 10,
        },
    )
    values = snapshot["values"]
    assert "hawkes_branching_ratio" in values
    assert "hawkes_cascade_active" in values
    assert "vpin_toxicity" in values
    assert "ofi_urgency_score" in values
    assert "roll_implied_spread" in values
    assert "phantom_liquidity_flag" in values
    assert validate_snapshot(snapshot)["valid"] is True

