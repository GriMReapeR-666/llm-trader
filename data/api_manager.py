#!/usr/bin/env python3
"""
API Manager - Centralized Economic Data Integration
Manages NewsAPI, FRED, SEC filing, Yahoo Finance, and OpenBB APIs with rate limiting
"""

import os
import time
import logging
import threading
import requests
from typing import Any, Callable, Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
from core.rate_limiter import MultiAPIRateLimiter


def _ds_note(src: str, outcome: str, note: str) -> None:
    """Best-effort boot-datasheet marker (never fatal, no-op when disabled)."""
    try:
        from intelligence.boot_datasheet import boot_datasheet_enabled, get_datasheet
        if boot_datasheet_enabled():
            get_datasheet().record_degraded(src, outcome, note)
    except Exception:
        pass


def _run_bounded(fn: Callable[[], Any], budget_s: float) -> Tuple[bool, Any, Optional[BaseException]]:
    """Run ``fn`` on a daemon thread with a hard wait-bound; never propagates.

    Returns ``(ok, result, error)``: ``ok`` is True when ``fn`` returned within
    the budget; a raised exception is reported via ``error`` (contained, not
    propagated); a deadline miss reports ``ok=False`` with a synthetic timeout
    error and abandons the (unkillable) worker thread — the caller fails open
    instead of hanging.
    """
    box: Dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # contained; re-raising would defeat the bound
            box["error"] = exc

    worker = threading.Thread(target=_target, name="api-manager-bounded", daemon=True)
    worker.start()
    worker.join(timeout=budget_s)
    if worker.is_alive():
        return False, None, TimeoutError(
            f"call exceeded {budget_s:.0f}s budget; abandoned (hung provider?)")
    if "error" in box:
        return False, None, box["error"]
    return True, box.get("result"), None

#: Response cache.  A bare relative name on purpose: the live bot runs from
#: the repo root.  Hoisted to a module constant so a test can repoint it
#: (tests/conftest.py::isolate_shared_api_cache) instead of every instance
#: re-deriving a path the suite would then write to the repo root.
API_CACHE_FILE = "api_cache.json"

# Optional imports with fallbacks
try:
    from newsapi import NewsApiClient
    NEWSAPI_AVAILABLE = True
except ImportError:
    NEWSAPI_AVAILABLE = False

try:
    from fredapi import Fred
    FRED_AVAILABLE = True
except ImportError:
    FRED_AVAILABLE = False

try:
    from data.yahoo_finance_provider import YahooFinanceProvider
    YAHOO_AVAILABLE = True
except ImportError:
    YAHOO_AVAILABLE = False

try:
    from data.openbb_data_bridge import OpenBBDataBridge
    OPENBB_AVAILABLE = True
except ImportError:
    OPENBB_AVAILABLE = False

try:
    from data.portfolio123_provider import Portfolio123Provider
    P123_AVAILABLE = True
except ImportError:
    P123_AVAILABLE = False

try:
    from data.seeking_alpha_provider import SeekingAlphaProvider
    SA_AVAILABLE = True
except ImportError:
    SA_AVAILABLE = False


class APIManager:
    """Centralized API management with rate limiting"""
    
    def __init__(self, store=None):
        self.log = logging.getLogger("APIManager")
        
        # Load API keys and multi-key rotation pools from environment
        newsapi_env = os.getenv("NEWSAPI_KEYS") or os.getenv("NEWSAPI_KEY") or ""
        self._newsapi_keys = [k.strip() for k in newsapi_env.split(",") if k.strip()]
        self._newsapi_key_idx = 0
        self.newsapi_key = self._newsapi_keys[0] if self._newsapi_keys else None

        fred_env = os.getenv("FRED_API_KEYS") or os.getenv("FRED_API_KEY") or ""
        self._fred_keys = [k.strip() for k in fred_env.split(",") if k.strip()]
        self._fred_key_idx = 0
        self.fred_key = self._fred_keys[0] if self._fred_keys else None

        # Exponential rate-limit backoff state per service
        self._service_backoffs: Dict[str, Dict[str, float]] = {
            "newsapi": {"last_429": 0.0, "delay_sec": 10.0},
            "fred": {"last_429": 0.0, "delay_sec": 10.0},
            "seeking_alpha": {"last_429": 0.0, "delay_sec": 10.0},
        }
        
        # Initialize clients
        self.newsapi = None
        self.fred = None
        self.yahoo = None
        self.openbb = None
        self.portfolio123 = None
        self.seeking_alpha = None
        self.sec = None  # SEC EDGAR client flag
        self._sec_cik_map: Dict[str, int] = {}
        self._sec_cik_map_loaded = False
        
        if P123_AVAILABLE:
            try:
                self.portfolio123 = Portfolio123Provider()
                self.log.info("✅ Portfolio123 provider initialized")
            except Exception as e:
                self.log.warning(f"Portfolio123 provider initialization failed: {e}")
        
        if SA_AVAILABLE:
            try:
                self.seeking_alpha = SeekingAlphaProvider()
                self.log.info("✅ Seeking Alpha provider initialized (all 6 data types)")
            except Exception as e:
                self.log.warning(f"Seeking Alpha provider initialization failed: {e}")
        
        if NEWSAPI_AVAILABLE and self.newsapi_key:
            try:
                self.newsapi = NewsApiClient(api_key=self.newsapi_key)
                self.log.info(f"✅ NewsAPI client initialized (pool size: {len(self._newsapi_keys)})")
            except Exception as e:
                self.log.warning(f"NewsAPI initialization failed: {e}")
        
        if FRED_AVAILABLE and self.fred_key:
            try:
                self.fred = Fred(api_key=self.fred_key)
                self.log.info(f"✅ FRED API client initialized (pool size: {len(self._fred_keys)})")
            except Exception as e:
                self.log.warning(f"FRED initialization failed: {e}")
        
        # Yahoo Finance (no API key needed)
        if YAHOO_AVAILABLE:
            try:
                self.yahoo = YahooFinanceProvider()
                self.log.info("✅ Yahoo Finance client initialized")
            except Exception as e:
                self.log.warning(f"Yahoo Finance initialization failed: {e}")
        
        # OpenBB Data Bridge (no API keys needed for registered providers)
        if OPENBB_AVAILABLE:
            try:
                self.openbb = OpenBBDataBridge(store=store)
                self.log.info(f"✅ OpenBB Data Bridge initialized ({self.openbb.provider_count()} active providers)")
            except Exception as e:
                self.log.warning(f"OpenBB Data Bridge initialization failed: {e}")
        
        # SEC EDGAR (no API key needed, just CIK resolution)
        self.sec = True  # Mark as available; actual HTTP calls use rate limiter
        self.log.info("✅ SEC EDGAR client marked available (CIK resolution lazy-loaded)")
        
        # Rate limiters
        self.rate_limiter = MultiAPIRateLimiter()
        self.rate_limiter.add_limiter("newsapi", max_requests=100, time_window=60)  # 100/min
        self.rate_limiter.add_limiter("fred", max_requests=120, time_window=60)     # 120/min
        self.rate_limiter.add_limiter("sec", max_requests=10, time_window=1)        # 10/sec
        
        # Cache to avoid duplicate requests
        self.cache_file = API_CACHE_FILE
        self.cache_ttl = 900  # 15 minutes
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        """Load cache from disk"""
        if os.path.exists(self.cache_file):
            try:
                import json
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                self.log.warning(f"Failed to load cache: {e}")
                return {}
        return {}

    def _save_cache(self):
        """Save cache to disk"""
        try:
            import json
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f)
        except Exception as e:
            self.log.warning(f"Failed to save cache: {e}")
    
    def _get_cache_key(self, api_name: str, params: str) -> str:
        """Generate cache key"""
        return f"{api_name}:{params}"
    
    def _is_cache_valid(self, cache_entry: Dict[str, Any]) -> bool:
        """Check if cache entry is still valid"""
        if not cache_entry:
            return False
        age = time.time() - cache_entry.get("timestamp", 0)
        return age < self.cache_ttl

    def _load_sec_cik_map(self) -> bool:
        """Lazy-load SEC ticker-to-CIK mapping from sec.gov."""
        if self._sec_cik_map_loaded:
            return bool(self._sec_cik_map)
        self._sec_cik_map_loaded = True
        cik_cache = "artifacts/sec_cik_map.json"
        try:
            import json
            if os.path.exists(cik_cache):
                with open(cik_cache, "r") as f:
                    raw = json.load(f)
                self._sec_cik_map = {v["ticker"]: int(v["cik_str"]) for v in raw.values() if "ticker" in v and "cik_str" in v}
                if self._sec_cik_map:
                    self.log.info("Loaded %d SEC CIK mappings from cache", len(self._sec_cik_map))
                    return True
        except Exception as e:
            self.log.debug("Failed to load SEC CIK cache: %s", e)
        # Fetch fresh from SEC
        try:
            from core.http_retry import request_with_retry
            headers = {"User-Agent": "Trading Bot contact@example.com"}
            resp = request_with_retry(None, "GET", "https://www.sec.gov/files/company_tickers.json",
                                      headers=headers, timeout=15, max_retries=2)
            if resp.status_code == 200:
                data = resp.json()
                self._sec_cik_map = {v["ticker"]: int(v["cik_str"]) for v in data.values() if "ticker" in v and "cik_str" in v}
                os.makedirs(os.path.dirname(cik_cache), exist_ok=True)
                with open(cik_cache, "w") as f:
                    json.dump(data, f)
                self.log.info("Fetched and cached %d SEC CIK mappings", len(self._sec_cik_map))
                return bool(self._sec_cik_map)
        except Exception as e:
            self.log.warning("Failed to fetch SEC CIK map: %s", e)
        return False

    def _resolve_sec_cik(self, ticker: str) -> Optional[int]:
        """Resolve a ticker symbol to a numeric SEC CIK."""
        if not self._sec_cik_map_loaded:
            self._load_sec_cik_map()
        return self._sec_cik_map.get(ticker.upper())

    def is_service_backed_off(self, service: str) -> bool:
        """Check if service is currently in rate-limit backoff."""
        bo = self._service_backoffs.get(service)
        if not bo or bo["last_429"] <= 0:
            return False
        return (time.time() - bo["last_429"]) < bo["delay_sec"]

    def record_rate_limit(self, service: str, long_backoff: bool = False) -> None:
        """Record a 429 rate limit or quota exhaustion with exponential backoff."""
        if service not in self._service_backoffs:
            self._service_backoffs[service] = {"last_429": 0.0, "delay_sec": 10.0}
        bo = self._service_backoffs[service]
        bo["last_429"] = time.time()
        if long_backoff:
            bo["delay_sec"] = 43200.0  # 12 hours for daily quota exhaustion
        else:
            bo["delay_sec"] = min(3600.0, max(bo["delay_sec"] * 2.0, 60.0))
        self.log.warning("Rate limit / quota hit for %s. Backoff set to %.1fs", service, bo["delay_sec"])

    def rotate_key(self, service: str, long_backoff: bool = False) -> bool:
        """Rotate to next key in key pool for the specified service."""
        if service == "newsapi" and len(self._newsapi_keys) > 1:
            self._newsapi_key_idx = (self._newsapi_key_idx + 1) % len(self._newsapi_keys)
            self.newsapi_key = self._newsapi_keys[self._newsapi_key_idx]
            self.log.info("Rotated NewsAPI key to index %d/%d", self._newsapi_key_idx + 1, len(self._newsapi_keys))
            if NEWSAPI_AVAILABLE:
                try:
                    self.newsapi = NewsApiClient(api_key=self.newsapi_key)
                    self._service_backoffs["newsapi"]["delay_sec"] = 10.0
                    self._service_backoffs["newsapi"]["last_429"] = 0.0
                    return True
                except Exception as e:
                    self.log.warning("Failed to reinitialize NewsAPI with rotated key: %s", e)
        elif service == "fred" and len(self._fred_keys) > 1:
            self._fred_key_idx = (self._fred_key_idx + 1) % len(self._fred_keys)
            self.fred_key = self._fred_keys[self._fred_key_idx]
            self.log.info("Rotated FRED key to index %d/%d", self._fred_key_idx + 1, len(self._fred_keys))
            if FRED_AVAILABLE:
                try:
                    self.fred = Fred(api_key=self.fred_key)
                    self._service_backoffs["fred"]["delay_sec"] = 10.0
                    self._service_backoffs["fred"]["last_429"] = 0.0
                    return True
                except Exception as e:
                    self.log.warning("Failed to reinitialize FRED with rotated key: %s", e)
        self.record_rate_limit(service, long_backoff=long_backoff)
        return False

    def get_news(self, keywords: List[str], language: str = "en", page_size: int = 5) -> List[Dict[str, Any]]:
        """
        Fetch news from NewsAPI
        """
        if not self.newsapi or self.is_service_backed_off("newsapi"):
            if self.is_service_backed_off("newsapi"):
                self.log.debug("NewsAPI is currently backed off due to rate limits.")
                _ds_note("newsapi.get_news", "SKIP", "service backed off")
            elif not self.newsapi:
                self.log.warning("NewsAPI not available. Skipping news fetch.")
                _ds_note("newsapi.get_news", "SKIP", "not configured")
            return []
        
        cache_key = self._get_cache_key("newsapi", ",".join(keywords))
        cached = self.cache.get(cache_key)
        
        if self._is_cache_valid(cached):
            self.log.debug(f"Using cached news for: {keywords}")
            return cached["data"]
        
        try:
            self.rate_limiter.wait_if_needed("newsapi")
            
            query = " OR ".join(keywords)
            from_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

            def _call() -> Dict[str, Any]:
                return self.newsapi.get_everything(
                    q=query,
                    language=language,
                    from_param=from_date,
                    sort_by="relevancy",
                    page_size=page_size
                )

            ok, response, err = _run_bounded(
                _call, float(os.getenv("NEWSAPI_BUDGET_S", "15")))
            if not ok:
                # Fail open with the LAST GOOD data instead of an empty list:
                # a stale headline is still an informative headline, and the
                # caller sees a bounded, degraded fetch rather than a hang.
                if cached and "data" in cached:
                    self.log.warning("NewsAPI unavailable (%s); serving stale cache for %s", err, keywords)
                    _ds_note("newsapi.get_news", "STALE", "budget/hang; stale cache served")
                    return cached["data"]
                self.log.warning("NewsAPI unavailable (%s); no cached news - returning empty", err)
                _ds_note("newsapi.get_news", "EMPTY", "budget/hang; no cache")
                return []
            
            articles = []
            for article in response.get("articles", []):
                articles.append({
                    "source": article["source"]["name"],
                    "title": article["title"],
                    "description": article.get("description", ""),
                    "url": article["url"],
                    "published_at": article["publishedAt"],
                    "sentiment": self._estimate_sentiment(article["title"])
                })
            
            # Cache result
            self.cache[cache_key] = {
                "timestamp": time.time(),
                "data": articles
            }
            self._save_cache()
            _ds_note("newsapi.get_news", "FRESH", "%d articles" % len(articles))
            
            self.log.info(f"Fetched {len(articles)} news articles for: {keywords}")
            return articles
            
        except Exception as e:
            err_str = str(e).lower()
            is_quota = "ratelimited" in err_str or "24 hour" in err_str or "too many requests recently" in err_str
            is_rate = is_quota or "429" in err_str or "rate" in err_str or "too many" in err_str
            if is_rate:
                self.rotate_key("newsapi", long_backoff=is_quota)
                if not is_quota:
                    self.log.warning("NewsAPI rate limited: %s", e)
            else:
                self.log.error("NewsAPI error: %s", e)
            if cached and "data" in cached:
                self.log.warning("NewsAPI failed (%s); serving stale cache for %s", e, keywords)
                _ds_note("newsapi.get_news", "STALE", str(e)[:60])
                return cached["data"]
            _ds_note("newsapi.get_news", "EMPTY", str(e)[:60])
            return []
    
    def get_hot_topics(self, limit: int = 3) -> List[Dict[str, Any]]:
        """
        Fetch top business/finance headlines (Hot Topics)
        """
        if not self.newsapi or self.is_service_backed_off("newsapi"):
            return []
            
        cache_key = self._get_cache_key("newsapi", "hot_topics")
        cached = self.cache.get(cache_key)
        
        if self._is_cache_valid(cached):
            self.log.debug("Using cached hot topics")
            return cached["data"]
            
        try:
            self.rate_limiter.wait_if_needed("newsapi")
            
            def _call() -> Dict[str, Any]:
                return self.newsapi.get_top_headlines(
                    q=None,
                    category="business",
                    language="en",
                    page_size=limit
                )

            ok, response, err = _run_bounded(
                _call, float(os.getenv("NEWSAPI_BUDGET_S", "15")))
            if not ok:
                if cached and "data" in cached:
                    self.log.warning("Hot topics unavailable (%s); serving stale cache", err)
                    _ds_note("newsapi.get_hot_topics", "STALE", "budget/hang; stale cache served")
                    return cached["data"]
                self.log.warning("Hot topics unavailable (%s); no cached headlines - returning empty", err)
                _ds_note("newsapi.get_hot_topics", "EMPTY", "budget/hang; no cache")
                return []
            
            articles = []
            for article in response.get("articles", []):
                articles.append({
                    "source": article["source"]["name"],
                    "title": article["title"],
                    "description": article.get("description", ""),
                    "url": article["url"],
                    "published_at": article["publishedAt"],
                    "sentiment": self._estimate_sentiment(article["title"])
                })
                
            # Cache result
            self.cache[cache_key] = {
                "timestamp": time.time(),
                "data": articles
            }
            self._save_cache()
            _ds_note("newsapi.get_hot_topics", "FRESH", "%d headlines" % len(articles))
            
            self.log.info(f"Fetched {len(articles)} hot topics (incl. geopolitics)")
            return articles
            
        except Exception as e:
            err_str = str(e).lower()
            is_quota = "ratelimited" in err_str or "24 hour" in err_str or "too many requests recently" in err_str
            is_rate = is_quota or "429" in err_str or "rate" in err_str or "too many" in err_str
            if is_rate:
                self.rotate_key("newsapi", long_backoff=is_quota)
                if not is_quota:
                    self.log.warning("Hot topics rate limited: %s", e)
            else:
                self.log.error("Hot topics fetch failed: %s", e)
            if cached and "data" in cached:
                self.log.warning("Hot topics failed (%s); serving stale cache", e)
                _ds_note("newsapi.get_hot_topics", "STALE", str(e)[:60])
                return cached["data"]
            _ds_note("newsapi.get_hot_topics", "EMPTY", str(e)[:60])
            return []

    def _estimate_sentiment(self, text: str) -> str:
        """
        Simple sentiment estimation based on keywords
        """
        text_lower = text.lower()
        
        bullish_words = ["surge", "rally", "gain", "rise", "positive", "growth", "strong"]
        bearish_words = ["fall", "drop", "decline", "negative", "weak", "plunge", "crash"]
        
        bullish_count = sum(1 for word in bullish_words if word in text_lower)
        bearish_count = sum(1 for word in bearish_words if word in text_lower)
        
        if bullish_count > bearish_count:
            return "BULLISH"
        elif bearish_count > bullish_count:
            return "BEARISH"
        else:
            return "NEUTRAL"
    
    def get_fred_data(self, series_ids: List[str]) -> Dict[str, Any]:
        """
        Fetch macro data from FRED
        """
        if not self.fred or self.is_service_backed_off("fred"):
            if self.is_service_backed_off("fred"):
                self.log.debug("FRED API is currently backed off due to rate limits.")
                _ds_note("fred.get_fred_data", "SKIP", "service backed off")
            elif not self.fred:
                self.log.warning("FRED API not available. Skipping macro data fetch.")
                _ds_note("fred.get_fred_data", "SKIP", "not configured")
            return {}
        
        cache_key = self._get_cache_key("fred", ",".join(series_ids))
        cached = self.cache.get(cache_key)
        
        if self._is_cache_valid(cached):
            self.log.debug(f"Using cached FRED data for: {series_ids}")
            return cached["data"]
        
        results = {}
        
        for series_id in series_ids:
            try:
                self.rate_limiter.wait_if_needed("fred")

                def _call(series_id=series_id):
                    return self.fred.get_series(series_id)

                ok, series, err = _run_bounded(
                    _call, float(os.getenv("FRED_BUDGET_S", "20")))
                if not ok:
                    # Fail open per series: log, keep what we have, keep
                    # walking the batch (the batch-level network-abort merge
                    # below already preserves cached series).
                    self.log.warning("FRED %s unavailable (%s); skipping series", series_id, err)
                    _ds_note("fred.get_series", "SKIP", "%s budget/hang" % series_id)
                    continue
                if series is not None and len(series) > 0:
                    latest_value = float(series.iloc[-1])
                    latest_date = series.index[-1].strftime("%Y-%m-%d")
                    previous_value = None
                    if len(series) >= 2:
                        try:
                            previous_value = float(series.iloc[-2])
                        except (TypeError, ValueError, OverflowError):
                            previous_value = None
                    row = {
                        "value": latest_value,
                        "date": latest_date,
                        "previous": previous_value,
                    }
                    if previous_value is not None:
                        try:
                            row["surprise"] = latest_value - previous_value
                        except (TypeError, ValueError, OverflowError):
                            pass
                    results[series_id] = row
                    _ds_note("fred.get_series", "FRESH", "%s (as of %s)" % (series_id, latest_date))
                    self.log.debug(f"FRED {series_id}: {latest_value} (as of {latest_date})")
                
            except Exception as e:
                err_str = str(e).lower()
                is_net_err = (
                    "getaddrinfo" in err_str
                    or "10053" in err_str
                    or "10054" in err_str
                    or "connection" in err_str
                    or "timed out" in err_str
                    or "remote end closed" in err_str
                )
                if is_net_err:
                    self.log.warning(
                        "FRED network connection aborted (%s). Stopping batch early to preserve cached series.",
                        e,
                    )
                    _ds_note("fred.get_fred_data", "ABORT", "net abort; cached series kept")
                    if cached and "data" in cached:
                        merged = dict(cached["data"])
                        merged.update(results)
                        results = merged
                    break
                self.log.warning("FRED error for %s: %s", series_id, e)
                if "429" in err_str or "rate" in err_str or "too many" in err_str:
                    self.rotate_key("fred")
        
        # Cache result
        if results:
            self.cache[cache_key] = {
                "timestamp": time.time(),
                "data": results
            }
            self._save_cache()
        
        self.log.info(f"Fetched {len(results)} FRED series")
        return results
    
    def get_sec_filings(self, ticker: str, filing_types: List[str] = ["8-K", "10-Q"]) -> List[Dict[str, Any]]:
        """
        Fetch recent SEC filings with proper CIK resolution
        """
        cache_key = self._get_cache_key("sec", f"{ticker}:{','.join(filing_types)}")
        cached = self.cache.get(cache_key)
        
        if self._is_cache_valid(cached):
            self.log.debug(f"Using cached SEC filings for: {ticker}")
            return cached["data"]
        
        # Resolve ticker to numeric CIK
        cik = self._resolve_sec_cik(ticker)
        if cik is None:
            self.log.warning(f"SEC CIK not found for ticker: {ticker}")
            return []
        
        try:
            self.rate_limiter.wait_if_needed("sec")
            
            headers = {
                "User-Agent": "Trading Bot contact@example.com"
            }
            
            url = f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json"
            from core.http_retry import request_with_retry
            response = request_with_retry(None, "GET", url, headers=headers, timeout=10, max_retries=3)
            
            if response.status_code == 200:
                data = response.json()
                filings = data.get("filings", {}).get("recent", {})
                
                results = []
                for i in range(min(5, len(filings.get("form", [])))):
                    form = filings["form"][i]
                    if form in filing_types:
                        results.append({
                            "form": form,
                            "filing_date": filings["filingDate"][i],
                            "accession_number": filings["accessionNumber"][i],
                            "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={str(cik).zfill(10)}&type={form}"
                        })
                
                # Cache result
                self.cache[cache_key] = {
                    "timestamp": time.time(),
                    "data": results
                }
                self._save_cache()
                
                self.log.info(f"Fetched {len(results)} SEC filings for {ticker} (CIK={cik})")
                return results
            else:
                self.log.warning(f"SEC API returned status {response.status_code} for CIK {cik}")
                return []
                
        except Exception as e:
            self.log.error(f"SEC filing error for {ticker}: {e}")
            return []
    
    def get_market_indices(self) -> Dict[str, Dict[str, Any]]:
        """Get major market indices from Yahoo Finance"""
        if not self.yahoo:
            return {}
        
        try:
            return self.yahoo.get_market_indices()
        except Exception as e:
            self.log.error(f"Failed to get market indices: {e}")
            return {}
    
    def get_currency_strength(self) -> Dict[str, float]:
        """Get currency strength from Yahoo Finance"""
        if not self.yahoo:
            return {}
        
        try:
            return self.yahoo.get_currency_strength()
        except Exception as e:
            self.log.error(f"Failed to get currency strength: {e}")
            return {}
    
    def get_market_sentiment(self) -> Dict[str, Any]:
        """Get market sentiment from Yahoo Finance"""
        if not self.yahoo:
            return {}
        
        try:
            return self.yahoo.get_market_sentiment()
        except Exception as e:
            self.log.error(f"Failed to get market sentiment: {e}")
            return {}
    
    def get_status(self) -> Dict[str, str]:
        """Get status of all API integrations"""
        status = {
            "NewsAPI": "ONLINE" if self.newsapi else "OFFLINE",
            "FRED": "ONLINE" if self.fred else "OFFLINE",
            "Yahoo Finance": "ONLINE" if self.yahoo else "OFFLINE",
            "SEC": "ONLINE" if self.sec else "OFFLINE",
        }
        # Some diagnostics/tests construct a manager without running the full
        # provider initializer. Missing provider attributes must not crash
        # health reporting. Include OpenBB only for initialized managers to
        # preserve the stable legacy status shape for partial diagnostics.
        if hasattr(self, "openbb"):
            status["OpenBB"] = "ONLINE" if getattr(self, "openbb", None) else "OFFLINE"
        if hasattr(self, "portfolio123"):
            status["Portfolio123"] = "ONLINE" if getattr(self, "portfolio123", None) else "OFFLINE"
        if hasattr(self, "seeking_alpha"):
            sa = getattr(self, "seeking_alpha", None)
            if sa is None:
                status["Seeking Alpha"] = "OFFLINE"
            elif hasattr(sa, "is_live") and not sa.is_live and getattr(sa, "_last_error", ""):
                status["Seeking Alpha"] = "DEGRADED"
            else:
                # Instantiated scraper is available; live flag flips after first successful fetch.
                status["Seeking Alpha"] = "ONLINE" if sa is not None else "OFFLINE"
        return status

    def is_news_blackout(self, symbol: str, threshold_minutes: int = 15) -> bool:
        """
        Check a *structured* cached calendar for a high-impact event.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            return True
        try:
            threshold = float(threshold_minutes)
        except (TypeError, ValueError):
            return True
        if not threshold >= 0:
            return True

        cached = self.cache.get("calendar:events") or self.cache.get("calendar_events")
        if not isinstance(cached, dict) or not self._is_cache_valid(cached):
            self.log.warning("Structured calendar unavailable; fail-closed news blackout for %s", symbol)
            return True
        events = cached.get("data")
        if not isinstance(events, list):
            return True

        symbol_upper = symbol.upper()
        currencies = {symbol_upper[:3], symbol_upper[3:]} if len(symbol_upper) >= 6 else {symbol_upper}
        now = datetime.now(timezone.utc)
        for event in events:
            if not isinstance(event, dict):
                return True
            raw_time = event.get("time", event.get("timestamp", event.get("datetime")))
            raw_currency = event.get("currency")
            raw_importance = event.get("importance")
            if not raw_time or not isinstance(raw_currency, str):
                return True
            try:
                importance = int(raw_importance)
            except (TypeError, ValueError):
                return True
            if importance < 2 or raw_currency.upper() not in currencies:
                continue
            try:
                event_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
                event_time = event_time.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                return True
            if abs((event_time - now).total_seconds()) <= threshold * 60:
                return True
        return False

    def is_holiday(self) -> bool:
        """Check if today is a major banking holiday (UTC based)"""
        today = datetime.utcnow().strftime("%m-%d")
        holidays = [
            "01-01", # New Year
            "12-25", # Christmas
        ]
        weekday = datetime.utcnow().weekday()
        if weekday >= 5:
             return True
             
        if today in holidays:
            return True
        return False
