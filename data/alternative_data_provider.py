"""Alternative / nowcast data provider — fetches free proxies, stubs paid sources.

Returns point-in-time observations aligned to ``alternative_data_registry`` entities.
Never grants execution authority.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from data.alternative_data_registry import (
    AlternativeDataSource,
    SOURCES,
    registry_summary,
)

log = logging.getLogger("AlternativeDataProvider")

CACHE_FILE = "artifacts/alternative_nowcast_cache.json"


@dataclass
class NowcastObservation:
    source_id: str
    entity: str
    tier: str
    provider: str
    value: Optional[float]
    status: str  # LIVE, CACHED, STUB, ERROR
    observed_at: str
    symbols: List[str]
    payload: Dict[str, Any]
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        row = asdict(self)
        row["execution_authority"] = False
        return row


class AlternativeDataProvider:
    """Fetch nowcast-layer observations for registered alternative sources."""

    def __init__(self, cache_path: str = CACHE_FILE) -> None:
        self.cache_path = cache_path
        self._yahoo = None
        self._eia = None
        self._cot = None
        self._zero_lag = None

    def _zero_lag_provider(self):
        if self._zero_lag is None:
            try:
                from data.zero_lag_macro_provider import get_zero_lag_provider
                self._zero_lag = get_zero_lag_provider()
            except Exception:
                try:
                    from data.zero_lag_macro_provider import get_zero_lag_provider
                    self._zero_lag = get_zero_lag_provider()
                except Exception:
                    self._zero_lag = False
        return self._zero_lag if self._zero_lag is not False else None

    def _yahoo_provider(self):
        if self._yahoo is None:
            try:
                from data.yahoo_finance_provider import YahooFinanceProvider
                self._yahoo = YahooFinanceProvider()
            except Exception:
                self._yahoo = False
        return self._yahoo if self._yahoo is not False else None

    def _eia_provider(self):
        if self._eia is None:
            try:
                from data.eia_provider import EIAProvider
                self._eia = EIAProvider()
            except Exception:
                self._eia = False
        return self._eia if self._eia is not False else None

    def _cot_provider(self):
        if self._cot is None:
            try:
                from data.cftc_cot_provider import CFTCCOTProvider
                self._cot = CFTCCOTProvider()
            except Exception:
                self._cot = False
        return self._cot if self._cot is not False else None

    def _utcnow(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _env_ready(self, src: AlternativeDataSource) -> bool:
        if not src.requires_api_key:
            return True
        if not src.env_key:
            return False
        return bool(os.getenv(src.env_key, "").strip())

    def _fetch_yahoo_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Latest quote via the shared guarded Yahoo path.

        Delegates to YahooFinanceProvider.get_quote_data so the module-level
        outage circuit breaker (DNS/network outages surface as yfinance
        "possibly delisted" noise) is honored here too. Result cached 5 min.
        """
        provider = self._yahoo_provider()
        if provider is None:
            return None
        now = time.monotonic()
        if not hasattr(self, "_quote_cache"):
            self._quote_cache: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}
        if symbol in self._quote_cache:
            ts, val = self._quote_cache[symbol]
            if now - ts < 300.0:
                return val
        try:
            quote = provider.get_quote_data(symbol)
        except Exception as exc:
            log.debug("Yahoo quote failed for %s: %s", symbol, exc)
            quote = None
        res: Optional[Dict[str, Any]] = None
        if quote and quote.get("price", 0) > 0:
            res = {
                "symbol": symbol,
                "price": float(quote["price"]),
                "volume": 0.0,
                "observed_at": str(quote.get("observed_at") or self._utcnow()),
            }
        self._quote_cache[symbol] = (now, res)
        return res

    def _fetch_market_symbols(self, src: AlternativeDataSource) -> NowcastObservation:
        quotes: List[Dict[str, Any]] = []
        for symbol in src.symbols:
            row = self._fetch_yahoo_quote(symbol)
            if row:
                quotes.append(row)
        value = None
        if quotes:
            value = sum(q["price"] for q in quotes) / len(quotes)
        status = "LIVE" if quotes else "ERROR"
        return NowcastObservation(
            source_id=src.source_id,
            entity=src.entity,
            tier=src.tier,
            provider=src.provider,
            value=value,
            status=status,
            observed_at=self._utcnow(),
            symbols=list(src.symbols),
            payload={"quotes": quotes, "kind": "market_tick_proxy"},
        )

    def _fetch_eia_petroleum(self, src: AlternativeDataSource) -> NowcastObservation:
        provider = self._eia_provider()
        if provider is None:
            return self._stub(src, reason="eia_provider_unavailable")
        try:
            report = provider.fetch_latest_report()
            payload = report.to_dict()
            return NowcastObservation(
                source_id=src.source_id,
                entity=src.entity,
                tier=src.tier,
                provider=src.provider,
                value=float(report.crude_surprise_mb),
                status="LIVE",
                observed_at=self._utcnow(),
                symbols=[],
                payload={**payload, "kind": "eia_petroleum"},
            )
        except Exception as exc:
            return self._error(src, str(exc))

    def _fetch_cot(self, src: AlternativeDataSource) -> NowcastObservation:
        provider = self._cot_provider()
        if provider is None:
            return self._stub(src, reason="cot_provider_unavailable")
        try:
            rows = provider.fetch_cot_report("EUR") or provider.fetch_cot_report("GOLD")
            if not rows:
                return self._stub(src, reason="no_cot_rows")
            top = rows[0]
            payload = top.to_dict()
            return NowcastObservation(
                source_id=src.source_id,
                entity=src.entity,
                tier=src.tier,
                provider=src.provider,
                value=float(top.percentile_52w),
                status="LIVE",
                observed_at=self._utcnow(),
                symbols=[],
                payload={**payload, "kind": "cftc_cot"},
            )
        except Exception as exc:
            return self._error(src, str(exc))

    def _stub(self, src: AlternativeDataSource, *, reason: str = "") -> NowcastObservation:
        return NowcastObservation(
            source_id=src.source_id,
            entity=src.entity,
            tier=src.tier,
            provider=src.provider,
            value=None,
            status="STUB",
            observed_at=self._utcnow(),
            symbols=list(src.symbols),
            payload={
                "kind": "stub",
                "reason": reason or "requires_api_key",
                "env_key": src.env_key,
                "requires_api_key": src.requires_api_key,
            },
        )

    def _error(self, src: AlternativeDataSource, message: str) -> NowcastObservation:
        return NowcastObservation(
            source_id=src.source_id,
            entity=src.entity,
            tier=src.tier,
            provider=src.provider,
            value=None,
            status="ERROR",
            observed_at=self._utcnow(),
            symbols=list(src.symbols),
            payload={"kind": "error", "error": message[:300]},
        )

    def _fetch_zero_lag(self, src: AlternativeDataSource) -> NowcastObservation:
        zlp = self._zero_lag_provider()
        if zlp is None:
            return self._stub(src, reason="zero_lag_provider_unavailable")
        try:
            snap = zlp.get_snapshot()
            val = None
            payload = {}
            if src.source_id == "grid_load_mw":
                val = float(snap.industrial_grid_load_index)
                payload = {"industrial_grid_index": val, "unit": "index_vs_baseline"}
            elif src.source_id == "sku_price_scrape":
                val = float(snap.daily_cpi_mom_nowcast)
                payload = {"cpi_mom_nowcast": val, "cpi_yoy_nowcast": snap.daily_cpi_yoy_nowcast}
            elif src.source_id == "sentinel_ndvi":
                val = float(snap.satellite_crop_health_score)
                payload = {"vegetation_soil_health_score": val, "unit": "index_0_100"}
            elif src.source_id in {"ais_vessel_tracking", "ais_tanker_tracking"}:
                val = float(snap.maritime_chokepoint_index)
                payload = {"chokepoint_transit_index": val, "unit": "index_vs_baseline"}
            elif src.source_id == "linkup_job_postings":
                val = float(snap.labor_hiring_velocity)
                payload = {"job_vacancy_velocity_mom": val, "unit": "percent_mom"}
            elif src.source_id == "money_market_repo":
                val = float(snap.net_liquidity_usd_billions)
                payload = {"net_liquidity_usd_b": val, "bias": snap.net_liquidity_bias}
            elif src.source_id == "growth_grid_rail_cards":
                val = float(snap.industrial_grid_load_index)
                payload = {"composite_activity_index": val, "net_liquidity_b": snap.net_liquidity_usd_billions}
            else:
                val = float(snap.industrial_grid_load_index)
                payload = {"snapshot": snap.to_dict()}

            return NowcastObservation(
                source_id=src.source_id,
                entity=src.entity,
                tier=src.tier,
                provider=src.provider,
                value=val,
                status="LIVE",
                observed_at=self._utcnow(),
                symbols=list(src.symbols),
                payload={**payload, "kind": "zero_lag_physical_proxy"},
            )
        except Exception as exc:
            return self._error(src, str(exc))

    def fetch_source(self, source_id: str) -> NowcastObservation:
        src = SOURCES.get(source_id)
        if src is None:
            raise KeyError(f"unknown alternative source: {source_id}")

        if src.requires_api_key and not self._env_ready(src):
            # If env key absent, use zero-lag physical proxy when available
            if src.source_id in {
                "grid_load_mw", "sku_price_scrape", "sentinel_ndvi",
                "ais_vessel_tracking", "ais_tanker_tracking",
                "linkup_job_postings", "growth_grid_rail_cards",
            }:
                return self._fetch_zero_lag(src)
            return self._stub(src, reason="missing_api_key")

        if src.module == "zero_lag_macro_provider" or src.provider in {
            "FRED", "REDFIN", "NOAA", "ENTSO-E", "AAR", "NASA", "GOOGLE_TRENDS",
            "OPENTABLE", "USGS", "USDA_NASS", "SKYFI", "NOAA_AIS", "AISHUB",
            "FREIGHTOS", "FED", "SKU_SCRAPER", "GRID_ISO",
        }:
            return self._fetch_zero_lag(src)

        if src.provider == "MARKET" and src.symbols:
            return self._fetch_market_symbols(src)
        if src.source_id == "eia_petroleum_weekly":
            return self._fetch_eia_petroleum(src)
        if src.source_id == "cftc_cot_weekly":
            return self._fetch_cot(src)
        if src.provider in {"CBOE", "MARKET"} and src.symbols:
            return self._fetch_market_symbols(src)

        return self._stub(src, reason="fetch_not_implemented")

    def fetch_nowcast_snapshot(
        self,
        *,
        include_stubs: bool = True,
        tiers: Optional[List[str]] = None,
        max_age_s: float = 300.0,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """Fetch all registry sources; persist cache for offline resilience."""
        if not force_refresh and max_age_s > 0:
            cached = self.load_cached_snapshot()
            if cached and isinstance(cached, dict) and cached.get("observations"):
                try:
                    cap_str = cached.get("captured_at")
                    if cap_str:
                        cap_dt = datetime.fromisoformat(str(cap_str).replace("Z", "+00:00"))
                        now_dt = datetime.now(timezone.utc)
                        if (now_dt - cap_dt).total_seconds() < max_age_s:
                            obs_list = cached["observations"]
                            if tiers:
                                obs_list = [o for o in obs_list if o.get("tier") in tiers]
                            if not include_stubs:
                                obs_list = [o for o in obs_list if o.get("status") != "STUB"]
                            return {
                                **cached,
                                "observations": obs_list,
                                "live_count": sum(1 for o in obs_list if o.get("status") == "LIVE"),
                                "stub_count": sum(1 for o in obs_list if o.get("status") == "STUB"),
                            }
                except Exception:
                    pass

        stamp = self._utcnow()
        observations: List[Dict[str, Any]] = []
        for source_id, src in SOURCES.items():
            if tiers and src.tier not in tiers:
                continue
            obs = self.fetch_source(source_id)
            if not include_stubs and obs.status == "STUB":
                continue
            observations.append(obs.to_dict())

        snapshot = {
            "captured_at": stamp,
            "registry": registry_summary(),
            "observations": observations,
            "live_count": sum(1 for o in observations if o["status"] == "LIVE"),
            "stub_count": sum(1 for o in observations if o["status"] == "STUB"),
            "execution_authority": False,
        }
        self._write_cache(snapshot)
        return snapshot

    def _write_cache(self, snapshot: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, indent=2, default=str)
        except Exception as exc:
            log.debug("Cache write failed: %s", exc)

    def load_cached_snapshot(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.cache_path):
            return None
        try:
            with open(self.cache_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except Exception:
            return None
