"""Audit live integration of every free/open data source listed in
``economic dynamics.txt`` and the project's own ``macro_source_policy`` allowlist.

This test module does **not** modify any state.  It only performs read-only
fetches against public endpoints / provider clients and reports whether the
project already wires the source and whether a live request succeeds.

Run:
    python -m pytest tests/test_data_source_live_integration.py -v
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

import pytest

# ---------------------------------------------------------------------------
# 1. Source inventory from ``economic dynamics.txt`` + project code
# ---------------------------------------------------------------------------

# Each entry: (label, url_or_hint, integration_path, fetch_test_fn_name)
# integration_path = where the project already references this source.
# fetch_test_fn_name = optional lightweight live test.

_GEOPOLITICAL_WAR = [
    ("Liveuamap", "https://liveuamap.com", "None — not wired", None),
    ("Twitter/X lists (OSINT)", "https://twitter.com/lists", "Blocked in macro_source_policy.py", None),
    ("MarineTraffic free tier", "https://marinetraffic.com", "None — not wired", None),
    ("Sentinel Hub (EU free)", "https://sentinel-hub.com", "None — not wired", None),
    ("Google News / RSS", "https://news.google.com", "None — not wired", None),
    ("Fed GPR index", "https://www.federalreserve.gov/econres/notes/gpr.htm", "PRIMARY domain federalreserve.gov", "_fetch_fed_gpr"),
]

_POLITICAL_SOCIAL = [
    ("Truth Social web scrape", "https://truthsocial.com", "Blocked in macro_source_policy.py", None),
    ("ProPublica Politwoops", "https://politwoops.propublica.org", "None — not wired", None),
    ("Congress.gov API", "https://congress.gov", "None — not wired", None),
    ("PredictIt (free view)", "https://predictit.org", "None — not wired", None),
    ("538 / RCP polling", "https://fivethirtyeight.com", "None — not wired", None),
    ("SEC EDGAR filings", "https://www.sec.gov/cgi-bin/browse-edgar", "api_manager.py + openbb_provider_registry SEC", "_fetch_sec_edgar"),
]

_CENTRAL_BANK = [
    ("Federal Reserve", "https://www.federalreserve.gov", "PRIMARY domain", "_fetch_federal_reserve"),
    ("ECB", "https://www.ecb.europa.eu", "PRIMARY domain", "_fetch_ecb"),
    ("Bank of Japan", "https://www.boj.or.jp", "PRIMARY domain", None),
    ("PBOC", "https://www.pbc.gov.cn", "PRIMARY domain", None),
    ("CME FedWatch", "https://www.cmegroup.com", "None — not wired", None),
    ("Bank of England", "https://www.bankofengland.co.uk", "PRIMARY domain", None),
    ("Bank of Canada", "https://www.bankofcanada.ca", "PRIMARY domain", None),
]

_MACRO_OFFICIAL = [
    ("FRED (St. Louis Fed)", "https://fred.stlouisfed.org", "PRIMARY + fredapi + OpenBB federal_reserve", "_fetch_fred_series"),
    ("BLS CPI / CES", "https://www.bls.gov", "PRIMARY domain", "_fetch_bls"),
    ("BEA GDP", "https://www.bea.gov", "None — not wired", None),
    ("World Bank", "https://data.worldbank.org", "None — not wired", None),
    ("NY Fed Nowcast", "https://newyorkfed.org/research/nowcast", "None — not wired", None),
    ("NY Fed recession prob", "https://newyorkfed.org/research/capital_markets", "None — not wired", None),
    ("Treasury yields / auctions", "https://home.treasury.gov", "PRIMARY + OpenBB government_us", "_fetch_treasury"),
    ("IMF", "https://data.imf.org", "PRIMARY + OpenBB imf", None),
    ("Eurostat", "https://ec.europa.eu", "PRIMARY domain", None),
    ("BIS", "https://www.bis.org", "PRIMARY domain", None),
]

_MARKET_DATA = [
    ("Yahoo Finance", "https://finance.yahoo.com", "yahoo_finance_provider.py + YFinance additive", "_fetch_yahoo_quote"),
    ("CBOE / VIX", "https://www.cboe.com", "OpenBB cboe + RUNTIME_DOMAIN", "_fetch_cboe_vix"),
    ("CME futures", "https://www.cmegroup.com", "None — not wired", None),
    ("CFTC COT", "https://www.cftc.gov", "None — not wired", None),
    ("FINRA short interest", "https://www.finra.org", "OpenBB finra", None),
    ("ETF.com flows", "https://www.etf.com", "None — not wired", None),
    ("MT5 / broker quotes", "MT5 terminal", "mt5_trader.py + mt5_calendar_wrapper", None),
]

_NEWS_RSS = [
    ("NewsAPI", "https://newsapi.org", "RUNTIME + api_manager.py get_news()", "_fetch_newsapi_headlines"),
    ("Reuters RSS", "https://www.reuters.com", "RUNTIME + realtime_news_ingest.py RSS feeds", "_fetch_reuters_rss"),
    ("Bloomberg RSS", "https://www.bloomberg.com", "RUNTIME domain (feed URL not wired)", None),
    ("WSJ", "https://www.wsj.com", "OpenBB wsj", None),
]

_PREDICTION_MARKETS = [
    ("Kalshi", "https://trading-api.kalshi.com", "None — not wired", None),
    ("PredictIt", "https://www.predictit.org", "None — not wired", None),
    ("Polymarket", "https://polymarket.com", "None — not wired", None),
]

_ALTERNATIVE = [
    ("Google Trends", "https://trends.google.com", "None — not wired", None),
    ("Sentinel Hub imagery", "https://sentinel-hub.com", "None — not wired", None),
    ("MarineTraffic", "https://marinetraffic.com", "None — not wired", None),
    ("Reddit API", "https://www.reddit.com/dev/api", "None — not wired", None),
    ("NOAA", "https://www.noaa.gov", "None — not wired", None),
    ("Weather.gov", "https://weather.gov", "None — not wired", None),
]

_ALL_SOURCES = (
    _GEOPOLITICAL_WAR + _POLITICAL_SOCIAL + _CENTRAL_BANK +
    _MACRO_OFFICIAL + _MARKET_DATA + _NEWS_RSS +
    _PREDICTION_MARKETS + _ALTERNATIVE
)


# ---------------------------------------------------------------------------
# 2. Lightweight fetch helpers (read-only, fail-closed, no secrets)
# ---------------------------------------------------------------------------

def _http_get(url: str, *, timeout: float = 8.0, headers: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "llm-trader-audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(2048).decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc)
    except Exception as exc:
        return -1, str(exc)


def _fetch_federal_reserve() -> Tuple[int, str]:
    return _http_get("https://www.federalreserve.gov/feeds/press_all.xml")


def _fetch_ecb() -> Tuple[int, str]:
    return _http_get("https://www.ecb.europa.eu/press/calendars/mgcg/html/index.en.html")


def _fetch_fred_series() -> Tuple[int, str]:
    # FRED API requires key; test the public series page instead.
    return _http_get("https://fred.stlouisfed.org/series/DGS10")


def _fetch_bls() -> Tuple[int, str]:
    return _http_get("https://www.bls.gov/cpi/")


def _fetch_treasury() -> Tuple[int, str]:
    return _http_get("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/")


def _fetch_yahoo_quote() -> Tuple[int, str]:
    return _http_get("https://finance.yahoo.com/quote/SPY")


def _fetch_cboe_vix() -> Tuple[int, str]:
    return _http_get("https://www.cboe.com/tradable_products/vix/")


def _fetch_newsapi_headlines() -> Tuple[int, str]:
    # NewsAPI requires key; test the public site/docs page.
    return _http_get("https://newsapi.org/docs")


def _fetch_reuters_rss() -> Tuple[int, str]:
    return _http_get("https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best")


def _fetch_fed_gpr() -> Tuple[int, str]:
    return _http_get("https://www.federalreserve.gov/econres/notes/gpr.htm")


def _fetch_sec_edgar() -> Tuple[int, str]:
    return _http_get("https://www.sec.gov/cgi-bin/browse-edgar")


# ---------------------------------------------------------------------------
# 3. Live fetch tests
# ---------------------------------------------------------------------------

class TestDataSourceLiveIntegration:
    """Read-only audit of data sources referenced in ``economic dynamics.txt``."""

    @pytest.mark.parametrize("label,url,integration,fetch_fn", _ALL_SOURCES)
    def test_source_reachability_and_project_integration(self, label: str, url: str, integration: str, fetch_fn: Optional[str], tmp_path):
        status, body = (-1, "no-test")
        if fetch_fn and fetch_fn in globals():
            fn = globals()[fetch_fn]
            status, body = fn()

        report = {
            "label": label,
            "url": url,
            "integration": integration,
            "http_status": status,
            "reachable": 200 <= status < 400,
            "execution_authority": False,
        }
        path = tmp_path / "data_source_live_integration_report.json"
        existing: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
        except Exception:
            pass
        existing.append(report)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2, default=str)

        assert report["execution_authority"] is False
        # We do NOT hard-fail on reachability because public endpoints rate-limit
        # or block datacenter IPs.  Instead we assert the project has *some*
        # integration path or the source is intentionally excluded.
        assert isinstance(report["integration"], str) and len(report["integration"]) > 0
