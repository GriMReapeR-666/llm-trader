"""Unit tests for Seeking Alpha-only provider (no Yahoo fallback)."""
from __future__ import annotations

from unittest.mock import patch

from seeking_alpha_provider import (
    SeekingAlphaProvider,
    NewsArticleRow,
    CalendarEarningsRow,
)


def _sample_metrics():
    return {
        "eps_estimate": 1.89,
        "revenue_estimate": 108_959_817_930.0,
        "eps_estimate_fq1": 1.97643,
        "revenue_estimate_fq1": 113_256_580_210.0,
        "eps_normalized_consensus_mean_fy1": 8.79342,
        "consensus_revenue_estimates_annual": 477_372_745_170.0,
        "eps_actual": 2.02,
        "revenue_actual": 109_417_000_000.0,
        "eps_surprise": 0.13,
        "recent_earnings_date": 1785384000,
        "eps_ltg": 10.88,
        "revenue_growth": 14.24,
        "diluted_eps": 8.71,
        "diluted_eps_growth": 32.6,
        "net_margin": 27.6,
        "momentum_6m": 12.6,
        "latest_eps_revisions_fy": 1.6,
        "pe_nongaap": 35.9,
        "quant_overall_rank": 819.0,
        "quant_sector_rank": 128.0,
        "quant_subindustry_rank": 13.0,
        "eps_beats": 8.0,
        "eps_misses": 0.0,
        "revenue_beats": 8.0,
        "revenue_misses": 0.0,
        "total_revenue": 466_823_000_000.0,
    }


def test_calendar_earnings_from_sa_metrics_only():
    sa = SeekingAlphaProvider()
    with patch.object(sa, "_metrics_map", return_value=_sample_metrics()), \
         patch.object(sa, "_company_name", return_value="Apple Inc."):
        rows = sa.get_calendar_earnings("AAPL")
    assert rows
    assert rows[0]["source"] == "seeking_alpha_metrics"
    assert "yahoo" not in rows[0]["source"]
    assert rows[0]["eps_consensus"] == 1.89
    assert rows[0]["release_date"] == "2026-07-30"
    CalendarEarningsRow.model_validate(rows[0])
    assert any(r.get("fiscal_quarter", "").startswith("FQ1") for r in rows)


def test_forward_eps_and_sales_from_sa_metrics():
    sa = SeekingAlphaProvider()
    with patch.object(sa, "_metrics_map", return_value=_sample_metrics()):
        eps = sa.get_forward_eps_estimates("AAPL")
        sales = sa.get_forward_sales_estimates("AAPL")
    assert eps[0]["source"] == "seeking_alpha_metrics"
    assert sales[0]["source"] == "seeking_alpha_metrics"
    assert eps[0]["eps_consensus"] == 1.97643
    assert any(r["period"].startswith("FY1") for r in eps)
    assert sales[0]["sales_consensus_b"] == 113.257


def test_news_prefers_rss_and_uses_api_when_available():
    sa = SeekingAlphaProvider()
    rss_rows = [{
        "title": "AAPL: Bullish upgrade after beat",
        "symbol": "AAPL",
        "author": "SA Author",
        "published_at": "Mon, 10 Aug 2026 08:16:08 -0400",
        "article_url": "https://seekingalpha.com/x",
        "sentiment_label": "BULLISH",
        "sentiment_score": 0.75,
        "source": "seeking_alpha_rss",
        "company_name": "Apple Inc.",
    }]
    api_payload = {
        "data": [{
            "id": "123",
            "type": "news",
            "attributes": {"title": "AAPL beats estimates in rally", "publishOn": "2026-08-10T12:00:00Z"},
            "relationships": {"author": {"data": {"id": "9", "type": "author"}}},
        }],
        "included": [{"id": "9", "type": "author", "attributes": {"nick": "Alpha Desk"}}],
    }
    with patch.object(sa, "_rss_articles", return_value=rss_rows), \
         patch.object(sa, "_get_json", return_value=api_payload):
        news = sa.get_news_articles(symbol="AAPL", limit=3)
    assert news[0]["source"] == "seeking_alpha_api_v3"
    NewsArticleRow.model_validate(news[0])


def test_news_falls_back_to_rss_when_api_blocked():
    sa = SeekingAlphaProvider()
    rss_rows = [{
        "title": "Apple gets rating cut",
        "symbol": "AAPL",
        "author": "Ravikash Bakolia",
        "published_at": "Mon, 10 Aug 2026 08:16:08 -0400",
        "article_url": "https://seekingalpha.com/symbol/AAPL/news",
        "sentiment_label": "BEARISH",
        "sentiment_score": 0.35,
        "source": "seeking_alpha_rss",
        "company_name": "Apple Inc.",
    }]
    with patch.object(sa, "_rss_articles", return_value=rss_rows), \
         patch.object(sa, "_get_json", side_effect=RuntimeError("Cloudflare")):
        news = sa.get_news_articles(symbol="AAPL", limit=2)
    assert news[0]["source"] == "seeking_alpha_rss"
    assert "yahoo" not in news[0]["source"]


def test_quant_ratings_from_sa_quant_ranks():
    sa = SeekingAlphaProvider()
    with patch.object(sa, "_metrics_map", return_value=_sample_metrics()), \
         patch.object(sa, "_symbol_data", side_effect=RuntimeError("blocked")):
        ratings = sa.get_quant_ratings("AAPL")
    assert ratings["source"] == "seeking_alpha_metrics"
    assert ratings["quant_rating"] in {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"}
    assert ratings["quant_score"] is not None
    assert "Overall #" in ratings["quant_ranking"]
    assert "Valuation" in ratings["factor_grades"]


def test_no_yahoo_dependency_in_module():
    import seeking_alpha_provider as mod

    assert not hasattr(mod, "YFINANCE_AVAILABLE")
    assert "yfinance" not in getattr(mod, "__dict__", {})
    # No import of yahoo companions
    assert "yahoo_finance_provider" not in dir(mod)
    sa = SeekingAlphaProvider()
    assert sa.status()["yahoo_fallback"] is False
    assert sa.status()["mode"] == "seeking_alpha_only_scraper"


def test_empty_when_sa_metrics_unavailable():
    sa = SeekingAlphaProvider()
    with patch.object(sa, "_metrics_map", return_value={}):
        assert sa.get_calendar_earnings("AAPL") == []
        assert sa.get_forward_eps_estimates("AAPL") == []
        assert sa.get_forward_sales_estimates("AAPL") == []
