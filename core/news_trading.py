"""Unified News Trading Facade.

Single entry point for all news-related trading logic in the system.
Wraps existing macro_event_alpha_engine components and adds:
  1. Economic calendar integration (ForexFactory + MT5)
  2. NewsTradingGate — blocks entries during spike phases
  3. NFP / CPI / Central Bank prediction wrappers
  4. Rolling Economic Surprise Index (ESI)
  5. News summary builder for LLM prompt context
  6. Delta (surprise factor) calculator

Usage:
    gate = NewsTradingGate()
    result = gate.evaluate(symbol="EURUSD", now=now, events=calendar_events)
    if not result["allowed"]:
        return {"action": "HOLD", "reason": result["block_reason"]}

    summary = build_news_summary_for_llm(symbol="EURUSD", events=events, esi=esi)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# =============================================================================
# 1. Event Tier Classification
# =============================================================================

# Tier 1: Ultra high-impact (100-300 pip moves expected)
TIER_1_EVENTS = {
    "NON-FARM PAYROLLS", "NFP", "NON-FARM EMPLOYMENT CHANGE",
    "FEDERAL FUNDS RATE", "FOMC STATEMENT", "FOMC INTEREST RATE DECISION",
    "ECB MAIN REFINANCING RATE", "ECB INTEREST RATE DECISION",
    "BOJ MONETARY POLICY STATEMENT", "BOJ INTEREST RATE DECISION",
    "CONSUMER PRICE INDEX", "CPI", "CORE CPI",
    "GROSS DOMESTIC PRODUCT", "GDP",
}

# Tier 2: High impact (50-90 pip moves)
TIER_2_EVENTS = {
    "RETAIL SALES", "UNEMPLOYMENT RATE", "AVERAGE HOURLY EARNINGS",
    "INITIAL JOBLESS CLAIMS", "PRODUCER PRICE INDEX", "PPI",
    "CORE PCE PRICE INDEX", "PMI MANUFACTURING", "PMI SERVICES",
    "CONSUMER CONFIDENCE", "ISM MANUFACTURING PMI", "ISM SERVICES PMI",
    "ADP EMPLOYMENT CHANGE", "JOLTS JOB OPENINGS",
}

# Tier 3: Moderate impact (20-50 pip moves)
TIER_3_EVENTS = {
    "TRADE BALANCE", "HOUSING STARTS", "BUILDING PERMITS",
    "INDUSTRIAL PRODUCTION", "DURABLE GOODS ORDERS",
    "FED SPEECH", "ECB SPEECH", "BOJ SPEECH",
}

# Currency→Symbol mapping for relevance filtering
CURRENCY_SYMBOL_MAP = {
    "USD": ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD", "XAUUSD"],
    "EUR": ["EURUSD", "EURGBP", "EURJPY", "EURAUD"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY"],
    "AUD": ["AUDUSD", "EURAUD", "AUDJPY"],
    "CAD": ["USDCAD", "EURCAD"],
    "NZD": ["NZDUSD"],
    "CHF": ["USDCHF", "EURCHF"],
}


def classify_event_tier(title: str) -> int:
    """Classify event importance tier (1=highest, 3=lowest, 0=unknown)."""
    upper = title.upper().strip()
    for t1 in TIER_1_EVENTS:
        if t1 in upper:
            return 1
    for t2 in TIER_2_EVENTS:
        if t2 in upper:
            return 2
    for t3 in TIER_3_EVENTS:
        if t3 in upper:
            return 3
    return 0


def is_relevant_to_symbol(event_currency: str, symbol: str) -> bool:
    """Check if a calendar event's currency is relevant to the trading symbol."""
    sym = symbol.upper()
    cur = event_currency.upper()
    relevant = CURRENCY_SYMBOL_MAP.get(cur, [])
    return sym in relevant or any(sym.startswith(c) or sym.endswith(c) for c in [cur])


# =============================================================================
# 2. News Trading Gate — Blocks Entries During Dangerous Phases
# =============================================================================

@dataclass
class NewsGateResult:
    """Result of news gate evaluation."""
    allowed: bool
    phase: str  # "CLEAR", "PRE_NEWS", "SPIKE_BLACKOUT", "POST_NEWS_ENTRY", "TREND_CONTINUATION"
    block_reason: str = ""
    nearest_event: Optional[Dict[str, Any]] = None
    minutes_to_event: Optional[float] = None
    minutes_since_event: Optional[float] = None
    tier: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


class NewsTradingGate:
    """Pre-trade gate that blocks entries during dangerous news phases.

    Phase model (from institutional 3-phase framework):
      PRE_NEWS:        T-60min to T-5min  → Prepare, no new entries
      SPIKE_BLACKOUT:  T-5min to T+15min  → HARD BLOCK, no entries at all
      POST_NEWS_ENTRY: T+15min to T+60min → Optimal entry zone (Fib pullback)
      TREND_CONTINUATION: T+60min+         → Normal trading
      CLEAR:           No nearby events    → Normal trading

    Dynamic Adjustment:
      When a NewsRegimeCorrelation instance is provided, blackout windows are
      automatically adjusted based on historical event spike accuracy. Events
      that historically spike hard get wider windows; duds get narrower.
    """

    # Blackout windows by tier (minutes before/after event) — static defaults
    BLACKOUT_WINDOWS = {
        1: {"before": 60, "after": 20},  # Tier 1: 1hr before, 20min after
        2: {"before": 30, "after": 15},  # Tier 2: 30min before, 15min after
        3: {"before": 15, "after": 10},  # Tier 3: 15min before, 10min after
    }

    # Warning windows (reduce position size, no new entries)
    WARNING_WINDOWS = {
        1: {"before": 120, "after": 45},
        2: {"before": 60, "after": 30},
        3: {"before": 30, "after": 15},
    }

    def __init__(self, correlation_engine=None, blackout_windows_override=None):
        """Initialize the gate.

        Args:
            correlation_engine: Optional NewsRegimeCorrelation instance.
                When provided, blackout windows are dynamically adjusted based
                on historical event spike accuracy.
            blackout_windows_override: Optional dict override for static windows.
                Format: {tier: {"before": int, "after": int}}
        """
        self._correlation_engine = correlation_engine
        self._windows_override = blackout_windows_override

    def _get_blackout_windows(self, tier: int, event_title: str = "", symbol: str = "EURUSD") -> Dict[str, int]:
        """Get blackout windows, preferring dynamic correlation-based adjustment."""
        # 1. Check static override
        if self._windows_override and tier in self._windows_override:
            return self._windows_override[tier]

        # 2. Check correlation engine
        if self._correlation_engine is not None:
            try:
                adjusted = self._correlation_engine.get_adjusted_windows(
                    tier=tier, event_title=event_title, symbol=symbol,
                )
                return {"before": adjusted["before"], "after": adjusted["after"]}
            except Exception:
                pass  # Fall through to defaults

        # 3. Default static windows
        return self.BLACKOUT_WINDOWS.get(tier, self.BLACKOUT_WINDOWS[2])

    def evaluate(
        self,
        symbol: str,
        now: datetime,
        events: List[Dict[str, Any]],
    ) -> NewsGateResult:
        """Evaluate if trading is allowed right now for this symbol.

        Args:
            symbol: Trading symbol (e.g. "EURUSD")
            now: Current UTC datetime
            events: List of calendar events with 'time', 'currency', 'title', 'importance'

        Returns:
            NewsGateResult with allowed=True/False and phase info
        """
        now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)

        # Filter to relevant events for this symbol
        relevant = []
        for ev in events:
            ev_currency = ev.get("currency", ev.get("country", ""))
            if not is_relevant_to_symbol(ev_currency, symbol):
                continue
            ev_time = self._parse_event_time(ev)
            if ev_time is None:
                continue
            tier = classify_event_tier(ev.get("title", ""))
            if tier == 0:
                # Use importance field as fallback
                imp = str(ev.get("importance", "")).upper()
                if imp == "HIGH":
                    tier = 2
                elif imp == "MEDIUM":
                    tier = 3
                else:
                    continue  # Skip low/unknown importance
            relevant.append({
                "title": ev.get("title", ""),
                "currency": ev_currency,
                "time": ev_time,
                "tier": tier,
                "forecast": ev.get("forecast"),
                "previous": ev.get("previous"),
                "actual": ev.get("actual"),
            })

        if not relevant:
            return NewsGateResult(allowed=True, phase="CLEAR")

        # Find the nearest event
        nearest = None
        min_abs_delta = float("inf")
        for ev in relevant:
            delta = abs((ev["time"] - now_utc).total_seconds())
            if delta < min_abs_delta:
                min_abs_delta = delta
                nearest = ev

        if nearest is None:
            return NewsGateResult(allowed=True, phase="CLEAR")

        delta_sec = (nearest["time"] - now_utc).total_seconds()
        delta_min = delta_sec / 60.0
        tier = nearest["tier"]
        blackout = self._get_blackout_windows(tier, nearest.get("title", ""), symbol)
        warning = self.WARNING_WINDOWS.get(tier, self.WARNING_WINDOWS[2])

        # Determine phase
        if delta_sec < 0:
            # Event is in the past
            minutes_since = abs(delta_min)
            if minutes_since <= blackout["after"]:
                return NewsGateResult(
                    allowed=False,
                    phase="SPIKE_BLACKOUT",
                    block_reason=f"Spike blackout: {nearest['title']} released {minutes_since:.0f}min ago (Tier {tier}, {blackout['after']}min blackout)",
                    nearest_event=nearest,
                    minutes_since_event=minutes_since,
                    tier=tier,
                )
            elif minutes_since <= warning["after"]:
                return NewsGateResult(
                    allowed=True,
                    phase="POST_NEWS_ENTRY",
                    block_reason="",
                    nearest_event=nearest,
                    minutes_since_event=minutes_since,
                    tier=tier,
                    details={"guidance": "Optimal entry zone — Fib pullback on 38.2%-61.8% retracement"},
                )
            else:
                return NewsGateResult(
                    allowed=True,
                    phase="TREND_CONTINUATION",
                    nearest_event=nearest,
                    minutes_since_event=minutes_since,
                    tier=tier,
                )
        else:
            # Event is in the future
            minutes_to = delta_min
            if minutes_to <= blackout["before"]:
                return NewsGateResult(
                    allowed=False,
                    phase="SPIKE_BLACKOUT",
                    block_reason=f"Pre-spike blackout: {nearest['title']} in {minutes_to:.0f}min (Tier {tier}, {blackout['before']}min blackout)",
                    nearest_event=nearest,
                    minutes_to_event=minutes_to,
                    tier=tier,
                )
            elif minutes_to <= warning["before"]:
                return NewsGateResult(
                    allowed=False,
                    phase="PRE_NEWS",
                    block_reason=f"Pre-news warning: {nearest['title']} in {minutes_to:.0f}min (Tier {tier}, {warning['before']}min warning)",
                    nearest_event=nearest,
                    minutes_to_event=minutes_to,
                    tier=tier,
                )
            else:
                return NewsGateResult(
                    allowed=True,
                    phase="CLEAR",
                    nearest_event=nearest,
                    minutes_to_event=minutes_to,
                    tier=tier,
                )

    @staticmethod
    def _parse_event_time(ev: Dict[str, Any]) -> Optional[datetime]:
        """Parse event time from various formats."""
        for key in ("time", "datetime_utc", "event_time", "date"):
            raw = ev.get(key)
            if raw is None:
                continue
            if isinstance(raw, datetime):
                return raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
            s = str(raw).strip()
            if not s:
                continue
            for fmt in (
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
            ):
                try:
                    dt = datetime.strptime(s, fmt)
                    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            # Try combining date + time fields
            date_str = ev.get("date", "")
            time_str = ev.get("time", "")
            if date_str and time_str:
                try:
                    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
        return None


# =============================================================================
# 3. Delta (Surprise Factor) Calculator
# =============================================================================

def calculate_delta(actual: float, forecast: float, historical_std: float = 1.0) -> Dict[str, Any]:
    """Calculate surprise factor (Delta = actual - forecast) normalized by historical std dev.

    Returns:
        dict with delta, z_score, reaction severity, and direction
    """
    raw_delta = actual - forecast
    sigma = max(1e-6, historical_std)
    z = raw_delta / sigma
    abs_z = abs(z)

    if abs_z < 0.5:
        reaction, severity = "minimal", "low"
    elif abs_z < 1.0:
        reaction, severity = "moderate", "medium"
    elif abs_z < 2.0:
        reaction, severity = "strong", "high"
    else:
        reaction, severity = "extreme", "critical"

    return {
        "delta": round(raw_delta, 4),
        "z_score": round(z, 4),
        "abs_z_score": round(abs_z, 4),
        "reaction": reaction,
        "severity": severity,
        "direction": "hawkish" if z > 0 else "dovish" if z < 0 else "neutral",
        "is_regime_shift": abs_z >= 2.0,
    }


# =============================================================================
# 4. Rolling Economic Surprise Index (ESI)
# =============================================================================

class EconomicSurpriseIndex:
    """Rolling ESI tracking % of recent releases beating consensus."""

    @staticmethod
    def calculate(
        releases: List[Dict[str, Any]],
        lookback: int = 30,
    ) -> Dict[str, Any]:
        """Calculate ESI from a list of {actual, consensus} releases."""
        if not releases:
            return {
                "esi_score": 50.0,
                "momentum": "NEUTRAL",
                "beating_pct": 50.0,
                "sample_size": 0,
                "bullish_bias": False,
                "dovish_risk": False,
            }

        sample = releases[-lookback:]
        beats = 0
        total = 0
        for r in sample:
            try:
                actual = float(r.get("actual", 0))
                consensus = float(r.get("consensus", 0))
                beats += 1 if actual > consensus else 0
                total += 1
            except (ValueError, TypeError):
                continue

        esi = float((beats / total) * 100.0) if total > 0 else 50.0

        if esi >= 80:
            momentum = "PERSISTENT_EXPANSION_HAWKISH"
        elif esi >= 55:
            momentum = "MODERATE_POSITIVE"
        elif esi <= 20:
            momentum = "PERSISTENT_DECELERATION_DOVISH"
        elif esi <= 45:
            momentum = "SOFTENING_NEGATIVE"
        else:
            momentum = "NEUTRAL_MIXED"

        return {
            "esi_score": round(esi, 1),
            "momentum": momentum,
            "beating_pct": round(esi, 1),
            "sample_size": total,
            "bullish_bias": esi >= 55.0,
            "dovish_risk": esi <= 30.0,
        }


# =============================================================================
# 5. NFP / CPI Prediction Wrappers
# =============================================================================

def predict_nfp(
    adp_change_k: float = 0.0,
    adp_consensus_k: float = 0.0,
    jobless_claims_4w_ma: float = 215.0,
    jobless_claims_baseline: float = 215.0,
    ism_employment_pmi: float = 50.0,
    jolts_openings_millions: float = 8.5,
    jolts_baseline: float = 8.0,
    esi_score: float = 50.0,
) -> Dict[str, Any]:
    """Predict NFP using leading indicators.

    Weights (from research):
        ADP Employment:        25%
        Initial Jobless Claims: 20%
        ISM Employment PMI:    20%
        JOLTS Job Openings:    15%
        ESI Trend:             20%
    """
    from core.macro_event_alpha_engine import MacroLeadingIndicatorScorecard
    result = MacroLeadingIndicatorScorecard.calculate_nfp_scorecard(
        adp_change_k=adp_change_k,
        adp_consensus_k=adp_consensus_k,
        jobless_claims_4w_ma_k=jobless_claims_4w_ma,
        jobless_claims_baseline_k=jobless_claims_baseline,
        ism_employment_pmi=ism_employment_pmi,
        jolts_openings_millions=jolts_openings_millions,
        jolts_baseline_millions=jolts_baseline,
        esi_trend_score=esi_score,
    )
    return {
        "event": "NFP",
        "composite_score": result.composite_score,
        "directional_bias": result.directional_bias,
        "confidence": result.confidence,
        "sub_scores": result.sub_scores,
        "recommended_model": result.recommended_model,
        "weights": {
            "ADP_employment": 0.25,
            "initial_jobless_claims": 0.20,
            "ISM_employment": 0.20,
            "JOLTS_openings": 0.15,
            "ESI_trend": 0.20,
        },
    }


def predict_cpi(
    ppi_mom_pct: float = 0.0,
    ppi_consensus_pct: float = 0.0,
    crude_oil_30d_change_pct: float = 0.0,
    agriculture_basket_change_pct: float = 0.0,
    metals_basket_change_pct: float = 0.0,
    import_price_mom_pct: float = 0.0,
    ism_prices_paid_pmi: float = 50.0,
    wage_growth_yoy_pct: float = 4.0,
    wage_baseline_pct: float = 3.5,
) -> Dict[str, Any]:
    """Predict CPI using leading indicators.

    Weights (from research):
        PPI:               25%
        Commodity costs:   30%
        Import prices:     15%
        ISM Prices Paid:   15%
        Wage pressures:    15%
    """
    from core.macro_event_alpha_engine import MacroLeadingIndicatorScorecard
    result = MacroLeadingIndicatorScorecard.calculate_cpi_scorecard(
        ppi_mom_pct=ppi_mom_pct,
        ppi_consensus_pct=ppi_consensus_pct,
        crude_oil_30d_change_pct=crude_oil_30d_change_pct,
        agriculture_basket_change_pct=agriculture_basket_change_pct,
        metals_basket_change_pct=metals_basket_change_pct,
        import_price_mom_pct=import_price_mom_pct,
        ism_prices_paid_pmi=ism_prices_paid_pmi,
        wage_growth_yoy_pct=wage_growth_yoy_pct,
        wage_baseline_pct=wage_baseline_pct,
    )
    return {
        "event": "CPI",
        "composite_score": result.composite_score,
        "directional_bias": result.directional_bias,
        "confidence": result.confidence,
        "sub_scores": result.sub_scores,
        "recommended_model": result.recommended_model,
        "weights": {
            "PPI": 0.25,
            "commodities": 0.30,
            "import_prices": 0.15,
            "ISM_prices_paid": 0.15,
            "wage_pressures": 0.15,
        },
    }


# =============================================================================
# 6. News Phase Detector (for LLM prompt context)
# =============================================================================

def detect_news_phase(event_time: str, current_time: str) -> Dict[str, Any]:
    """Detect which phase of news price action we're in.

    Returns:
        dict with phase name, time deltas, and trading guidance
    """
    try:
        et = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        ct = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return {"phase": "unknown", "guidance": "Cannot determine phase from provided times"}

    et_utc = et.astimezone(timezone.utc) if et.tzinfo else et.replace(tzinfo=timezone.utc)
    ct_utc = ct.astimezone(timezone.utc) if ct.tzinfo else ct.replace(tzinfo=timezone.utc)
    diff_min = (ct_utc - et_utc).total_seconds() / 60.0

    if diff_min < -60:
        return {"phase": "pre_news_distant", "minutes_to_event": round(abs(diff_min), 1),
                "guidance": "No restrictions — event is >1hr away. Monitor scorecard for directional bias."}
    elif diff_min < -5:
        return {"phase": "pre_news_imminent", "minutes_to_event": round(abs(diff_min), 1),
                "guidance": "WARNING: Event imminent. Cancel unfilled pending orders. Prepare levels."}
    elif diff_min < 0:
        return {"phase": "pre_news_critical", "minutes_to_event": round(abs(diff_min), 1),
                "guidance": "HARD BLACKOUT: No new entries. All positions should have stops in place."}
    elif diff_min < 5:
        return {"phase": "spike", "minutes_since_event": round(diff_min, 1),
                "guidance": "DANGER ZONE: Extreme volatility, slippage, whipsaws. NO ENTRIES."}
    elif diff_min < 15:
        return {"phase": "spike_exhaustion", "minutes_since_event": round(diff_min, 1),
                "guidance": "Still dangerous. Spreads widening. Wait for exhaustion candles."}
    elif diff_min < 30:
        return {"phase": "post_news_entry", "minutes_since_event": round(diff_min, 1),
                "guidance": "OPTIMAL ENTRY ZONE: Look for 38.2%-61.8% Fibonacci pullback. Confirm direction."}
    elif diff_min < 60:
        return {"phase": "trend_continuation", "minutes_since_event": round(diff_min, 1),
                "guidance": "Trend continuation phase. Spreads normal. Standard risk management applies."}
    else:
        return {"phase": "normal", "minutes_since_event": round(diff_min, 1),
                "guidance": "Event fully absorbed. Resume normal trading."}


# =============================================================================
# 7. News Summary Builder for LLM Prompt
# =============================================================================

def build_news_summary_for_llm(
    symbol: str,
    events: List[Dict[str, Any]],
    esi: Optional[Dict[str, Any]] = None,
    nfp_prediction: Optional[Dict[str, Any]] = None,
    cpi_prediction: Optional[Dict[str, Any]] = None,
    max_events: int = 5,
) -> str:
    """Build a concise news summary block for the LLM prompt template.

    Returns a formatted string that can be inserted into the signal system prompt.
    """
    now = datetime.now(timezone.utc)
    lines = []

    # ESI
    if esi:
        score = esi.get("esi_score", 50)
        mom = esi.get("momentum", "NEUTRAL")
        bias = "Bullish USD" if esi.get("bullish_bias") else ("Dovish USD" if esi.get("dovish_risk") else "Neutral")
        lines.append(f"   ESI: {score:.0f}/100 ({mom}) — {bias}")

    # NFP prediction
    if nfp_prediction:
        bias = nfp_prediction.get("directional_bias", "IN_LINE")
        score = nfp_prediction.get("composite_score", 0)
        conf = nfp_prediction.get("confidence", 0)
        lines.append(f"   NFP Prediction: {bias} (score={score:+.1f}, confidence={conf:.0%})")

    # CPI prediction
    if cpi_prediction:
        bias = cpi_prediction.get("directional_bias", "IN_LINE")
        score = nfp_prediction.get("composite_score", 0) if nfp_prediction else cpi_prediction.get("composite_score", 0)
        lines.append(f"   CPI Prediction: {bias} (score={score:+.1f})")

    # Upcoming events
    relevant = []
    for ev in events:
        ev_currency = ev.get("currency", ev.get("country", ""))
        if not is_relevant_to_symbol(ev_currency, symbol):
            continue
        tier = classify_event_tier(ev.get("title", ""))
        if tier == 0:
            imp = str(ev.get("importance", "")).upper()
            tier = 2 if imp == "HIGH" else 3 if imp == "MEDIUM" else 0
        if tier == 0:
            continue
        ev_time = NewsTradingGate._parse_event_time(ev)
        if ev_time is None:
            continue
        delta_min = (ev_time - now).total_seconds() / 60.0
        if delta_min < -30:
            continue  # Skip events more than 30min in the past
        relevant.append({
            "title": ev.get("title", ""),
            "tier": tier,
            "minutes": round(delta_min, 0),
            "forecast": ev.get("forecast", "?"),
            "previous": ev.get("previous", "?"),
        })

    relevant.sort(key=lambda x: abs(x["minutes"]))
    relevant = relevant[:max_events]

    if relevant:
        lines.append("   Upcoming Events:")
        for ev in relevant:
            stars = "⭐" * (4 - ev["tier"])  # Tier 1 = ⭐⭐⭐, Tier 2 = ⭐⭐, Tier 3 = ⭐
            if ev["minutes"] > 0:
                timing = f"in {ev['minutes']:.0f}min"
            elif ev["minutes"] > -5:
                timing = f"{abs(ev['minutes']):.0f}min ago"
            else:
                timing = f"{abs(ev['minutes']):.0f}min ago (absorbed)"
            lines.append(
                f"     {stars} {ev['title']}: {timing} | forecast={ev['forecast']} prev={ev['previous']}"
            )

    # Phase detection for nearest event
    if relevant:
        nearest = relevant[0]
        phase_info = detect_news_phase(
            event_time=(now + __import__("datetime").timedelta(minutes=nearest["minutes"])).isoformat(),
            current_time=now.isoformat(),
        )
        lines.append(f"   Phase: {phase_info['phase']} — {phase_info['guidance']}")

    if not lines:
        return "   No significant upcoming news events for this symbol."

    return "\n".join(lines)


# =============================================================================
# 8. Master News Trading Summary (for orchestrator)
# =============================================================================

def get_news_trading_context(
    symbol: str,
    events: List[Dict[str, Any]],
    now: Optional[datetime] = None,
    live_indicator_fetcher: Optional[Any] = None,
    correlation_engine: Optional[Any] = None,
) -> Dict[str, Any]:
    """Get the complete news trading context for a symbol.

    Args:
        symbol: Trading symbol (e.g. "EURUSD")
        events: Calendar events list
        now: Current datetime (UTC)
        live_indicator_fetcher: Optional LiveIndicatorFetcher for real FRED data.
            When provided, NFP/CPI predictions use actual FRED indicators instead
            of defaults, and ESI is computed from real releases.
        correlation_engine: Optional NewsRegimeCorrelation instance.
            When provided, blackout windows are dynamically adjusted based on
            historical event spike accuracy.

    Returns a dict with:
      - gate: NewsGateResult (is trading allowed?)
      - esi: ESI data (real or calendar-based)
      - nfp_prediction: NFP leading indicator scorecard
      - cpi_prediction: CPI leading indicator scorecard
      - nearest_event: nearest relevant event info
      - phase: current news phase
      - summary_for_llm: formatted string for LLM prompt
      - is_live: bool — whether real FRED data was used
      - correlation: correlation analysis data (if engine provided)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    gate = NewsTradingGate(correlation_engine=correlation_engine)
    gate_result = gate.evaluate(symbol=symbol, now=now, events=events)

    # --- LIVE INDICATOR PATH ---
    is_live = False
    nfp_prediction = None
    cpi_prediction = None
    esi = None

    if live_indicator_fetcher is not None:
        try:
            summary_data = live_indicator_fetcher.get_summary()
            nfp_prediction = summary_data.get("nfp_prediction")
            cpi_prediction = summary_data.get("cpi_prediction")
            esi = summary_data.get("esi")
            is_live = summary_data.get("nfp_is_live", False) or summary_data.get("cpi_is_live", False)
            logger.info(
                "Live indicators: NFP=%s CPI=%s ESI=%.1f (live=%s)",
                nfp_prediction.get("directional_bias") if nfp_prediction else "N/A",
                cpi_prediction.get("directional_bias") if cpi_prediction else "N/A",
                esi.get("esi_score", 50) if esi else 50.0,
                is_live,
            )
        except Exception as exc:
            logger.warning("Live indicator fetch failed, falling back to calendar data: %s", exc)

    # --- FALLBACK: Build ESI from calendar releases ---
    if esi is None:
        recent_releases = [
            ev for ev in events
            if ev.get("actual") is not None and is_relevant_to_symbol(ev.get("currency", ""), symbol)
        ]
        esi = EconomicSurpriseIndex.calculate(recent_releases)

    # Build LLM summary
    summary = build_news_summary_for_llm(
        symbol=symbol, events=events, esi=esi,
        nfp_prediction=nfp_prediction, cpi_prediction=cpi_prediction,
    )

    # --- CORRELATION ANALYSIS ---
    correlation_data = None
    if correlation_engine is not None:
        try:
            report = correlation_engine.get_correlation_report(symbol=symbol)
            correlation_data = {
                "total_events": report.total_events_recorded,
                "hit_rate": report.overall_hit_rate,
                "accuracy_score": report.accuracy_score,
                "window_efficiency": report.window_efficiency,
                "false_positive_rate": report.false_positive_rate,
                "adjustments": report.regime_adjustments,
            }
        except Exception as exc:
            logger.warning("Correlation analysis failed: %s", exc)

    return {
        "gate": {
            "allowed": gate_result.allowed,
            "phase": gate_result.phase,
            "block_reason": gate_result.block_reason,
            "tier": gate_result.tier,
            "minutes_to_event": gate_result.minutes_to_event,
            "minutes_since_event": gate_result.minutes_since_event,
        },
        "esi": esi,
        "nfp_prediction": nfp_prediction,
        "cpi_prediction": cpi_prediction,
        "nearest_event": gate_result.nearest_event,
        "summary_for_llm": summary,
        "is_live": is_live,
        "correlation": correlation_data,
    }
