"""Broker server time and trading day utilities (PR 4.2).

Corrects MT5 broker server time offsets (e.g. UTC+2 / UTC+3) across positions,
deals, ticks, and daily/weekly risk limits.
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("broker_time")

_SERVER_OFFSET_CACHE: Dict[str, Any] = {
    "offset": 0,
    "expires": 0.0,
}
_SERVER_OFFSET_TTL = 86400.0  # 24 hours


def set_broker_utc_offset_seconds(offset: int, ttl: float = _SERVER_OFFSET_TTL) -> None:
    """Manually configure or override the broker offset (seconds ahead of UTC)."""
    _SERVER_OFFSET_CACHE["offset"] = int(offset)
    _SERVER_OFFSET_CACHE["expires"] = time.time() + ttl


def reset_broker_time_cache() -> None:
    """Clear the cached broker offset."""
    _SERVER_OFFSET_CACHE["offset"] = 0
    _SERVER_OFFSET_CACHE["expires"] = 0.0


def get_broker_utc_offset_seconds(mt5_inst: Any = None) -> int:
    """Return the cached or estimated broker offset (seconds ahead of UTC)."""
    return _server_utc_offset_seconds(mt5_inst)


def _server_utc_offset_seconds(
    mt5_inst: Any = None,
    probe_symbols: Tuple[str, ...] = ("EURUSD", "GBPUSD", "XAUUSD"),
) -> int:
    """Estimate the broker server-timezone offset (server ahead of UTC), seconds.

    MT5 bar and deal timestamps are epoch seconds expressed in the BROKER SERVER's
    timezone, not UTC (e.g. MetaQuotes demo servers run UTC+2 or UTC+3).
    A fresh tick carries the server's *now* in server epoch; compare against true UTC now.
    Broker clocks are NTP-synced, so residual after snapping to a 30-minute grid must
    be small (<5 min). Returns 0 when no trustworthy probe exists.
    """
    now = time.time()
    if now < _SERVER_OFFSET_CACHE["expires"]:
        return _SERVER_OFFSET_CACHE["offset"]
    if mt5_inst is None:
        try:
            from core.order_execution import _get_mt5
            mt5_inst = _get_mt5()
        except Exception:
            mt5_inst = None
    if mt5_inst is None:
        return _SERVER_OFFSET_CACHE["offset"]

    try:
        candidates = []  # (offset_seconds, residual_ms)
        for name in probe_symbols:
            t = mt5_inst.symbol_info_tick(name)
            if t is None:
                continue
            server_ms = float(getattr(t, "time_msc", 0) or 0)
            if server_ms <= 0:
                continue
            residual_ms = server_ms - now * 1000.0
            steps = int(round(residual_ms / 1_800_000.0))  # snap to 30-min grid
            if steps < -28 or steps > 28:                  # clamp to ±14h
                continue
            residual = abs(residual_ms - steps * 1_800_000.0)
            if residual > 300_000:  # frozen/stale tick
                continue
            candidates.append((steps * 1800, residual))

        if not candidates:
            return _SERVER_OFFSET_CACHE["offset"]

        from collections import Counter
        offset, votes = Counter(off for off, _ in candidates).most_common(1)[0]
        if votes < 2:
            if candidates[0][1] > 120_000:
                return _SERVER_OFFSET_CACHE["offset"]

        logger.info(
            "MT5 server-timezone offset detected: %+ds (UTC%+g)",
            offset,
            offset / 3600.0,
        )
        _SERVER_OFFSET_CACHE["offset"] = int(offset)
        _SERVER_OFFSET_CACHE["expires"] = now + _SERVER_OFFSET_TTL
        return int(offset)
    except Exception as exc:
        logger.debug("Server UTC offset detection failed: %s", exc)
        return _SERVER_OFFSET_CACHE["offset"]


def broker_epoch_to_utc(epoch: float, mt5_inst: Any = None) -> datetime:
    """Convert a broker server epoch (seconds) to a UTC datetime."""
    try:
        epoch_val = float(epoch)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc)
    offset = get_broker_utc_offset_seconds(mt5_inst)
    utc_epoch = epoch_val - offset
    return datetime.fromtimestamp(utc_epoch, tz=timezone.utc)


def utc_to_broker_time(dt_utc: Optional[datetime] = None, mt5_inst: Any = None) -> datetime:
    """Convert a UTC datetime to broker server datetime."""
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)
    elif dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    else:
        dt_utc = dt_utc.astimezone(timezone.utc)
    offset = get_broker_utc_offset_seconds(mt5_inst)
    return dt_utc + timedelta(seconds=offset)


def broker_trading_day(now_utc: Optional[datetime] = None, mt5_inst: Any = None) -> str:
    """ISO date string (YYYY-MM-DD) of the current broker trading day (server midnight boundary)."""
    broker_dt = utc_to_broker_time(now_utc, mt5_inst=mt5_inst)
    return broker_dt.date().isoformat()


def broker_trading_week(now_utc: Optional[datetime] = None, mt5_inst: Any = None) -> str:
    """ISO week string (YYYY-Www) of the current broker trading week."""
    broker_dt = utc_to_broker_time(now_utc, mt5_inst=mt5_inst)
    iso = broker_dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"
