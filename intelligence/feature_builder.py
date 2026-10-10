"""Point-in-time, versioned scalar feature snapshots for research and replay."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import fmean
from typing import Any, Dict, Iterable, List
from intelligence.feature_schema import FEATURE_SCHEMA_VERSION, schema_hash, values_hash as snapshot_values_hash
from intelligence.market_filters import is_london_ny_overlap, is_london_open, is_ny_open


FEATURE_SPEC_VERSION = FEATURE_SCHEMA_VERSION


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _value(series: Any, index: int, default: float = 0.0) -> float:
    return _number(series[index], default) if isinstance(series, list) and len(series) > abs(index) else default


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def build_snapshot(ohlc: Iterable[Dict[str, Any]], indicators: Dict[str, Any], *,
                   timeframe: str, source: str, captured_at: str = "",
                   event_times: Iterable[Dict[str, Any]] = (),
                   cross_market: Any = None,
                   driver_buckets: Iterable[str] = (),
                   microstructure_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build scalar features from completed bars only.

    The final row is treated as the potentially forming bar, so the snapshot
    uses index ``-2`` and labels its exact timestamp/feature specification.
    """
    bars = list(ohlc or [])
    if len(bars) < 22:
        return {"version": FEATURE_SPEC_VERSION, "schema_hash": schema_hash(), "ready": False, "reason": "need at least 22 bars", "timeframe": timeframe}
    index = len(bars) - 2
    completed = bars[:index + 1]
    closes = [_number(bar.get("close")) for bar in completed]
    highs = [_number(bar.get("high")) for bar in completed]
    lows = [_number(bar.get("low")) for bar in completed]
    opens = [_number(bar.get("open")) for bar in completed]
    volumes = [_number(bar.get("tick_volume", bar.get("real_volume", 0))) for bar in completed]
    current = closes[-1]
    returns = [math.log(closes[pos] / closes[pos - 1]) for pos in range(1, len(closes)) if closes[pos] > 0 and closes[pos - 1] > 0]
    recent_returns = returns[-20:]
    realized_vol = math.sqrt(fmean([(item - fmean(recent_returns)) ** 2 for item in recent_returns])) if len(recent_returns) >= 2 else 0.0
    window = closes[-20:]
    bb_mean = fmean(window)
    bb_std = math.sqrt(fmean([(item - bb_mean) ** 2 for item in window]))
    upper, lower = bb_mean + 2 * bb_std, bb_mean - 2 * bb_std
    high, low, opening = highs[-1], lows[-1], opens[-1]
    body = abs(current - opening)
    total_range = max(high - low, 1e-12)
    upper_wick = high - max(opening, current)
    lower_wick = min(opening, current) - low
    typical_values = [(highs[pos] + lows[pos] + closes[pos]) / 3 for pos in range(max(0, len(closes) - 20), len(closes))]
    recent_volumes = volumes[-len(typical_values):]
    volume_sum = sum(recent_volumes)
    vwap = sum(price * volume for price, volume in zip(typical_values, recent_volumes)) / volume_sum if volume_sum > 0 else current
    ema = _value(indicators.get("ema_50"), index, current)
    prior_ema = _value(indicators.get("ema_50"), index - 1, ema)
    sma_20 = fmean(closes[-20:]) if len(closes) >= 20 else current
    atr_value = _value(indicators.get("atr_14"), index)
    atr_series = []
    raw_atr_series = indicators.get("atr_14")
    if isinstance(raw_atr_series, list):
        for value in raw_atr_series[:index + 1]:
            parsed = _number(value, float("nan"))
            if math.isfinite(parsed) and parsed > 0:
                atr_series.append(parsed)
    current_atr = _number(atr_value, 0.0)
    # Use only completed-bar ATR observations. The percentile is a bounded
    # volatility-regime descriptor for sizing/research, never an order rule.
    atr_window = atr_series[-min(len(atr_series), 90):]
    atr_percentile_90d = (
        sum(value <= current_atr for value in atr_window) / len(atr_window)
        if atr_window and current_atr > 0 else None
    )
    macd = indicators.get("macd", {}) if isinstance(indicators.get("macd"), dict) else {}
    timestamp = completed[-1].get("timestamp", completed[-1].get("time", completed[-1].get("date", "")))
    structural_tolerance = max(atr_value * 0.15, current * 0.0002)
    prior_high, prior_low = max(highs[-21:-1]), min(lows[-21:-1])
    sweep_high = high > prior_high + structural_tolerance and current < prior_high
    sweep_low = low < prior_low - structural_tolerance and current > prior_low
    # A sweep is only an entry candidate after the *next completed candle*
    # confirms rejection.  The previous completed bar is the sweep candidate;
    # the current completed bar must close through that candidate's extreme in
    # the reversal direction and carry meaningful volume.
    candidate_high, candidate_low = highs[-2], lows[-2]
    candidate_open, candidate_close = opens[-2], closes[-2]
    candidate_prior_high = max(highs[-22:-2])
    candidate_prior_low = min(lows[-22:-2])
    candidate_tolerance = max(_value(indicators.get("atr_14"), index - 1) * 0.15, candidate_close * 0.0002)
    candidate_sweep_high = candidate_high > candidate_prior_high + candidate_tolerance and candidate_close < candidate_prior_high
    candidate_sweep_low = candidate_low < candidate_prior_low - candidate_tolerance and candidate_close > candidate_prior_low
    volume_confirmed = volumes[-1] >= fmean(volumes[-20:]) * 1.1 if volumes else False
    sweep_confirmation_high = candidate_sweep_high and current < candidate_low and current < opening and volume_confirmed
    sweep_confirmation_low = candidate_sweep_low and current > candidate_high and current > opening and volume_confirmed
    equal_highs = any(abs(highs[-1] - value) <= structural_tolerance for value in highs[-11:-1])
    equal_lows = any(abs(lows[-1] - value) <= structural_tolerance for value in lows[-11:-1])
    timestamps = []
    for bar in completed:
        raw = bar.get("timestamp", bar.get("time", bar.get("date")))
        try:
            parsed = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            timestamps.append(parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc))
        except (TypeError, ValueError):
            timestamps.append(None)
    current_time = timestamps[-1]
    capture_time = _timestamp(captured_at) or current_time
    # Cross-market context is optional, but never inferred from a bare scalar:
    # every observation must carry its own timestamp so replay cannot leak a
    # value published after the decision.  Accepted observations are reduced
    # to deterministic scalar summaries; the original payload remains in the
    # authoritative journal.
    cross_market_returns = []
    cross_market_names = []
    future_cross_market = 0
    max_cross_market_age = 0.0
    if isinstance(cross_market, dict):
        cross_items = list(cross_market.items())
    elif isinstance(cross_market, list):
        cross_items = [(str(index), item) for index, item in enumerate(cross_market)]
    else:
        cross_items = []
    for name, observation in cross_items:
        if not isinstance(observation, dict):
            continue
        raw_observation_time = next((observation.get(key) for key in
                                     ("timestamp", "time", "captured_at", "observed_at", "date")
                                     if observation.get(key) not in (None, "")), None)
        observation_time = _timestamp(raw_observation_time)
        if observation_time is None or capture_time is None:
            continue
        if observation_time > capture_time:
            future_cross_market += 1
            continue
        raw_return = next((observation.get(key) for key in
                           ("return", "return_fraction", "log_return", "change")
                           if observation.get(key) not in (None, "")), None)
        try:
            parsed_return = float(raw_return)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(parsed_return):
            continue
        cross_market_returns.append(parsed_return)
        cross_market_names.append(str(name))
        max_cross_market_age = max(max_cross_market_age,
                                   max(0.0, (capture_time - observation_time).total_seconds() / 60.0))
    normalized_buckets = sorted({str(bucket).strip() for bucket in (driver_buckets or ()) if str(bucket).strip()})
    bucket_hash = hashlib.sha256(json.dumps(normalized_buckets, separators=(",", ":")).encode("utf-8")).hexdigest() if normalized_buckets else ""
    upcoming_event_minutes = []
    for event in event_times or ():
        if not isinstance(event, dict):
            continue
        raw_event_time = next((event.get(key) for key in
                               ("time", "timestamp", "datetime", "scheduled_at", "date")
                               if event.get(key) not in (None, "")), None)
        event_time = _timestamp(raw_event_time)
        if event_time is None or capture_time is None or event_time < capture_time:
            continue
        try:
            importance = int(event.get("importance", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            importance = 0
        if importance < 3:
            continue
        upcoming_event_minutes.append(max(0.0, (event_time - capture_time).total_seconds() / 60.0))
    event_proximity_minutes = min(upcoming_event_minutes) if upcoming_event_minutes else None
    # Session flags are derived from the completed candle timestamp rather than
    # wall-clock time, so replay and live analysis receive identical liquidity
    # context.  They are advisory features; setup eligibility remains owned by
    # the deterministic setup registry.
    london_open_window = bool(current_time and is_london_open(current_time))
    new_york_open_window = bool(current_time and is_ny_open(current_time))
    london_new_york_overlap = bool(current_time and is_london_ny_overlap(current_time))
    asian_indexes = [pos for pos, value in enumerate(timestamps) if value and current_time and value.date() == current_time.date() and 0 <= value.hour < 7]
    asian_high = max((highs[pos] for pos in asian_indexes), default=0.0)
    asian_low = min((lows[pos] for pos in asian_indexes), default=0.0)
    asian_breakout_up = asian_high > 0 and current > asian_high + structural_tolerance
    asian_breakout_down = asian_low > 0 and current < asian_low - structural_tolerance
    magnitude = 10 ** max(-4, int(math.floor(math.log10(max(abs(current), 1e-12)))) - 1)
    nearest_round = round(current / magnitude) * magnitude

    micro_ctx = microstructure_context if isinstance(microstructure_context, dict) else {}
    hawkes_ratio = 0.0
    hawkes_cascade = False
    if micro_ctx.get("tick_timestamps"):
        try:
            from core.hawkes_process import calibrate_hawkes_process
            h_res = calibrate_hawkes_process(micro_ctx["tick_timestamps"])
            hawkes_ratio = float(h_res.branching_ratio)
            hawkes_cascade = bool(h_res.is_cascade)
        except Exception:
            pass

    vpin_tox = 0.0
    if micro_ctx.get("trade_volumes") and micro_ctx.get("price_changes"):
        try:
            from intelligence.macro_alpha_signals_and_microstructure import calculate_vpin_flow_toxicity
            v_res = calculate_vpin_flow_toxicity(micro_ctx["trade_volumes"], micro_ctx["price_changes"])
            vpin_tox = float(v_res.get("vpin_toxicity", 0.0))
        except Exception:
            pass

    ofi_score = 0.0
    if "order_flow_imbalance" in micro_ctx:
        try:
            from core.urgency_sor import calculate_alpha_decay_urgency_score
            ofi_score = float(calculate_alpha_decay_urgency_score(
                instantaneous_volatility=micro_ctx.get("instantaneous_volatility", realized_vol),
                order_flow_imbalance=micro_ctx.get("order_flow_imbalance", 0.0),
                order_book_depth=micro_ctx.get("order_book_depth", 100.0),
            ))
        except Exception:
            pass

    roll_spread = 0.0
    try:
        from core.market_structure_edges import estimate_spread_from_roll_model
        p_series = micro_ctx.get("trade_prices") if micro_ctx.get("trade_prices") else closes[-20:]
        r_res = estimate_spread_from_roll_model(p_series)
        roll_spread = float(r_res.implied_spread)
    except Exception:
        pass

    phantom_flag = bool(micro_ctx.get("phantom_liquidity", False))
    ou_half_life = float(micro_ctx["ou_half_life_bars"]) if micro_ctx.get("ou_half_life_bars") is not None else None

    values = {
        "hawkes_branching_ratio": hawkes_ratio,
        "hawkes_cascade_active": hawkes_cascade,
        "vpin_toxicity": vpin_tox,
        "ofi_urgency_score": ofi_score,
        "roll_implied_spread": roll_spread,
        "phantom_liquidity_flag": phantom_flag,
        "ou_half_life_bars": ou_half_life,
        "close": current, "log_return_1": returns[-1] if returns else 0.0,
        "realized_volatility_20": realized_vol,
        "atr_14": atr_value,
        "atr_percentile_90d": atr_percentile_90d,
        "ema_50": ema, "ema_50_slope": ema - prior_ema,
        "sma_20": sma_20,
        "ema_distance": current - ema, "rsi_14": _value(indicators.get("rsi_14"), index, 50.0),
        "macd_line": _value(macd.get("line"), index),
        "macd_signal": _value(macd.get("signal"), index),
        "macd_histogram": _value(macd.get("histogram"), index),
        "adx_14": _value(indicators.get("adx_14"), index),
        "bollinger_mid": bb_mean, "bollinger_upper": upper, "bollinger_lower": lower,
        "bollinger_percent_b": (current - lower) / max(upper - lower, 1e-12),
        "vwap_20": vwap, "vwap_distance": current - vwap,
        "candle_range": high - low, "candle_body": body,
        "upper_wick_ratio": upper_wick / total_range, "lower_wick_ratio": lower_wick / total_range,
        "tick_volume": volumes[-1], "tick_volume_ratio_20": volumes[-1] / max(fmean(volumes[-20:]), 1e-12),
        "support_20": min(lows[-20:]), "resistance_20": max(highs[-20:]),
        "liquidity_sweep_high": sweep_high, "liquidity_sweep_low": sweep_low,
        "liquidity_sweep_confirmation_high": sweep_confirmation_high,
        "liquidity_sweep_confirmation_low": sweep_confirmation_low,
        "confirmed_reversal_candle": sweep_confirmation_high or sweep_confirmation_low,
        "equal_highs": equal_highs, "equal_lows": equal_lows,
        "asian_range_high": asian_high or None, "asian_range_low": asian_low or None,
        "asian_range_breakout_up": asian_breakout_up, "asian_range_breakout_down": asian_breakout_down,
        "london_open_window": london_open_window,
        "new_york_open_window": new_york_open_window,
        "london_new_york_overlap": london_new_york_overlap,
        "high_impact_event_proximity_minutes": event_proximity_minutes,
        "high_impact_event_within_60m": bool(event_proximity_minutes is not None and event_proximity_minutes <= 60.0),
        "cross_market_available": bool(cross_market_returns),
        "cross_market_observation_count": len(cross_market_returns),
        "cross_market_mean_return": (fmean(cross_market_returns) if cross_market_returns else None),
        "cross_market_max_age_minutes": (max_cross_market_age if cross_market_returns else None),
        "cross_market_future_observations_rejected": future_cross_market,
        "driver_bucket_count": len(normalized_buckets),
        "nearest_round_number": nearest_round, "round_number_distance": abs(current - nearest_round),
        "wick_rejection": max(upper_wick, lower_wick) / total_range >= 0.45,
    }
    values_hash = snapshot_values_hash(values)
    return {
        "version": FEATURE_SPEC_VERSION,
        "schema_hash": schema_hash(),
        "ready": True,
        "source": source,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
        "timeframe": timeframe,
        "completed_bar_index": index,
        "completed_bar_time": str(timestamp),
        "lineage": {
            "source": source,
            "timeframe": timeframe,
            "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
            "completed_bar_time": str(timestamp),
            "lookback_bars": len(completed),
            "cross_market_names": cross_market_names,
            "driver_buckets": normalized_buckets,
            "driver_bucket_hash": bucket_hash,
        },
        "lookbacks": {"returns": 20, "bollinger": 20, "vwap": 20, "ema": 50, "indicator_warmup": len(bars)},
        "values": values,
        "values_hash": values_hash,
    }
