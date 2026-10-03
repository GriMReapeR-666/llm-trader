"""Deterministic session, rollover, and liquidity-entry filters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MarketConditionResult:
    allowed: bool
    session: str
    reasons: List[str]


@dataclass(frozen=True)
class RolloverWindow:
    start_minute_utc: int
    end_minute_utc: int

    @classmethod
    def for_broker(cls, name: str) -> "RolloverWindow":
        """Factory for common brokers."""
        name = name.lower()
        if name == "icmarkets":
            return cls(21 * 60 + 59, 22 * 60 + 5)
        if name == "darwinex":
            return cls(21 * 60 + 55, 22 * 60 + 5)
        return cls(21 * 60 + 55, 22 * 60 + 10)


def session_name(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    hour = now.astimezone(timezone.utc).hour
    if 22 <= hour or hour < 7:
        return "ASIAN"
    if hour < 12:
        return "LONDON"
    if hour < 16:
        return "NY-LONDON"
    if hour < 21:
        return "NEW YORK"
    return "SESSION CLOSE"


def is_london_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return 7 <= now.hour < 16


def is_ny_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return 12 <= now.hour < 21


def is_london_ny_overlap(now: datetime | None = None) -> bool:
    return is_london_open(now) and is_ny_open(now)


def is_event_blackout(
    events: Optional[Iterable[Dict[str, Any]]] = None,
    event_timestamp: Optional[datetime] = None,
    now: Optional[datetime] = None,
    pre_event_minutes: int = 15,
    post_event_minutes: int = 5,
    min_importance: int = 2,
) -> Tuple[bool, Optional[str]]:
    """Determine whether current time is within a pre or post high-impact event window."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    # 1. Check direct event_timestamp
    if event_timestamp is not None:
        try:
            ts = event_timestamp.astimezone(timezone.utc)
            delta_sec = (ts - now).total_seconds()
            if -post_event_minutes * 60 <= delta_sec <= pre_event_minutes * 60:
                rel = f"T{-delta_sec / 60:+.0f}m"
                return True, f"Tier-1 macro event blackout ({rel})"
        except Exception:
            pass

    # 2. Check list of events
    if events:
        for ev in events:
            if not isinstance(ev, dict):
                continue
            importance = int(ev.get("importance", 0) or 0)
            impact = str(ev.get("impact", "")).upper()
            if importance < min_importance and impact not in ("HIGH", "CRITICAL"):
                continue

            ev_time = ev.get("time") or ev.get("timestamp") or ev.get("date")
            if not ev_time:
                continue

            ts = None
            if isinstance(ev_time, (int, float)):
                try:
                    ts = datetime.fromtimestamp(float(ev_time), tz=timezone.utc)
                except Exception:
                    continue
            elif isinstance(ev_time, datetime):
                ts = ev_time.astimezone(timezone.utc)
            elif isinstance(ev_time, str):
                try:
                    ts = datetime.fromisoformat(ev_time.replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    continue

            if ts is not None:
                delta_sec = (ts - now).total_seconds()
                if -post_event_minutes * 60 <= delta_sec <= pre_event_minutes * 60:
                    name = ev.get("title") or ev.get("event") or ev.get("name") or "macroeconomic event"
                    rel = f"T{-delta_sec / 60:+.0f}m"
                    return True, f"Tier-1 macro event blackout: {name} ({rel})"

    return False, None


def evaluate(*, now: datetime | None = None, volume_ratio: Any = None, dom_depth: Any = None,
             dom_available: bool = True, require_dom: bool = False, min_volume_ratio: float = 0.35,
             rollover_start_minute_utc: int = 21 * 60 + 55, rollover_end_minute_utc: int = 22 * 60 + 10,
             friday_close_hour_utc: int = 20, symbol: Optional[str] = None,
             events: Optional[Iterable[Dict[str, Any]]] = None,
             event_timestamp: Optional[datetime] = None, event_blackout_minutes: int = 0,
             post_event_blackout_minutes: int = 5,
             sunday_open_hour_utc: int = 22, sunday_gap_protection_minutes: int = 15) -> MarketConditionResult:
    """Fail closed for a weekend, broker rollover window, or required liquidity.

    The time windows are conservative defaults and must be reviewed against the
    broker preflight policy before a live rollout.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    reasons: List[str] = []
    minute = now.hour * 60 + now.minute
    
    # Weekend / Friday Close
    if now.weekday() >= 5 and now.weekday() != 6:
        reasons.append("weekend market closure")
    elif now.weekday() == 6:
        open_minute = sunday_open_hour_utc * 60
        if minute < open_minute:
            reasons.append("weekend market closure")
        elif open_minute <= minute < open_minute + sunday_gap_protection_minutes:
            reasons.append("Sunday gap protection window")
    if now.weekday() == 4 and now.hour >= friday_close_hour_utc:
        reasons.append("Friday close no-trade window")
        
    # Rollover
    if rollover_start_minute_utc <= minute <= rollover_end_minute_utc:
        reasons.append("broker rollover/maintenance no-trade window")
        
    # Events (dual-sided pre and post window)
    pre_minutes = event_blackout_minutes if event_blackout_minutes > 0 else 15
    if event_timestamp or events:
        is_bo, bo_reason = is_event_blackout(
            events=events,
            event_timestamp=event_timestamp,
            now=now,
            pre_event_minutes=pre_minutes,
            post_event_minutes=post_event_blackout_minutes,
        )
        if is_bo and bo_reason:
            reasons.append(bo_reason)
            if "T-" in bo_reason or "upcoming" in bo_reason or (event_timestamp and (event_timestamp.astimezone(timezone.utc) - now).total_seconds() >= 0):
                if "upcoming macroeconomic event blackout" not in reasons:
                    reasons.append("upcoming macroeconomic event blackout")

    # Liquidity & Volume
    try:
        if volume_ratio is not None and float(volume_ratio) < min_volume_ratio:
            # Skip volume check for Asian-session-native pairs during Asian session
            skip_volume = False
            if symbol and session_name(now) == "ASIAN":
                sym_upper = symbol.upper()
                if "JPY" in sym_upper or "AUD" in sym_upper:
                    skip_volume = True
            if not skip_volume:
                reasons.append("abnormally low tick-volume liquidity")
    except (TypeError, ValueError):
        reasons.append("malformed volume liquidity measure")
        
    # DOM
    if require_dom:
        if not dom_available:
            reasons.append("DOM unavailable for liquidity-required setup")
        try:
            if int(dom_depth or 0) <= 0:
                reasons.append("DOM depth unavailable for liquidity-required setup")
        except (TypeError, ValueError):
            reasons.append("malformed DOM depth")
            
    return MarketConditionResult(not reasons, session_name(now), reasons)
