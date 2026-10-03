"""Read-only MT5 authorization diagnostics.

This module reports credential/configuration *shape* and probes the terminal
without printing passwords or placing orders.  It distinguishes missing local
configuration from a terminal/broker authorization failure; it does not try to
repair credentials automatically.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


REQUIRED_ACCOUNT_FIELDS = ("login", "server", "type")
ENV_FIELDS = ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")


def configuration_shape(path: str = "accounts.json") -> Dict[str, Any]:
    result: Dict[str, Any] = {"path": str(Path(path)), "file_present": Path(path).exists(),
                              "accounts": [], "environment": {}}
    for key in ENV_FIELDS:
        result["environment"][key] = "PRESENT" if str(os.environ.get(key, "")).strip() else "MISSING"
    if not result["file_present"]:
        result["status"] = "MISSING_ACCOUNT_FILE"
        return result
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        result["status"] = "MALFORMED_ACCOUNT_FILE"
        return result
    accounts = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(accounts, dict):
        result["status"] = "MALFORMED_ACCOUNT_FILE"
        return result
    for name, item in accounts.items():
        fields = sorted(item.keys()) if isinstance(item, dict) else []
        has_secret_source = isinstance(item, dict) and ("password" in fields or "password_env" in fields)
        result["accounts"].append({"name": str(name), "fields": fields,
                                    "type": item.get("type") if isinstance(item, dict) else None,
                                    "required_fields_present": bool(has_secret_source and all(field in fields for field in REQUIRED_ACCOUNT_FIELDS))})
    result["default"] = payload.get("default")
    result["status"] = "READY_SHAPE" if result["accounts"] and all(item["required_fields_present"] for item in result["accounts"]) else "INCOMPLETE_ACCOUNT_FIELDS"
    return result


def classify_error(error: Any) -> str:
    try:
        code = int(error[0])
    except (TypeError, ValueError, IndexError):
        return "UNKNOWN"
    return {
        -6: "AUTHORIZATION_FAILED_CHECK_LOGIN_PASSWORD_SERVER_OR_TERMINAL_SESSION",
        -10004: "NO_CONNECTION",
        -10005: "TIMEOUT",
    }.get(code, "MT5_ERROR")


def probe_terminal(mt5_module: Any, paths: Iterable[Optional[str]], *, timeout_ms: int = 5000) -> Dict[str, Any]:
    """Probe candidate terminals without allowing an unavailable terminal to stall.

    MetaTrader5's native ``initialize`` accepts a millisecond timeout.  A
    bounded value is part of the evidence so an operator can distinguish a
    quick fail-closed probe from an unbounded connection attempt.  Small fake
    modules used by tests (and older wrappers) may not accept ``timeout``;
    those are retried once without the keyword, never with an unbounded real
    MT5 call.
    """
    bounded_timeout = max(1000, min(int(timeout_ms), 30000))
    attempts = []
    for path in paths:
        try:
            try:
                ok = bool(mt5_module.initialize(path=path, timeout=bounded_timeout) if path
                          else mt5_module.initialize(timeout=bounded_timeout))
            except TypeError:
                ok = bool(mt5_module.initialize(path=path) if path else mt5_module.initialize())
            error = tuple(mt5_module.last_error())
            attempt: Dict[str, Any] = {"path": path or "default", "initialized": ok,
                                       "timeout_ms": bounded_timeout,
                                       "error_code": error[0] if error else None,
                                       "error_class": classify_error(error)}
            if ok:
                ti = mt5_module.terminal_info()
                ai = mt5_module.account_info()
                attempt["terminal"] = {"name": getattr(ti, "name", None),
                                        "company": getattr(ti, "company", None),
                                        "trade_allowed": getattr(ti, "trade_allowed", None)}
                # Keep account scope identity inside the runtime journal, but
                # never expose the login identifier in diagnostic output.
                attempt["account"] = {"login": "REDACTED" if ai and getattr(ai, "login", None) is not None else None,
                                       "server": getattr(ai, "server", None),
                                       "currency": getattr(ai, "currency", None),
                                       "leverage": getattr(ai, "leverage", None)} if ai else None
                attempts.append(attempt)
                try: mt5_module.shutdown()
                except Exception: pass
                return {"status": "AUTHORIZED", "attempts": attempts, "execution_authority": False}
            attempts.append(attempt)
        except Exception as exc:
            attempts.append({"path": path or "default", "initialized": False,
                             "timeout_ms": bounded_timeout,
                             "error_class": "EXCEPTION", "error_type": type(exc).__name__})
        finally:
            try: mt5_module.shutdown()
            except Exception: pass
    status = "AUTHORIZATION_FAILED" if any(item.get("error_class") == "AUTHORIZATION_FAILED_CHECK_LOGIN_PASSWORD_SERVER_OR_TERMINAL_SESSION" for item in attempts) else "TERMINAL_UNAVAILABLE"
    return {"status": status, "attempts": attempts, "execution_authority": False}


def report(*, accounts_path: str = "accounts.json", mt5_module: Any = None,
           paths: Iterable[Optional[str]] = (None, r"C:\Program Files\MetaTrader 5\terminal64.exe"),
           timeout_ms: int = 5000) -> Dict[str, Any]:
    config = configuration_shape(accounts_path)
    output: Dict[str, Any] = {"configuration": config, "execution_authority": False}
    if mt5_module is None:
        try:
            import MetaTrader5 as mt5_module  # type: ignore
        except ImportError:
            output["probe"] = {"status": "MT5_MODULE_MISSING", "execution_authority": False}
            return output
    output["probe"] = probe_terminal(mt5_module, paths, timeout_ms=timeout_ms)
    output["status"] = output["probe"]["status"]
    output["next_actions"] = [
        "Verify the terminal is logged into the intended broker server and account.",
        "Rotate any previously exposed password/API values; do not paste them into logs.",
        "Rerun diagnose/auth-status, then broker-preflight after authorization succeeds.",
    ]
    return output
