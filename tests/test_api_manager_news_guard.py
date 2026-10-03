from datetime import datetime, timedelta, timezone
import logging

from api_manager import APIManager


def _manager(cache):
    manager = APIManager.__new__(APIManager)
    manager.cache = cache
    manager.cache_ttl = 900
    manager.log = logging.getLogger("test-api-manager")
    return manager


def _calendar(events):
    return {"calendar:events": {"timestamp": datetime.now(timezone.utc).timestamp(), "data": events}}


def test_source_status_does_not_claim_unconfigured_sec_client_is_online():
    manager = APIManager.__new__(APIManager)
    manager.newsapi = None
    manager.fred = None
    manager.yahoo = None
    manager.sec = None
    assert manager.get_status() == {
        "NewsAPI": "OFFLINE",
        "FRED": "OFFLINE",
        "Yahoo Finance": "OFFLINE",
        "SEC": "OFFLINE",
    }


def test_news_headlines_cannot_prove_calendar_safety():
    manager = _manager({
        "newsapi:hot_topics": {
            "timestamp": datetime.now(timezone.utc).timestamp(),
            "data": [{"title": "Markets calm"}],
        }
    })
    assert manager.is_news_blackout("EURUSD") is True


def test_structured_calendar_allows_symbol_when_no_relevant_event():
    event_time = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    manager = _manager(_calendar([{"time": event_time, "currency": "GBP", "importance": 3}]))
    assert manager.is_news_blackout("EURUSD") is False


def test_structured_calendar_blocks_relevant_high_impact_event():
    event_time = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    manager = _manager(_calendar([{"time": event_time, "currency": "USD", "importance": 3}]))
    assert manager.is_news_blackout("EURUSD", threshold_minutes=15) is True


def test_malformed_calendar_fails_closed():
    manager = _manager(_calendar([{"currency": "USD", "importance": 3}]))
    assert manager.is_news_blackout("EURUSD") is True
