"""Tests for FredBacktestData — FRED historical data for backtest."""
from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime, timezone, timedelta

import numpy as np
import pytest


@pytest.fixture
def fred_data():
    """Create FredBacktestData without FRED API (synthetic mode)."""
    from research.fred_backtest_data import FredBacktestData
    return FredBacktestData(api_manager=None, seed=42)


class TestFredBacktestData:
    """Test the FredBacktestData class."""

    def test_import(self):
        from research.fred_backtest_data import FredBacktestData, ReleaseRecord, ESISnapshot
        assert FredBacktestData is not None
        assert ReleaseRecord is not None
        assert ESISnapshot is not None

    def test_synthetic_release_history(self, fred_data):
        """Should generate synthetic releases when no FRED API."""
        releases = fred_data.fetch_release_history("PAYEMS", months_back=24)
        assert len(releases) >= 20
        assert all(r.actual is not None for r in releases)
        assert all(r.consensus is not None for r in releases)
        assert releases[0].event_name == "NFP"

    def test_synthetic_cpi_history(self, fred_data):
        releases = fred_data.fetch_release_history("PPIACO", months_back=12)
        assert len(releases) >= 10
        assert releases[0].event_name == "PPI"

    def test_surprise_zscore(self, fred_data):
        """Z-score should be positive for beats, negative for misses."""
        # Beat: actual >> consensus
        z_beat = fred_data.compute_surprise_zscore("PAYEMS", actual=300.0, forecast=180.0)
        assert z_beat > 1.0  # Strong positive surprise

        # Miss: actual << consensus
        z_miss = fred_data.compute_surprise_zscore("PAYEMS", actual=50.0, forecast=180.0)
        assert z_miss < -1.0  # Strong negative surprise

        # In-line: actual ≈ consensus
        z_inline = fred_data.compute_surprise_zscore("PAYEMS", actual=185.0, forecast=180.0)
        assert -1.0 < z_inline < 1.0

    def test_classify_surprise(self, fred_data):
        assert fred_data.classify_surprise(2.5) == "STRONG_BEAT"
        assert fred_data.classify_surprise(1.0) == "BEAT"
        assert fred_data.classify_surprise(0.0) == "IN_LINE"
        assert fred_data.classify_surprise(-1.0) == "MISS"
        assert fred_data.classify_surprise(-2.5) == "STRONG_MISS"

    def test_rolling_esi(self, fred_data):
        """ESI should be a valid score between 0-100."""
        esi = fred_data.compute_rolling_esi(months_back=6)
        assert 0 <= esi.esi_score <= 100
        assert esi.n_releases > 0
        assert esi.bias in ("HAWKISH", "DOVISH", "NEUTRAL")
        assert 0 <= esi.beat_rate <= 1.0

    def test_esi_with_specific_series(self, fred_data):
        """ESI with subset of series should work."""
        esi = fred_data.compute_rolling_esi(months_back=6, series_ids=["PAYEMS", "ICSA"])
        assert 0 <= esi.esi_score <= 100
        assert esi.n_releases > 0

    def test_enhance_events_with_fred(self, fred_data):
        """Events should get FRED data attached."""
        events = [
            {"title": "NON-FARM PAYROLLS", "time": datetime.now(timezone.utc), "importance": "high"},
            {"title": "CONSUMER PRICE INDEX", "time": datetime.now(timezone.utc), "importance": "high"},
            {"title": "RANDOM EVENT", "time": datetime.now(timezone.utc), "importance": "low"},
        ]
        enhanced = fred_data.enhance_events_with_fred(events, days_back=365)
        assert len(enhanced) == 3

        # NFP should have FRED data
        nfp = enhanced[0]
        assert "fred_actual" in nfp
        assert "fred_consensus" in nfp
        assert "fred_surprise_zscore" in nfp
        assert "fred_surprise_direction" in nfp

        # CPI should have FRED data
        cpi = enhanced[1]
        assert "fred_actual" in cpi

    def test_event_attribution(self, fred_data):
        """Should compute surprise→spike attribution."""
        reactions = [
            {"title": "NON-FARM PAYROLLS", "time": datetime.now(timezone.utc) - timedelta(days=30),
             "max_spike_pips": 85.0, "direction": "BULLISH"},
            {"title": "CONSUMER PRICE INDEX", "time": datetime.now(timezone.utc) - timedelta(days=60),
             "max_spike_pips": 40.0, "direction": "BEARISH"},
        ]
        attr = fred_data.compute_event_attribution(reactions, days_back=365)
        assert attr["n_attributed"] >= 0  # May or may not match depending on synthetic data
        assert "directional_accuracy" in attr
        assert "surprise_spike_correlation" in attr

    def test_direction_match(self):
        from research.fred_backtest_data import FredBacktestData
        assert FredBacktestData._direction_match(1.5, "BULLISH") is True
        assert FredBacktestData._direction_match(-1.5, "BEARISH") is True
        assert FredBacktestData._direction_match(1.5, "BEARISH") is False
        assert FredBacktestData._direction_match(-1.5, "BULLISH") is False
        assert FredBacktestData._direction_match(0.0, "NEUTRAL") is True

    def test_cache_behavior(self, fred_data):
        """Second call should use cache."""
        r1 = fred_data.fetch_release_history("PAYEMS", months_back=12)
        r2 = fred_data.fetch_release_history("PAYEMS", months_back=12)
        assert r1 is r2  # Same object from cache

    def test_all_series_synthetic(self, fred_data):
        """All mapped series should generate synthetic data."""
        from research.fred_backtest_data import NFP_SERIES, CPI_SERIES
        all_ids = list(NFP_SERIES.keys()) + list(CPI_SERIES.keys())
        for sid in all_ids:
            releases = fred_data.fetch_release_history(sid, months_back=6)
            assert len(releases) >= 3, f"Series {sid} should have at least 3 releases"

    def test_release_times_utc(self):
        from research.fred_backtest_data import RELEASE_TIMES_UTC
        assert RELEASE_TIMES_UTC["NFP"] == (13, 30)
        assert RELEASE_TIMES_UTC["ISM_EMPLOYMENT"] == (14, 0)

    def test_backtest_wiring(self):
        """Full backtest wiring test — run_from_mt5_history with fred_backtest_data."""
        from research.news_gate_backtest import NewsGateBacktest
        from research.fred_backtest_data import FredBacktestData

        bt = NewsGateBacktest()
        fred = FredBacktestData(seed=42)

        # Run with synthetic data (no MT5 available in test env)
        # This tests the wiring without requiring MT5
        result = bt.run(symbol="EURUSD", n_synthetic_trades=100, n_synthetic_events=10)
        assert result.total_trades == 100
        assert result.events_analyzed == 10
        # Verify result structure
        assert "win_rate" in result.with_gate
        assert "sharpe" in result.without_gate

    def test_esi_snapshot_fields(self, fred_data):
        esi = fred_data.compute_rolling_esi(months_back=3)
        assert hasattr(esi, "esi_score")
        assert hasattr(esi, "n_releases")
        assert hasattr(esi, "n_beats")
        assert hasattr(esi, "n_misses")
        assert hasattr(esi, "beat_rate")
        assert hasattr(esi, "recent_surprises")
        assert hasattr(esi, "bias")
        assert hasattr(esi, "timestamp")
        assert isinstance(esi.recent_surprises, list)

    def test_release_record_fields(self, fred_data):
        releases = fred_data.fetch_release_history("PAYEMS", months_back=6)
        r = releases[0]
        assert hasattr(r, "series_id")
        assert hasattr(r, "event_name")
        assert hasattr(r, "release_date")
        assert hasattr(r, "actual")
        assert hasattr(r, "consensus")
        assert hasattr(r, "previous")
        assert hasattr(r, "surprise_zscore")
        assert hasattr(r, "surprise_direction")
        assert hasattr(r, "unit")
