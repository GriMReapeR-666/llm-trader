"""Tests for core/news_trading.py — Unified News Trading Facade.

Covers: NewsTradingGate, Delta calculator, ESI, NFP/CPI prediction,
phase detection, LLM summary builder, and context aggregation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _future_event(minutes_ahead: int, title: str = "Non-Farm Payrolls",
                  currency: str = "USD", importance: str = "HIGH") -> dict:
    """Create a calendar event N minutes in the future."""
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)
    return {
        "title": title, "currency": currency, "country": currency,
        "time": t.strftime("%Y-%m-%d %H:%M:%S"),
        "importance": importance, "forecast": "175K", "previous": "206K",
    }


def _past_event(minutes_ago: int, title: str = "Non-Farm Payrolls",
                currency: str = "USD", importance: str = "HIGH",
                actual: str = "250K") -> dict:
    """Create a calendar event N minutes in the past."""
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "title": title, "currency": currency, "country": currency,
        "time": t.strftime("%Y-%m-%d %H:%M:%S"),
        "importance": importance, "forecast": "175K", "previous": "206K",
        "actual": actual,
    }


# ===========================================================================
# Event Tier Classification
# ===========================================================================
class TestEventTierClassification:
    def test_tier1_nfp(self):
        from core.news_trading import classify_event_tier
        assert classify_event_tier("Non-Farm Payrolls") == 1

    def test_tier1_cpi(self):
        from core.news_trading import classify_event_tier
        assert classify_event_tier("Consumer Price Index (CPI)") == 1

    def test_tier1_fomc(self):
        from core.news_trading import classify_event_tier
        assert classify_event_tier("FOMC Interest Rate Decision") == 1

    def test_tier2_retail_sales(self):
        from core.news_trading import classify_event_tier
        assert classify_event_tier("Retail Sales m/m") == 2

    def test_tier2_ism(self):
        from core.news_trading import classify_event_tier
        assert classify_event_tier("ISM Manufacturing PMI") == 2

    def test_tier3_trade_balance(self):
        from core.news_trading import classify_event_tier
        assert classify_event_tier("Trade Balance") == 3

    def test_unknown_event(self):
        from core.news_trading import classify_event_tier
        assert classify_event_tier("Random Stuff") == 0


# ===========================================================================
# Symbol Relevance
# ===========================================================================
class TestSymbolRelevance:
    def test_usd_relevant_to_eurusd(self):
        from core.news_trading import is_relevant_to_symbol
        assert is_relevant_to_symbol("USD", "EURUSD") is True

    def test_eur_relevant_to_eurusd(self):
        from core.news_trading import is_relevant_to_symbol
        assert is_relevant_to_symbol("EUR", "EURUSD") is True

    def test_gbp_not_relevant_to_usdjpy(self):
        from core.news_trading import is_relevant_to_symbol
        assert is_relevant_to_symbol("GBP", "USDJPY") is False

    def test_jpy_relevant_to_usdjpy(self):
        from core.news_trading import is_relevant_to_symbol
        assert is_relevant_to_symbol("JPY", "USDJPY") is True


# ===========================================================================
# News Trading Gate
# ===========================================================================
class TestNewsTradingGate:
    def test_clear_when_no_events(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        result = gate.evaluate("EURUSD", datetime.now(timezone.utc), [])
        assert result.allowed is True
        assert result.phase == "CLEAR"

    def test_clear_when_distant_event(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        events = [_future_event(180)]  # 3 hours away
        result = gate.evaluate("EURUSD", datetime.now(timezone.utc), events)
        assert result.allowed is True
        assert result.phase == "CLEAR"

    def test_pre_news_blocked(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        # Tier 1 event 20min away → within 60min blackout window
        events = [_future_event(20, title="Non-Farm Payrolls")]
        result = gate.evaluate("EURUSD", datetime.now(timezone.utc), events)
        assert result.allowed is False
        assert result.phase == "SPIKE_BLACKOUT"

    def test_spike_blackout_after_event(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        # Tier 1 event 5min ago → still in blackout
        events = [_past_event(5, title="Non-Farm Payrolls")]
        result = gate.evaluate("EURUSD", datetime.now(timezone.utc), events)
        assert result.allowed is False
        assert result.phase == "SPIKE_BLACKOUT"

    def test_post_news_entry_allowed(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        # Tier 1 event 30min ago → post-news entry zone
        events = [_past_event(30, title="Non-Farm Payrolls")]
        result = gate.evaluate("EURUSD", datetime.now(timezone.utc), events)
        assert result.allowed is True
        assert result.phase == "POST_NEWS_ENTRY"

    def test_trend_continuation(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        # Tier 1 event 2hrs ago → trend continuation
        events = [_past_event(120, title="Non-Farm Payrolls")]
        result = gate.evaluate("EURUSD", datetime.now(timezone.utc), events)
        assert result.allowed is True
        assert result.phase == "TREND_CONTINUATION"

    def test_irrelevant_currency_ignored(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        # GBP event should not block EURUSD
        events = [_future_event(5, title="Retail Sales", currency="GBP")]
        result = gate.evaluate("EURUSD", datetime.now(timezone.utc), events)
        assert result.allowed is True

    def test_tier2_shorter_blackout(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        # Tier 2 event 20min away → within 30min blackout
        events = [_future_event(20, title="Retail Sales", importance="HIGH")]
        result = gate.evaluate("EURUSD", datetime.now(timezone.utc), events)
        assert result.allowed is False
        assert result.phase == "SPIKE_BLACKOUT"

    def test_nearest_event_selected(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        events = [
            _future_event(120, title="Retail Sales", importance="HIGH"),
            _future_event(15, title="Non-Farm Payrolls"),
        ]
        result = gate.evaluate("EURUSD", datetime.now(timezone.utc), events)
        assert result.nearest_event is not None
        assert "Non-Farm" in result.nearest_event["title"]


# ===========================================================================
# Delta Calculator
# ===========================================================================
class TestDeltaCalculator:
    def test_minimal_surprise(self):
        from core.news_trading import calculate_delta
        result = calculate_delta(actual=176.0, forecast=175.0, historical_std=50.0)
        assert result["severity"] == "low"
        assert result["direction"] == "hawkish"

    def test_strong_surprise(self):
        from core.news_trading import calculate_delta
        result = calculate_delta(actual=230.0, forecast=175.0, historical_std=50.0)
        assert result["severity"] == "high"
        assert result["is_regime_shift"] is False

    def test_extreme_regime_shift(self):
        from core.news_trading import calculate_delta
        result = calculate_delta(actual=400.0, forecast=175.0, historical_std=50.0)
        assert result["severity"] == "critical"
        assert result["is_regime_shift"] is True

    def test_negative_surprise(self):
        from core.news_trading import calculate_delta
        result = calculate_delta(actual=100.0, forecast=175.0, historical_std=50.0)
        assert result["direction"] == "dovish"
        assert result["z_score"] < 0


# ===========================================================================
# ESI
# ===========================================================================
class TestEconomicSurpriseIndex:
    def test_all_beats(self):
        from core.news_trading import EconomicSurpriseIndex
        releases = [{"actual": 200, "consensus": 175}] * 10
        result = EconomicSurpriseIndex.calculate(releases)
        assert result["esi_score"] == 100.0
        assert result["bullish_bias"] is True

    def test_all_misses(self):
        from core.news_trading import EconomicSurpriseIndex
        releases = [{"actual": 150, "consensus": 175}] * 10
        result = EconomicSurpriseIndex.calculate(releases)
        assert result["esi_score"] == 0.0
        assert result["dovish_risk"] is True

    def test_empty_releases(self):
        from core.news_trading import EconomicSurpriseIndex
        result = EconomicSurpriseIndex.calculate([])
        assert result["esi_score"] == 50.0
        assert result["sample_size"] == 0

    def test_mixed_releases(self):
        from core.news_trading import EconomicSurpriseIndex
        releases = [
            {"actual": 200, "consensus": 175},
            {"actual": 160, "consensus": 175},
            {"actual": 190, "consensus": 175},
            {"actual": 170, "consensus": 175},
        ]
        result = EconomicSurpriseIndex.calculate(releases)
        assert result["esi_score"] == 50.0  # 2 beats out of 4


# ===========================================================================
# NFP Prediction
# ===========================================================================
class TestNPHPrediction:
    def test_strong_beat_prediction(self):
        from core.news_trading import predict_nfp
        result = predict_nfp(
            adp_change_k=250, adp_consensus_k=180,
            jobless_claims_4w_ma=200, jobless_claims_baseline=215,
            ism_employment_pmi=55.0,
            jolts_openings_millions=9.0, jolts_baseline=8.0,
            esi_score=70.0,
        )
        assert result["directional_bias"] in ("STRONG_BEAT", "MODERATE_BEAT")
        assert result["composite_score"] > 0
        assert result["confidence"] > 0.5

    def test_strong_miss_prediction(self):
        from core.news_trading import predict_nfp
        result = predict_nfp(
            adp_change_k=100, adp_consensus_k=180,
            jobless_claims_4w_ma=250, jobless_claims_baseline=215,
            ism_employment_pmi=45.0,
            jolts_openings_millions=7.5, jolts_baseline=8.0,
            esi_score=30.0,
        )
        assert result["directional_bias"] in ("STRONG_MISS", "MODERATE_MISS")
        assert result["composite_score"] < 0


# ===========================================================================
# CPI Prediction
# ===========================================================================
class TestCPIPrediction:
    def test_high_inflation_prediction(self):
        from core.news_trading import predict_cpi
        result = predict_cpi(
            ppi_mom_pct=0.5, ppi_consensus_pct=0.2,
            crude_oil_30d_change_pct=10.0,
            ism_prices_paid_pmi=60.0,
            wage_growth_yoy_pct=5.0,
        )
        assert result["composite_score"] > 0

    def test_low_inflation_prediction(self):
        from core.news_trading import predict_cpi
        result = predict_cpi(
            ppi_mom_pct=-0.3, ppi_consensus_pct=0.1,
            crude_oil_30d_change_pct=-15.0,
            ism_prices_paid_pmi=42.0,
            wage_growth_yoy_pct=3.0,
        )
        assert result["composite_score"] < 0


# ===========================================================================
# Phase Detector
# ===========================================================================
class TestPhaseDetector:
    def test_pre_news_distant(self):
        from core.news_trading import detect_news_phase
        now = datetime.now(timezone.utc)
        event = now + timedelta(minutes=90)
        result = detect_news_phase(event.isoformat(), now.isoformat())
        assert result["phase"] == "pre_news_distant"

    def test_pre_news_imminent(self):
        from core.news_trading import detect_news_phase
        now = datetime.now(timezone.utc)
        event = now + timedelta(minutes=30)
        result = detect_news_phase(event.isoformat(), now.isoformat())
        assert result["phase"] == "pre_news_imminent"

    def test_spike(self):
        from core.news_trading import detect_news_phase
        now = datetime.now(timezone.utc)
        event = now - timedelta(minutes=2)
        result = detect_news_phase(event.isoformat(), now.isoformat())
        assert result["phase"] == "spike"

    def test_post_news_entry(self):
        from core.news_trading import detect_news_phase
        now = datetime.now(timezone.utc)
        event = now - timedelta(minutes=20)
        result = detect_news_phase(event.isoformat(), now.isoformat())
        assert result["phase"] == "post_news_entry"

    def test_normal(self):
        from core.news_trading import detect_news_phase
        now = datetime.now(timezone.utc)
        event = now - timedelta(hours=3)
        result = detect_news_phase(event.isoformat(), now.isoformat())
        assert result["phase"] == "normal"


# ===========================================================================
# LLM Summary Builder
# ===========================================================================
class TestLLMSummaryBuilder:
    def test_no_events(self):
        from core.news_trading import build_news_summary_for_llm
        result = build_news_summary_for_llm("EURUSD", [])
        assert "No significant" in result

    def test_with_events(self):
        from core.news_trading import build_news_summary_for_llm
        events = [_future_event(60)]
        result = build_news_summary_for_llm("EURUSD", events)
        assert "Non-Farm" in result or "Upcoming" in result

    def test_with_esi(self):
        from core.news_trading import build_news_summary_for_llm
        esi = {"esi_score": 72.0, "momentum": "MODERATE_POSITIVE", "bullish_bias": True, "dovish_risk": False}
        result = build_news_summary_for_llm("EURUSD", [], esi=esi)
        assert "72" in result or "ESI" in result


# ===========================================================================
# Full Context Aggregation
# ===========================================================================
class TestNewsTradingContext:
    def test_returns_all_fields(self):
        from core.news_trading import get_news_trading_context
        result = get_news_trading_context("EURUSD", [])
        assert "gate" in result
        assert "esi" in result
        assert "summary_for_llm" in result
        assert result["gate"]["allowed"] is True

    def test_blocks_during_spike(self):
        from core.news_trading import get_news_trading_context
        events = [_past_event(3, title="Non-Farm Payrolls")]
        result = get_news_trading_context("EURUSD", events)
        assert result["gate"]["allowed"] is False
        assert result["gate"]["phase"] == "SPIKE_BLACKOUT"
