"""Unified Global Macroeconomic Bridge.

Aggregates multiple real-time and structural macro feeds:
- Forex Factory economic calendar & high-impact event blackouts
- Exness institutional market analysis & technical pivot levels
- CFTC Commitments of Traders (COT) institutional positioning & percentiles
- US EIA Weekly Petroleum status & Oil/CAD bias
- Seeking Alpha corporate earnings, forward guidance & quant grades
- Federal Reserve / FRED yield curve & liquidity proxies

Provides a unified, point-in-time snapshot to the LLM Orchestrator and Causal Graph.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from data.cftc_cot_provider import CFTCCOTProvider
from data.eia_provider import EIAProvider
from data.exness_provider import ExnessProvider
from data.forexfactory_provider import ForexFactoryProvider
from data.seeking_alpha_provider import SeekingAlphaProvider
from data.alternative_data_provider import AlternativeDataProvider

log = logging.getLogger("GlobalMacroBridge")


@dataclass
class GlobalMacroSnapshot:
    symbol: str
    timestamp_utc: str
    calendar_events_count: int
    high_impact_near: bool
    upcoming_catalyst: Optional[str]
    analyst_sentiment: str
    analyst_support: Optional[float]
    analyst_resistance: Optional[float]
    analyst_pivot: Optional[float]
    cot_sentiment_bias: str
    cot_percentile_52w: float
    energy_oil_bias: str
    macro_regime: str
    is_event_clear: bool
    summary_text: str
    nowcast_live_count: int = 0
    nowcast_golden_eight_live: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GlobalMacroBridge:
    """Centralized aggregator providing multi-provider global macro intelligence."""

    def __init__(self):
        self.ff_provider = ForexFactoryProvider()
        self.exness_provider = ExnessProvider()
        self.cot_provider = CFTCCOTProvider()
        self.eia_provider = EIAProvider()
        self.sa_provider = SeekingAlphaProvider()
        self.alt_provider = AlternativeDataProvider()

    def get_nowcast_layer(self) -> Dict[str, Any]:
        """Tier 1/2/3 alternative nowcast snapshot (research only)."""
        return self.alt_provider.fetch_nowcast_snapshot(include_stubs=False)

    def get_macro_snapshot(self, symbol: str = "EURUSD") -> GlobalMacroSnapshot:
        """Fetch and combine all macro indicators for a target symbol."""
        now_utc = datetime.now(timezone.utc).isoformat()
        base_curr = symbol[:3].upper() if len(symbol) >= 6 else symbol.upper()
        quote_curr = symbol[3:6].upper() if len(symbol) >= 6 else "USD"

        # 1. Forex Factory Calendar
        ff_events = self.ff_provider.fetch_calendar(currency=base_curr)
        high_impact = [e for e in ff_events if e.impact == "HIGH"]
        has_high_impact = len(high_impact) > 0
        upcoming_catalyst = high_impact[0].title if high_impact else (ff_events[0].title if ff_events else None)

        # 2. Exness Analyst Levels & Sentiment
        exness_levels = self.exness_provider.get_latest_levels(symbol)
        analyst_sent = str(exness_levels.get("sentiment") or "NEUTRAL")

        # 3. CFTC COT Positioning
        cot_rows = self.cot_provider.fetch_cot_report(base_curr)
        if cot_rows:
            cot_bias = cot_rows[0].sentiment_bias
            cot_p = cot_rows[0].percentile_52w
        else:
            cot_bias = "NEUTRAL"
            cot_p = 50.0

        # 4. EIA Petroleum Status
        eia_report = self.eia_provider.fetch_latest_report()
        oil_bias = eia_report.oil_bias

        # 5. Determine Overall Macro Regime
        nowcast = self.get_nowcast_layer()
        live_obs = [o for o in (nowcast.get("observations") or []) if o.get("status") == "LIVE"]
        golden_live = sum(
            1 for o in live_obs
            if str(o.get("source_id") or "") in {
                "grid_load_mw", "sku_price_scrape", "zillow_zori", "credit_card_spending",
                "linkup_job_postings", "ais_tanker_tracking", "sentinel_ndvi", "fed_sofr_futures",
            }
        )

        if cot_bias == "EXTREME_BULLISH" and analyst_sent == "BULLISH":
            regime = "RISK_ON"
        elif cot_bias in ("EXTREME_BEARISH", "BEARISH") or analyst_sent == "BEARISH":
            regime = "RISK_OFF"
        else:
            regime = "NORMAL"

        # Event clearance (true if no immediate high impact event blocking)
        is_clear = not has_high_impact

        summary = (
            f"Symbol: {symbol} | Regime: {regime} | "
            f"ForexFactory: {len(ff_events)} events ({'⚠️ High Impact' if has_high_impact else 'Clear'}) | "
            f"Exness: {analyst_sent} [S: {exness_levels.get('support')} | R: {exness_levels.get('resistance')}] | "
            f"COT: {cot_bias} ({cot_p}% percentile) | "
            f"EIA Oil Bias: {oil_bias} | "
            f"Nowcast LIVE: {len(live_obs)} (Golden8: {golden_live})"
        )

        return GlobalMacroSnapshot(
            symbol=symbol,
            timestamp_utc=now_utc,
            calendar_events_count=len(ff_events),
            high_impact_near=has_high_impact,
            upcoming_catalyst=upcoming_catalyst,
            analyst_sentiment=analyst_sent,
            analyst_support=exness_levels.get("support"),
            analyst_resistance=exness_levels.get("resistance"),
            analyst_pivot=exness_levels.get("pivot"),
            cot_sentiment_bias=cot_bias,
            cot_percentile_52w=cot_p,
            energy_oil_bias=oil_bias,
            macro_regime=regime,
            is_event_clear=is_clear,
            summary_text=summary,
            nowcast_live_count=len(live_obs),
            nowcast_golden_eight_live=golden_live,
        )

    def fetch_summary(self) -> Dict[str, Any]:
        """Return diagnostic snapshot covering major symbols."""
        return {
            "EURUSD": self.get_macro_snapshot("EURUSD").to_dict(),
            "XAUUSD": self.get_macro_snapshot("XAUUSD").to_dict(),
            "USOIL": self.get_macro_snapshot("USOIL").to_dict(),
        }
