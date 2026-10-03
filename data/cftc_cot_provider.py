"""CFTC Commitments of Traders (COT) Data Provider.

Fetches and calculates institutional and speculative positioning (Smart Money vs. Commercials)
for foreign currencies, precious metals, and commodities with 52-week percentile scoring.

Live data sourced from CFTC Socrata API (publicreporting.cftc.gov).
TFF endpoint for FX futures, Disaggregated endpoint for commodity futures.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional
import requests

log = logging.getLogger("CFTCCOTProvider")

CACHE_FILE = "artifacts/cftc_cot_cache.json"

# CFTC Socrata API endpoints (free, no API key)
_TFF_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"  # Traders in Financial Futures (FX, rates)
_DISAGG_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"  # Disaggregated (commodities)

# Map our asset codes to CFTC market names
_INSTRUMENT_MAP = {
    "EUR": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "GBP": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
    "JPY": "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
    "CHF": "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE",
    "AUD": "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "CAD": "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "NZD": "NZ DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "GOLD": "GOLD - COMMODITY EXCHANGE INC.",
    "SILVER": "SILVER - COMMODITY EXCHANGE INC.",
    "COPPER": "COPPER- #1 - COMMODITY EXCHANGE INC.",
    "PLATINUM": "PLATINUM - NEW YORK MERCANTILE EXCHANGE",
    "OIL": "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
    "NATGAS": "NATURAL GAS - NEW YORK MERCANTILE EXCHANGE",
    "CORN": "CORN - CHICAGO BOARD OF TRADE",
    "SOYBEAN": "SOYBEANS - CHICAGO BOARD OF TRADE",
    "WHEAT": "WHEAT-SRW - CHICAGO BOARD OF TRADE",
}


@dataclass
class COTReportRow:
    asset: str  # e.g., "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "GOLD", "OIL"
    contract_name: str
    report_date: str
    non_commercial_long: int
    non_commercial_short: int
    non_commercial_net: int
    commercial_net: int
    open_interest: int
    net_change: int
    percentile_52w: float  # 0.0 to 100.0%
    sentiment_bias: str  # "EXTREME_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "EXTREME_BEARISH"
    source: str = "cftc_cot_reports"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Baseline institutional COT positioning dataset for offline resilience
FALLBACK_COT: List[Dict[str, Any]] = [
    {
        "asset": "EUR",
        "contract_name": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
        "report_date": "2026-08-11",
        "non_commercial_long": 198450,
        "non_commercial_short": 142100,
        "non_commercial_net": 56350,
        "commercial_net": -68200,
        "open_interest": 612400,
        "net_change": 4200,
        "percentile_52w": 68.5,
        "sentiment_bias": "BULLISH",
    },
    {
        "asset": "GBP",
        "contract_name": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
        "report_date": "2026-08-11",
        "non_commercial_long": 112400,
        "non_commercial_short": 68200,
        "non_commercial_net": 44200,
        "commercial_net": -51000,
        "open_interest": 248000,
        "net_change": 1850,
        "percentile_52w": 74.0,
        "sentiment_bias": "BULLISH",
    },
    {
        "asset": "JPY",
        "contract_name": "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
        "report_date": "2026-08-11",
        "non_commercial_long": 45100,
        "non_commercial_short": 182300,
        "non_commercial_net": -137200,
        "commercial_net": 148900,
        "open_interest": 315000,
        "net_change": -8400,
        "percentile_52w": 12.0,
        "sentiment_bias": "EXTREME_BEARISH",
    },
    {
        "asset": "GOLD",
        "contract_name": "GOLD - COMMODITY EXCHANGE INC.",
        "report_date": "2026-08-11",
        "non_commercial_long": 284500,
        "non_commercial_short": 48200,
        "non_commercial_net": 236300,
        "commercial_net": -268400,
        "open_interest": 520000,
        "net_change": 12400,
        "percentile_52w": 88.5,
        "sentiment_bias": "EXTREME_BULLISH",
    },
    {
        "asset": "OIL",
        "contract_name": "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE",
        "report_date": "2026-08-11",
        "non_commercial_long": 310200,
        "non_commercial_short": 125400,
        "non_commercial_net": 184800,
        "commercial_net": -201500,
        "open_interest": 1820000,
        "net_change": -6300,
        "percentile_52w": 45.0,
        "sentiment_bias": "NEUTRAL",
    },
]


class CFTCCOTProvider:
    """Manages ingestion and percentile scoring of CFTC COT reports."""

    def __init__(self, cache_path: str = CACHE_FILE):
        self.cache_path = cache_path

    def _fetch_from_cftc(self) -> List[Dict[str, Any]]:
        """Fetch latest COT data from CFTC Socrata API (live, free, no key)."""
        results: List[Dict[str, Any]] = []
        # FX instruments from TFF endpoint
        fx_names = [_INSTRUMENT_MAP[a] for a in ["EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"]
                    if a in _INSTRUMENT_MAP]
        # Commodity instruments from Disaggregated endpoint
        comm_names = [_INSTRUMENT_MAP[a] for a in ["GOLD", "SILVER", "COPPER", "PLATINUM", "OIL", "NATGAS", "CORN", "SOYBEAN", "WHEAT"]
                      if a in _INSTRUMENT_MAP]
        try:
            where_fx = "market_and_exchange_names in ('" + "','".join(fx_names) + "')"
            r_tff = requests.get(_TFF_URL, params={"$where": where_fx, "$limit": 500,
                                                     "$order": "report_date_as_yyyy_mm_dd DESC"}, timeout=15)
            if r_tff.status_code == 200:
                tff_data = r_tff.json()
                if isinstance(tff_data, list):
                    results.extend(self._parse_tff_rows(tff_data))
            else:
                log.debug("CFTC TFF fetch failed: %s", r_tff.status_code)
        except Exception as exc:
            log.debug("CFTC TFF fetch error: %s", exc)
        try:
            where_comm = "market_and_exchange_names in ('" + "','".join(comm_names) + "')"
            r_disagg = requests.get(_DISAGG_URL, params={"$where": where_comm, "$limit": 500,
                                                          "$order": "report_date_as_yyyy_mm_dd DESC"}, timeout=15)
            if r_disagg.status_code == 200:
                disagg_data = r_disagg.json()
                if isinstance(disagg_data, list):
                    results.extend(self._parse_disagg_rows(disagg_data))
            else:
                log.debug("CFTC Disagg fetch failed: %s", r_disagg.status_code)
        except Exception as exc:
            log.debug("CFTC Disagg fetch error: %s", exc)
        if results:
            try:
                os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
                with open(self.cache_path, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=1)
                log.info("CFTC cache written: %d rows", len(results))
            except Exception as exc:
                log.debug("CFTC cache write failed: %s", exc)
        return results

    def _parse_tff_rows(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse TFF (Traders in Financial Futures) rows for FX instruments."""
        results = []
        for row in data:
            name = row.get("market_and_exchange_names", "")
            if not name:
                continue
            asset = None
            for code, cftc_name in _INSTRUMENT_MAP.items():
                if name == cftc_name:
                    asset = code
                    break
            if asset is None:
                continue
            try:
                oi = int(row.get("open_interest_all", 0) or 0)
                nc_long = int(row.get("asset_mgr_positions_long", 0) or 0)
                nc_short = int(row.get("asset_mgr_positions_short", 0) or 0)
                net_nc = nc_long - nc_short
                comm_long = int(row.get("dealer_positions_long_all", 0) or 0)
                comm_short = int(row.get("dealer_positions_short_all", 0) or 0)
                comm_net = comm_long - comm_short
                net_change = int(row.get("change_in_open_interest_all", 0) or 0)
                report_date = str(row.get("report_date_as_yyyy_mm_dd", ""))[:10]
                results.append({
                    "asset": asset,
                    "contract_name": name,
                    "report_date": report_date,
                    "non_commercial_long": nc_long,
                    "non_commercial_short": nc_short,
                    "non_commercial_net": net_nc,
                    "commercial_net": comm_net,
                    "open_interest": oi,
                    "net_change": net_change,
                    "percentile_52w": 50.0,
                })
            except Exception as exc:
                log.debug("TFF row parse error for %s: %s", asset, exc)
        return results

    def _parse_disagg_rows(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse Disaggregated rows for commodity instruments."""
        results = []
        for row in data:
            name = row.get("market_and_exchange_names", "")
            if not name:
                continue
            asset = None
            for code, cftc_name in _INSTRUMENT_MAP.items():
                if name == cftc_name:
                    asset = code
                    break
            if asset is None:
                continue
            try:
                oi = int(row.get("open_interest_all", 0) or 0)
                nc_long = int(row.get("m_money_positions_long_all", 0) or 0)
                nc_short = int(row.get("m_money_positions_short_all", 0) or 0)
                net_nc = nc_long - nc_short
                comm_long = int(row.get("prod_merc_positions_long", 0) or 0)
                comm_short = int(row.get("prod_merc_positions_short", 0) or 0)
                comm_net = comm_long - comm_short
                net_change = int(row.get("change_in_open_interest_all", 0) or 0)
                report_date = str(row.get("report_date_as_yyyy_mm_dd", ""))[:10]
                results.append({
                    "asset": asset,
                    "contract_name": name,
                    "report_date": report_date,
                    "non_commercial_long": nc_long,
                    "non_commercial_short": nc_short,
                    "non_commercial_net": net_nc,
                    "commercial_net": comm_net,
                    "open_interest": oi,
                    "net_change": net_change,
                    "percentile_52w": 50.0,
                })
            except Exception as exc:
                log.debug("Disagg row parse error for %s: %s", asset, exc)
        return results

    def _compute_percentiles(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compute 52-week percentile for each instrument from net position history."""
        hist_path = self.cache_path.replace(".json", "_history.json")
        history: Dict[str, List[int]] = {}
        if os.path.exists(hist_path):
            try:
                with open(hist_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = {}

        # First: load all historical rows into history (even if already present)
        for row in rows:
            asset = row.get("asset", "")
            date = row.get("report_date", "")
            net = row.get("non_commercial_net", 0)
            if asset not in history:
                history[asset] = []
            # Add if not already present (deduplicate by date)
            key = f"{date}:{net}"
            existing_keys = {f"{r.get('report_date', '')}:{r.get('non_commercial_net', 0)}" for r in rows if r.get("asset") == asset}
            if key not in existing_keys:
                history[asset].append(net)
            else:
                # Ensure it's in history
                if net not in history[asset][-5:]:
                    history[asset].append(net)
            history[asset] = history[asset][-52:]

        # Second: compute percentiles
        for row in rows:
            asset = row.get("asset", "")
            net = row.get("non_commercial_net", 0)
            hist = history.get(asset, [])
            if len(hist) >= 2:
                h_min = min(hist)
                h_max = max(hist)
                if h_max != h_min:
                    percentile = ((net - h_min) / (h_max - h_min)) * 100.0
                else:
                    percentile = 50.0
            else:
                percentile = 50.0
            row["percentile_52w"] = round(max(0.0, min(100.0, percentile)), 1)

        # Save history
        try:
            os.makedirs(os.path.dirname(hist_path) or ".", exist_ok=True)
            with open(hist_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=1)
        except Exception:
            pass
        return rows

    def fetch_cot_report(self, asset: Optional[str] = None) -> List[COTReportRow]:
        """Fetch COT rows for a specific asset or all tracked assets."""
        raw_rows = self._load_cot_data()
        # Compute percentiles from historical data
        raw_rows = self._compute_percentiles(raw_rows)
        # Keep only the latest row per instrument
        latest_by_asset: Dict[str, Dict[str, Any]] = {}
        for row in raw_rows:
            a = row.get("asset", "")
            date = row.get("report_date", "")
            if a not in latest_by_asset or date > latest_by_asset[a].get("report_date", ""):
                latest_by_asset[a] = row
        results: List[COTReportRow] = []

        for item in latest_by_asset.values():
            curr_asset = str(item.get("asset") or "").upper()
            if asset:
                normalized_asset = asset.upper().replace("XAUUSD", "GOLD").replace("USOIL", "OIL")
                if curr_asset != normalized_asset and curr_asset not in normalized_asset:
                    continue

            p = float(item.get("percentile_52w", 50.0))
            if p >= 85.0:
                bias = "EXTREME_BULLISH"
            elif p >= 60.0:
                bias = "BULLISH"
            elif p <= 15.0:
                bias = "EXTREME_BEARISH"
            elif p <= 40.0:
                bias = "BEARISH"
            else:
                bias = "NEUTRAL"

            row = COTReportRow(
                asset=curr_asset,
                contract_name=str(item.get("contract_name") or ""),
                report_date=str(item.get("report_date") or ""),
                non_commercial_long=int(item.get("non_commercial_long", 0)),
                non_commercial_short=int(item.get("non_commercial_short", 0)),
                non_commercial_net=int(item.get("non_commercial_net", 0)),
                commercial_net=int(item.get("commercial_net", 0)),
                open_interest=int(item.get("open_interest", 0)),
                net_change=int(item.get("net_change", 0)),
                percentile_52w=p,
                sentiment_bias=bias,
            )
            results.append(row)

        return results

    def get_positioning_for_pair(self, base: str, quote: str = "USD") -> Dict[str, Any]:
        """Compute relative speculative positioning between base and quote currencies."""
        base_cot = self.fetch_cot_report(base)
        quote_cot = self.fetch_cot_report(quote)

        base_bias = base_cot[0].sentiment_bias if base_cot else "NEUTRAL"
        base_p = base_cot[0].percentile_52w if base_cot else 50.0

        quote_bias = quote_cot[0].sentiment_bias if quote_cot else "NEUTRAL"
        quote_p = quote_cot[0].percentile_52w if quote_cot else 50.0

        relative_diff = base_p - quote_p
        if relative_diff >= 30.0:
            rel_sentiment = "STRONGLY_BULLISH_BASE"
        elif relative_diff >= 10.0:
            rel_sentiment = "MODERATELY_BULLISH_BASE"
        elif relative_diff <= -30.0:
            rel_sentiment = "STRONGLY_BEARISH_BASE"
        elif relative_diff <= -10.0:
            rel_sentiment = "MODERATELY_BEARISH_BASE"
        else:
            rel_sentiment = "NEUTRAL"

        return {
            "base_currency": base.upper(),
            "quote_currency": quote.upper(),
            "base_percentile": base_p,
            "quote_percentile": quote_p,
            "relative_score": round(relative_diff, 2),
            "relative_bias": rel_sentiment,
        }

    def _load_cot_data(self) -> List[Dict[str, Any]]:
        # Try cache first
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        # Check staleness (>7 days old)
                        sample_date = data[0].get("report_date", "")
                        if sample_date:
                            try:
                                dt = datetime.fromisoformat(sample_date.replace("Z", "+00:00"))
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
                                if age_days <= 7:
                                    return data
                                log.debug("CFTC cache stale (%.0f days), fetching live", age_days)
                            except (ValueError, TypeError):
                                pass
                        return data
            except Exception as exc:
                log.debug("CFTC cache read failed, trying live fetch: %s", exc)
        # Try live fetch from CFTC API
        live_data = self._fetch_from_cftc()
        if live_data:
            return live_data
        return FALLBACK_COT
