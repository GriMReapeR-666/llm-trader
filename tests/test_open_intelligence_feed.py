"""Unit tests for the open-source intelligence ingestion feeds.

Tests:
- GDELT 2.0 event scoring, Goldstein metric, and offline fallback
- ACLED maritime and pipeline chokepoints
- DBnomics global indicator extraction
- BIS effective exchange rate and liquidity indicators
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from data.open_intelligence_feed import (
    GDELTFeed,
    ACLEDFeed,
    DBnomicsFeed,
    BISFeed,
    OpenIntelligenceAggregator,
)


def test_gdelt_event_scoring_and_fallback():
    feed = GDELTFeed()
    # Test offline / mocked parsing
    sample_records = [
        {"actor1": "USA", "actor2": "CHN", "goldstein": -7.5, "quad_class": 3, "timestamp": "2026-10-15T00:00:00Z"},
        {"actor1": "RUS", "actor2": "UKR", "goldstein": -9.0, "quad_class": 3, "timestamp": "2026-10-15T00:05:00Z"},
        {"actor1": "GBR", "actor2": "FRA", "goldstein": 5.0, "quad_class": 1, "timestamp": "2026-10-15T00:10:00Z"},
    ]
    res = feed.compute_conflict_intensity(sample_records)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["mean_goldstein"] < 0
    assert res["material_conflict_ratio"] > 0.5
    assert 0.0 <= res["normalized_conflict_intensity"] <= 1.0


def test_acled_chokepoint_incident_detection():
    feed = ACLEDFeed()
    # Test chokepoints detection: Hormuz, Bab el-Mandeb, Malacca
    sample_incidents = [
        {"location": "Bab el-Mandeb Strait", "event_type": "Explosions/Remote violence", "fatalities": 0, "latitude": 12.58, "longitude": 43.33},
        {"location": "Red Sea Shipping Lane", "event_type": "Battles", "fatalities": 2, "latitude": 14.10, "longitude": 42.70},
    ]
    status = feed.evaluate_chokepoints(sample_incidents)
    assert status["status"] == "READY_FOR_REVIEW"
    assert status["chokepoints_at_risk"]["BAB_EL_MANDEB"] is True
    assert status["chokepoints_at_risk"]["STRAIT_OF_HORMUZ"] is False
    assert status["supply_shock_detected"] is True


def test_dbnomics_series_normalization():
    feed = DBnomicsFeed()
    raw_series = [
        {"period": "2026-07-01", "value": 105.2},
        {"period": "2026-08-01", "value": 106.1},
        {"period": "2026-09-01", "value": 107.0},
    ]
    normalized = feed.normalize_indicator("OECD/KEI/PRINTO01.DEU", raw_series)
    assert normalized["status"] == "READY_FOR_REVIEW"
    assert normalized["latest_value"] == 107.0
    assert normalized["mom_change"] == pytest.approx(0.9, rel=1e-3)


def test_bis_eer_and_liquidity_normalization():
    feed = BISFeed()
    raw_eer = [
        {"period": "2026-08", "value": 98.5},
        {"period": "2026-09", "value": 101.2},
    ]
    res = feed.evaluate_effective_exchange_rate("USD", raw_eer)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["currency"] == "USD"
    assert res["eer_change_pct"] > 0
    assert res["direction"] == "STRENGTHENING"


def test_open_intelligence_aggregator():
    agg = OpenIntelligenceAggregator()
    vector = agg.get_geopolitical_telemetry_vector()
    assert "status" in vector
    assert "goldstein_conflict_intensity" in vector
    assert "chokepoint_disruptions" in vector
    assert vector["execution_authority"] is False
