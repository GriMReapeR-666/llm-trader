"""Read-only dashboard evidence assembled from the authoritative journal.

The dashboard is an operator/reporting surface, not a trading permission
layer.  Every panel carries its research qualifiers and this module never
creates an order, changes policy, or grants execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Callable, Dict

from health_alerts import evaluate as evaluate_health_alerts
from journal_audit import qualified_metrics
from readiness_report import build as build_readiness
from trading_memory import TradingMemory


def _safe(name: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Turn a failed evidence source into a visible, fail-closed panel."""
    try:
        value = fn()
        if isinstance(value, dict):
            return value
        return {
            "status": "ERROR",
            "source": name,
            "error": "evidence source returned a non-object",
            "execution_authority": False,
        }
    except Exception as exc:  # noqa: BLE001 - dashboard must remain diagnostic
        return {
            "status": "ERROR",
            "source": name,
            "error": "evidence source unavailable",
            "error_type": type(exc).__name__,
            "execution_authority": False,
        }


def build(
    path: str = "trading_state.db",
    *,
    auth_fn: Callable[[], Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Build a compact, qualified dashboard report.

    ``auth_fn`` is injectable for offline diagnostics/tests.  The default
    readiness implementation may perform its bounded MT5 authorization
    diagnostic, but this report still always returns ``execution_authority``
    as ``False``.
    """
    existed_before = os.path.exists(path)
    metrics = _safe("qualified_metrics", lambda: qualified_metrics(path))
    readiness = _safe("readiness", lambda: build_readiness(path, auth_fn=auth_fn))
    alerts = _safe("alerts", lambda: evaluate_health_alerts(path))
    # Readiness may initialize a missing SQLite schema while collecting its
    # evidence. Preserve the original missing-database condition in the
    # dashboard so an operator cannot mistake that initialization for health.
    if not existed_before and alerts.get("status") == "OK":
        alerts = {
            "status": "CRITICAL",
            "alerts": [{"code": "DATABASE_MISSING", "severity": "CRITICAL"}],
            "execution_authority": False,
            "limitation": "The journal did not exist when this dashboard was requested.",
        }
    memory = _safe("memory", lambda: TradingMemory(path).memory_quality_metrics())

    research = metrics.get("research_quality") if isinstance(metrics.get("research_quality"), dict) else {}
    qualifiers = {
        "sample_size": research.get("sample_size", metrics.get("sample_size", 0)),
        "date_range": research.get("date_range", metrics.get("date_range")),
        "cost_model": research.get("cost_model", metrics.get("cost_model")),
        "point_in_time": research.get("point_in_time"),
        "out_of_sample_status": research.get("out_of_sample_status", metrics.get("out_of_sample_status")),
        "promotion_allowed": bool(research.get("promotion_allowed", False)),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "BLOCKED" if readiness.get("status") in {"BLOCKED", "ERROR"} else "READY_FOR_REVIEW",
        "execution_authority": False,
        "qualifiers": qualifiers,
        "panels": {
            "research_quality": research,
            "proposal_quality": metrics.get("proposal_quality", {}),
            "trade_quality": metrics.get("trade_quality", {}),
            "execution_quality": metrics.get("execution_quality", {}),
            "portfolio_quality": metrics.get("portfolio_quality", {}),
            "period_quality": metrics.get("period_quality", {}),
            "memory_quality": memory,
        },
        "readiness": readiness,
        "alerts": alerts,
        "limitations": [
            "Dashboard values are descriptive evidence only and may not grant order permission.",
            "Research and risk metrics remain non-promotable until labelled, point-in-time, cost-aware, purged/OOS evidence passes.",
            "External alert delivery, broker authorization, and operator approvals remain separate gates.",
        ],
    }
