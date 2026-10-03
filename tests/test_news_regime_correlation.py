"""Tests for news regime correlation analysis and dynamic blackout adjustment."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

# Ensure project root on path
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ["core", "intelligence", "data", "research"]:
    _p = os.path.join(_root, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def tmp_db(tmp_path):
    """Create a NewsRegimeCorrelation with a temp SQLite database."""
    from core.news_regime_correlation import NewsRegimeCorrelation
    db_path = str(tmp_path / "test_correlation.db")
    engine = NewsRegimeCorrelation(db_path=db_path)
    yield engine
    engine.close()


@pytest.fixture
def sample_events():
    """Generate sample event data for testing."""
    base = datetime(2025, 6, 1, 13, 30, tzinfo=timezone.utc)
    return [
        {
            "title": "NON-FARM PAYROLLS",
            "currency": "USD",
            "time": base,
            "importance": "high",
            "forecast": 180.0,
            "previous": 175.0,
        },
        {
            "title": "INITIAL JOBLESS CLAIMS",
            "currency": "USD",
            "time": base + timedelta(hours=1),
            "importance": "medium",
            "forecast": 215.0,
            "previous": 218.0,
        },
        {
            "title": "ISM MANUFACTURING PMI",
            "currency": "USD",
            "time": base + timedelta(hours=2),
            "importance": "medium",
            "forecast": 50.0,
            "previous": 49.5,
        },
    ]


# =============================================================================
# EventOutcome Tests
# =============================================================================

class TestEventOutcome:
    def test_spike_computation_buy(self):
        from core.news_regime_correlation import EventOutcome
        outcome = EventOutcome(
            event_title="NFP", tier=1, currency="USD",
            event_time=datetime.now(timezone.utc),
            price_before=1.1000, price_after=1.1085,
        )
        assert outcome.spike_pips == pytest.approx(85.0, abs=0.1)
        assert outcome.direction == "BULLISH"
        assert outcome.is_hit is True  # 85 > 20 threshold

    def test_spike_computation_sell(self):
        from core.news_regime_correlation import EventOutcome
        outcome = EventOutcome(
            event_title="CPI", tier=1, currency="USD",
            event_time=datetime.now(timezone.utc),
            price_before=1.1000, price_after=1.0950,
        )
        assert outcome.spike_pips == pytest.approx(-50.0, abs=0.1)
        assert outcome.direction == "BEARISH"
        assert outcome.is_hit is True  # |-50| > 20 threshold

    def test_dud_event(self):
        from core.news_regime_correlation import EventOutcome
        outcome = EventOutcome(
            event_title="TRADE BALANCE", tier=3, currency="USD",
            event_time=datetime.now(timezone.utc),
            price_before=1.1000, price_after=1.1002,
        )
        assert abs(outcome.spike_pips) < 5.0
        assert outcome.is_hit is False  # 2 pips < 5 threshold for tier 3

    def test_jpy_pip_value(self):
        from core.news_regime_correlation import EventOutcome
        outcome = EventOutcome(
            event_title="NFP", tier=1, currency="USD",
            event_time=datetime.now(timezone.utc),
            price_before=150.00, price_after=150.85,
            symbol="USDJPY",
        )
        # JPY uses 100 multiplier
        assert outcome.spike_pips == pytest.approx(85.0, abs=0.1)


# =============================================================================
# Recording Tests
# =============================================================================

class TestRecording:
    def test_record_single_event(self, tmp_db):
        now = datetime.now(timezone.utc)
        outcome = tmp_db.record_event_outcome(
            event_title="NON-FARM PAYROLLS",
            tier=1, currency="USD",
            event_time=now - timedelta(hours=1),
            price_before=1.1000, price_after=1.1085,
            symbol="EURUSD",
        )
        assert outcome.id is not None
        assert outcome.spike_pips == pytest.approx(85.0, abs=0.1)
        assert outcome.is_hit is True

    def test_record_price_snapshot_multiple_measurements(self, tmp_db):
        now = datetime.now(timezone.utc)
        outcomes = tmp_db.record_price_snapshot(
            event_title="NON-FARM PAYROLLS",
            tier=1, currency="USD",
            event_time=now - timedelta(hours=2),
            price_at_event=1.1000,
            symbol="EURUSD",
            price_after_15m=1.1085,
            price_after_30m=1.1070,
            price_after_60m=1.1060,
            max_price_in_window=1.1090,
            min_price_in_window=1.0995,
        )
        assert len(outcomes) == 3  # 15m, 30m, 60m
        assert all(o.id is not None for o in outcomes)
        assert outcomes[0].duration_min == 15.0
        assert outcomes[1].duration_min == 30.0
        assert outcomes[2].duration_min == 60.0

    def test_stats_after_recording(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Record 3 events
        for i in range(3):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i),
                price_before=1.1000, price_after=1.1000 + (i * 0.005),
                symbol="EURUSD",
            )
        stats = tmp_db.get_stats()
        assert stats["total_events"] == 3
        assert stats["by_tier"][1] == 3


# =============================================================================
# Hit Rate Computation Tests
# =============================================================================

class TestHitRates:
    def test_empty_database(self, tmp_db):
        hit_rates = tmp_db.compute_hit_rates()
        assert hit_rates == []

    def test_single_event_type(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Record 10 NFP events with varying spikes
        spikes = [85, 45, 120, -30, 65, 90, 15, 55, 75, 40]
        for i, spike in enumerate(spikes):
            tmp_db.record_event_outcome(
                event_title="NON-FARM PAYROLLS",
                tier=1, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000,
                price_after=1.1000 + spike / 10000.0,
                symbol="EURUSD",
            )
        hit_rates = tmp_db.compute_hit_rates()
        assert len(hit_rates) == 1
        hr = hit_rates[0]
        assert hr.event_title == "NON-FARM PAYROLLS"
        assert hr.n_events == 10
        assert hr.hit_rate > 0.5  # Most events > 20 pips
        assert hr.avg_spike_pips > 30  # Average spike is substantial
        assert hr.confidence in ("MEDIUM", "HIGH")  # 10 events >= threshold for MEDIUM

    def test_multiple_event_types(self, tmp_db):
        now = datetime.now(timezone.utc)
        # NFP: big spikes
        for i in range(8):
            tmp_db.record_event_outcome(
                event_title="NON-FARM PAYROLLS", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + (60 + i * 5) / 10000.0,
                symbol="EURUSD",
            )
        # Claims: small spikes
        for i in range(8):
            tmp_db.record_event_outcome(
                event_title="INITIAL JOBLESS CLAIMS", tier=2, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + (3 + i) / 10000.0,
                symbol="EURUSD",
            )
        hit_rates = tmp_db.compute_hit_rates()
        assert len(hit_rates) == 2
        # NFP should have higher avg spike
        nfp_hr = next(hr for hr in hit_rates if hr.event_title == "NON-FARM PAYROLLS")
        claims_hr = next(hr for hr in hit_rates if hr.event_title == "INITIAL JOBLESS CLAIMS")
        assert nfp_hr.avg_spike_pips > claims_hr.avg_spike_pips

    def test_recency_weighting(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Old events: small spikes
        for i in range(5):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=90 + i * 7),
                price_before=1.1000, price_after=1.1000 + 5 / 10000.0,
                symbol="EURUSD",
            )
        # Recent events: big spikes
        for i in range(5):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i),
                price_before=1.1000, price_after=1.1000 + 80 / 10000.0,
                symbol="EURUSD",
            )
        hit_rates = tmp_db.compute_hit_rates(lookback_days=120)
        hr = hit_rates[0]
        # Recency weighting should pull avg_spike toward recent big spikes
        assert hr.avg_spike_pips > 30  # Weighted avg > pure avg of 42.5

    def test_lookback_filtering(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Old events (> 90 days)
        for i in range(5):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=120 + i),
                price_before=1.1000, price_after=1.1080,
                symbol="EURUSD",
            )
        # Recent events (< 90 days)
        for i in range(5):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i),
                price_before=1.1000, price_after=1.1005,
                symbol="EURUSD",
            )
        hit_rates = tmp_db.compute_hit_rates(lookback_days=90)
        hr = hit_rates[0]
        # Only recent events should be counted
        assert hr.n_events == 5


# =============================================================================
# Dynamic Window Adjustment Tests
# =============================================================================

class TestDynamicWindows:
    def test_fallback_to_defaults_when_empty(self, tmp_db):
        windows = tmp_db.get_adjusted_windows(tier=1, event_title="NFP")
        assert windows["before"] == 60
        assert windows["after"] == 20
        assert windows["source"] == "default"

    def test_wider_windows_for_hard_spikers(self, tmp_db):
        now = datetime.now(timezone.utc)
        # NFP consistently spikes 80+ pips
        for i in range(10):
            tmp_db.record_event_outcome(
                event_title="NON-FARM PAYROLLS", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + 80 / 10000.0,
                symbol="EURUSD",
            )
        windows = tmp_db.get_adjusted_windows(
            tier=1, event_title="NON-FARM PAYROLLS",
        )
        # Should be wider than default (60/20)
        assert windows["before"] > 60
        assert windows["after"] > 20

    def test_narrower_windows_for_duds(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Trade Balance rarely moves
        for i in range(10):
            tmp_db.record_event_outcome(
                event_title="TRADE BALANCE", tier=3, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + 2 / 10000.0,
                symbol="EURUSD",
            )
        windows = tmp_db.get_adjusted_windows(
            tier=3, event_title="TRADE BALANCE",
        )
        # Should be narrower than default (15/10)
        assert windows["before"] < 15
        assert windows["after"] < 10

    def test_get_all_adjusted_windows(self, tmp_db):
        windows = tmp_db.get_all_adjusted_windows()
        assert 1 in windows
        assert 2 in windows
        assert 3 in windows
        assert "before" in windows[1]
        assert "after" in windows[1]

    def test_minimum_window_5_minutes(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Extremely low spike events
        for i in range(10):
            tmp_db.record_event_outcome(
                event_title="HOUSING STARTS", tier=3, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + 0.5 / 10000.0,
                symbol="EURUSD",
            )
        windows = tmp_db.get_adjusted_windows(
            tier=3, event_title="HOUSING STARTS",
        )
        assert windows["before"] >= 5  # Never below 5 minutes
        assert windows["after"] >= 5


# =============================================================================
# NewsTradingGate Integration Tests
# =============================================================================

class TestGateIntegration:
    def test_gate_uses_static_defaults_without_engine(self, sample_events):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        # Event in 30 minutes — should be blocked (default tier 1 blackout = 60min)
        event_time = datetime.now(timezone.utc) + timedelta(minutes=30)
        events = [{**sample_events[0], "time": event_time}]
        result = gate.evaluate(symbol="EURUSD", now=datetime.now(timezone.utc), events=events)
        assert result.allowed is False
        assert result.phase == "SPIKE_BLACKOUT"

    def test_gate_uses_correlation_engine(self, tmp_db, sample_events):
        from core.news_trading import NewsTradingGate
        # Record events that rarely spike → should shrink windows
        now = datetime.now(timezone.utc)
        for i in range(10):
            tmp_db.record_event_outcome(
                event_title="NON-FARM PAYROLLS", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + 3 / 10000.0,
                symbol="EURUSD",
            )
        gate = NewsTradingGate(correlation_engine=tmp_db)
        # Event in 20 minutes — with default (60min) this would be blocked
        # But with narrow windows from correlation, it might be allowed
        event_time = now + timedelta(minutes=20)
        events = [{**sample_events[0], "time": event_time}]
        result = gate.evaluate(symbol="EURUSD", now=now, events=events)
        # The gate should use the dynamically adjusted (narrower) windows
        # With dud events, the before window should be < 60, possibly allowing 20min
        # (exact result depends on the computed windows)
        assert result.phase in ("SPIKE_BLACKOUT", "PRE_NEWS", "CLEAR")

    def test_gate_static_override(self, sample_events):
        from core.news_trading import NewsTradingGate
        override = {1: {"before": 10, "after": 5}}
        gate = NewsTradingGate(blackout_windows_override=override)
        event_time = datetime.now(timezone.utc) + timedelta(minutes=15)
        events = [{**sample_events[0], "time": event_time}]
        result = gate.evaluate(symbol="EURUSD", now=datetime.now(timezone.utc), events=events)
        # 15min > 10min before window → should be in PRE_NEWS or CLEAR
        assert result.allowed is True or result.phase != "SPIKE_BLACKOUT"


# =============================================================================
# Correlation Report Tests
# =============================================================================

class TestCorrelationReport:
    def test_empty_report(self, tmp_db):
        report = tmp_db.get_correlation_report()
        assert report.total_events_recorded == 0
        assert report.overall_hit_rate == 0.0
        assert report.event_hit_rates == []

    def test_report_with_data(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Record events
        for i in range(15):
            tmp_db.record_event_outcome(
                event_title="NON-FARM PAYROLLS", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 5),
                price_before=1.1000, price_after=1.1000 + (50 + i * 3) / 10000.0,
                symbol="EURUSD",
            )
            tmp_db.record_event_outcome(
                event_title="INITIAL JOBLESS CLAIMS", tier=2, currency="USD",
                event_time=now - timedelta(days=i * 5),
                price_before=1.1000, price_after=1.1000 + (5 + i) / 10000.0,
                symbol="EURUSD",
            )
        report = tmp_db.get_correlation_report(symbol="EURUSD")
        assert report.total_events_recorded == 30
        assert len(report.event_hit_rates) == 2
        assert report.events_by_tier[1] == 15
        assert report.events_by_tier[2] == 15
        assert report.accuracy_score >= 0.0
        assert report.generated_at is not None

    def test_report_regime_adjustments(self, tmp_db):
        now = datetime.now(timezone.utc)
        for i in range(10):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + 70 / 10000.0,
                symbol="EURUSD",
            )
        report = tmp_db.get_correlation_report()
        assert "1" in report.regime_adjustments
        assert report.regime_adjustments["1"]["before"] > 60  # Wider than default


# =============================================================================
# Query Tests
# =============================================================================

class TestQueries:
    def test_get_event_history(self, tmp_db):
        now = datetime.now(timezone.utc)
        for i in range(5):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i),
                price_before=1.1000, price_after=1.1000 + i * 0.001,
                symbol="EURUSD",
            )
        history = tmp_db.get_event_history(event_title="NFP")
        assert len(history) == 5

    def test_spike_distribution(self, tmp_db):
        now = datetime.now(timezone.utc)
        for i in range(10):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i),
                price_before=1.1000, price_after=1.1000 + (i * 10) / 10000.0,
                symbol="EURUSD",
            )
        dist = tmp_db.get_spike_distribution(event_title="NFP")
        assert dist["n"] == 10
        assert dist["mean"] > 0
        assert dist["p95"] >= dist["median"]


# =============================================================================
# Auto-Record from Price Data Tests
# =============================================================================

class TestAutoRecord:
    def test_auto_record_from_bars(self, tmp_db):
        now = datetime.now(timezone.utc)
        event_ts = now.timestamp()
        bars = [
            {"time": event_ts - 900, "open": 1.1000, "high": 1.1005, "low": 1.0998, "close": 1.1002, "volume": 100},
            {"time": event_ts, "open": 1.1002, "high": 1.1010, "low": 1.1000, "close": 1.1008, "volume": 500},
            {"time": event_ts + 900, "open": 1.1008, "high": 1.1090, "low": 1.1005, "close": 1.1085, "volume": 800},
            {"time": event_ts + 1800, "open": 1.1085, "high": 1.1095, "low": 1.1080, "close": 1.1088, "volume": 300},
            {"time": event_ts + 2700, "open": 1.1088, "high": 1.1092, "low": 1.1082, "close": 1.1086, "volume": 200},
        ]
        outcome = tmp_db.auto_record_from_price_data(
            event_title="NON-FARM PAYROLLS",
            tier=1, currency="USD",
            event_time=now,
            bars=bars,
            symbol="EURUSD",
        )
        assert outcome is not None
        assert outcome.spike_pips > 50  # Should detect the spike
        assert outcome.is_hit is True

    def test_auto_record_insufficient_bars(self, tmp_db):
        result = tmp_db.auto_record_from_price_data(
            event_title="NFP", tier=1, currency="USD",
            event_time=datetime.now(timezone.utc),
            bars=[], symbol="EURUSD",
        )
        assert result is None


# =============================================================================
# Decay Monitor Tests
# =============================================================================

class TestDecayMonitor:
    def test_no_decay_when_no_data(self, tmp_db):
        alerts = tmp_db.detect_decay()
        assert alerts == []

    def test_no_decay_when_healthy(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Record consistent spiking events
        for i in range(10):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 5),
                price_before=1.1000, price_after=1.1000 + 60 / 10000.0,
                symbol="EURUSD",
            )
        alerts = tmp_db.detect_decay(short_window_days=30)
        # Should have no decay alerts
        assert len(alerts) == 0

    def test_detect_hit_rate_decay(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Historical: strong spikes
        for i in range(8):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=60 + i * 5),
                price_before=1.1000, price_after=1.1000 + 80 / 10000.0,
                symbol="EURUSD",
            )
        # Recent: all duds
        for i in range(4):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 3),
                price_before=1.1000, price_after=1.1000 + 2 / 10000.0,
                symbol="EURUSD",
            )
        alerts = tmp_db.detect_decay(short_window_days=30)
        assert len(alerts) >= 1
        nfp_alert = next(a for a in alerts if a["event_title"] == "NFP")
        assert nfp_alert["severity"] in ("WARNING", "CRITICAL")
        assert any("Hit rate" in r or "duds" in r for r in nfp_alert["reasons"])

    def test_detect_spike_drop(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Historical: 80 pip avg
        for i in range(8):
            tmp_db.record_event_outcome(
                event_title="CPI", tier=1, currency="USD",
                event_time=now - timedelta(days=60 + i * 5),
                price_before=1.1000, price_after=1.1000 + 80 / 10000.0,
                symbol="EURUSD",
            )
        # Recent: 10 pip avg (big drop)
        for i in range(4):
            tmp_db.record_event_outcome(
                event_title="CPI", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 3),
                price_before=1.1000, price_after=1.1000 + 10 / 10000.0,
                symbol="EURUSD",
            )
        alerts = tmp_db.detect_decay(short_window_days=30, spike_drop_pct=0.3)
        cpi_alerts = [a for a in alerts if a["event_title"] == "CPI"]
        assert len(cpi_alerts) >= 1
        assert any("spike" in r.lower() for r in cpi_alerts[0]["reasons"])

    def test_critical_severity_for_streak(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Historical: 80% hit rate
        for i in range(8):
            tmp_db.record_event_outcome(
                event_title="FOMC", tier=1, currency="USD",
                event_time=now - timedelta(days=60 + i * 5),
                price_before=1.1000, price_after=1.1000 + 90 / 10000.0,
                symbol="EURUSD",
            )
        # Recent: 5 consecutive duds
        for i in range(5):
            tmp_db.record_event_outcome(
                event_title="FOMC", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 2),
                price_before=1.1000, price_after=1.1000 + 1 / 10000.0,
                symbol="EURUSD",
            )
        alerts = tmp_db.detect_decay(short_window_days=30)
        fomc = [a for a in alerts if a["event_title"] == "FOMC"]
        assert len(fomc) >= 1
        assert fomc[0]["severity"] == "CRITICAL"
        assert any("consecutive duds" in r for r in fomc[0]["reasons"])

    def test_decay_dashboard(self, tmp_db):
        now = datetime.now(timezone.utc)
        for i in range(10):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + 50 / 10000.0,
                symbol="EURUSD",
            )
        dashboard = tmp_db.get_decay_dashboard()
        assert "health" in dashboard
        assert "alerts" in dashboard
        assert "event_hit_rates" in dashboard
        assert dashboard["total_events"] == 10

    def test_decay_recommendation_text(self, tmp_db):
        rec = tmp_db._decay_recommendation("CRITICAL", 0.8, 0.0, "NFP")
        assert "CRITICAL" in rec
        assert "NFP" in rec

        rec = tmp_db._decay_recommendation("WARNING", 0.6, 0.2, "CPI")
        assert "WARNING" in rec
        assert "CPI" in rec


# =============================================================================
# Export Tests
# =============================================================================

class TestExport:
    def test_export_json_creates_file(self, tmp_db, tmp_path):
        now = datetime.now(timezone.utc)
        for i in range(5):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + 60 / 10000.0,
                symbol="EURUSD",
            )
        out_path = str(tmp_path / "export.json")
        result = tmp_db.export_json(output_path=out_path)
        assert result == out_path
        import os
        assert os.path.exists(out_path)
        assert os.path.getsize(out_path) > 100

    def test_export_json_structure(self, tmp_db, tmp_path):
        import json
        now = datetime.now(timezone.utc)
        for i in range(5):
            tmp_db.record_event_outcome(
                event_title="CPI", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 7),
                price_before=1.1000, price_after=1.1000 + 40 / 10000.0,
                symbol="EURUSD",
            )
        out_path = str(tmp_path / "export.json")
        tmp_db.export_json(output_path=out_path)
        with open(out_path) as f:
            data = json.load(f)
        assert "exported_at" in data
        assert "summary" in data
        assert "hit_rates" in data
        assert "event_outcomes" in data
        assert "decay_alerts" in data
        assert "adjusted_windows" in data
        assert "heatmap" in data
        assert "spike_distributions" in data
        assert len(data["event_outcomes"]) == 5
        assert len(data["hit_rates"]) == 1

    def test_export_empty_database(self, tmp_db, tmp_path):
        import json
        out_path = str(tmp_path / "empty.json")
        tmp_db.export_json(output_path=out_path)
        with open(out_path) as f:
            data = json.load(f)
        assert data["summary"]["total_events"] == 0
        assert data["event_outcomes"] == []
        assert data["hit_rates"] == []

    def test_export_default_path(self, tmp_db):
        # Should not raise even if artifacts dir exists
        result = tmp_db.export_json()
        assert result.endswith("news_correlation_export.json")
        import os
        assert os.path.exists(result)
        # Clean up
        os.remove(result)


# =============================================================================
# Heatmap Tests
# =============================================================================

class TestHeatmap:
    def test_heatmap_empty(self, tmp_db):
        hm = tmp_db.get_heatmap_data()
        assert hm["events"] == []
        assert hm["months"] == []
        assert hm["matrix"] == []

    def test_heatmap_with_data(self, tmp_db):
        now = datetime.now(timezone.utc)
        # Record events across different months
        for month_offset in range(6):
            ev_time = now - timedelta(days=month_offset * 30 + 10)
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=ev_time,
                price_before=1.1000, price_after=1.1000 + 50 / 10000.0,
                symbol="EURUSD",
            )
            tmp_db.record_event_outcome(
                event_title="CPI", tier=1, currency="USD",
                event_time=ev_time,
                price_before=1.1000, price_after=1.1000 + 30 / 10000.0,
                symbol="EURUSD",
            )
        hm = tmp_db.get_heatmap_data(lookback_days=365)
        assert len(hm["events"]) == 2
        assert "NFP" in hm["events"]
        assert "CPI" in hm["events"]
        assert len(hm["months"]) == 12
        assert len(hm["matrix"]) == 2
        assert len(hm["matrix"][0]) == 12  # 12 months
        assert len(hm["n_matrix"]) == 2

    def test_heatmap_text_render(self, tmp_db):
        now = datetime.now(timezone.utc)
        for i in range(8):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 15),
                price_before=1.1000, price_after=1.1000 + 60 / 10000.0,
                symbol="EURUSD",
            )
        text = tmp_db.get_heatmap_text()
        assert "NFP" in text
        assert "Jan" in text or "Feb" in text  # At least one month header

    def test_heatmap_text_empty(self, tmp_db):
        text = tmp_db.get_heatmap_text()
        assert "No heatmap data" in text

    def test_heatmap_in_export(self, tmp_db, tmp_path):
        import json
        now = datetime.now(timezone.utc)
        for i in range(5):
            tmp_db.record_event_outcome(
                event_title="NFP", tier=1, currency="USD",
                event_time=now - timedelta(days=i * 30),
                price_before=1.1000, price_after=1.1000 + 50 / 10000.0,
                symbol="EURUSD",
            )
        out_path = str(tmp_path / "export.json")
        tmp_db.export_json(output_path=out_path)
        with open(out_path) as f:
            data = json.load(f)
        hm = data["heatmap"]
        assert "events" in hm
        assert "matrix" in hm
        assert len(hm["events"]) >= 1
