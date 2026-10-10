"""Unit tests for lag_delay_engine.py."""
import time
import pytest
from intelligence.lag_delay_engine import (
    TimestampedEvent,
    HeartbeatTracker,
    ConfidenceDecayEngine,
    SmithPredictorEngine,
    LagAwareCausalEdge,
    utcnow_iso,
)


def test_three_timestamp_event():
    """Verify Three-Timestamp Envelope calculates staleness and pipeline latency."""
    t_evt = "2026-08-10T12:00:00.000Z"
    t_ing = "2026-08-10T12:00:01.000Z"
    t_prc = "2026-08-10T12:00:02.000Z"

    event = TimestampedEvent(
        event_id="evt_101",
        source="FRED",
        t_event=t_evt,
        t_ingest=t_ing,
        t_proc=t_prc,
        data_type="macro",
    )

    assert abs(event.staleness_ms - 2000.0) < 1.0
    assert abs(event.pipeline_latency_ms - 1000.0) < 1.0


def test_heartbeat_tracker():
    """Verify HeartbeatTracker tracks source status and latency."""
    tracker = HeartbeatTracker()
    now = utcnow_iso()
    event = TimestampedEvent(
        event_id="evt_102",
        source="MT5",
        t_event=now,
        t_ingest=now,
        t_proc=now,
        data_type="price_tick",
    )

    entry = tracker.update_source(event)
    assert entry["source"] == "MT5"
    assert entry["status"] == "GREEN"
    assert entry["staleness_ms"] >= 0.0


def test_confidence_decay():
    """Verify exponential confidence decay with VIX multiplier."""
    conf_fresh = ConfidenceDecayEngine.calculate_confidence(staleness_ms=10.0, data_type="price_tick", vix_level=15.0)
    conf_stale = ConfidenceDecayEngine.calculate_confidence(staleness_ms=2000.0, data_type="price_tick", vix_level=15.0)
    conf_high_vix = ConfidenceDecayEngine.calculate_confidence(staleness_ms=500.0, data_type="price_tick", vix_level=40.0)

    assert conf_fresh > conf_stale
    assert 0.0 <= conf_fresh <= 1.0
    assert 0.0 <= conf_stale <= 1.0


def test_smith_predictor():
    """Verify Smith Predictor returns state prediction and order type recommendation."""
    pred = SmithPredictorEngine.predict_state(
        asset="XAUUSD",
        observed_price=2050.0,
        staleness_ms=50.0,
        recent_returns_slope=0.0001,
        current_atr=12.5,
        execution_latency_ms=20.0,
    )

    assert pred.asset == "XAUUSD"
    assert pred.predicted_price >= 2050.0
    assert pred.order_type_recommendation in ["LIMIT", "MARKET"]
    assert 0.0 <= pred.confidence <= 1.0


def test_lag_aware_causal_edge():
    """Verify LagAwareCausalEdge propagation window and current impact calculation."""
    edge = LagAwareCausalEdge(
        source_entity="FOMC Rate Hike",
        target_entity="EURUSD",
        relation="depresses",
        min_lag_seconds=300.0,      # 5 minutes
        max_lag_seconds=86400.0,    # 24 hours
        decay_half_life_seconds=14400.0,
        historical_win_rate=0.75,
        confidence=0.9,
    )

    assert edge.is_in_effect_window(600.0) is True     # 10 minutes (in window)
    assert edge.is_in_effect_window(10.0) is False     # 10 seconds (too early)
    assert edge.is_in_effect_window(100000.0) is False # > 24 hours (expired)

    impact = edge.current_causal_impact(600.0)
    assert impact > 0.0
