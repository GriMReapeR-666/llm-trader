"""Deterministic, broker-cost-aware offline replay harness.

This is a research simulator, not an order router.  It refuses to run without
explicit costs/specifications and models conservative stop-first intrabar
ordering when both SL and TP are touched in the same bar.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class ReplaySpec:
    point: float
    contract_size: float
    commission_per_volume: float
    swap_per_volume_day: float
    expected_slippage_price: float
    spread_price: float
    latency_bars: int = 0
    event_spread_multiplier: float = 2.0
    closed_weekends: bool = True
    default_bar_seconds: float = 900.0


@dataclass(frozen=True)
class ReplayIntent:
    ticket: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    volume: float
    submit_index: int = 0
    expiry_index: Optional[int] = None


def _time(value: Any) -> Optional[datetime]:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _validate_spec(spec: ReplaySpec) -> None:
    if not isinstance(spec, ReplaySpec):
        raise ValueError("replay specification is malformed")
    numeric = (spec.point, spec.contract_size, spec.commission_per_volume,
               spec.swap_per_volume_day, spec.expected_slippage_price,
               spec.spread_price, spec.event_spread_multiplier)
    if not _all_finite(numeric):
        raise ValueError("replay specification contains non-finite values")
    if spec.point <= 0 or spec.contract_size <= 0 or spec.spread_price < 0 or spec.expected_slippage_price < 0:
        raise ValueError("replay symbol/cost specification is invalid")
    if spec.commission_per_volume < 0 or spec.swap_per_volume_day < 0:
        raise ValueError("replay costs/latency must be non-negative")
    if not isinstance(spec.latency_bars, int) or isinstance(spec.latency_bars, bool) or spec.latency_bars < 0:
        raise ValueError("replay latency must be a non-negative integer")
    if spec.event_spread_multiplier < 1:
        raise ValueError("event spread multiplier must be >= 1")
    if not isinstance(spec.closed_weekends, bool):
        raise ValueError("closed_weekends must be boolean")
    if not math.isfinite(spec.default_bar_seconds) or spec.default_bar_seconds <= 0:
        raise ValueError("default bar duration must be positive and finite")


def _ohlc(bar: Dict[str, Any]) -> tuple[float, float, float]:
    try:
        low = float(bar["low"])
        high = float(bar["high"])
        close = float(bar["close"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("replay bar requires numeric low/high/close") from None
    if not all(math.isfinite(value) for value in (low, high, close)):
        raise ValueError("replay bar contains non-finite OHLC")
    if low > high or close < low or close > high:
        raise ValueError("replay bar has inconsistent OHLC bounds")
    return low, high, close


def _holding_days(bars: List[Dict[str, Any]], start: int, end: int, default_bar_seconds: float) -> float:
    start_time = _time(bars[start].get("timestamp", bars[start].get("time", "")))
    end_time = _time(bars[end].get("timestamp", bars[end].get("time", "")))
    if start_time is not None and end_time is not None:
        elapsed = max(0.0, (end_time - start_time).total_seconds())
    else:
        elapsed = max(0, end - start) * default_bar_seconds
    return elapsed / 86_400.0


def _all_finite(values: Iterable[Any]) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError, OverflowError):
        return False


def _valid_index(value: Any, *, allow_none: bool = False) -> bool:
    if value is None and allow_none:
        return True
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def replay(intents: Iterable[ReplayIntent], bars: List[Dict[str, Any]], spec: ReplaySpec,
           *, event_indices: Optional[Iterable[int]] = None) -> Dict[str, Any]:
    """Replay limit intents against completed OHLC bars with conservative costs."""
    _validate_spec(spec)
    if not bars:
        return {"status": "INSUFFICIENT_DATA", "trades": [], "spec": asdict(spec)}
    # Fail closed before any intent can be scored.  A malformed bar must not
    # become a silently profitable/losing synthetic observation.
    for bar in bars:
        _ohlc(bar)
    event_indices = set(event_indices or [])
    output: List[Dict[str, Any]] = []
    for intent in intents:
        if not isinstance(intent, ReplayIntent):
            output.append({"ticket": None, "status": "REJECTED", "reason": "invalid intent"})
            continue
        side = str(intent.side).upper()
        levels = (intent.entry, intent.stop_loss, intent.take_profit, intent.volume)
        if (side not in {"BUY", "SELL"}
                or not _all_finite(levels)
                or intent.volume <= 0 or intent.entry <= 0
                or not _valid_index(intent.submit_index)
                or not _valid_index(intent.expiry_index, allow_none=True)):
            output.append({"ticket": intent.ticket, "status": "REJECTED", "reason": "invalid intent"})
            continue
        geometry_valid = (
            intent.stop_loss < intent.entry < intent.take_profit
            if side == "BUY" else
            intent.stop_loss > intent.entry > intent.take_profit
        )
        if not geometry_valid:
            output.append({"ticket": intent.ticket, "status": "REJECTED", "reason": "invalid stop/target geometry"})
            continue
        fill_index = intent.submit_index + spec.latency_bars
        filled = None
        for index in range(max(0, fill_index), len(bars)):
            if intent.expiry_index is not None and index > intent.expiry_index:
                break
            stamp = _time(bars[index].get("timestamp", bars[index].get("time", "")))
            if spec.closed_weekends and stamp is not None and stamp.weekday() >= 5:
                continue
            spread = spec.spread_price * (spec.event_spread_multiplier if index in event_indices else 1.0)
            low, high, _ = _ohlc(bars[index])
            if low <= intent.entry <= high:
                direction = 1 if side == "BUY" else -1
                fill_price = intent.entry + direction * (spread / 2 + spec.expected_slippage_price)
                filled = (index, fill_price, spread)
                break
        if filled is None:
            output.append({"ticket": intent.ticket, "status": "MISSED", "reason": "unfilled or expired"})
            continue
        entry_index, fill_price, spread = filled
        exit_index, exit_price, exit_reason = None, None, None
        direction = 1 if side == "BUY" else -1
        # Excursions are calculated from the filled price using only bars that
        # occurred after entry.  They are research annotations, not execution
        # permissions.  The same completed-bar path is used for both the
        # exit decision and MFE/MAE so the replay cannot accidentally inspect
        # future data.
        max_favorable_price = 0.0
        max_adverse_price = 0.0
        for index in range(entry_index, len(bars)):
            low, high, _ = _ohlc(bars[index])
            if side == "BUY":
                favorable = max(0.0, high - fill_price)
                adverse = max(0.0, fill_price - low)
            else:
                favorable = max(0.0, fill_price - low)
                adverse = max(0.0, high - fill_price)
            max_favorable_price = max(max_favorable_price, favorable)
            max_adverse_price = max(max_adverse_price, adverse)
            stop_hit = (low <= intent.stop_loss) if side == "BUY" else (high >= intent.stop_loss)
            target_hit = (high >= intent.take_profit) if side == "BUY" else (low <= intent.take_profit)
            if stop_hit:  # conservative: stop wins ties within one OHLC bar
                exit_index, exit_price, exit_reason = index, intent.stop_loss - direction * spec.expected_slippage_price, "STOP"
                break
            if target_hit:
                exit_index, exit_price, exit_reason = index, intent.take_profit - direction * spec.expected_slippage_price, "TARGET"
                break
        if exit_index is None:
            last = bars[-1]
            _, _, last_close = _ohlc(last)
            exit_index, exit_price, exit_reason = len(bars) - 1, last_close, "END_OF_DATA"
        gross = (exit_price - fill_price) * direction * intent.volume * spec.contract_size
        commission = spec.commission_per_volume * intent.volume
        holding_days = _holding_days(bars, entry_index, exit_index, spec.default_bar_seconds)
        swap = spec.swap_per_volume_day * intent.volume * holding_days
        net = gross - commission - swap
        risk_distance = abs(fill_price - intent.stop_loss) * intent.volume * spec.contract_size
        mfe_currency = max_favorable_price * intent.volume * spec.contract_size
        mae_currency = max_adverse_price * intent.volume * spec.contract_size
        output.append({"ticket": intent.ticket, "status": "CLOSED", "entry_index": entry_index,
                       "exit_index": exit_index, "entry_price": fill_price, "exit_price": exit_price,
                       "exit_reason": exit_reason, "gross_pnl": gross, "commission": commission,
                       "swap": swap, "net_pnl": net, "risk_amount": risk_distance,
                       "r_multiple": net / risk_distance if risk_distance > 0 else None,
                       "mfe_price": max_favorable_price, "mae_price": max_adverse_price,
                       "mfe_currency": mfe_currency, "mae_currency": mae_currency,
                       "mfe_r": mfe_currency / risk_distance if risk_distance > 0 else None,
                       "mae_r": mae_currency / risk_distance if risk_distance > 0 else None,
                       "spread_price": spread, "slippage_price": spec.expected_slippage_price})
    closed = [row for row in output if row.get("status") == "CLOSED"]
    return {"status": "READY_FOR_REVIEW", "spec": asdict(spec), "trades": output,
            "closed_trades": len(closed), "net_pnl": sum(float(row["net_pnl"]) for row in closed),
            "net_r": sum(float(row["r_multiple"]) for row in closed if row.get("r_multiple") is not None),
            "execution_authority": False,
            "limitation": "Replay results are not evidence of live edge until matched against broker fills and demo reconciliation."}
