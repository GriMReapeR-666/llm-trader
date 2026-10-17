"""Deterministic planned-entry and pre-submit quote checks.

This module intentionally has no MT5/LLM dependency.  It turns a validated
trade hypothesis into a broker-appropriate pending order, or rejects it when
the live quote has made that plan unsafe/ambiguous.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class PlannedEntry:
    order_type: str
    symbol: str
    side: str
    entry: float
    expires_at: str
    quote_bid: float
    quote_ask: float
    quote_time_msc: int
    max_spread: float
    entry_tolerance: float

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RotationAction:
    """Represents an atomic portfolio sleeve rotation: close decayed position first, then enter new."""
    decayed_ticket: int
    decayed_symbol: str
    decayed_side: str
    decayed_volume: float
    decayed_magic: int
    new_symbol: str
    new_side: str
    new_volume: float
    new_entry: float
    new_sl: float
    new_tp: float
    magic: int
    reason: str = "portfolio_sleeve_rotation"
    planned_entry: Optional[PlannedEntry] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TwoLegPlannedEntry:
    """Represents a planned atomic two-leg pair execution with hedge ratio."""
    leg1_symbol: str
    leg1_side: str
    leg1_volume: float
    leg1_entry: float
    leg1_sl: float
    leg1_tp: float
    leg2_symbol: str
    leg2_side: str
    leg2_volume: float
    leg2_entry: float
    leg2_sl: float
    leg2_tp: float
    hedge_ratio: float
    magic: int
    reason: str = "two_leg_pair"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_emergency_exit_request(*, position_ticket: Any, symbol: Any, side: Any,
                                    volume: Any, magic: Any, reason: Any) -> Dict[str, Any]:
    """Validate the narrow, deterministic escape hatch for an existing position.

    Ordinary entries must use :func:`build_pending_entry`.  A market request is
    only valid as an emergency *exit* with an explicit position ticket, owned
    magic number, positive finite volume, and an auditable reason.  This helper
    only validates intent; it never grants order permission by itself.
    """
    reasons: list[str] = []
    if isinstance(position_ticket, bool) or not isinstance(position_ticket, int) or position_ticket <= 0:
        reasons.append("position ticket must be a positive integer")
    if not isinstance(symbol, str) or not symbol.strip():
        reasons.append("symbol is required")
    normalized_side = str(side or "").upper()
    if normalized_side not in {"BUY", "SELL"}:
        reasons.append("position side must be BUY or SELL")
    try:
        numeric_volume = float(volume)
        numeric_magic = int(magic)
    except (TypeError, ValueError, OverflowError):
        numeric_volume, numeric_magic = float("nan"), 0
    if not math.isfinite(numeric_volume) or numeric_volume <= 0:
        reasons.append("exit volume must be finite and positive")
    if numeric_magic <= 0:
        reasons.append("owned positive magic number is required")
    if not isinstance(reason, str) or not reason.strip():
        reasons.append("auditable emergency-exit reason is required")
    return {
        "valid": not reasons,
        "position_ticket": position_ticket,
        "symbol": str(symbol or "").strip(),
        "side": normalized_side,
        "volume": numeric_volume,
        "magic": numeric_magic,
        "reason": str(reason or "").strip()[:500],
        "reasons": reasons,
        "execution_authority": False,
    }


def build_pending_entry(*, symbol: str, side: str, entry: float, bid: float, ask: float,
                        quote_time_msc: int = 0, entry_tolerance: float,
                        min_entry_distance: float = 0.0, max_spread: float,
                        expires_seconds: int = 900) -> PlannedEntry:
    """Choose limit/stop based on an immutable planned price.

    An entry at the current quote is rejected rather than silently converted
    to a market order.  That forces a fresh proposal if the intended price was
    already reached while the LLM/risk checks were running.
    """
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        raise ValueError("pending entry symbol is required")
    side = str(side or "").upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("pending entry side must be BUY or SELL")
    try:
        numeric_entry, numeric_bid, numeric_ask = float(entry), float(bid), float(ask)
        numeric_tolerance = float(entry_tolerance)
        numeric_min_distance = float(min_entry_distance)
        numeric_max_spread = float(max_spread)
        numeric_expiry = float(expires_seconds)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid pre-submit bid/ask/entry or execution limits") from None
    if (any(not math.isfinite(value) or value <= 0 for value in (numeric_entry, numeric_bid, numeric_ask))
            or numeric_ask < numeric_bid
            or any(not math.isfinite(value) or value < 0 for value in
                   (numeric_tolerance, numeric_min_distance, numeric_max_spread))
            or not math.isfinite(numeric_expiry) or numeric_expiry <= 0):
        raise ValueError("invalid pre-submit bid/ask/entry")
    if numeric_ask - numeric_bid > numeric_max_spread:
        raise ValueError("pre-submit spread exceeds planned maximum")
    reference = numeric_ask if side == "BUY" else numeric_bid
    distance = abs(numeric_entry - reference)
    if distance <= max(numeric_tolerance, numeric_min_distance):
        raise ValueError("planned entry is at/through the current quote; refresh plan instead of using market execution")
    if side == "BUY":
        order_type = "buy_limit" if numeric_entry < numeric_ask else "buy_stop"
    else:
        order_type = "sell_limit" if numeric_entry > numeric_bid else "sell_stop"
    expiry = datetime.now(timezone.utc) + timedelta(seconds=max(1, int(numeric_expiry)))
    try:
        numeric_quote_time = float(quote_time_msc or 0)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid quote timestamp") from None
    if (not math.isfinite(numeric_quote_time) or numeric_quote_time <= 0
            or numeric_quote_time != int(numeric_quote_time)):
        raise ValueError("invalid quote timestamp")
    if numeric_expiry != int(numeric_expiry):
        raise ValueError("expiry must be a positive integer number of seconds")
    return PlannedEntry(order_type, normalized_symbol, side, numeric_entry, expiry.isoformat(), numeric_bid, numeric_ask,
                        int(numeric_quote_time), numeric_max_spread, numeric_tolerance)


def validate_quote(*, tick: Any, info: Any, max_tick_age_ms: int, max_spread: float) -> Tuple[bool, str, Dict[str, float]]:
    """Verify broker tradability and quote freshness immediately before send."""
    try:
        numeric_max_age = float(max_tick_age_ms)
        numeric_max_spread = float(max_spread)
        bid, ask = float(getattr(tick, "bid", 0) or 0), float(getattr(tick, "ask", 0) or 0)
        raw_tick_time = float(getattr(tick, "time_msc", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return False, "malformed quote or execution limits", {"bid": 0.0, "ask": 0.0, "spread": float("inf"), "tick_age_ms": float("inf"), "time_msc": 0.0}
    if (not math.isfinite(numeric_max_age) or numeric_max_age < 0 or
            not math.isfinite(numeric_max_spread) or numeric_max_spread < 0 or
            not all(math.isfinite(value) for value in (bid, ask, raw_tick_time))):
        return False, "malformed quote or execution limits", {"bid": bid, "ask": ask, "spread": float("inf"), "tick_age_ms": float("inf"), "time_msc": raw_tick_time}
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if raw_tick_time <= 0 or raw_tick_time != int(raw_tick_time):
        return False, "invalid quote timestamp at submission", {
            "bid": bid, "ask": ask, "spread": ask - bid,
            "tick_age_ms": float("inf"), "time_msc": raw_tick_time,
        }
    tick_time_msc = int(raw_tick_time)
    age_ms = now_ms - tick_time_msc if tick_time_msc else 10**12
    quote = {"bid": bid, "ask": ask, "spread": ask - bid, "tick_age_ms": age_ms, "time_msc": tick_time_msc}
    try:
        trade_mode = int(getattr(info, "trade_mode", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        trade_mode = 0
    if not info or trade_mode == 0:
        return False, "symbol no longer tradable at submission", quote
    if bid <= 0 or ask <= 0 or ask < bid:
        return False, "invalid quote at submission", quote
    if age_ms < -1_000 or age_ms > numeric_max_age:
        return False, "stale or clock-drifted quote at submission", quote
    if quote["spread"] > numeric_max_spread:
        return False, "spread widened after validation", quote
    return True, "quote valid", quote


def build_rotation_action(
    *,
    decayed_ticket: int,
    decayed_symbol: str,
    decayed_side: str,
    decayed_volume: float,
    decayed_magic: int,
    new_symbol: str,
    new_side: str,
    new_volume: float,
    new_entry: float,
    new_sl: float,
    new_tp: float,
    magic: int,
    reason: str = "portfolio_sleeve_rotation",
    planned_entry: Optional[PlannedEntry] = None,
) -> RotationAction:
    """Validate and construct an atomic sleeve rotation plan."""
    if int(decayed_ticket) <= 0:
        raise ValueError("decayed_ticket must be a positive integer")
    if not str(decayed_symbol).strip():
        raise ValueError("decayed_symbol is required")
    if str(decayed_side).upper() not in {"BUY", "SELL"}:
        raise ValueError("decayed_side must be BUY or SELL")
    if float(decayed_volume) <= 0:
        raise ValueError("decayed_volume must be positive")
    if not str(new_symbol).strip():
        raise ValueError("new_symbol is required")
    if str(new_side).upper() not in {"BUY", "SELL"}:
        raise ValueError("new_side must be BUY or SELL")
    if float(new_volume) <= 0:
        raise ValueError("new_volume must be positive")

    return RotationAction(
        decayed_ticket=int(decayed_ticket),
        decayed_symbol=str(decayed_symbol).strip().upper(),
        decayed_side=str(decayed_side).upper(),
        decayed_volume=float(decayed_volume),
        decayed_magic=int(decayed_magic),
        new_symbol=str(new_symbol).strip().upper(),
        new_side=str(new_side).upper(),
        new_volume=float(new_volume),
        new_entry=float(new_entry),
        new_sl=float(new_sl),
        new_tp=float(new_tp),
        magic=int(magic),
        reason=str(reason).strip(),
        planned_entry=planned_entry,
    )


def build_two_leg_pair_entry(
    *,
    leg1_symbol: str,
    leg1_side: str,
    leg1_volume: float,
    leg1_entry: float,
    leg1_sl: float,
    leg1_tp: float,
    leg2_symbol: str,
    hedge_ratio: float,
    leg2_entry: float,
    leg2_sl: float,
    leg2_tp: float,
    magic: int,
    reason: str = "two_leg_pair",
) -> TwoLegPlannedEntry:
    """Validate and construct an atomic two-leg pair plan with hedge ratio."""
    l1_sym = str(leg1_symbol).strip().upper()
    l2_sym = str(leg2_symbol).strip().upper()
    if not l1_sym or not l2_sym:
        raise ValueError("Both leg1_symbol and leg2_symbol are required")
    l1_side = str(leg1_side).upper()
    if l1_side not in {"BUY", "SELL"}:
        raise ValueError("leg1_side must be BUY or SELL")

    # If hedge ratio > 0, leg 2 takes the opposite side (e.g. Long Y + Short X)
    hr = float(hedge_ratio)
    if hr >= 0:
        l2_side = "SELL" if l1_side == "BUY" else "BUY"
    else:
        l2_side = l1_side

    l1_vol = float(leg1_volume)
    if l1_vol <= 0:
        raise ValueError("leg1_volume must be positive")
    l2_vol = round(max(0.01, l1_vol * abs(hr)), 2)

    return TwoLegPlannedEntry(
        leg1_symbol=l1_sym,
        leg1_side=l1_side,
        leg1_volume=l1_vol,
        leg1_entry=float(leg1_entry),
        leg1_sl=float(leg1_sl),
        leg1_tp=float(leg1_tp),
        leg2_symbol=l2_sym,
        leg2_side=l2_side,
        leg2_volume=l2_vol,
        leg2_entry=float(leg2_entry),
        leg2_sl=float(leg2_sl),
        leg2_tp=float(leg2_tp),
        hedge_ratio=hr,
        magic=int(magic),
        reason=str(reason).strip(),
    )
