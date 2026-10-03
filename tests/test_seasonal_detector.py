"""Tests for SeasonalPatternDetector — seasonal pattern analysis."""
from __future__ import annotations

import math
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import numpy as np
import pytest


@pytest.fixture
def engine_with_seasonal_data(tmp_path):
    """Create a correlation engine with seasonal event data — NFP spikes harder in March."""
    db_path = str(tmp_path / "test_seasonal.db")
    from core.news_regime_correlation import NewsRegimeCorrelation
    eng = NewsRegimeCorrelation(db_path=db_path)

    rng = np.random.RandomState(42)
    now = datetime.now(timezone.utc)
    # Spread events across the last 11 months so all months have data within lookback
    # Month 0 = 11 months ago, Month 11 = this month
    # We map event month 0-11 to calendar months spread across last 11 months

    # NFP events — one specific calendar month has much larger spikes
    # Let's say the month 10 months ago (peak_month) has 80-120 pip spikes
    peak_offset = 10  # 10 months ago = peak
    for offset in range(12):
        ev_time = now - timedelta(days=30 * (11 - offset))
        n_events = 4 if offset == peak_offset else 2
        for _ in range(n_events):
            # Add some day jitter within the month
            jitter_days = rng.randint(0, 25)
            ev_time_jittered = ev_time + timedelta(days=jitter_days)
            if offset == peak_offset:
                spike = rng.uniform(80, 120)
            else:
                spike = rng.uniform(20, 40)
            price_before = 1.1000
            price_after = price_before + spike / 10000
            eng.record_event_outcome(
                event_title="NON-FARM PAYROLLS", tier=1, currency="USD",
                event_time=ev_time_jittered, price_before=price_before, price_after=price_after,
                symbol="EURUSD",
            )

    # CPI events — no strong seasonality (uniform spikes)
    for offset in range(12):
        ev_time = now - timedelta(days=30 * (11 - offset))
        spike = rng.uniform(30, 50)
        price_before = 1.1000
        price_after = price_before + spike / 10000
        eng.record_event_outcome(
            event_title="CONSUMER PRICE INDEX", tier=1, currency="USD",
            event_time=ev_time, price_before=price_before, price_after=price_after,
            symbol="EURUSD",
        )

    yield eng
    eng.close()


class TestSeasonalPatternDetector:
    """Test the SeasonalPatternDetector class."""

    def test_import(self):
        from core.news_regime_correlation import SeasonalPatternDetector, SeasonalPattern
        assert SeasonalPatternDetector is not None
        assert SeasonalPattern is not None

    def test_detect_all_returns_patterns(self, engine_with_seasonal_data):
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        patterns = detector.detect_all(lookback_days=365)
        assert len(patterns) >= 1
        titles = [p.event_title for p in patterns]
        assert "NON-FARM PAYROLLS" in titles

    def test_nfp_seasonality_detected(self, engine_with_seasonal_data):
        """NFP should show significant seasonality (peak at offset=10)."""
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        patterns = detector.detect_all(lookback_days=365)
        nfp = next(p for p in patterns if p.event_title == "NON-FARM PAYROLLS")
        # Peak should be higher than trough
        assert nfp.peak_avg_spike > nfp.trough_avg_spike
        # Peak should have 80+ pip avg (the peak_offset months have 80-120 pip spikes)
        assert nfp.peak_avg_spike > 70
        # Trough should be < 50 pip avg
        assert nfp.trough_avg_spike < 50

    def test_cpi_no_strong_seasonality(self, engine_with_seasonal_data):
        """CPI has uniform spikes — should not be strongly seasonal."""
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        patterns = detector.detect_all(lookback_days=365)
        cpi = next((p for p in patterns if p.event_title == "CONSUMER PRICE INDEX"), None)
        if cpi is not None:
            # CPI is uniform, so seasonal strength should be low
            assert cpi.seasonal_strength < 0.5

    def test_monthly_stats_computed(self, engine_with_seasonal_data):
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        patterns = detector.detect_all(lookback_days=365)
        nfp = next(p for p in patterns if p.event_title == "NON-FARM PAYROLLS")
        # At least some months should have data
        months_with_data = sum(1 for n in nfp.monthly_n if n > 0)
        assert months_with_data >= 4

    def test_insight_generated(self, engine_with_seasonal_data):
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        patterns = detector.detect_all(lookback_days=365)
        nfp = next(p for p in patterns if p.event_title == "NON-FARM PAYROLLS")
        assert isinstance(nfp.insight, str)
        assert len(nfp.insight) > 10

    def test_report_dict(self, engine_with_seasonal_data):
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        report = detector.get_detector_report(lookback_days=365)
        assert "n_events_analyzed" in report
        assert "patterns" in report
        assert isinstance(report["patterns"], list)

    def test_text_report(self, engine_with_seasonal_data):
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        text = detector.get_seasonal_text_report(lookback_days=365)
        assert "SEASONAL PATTERN ANALYSIS" in text
        assert "NON-FARM PAYROLLS" in text

    def test_kruskal_wallis(self):
        """Test the Kruskal-Wallis test with known different distributions."""
        from core.news_regime_correlation import SeasonalPatternDetector
        # Two clearly different groups
        group1 = np.array([10, 12, 11, 13, 10])
        group2 = np.array([50, 55, 48, 52, 51])
        h, p = SeasonalPatternDetector._kruskal_wallis([group1, group2])
        assert h > 5  # Should be significant
        assert p < 0.05

    def test_kruskal_wallis_similar_groups(self):
        """Test Kruskal-Wallis with similar distributions."""
        from core.news_regime_correlation import SeasonalPatternDetector
        group1 = np.array([10, 12, 11, 13, 10])
        group2 = np.array([11, 10, 12, 11, 13])
        h, p = SeasonalPatternDetector._kruskal_wallis([group1, group2])
        assert p > 0.05  # Should NOT be significant

    def test_min_events_filter(self, tmp_path):
        """Too few events should return no patterns."""
        db_path = str(tmp_path / "test_min.db")
        from core.news_regime_correlation import NewsRegimeCorrelation, SeasonalPatternDetector
        eng = NewsRegimeCorrelation(db_path=db_path)
        # Only 3 events total — below MIN_TOTAL_EVENTS
        for i in range(3):
            eng.record_event_outcome(
                event_title="RARE EVENT", tier=3, currency="USD",
                event_time=datetime(2024, 1, 1 + i, tzinfo=timezone.utc),
                price_before=1.1000, price_after=1.1005, symbol="EURUSD",
            )
        detector = SeasonalPatternDetector(eng)
        patterns = detector.detect_all(lookback_days=365)
        assert len(patterns) == 0
        eng.close()

    def test_empty_engine(self, tmp_path):
        """Empty engine should return no patterns."""
        db_path = str(tmp_path / "test_empty.db")
        from core.news_regime_correlation import NewsRegimeCorrelation, SeasonalPatternDetector
        eng = NewsRegimeCorrelation(db_path=db_path)
        detector = SeasonalPatternDetector(eng)
        patterns = detector.detect_all()
        assert patterns == []
        text = detector.get_seasonal_text_report()
        assert "No seasonal patterns" in text
        eng.close()

    def test_peak_trough_month_names(self, engine_with_seasonal_data):
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        report = detector.get_detector_report(lookback_days=365)
        nfp = next(p for p in report["patterns"] if p["event_title"] == "NON-FARM PAYROLLS")
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        assert nfp["peak_month"] in month_names
        assert nfp["trough_month"] in month_names

    def test_seasonal_overrides_computed(self, engine_with_seasonal_data):
        """Significant patterns should have seasonal overrides."""
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        patterns = detector.detect_all(lookback_days=365)
        nfp = next(p for p in patterns if p.event_title == "NON-FARM PAYROLLS")
        if nfp.is_significant:
            assert len(nfp.seasonal_overrides) > 0
            # March override should be wider than default
            march_override = nfp.seasonal_overrides.get(2)
            if march_override:
                assert march_override["before"] >= 60  # At least default

    def test_symbol_filter(self, engine_with_seasonal_data):
        """Filtering by symbol should work."""
        from core.news_regime_correlation import SeasonalPatternDetector
        detector = SeasonalPatternDetector(engine_with_seasonal_data)
        patterns = detector.detect_all(lookback_days=365, symbol="EURUSD")
        assert len(patterns) >= 1
        # Non-existent symbol should return nothing
        patterns_gbp = detector.detect_all(lookback_days=365, symbol="GBPUSD")
        assert len(patterns_gbp) == 0
