"""Versioned feature schemas for leakage-safe historical replay."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable


FEATURE_SCHEMA_VERSION = "feature_snapshot_v1"
# Required scalar names are intentionally explicit: changing this list requires
# a new schema version or a reviewed migration, never a silent reinterpretation.
REQUIRED_VALUES = (
    "close", "log_return_1", "realized_volatility_20", "atr_14", "ema_50",
    "ema_50_slope", "rsi_14", "macd_line", "macd_signal", "macd_histogram",
    "adx_14", "bollinger_mid", "bollinger_upper", "bollinger_lower",
    "vwap_20", "tick_volume", "tick_volume_ratio_20", "support_20",
    "resistance_20", "liquidity_sweep_high", "liquidity_sweep_low",
    "liquidity_sweep_confirmation_high", "liquidity_sweep_confirmation_low",
)


def schema_hash(version: str = FEATURE_SCHEMA_VERSION, values: Iterable[str] = REQUIRED_VALUES) -> str:
    payload = json.dumps({"version": version, "values": sorted(values)}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


SCHEMA_HASHES = {FEATURE_SCHEMA_VERSION: schema_hash()}


def values_hash(values: Dict[str, Any]) -> str:
    """Hash the exact point-in-time scalar snapshot for replay/audit."""
    return hashlib.sha256(json.dumps(values or {}, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _finite_scalar(value: Any) -> bool:
    if value is None:
        return True  # optional feature fields may be intentionally unavailable
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return False


def validate_snapshot(snapshot: Dict[str, Any], *, require_ready: bool = True) -> Dict[str, Any]:
    """Validate a point-in-time feature snapshot without calculating/mutating it.

    This is deliberately stricter than a presence check: malformed containers,
    non-finite scalars, invalid timestamps, and a completed bar after capture
    time are all rejected so historical training cannot acquire future data.
    """
    reasons: list[str] = []
    if not isinstance(snapshot, dict):
        return {"valid": False, "version": None, "schema_hash": None,
                "reasons": ["feature snapshot container is malformed"]}
    version = snapshot.get("version")
    if version not in SCHEMA_HASHES:
        reasons.append("unknown feature schema version")
    expected_hash = SCHEMA_HASHES.get(version)
    if expected_hash and snapshot.get("schema_hash") != expected_hash:
        reasons.append("feature schema hash mismatch")
    if not isinstance(snapshot.get("ready", False), bool):
        reasons.append("feature snapshot ready flag is malformed")
    if require_ready and snapshot.get("ready") is not True:
        reasons.append("feature snapshot is not ready")
    values = snapshot.get("values")
    if values is None:
        values = {}
    if not isinstance(values, dict):
        reasons.append("feature values container is malformed")
        values = {}
    missing = [name for name in REQUIRED_VALUES if name not in values]
    if require_ready and missing:
        reasons.append("missing required feature fields: " + ", ".join(missing))
    nonfinite = [name for name, value in values.items() if not _finite_scalar(value)]
    if nonfinite:
        reasons.append("feature values must be finite scalars: " + ", ".join(sorted(nonfinite)))
    required_nonfinite = [name for name in REQUIRED_VALUES if name in values and values[name] is None]
    if required_nonfinite:
        reasons.append("required feature values cannot be null: " + ", ".join(required_nonfinite))
    if snapshot.get("values_hash") is not None and snapshot.get("values_hash") != values_hash(values):
        reasons.append("point-in-time feature values hash missing or mismatched")
    if require_ready and not isinstance(snapshot.get("values_hash"), str):
        reasons.append("point-in-time feature values hash missing or malformed")
    completed = _parse_timestamp(snapshot.get("completed_bar_time"))
    captured = _parse_timestamp(snapshot.get("captured_at"))
    if require_ready and completed is None:
        reasons.append("completed bar timestamp is missing or malformed")
    if require_ready and captured is None:
        reasons.append("capture timestamp is missing or malformed")
    if completed is not None and captured is not None and completed > captured:
        reasons.append("completed bar timestamp is later than capture time")
    index = snapshot.get("completed_bar_index")
    if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index < 0):
        reasons.append("completed bar index is malformed")
    if require_ready and not isinstance(snapshot.get("source"), str):
        reasons.append("feature source identity is missing or malformed")
    lineage = snapshot.get("lineage")
    if lineage is not None:
        if not isinstance(lineage, dict):
            reasons.append("feature lineage container is malformed")
        else:
            if str(lineage.get("source", "")) != str(snapshot.get("source", "")):
                reasons.append("feature lineage source does not match snapshot source")
            if str(lineage.get("timeframe", "")) != str(snapshot.get("timeframe", "")):
                reasons.append("feature lineage timeframe does not match snapshot timeframe")
            lineage_captured = _parse_timestamp(lineage.get("captured_at"))
            lineage_completed = _parse_timestamp(lineage.get("completed_bar_time"))
            if lineage_captured is None or lineage_completed is None:
                reasons.append("feature lineage timestamps are missing or malformed")
            elif lineage_completed > lineage_captured:
                reasons.append("feature lineage completed bar is later than capture")
            lookback = lineage.get("lookback_bars")
            if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 0:
                reasons.append("feature lineage lookback count is malformed")
            cross_names = lineage.get("cross_market_names")
            if cross_names is not None and (not isinstance(cross_names, list) or
                                             any(not isinstance(name, str) or not name.strip() for name in cross_names)):
                reasons.append("feature lineage cross-market names are malformed")
            driver_buckets = lineage.get("driver_buckets")
            if driver_buckets is not None and (not isinstance(driver_buckets, list) or
                                                any(not isinstance(bucket, str) or not bucket.strip() for bucket in driver_buckets)):
                reasons.append("feature lineage driver buckets are malformed")
            bucket_hash = lineage.get("driver_bucket_hash")
            if bucket_hash is not None and (not isinstance(bucket_hash, str) or
                                             (bucket_hash and (len(bucket_hash) != 64 or any(char not in "0123456789abcdef" for char in bucket_hash.lower())))):
                reasons.append("feature lineage driver bucket hash is malformed")
    return {"valid": not reasons, "version": version, "schema_hash": snapshot.get("schema_hash"), "reasons": reasons}
