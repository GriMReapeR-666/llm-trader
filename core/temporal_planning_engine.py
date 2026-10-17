"""Multi-Horizon Temporal Planning Engine.

Tracks multi-horizon temporal cycles across institutional order flow:
- Tier 1: Annual Macro & Liquidity Seasonality (Q1 to Q4).
- Tier 2: 4-Week Monthly Catalyst Cycle (Weeks 1 to 4).
- Tier 3: Day-of-the-Week Structural Templates (Mon to Fri).
- Tier 4: Intraday Session Timing & Killzones (Asian, London, NY, London Fix).

Doctrine:
- execution_authority: strictly False.
- Pure Python stdlib.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

EXECUTION_AUTHORITY = False


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def classify_yearly_regime(dt: datetime) -> Dict[str, Any]:
    """Classifies annual macro liquidity seasonality (Tier 1)."""
    utc_dt = _ensure_utc(dt)
    m = utc_dt.month
    d = utc_dt.day

    if 1 <= m <= 3:
        quarter = "Q1"
        name = "FRESH_CAPITAL_DEPLOYMENT_AND_MANDATES"
        guidance = "High trend follow-through post-first-FOMC and January NFP."
        freeze = False
    elif 4 <= m <= 6:
        quarter = "Q2"
        name = "TAX_RECEIPTS_DRAIN_PRE_SUMMER"
        guidance = "Mid-April US tax inflows drain bank reserves into TGA; USD liquidity squeeze."
        freeze = False
    elif 7 <= m <= 9:
        quarter = "Q3"
        name = "SUMMER_LIQUIDITY_THINNING_JACKSON_HOLE"
        guidance = "Thinner order books amplify volatility; late August Jackson Hole pivot."
        freeze = False
    else:
        quarter = "Q4"
        name = "AUTUMN_TREND_ACCELERATION_AND_TURN"
        guidance = "High trend efficiency through late Nov; G-SIB year-end balance sheet contraction."
        freeze = bool(m == 12 and d >= 15)

    return {
        "status": "READY_FOR_REVIEW",
        "quarter": quarter,
        "regime_name": name,
        "guidance": guidance,
        "year_end_freeze": freeze,
        "execution_authority": False,
    }


def classify_monthly_cycle(dt: datetime) -> Dict[str, Any]:
    """Classifies the 4-week monthly economic catalyst window (Tier 2)."""
    utc_dt = _ensure_utc(dt)
    day = utc_dt.day

    if 1 <= day <= 7:
        week_num = 1
        name = "GROWTH_LABOR_BASELINE"
        catalysts = ["Global Manufacturing/Services PMIs", "ADP", "US Non-Farm Payrolls"]
        guidance = "Map the Monthly Initial Balance (Week 1 High/Low)."
    elif 8 <= day <= 14:
        week_num = 2
        name = "INFLATION_REAL_YIELDS"
        catalysts = ["US CPI", "PPI", "US Treasury 3Y/10Y/30Y Refunding Auctions"]
        guidance = "Reprice real yields and breakevens; detect Week 1 sweep for true monthly trend."
    elif 15 <= day <= 21:
        week_num = 3
        name = "POLICY_DECISIONS_AND_OPEX"
        catalysts = ["FOMC", "ECB", "BOJ Rate Decisions", "Third-Friday Options Expiration (OPEX)"]
        guidance = "Dealer gamma unpins post-OPEX; execute highest-conviction monthly breakouts/reversals."
    else:
        week_num = 4
        name = "REBALANCING_PCE_AND_FIX"
        catalysts = ["US Core PCE", "GDP Revisions", "WMR 4:00 PM London Fix Rebalancing"]
        guidance = "Transition from trend-following to mean-reversion during final 3 trading days."

    return {
        "status": "READY_FOR_REVIEW",
        "monthly_week": week_num,
        "window_name": name,
        "primary_catalysts": catalysts,
        "guidance": guidance,
        "execution_authority": False,
    }


def classify_day_of_week_template(dt: datetime) -> Dict[str, Any]:
    """Classifies the day-of-the-week structural profile (Tier 3)."""
    utc_dt = _ensure_utc(dt)
    iso_day = utc_dt.isocalendar()[2]  # 1 = Mon, ..., 7 = Sun
    hour = utc_dt.hour

    is_trade_1 = False
    is_trade_2 = False
    weekend_derisk = False

    if iso_day == 1:
        day_name = "MONDAY"
        profile = "RANGE_DEFINITION_AND_INVENTORY_BUILDING"
        guidance = "Rarely enter. Record Monday High and Low as weekly liquidity reference points."
    elif iso_day == 2:
        day_name = "TUESDAY"
        profile = "TURNAROUND_TUESDAY_JUDAS_SWING"
        guidance = "Algos drive price against macro bias to sweep Mon H/L. Primary Trade 1 window."
        is_trade_1 = True
    elif iso_day == 3:
        day_name = "WEDNESDAY"
        profile = "MID_WEEK_CATALYST_AND_CORE_EXPANSION"
        guidance = "Primary range expansion day. Mid-week liquidity reversal if Tue trended without sweep."
    elif iso_day == 4:
        day_name = "THURSDAY"
        profile = "TREND_CONTINUATION_OR_PRE_NFP_CONSOLIDATION"
        guidance = "Extends post-catalyst move toward external weekly targets. Trade 2 deployment window."
        is_trade_2 = True
    elif iso_day == 5:
        day_name = "FRIDAY"
        profile = "TARGET_FULFILLMENT_AND_WEEKEND_DERISKING"
        guidance = "Desks square books. Lock in TP targets; close trades < 1.0 ATR profit by 16:00 UTC."
        weekend_derisk = bool(hour >= 16)
    else:
        day_name = "WEEKEND"
        profile = "MARKETS_CLOSED_SCANNING_WINDOW"
        guidance = "Run data pipeline; ingest COT reports; compute Net Conviction Score S_{i,t}."

    return {
        "status": "READY_FOR_REVIEW",
        "iso_day": iso_day,
        "day_name": day_name,
        "structural_profile": profile,
        "guidance": guidance,
        "is_trade_1_window": is_trade_1,
        "is_trade_2_window": is_trade_2,
        "weekend_derisking_active": weekend_derisk,
        "execution_authority": False,
    }


def detect_session_killzone(dt: datetime) -> Dict[str, Any]:
    """Detects institutional liquidity session killzones (Tier 4)."""
    utc_dt = _ensure_utc(dt)
    h = utc_dt.hour
    m = utc_dt.minute

    total_m = h * 60 + m

    if 0 <= total_m < 360:  # 00:00 - 06:00 UTC
        kz = "ASIAN_ACCUMULATION"
        role = "Accumulation box; tags Asian Session High (ASH) and Low (ASL) as resting pools."
        active = True
    elif 420 <= total_m < 600:  # 07:00 - 10:00 UTC
        kz = "LONDON_OPEN"
        role = "Manipulation / Stop Hunt; sweeps ASH or ASL to fill institutional limit orders."
        active = True
    elif 720 <= total_m < 960:  # 12:00 - 16:00 UTC
        kz = "NY_OPEN"
        role = "Macro Catalyst & True Expansion; US data hits at 12:30/13:30 UTC."
        active = True
    else:
        kz = "OFF_HOURS_DORMANT"
        role = "Low-liquidity dead zone; system remains dormant."
        active = False

    is_fix = bool(900 <= total_m < 960)  # 15:00 - 16:00 UTC

    return {
        "status": "READY_FOR_REVIEW",
        "killzone": kz,
        "role": role,
        "is_active_killzone": active,
        "is_london_fix_window": is_fix,
        "execution_authority": False,
    }


def evaluate_temporal_context(dt: datetime) -> Dict[str, Any]:
    """Synthesizes the complete 4-tier temporal planning context."""
    utc_dt = _ensure_utc(dt)
    y = classify_yearly_regime(utc_dt)
    m = classify_monthly_cycle(utc_dt)
    d = classify_day_of_week_template(utc_dt)
    kz = detect_session_killzone(utc_dt)

    high_prob = bool(
        d["iso_day"] in (2, 3, 4)
        and kz["killzone"] in ("LONDON_OPEN", "NY_OPEN")
        and not y["year_end_freeze"]
    )

    return {
        "status": "READY_FOR_REVIEW",
        "utc_timestamp": utc_dt.isoformat(),
        "tier_1_yearly": y,
        "tier_2_monthly": m,
        "tier_3_day": d,
        "tier_4_killzone": kz,
        "is_high_probability_execution_window": high_prob,
        "execution_authority": False,
    }


class TemporalPlanningEngine:
    """Class wrapper for temporal evaluations."""

    @staticmethod
    def get_context(dt: Optional[datetime] = None) -> Dict[str, Any]:
        target = dt or datetime.now(timezone.utc)
        return evaluate_temporal_context(target)
