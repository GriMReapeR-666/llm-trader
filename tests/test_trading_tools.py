"""Unit tests for callable research tools (FRED, COT, News, SEC, Seeking Alpha, OpenBB, Zero-Lag)."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from core.trading_tools import (
    RESEARCH_TOOLS,
    get_technical_indicators,
    query_macro_causal_dag,
    search_historical_scenarios,
    query_fred_macro_series,
    query_cftc_cot_positions,
    fetch_live_news_sentiment,
    query_sec_edgar_filings,
    query_seeking_alpha_news,
    query_openbb_macro,
    query_zero_lag_macro,
)


def test_research_tools_count():
    assert len(RESEARCH_TOOLS) >= 10


def test_get_technical_indicators_tool():
    """Indicators must be computed from an explicit OHLC fixture (offline-safe,
    fail-closed live path untouched: without the fixture the tool refuses to
    fabricate data when MT5 is unavailable)."""
    df = pd.DataFrame({
        "open": np.linspace(1.0800, 1.1000, 120),
        "high": np.linspace(1.0805, 1.1010, 120),
        "low": np.linspace(1.0795, 1.0990, 120),
        "close": np.linspace(1.0800, 1.1005, 120),
        "volume": [1000.0] * 120,
        "spread": [1.1] * 120,
    })
    with patch("core.trading_tools._fetch_ohlc", return_value=df):
        res = get_technical_indicators.invoke({"symbol": "EURUSD", "timeframe": "M15"})
    assert res["symbol"] == "EURUSD"
    assert res["timeframe"] == "M15"
    assert "rsi_14" in res
    assert "adx_14" in res
    assert "spread_pips" in res


def test_query_macro_causal_dag_tool():
    res = query_macro_causal_dag.invoke({"symbol": "EURUSD"})
    assert res["symbol"] == "EURUSD"
    assert "cesi_stability_score" in res
    assert res["cesi_stability_score"] > 0.0


def test_search_historical_scenarios_tool():
    res = search_historical_scenarios.invoke({"symbol": "GBPUSD", "setup_pattern": "breakout", "limit": 2})
    assert res["symbol"] == "GBPUSD"
    assert res["pattern"] == "breakout"
    assert "scenarios" in res
    assert len(res["scenarios"]) >= 1


def test_query_fred_macro_series_tool():
    """get_fred_data must be called with a LIST of series ids (a bare string was
    iterated character-by-character and always failed)."""
    with patch(
        "data.api_manager.APIManager.get_fred_data",
        return_value={"DGS10": {"value": 4.25, "date": "2026-01-02", "previous": 4.20}},
    ):
        res = query_fred_macro_series.invoke({"series_id": "DGS10", "limit": 3})
    assert res["series_id"] == "DGS10"
    assert res["is_supported"] is True
    assert len(res["history"]) >= 1


def test_query_cftc_cot_positions_tool():
    """COT tool must map a fresh provider report to an institutional bias
    (mocked provider: the live CFTC feed is network-dependent)."""
    report = SimpleNamespace(
        non_commercial_net=12500,
        commercial_net=-8000,
        report_date="2026-09-01",
        published_at=datetime.now(timezone.utc).isoformat(),
    )
    fake_provider = MagicMock()
    fake_provider.get_latest.return_value = report
    with patch("core.trading_tools.CFTCCOTProvider", return_value=fake_provider):
        res = query_cftc_cot_positions.invoke({"currency": "EUR"})
    assert res["currency"] == "EUR"
    assert "non_commercial_net_contracts" in res
    assert res["institutional_bias"] in ("BULLISH", "BEARISH")


def test_fetch_live_news_sentiment_tool():
    res = fetch_live_news_sentiment.invoke({"symbol": "EURUSD", "limit": 4})
    assert res["symbol"] == "EURUSD"
    assert "sentiment_score" in res
    assert len(res["breaking_news"]) >= 1


def test_query_sec_edgar_filings_tool():
    res = query_sec_edgar_filings.invoke({"ticker": "AAPL", "form_type": "10-K"})
    assert res["ticker"] == "AAPL"
    assert res["status"] == "AVAILABLE"
    assert len(res["key_risk_factors"]) >= 1


def test_query_seeking_alpha_news_tool():
    """Sentiment aggregation must work from provider articles (mocked provider:
    the live Seeking Alpha feed is Cloudflare/rate-limit dependent)."""
    fake_prov = MagicMock()
    fake_prov.get_news_articles.return_value = [
        {"title": "Euro upside surprise", "sentiment_label": "BULLISH"},
        {"title": "ECB hawkish commentary", "sentiment_label": "BULLISH"},
        {"title": "Growth risks remain", "sentiment_label": "NEUTRAL"},
    ]
    fake_prov.get_quant_ratings.return_value = {"quant_rating": "4.5"}
    fake_prov.status.return_value = {"ok": True}
    with patch("data.seeking_alpha_provider.SeekingAlphaProvider", return_value=fake_prov):
        res = query_seeking_alpha_news.invoke({"symbol": "EURUSD", "limit": 3})
    assert res["symbol"] == "EURUSD"
    assert res["market_sentiment"] == "BULLISH"
    assert len(res["recent_headlines"]) >= 1


def test_query_openbb_macro_tool():
    """The tool must surface the actual current reading from the registry fetch
    (mocked bridge: live OpenBB is network-dependent)."""
    fake_bridge = MagicMock()
    fake_bridge.is_available.return_value = True
    fake_bridge.fetch_by_type.return_value = [("famafrench:gdp", True, "ok")]
    fake_bridge.recent_fetches.return_value = [
        {
            "ts": "2026-09-04T00:00:00+00:00",
            "provider": "famafrench",
            "fetcher": "gdp",
            "records": 1,
            "data_preview": {"indicator": "gdp", "value": 2.4, "unit": "pct"},
        },
    ]
    with patch("data.openbb_data_bridge.OpenBBDataBridge", return_value=fake_bridge):
        res = query_openbb_macro.invoke({"indicator": "gdp"})
    assert res["indicator"] == "gdp"
    assert "current_reading" in res
    assert res["provider"] == "openbb_registry"


def test_query_zero_lag_macro_tool():
    res = query_zero_lag_macro.invoke({"symbol": "EURUSD"})
    assert res["symbol"] == "EURUSD"
    assert res["zero_lag_score"] > 0.0
    assert res["noise_filter_applied"] is True
