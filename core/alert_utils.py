"""Shared alert dispatch utility for core modules.

Eliminates duplicated _emit_alert patterns across modules.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("AlertUtils")


def emit_alert(
    db_path: str,
    code: str,
    severity: str,
    payload: Dict[str, Any],
    *,
    channel: str = "operator",
    truncate_code: int = 200,
) -> bool:
    """Enqueue an alert to the operator outbox.

    Args:
        db_path: Path to the alert outbox SQLite database.
        code: Alert code string (e.g. 'DRAWDOWN_CRITICAL').
        severity: Alert severity ('WARNING', 'CRITICAL', etc.).
        payload: Alert detail dictionary.
        channel: Outbox channel (default 'operator').
        truncate_code: Max length for code string.

    Returns:
        True if enqueued successfully, False otherwise.
    """
    try:
        from operations.alert_outbox import enqueue
        enqueue(db_path, [{
            "code": code[:truncate_code],
            "severity": severity,
            "details": payload,
            "payload": payload,
        }], channel=channel)
        return True
    except Exception as exc:
        logger.warning("Alert handoff failed for %s: %s", code, exc)
        return False
