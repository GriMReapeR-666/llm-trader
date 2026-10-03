import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from data.api_manager import APIManager
from intelligence.market_filters import evaluate, is_event_blackout


class TestDataIngestionHardening:
    def test_api_key_pool_parsing_and_rotation(self, monkeypatch):
        monkeypatch.setenv("NEWSAPI_KEYS", "key_alpha,key_beta,key_gamma")
        monkeypatch.setenv("FRED_API_KEYS", "fred_1,fred_2")

        mgr = APIManager()
        assert mgr._newsapi_keys == ["key_alpha", "key_beta", "key_gamma"]
        assert mgr.newsapi_key == "key_alpha"
        assert mgr._newsapi_key_idx == 0

        assert mgr._fred_keys == ["fred_1", "fred_2"]
        assert mgr.fred_key == "fred_1"
        assert mgr._fred_key_idx == 0

        # Rotate NewsAPI key
        with patch("data.api_manager.NEWSAPI_AVAILABLE", True):
            with patch("data.api_manager.NewsApiClient", return_value=MagicMock()):
                rotated = mgr.rotate_key("newsapi")
                assert rotated is True
                assert mgr.newsapi_key == "key_beta"
                assert mgr._newsapi_key_idx == 1

        # Exponential rate-limit backoff. The policy is deliberate (see
        # APIManager.record_rate_limit): a 60s floor, doubling per consecutive
        # 429, a 3600s ceiling, and a separate 12h path for NewsAPI's *daily*
        # quota ("RateLimited" / "24 hour" / "too many requests recently").
        # An earlier revision doubled from the 10.0 post-rotation reset value
        # with no floor, so the first penalty was 20s; these assertions pin the
        # current policy instead.
        assert not mgr.is_service_backed_off("newsapi")
        mgr.record_rate_limit("newsapi")
        assert mgr.is_service_backed_off("newsapi")
        assert mgr._service_backoffs["newsapi"]["delay_sec"] == 60.0

        mgr.record_rate_limit("newsapi")
        assert mgr._service_backoffs["newsapi"]["delay_sec"] == 120.0

        # Daily-quota exhaustion backs off 12h, it does not merely double.
        mgr.record_rate_limit("newsapi", long_backoff=True)
        assert mgr._service_backoffs["newsapi"]["delay_sec"] == 43200.0

        # A successful key rotation CLEARS the penalty rather than compounding
        # it, and the window closes.
        with patch("data.api_manager.NEWSAPI_AVAILABLE", True):
            with patch("data.api_manager.NewsApiClient", return_value=MagicMock()):
                assert mgr.rotate_key("newsapi") is True
        assert mgr._service_backoffs["newsapi"]["delay_sec"] == 10.0
        assert mgr._service_backoffs["newsapi"]["last_429"] == 0.0
        assert not mgr.is_service_backed_off("newsapi")

    def test_is_event_blackout_pre_and_post_windows(self):
        t0 = datetime(2026, 9, 4, 12, 30, 0, tzinfo=timezone.utc)
        events = [
            {
                "title": "US Non-Farm Payrolls",
                "importance": 3,
                "impact": "HIGH",
                "time": t0.isoformat(),
            },
            {
                "title": "Minor Housing Survey",
                "importance": 1,
                "impact": "LOW",
                "time": (t0 - timedelta(hours=1)).isoformat(),
            }
        ]

        # 1. 10 minutes BEFORE Tier-1 event: In blackout
        t_pre = t0 - timedelta(minutes=10)
        is_bo, reason = is_event_blackout(events=events, now=t_pre, pre_event_minutes=15, post_event_minutes=5)
        assert is_bo is True
        assert "Non-Farm Payrolls" in str(reason)
        assert "T-10m" in str(reason)

        # 2. 3 minutes AFTER Tier-1 event: In blackout (post-event window)
        t_post = t0 + timedelta(minutes=3)
        is_bo, reason = is_event_blackout(events=events, now=t_post, pre_event_minutes=15, post_event_minutes=5)
        assert is_bo is True
        assert "Non-Farm Payrolls" in str(reason)
        assert "T+3m" in str(reason)

        # 3. 25 minutes BEFORE event: Outside blackout
        t_safe_pre = t0 - timedelta(minutes=25)
        is_bo, reason = is_event_blackout(events=events, now=t_safe_pre, pre_event_minutes=15, post_event_minutes=5)
        assert is_bo is False
        assert reason is None

        # 4. 10 minutes AFTER event: Outside blackout
        t_safe_post = t0 + timedelta(minutes=10)
        is_bo, reason = is_event_blackout(events=events, now=t_safe_post, pre_event_minutes=15, post_event_minutes=5)
        assert is_bo is False
        assert reason is None

    def test_evaluate_market_conditions_with_events(self):
        t0 = datetime(2026, 9, 4, 14, 0, 0, tzinfo=timezone.utc)
        now_in_blackout = datetime(2026, 9, 4, 13, 50, 0, tzinfo=timezone.utc)  # T - 10m

        events = [
            {
                "title": "FOMC Rate Decision",
                "importance": 3,
                "impact": "CRITICAL",
                "time": t0.isoformat(),
            }
        ]

        res = evaluate(
            now=now_in_blackout,
            events=events,
            volume_ratio=1.0,
            dom_available=True,
        )
        assert res.allowed is False
        assert any("FOMC Rate Decision" in r for r in res.reasons)
