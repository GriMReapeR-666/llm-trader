"""Prometheus textfile exporter (master-plan P6.4).

Refreshed from the cycle gate on every pulse; Grafana agent / prometheus
scrapes the file — no extra server, no engine coupling.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Tuple

from core.file_utils import safe_atomic_write

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "artifacts", "metrics", "trader.prom")


def _fmt(value: Any) -> str:
    try:
        v = float(value)
        return f"{v:g}"
    except (TypeError, ValueError):
        return "0"


def collect() -> List[Tuple[str, str, Any]]:
    """(metric_name, help_text, value|labels->value) triples."""
    rows: List[Tuple[str, str, Any]] = []

    try:
        from operations.supervisor import get_supervisor
        health = get_supervisor().overall_health()
        phase = health.get("phase", "OFFLINE")
        phases = ["OFFLINE", "BOOTING", "PREFLIGHT", "WARMUP", "RUNNING",
                  "DEGRADED", "PAUSED", "SHUTDOWN_REQUESTED", "STOPPED"]
        for p in phases:
            rows.append((f'trader_supervisor_phase{{phase="{p}"}}',
                         "Engine supervisor phase (1 = current).",
                         1 if p == phase else 0))
        rows.append(("trader_healthy", "Overall supervisor health.",
                     1 if health.get("healthy") else 0))
        for name in sorted(health.get("principals", {})):
            info = health["principals"][name]
            label = name.replace('"', "")
            rows.append((f'trader_principal_age_seconds{{principal="{label}"}}',
                         "Seconds since last principal beat.",
                         info.get("age_s", 0)))
    except Exception as exc:
        logger.debug("metrics: supervisor section failed: %s", exc)

    try:
        from operations.autonomy import LEVELS, get_autonomy
        snap = get_autonomy().snapshot()
        for key, lv in LEVELS.items():
            rows.append((f'trader_autonomy_level{{level="{key}"}}',
                         "Current autonomy level (1 = active).",
                         1 if snap.level == key else 0))
        rows.append(("trader_halted", "Kill switch engaged.", 1 if snap.halted else 0))
        rows.append(("trader_paused", "Operator pause active.", 1 if snap.paused else 0))
    except Exception as exc:
        logger.debug("metrics: autonomy section failed: %s", exc)

    try:
        from operations.event_bridge import stats as bridge_stats
        s = bridge_stats()
        rows.append(("trader_events_emitted_total",
                     "Events emitted through the bridge.", s.get("emitted", 0)))
        rows.append(("trader_events_dropped_total",
                     "Dropped pre-init emissions.", s.get("dropped", 0)))
    except Exception as exc:
        logger.debug("metrics: bridge section failed: %s", exc)

    try:
        from operations.strategy_factory import get_factory
        strategies = get_factory().all()
        rows.append(("trader_strategies_registered",
                     "Strategies known to the factory.", len(strategies)))
        pending = len([a for a in get_factory().pending_approvals()
                       if a.get("status") == "PENDING"])
        rows.append(("trader_approvals_pending",
                     "Governance approvals awaiting operator.", pending))
    except Exception as exc:
        logger.debug("metrics: factory section failed: %s", exc)

    return rows


def write_metrics(out_path: str = DEFAULT_OUT) -> Dict[str, Any]:
    """Render Prometheus text format; atomic replace. Never raises."""
    lines: List[str] = []
    seen_help: set = set()
    try:
        for metric, help_text, value in collect():
            if metric not in seen_help:
                base = metric.split("{")[0]
                lines.append(f"# HELP {base} {help_text}")
                lines.append(f"# TYPE {base} gauge")
                seen_help.add(metric)
            lines.append(f"{metric} {_fmt(value)}")
        content = "\n".join(lines) + "\n"
        safe_atomic_write(out_path, content)
        return {"ok": True, "path": out_path, "lines": len(lines)}
    except Exception as exc:
        logger.error("metrics export failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def handle_metrics_export(payload: Dict[str, Any],
                          context: Any = None) -> Dict[str, Any]:
    return write_metrics()
