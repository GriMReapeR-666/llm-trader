"""Broker-specification capture for deterministic, auditable preflight checks.

The values returned by MT5 vary by broker and symbol.  This module does not
invent trading thresholds; it snapshots the broker's published constraints,
fingerprints them, and makes a change visible to the operator/journal.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Dict, Iterable


SYMBOL_FIELDS = (
    "name", "trade_mode", "visible", "point", "digits", "spread",
    "trade_contract_size", "trade_tick_size", "trade_tick_value",
    "volume_min", "volume_max", "volume_step", "trade_stops_level",
    "trade_freeze_level", "filling_mode", "order_mode", "expiration_mode",
    "margin_initial", "margin_maintenance", "currency_base",
    "currency_profit", "currency_margin",
)
ACCOUNT_FIELDS = (
    "login", "server", "currency", "leverage", "balance", "equity",
    "margin", "margin_free", "margin_level", "margin_so_mode", "margin_so_call",
    "margin_so_so", "trade_allowed", "trade_expert",
)

REQUIRED_ACCOUNT_FIELDS = ("login", "server", "currency", "leverage", "trade_allowed", "trade_expert")
REQUIRED_SYMBOL_FIELDS = (
    "name", "trade_mode", "point", "digits", "trade_contract_size", "trade_tick_size",
    "trade_tick_value", "volume_min", "volume_max", "volume_step", "trade_stops_level",
    "trade_freeze_level", "filling_mode", "order_mode",
)


def _clean(value: Any) -> Any:
    """Convert MT5 namedtuple-ish values to stable JSON primitives."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def symbol_spec(info: Any, tick: Any = None) -> Dict[str, Any]:
    data = {field: _clean(getattr(info, field, None)) for field in SYMBOL_FIELDS}
    data["quote"] = {
        "bid": _clean(getattr(tick, "bid", None)),
        "ask": _clean(getattr(tick, "ask", None)),
        "time_msc": _clean(getattr(tick, "time_msc", None)),
    }
    return data


def account_spec(info: Any) -> Dict[str, Any]:
    return {field: _clean(getattr(info, field, None)) for field in ACCOUNT_FIELDS}


def fingerprint(value: Dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def has_contract_changed(previous_preflight: Dict[str, Any], current_preflight: Dict[str, Any]) -> bool:
    """Return True if stable account/symbol contract constraints have changed."""
    if not isinstance(previous_preflight, dict) or not isinstance(current_preflight, dict):
        return False
    prev_fp = previous_preflight.get("fingerprint") or ""
    curr_fp = current_preflight.get("fingerprint") or ""
    if not prev_fp or not curr_fp:
        return False
    return prev_fp != curr_fp


def _finite(value: Any, *, positive: bool = False, nonnegative: bool = False) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(number):
        return False
    if positive and number <= 0:
        return False
    if nonnegative and number < 0:
        return False
    return True


def validate_envelope(envelope: Dict[str, Any], symbols: Iterable[str] = ()) -> Dict[str, Any]:
    """Validate a captured broker envelope without inventing missing values."""
    reasons: list[str] = []
    if not isinstance(envelope, dict):
        return {"valid": False, "status": "INVALID", "reasons": ["preflight envelope is not an object"]}
    report = envelope.get("report")
    if report is None:
        report = {}
    if not isinstance(report, dict):
        reasons.append("preflight report is malformed")
        report = {}
    account = report.get("account")
    if account is None:
        account = {}
    if not isinstance(account, dict):
        reasons.append("preflight account report is malformed")
        account = {}
    for field in REQUIRED_ACCOUNT_FIELDS:
        if account.get(field) in (None, ""):
            reasons.append(f"account field missing: {field}")
    if not isinstance(account.get("trade_allowed"), bool):
        reasons.append("account trade_allowed flag is malformed")
    elif account.get("trade_allowed") is False:
        reasons.append("account trading is disabled")
    if not isinstance(account.get("trade_expert"), bool):
        reasons.append("account trade_expert flag is malformed")
    elif account.get("trade_expert") is False:
        reasons.append("expert trading is disabled")
    if not _finite(account.get("leverage"), positive=True):
        reasons.append("account leverage is malformed or not positive")
    captured_symbols = report.get("symbols")
    if captured_symbols is None:
        captured_symbols = {}
    if not isinstance(captured_symbols, dict):
        reasons.append("preflight symbol report is malformed")
        captured_symbols = {}
    try:
        requested_values = list(symbols or [])
    except TypeError:
        requested_values = []
        reasons.append("requested symbol list is malformed")
    requested = [str(symbol).upper() for symbol in requested_values if str(symbol).strip()]
    for symbol in requested:
        item = captured_symbols.get(symbol) or captured_symbols.get(symbol.upper())
        if not isinstance(item, dict):
            reasons.append(f"symbol snapshot missing: {symbol}")
            continue
        if item.get("error"):
            reasons.append(f"{symbol}: {item['error']}")
        for field in REQUIRED_SYMBOL_FIELDS:
            if item.get(field) in (None, ""):
                reasons.append(f"{symbol} field missing: {field}")
        try:
            trade_mode = int(item.get("trade_mode", 0))
            if trade_mode == 0:
                reasons.append(f"{symbol} is not tradable")
            if int(item.get("digits", -1)) < 0:
                reasons.append(f"{symbol} has invalid digits")
        except (TypeError, ValueError, OverflowError):
            reasons.append(f"{symbol} has malformed trade-mode/digits constraints")
        if not _finite(item.get("point"), positive=True) or not _finite(item.get("trade_contract_size"), positive=True):
            reasons.append(f"{symbol} has invalid point/contract size")
        if not _finite(item.get("trade_tick_size"), positive=True) or not _finite(item.get("trade_tick_value"), positive=True):
            reasons.append(f"{symbol} has invalid tick size/value")
        volume_values = [item.get("volume_min"), item.get("volume_max"), item.get("volume_step")]
        if not all(_finite(value, positive=True) for value in volume_values):
            reasons.append(f"{symbol} has invalid volume constraints")
        else:
            try:
                if float(item["volume_max"]) < float(item["volume_min"]):
                    reasons.append(f"{symbol} volume maximum is below minimum")
            except (TypeError, ValueError, OverflowError):
                reasons.append(f"{symbol} has malformed volume constraints")
        if not _finite(item.get("trade_stops_level"), nonnegative=True) or not _finite(item.get("trade_freeze_level"), nonnegative=True):
            reasons.append(f"{symbol} has invalid stop/freeze constraints")
        for field in ("filling_mode", "order_mode"):
            try:
                if int(item.get(field, -1)) < 0:
                    reasons.append(f"{symbol} has invalid {field}")
            except (TypeError, ValueError, OverflowError):
                reasons.append(f"{symbol} has malformed {field}")
    if not str(envelope.get("fingerprint", "")).strip():
        reasons.append("broker fingerprint missing")
    if envelope.get("changed"):
        reasons.append("broker contract changed since the prior capture; policy review required")
    return {"valid": not reasons, "status": "READY" if not reasons else "INSUFFICIENT_DATA",
            "reasons": reasons, "fingerprint": envelope.get("fingerprint"),
            "captured_at": report.get("captured_at"), "symbols": sorted(captured_symbols),
            "execution_authority": False}


def persist_preflight(store: Any, mt5_module: Any, symbols: Iterable[str]) -> Dict[str, Any]:
    """Persist exact account/symbol constraints and flag a broker-spec change."""
    symbols = list(symbols)
    captured_at = datetime.now(timezone.utc).isoformat()
    account_info = mt5_module.account_info()
    if account_info is None:
        raise RuntimeError("authenticated MT5 account specification unavailable")
    account = account_spec(account_info)
    report: Dict[str, Any] = {"captured_at": captured_at, "account": account, "symbols": {}}
    for symbol in symbols:
        info = mt5_module.symbol_info(symbol)
        if info is None:
            report["symbols"][symbol] = {"error": "symbol_info unavailable"}
            continue
        report["symbols"][symbol] = symbol_spec(info, mt5_module.symbol_info_tick(symbol))
    # A new capture timestamp, balance, current quote or floating spread is not
    # a broker contract change.  Only stable account identity/constraints and
    # published symbol specifications invalidate a previously reviewed policy.
    account_contract = {key: account.get(key) for key in (
        "login", "server", "currency", "leverage", "margin_so_call", "margin_so_so",
        "trade_allowed", "trade_expert",
    )}
    symbol_contracts = {
        symbol: {key: value for key, value in values.items() if key not in {"quote", "spread"}}
        for symbol, values in report["symbols"].items()
    }
    current_hash = fingerprint({"account": account_contract, "symbols": symbol_contracts})
    prior = store.get_state("broker_preflight", {}) or {}
    changed = bool(prior and prior.get("fingerprint") != current_hash)
    envelope = {"fingerprint": current_hash, "changed": changed, "report": report}
    envelope["validation"] = validate_envelope(envelope, symbols)
    store.set_state("broker_preflight", envelope)
    return envelope
