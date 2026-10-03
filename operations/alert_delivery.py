"""External notifier delivery for the durable operator-alert outbox.

The outbox in ``alert_outbox.py`` is transport-neutral and never opens a
network connection. This module is the explicit, opt-in delivery half:

- Delivery must be requested with a concrete transport (``file`` or
  ``webhook``); a missing/malformed configuration fails closed before any
  alert is claimed.
- Alerts are delivered through the existing durable
  claim/acknowledge/retry protocol, so a crash between claim and
  acknowledge is recovered by lease expiry without silent loss.
- Payloads are already redacted at rest; the rendered message adds only
  routing metadata and stays bounded.
- Nothing here can grant trading or execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from alert_outbox import acknowledge as acknowledge_alert
from alert_outbox import claim as claim_alerts
from alert_outbox import recover_expired as recover_expired_leases


WEBHOOK_ENV_URL = "TRADING_ALERT_WEBHOOK_URL"
WEBHOOK_TIMEOUT_SECONDS = 10
MAX_MESSAGE_CHARS = 8000

Sender = Callable[[str, Dict[str, Any]], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_transport_config(transport: str, *, file_path: Optional[str],
                               webhook_url: Optional[str]) -> None:
    if transport == "file":
        if not file_path or not str(file_path).strip():
            raise ValueError("file transport requires an explicit output path")
        directory = os.path.dirname(os.path.abspath(str(file_path)))
        if not os.path.isdir(directory):
            raise ValueError("file transport directory does not exist")
    elif transport == "webhook":
        if not webhook_url or not str(webhook_url).strip():
            raise ValueError("webhook transport requires an explicit URL")
        url = str(webhook_url).strip()
        if not url.lower().startswith(("https://", "http://")):
            raise ValueError("webhook URL must be http(s)")
    else:
        raise ValueError("transport must be 'file' or 'webhook'")


def _file_sender(file_path: str) -> Sender:
    resolved = os.path.abspath(file_path)

    def send(message: str, alert: Dict[str, Any]) -> None:
        with open(resolved, "a", encoding="utf-8") as handle:
            handle.write(message.rstrip("\n") + "\n")

    return send


def _webhook_sender(url: str) -> Sender:
    resolved = str(url).strip()

    def send(message: str, alert: Dict[str, Any]) -> None:
        request = urllib.request.Request(
            resolved,
            data=message.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
                status_code = int(getattr(response, "status", 0) or 0)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"webhook returned HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RuntimeError(f"webhook unreachable: {reason}") from None
        if not 200 <= status_code < 300:
            raise RuntimeError(f"webhook returned HTTP {status_code}")

    return send


def build_sender(transport: str, *, file_path: Optional[str] = None,
                 webhook_url: Optional[str] = None) -> Sender:
    _validate_transport_config(transport, file_path=file_path, webhook_url=webhook_url)
    if transport == "file":
        return _file_sender(str(file_path))
    return _webhook_sender(str(webhook_url))


def resolve_webhook_url(explicit: Optional[str] = None) -> Optional[str]:
    """Prefer the explicit value; fall back to the environment variable."""
    candidate = explicit if explicit and str(explicit).strip() else os.environ.get(WEBHOOK_ENV_URL, "")
    candidate = str(candidate).strip()
    return candidate or None


def render_message(item: Dict[str, Any], *, channel: str) -> str:
    payload = item.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("claimed alert payload must be an object")
    message = {
        "id": int(item["id"]),
        "channel": str(channel)[:80],
        "code": str(payload.get("code", ""))[:200],
        "severity": str(payload.get("severity", ""))[:20],
        "count": payload.get("count"),
        "details": payload.get("details", {}),
        "attempt": int(item.get("attempts") or 0) + 1,
        "delivered_at": _now(),
    }
    text = json.dumps(message, sort_keys=True, separators=(",", ":"), default=str)
    return text[:MAX_MESSAGE_CHARS]


def deliver_pending(path: str, *, transport: str = "file",
                    file_path: Optional[str] = None,
                    webhook_url: Optional[str] = None,
                    limit: int = 20, lease_seconds: int = 120,
                    max_attempts: int = 5,
                    sender: Optional[Sender] = None) -> Dict[str, Any]:
    """Deliver due PENDING alerts through the configured transport.

    The sender callable is injectable for deterministic tests; production
    callers get the fail-closed file/webhook senders from :func:`build_sender`.
    Configuration is validated before any alert is claimed.
    """
    transport_name = str(transport or "").strip().lower()
    limit = _validate_positive_int(limit, "limit")
    lease_seconds = _validate_positive_int(lease_seconds, "lease_seconds")
    max_attempts = _validate_positive_int(max_attempts, "max_attempts")
    if sender is None:
        sender = build_sender(transport_name, file_path=file_path, webhook_url=webhook_url)

    # Reclaim alerts whose delivery crashed mid-flight before claiming new work.
    recovery = recover_expired_leases(path)

    claimed: List[Dict[str, Any]] = claim_alerts(path, limit=limit, lease_seconds=lease_seconds)
    delivered: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for item in claimed:
        code = ""
        try:
            payload = item.get("payload")
            code = str(payload.get("code", "")) if isinstance(payload, dict) else ""
            channel = str(item.get("channel") or "")
            if not channel:
                raise ValueError("claimed alert has no channel")
            message = render_message(item, channel=channel)
            sender(message, item)
            outcome = acknowledge_alert(path, int(item["id"]), delivered=True,
                                        max_attempts=max_attempts)
            delivered.append({"id": int(item["id"]), "code": code,
                              "status": outcome["status"]})
        except Exception as exc:  # per-alert isolation; one bad target cannot stop the batch
            error_text = f"{type(exc).__name__}: {exc}"[:500]
            try:
                outcome = acknowledge_alert(path, int(item["id"]), delivered=False,
                                            error=error_text, max_attempts=max_attempts)
                failed.append({"id": int(item["id"]), "code": code,
                               "status": outcome["status"], "error": error_text})
            except Exception as ack_error:  # leave the lease to expire for recovery
                skipped.append({"id": int(item["id"]),
                                "ack_error": f"{type(ack_error).__name__}: {ack_error}"[:500]})

    return {
        "transport": transport_name.upper(),
        "recovered_leases": recovery.get("recovered", 0),
        "claimed": len(claimed),
        "delivered": delivered,
        "failed": failed,
        "skipped": skipped,
        "delivery_requested_at": _now(),
        "execution_authority": False,
    }
