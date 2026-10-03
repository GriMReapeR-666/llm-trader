"""Reviewed, symbol-specific execution policy loader.

The broker publishes constraints, but spread/slippage/expiry tolerances also
need an operator calibration artifact.  This module refuses to invent one.
Paper mode can use its explicit simulator; live/demo mode must have an
approved policy in the authoritative journal before a plan is frozen.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Dict


REQUIRED = ("version", "symbol", "broker_fingerprint", "max_spread_price",
            "min_stop_distance_price", "entry_tolerance_min_price",
            "entry_tolerance_atr_fraction", "expected_slippage_price",
            "commission_per_volume", "expected_swap_money", "expires_seconds",
            "max_tick_age_ms", "status", "reviewed_at")


def _number(policy: Dict[str, Any], key: str, *, allow_zero: bool = False) -> float:
    try:
        value = float(policy[key])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"execution policy missing numeric field {key}") from exc
    if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"execution policy field {key} must be {'non-negative' if allow_zero else 'positive'}")
    return value


def _positive_integer(policy: Dict[str, Any], key: str) -> int:
    try:
        value = float(policy[key])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"execution policy field {key} must be a positive integer") from exc
    if not math.isfinite(value) or value <= 0 or value != int(value):
        raise ValueError(f"execution policy field {key} must be a positive integer")
    return int(value)


def validate(policy: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    if str(policy.get("status", "")).upper() != "APPROVED":
        raise ValueError(f"no approved execution policy for {symbol}")
    if str(policy.get("symbol", "")).upper() != str(symbol).upper():
        raise ValueError("execution policy symbol mismatch")
    for field in REQUIRED:
        if field not in policy or policy[field] in (None, ""):
            raise ValueError(f"execution policy missing {field}")
    for field in ("version", "symbol", "broker_fingerprint", "status", "reviewed_at"):
        if not str(policy.get(field, "")).strip():
            raise ValueError(f"execution policy missing {field}")
    for field in ("max_spread_price", "min_stop_distance_price", "entry_tolerance_min_price",
                  "entry_tolerance_atr_fraction", "expected_slippage_price", "commission_per_volume",
                  "expected_swap_money"):
        _number(policy, field, allow_zero=field in {"min_stop_distance_price", "entry_tolerance_min_price",
                                                     "expected_slippage_price", "commission_per_volume", "expected_swap_money"})
    _positive_integer(policy, "expires_seconds")
    _positive_integer(policy, "max_tick_age_ms")
    try:
        reviewed_at = datetime.fromisoformat(str(policy["reviewed_at"]).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("execution policy reviewed_at must be an ISO timestamp") from exc
    if reviewed_at.tzinfo is None:
        raise ValueError("execution policy reviewed_at must include a timezone")
    if reviewed_at.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("execution policy reviewed_at cannot be materially in the future")
    return dict(policy)


def _require_symbol_snapshot(preflight: Dict[str, Any], symbol: str) -> None:
    """Require symbol-specific contract evidence, not only a global hash."""
    report = preflight.get("report")
    validation = preflight.get("validation")
    captured = report.get("symbols") if isinstance(report, dict) else None
    if not isinstance(captured, dict) or not isinstance(captured.get(str(symbol).upper()), dict):
        raise ValueError(f"broker preflight is missing the {symbol} symbol contract")
    validated_symbols = validation.get("symbols") if isinstance(validation, dict) else None
    if not isinstance(validated_symbols, list) or str(symbol).upper() not in {str(item).upper() for item in validated_symbols}:
        raise ValueError(f"broker preflight validation does not cover {symbol}")


def load(store: Any, symbol: str) -> Dict[str, Any]:
    policies = store.get_state("execution_policies", {}) or {}
    if not isinstance(policies, dict):
        raise ValueError("execution policy registry is malformed")
    policy = policies.get(str(symbol).upper()) or policies.get(str(symbol))
    if not isinstance(policy, dict):
        raise ValueError(f"no approved execution policy for {symbol}")
    validated = validate(policy, symbol)
    raw_preflight = store.get_state("broker_preflight", {})
    preflight = raw_preflight if isinstance(raw_preflight, dict) else {}
    if not isinstance(raw_preflight, dict):
        raise ValueError("broker preflight is malformed; cannot bind execution policy")
    current_fingerprint = str(preflight.get("fingerprint", ""))
    if not current_fingerprint:
        raise ValueError("broker preflight is unavailable; cannot bind execution policy")
    if str(validated.get("broker_fingerprint", "")) != current_fingerprint:
        raise ValueError(f"execution policy broker fingerprint mismatch for {symbol}")
    if preflight.get("changed"):
        raise ValueError("broker contract changed; execution policy requires fresh review")
    validation = preflight.get("validation")
    if not isinstance(validation, dict) or validation.get("valid") is not True:
        raise ValueError("broker preflight is incomplete; execution policy review is invalid")
    _require_symbol_snapshot(preflight, symbol)
    return validated


def candidate_template(symbol: str, broker_fingerprint: str) -> Dict[str, Any]:
    """Create a non-approved template for an operator calibration workflow."""
    return {
        "version": "execution-policy-v1", "symbol": symbol.upper(),
        "broker_fingerprint": broker_fingerprint, "max_spread_price": None,
        "min_stop_distance_price": None, "entry_tolerance_min_price": None,
        "entry_tolerance_atr_fraction": None, "expected_slippage_price": None,
        "commission_per_volume": None, "expected_swap_money": None,
        "expires_seconds": 900, "max_tick_age_ms": 5000,
        "status": "CANDIDATE", "reviewed_at": None,
        "execution_authority": False,
    }


def _current_preflight(store: Any, symbol: str = "") -> tuple[Dict[str, Any], str]:
    """Return the current authenticated broker contract or fail closed."""
    preflight = store.get_state("broker_preflight", {}) or {}
    if not isinstance(preflight, dict):
        raise ValueError("broker preflight is malformed; cannot create execution policy")
    fingerprint = str(preflight.get("fingerprint", ""))
    validation = preflight.get("validation") or {}
    if not fingerprint:
        raise ValueError("broker preflight is unavailable; cannot create execution policy")
    if not isinstance(validation, dict) or not validation.get("valid", False):
        raise ValueError("broker preflight is incomplete; policy review is not permitted")
    if preflight.get("changed"):
        raise ValueError("broker contract changed; refresh preflight before policy review")
    if symbol:
        _require_symbol_snapshot(preflight, symbol)
    return preflight, fingerprint


def candidate_for_store(store: Any, symbol: str) -> Dict[str, Any]:
    """Create a broker-bound, non-approved policy candidate.

    This intentionally does not persist or grant execution authority.  An
    operator must fill in calibrated costs/tolerances and explicitly approve
    the artifact after reviewing broker evidence.
    """
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("policy candidate requires a symbol")
    preflight, fingerprint = _current_preflight(store, symbol)
    result = candidate_template(symbol, fingerprint)
    result["preflight_captured_at"] = (preflight.get("report") or {}).get("captured_at")
    result["preflight_validation"] = {
        "valid": True,
        "status": (preflight.get("validation") or {}).get("status", "READY"),
    }
    result["execution_authority"] = False
    return result


def approve_for_store(store: Any, symbol: str, policy: Dict[str, Any], reviewer: str) -> Dict[str, Any]:
    """Approve and persist a reviewed policy bound to the current preflight."""
    reviewer = str(reviewer or "").strip()
    if not reviewer:
        raise ValueError("policy approval requires a reviewer")
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("policy approval requires a symbol")
    preflight, fingerprint = _current_preflight(store, symbol)
    if not isinstance(policy, dict):
        raise ValueError("policy JSON must be an object")
    approved = dict(policy)
    approved["symbol"] = symbol
    supplied_fingerprint = str(approved.get("broker_fingerprint", ""))
    if supplied_fingerprint != fingerprint:
        raise ValueError("policy broker fingerprint must match the current preflight")
    approved["status"] = "APPROVED"
    approved["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    approved["reviewed_by"] = reviewer
    approved["preflight_captured_at"] = (preflight.get("report") or {}).get("captured_at")
    approved["preflight_validation"] = {
        "valid": True,
        "status": (preflight.get("validation") or {}).get("status", "READY"),
    }
    approved["execution_authority"] = False
    validated = validate(approved, symbol)
    policies = store.get_state("execution_policies", {}) or {}
    if not isinstance(policies, dict):
        raise ValueError("execution policy registry is malformed")
    policies[symbol] = validated
    store.set_state("execution_policies", policies)
    return validated
