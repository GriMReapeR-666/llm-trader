"""Operator-alert dispatcher: drains the durable alert outbox.

The market_health schedule (and other fail-closed surfaces) persist redacted
alerts into the journal's ``alert_outbox`` table, but historically nothing
consumed them: rows accumulated (2,800+ PENDING) and expired CLAIMED leases
were never recovered. This executor closes that loop.

Delivery is transport-neutral, matching the outbox module's contract: an
alert is "delivered" when it has been emitted to the operator event stream
(event bridge + structured log). No network transport is opened here; an
external notifier can subscribe to OPERATOR_ALERT events without this
module gaining any trading authority.

Runs under the IncidentAgent principal (observational, zero order authority).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger("AlertAgent")

DEFAULT_JOURNAL = "trading_state.db"
# Bounded work per invocation: the schedule re-fires every 60s, so draining
# in slices keeps each maintenance batch well inside its handler budget.
DRAIN_LIMIT = 50
# Backlog catch-up: with a large PENDING pile the dispatcher drains harder
# per invocation (still far inside the 60s handler budget) until the
# backlog returns to steady state.
BACKLOG_THRESHOLD = 200
BACKLOG_DRAIN_LIMIT = 400
# Stale news: a PENDING alert older than this is expired, never delivered.
ALERT_TTL_DAYS = 7
# Per-code collapse: producers that re-enqueue with live payload numbers
# (fingerprint dedupe can't catch them) collapse to the newest row.
KEEP_PER_CODE = 1


def handle_alert_dispatch(payload: Dict[str, Any],
                          context: Any = None) -> Dict[str, Any]:
    """Recover leases, expire stale/duplicate alerts, then claim->emit->ack."""
    from operations import alert_outbox

    journal_path = (
        (payload or {}).get("journal_path")
        or getattr(getattr(context, "store", None), "path", None)
        or getattr(getattr(context, "operations", None), "db_path", None)
        or getattr(context, "journal_path", None)
        or DEFAULT_JOURNAL
    )

    recovered = alert_outbox.recover_expired(journal_path)
    # "delivered or expired": stale news and superseded per-code piles must
    # not be delivered — they are expired (kept as EXPIRED rows for audit).
    expired_stale = alert_outbox.expire_stale(journal_path, ttl_days=ALERT_TTL_DAYS)
    expired_dupes = alert_outbox.expire_duplicates(journal_path,
                                                   keep_per_code=KEEP_PER_CODE)

    limit = DRAIN_LIMIT
    try:
        counts = alert_outbox.status(journal_path).get("counts", {})
        if int(counts.get("PENDING", 0) or 0) > BACKLOG_THRESHOLD:
            limit = BACKLOG_DRAIN_LIMIT
    except Exception:
        pass

    claimed = alert_outbox.claim(journal_path, limit=limit)
    sent, failed = 0, 0
    for alert in claimed:
        alert_id = int(alert["id"])
        try:
            _emit_operator_alert(alert)
            alert_outbox.acknowledge(journal_path, alert_id, delivered=True)
            sent += 1
        except Exception as exc:  # noqa: BLE001 - delivery must never crash the pulse
            logger.warning("alert %s delivery failed: %s", alert_id, exc)
            try:
                alert_outbox.acknowledge(
                    journal_path, alert_id, delivered=False,
                    error=str(exc)[:500])
            except Exception as ack_exc:
                logger.error("alert %s acknowledge failed: %s", alert_id, ack_exc)
            failed += 1

    # Self-report: when outbox_health is CRITICAL the pipeline raises its own
    # ALERT_CONSUMER_STALLED incident (and resolves it on recovery).  This
    # runs on the dispatcher itself, so an ALIVE consumer surfaces CRITICAL
    # backlogs promptly; the hourly journal-maintenance watchdog covers the
    # case where this dispatcher never runs at all.
    incident = alert_outbox.report_consumer_incident(journal_path)
    if incident.get("raised") or incident.get("resolved"):
        try:
            from operations.event_bridge import emit
            emit("OUTBOX_INCIDENT", {
                "health_status": incident.get("health_status"),
                "raised": bool(incident.get("raised")),
                "resolved": bool(incident.get("resolved")),
                "journal": journal_path,
            }, actor="IncidentAgent")
        except Exception as bridge_exc:  # noqa: BLE001
            logger.debug("outbox incident bridge emit failed: %s", bridge_exc)

    return {
        "status": "OK",
        "recovered_leases": int(recovered.get("recovered", 0)),
        "expired_stale": int(expired_stale.get("expired", 0)),
        "expired_duplicates": int(expired_dupes.get("expired", 0)),
        "claimed": len(claimed),
        "sent": sent,
        "failed": failed,
        "incident": incident,
        "execution_authority": False,
    }


def _emit_operator_alert(alert: Dict[str, Any]) -> None:
    """Emit one alert to the operator event stream (transport-neutral)."""
    from operations.event_bridge import emit

    payload = alert.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {"raw": payload[:500]}
    emit("OPERATOR_ALERT", {
        "code": alert.get("code"),
        "severity": alert.get("severity"),
        "channel": alert.get("channel"),
        "payload": payload,
    }, actor="IncidentAgent")
    logger.log(
        logging.CRITICAL if str(alert.get("severity", "")).upper() == "CRITICAL"
        else logging.WARNING,
        "OPERATOR ALERT [%s] %s: %s",
        alert.get("severity"), alert.get("code"),
        json.dumps(payload, default=str)[:300] if payload is not None else "",
    )
