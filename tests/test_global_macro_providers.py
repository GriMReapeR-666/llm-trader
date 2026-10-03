"""Tests for comprehensive Global Macro Data Providers.

Covers:
- ForexFactoryProvider (Calendar parsing & impact classification)
- ExnessProvider (Analyst commentary, key levels & sentiment extraction)
- CFTCCOTProvider (Smart money speculative positioning & 52-week Z-scores)
- EIAProvider (Petroleum inventory changes & Oil/CAD bias)
- GlobalMacroBridge (Multi-provider unified macroeconomic snapshot aggregation)
"""
from __future__ import annotations

import pytest

import json

from data.forexfactory_provider import ForexFactoryProvider, ForexFactoryEvent
from data.exness_provider import ExnessProvider, ExnessMarketInsight
from data.cftc_cot_provider import CFTCCOTProvider, COTReportRow
from data.eia_provider import EIAProvider, EIAPetroleumReport
from data.global_macro_bridge import GlobalMacroBridge, GlobalMacroSnapshot


def test_forexfactory_calendar_fetch():
    provider = ForexFactoryProvider()
    events = provider.fetch_calendar()
    assert len(events) > 0
    assert all(isinstance(e, ForexFactoryEvent) for e in events)

    # Test currency filtering
    usd_events = provider.fetch_calendar(currency="USD")
    assert len(usd_events) > 0
    assert all(e.currency == "USD" for e in usd_events)

    # Test high impact filtering
    high_events = provider.get_upcoming_high_impact_events()
    assert all(e.impact == "HIGH" for e in high_events)


def test_exness_insights_fetch(tmp_path):
    # Hermetic: the live Exness feed 403s automated clients and the provider
    # deliberately returns honest-empty without a cache, so seed a fixture
    # cache to exercise normalization + level extraction offline.
    from data.exness_provider import FALLBACK_INSIGHTS
    cache = tmp_path / "exness_cache.json"
    cache.write_text(json.dumps(FALLBACK_INSIGHTS), encoding="utf-8")
    provider = ExnessProvider(cache_path=str(cache))
    insights = provider.fetch_insights()
    assert len(insights) > 0
    assert all(isinstance(i, ExnessMarketInsight) for i in insights)

    # Test symbol-specific level extraction
    levels_eur = provider.get_latest_levels("EURUSD")
    assert "support" in levels_eur
    assert "resistance" in levels_eur
    assert "sentiment" in levels_eur

    levels_xau = provider.get_latest_levels("XAUUSD")
    assert levels_xau.get("support") is not None or levels_xau.get("resistance") is not None


def test_cftc_cot_positioning():
    provider = CFTCCOTProvider()
    rows = provider.fetch_cot_report()
    assert len(rows) > 0
    assert all(isinstance(r, COTReportRow) for r in rows)

    # Test specific asset retrieval
    gold_cot = provider.fetch_cot_report("GOLD")
    assert len(gold_cot) > 0
    assert gold_cot[0].asset == "GOLD"
    assert gold_cot[0].sentiment_bias in ("EXTREME_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "EXTREME_BEARISH")

    # Test relative positioning calculation
    rel = provider.get_positioning_for_pair("EUR", "USD")
    assert "base_percentile" in rel
    assert "quote_percentile" in rel
    assert "relative_bias" in rel


def test_eia_petroleum_status():
    provider = EIAProvider()
    report = provider.fetch_latest_report()
    assert isinstance(report, EIAPetroleumReport)
    assert report.report_date != ""
    assert isinstance(report.crude_surprise_mb, float)
    assert report.oil_bias in ("BULLISH", "BEARISH", "NEUTRAL")
    assert report.cad_bias in ("BULLISH_CAD", "BEARISH_CAD", "NEUTRAL")


def test_global_macro_bridge_snapshot():
    bridge = GlobalMacroBridge()
    snapshot = bridge.get_macro_snapshot("EURUSD")
    assert isinstance(snapshot, GlobalMacroSnapshot)
    assert snapshot.symbol == "EURUSD"
    assert snapshot.macro_regime in ("RISK_ON", "RISK_OFF", "NORMAL")
    assert isinstance(snapshot.calendar_events_count, int)
    assert isinstance(snapshot.summary_text, str)
    assert len(snapshot.summary_text) > 20

    # Test multi-symbol overview
    summary = bridge.fetch_summary()
    assert "EURUSD" in summary
    assert "XAUUSD" in summary
    assert "USOIL" in summary
