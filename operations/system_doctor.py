"""MT5 system doctor — checks prerequisites for live trading readiness.

This module is diagnostic only.  It never grants order permission.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Tuple

from mt5_auth_diagnostics import report as auth_report
from broker_preflight import validate_envelope, persist_preflight
from sample_gate import report as sample_report
from live_rollout_gate import status as live_rollout_status
from security_audit import audit as security_audit
from research_influence_gate import status as research_influence_status
from readiness_report import build as build_readiness


def _check_mt5_installed() -> Tuple[bool, str]:
    try:
        import MetaTrader5  # noqa: F401
        return True, "MetaTrader5 Python package installed"
    except ImportError:
        return False, "MetaTrader5 Python package not installed"


def _check_mt5_terminal() -> Tuple[bool, str]:
    auth = auth_report()
    status_val = str(auth.get("status", "")).upper()
    probe_status = str((auth.get("probe") or {}).get("status", "UNKNOWN")).upper()
    if status_val == "AUTHORIZED" and probe_status == "AUTHORIZED":
        return True, "MT5 terminal authorized and reachable"
    return False, f"MT5 terminal not ready: status={status_val}, probe={probe_status}"


def _check_env_vars() -> List[Dict[str, Any]]:
    required = ["MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"]
    optional = ["LLM_TRADER_ALLOW_LIVE", "OPENROUTER_API_KEY", "FRED_API_KEY", "NEWSAPI_KEY"]
    results = []
    for var in required:
        present = bool(os.environ.get(var))
        results.append({"var": var, "required": True, "present": present})
    for var in optional:
        present = bool(os.environ.get(var))
        results.append({"var": var, "required": False, "present": present})
    return results


def _check_broker_preflight() -> Tuple[bool, str]:
    try:
        from trading_safety import TradingStore
        store = TradingStore("trading_state.db")
        envelope = store.get_state("broker_preflight", {}) or {}
        symbols = sorted((envelope.get("report") or {}).get("symbols", {}).keys())
        result = validate_envelope(envelope, symbols)
        if result.get("valid"):
            return True, "Broker preflight valid"
        return False, f"Broker preflight invalid: {result.get('reasons', [])}"
    except Exception as exc:
        return False, f"Broker preflight error: {exc}"


def _check_readiness() -> Dict[str, Any]:
    return build_readiness()


def run() -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "execution_authority": False,
        "checks": {},
    }

    mt5_pkg_ok, mt5_pkg_msg = _check_mt5_installed()
    report["checks"]["mt5_package"] = {"pass": mt5_pkg_ok, "message": mt5_pkg_msg}

    mt5_term_ok, mt5_term_msg = _check_mt5_terminal()
    report["checks"]["mt5_terminal"] = {"pass": mt5_term_ok, "message": mt5_term_msg}

    env_checks = _check_env_vars()
    env_ok = all(c["present"] for c in env_checks if c["required"])
    report["checks"]["environment"] = {
        "pass": env_ok,
        "message": "All required env vars present" if env_ok else "Missing required env vars",
        "variables": env_checks,
    }

    preflight_ok, preflight_msg = _check_broker_preflight()
    report["checks"]["broker_preflight"] = {"pass": preflight_ok, "message": preflight_msg}

    sample = sample_report()
    sample_ok = sample.get("status") == "READY_FOR_RESEARCH"
    report["checks"]["labelled_samples"] = {
        "pass": sample_ok,
        "message": f"Sample status: {sample.get('status')} (size={sample.get('sample_size', 0)})",
        "detail": sample,
    }

    rollout = live_rollout_status()
    rollout_ok = rollout.get("active", False)
    report["checks"]["tiny_live_approval"] = {
        "pass": rollout_ok,
        "message": "Tiny-live approval active" if rollout_ok else "Tiny-live approval missing or expired",
        "detail": rollout,
    }

    security = security_audit()
    security_ok = security.get("status") == "CLEAR_FOR_OPERATOR_REVIEW"
    report["checks"]["security_review"] = {
        "pass": security_ok,
        "message": f"Security status: {security.get('status')}",
        "detail": {
            "credential_findings": len(security.get("credential_findings", [])),
            "non_production_findings": len(security.get("non_production_findings", [])),
            "tracked_risky_files": security.get("tracked_risky_files", []),
            "historical_risky_files": security.get("historical_risky_files", []),
            "l0_trace_directories": security.get("l0_trace_directories", []),
        },
    }

    research = research_influence_status()
    research_ok = research.get("allowed", False)
    report["checks"]["research_influence"] = {
        "pass": research_ok,
        "message": f"Research influence: {research.get('status')}",
        "detail": research,
    }

    readiness = _check_readiness()
    report["readiness"] = {
        "status": readiness.get("status"),
        "blockers": readiness.get("blockers", []),
        "next_actions": readiness.get("next_actions", []),
    }

    all_pass = all(v["pass"] for v in report["checks"].values())
    report["overall"] = "PASS" if all_pass else "FAIL"
    return report


if __name__ == "__main__":
    import json
    result = run()
    print(json.dumps(result, indent=2, default=str))
