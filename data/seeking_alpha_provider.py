"""Seeking Alpha Provider — live free scraper (Seeking Alpha only, no Yahoo).

Uses the same public surfaces as open-source GitHub scrapers
(OpenBB / Gamestonk-style session + ``/api/v3`` + RSS):

* Session warm-up on seekingalpha.com
* ``/api/v3/symbols/{ticker}/metrics`` — EPS/revenue estimates, surprises,
  quant ranks, growth, profitability (primary structured source)
* ``/api/v3/symbols/{ticker}/news`` — headlines when not Cloudflare-blocked
* ``/api/v3/symbols/{ticker}/transcripts`` — earnings call index
* ``/api/v3/symbol_data`` — company identity / ratings when available
* ``/api/sa/combined/{ticker}.xml`` and ``/feed.xml`` — real RSS

All returned rows are labeled ``source=seeking_alpha_*``. Hardcoded mocks and
Yahoo/yfinance fallbacks are not used.
"""
from __future__ import annotations

import logging
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger("SeekingAlphaProvider")

SA_NS = {"sa": "https://seekingalpha.com/api/1.0"}


# ── schemas ─────────────────────────────────────────────────────────────────

class CalendarEarningsRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    symbol: str
    company_name: str = ""
    release_date: str = ""
    time_of_day: str = "UNKNOWN"
    eps_consensus: Optional[float] = None
    revenue_consensus_b: Optional[float] = None
    fiscal_quarter: str = ""
    source: str = "seeking_alpha_metrics"

    @field_validator("symbol")
    @classmethod
    def _upper(cls, value: str) -> str:
        return str(value or "").upper()


class ForwardEstimateRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    symbol: str
    period: str
    source: str = "seeking_alpha_metrics"


class NewsArticleRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str
    symbol: str = "MARKET"
    author: str = ""
    published_at: str = ""
    article_url: str = ""
    sentiment_label: str = "NEUTRAL"
    sentiment_score: float = 0.5
    source: str = "seeking_alpha_rss"


class TranscriptPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    symbol: str
    quarter: str = ""
    title: str = ""
    date: str = ""
    key_takeaways: List[str] = Field(default_factory=list)
    executives_present: List[str] = Field(default_factory=list)
    qa_highlights: List[Dict[str, Any]] = Field(default_factory=list)
    transcript_summary: str = ""
    source: str = "seeking_alpha_api_v3"
    articles: List[Dict[str, Any]] = Field(default_factory=list)


class QuantRatingsPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    symbol: str
    quant_score: Optional[float] = None
    quant_rating: str = "UNKNOWN"
    quant_ranking: str = ""
    seeking_alpha_authors_rating: str = "UNKNOWN"
    seeking_alpha_authors_score: Optional[float] = None
    wall_street_rating: str = "UNKNOWN"
    wall_street_score: Optional[float] = None
    factor_grades: Dict[str, str] = Field(default_factory=dict)
    rating_distribution: Dict[str, int] = Field(default_factory=dict)
    source: str = "seeking_alpha_metrics"
    metrics_sample: Dict[str, Any] = Field(default_factory=dict)


def _retry_request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_retries: int = 2,
    base_delay: float = 0.5,
    timeout: float = 5.0,
    **kwargs: Any,
) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.2)
                log.warning("Seeking Alpha HTTP %s on %s; retry in %.1fs", response.status_code, url, delay)
                time.sleep(delay)
                continue
            return response
        except requests.ConnectionError as exc:
            # DNS failure or connection refused — retrying immediately will fail again
            last_exc = exc
            log.warning("Seeking Alpha connection error (%s); aborting request", exc)
            break
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= max_retries - 1:
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.2)
            log.warning("Seeking Alpha request error (%s); retry in %.1fs", exc, delay)
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Seeking Alpha request failed for {url}")


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or abs(number) == float("inf"):
        return None
    return number


def _billions(value: Any) -> Optional[float]:
    number = _safe_float(value)
    if number is None:
        return None
    return round(number / 1e9, 3) if abs(number) > 1e6 else round(number, 3)


def _unix_to_iso(value: Any) -> str:
    number = _safe_float(value)
    if number is None or number <= 0:
        return ""
    # Seeking Alpha metric dates are unix seconds.
    if number > 1e12:
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _sentiment_from_text(text: str) -> Tuple[str, float]:
    blob = (text or "").lower()
    bullish = sum(1 for w in ("beat", "surge", "rally", "bullish", "upgrade", "growth", "record", "raise") if w in blob)
    bearish = sum(1 for w in ("miss", "cut", "downgrade", "bearish", "lawsuit", "decline", "weak", "cancel") if w in blob)
    if bullish > bearish:
        return "BULLISH", min(0.95, 0.55 + 0.1 * (bullish - bearish))
    if bearish > bullish:
        return "BEARISH", max(0.05, 0.45 - 0.1 * (bearish - bullish))
    return "NEUTRAL", 0.5


def _grade_from_metric(value: Optional[float], *, higher_better: bool = True) -> str:
    if value is None:
        return "N/A"
    thresholds = (30, 15, 5, 0, -10) if higher_better else (10, 20, 30, 40, 60)
    letters = ["A", "B", "C", "D", "F"]
    for threshold, letter in zip(thresholds, letters):
        if (value >= threshold) if higher_better else (value <= threshold):
            return letter
    return "F"


def _rating_from_quant_ranks(
    overall: Optional[float],
    sector: Optional[float],
    beats: Optional[float],
    misses: Optional[float],
) -> Tuple[str, Optional[float], str]:
    """Derive a 1-5 style score from SA quant ranks + beat/miss history."""
    score = None
    if overall is not None and overall > 0:
        # Lower overall rank is better; map roughly into 1..5.
        # Rank 1 → ~5.0, rank 4500 → ~1.0 (universe ~4500 names).
        score = max(1.0, min(5.0, 5.0 - (float(overall) - 1.0) * (4.0 / 4499.0)))
    if score is None and beats is not None and misses is not None:
        total = float(beats) + float(misses)
        if total > 0:
            score = 1.0 + 4.0 * (float(beats) / total)
    if score is None:
        return "UNKNOWN", None, ""
    if score >= 4.5:
        rating = "STRONG_BUY"
    elif score >= 3.5:
        rating = "BUY"
    elif score >= 2.5:
        rating = "HOLD"
    elif score >= 1.5:
        rating = "SELL"
    else:
        rating = "STRONG_SELL"
    ranking = ""
    parts = []
    if overall is not None:
        parts.append(f"Overall #{int(overall)}")
    if sector is not None:
        parts.append(f"Sector #{int(sector)}")
    if parts:
        ranking = " | ".join(parts)
    return rating, round(float(score), 2), ranking


class SeekingAlphaProvider:
    """Live Seeking Alpha-only scraper."""

    BASE_URL = "https://seekingalpha.com"
    API_V3 = f"{BASE_URL}/api/v3"
    RSS_URL = f"{BASE_URL}/feed.xml"

    def __init__(self, api_key: Optional[str] = None, *, session: Optional[requests.Session] = None):
        self.api_key = api_key  # unused; kept for APIManager compatibility
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html, application/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._warmed = False
        self._last_error = ""
        self._last_ok_at = ""
        self._metrics_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._company_cache: Dict[str, str] = {}

    @property
    def is_live(self) -> bool:
        return bool(self._last_ok_at)

    def status(self) -> Dict[str, Any]:
        return {
            "provider": "Seeking Alpha",
            "mode": "seeking_alpha_only_scraper",
            "live": self.is_live,
            "last_ok_at": self._last_ok_at,
            "last_error": self._last_error,
            "yahoo_fallback": False,
        }

    def _mark_ok(self) -> None:
        self._last_ok_at = datetime.now(timezone.utc).isoformat()
        self._last_error = ""

    def _mark_error(self, message: str) -> None:
        self._last_error = str(message)[:500]
        log.warning("Seeking Alpha scrape issue: %s", self._last_error)

    def _warm_session(self) -> None:
        if self._warmed:
            return
        if time.time() < getattr(self, "_warm_retry_after", 0.0):
            return
        try:
            _retry_request(self.session, "GET", self.BASE_URL, max_retries=1)
            self._warmed = True
        except Exception as exc:
            self._warm_retry_after = time.time() + 300.0
            self._mark_error(f"session warm-up failed (backing off 300s): {exc}")

    def _get_json(self, path: str, params: Optional[Any] = None) -> Dict[str, Any]:
        self._warm_session()
        url = path if str(path).startswith("http") else f"{self.API_V3}{path}"
        response = _retry_request(self.session, "GET", url, params=params)
        if response.status_code == 403:
            raise RuntimeError(f"Cloudflare/anti-bot blocked {url}")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code} for {url}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"non-object JSON from {url}")
        self._mark_ok()
        return payload

    # ── metrics (core SA structured feed) ───────────────────────────────────

    def _metrics_map(self, symbol: str, *, ttl_sec: float = 300.0) -> Dict[str, Any]:
        key = symbol.upper()
        cached = self._metrics_cache.get(key)
        now = time.time()
        if cached and (now - cached[0]) < ttl_sec:
            return dict(cached[1])
        try:
            payload = self._get_json(f"/symbols/{key}/metrics")
            field_by_id = {
                str(item["id"]): (item.get("attributes") or {}).get("field")
                for item in (payload.get("included") or [])
                if item.get("type") == "metric_type"
            }
            out: Dict[str, Any] = {}
            for row in payload.get("data") or []:
                mid = str((((row.get("relationships") or {}).get("metric_type") or {}).get("data") or {}).get("id") or "")
                field = field_by_id.get(mid)
                if not field:
                    continue
                out[str(field)] = (row.get("attributes") or {}).get("value")
            self._metrics_cache[key] = (now, out)
            return dict(out)
        except Exception as exc:
            self._mark_error(f"metrics failed for {key}: {exc}")
            return dict(cached[1]) if cached else {}

    def _company_name(self, symbol: str, metrics: Optional[Dict[str, Any]] = None) -> str:
        key = symbol.upper()
        if key in self._company_cache:
            return self._company_cache[key]
        # Prefer RSS stock tag (reliable, free)
        try:
            articles = self._rss_articles(symbol=key, limit=1)
            if articles and articles[0].get("company_name"):
                self._company_cache[key] = str(articles[0]["company_name"])
                return self._company_cache[key]
        except Exception as exc:
            logger.debug("metrics export failed: %s", exc)
        # symbol_data when not blocked
        try:
            attrs = self._symbol_data(key)
            name = attrs.get("companyName")
            if name:
                self._company_cache[key] = str(name)
                return self._company_cache[key]
        except Exception as exc:
            logger.debug("_symbol_data operation failed: %s", exc)
        return f"{key} Corp"

    def _symbol_data(self, symbol: str) -> Dict[str, Any]:
        fields = [
            "marketCap", "quantRating", "authorsRating", "sellSideRating",
            "divYield", "peRatioFwd", "companyName",
        ]
        params: List[Tuple[str, str]] = [("slugs", symbol.upper())]
        params.extend(("fields[]", field) for field in fields)
        payload = self._get_json("/symbol_data", params=params)
        rows = payload.get("data") or []
        if not rows:
            return {}
        return dict(rows[0].get("attributes") or {})

    # ── 1. Calendar Earnings ────────────────────────────────────────────────

    def get_calendar_earnings(self, symbol: Optional[str] = None, date: Optional[str] = None) -> List[Dict[str, Any]]:
        target = (symbol or "AAPL").upper()
        metrics = self._metrics_map(target)
        if not metrics:
            return []
        company = self._company_name(target, metrics)
        rows: List[Dict[str, Any]] = []

        recent_date = date or _unix_to_iso(
            metrics.get("earnings_release_date")
            or metrics.get("earning_announce_date")
            or metrics.get("recent_earnings_date")
        )
        rows.append(CalendarEarningsRow(
            symbol=target,
            company_name=company,
            release_date=recent_date,
            time_of_day=str(metrics.get("earning_announce_time") or "UNKNOWN"),
            eps_consensus=_safe_float(metrics.get("eps_estimate") or metrics.get("earnings_eps_normalized_consensus_mean_quarterly")),
            revenue_consensus_b=_billions(metrics.get("revenue_estimate") or metrics.get("earnings_revenue_consensus_mean_quarterly")),
            fiscal_quarter="Recent / current quarter",
            source="seeking_alpha_metrics",
            **{
                "eps_actual": _safe_float(metrics.get("eps_actual") or metrics.get("eps_normalized_actual")),
                "revenue_actual_b": _billions(metrics.get("revenue_actual") or metrics.get("revenue_actual_quarterly")),
                "eps_surprise": _safe_float(metrics.get("eps_surprise")),
                "revenue_surprise": _safe_float(metrics.get("revenue_surprise")),
            },
        ).model_dump())

        # Forward quarter consensus (FQ1)
        fq1_eps = _safe_float(metrics.get("eps_estimate_fq1") or metrics.get("eps_consensus_estimate_non_gaap_quarterly"))
        fq1_rev = _safe_float(metrics.get("revenue_estimate_fq1") or metrics.get("consensus_revenue_estimates_quarterly"))
        if fq1_eps is not None or fq1_rev is not None:
            rows.append(CalendarEarningsRow(
                symbol=target,
                company_name=company,
                release_date="",
                time_of_day="UNKNOWN",
                eps_consensus=fq1_eps,
                revenue_consensus_b=_billions(fq1_rev),
                fiscal_quarter="FQ1 forward consensus",
                source="seeking_alpha_metrics",
            ).model_dump())
        return rows

    # ── 2. Forward EPS Estimates ────────────────────────────────────────────

    def get_forward_eps_estimates(self, symbol: str = "AAPL") -> List[Dict[str, Any]]:
        target = symbol.upper()
        metrics = self._metrics_map(target)
        if not metrics:
            return []
        rows: List[Dict[str, Any]] = []
        growth = _safe_float(metrics.get("eps_ltg") or metrics.get("diluted_eps_growth"))
        analysts_up = _safe_float(metrics.get("eps_revision_analysts_num_up_annual"))
        analysts_down = _safe_float(metrics.get("eps_revision_analysts_num_down_annual"))
        num_analysts = None
        if analysts_up is not None or analysts_down is not None:
            num_analysts = int((analysts_up or 0) + (analysts_down or 0)) or None

        quarterly = _safe_float(
            metrics.get("eps_estimate_fq1")
            or metrics.get("eps_consensus_estimate_non_gaap_quarterly")
            or metrics.get("earnings_eps_normalized_consensus_mean_quarterly")
        )
        if quarterly is not None:
            rows.append(ForwardEstimateRow(
                symbol=target,
                period="FQ1",
                source="seeking_alpha_metrics",
                **{
                    "eps_consensus": quarterly,
                    "eps_low": None,
                    "eps_high": None,
                    "num_analysts": num_analysts,
                    "yoy_growth_pct": growth,
                    "eps_gaap_consensus": _safe_float(metrics.get("eps_consensus_estimate_gaap_quarterly")),
                },
            ).model_dump())

        annual = _safe_float(
            metrics.get("eps_normalized_consensus_mean_fy1")
            or metrics.get("eps_consensus_estimate_non_gaap_annual")
            or metrics.get("eps_consensus_estimate_gaap_annual")
        )
        if annual is not None:
            rows.append(ForwardEstimateRow(
                symbol=target,
                period="FY1",
                source="seeking_alpha_metrics",
                **{
                    "eps_consensus": annual,
                    "eps_low": None,
                    "eps_high": None,
                    "num_analysts": num_analysts,
                    "yoy_growth_pct": growth,
                    "trailing_eps": _safe_float(metrics.get("diluted_eps") or metrics.get("diluted_eps_normalized")),
                },
            ).model_dump())
        return rows

    # ── 3. Forward Sales Estimates ──────────────────────────────────────────

    def get_forward_sales_estimates(self, symbol: str = "AAPL") -> List[Dict[str, Any]]:
        target = symbol.upper()
        metrics = self._metrics_map(target)
        if not metrics:
            return []
        rows: List[Dict[str, Any]] = []
        growth = _safe_float(metrics.get("revenue_growth"))
        analysts_up = _safe_float(metrics.get("revenue_revision_analysts_num_up_annual"))
        analysts_down = _safe_float(metrics.get("revenue_revision_analysts_num_down_annual"))
        num_analysts = None
        if analysts_up is not None or analysts_down is not None:
            num_analysts = int((analysts_up or 0) + (analysts_down or 0)) or None

        quarterly = _safe_float(
            metrics.get("revenue_estimate_fq1")
            or metrics.get("consensus_revenue_estimates_quarterly")
            or metrics.get("earnings_revenue_consensus_mean_quarterly")
        )
        if quarterly is not None:
            rows.append(ForwardEstimateRow(
                symbol=target,
                period="FQ1",
                source="seeking_alpha_metrics",
                **{
                    "sales_consensus_b": _billions(quarterly),
                    "sales_low_b": None,
                    "sales_high_b": None,
                    "num_analysts": num_analysts,
                    "yoy_growth_pct": growth,
                },
            ).model_dump())

        annual = _safe_float(metrics.get("consensus_revenue_estimates_annual") or metrics.get("total_revenue"))
        if annual is not None:
            rows.append(ForwardEstimateRow(
                symbol=target,
                period="FY1 / annual consensus",
                source="seeking_alpha_metrics",
                **{
                    "sales_consensus_b": _billions(annual),
                    "sales_low_b": None,
                    "sales_high_b": None,
                    "num_analysts": num_analysts,
                    "yoy_growth_pct": growth,
                },
            ).model_dump())
        return rows

    # ── 4. News (RSS primary, API secondary) ────────────────────────────────

    def get_news_articles(self, symbol: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 5), 30))
        articles: List[Dict[str, Any]] = []

        # Prefer real RSS — least blocked free path.
        articles.extend(self._rss_articles(symbol=symbol, limit=limit))

        # Enrich/replace with API v3 when Cloudflare allows it.
        if symbol:
            try:
                target = symbol.upper()
                payload = self._get_json(
                    f"/symbols/{target}/news",
                    params={
                        "id": target,
                        "include": "author,primaryTickers,secondaryTickers",
                        "page[size]": limit,
                        "page[number]": 1,
                    },
                )
                authors = {
                    item["id"]: (item.get("attributes") or {})
                    for item in (payload.get("included") or [])
                    if item.get("type") == "author"
                }
                api_articles: List[Dict[str, Any]] = []
                for item in payload.get("data") or []:
                    attrs = item.get("attributes") or {}
                    title = str(attrs.get("title") or "").strip()
                    if not title:
                        continue
                    author_rel = (((item.get("relationships") or {}).get("author") or {}).get("data") or {})
                    author_id = str(author_rel.get("id") or "")
                    author_name = (
                        (authors.get(author_id) or {}).get("nick")
                        or (authors.get(author_id) or {}).get("name")
                        or "Seeking Alpha"
                    )
                    label, score = _sentiment_from_text(title)
                    api_articles.append(NewsArticleRow(
                        title=title,
                        symbol=target,
                        author=str(author_name),
                        published_at=str(attrs.get("publishOn") or ""),
                        article_url=f"{self.BASE_URL}/news/{item.get('id')}",
                        sentiment_label=label,
                        sentiment_score=float(score),
                        source="seeking_alpha_api_v3",
                    ).model_dump())
                if api_articles:
                    articles = api_articles
            except Exception as exc:
                if not articles:
                    self._mark_error(f"news api unavailable, using RSS: {exc}")
                else:
                    log.debug("Seeking Alpha API v3 unavailable (%s), retained %d RSS articles", exc, len(articles))

        return articles[:limit]

    def _rss_articles(self, symbol: Optional[str], limit: int) -> List[Dict[str, Any]]:
        clean_sym = str(symbol or "").upper().replace("=X", "").replace("/", "")
        is_fx = len(clean_sym) == 6 and clean_sym.isalpha() and clean_sym in {
            "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
            "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "AUDJPY", "CADJPY", "EURAUD",
        }
        url = self.RSS_URL if (not symbol or is_fx) else f"{self.BASE_URL}/api/sa/combined/{clean_sym}.xml"
        try:
            self._warm_session()
            response = _retry_request(self.session, "GET", url)
            if response.status_code == 404 and symbol:
                log.debug("Seeking Alpha ticker RSS 404 for %s; falling back to market feed", symbol)
                url = self.RSS_URL
                response = _retry_request(self.session, "GET", url)
            if response.status_code != 200:
                raise RuntimeError(f"RSS HTTP {response.status_code}")
            root = ET.fromstring(response.text)
            channel = root.find("channel")
            if channel is None:
                return []
            out: List[Dict[str, Any]] = []
            for item in channel.findall("item")[:limit]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                author = (
                    item.findtext("sa:author_name", default="", namespaces=SA_NS)
                    or item.findtext("{http://purl.org/dc/elements/1.1/}creator")
                    or "Seeking Alpha"
                )
                stock = item.find("sa:stock", SA_NS)
                company = ""
                sym = (symbol or "MARKET").upper()
                if stock is not None:
                    sym = (stock.findtext("sa:symbol", default=sym, namespaces=SA_NS) or sym).upper()
                    company = stock.findtext("sa:company_name", default="", namespaces=SA_NS) or ""
                    if company and sym:
                        self._company_cache[sym] = company
                if not title:
                    continue
                label, score = _sentiment_from_text(title)
                row = NewsArticleRow(
                    title=title,
                    symbol=sym,
                    author=str(author),
                    published_at=pub,
                    article_url=link or self.BASE_URL,
                    sentiment_label=label,
                    sentiment_score=float(score),
                    source="seeking_alpha_rss",
                ).model_dump()
                row["company_name"] = company
                out.append(row)
            self._mark_ok()
            return out
        except Exception as exc:
            self._mark_error(f"RSS fetch failed: {exc}")
            return []

    # ── 5. Transcripts ──────────────────────────────────────────────────────

    def get_earnings_transcripts(self, symbol: str = "AAPL", quarter: Optional[str] = None) -> Dict[str, Any]:
        target = symbol.upper()
        # API first
        try:
            payload = self._get_json(
                f"/symbols/{target}/transcripts",
                params={
                    "id": target,
                    "include": "author,primaryTickers,secondaryTickers",
                    "page[size]": 8,
                    "page[number]": 1,
                },
            )
            articles = []
            selected = None
            for item in payload.get("data") or []:
                attrs = item.get("attributes") or {}
                title = str(attrs.get("title") or "")
                entry = {
                    "id": item.get("id"),
                    "title": title,
                    "published_at": attrs.get("publishOn"),
                    "is_paywalled": bool(attrs.get("isPaywalled")),
                    "url": f"{self.BASE_URL}/article/{item.get('id')}",
                }
                articles.append(entry)
                if selected is None:
                    selected = entry
                if quarter and quarter.lower() in title.lower():
                    selected = entry
                    break
            if selected:
                q_match = re.search(r"Q([1-4])\s*(20\d{2})", selected["title"], re.I)
                detected_q = f"Q{q_match.group(1)} {q_match.group(2)}" if q_match else (quarter or "")
                metrics = self._metrics_map(target)
                takeaways = [
                    f"Transcript: {selected['title']}",
                    f"Published: {selected.get('published_at')}",
                ]
                if _safe_float(metrics.get("eps_actual")) is not None:
                    takeaways.append(
                        f"SA metrics EPS actual={metrics.get('eps_actual')} vs estimate={metrics.get('eps_estimate')} "
                        f"(surprise={metrics.get('eps_surprise')})"
                    )
                if _safe_float(metrics.get("revenue_actual")) is not None:
                    takeaways.append(
                        f"SA metrics revenue actual_b={_billions(metrics.get('revenue_actual'))} "
                        f"vs estimate_b={_billions(metrics.get('revenue_estimate'))}"
                    )
                return TranscriptPayload(
                    symbol=target,
                    quarter=detected_q,
                    title=selected["title"],
                    date=str(selected.get("published_at") or "")[:10],
                    key_takeaways=takeaways,
                    executives_present=[],
                    qa_highlights=[],
                    transcript_summary=selected["title"],
                    source="seeking_alpha_api_v3",
                    articles=articles,
                ).model_dump()
        except Exception as exc:
            self._mark_error(f"transcripts api failed: {exc}")

        # RSS fallback: titles that look like earnings call transcripts
        rss = self._rss_articles(symbol=target, limit=30)
        transcript_hits = [a for a in rss if "transcript" in a.get("title", "").lower() or "earnings call" in a.get("title", "").lower()]
        if transcript_hits:
            hit = transcript_hits[0]
            if quarter:
                for candidate in transcript_hits:
                    if quarter.lower() in candidate.get("title", "").lower():
                        hit = candidate
                        break
            q_match = re.search(r"Q([1-4])\s*(20\d{2})", hit.get("title", ""), re.I)
            detected_q = f"Q{q_match.group(1)} {q_match.group(2)}" if q_match else (quarter or "")
            return TranscriptPayload(
                symbol=target,
                quarter=detected_q,
                title=hit.get("title", ""),
                date=str(hit.get("published_at") or "")[:16],
                key_takeaways=[hit.get("title", ""), f"From Seeking Alpha RSS by {hit.get('author')}"],
                executives_present=[],
                qa_highlights=[],
                transcript_summary=hit.get("title", ""),
                source="seeking_alpha_rss",
                articles=transcript_hits,
            ).model_dump()

        # Metrics-backed stub when listings are Cloudflare-blocked / absent from RSS
        metrics = self._metrics_map(target)
        takeaways: List[str] = []
        title = ""
        if metrics:
            takeaways.append(
                "Transcript article index blocked/empty; using Seeking Alpha metrics for earnings context."
            )
            eps_a = _safe_float(metrics.get("eps_actual"))
            eps_e = _safe_float(metrics.get("eps_estimate"))
            rev_a = _billions(metrics.get("revenue_actual"))
            rev_e = _billions(metrics.get("revenue_estimate"))
            if eps_a is not None or eps_e is not None:
                takeaways.append(
                    f"SA metrics EPS actual={eps_a} vs estimate={eps_e} "
                    f"(surprise={metrics.get('eps_surprise')})"
                )
                title = f"{target} earnings metrics (SA) — EPS actual {eps_a}"
            if rev_a is not None or rev_e is not None:
                takeaways.append(f"SA metrics revenue actual_b={rev_a} vs estimate_b={rev_e}")
            if metrics.get("recent_earnings_date"):
                takeaways.append(
                    f"SA recent_earnings_date={_unix_to_iso(metrics.get('recent_earnings_date'))}"
                )
        if not takeaways:
            takeaways = [
                "No Seeking Alpha transcript listing available from API, RSS, or metrics."
            ]
        return TranscriptPayload(
            symbol=target,
            quarter=quarter or "",
            title=title,
            date=_unix_to_iso(metrics.get("recent_earnings_date")) if metrics else "",
            key_takeaways=takeaways,
            executives_present=[],
            qa_highlights=[],
            transcript_summary=title or takeaways[0],
            source="seeking_alpha_metrics",
            articles=[],
        ).model_dump()

    # ── 6. Quant Ratings ────────────────────────────────────────────────────

    def get_quant_ratings(self, symbol: str = "AAPL") -> Dict[str, Any]:
        target = symbol.upper()
        metrics = self._metrics_map(target)
        if not metrics:
            return QuantRatingsPayload(symbol=target, source="seeking_alpha_metrics").model_dump()

        overall = _safe_float(metrics.get("quant_overall_rank"))
        sector = _safe_float(metrics.get("quant_sector_rank"))
        sub = _safe_float(metrics.get("quant_subindustry_rank"))
        beats = _safe_float(metrics.get("eps_beats"))
        misses = _safe_float(metrics.get("eps_misses"))
        rating, score, ranking = _rating_from_quant_ranks(overall, sector, beats, misses)
        if sub is not None:
            ranking = (ranking + f" | Sub-industry #{int(sub)}").strip(" |")

        authors_rating = "UNKNOWN"
        authors_score = None
        wall_rating = "UNKNOWN"
        wall_score = None
        try:
            attrs = self._symbol_data(target)
            authors_score = _safe_float(attrs.get("authorsRating"))
            wall_score = _safe_float(attrs.get("sellSideRating"))
            # If SA returns direct quantRating (1-5), prefer it.
            direct_quant = _safe_float(attrs.get("quantRating"))
            if direct_quant is not None:
                score = direct_quant
                if direct_quant >= 4.5:
                    rating = "STRONG_BUY"
                elif direct_quant >= 3.5:
                    rating = "BUY"
                elif direct_quant >= 2.5:
                    rating = "HOLD"
                elif direct_quant >= 1.5:
                    rating = "SELL"
                else:
                    rating = "STRONG_SELL"
            if authors_score is not None:
                authors_rating = (
                    "BUY" if authors_score >= 3.5 else
                    "HOLD" if authors_score >= 2.5 else "SELL"
                )
            if wall_score is not None:
                wall_rating = (
                    "STRONG_BUY" if wall_score >= 4.5 else
                    "BUY" if wall_score >= 3.5 else
                    "HOLD" if wall_score >= 2.5 else
                    "SELL" if wall_score >= 1.5 else "STRONG_SELL"
                )
        except Exception as exc:
            self._mark_error(f"symbol_data ratings unavailable: {exc}")

        factor_grades = {
            "Valuation": _grade_from_metric(_safe_float(metrics.get("pe_nongaap")), higher_better=False),
            "Growth": _grade_from_metric(_safe_float(metrics.get("revenue_growth")), higher_better=True),
            "Profitability": _grade_from_metric(_safe_float(metrics.get("net_margin")), higher_better=True),
            "Momentum": _grade_from_metric(_safe_float(metrics.get("momentum_6m")), higher_better=True),
            "Revisions": _grade_from_metric(_safe_float(metrics.get("latest_eps_revisions_fy")), higher_better=True),
        }
        sample = {
            key: metrics.get(key)
            for key in (
                "quant_overall_rank", "quant_sector_rank", "quant_subindustry_rank",
                "eps_beats", "eps_misses", "revenue_growth", "diluted_eps_growth",
                "net_margin", "momentum_6m", "latest_eps_revisions_fy", "pe_nongaap",
                "eps_estimate_fq1", "revenue_estimate_fq1",
            )
            if key in metrics
        }
        return QuantRatingsPayload(
            symbol=target,
            quant_score=score,
            quant_rating=rating,
            quant_ranking=ranking,
            seeking_alpha_authors_rating=authors_rating,
            seeking_alpha_authors_score=authors_score,
            wall_street_rating=wall_rating,
            wall_street_score=wall_score,
            factor_grades=factor_grades,
            rating_distribution={
                "eps_beats": int(beats or 0),
                "eps_misses": int(misses or 0),
                "revenue_beats": int(_safe_float(metrics.get("revenue_beats")) or 0),
                "revenue_misses": int(_safe_float(metrics.get("revenue_misses")) or 0),
            },
            source="seeking_alpha_metrics",
            metrics_sample=sample,
        ).model_dump()
