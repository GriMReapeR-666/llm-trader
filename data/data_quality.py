"""Fail-closed validation for market inputs before a proposal reaches risk code."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import threading
from typing import Any, Dict, List


@dataclass
class DataQualityResult:
    passed: bool
    reasons: List[str]


# Bar durations used to decide when a candle has settled. A candle stamped T
# covers [T, T + duration); it is only "closed" once that window has passed.
_TIMEFRAME_SECONDS: Dict[str, int] = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360,
    "M10": 600, "M12": 720, "M15": 900, "M20": 1200, "M30": 1800,
    "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400, "H6": 21600,
    "H8": 28800, "H12": 43200,
    "D1": 86400, "W1": 604800, "MN1": 2592000,
}


def timeframe_seconds(timeframe: Any) -> int:
    """Resolve a timeframe label to seconds; 0 when unknown."""
    if isinstance(timeframe, int) and timeframe > 0:
        return timeframe * 60
    label = str(timeframe or "").strip().upper()
    return _TIMEFRAME_SECONDS.get(label, 0)


class DataQualityGate:
    def __init__(self, max_return_zscore: float = 5.0, max_snapshot_age_seconds: float = 5.0,
                 max_dom_age_seconds: float = 3.0, max_clock_offset_seconds: float = 5.0,
                 candle_settle_bars: float = 0.0):
        values = {
            "max_return_zscore": max_return_zscore,
            "max_snapshot_age_seconds": max_snapshot_age_seconds,
            "max_dom_age_seconds": max_dom_age_seconds,
            "max_clock_offset_seconds": max_clock_offset_seconds,
            "candle_settle_bars": candle_settle_bars,
        }
        for name, value in values.items():
            try:
                parsed = float(value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"{name} must be finite and non-negative") from None
            if not math.isfinite(parsed) or parsed < 0:
                raise ValueError(f"{name} must be finite and non-negative")
            setattr(self, name, parsed)
        # In-process point-in-time revision guard.  The first observation of a
        # candle timestamp is authoritative for this run; a later payload with
        # the same timestamp but different OHLC/revision data is rejected.
        self._candle_fingerprints: Dict[str, str] = {}
        self._fingerprint_lock = threading.Lock()

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    def validate_snapshot(self, snapshot: Dict[str, Any], require_live_quote: bool = False, require_dom: bool = False) -> DataQualityResult:
        return self.validate(snapshot, require_live_quote=require_live_quote, require_dom=require_dom)

    def validate(self, snapshot: Dict[str, Any], *, require_live_quote: bool,
                 require_dom: bool = False) -> DataQualityResult:
        reasons: List[str] = []
        if not isinstance(snapshot, dict):
            return DataQualityResult(False, ["market snapshot is missing or malformed"])
        context = snapshot.get("context") or {}
        if not isinstance(context, dict):
            context = {}
            reasons.append("market snapshot context is malformed")
        capture_time = None
        if require_live_quote:
            if context.get("market_data_source") != "mt5":
                reasons.append("live market-data source is not authenticated MT5")
            identity = context.get("source_identity")
            if not isinstance(identity, dict):
                reasons.append("live market-data source identity is missing or malformed")
            else:
                provider = str(identity.get("provider", "")).strip().lower()
                server = str(identity.get("server", "")).strip()
                account_hash = str(identity.get("account_hash", "")).strip().lower()
                if provider not in {"mt5", "metatrader5"}:
                    reasons.append("live market-data source provider identity is invalid")
                if not server:
                    reasons.append("live market-data source server identity is missing")
                if len(account_hash) < 16 or any(ch not in "0123456789abcdef" for ch in account_hash):
                    reasons.append("live market-data source account identity hash is invalid")
                if any(key.lower() in {"password", "secret", "token"} for key in identity):
                    reasons.append("live market-data source identity contains sensitive fields")
            # DOM's own captured_at feeds the freshness check (1a-F14)
            dom_captured = None
            if isinstance(context.get("dom"), dict) and context["dom"].get("captured_at"):
                dom_captured = context["dom"]["captured_at"]
            elif isinstance(snapshot.get("dom"), dict) and snapshot["dom"].get("captured_at"):
                dom_captured = snapshot["dom"]["captured_at"]
            captured_at = dom_captured or context.get("captured_at")
            if not captured_at:
                reasons.append("market snapshot capture time missing")
            else:
                try:
                    capture_time = self._parse_time(captured_at)
                    age = (datetime.now(timezone.utc) - capture_time).total_seconds()
                    if age < -1 or age > self.max_snapshot_age_seconds:
                        reasons.append("market snapshot clock drift or stale capture")
                except (TypeError, ValueError, OSError):
                    reasons.append("malformed market snapshot capture time")
            clock_health = context.get("clock_health") or {}
            if not isinstance(clock_health, dict):
                reasons.append("broker/local clock-health observation malformed")
            elif not clock_health:
                reasons.append("broker/local clock-health observation missing")
            elif not clock_health.get("valid", False):
                reasons.append("broker tick is stale or local/broker clock skew exceeds threshold")
            else:
                try:
                    offset = float(clock_health.get("offset_seconds", 0) or 0)
                    if not math.isfinite(offset):
                        raise ValueError
                    if abs(offset) > self.max_clock_offset_seconds:
                        reasons.append("broker/local clock offset exceeds data-quality threshold")
                except (TypeError, ValueError, OverflowError):
                    reasons.append("broker/local clock offset is malformed")
            provider_time = context.get("provider_time")
            if not provider_time:
                reasons.append("provider-time observation is missing")
            else:
                try:
                    parsed_provider_time = self._parse_time(provider_time)
                    if capture_time is not None:
                        provider_delta = abs((capture_time - parsed_provider_time).total_seconds())
                        if provider_delta > self.max_clock_offset_seconds:
                            reasons.append("provider/local snapshot time differs beyond threshold")
                except (TypeError, ValueError, OSError, OverflowError):
                    reasons.append("malformed provider-time observation")
        bars = snapshot.get("ohlc") or []
        if not isinstance(bars, (list, tuple)):
            reasons.append("OHLC container is malformed")
            bars = []
        if len(bars) < 25:
            reasons.append("insufficient OHLC history for data-quality checks")
        closes: List[float] = []
        for index, bar in enumerate(bars):
            try:
                if not isinstance(bar, dict):
                    raise TypeError
                low, high = float(bar["low"]), float(bar["high"])
                opening, close = float(bar["open"]), float(bar["close"])
                if not all(math.isfinite(value) for value in (low, high, opening, close)):
                    raise ValueError
                if low > high or not low <= opening <= high or not low <= close <= high:
                    reasons.append(f"invalid OHLC consistency at bar {index}")
                    break
                if close <= 0:
                    reasons.append(f"non-positive close at bar {index}")
                    break
                closes.append(close)
            except (KeyError, TypeError, ValueError, OverflowError):
                reasons.append(f"malformed OHLC bar {index}")
                break
        timestamps = []
        for bar in bars:
            value = bar.get("timestamp", bar.get("time", bar.get("date"))) if isinstance(bar, dict) else None
            if value is None:
                continue
            try:
                if isinstance(value, datetime):
                    parsed = value
                else:
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                timestamps.append(parsed.timestamp())
            except (TypeError, ValueError, OSError, OverflowError):
                reasons.append("malformed candle timestamp")
                break
        # Detect in-run candle revisions independently of timestamp ordering.
        # This catches a broker/provider rewriting a closed candle while its
        # timestamp remains unchanged, which could otherwise contaminate
        # indicators, memory, and research labels.
        #
        # Only SETTLED candles are fingerprinted. A candle that is still forming
        # (or only just closed) legitimately changes on every poll, so comparing
        # it would flag normal market formation as provider corruption and
        # reject every proposal forever. The guard keeps its full strength for
        # any candle that has been closed long enough to be authoritative.
        source_key = f"{context.get('symbol', '')}|{context.get('timeframe', '')}"
        if source_key != "|":
            bar_seconds = timeframe_seconds(context.get("timeframe"))
            # Reference "now": the snapshot capture time when available so the
            # decision is point-in-time and reproducible, else wall clock.
            if capture_time is not None:
                now_epoch = capture_time.timestamp()
            else:
                now_epoch = datetime.now(timezone.utc).timestamp()
            # A candle stamped T covers [T, T + bar_seconds). It becomes
            # authoritative once that window has passed; candle_settle_bars adds
            # any extra whole bars of grace for providers that settle late.
            grace = bar_seconds * (1.0 + max(0.0, self.candle_settle_bars))
            for bar in bars:
                if not isinstance(bar, dict):
                    continue
                raw_time = bar.get("timestamp", bar.get("time", bar.get("date")))
                if raw_time is None:
                    continue
                try:
                    bar_time = self._parse_time(raw_time)
                except (TypeError, ValueError, OSError, OverflowError):
                    continue
                if bar_seconds > 0 and bar_time.timestamp() + grace > now_epoch:
                    continue  # still forming / not yet authoritative
                normalized_time = bar_time.isoformat()
                payload = {key: bar.get(key) for key in
                           ("timestamp", "time", "date", "open", "high", "low", "close", "volume", "tick_volume", "revision")
                           if key in bar}
                encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
                fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                revision_key = f"{source_key}|{normalized_time}"
                with self._fingerprint_lock:
                    previous = self._candle_fingerprints.get(revision_key)
                    if previous is not None and previous != fingerprint:
                        reasons.append(f"candle revision detected at {normalized_time}")
                    else:
                        self._candle_fingerprints[revision_key] = fingerprint
        # Bound memory use during continuous operation while retaining the most
        # recent point-in-time observations.
        with self._fingerprint_lock:
            if len(self._candle_fingerprints) > 10000:
                for key in list(self._candle_fingerprints)[:2000]:
                    self._candle_fingerprints.pop(key, None)
        if require_live_quote and bars and len(timestamps) != len(bars):
            reasons.append("live candle timestamp missing; point-in-time ordering cannot be verified")
        if require_live_quote and capture_time is not None and timestamps:
            if max(timestamps) > capture_time.timestamp() + 1.0:
                reasons.append("candle timestamp is later than snapshot capture; future-data risk")
        if len(timestamps) >= 3:
            intervals = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
            if any(interval <= 0 for interval in intervals):
                reasons.append("duplicate or out-of-order candle timestamp")
            else:
                ordered = sorted(intervals)
                median = ordered[len(ordered) // 2]
                if median > 0:
                    # Scheduled exchange session breaks (XAUUSD daily ~1h
                    # maintenance, FX weekend rollover) are GRID-ALIGNED: the
                    # gap spans an exact multiple of the bar interval. Feed
                    # corruption (dropped/irregular stamps) produces off-grid
                    # gaps. Reject only off-grid outliers so scheduled breaks
                    # no longer fail-closed every real snapshot.
                    suspicious = [iv for iv in intervals if iv > median * 3.0]
                    if suspicious:
                        def _seconds(iv) -> float:
                            return iv.total_seconds() if hasattr(iv, "total_seconds") else float(iv)
                        grid_ok = all(
                            abs((_seconds(iv) / _seconds(median)) - round(_seconds(iv) / _seconds(median))) < 0.05
                            for iv in suspicious
                        )
                        if not grid_ok:
                            reasons.append("abnormal candle timestamp gap")
        if len(closes) >= 22:
            returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
            baseline = returns[-21:-1]
            mean = sum(baseline) / len(baseline)
            variance = sum((value - mean) ** 2 for value in baseline) / len(baseline)
            std = math.sqrt(variance)
            if std > 0 and abs((returns[-1] - mean) / std) > self.max_return_zscore:
                reasons.append("latest return is a data outlier; require event/manual review")
        dom = snapshot.get("dom") or {}
        if not isinstance(dom, dict):
            reasons.append("DOM container is malformed")
            dom = {}
        bid, ask, spread = dom.get("best_bid"), dom.get("best_ask"), dom.get("spread")
        if require_dom:
            if not dom.get("captured_at"):
                reasons.append("DOM capture time missing")
            else:
                try:
                    dom_age = (datetime.now(timezone.utc) - self._parse_time(dom["captured_at"])).total_seconds()
                    if dom_age < -1 or dom_age > self.max_dom_age_seconds:
                        reasons.append("DOM is stale or clock-drifted")
                except (TypeError, ValueError, OSError):
                    reasons.append("malformed DOM capture time")
            try:
                dom_depth = float(dom.get("dom_depth", 0) or 0)
                valid_depth = math.isfinite(dom_depth) and dom_depth > 0 and dom_depth.is_integer()
            except (TypeError, ValueError, OverflowError):
                valid_depth = False
            if not valid_depth:
                reasons.append("DOM unavailable or empty for DOM-required setup")
        if require_live_quote:
            try:
                bid_value, ask_value = float(bid), float(ask)
                spread_value = None if spread is None else float(spread)
                if (not math.isfinite(bid_value) or not math.isfinite(ask_value)
                        or bid_value <= 0 or ask_value <= 0 or ask_value < bid_value):
                    reasons.append("invalid or absent live bid/ask")
                if spread_value is None or not math.isfinite(spread_value) or spread_value < 0:
                    reasons.append("invalid live spread")
            except (TypeError, ValueError, OverflowError):
                reasons.append("malformed live quote")
        return DataQualityResult(not reasons, reasons)
