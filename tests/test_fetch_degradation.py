"""Graceful degradation of the FRED/NewsAPI fetchers and the bootstrap
PREPARE data-source stages.

Night of 2026-09-30 the FRED and news API calls failed across the board and
each failure burned 38-94s of silent boot time (requests with no effective
timeout inside third-party clients, exception-only guards, no per-call
bound). These tests pin the corrected behavior:

* every provider call is wait-bounded (a hung call is abandoned, not waited on);
* on failure the fetchers serve the last good cached data, never an
  unexplained silent empty list;
* bootstrap PREPARE stages are deadline-bounded and disclose per-step elapsed
  times in the completion log.
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from data.api_manager import APIManager, _run_bounded

# NOTE: APIManager's response cache (api_cache.json) used to be a bare relative
# name, so constructing APIManager() with no arguments rewrote the file at the
# REPO ROOT.  The isolation now lives in conftest
# (isolate_shared_api_cache) rather than here: it was a per-module fixture at
# first, and three other modules (test_boot_datasheet, test_data_ingestion_
# hardening, test_openbb_data_bridge) construct APIManager() with no arguments
# too, so each one had to be fixed separately until the shared file got a
# suite-wide owner.


# ---------------------------------------------------------------------------
# _run_bounded helper
# ---------------------------------------------------------------------------

def test_run_bounded_returns_result():
    ok, result, err = _run_bounded(lambda: 42, 5.0)
    assert ok is True
    assert result == 42
    assert err is None


def test_run_bounded_contains_exception():
    def boom():
        raise RuntimeError("provider 500")
    ok, result, err = _run_bounded(boom, 5.0)
    assert ok is False
    assert result is None
    assert isinstance(err, RuntimeError)


def test_run_bounded_abandons_hung_call():
    started = time.monotonic()
    ok, result, err = _run_bounded(lambda: time.sleep(5), 0.4)
    elapsed = time.monotonic() - started
    assert elapsed < 3.0                 # caller is NOT blocked 5s
    assert ok is False
    assert result is None
    assert isinstance(err, TimeoutError)
    assert "budget" in str(err)


# ---------------------------------------------------------------------------
# APIManager.get_news / get_hot_topics / get_fred_data degradation
# ---------------------------------------------------------------------------

def _manager_with_cache(monkeypatch, cache):
    monkeypatch.setenv("NEWSAPI_BUDGET_S", "0.4")
    monkeypatch.delenv("NEWSAPI_KEYS", raising=False)
    monkeypatch.delenv("NEWSAPI_KEY", raising=False)
    monkeypatch.delenv("FRED_API_KEYS", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    mgr = APIManager()
    # Simulate a configured client whose network call hangs past the budget.
    mgr.newsapi = MagicMock()
    mgr.cache = dict(cache)
    return mgr


def test_get_news_hung_call_fails_open_with_stale_cache(monkeypatch):
    mgr = _manager_with_cache(monkeypatch, {"newsapi:EURUSD": {
        "timestamp": time.time() - 7200,   # stale (ttl 900s)
        "data": [{"source": "Old", "title": "stale headline"}],
    }})

    def hang(**kwargs):
        time.sleep(5)
    mgr.newsapi.get_everything = hang

    started = time.monotonic()
    articles = mgr.get_news(["EURUSD"])
    elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert articles == [{"source": "Old", "title": "stale headline"}]


def test_get_news_hung_call_without_cache_returns_empty(monkeypatch):
    mgr = _manager_with_cache(monkeypatch, {})

    def hang(**kwargs):
        time.sleep(5)
    mgr.newsapi.get_everything = hang

    started = time.monotonic()
    articles = mgr.get_news(["EURUSD"])
    elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert articles == []


def test_get_hot_topics_hung_call_fails_open_with_stale_cache(monkeypatch):
    mgr = _manager_with_cache(monkeypatch, {"newsapi:hot_topics": {
        "timestamp": time.time() - 7200,
        "data": [{"source": "Reuters", "title": "stale top story"}],
    }})

    def hang(**kwargs):
        time.sleep(5)
    mgr.newsapi.get_top_headlines = hang

    started = time.monotonic()
    topics = mgr.get_hot_topics()
    elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert topics == [{"source": "Reuters", "title": "stale top story"}]


def test_get_fred_data_hung_series_fails_open_and_continues_batch(monkeypatch):
    monkeypatch.setenv("FRED_BUDGET_S", "0.4")
    monkeypatch.delenv("FRED_API_KEYS", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    mgr = APIManager()
    mgr.fred = MagicMock()
    mgr.cache = {}

    calls = {"n": 0}

    def flaky_get_series(series_id):
        # First series hangs past its budget; the rest must still be attempted.
        calls["n"] += 1
        if series_id == "CPIAUCSL":
            time.sleep(5)
        import pandas as pd
        return pd.Series([1.0, 2.0], index=pd.DatetimeIndex(
            ["2026-01-01", "2026-02-01"]))

    mgr.fred.get_series = flaky_get_series

    started = time.monotonic()
    results = mgr.get_fred_data(["CPIAUCSL", "DGS10"])
    elapsed = time.monotonic() - started

    assert elapsed < 5.0                    # the hung series did not eat the batch
    assert calls["n"] == 2                  # batch continued past the abandoned series
    assert "DGS10" in results               # healthy series still fetched
    assert "CPIAUCSL" not in results        # abandoned series simply missing


def test_get_fred_data_all_series_hung_returns_empty_not_hang(monkeypatch):
    monkeypatch.setenv("FRED_BUDGET_S", "0.4")
    monkeypatch.delenv("FRED_API_KEYS", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    mgr = APIManager()
    mgr.fred = MagicMock()
    mgr.cache = {}
    mgr.fred.get_series = lambda series_id: time.sleep(5)

    started = time.monotonic()
    results = mgr.get_fred_data(["CPIAUCSL", "DGS10", "FEDFUNDS"])
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert results == {}


# ---------------------------------------------------------------------------
# Bootstrap PREPARE bounding
# ---------------------------------------------------------------------------

def test_boot_stage_timeout_fails_open_with_marker():
    from core.autonomous_bootstrap import _boot_stage

    def hang():
        time.sleep(5)

    started = time.monotonic()
    result = _boot_stage("bootstrap_probe", hang, 0.4)
    elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert result["status"] == "TIMEOUT"
    assert "budget" in result["error"]


def test_boot_stage_exception_contained():
    from core.autonomous_bootstrap import _boot_stage

    def boom():
        raise RuntimeError("yahoo down")

    result = _boot_stage("bootstrap_probe", boom, 5.0)
    assert result == {"status": "ERROR", "error": "yahoo down"}


def test_boot_stage_success_passes_result_through():
    from core.autonomous_bootstrap import _boot_stage
    assert _boot_stage("bootstrap_probe", lambda: {"k": 1}, 5.0) == {"k": 1}


# ---------------------------------------------------------------------------
# Scheduled-agent external calls: same deadline pattern outside the boot path
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_datasheet(monkeypatch):
    """Isolate the datasheet singleton for the degradation-marker assertions."""
    from intelligence.boot_datasheet import BootDatasheet
    from intelligence import boot_datasheet as ds_mod
    sheet = BootDatasheet()
    monkeypatch.setattr(ds_mod, "_ds_singleton", sheet)
    yield sheet
    sheet.reset()


def test_live_indicator_fetcher_hung_series_bounded(monkeypatch):
    """LiveIndicatorFetcher's raw .fred.get_series must be wait-bounded: a hung
    series is abandoned at the budget and the snapshot is honest MISSING."""
    from core.news_indicator_fetcher import LiveIndicatorFetcher

    mgr = MagicMock()
    mgr.fred.get_series = lambda *a, **kw: time.sleep(5)
    monkeypatch.setenv("FRED_BUDGET_S", "1")
    fetcher = LiveIndicatorFetcher(mgr)

    started = time.monotonic()
    snap = fetcher._fetch_fred_series("PAYEMS")
    elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert snap.status == "MISSING" and snap.source == "FRED_FALLBACK"


def test_live_indicator_fetcher_happy_path_unchanged(monkeypatch):
    from core.news_indicator_fetcher import LiveIndicatorFetcher
    import pandas as pd

    mgr = MagicMock()
    mgr.fred.get_series = lambda sid: pd.Series(
        [100.0, 101.0], index=pd.DatetimeIndex(
            ["2026-09-01", "2026-09-02"]))
    fetcher = LiveIndicatorFetcher(mgr)

    snap = fetcher._fetch_fred_series("PAYEMS")
    assert snap.status == "OK" and snap.value == 101.0 and snap.previous == 100.0


def test_query_seeking_alpha_hung_call_bounded(monkeypatch):
    """query_seeking_alpha_news's live fetch must be wait-bounded: past the
    budget the tool returns honest OFFLINE, never hangs the agent."""
    import core.trading_tools as tt

    def hung_provider(*a, **kw):
        prov = MagicMock()
        prov.get_news_articles = lambda *a2, **kw2: time.sleep(5)
        prov.status.return_value = {"ok": False}
        return prov

    monkeypatch.setattr(
        "data.seeking_alpha_provider.SeekingAlphaProvider", hung_provider)
    monkeypatch.setenv("SEEKING_ALPHA_BUDGET_S", "1")

    started = time.monotonic()
    res = tt.query_seeking_alpha_news.invoke({"symbol": "EURUSD", "limit": 3})
    elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert res["source"] == "OFFLINE_PROVIDER_UNAVAILABLE"
    assert res["article_count"] == 0


def test_query_seeking_alpha_happy_path_unchanged(monkeypatch):
    import core.trading_tools as tt

    fake_prov = MagicMock()
    fake_prov.get_news_articles.return_value = [
        {"title": "Euro upside surprise", "sentiment_label": "BULLISH"}]
    fake_prov.get_quant_ratings.return_value = {"quant_rating": "4.5"}
    fake_prov.status.return_value = {"ok": True}
    monkeypatch.setattr(
        "data.seeking_alpha_provider.SeekingAlphaProvider",
        lambda *a, **kw: fake_prov)

    res = tt.query_seeking_alpha_news.invoke({"symbol": "EURUSD", "limit": 3})
    assert res["source"] == "LIVE_SEEKING_ALPHA"
    assert res["market_sentiment"] == "BULLISH"


def test_query_seeking_alpha_timeout_records_datasheet_marker(monkeypatch):
    """A timed-out Seeking Alpha call must land a degradation marker on the
    boot datasheet so the provider-scorecard trend sees it."""
    import core.trading_tools as tt
    from intelligence.boot_datasheet import get_datasheet

    def hung_provider(*a, **kw):
        prov = MagicMock()
        prov.get_news_articles = lambda *a2, **kw2: time.sleep(5)
        prov.status.return_value = {"ok": False}
        return prov

    monkeypatch.setattr(
        "data.seeking_alpha_provider.SeekingAlphaProvider", hung_provider)
    monkeypatch.setenv("SEEKING_ALPHA_BUDGET_S", "1")

    tt.query_seeking_alpha_news.invoke({"symbol": "EURUSD", "limit": 3})

    summary = get_datasheet().summary()
    assert summary["degraded_markers"] >= 1
    assert "seeking_alpha" in " ".join(summary["degraded_sources"])


def test_query_fred_macro_series_hung_call_bounded(monkeypatch):
    """query_fred_macro_series's managed-APIManager path must be wait-bounded.

    (The fake get_fred_data raises no rate-limit string, so the tool proceeds
    to the OFFLINE fallback after the bounded fetch is abandoned.)"""
    import core.trading_tools as tt

    hung = MagicMock()
    hung.get_fred_data = lambda ids: time.sleep(5)
    monkeypatch.setenv("FRED_BUDGET_S", "1")
    with patch("data.api_manager.APIManager", return_value=hung):
        started = time.monotonic()
        res = tt.query_fred_macro_series.invoke(
            {"series_id": "CPIAUCSL", "limit": 3})
        elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert res["source"].startswith("OFFLINE")
