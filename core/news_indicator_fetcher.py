"""Live Economic Indicator Fetcher — pulls real NFP/CPI leading indicators from FRED.

Fetches actual data from the FRED API (St. Louis Fed) to populate:
  - NFP leading indicators: ADP Employment, Initial Claims, ISM Employment, JOLTS
  - CPI leading indicators: PPI, Import Prices, ISM Prices Paid, Wage Growth
  - ESI raw data: actual vs consensus for rolling surprise index

Falls back to stubs when FRED API key is not available.

Usage:
    fetcher = LiveIndicatorFetchers(api_manager=api_manager)
    indicators = fetcher.fetch_all_indicators()
    nfp_data = fetcher.get_nfp_indicators()
    cpi_data = fetcher.get_cpi_indicators()
    esi_releases = fetcher.get_esi_releases()
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# FRED Series ID Mapping
# =============================================================================

# NFP Leading Indicators
NFP_INDICATOR_SERIES = {
    "adp_employment": {
        "fred_series": "PAYEMS",  # Total Nonfarm Payroll (proxy for employment change)
        "description": "Total Nonfarm Payroll",
        "unit": "thousands",
        "weight_in_nfp": 0.25,
    },
    "initial_claims": {
        "fred_series": "ICSA",  # Initial Jobless Claims
        "description": "Initial Jobless Claims (SA)",
        "unit": "thousands",
        "weight_in_nfp": 0.20,
        "inverse": True,  # Higher claims = weaker jobs
    },
    "ism_employment": {
        "fred_series": "MANEMP",  # All Employees, Manufacturing (proxy for ISM Employment)
        "description": "ISM Manufacturing Employment (proxy)",
        "unit": "index",
        "weight_in_nfp": 0.20,
        "baseline": 50.0,
    },
    "jolts_openings": {
        "fred_series": "JTSJOL",  # JOLTS Job Openings (millions)
        "description": "Job Openings and Labor Turnover Survey",
        "unit": "millions",
        "weight_in_nfp": 0.15,
    },
}

# CPI Leading Indicators
CPI_INDICATOR_SERIES = {
    "ppi": {
        "fred_series": "PPIACO",  # Producer Price Index, All Commodities
        "description": "Producer Price Index",
        "unit": "index",
        "weight_in_cpi": 0.25,
    },
    "import_prices": {
        "fred_series": "IR",  # Import Price Index
        "description": "Import Price Index (All Commodities)",
        "unit": "index",
        "weight_in_cpi": 0.15,
    },
    "ism_prices_paid": {
        "fred_series": "PCUOMFGOMFG",  # Total Manufacturing Industries PPI (proxy for Prices Paid)
        "description": "Total Manufacturing Industries PPI (proxy for Prices Paid)",
        "unit": "index",
        "weight_in_cpi": 0.15,
        "baseline": 50.0,
    },
    "wage_growth": {
        "fred_series": "CES0500000003",  # Average Hourly Earnings
        "description": "Average Hourly Earnings of All Employees",
        "unit": "index",
        "weight_in_cpi": 0.15,
    },
    "core_pce": {
        "fred_series": "PCEPILFE",  # Core PCE Price Index
        "description": "Core PCE Price Index (Fed's preferred gauge)",
        "unit": "index",
        "weight_in_cpi": 0.15,
    },
}

# ESI Calculation: series that have actual vs forecast releases
ESI_SERIES = {
    "CPIAUCSL": "Consumer Price Index for All Urban Consumers",
    "UNRATE": "Unemployment Rate",
    "PAYEMS": "All Employees, Total Nonfarm",
    "RSAFS": "Advance Retail Sales",
    "INDPRO": "Industrial Production Index",
    "HOUST": "Housing Starts",
    "PPIACO": "Producer Price Index",
    "UMCSENT": "Consumer Sentiment Index",
    "MANEMP": "ISM Manufacturing Employment Proxy",
    "ICSA": "Initial Jobless Claims",
    "JTSJOL": "JOLTS Job Openings",
}

# Economic calendar event titles → FRED series mapping for ESI
RELEASE_TO_FRED = {
    "NON-FARM PAYROLLS": "PAYEMS",
    "CONSUMER PRICE INDEX": "CPIAUCSL",
    "UNEMPLOYMENT RATE": "UNRATE",
    "RETAIL SALES": "RSAFS",
    "INDUSTRIAL PRODUCTION": "INDPRO",
    "HOUSING STARTS": "HOUST",
    "PRODUCER PRICE INDEX": "PPIACO",
    "CONSUMER CONFIDENCE": "UMCSENT",
    "ISM MANUFACTURING PMI": "MANEMP",
    "INITIAL JOBLESS CLAIMS": "ICSA",
    "JOLTS JOB OPENINGS": "JTSJOL",
}


@dataclass
class IndicatorSnapshot:
    """A single indicator's current state."""
    series_id: str
    description: str
    value: Optional[float] = None
    previous: Optional[float] = None
    date: Optional[str] = None
    unit: str = ""
    source: str = "FRED"
    status: str = "OK"  # OK, STALE, MISSING, ERROR
    age_days: Optional[int] = None  # How old is the data


@dataclass
class NFPIndicators:
    """NFP leading indicator snapshot."""
    adp_change_k: float = 0.0
    adp_consensus_k: float = 0.0
    jobless_claims_4w_ma: float = 215.0
    jobless_claims_baseline: float = 215.0
    ism_employment_pmi: float = 50.0
    jolts_openings_millions: float = 8.5
    jolts_baseline: float = 8.0
    esi_score: float = 50.0
    snapshots: Dict[str, IndicatorSnapshot] = field(default_factory=dict)
    is_live: bool = False


@dataclass
class CPIIndicators:
    """CPI leading indicator snapshot."""
    ppi_mom_pct: float = 0.0
    ppi_consensus_pct: float = 0.0
    crude_oil_30d_change_pct: float = 0.0
    agriculture_basket_change_pct: float = 0.0
    metals_basket_change_pct: float = 0.0
    import_price_mom_pct: float = 0.0
    ism_prices_paid_pmi: float = 50.0
    wage_growth_yoy_pct: float = 4.0
    wage_baseline_pct: float = 3.5
    snapshots: Dict[str, IndicatorSnapshot] = field(default_factory=dict)
    is_live: bool = False


class LiveIndicatorFetcher:
    """Fetches real economic indicators from FRED for NFP/CPI predictions and ESI.

    Falls back to reasonable defaults when the FRED API is not available.
    """

    def __init__(self, api_manager=None):
        """
        Args:
            api_manager: data.api_manager.APIManager instance (with .fred client)
        """
        self.api_manager = api_manager
        self._cache: Dict[str, IndicatorSnapshot] = {}
        self._cache_ts: float = 0.0
        self._CACHE_TTL: float = 300.0  # 5 minutes
        self._nfp_snapshot: Optional[NFPIndicators] = None
        self._cpi_snapshot: Optional[CPIIndicators] = None
        self._esi_releases: List[Dict[str, Any]] = []
        self._last_fetch_ts: float = 0.0

    def _fetch_fred_series(self, series_id: str) -> Optional[IndicatorSnapshot]:
        """Fetch a single FRED series, with caching."""
        now = time.time()
        cache_key = f"fred:{series_id}"

        # Check cache
        if cache_key in self._cache and (now - self._cache_ts) < self._CACHE_TTL:
            return self._cache[cache_key]

        # Try FRED API (bounded: the raw fredapi client has no effective
        # timeout, and a scheduled agent must never hang on one series —
        # same wait-bound pattern as the boot fetch watchdog).
        if self.api_manager and hasattr(self.api_manager, "fred") and self.api_manager.fred:
            try:
                from run_bounded import run_bounded

                ok, series, _err = run_bounded(
                    lambda: self.api_manager.fred.get_series(series_id),
                    float(os.getenv("FRED_BUDGET_S", "20")))
                if not ok:
                    logger.warning(
                        "FRED %s unavailable (%s); marking MISSING (bounded)",
                        series_id, _err)
                    return IndicatorSnapshot(
                        series_id=series_id,
                        description=ESI_SERIES.get(series_id, series_id),
                        status="MISSING",
                        source="FRED_FALLBACK",
                    )
                if series is not None and len(series) > 0:
                    value = float(series.iloc[-1])
                    date_str = series.index[-1].strftime("%Y-%m-%d")
                    previous = None
                    if len(series) >= 2:
                        try:
                            previous = float(series.iloc[-2])
                        except (TypeError, ValueError):
                            pass

                    # Calculate data age
                    try:
                        data_date = datetime.strptime(date_str, "%Y-%m-%d")
                        age_days = (datetime.now() - data_date).days
                    except ValueError:
                        age_days = None

                    status = "OK"
                    if age_days is not None and age_days > 90:
                        status = "STALE"

                    snap = IndicatorSnapshot(
                        series_id=series_id,
                        description=ESI_SERIES.get(series_id, series_id),
                        value=value,
                        previous=previous,
                        date=date_str,
                        unit="",
                        source="FRED",
                        status=status,
                        age_days=age_days,
                    )
                    self._cache[cache_key] = snap
                    self._cache_ts = now
                    return snap
            except Exception as exc:
                logger.warning("FRED fetch failed for %s: %s", series_id, exc)

        # Fallback: return MISSING
        return IndicatorSnapshot(
            series_id=series_id,
            description=ESI_SERIES.get(series_id, series_id),
            status="MISSING",
            source="FRED_FALLBACK",
        )

    def _fetch_multiple(self, series_ids: List[str]) -> Dict[str, IndicatorSnapshot]:
        """Fetch multiple FRED series."""
        results = {}
        for sid in series_ids:
            snap = self._fetch_fred_series(sid)
            if snap is not None:
                results[sid] = snap
        return results

    def _has_fred_api(self) -> bool:
        """Check if FRED API is available."""
        return (
            self.api_manager is not None
            and hasattr(self.api_manager, "fred")
            and self.api_manager.fred is not None
        )

    def fetch_all_indicators(self) -> Dict[str, Any]:
        """Fetch all NFP, CPI, and ESI indicators from FRED.

        Returns a dict with:
          - nfp: NFPIndicators
          - cpi: CPIIndicators
          - esi_releases: list of {actual, consensus, series, date}
          - is_live: bool
          - fetch_time: str
        """
        now = time.time()
        if (now - self._last_fetch_ts) < self._CACHE_TTL and self._nfp_snapshot:
            return {
                "nfp": self._nfp_snapshot,
                "cpi": self._cpi_snapshot,
                "esi_releases": self._esi_releases,
                "is_live": self._nfp_snapshot.is_live if self._nfp_snapshot else False,
                "fetch_time": datetime.now(timezone.utc).isoformat(),
                "cached": True,
            }

        is_live = self._has_fred_api()
        logger.info("Fetching live indicators (FRED API: %s)", "available" if is_live else "unavailable")

        all_series_ids = []
        for info in NFP_INDICATOR_SERIES.values():
            all_series_ids.append(info["fred_series"])
        for info in CPI_INDICATOR_SERIES.values():
            all_series_ids.append(info["fred_series"])
        all_series_ids.extend(list(ESI_SERIES.keys()))

        # Dedupe
        all_series_ids = list(dict.fromkeys(all_series_ids))

        snapshots = self._fetch_multiple(all_series_ids)

        # Build NFP indicators
        nfp = self._build_nfp_indicators(snapshots)

        # Build CPI indicators
        cpi = self._build_cpi_indicators(snapshots)

        # Build ESI releases (actual vs consensus)
        esi_releases = self._build_esi_releases(snapshots)

        self._nfp_snapshot = nfp
        self._cpi_snapshot = cpi
        self._esi_releases = esi_releases
        self._last_fetch_ts = now

        return {
            "nfp": nfp,
            "cpi": cpi,
            "esi_releases": esi_releases,
            "is_live": is_live,
            "fetch_time": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "series_fetched": len(snapshots),
            "series_available": sum(1 for s in snapshots.values() if s.status == "OK"),
        }

    def _build_nfp_indicators(self, snapshots: Dict[str, IndicatorSnapshot]) -> NFPIndicators:
        """Build NFP indicator snapshot from FRED data."""
        nfp = NFPIndicators()
        nfp.snapshots = {}

        # ADP / Payroll Employment
        adp_snap = snapshots.get("PAYEMS") or snapshots.get("ADPFE")
        if adp_snap and adp_snap.value is not None:
            if adp_snap.previous is not None and adp_snap.value > 10000:
                nfp.adp_change_k = adp_snap.value - adp_snap.previous
                nfp.adp_consensus_k = 150.0
            else:
                nfp.adp_change_k = adp_snap.value
                nfp.adp_consensus_k = adp_snap.previous if adp_snap.previous is not None else adp_snap.value
            nfp.snapshots["adp"] = adp_snap

        # Initial Claims (4-week MA approximation)
        claims_snap = snapshots.get("ICSA")
        if claims_snap and claims_snap.value is not None:
            nfp.jobless_claims_4w_ma = claims_snap.value
            nfp.jobless_claims_baseline = claims_snap.previous if claims_snap.previous is not None else 215.0
            nfp.snapshots["claims"] = claims_snap

        # ISM Employment (use manufacturing employment as proxy)
        ism_snap = snapshots.get("MANEMP")
        if ism_snap and ism_snap.value is not None:
            # Convert to PMI-like scale (50 = neutral)
            # Manufacturing employment level → approximate PMI
            nfp.ism_employment_pmi = 50.0 + (ism_snap.value - (ism_snap.previous or ism_snap.value)) / max(abs(ism_snap.previous or 1.0), 1.0) * 10.0
            nfp.snapshots["ism"] = ism_snap

        # JOLTS
        jolts_snap = snapshots.get("JTSJOL")
        if jolts_snap and jolts_snap.value is not None:
            nfp.jolts_openings_millions = jolts_snap.value / 1000.0  # Convert thousands to millions
            nfp.jolts_baseline = jolts_snap.previous / 1000.0 if jolts_snap.previous is not None else 8.0
            nfp.snapshots["jolts"] = jolts_snap

        nfp.is_live = any(s.status == "OK" for s in nfp.snapshots.values())
        return nfp

    def _build_cpi_indicators(self, snapshots: Dict[str, IndicatorSnapshot]) -> CPIIndicators:
        """Build CPI indicator snapshot from FRED data."""
        cpi = CPIIndicators()
        cpi.snapshots = {}

        # PPI
        ppi_snap = snapshots.get("PPIACO")
        if ppi_snap and ppi_snap.value is not None:
            cpi.ppi_mom_pct = ppi_snap.value
            cpi.ppi_consensus_pct = ppi_snap.previous if ppi_snap.previous is not None else ppi_snap.value
            cpi.snapshots["ppi"] = ppi_snap

        # Import Prices
        import_snap = snapshots.get("IR")
        if import_snap and import_snap.value is not None:
            cpi.import_price_mom_pct = import_snap.value
            cpi.snapshots["import_prices"] = import_snap

        # ISM Prices Paid (use PCUOMFGOMFG or NAPM proxy)
        ism_snap = snapshots.get("PCUOMFGOMFG") or snapshots.get("NAPM")
        if ism_snap and ism_snap.value is not None:
            if ism_snap.previous is not None and ism_snap.value > 100:
                pct = ((ism_snap.value / ism_snap.previous) - 1.0) * 100.0
                cpi.ism_prices_paid_pmi = 50.0 + pct * 5.0
            else:
                cpi.ism_prices_paid_pmi = ism_snap.value
            cpi.snapshots["ism_prices"] = ism_snap

        # Wage Growth
        wage_snap = snapshots.get("CES0500000003")
        if wage_snap and wage_snap.value is not None:
            if wage_snap.previous and wage_snap.previous > 0:
                cpi.wage_growth_yoy_pct = ((wage_snap.value / wage_snap.previous) - 1.0) * 100.0
            cpi.snapshots["wages"] = wage_snap

        cpi.is_live = any(s.status == "OK" for s in cpi.snapshots.values())
        return cpi

    def _build_esi_releases(self, snapshots: Dict[str, IndicatorSnapshot]) -> List[Dict[str, Any]]:
        """Build ESI releases (actual vs consensus) from FRED data."""
        releases = []
        for fred_series, description in ESI_SERIES.items():
            snap = snapshots.get(fred_series)
            if snap is not None and snap.value is not None and snap.previous is not None:
                releases.append({
                    "series": fred_series,
                    "description": description,
                    "actual": snap.value,
                    "consensus": snap.previous,  # Previous release as consensus proxy
                    "date": snap.date,
                    "delta": snap.value - snap.previous,
                })
        return releases

    def get_nfp_indicators(self) -> NFPIndicators:
        """Get NFP leading indicators (fetches if stale)."""
        if not self._nfp_snapshot or (time.time() - self._last_fetch_ts) > self._CACHE_TTL:
            self.fetch_all_indicators()
        return self._nfp_snapshot or NFPIndicators()

    def get_cpi_indicators(self) -> CPIIndicators:
        """Get CPI leading indicators (fetches if stale)."""
        if not self._cpi_snapshot or (time.time() - self._last_fetch_ts) > self._CACHE_TTL:
            self.fetch_all_indicators()
        return self._cpi_snapshot or CPIIndicators()

    def get_esi_releases(self) -> List[Dict[str, Any]]:
        """Get ESI releases (fetches if stale)."""
        if not self._esi_releases or (time.time() - self._last_fetch_ts) > self._CACHE_TTL:
            self.fetch_all_indicators()
        return self._esi_releases or []

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary dict suitable for the LLM prompt."""
        nfp = self.get_nfp_indicators()
        cpi = self.get_cpi_indicators()
        esi_releases = self.get_esi_releases()

        from core.news_trading import predict_nfp, predict_cpi, EconomicSurpriseIndex

        nfp_pred = predict_nfp(
            adp_change_k=nfp.adp_change_k,
            adp_consensus_k=nfp.adp_consensus_k,
            jobless_claims_4w_ma=nfp.jobless_claims_4w_ma,
            jobless_claims_baseline=nfp.jobless_claims_baseline,
            ism_employment_pmi=nfp.ism_employment_pmi,
            jolts_openings_millions=nfp.jolts_openings_millions,
            jolts_baseline=nfp.jolts_baseline,
            esi_score=nfp.esi_score,
        )

        cpi_pred = predict_cpi(
            ppi_mom_pct=cpi.ppi_mom_pct,
            ppi_consensus_pct=cpi.ppi_consensus_pct,
            crude_oil_30d_change_pct=cpi.crude_oil_30d_change_pct,
            agriculture_basket_change_pct=cpi.agriculture_basket_change_pct,
            metals_basket_change_pct=cpi.metals_basket_change_pct,
            import_price_mom_pct=cpi.import_price_mom_pct,
            ism_prices_paid_pmi=cpi.ism_prices_paid_pmi,
            wage_growth_yoy_pct=cpi.wage_growth_yoy_pct,
            wage_baseline_pct=cpi.wage_baseline_pct,
        )

        esi = EconomicSurpriseIndex.calculate(esi_releases)

        return {
            "nfp_prediction": nfp_pred,
            "cpi_prediction": cpi_pred,
            "esi": esi,
            "nfp_is_live": nfp.is_live,
            "cpi_is_live": cpi.is_live,
            "indicators_available": sum(
                1 for s in {**nfp.snapshots, **cpi.snapshots}.values()
                if s.status == "OK"
            ),
        }
