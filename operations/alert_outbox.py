"""Durable operator-alert handoff for read-only health reports.

The outbox is deliberately transport-neutral: it persists redacted alert
payloads for an external notifier, but never opens a network connection,
changes trading permission, or acknowledges delivery without an explicit
caller action. Idempotency is keyed by report fingerprint and alert code.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from typing import Any, Dict, Iterable, List


MAX_ATTEMPTS = 5
LEASE_SECONDS = 120

# Self-reporting incident: when outbox_health reports CRITICAL (pending
# backlog beyond the stall threshold) the alert pipeline raises its own
# incident, so a stalled consumer is surfaced instead of silently swallowing
# every downstream alert.
CONSUMER_STALLED_CODE = "ALERT_CONSUMER_STALLED"
CONSUMER_RESUMED_CODE = "ALERT_CONSUMER_RESUMED"
#: Re-raise dampener: once an incident row was delivered/expired, do not
#: raise a fresh one within this window (the dispatcher self-report would
#: otherwise re-raise every 60s while a large backlog drains).
INCIDENT_RAISE_COOLDOWN_S = 3600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(alert: Dict[str, Any], *, channel: str) -> str:
    material = {"channel": channel, "code": alert.get("code"),
                "severity": alert.get("severity"), "payload": alert}
    return hashlib.sha256(_stable(material).encode("utf-8")).hexdigest()


def _redact(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Keep alert payloads bounded and prevent secret-like fields at rest."""
    lowered = key.lower()
    if any(marker in lowered for marker in ("password", "secret", "token", "api_key", "credential")):
        return "[REDACTED]"
    if depth >= 3:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, dict):
        return {str(k)[:80]: _redact(v, key=str(k), depth=depth + 1)
                for k, v in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth=depth + 1) for item in list(value)[:20]]
    return str(value)[:500]


_KNOWN_SCHEMAS: set = set()


def ensure_schema(path: str) -> None:
    if path in _KNOWN_SCHEMAS:
        return
    con = sqlite3.connect(path, timeout=10)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS alert_outbox(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL UNIQUE,
            channel TEXT NOT NULL,
            code TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            lease_until TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sent_at TEXT,
            error TEXT,
            payload TEXT NOT NULL
        )""")
        con.commit()
        _KNOWN_SCHEMAS.add(path)
    finally:
        con.close()


def _connect(path: str, ensure: bool = True) -> sqlite3.Connection:
    if ensure and path not in _KNOWN_SCHEMAS:
        ensure_schema(path)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _validate_alert(alert: Any) -> Dict[str, Any]:
    if not isinstance(alert, dict):
        raise ValueError("alert must be an object")
    code = str(alert.get("code") or "").strip()
    severity = str(alert.get("severity") or "WARNING").strip().upper()
    if not code:
        raise ValueError("alert code is required")
    if severity not in {"INFO", "WARNING", "CRITICAL"}:
        raise ValueError("alert severity is invalid")
    details = alert.get("details") if alert.get("details") is not None else alert.get("payload", {})
    return {"code": code[:200], "severity": severity,
            "count": _redact(alert.get("count"), key="count"),
            "details": _redact(details, key="details")}


def enqueue(path: str, alerts: Iterable[Dict[str, Any]], *, channel: str = "operator") -> Dict[str, Any]:
    channel = str(channel or "").strip()
    if not channel or len(channel) > 80:
        raise ValueError("alert channel is required")
    try:
        values = list(alerts or [])
    except TypeError:
        raise ValueError("alerts must be iterable") from None
    now = _now()
    inserted, duplicate = [], []
    con = _connect(path)
    try:
        for raw in values:
            alert = _validate_alert(raw)
            fingerprint = _fingerprint(alert, channel=channel)
            payload = _stable(alert)
            try:
                con.execute("""INSERT INTO alert_outbox
                    (fingerprint,channel,code,severity,status,attempts,next_attempt_at,created_at,updated_at,payload)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (fingerprint, channel, alert["code"], alert["severity"], "PENDING", 0,
                             now, now, now, payload))
                inserted.append(fingerprint)
            except sqlite3.IntegrityError:
                duplicate.append(fingerprint)
        con.commit()
    finally:
        con.close()
    return {"inserted": inserted, "duplicates": duplicate,
            "count": len(inserted), "execution_authority": False,
            "transport": "NONE"}


def upsert_alert(path: str, alerts: Iterable[Dict[str, Any]],
                 *, channel: str = "operator") -> Dict[str, Any]:
    """Insert or refresh alerts keyed by (channel, code) for recurring conditions.

    Condition alerts (e.g. ``ALPHA_REVERSED_<alpha>``) carry live metrics in
    their payload, so payload fingerprints change on every check and plain
    ``enqueue`` piles up duplicate PENDING rows. Upsert semantics:

    - An existing UNRESOLVED row (PENDING or CLAIMED) with the same channel
      and code is updated in place: payload/severity refreshed, lease cleared,
      status returned to PENDING, ``next_attempt_at`` reset so the dispatcher
      re-delivers the latest numbers. Attempts are preserved.
    - Otherwise (no unresolved row — e.g. the previous alert was already
      SENT/EXPIRED) a fresh row is inserted exactly like ``enqueue``, keeping
      delivery history intact.
    """
    channel = str(channel or "").strip()
    if not channel or len(channel) > 80:
        raise ValueError("alert channel is required")
    try:
        values = list(alerts or [])
    except TypeError:
        raise ValueError("alerts must be iterable") from None
    now = _now()
    inserted, updated, duplicate = [], [], []
    con = _connect(path)
    try:
        for raw in values:
            alert = _validate_alert(raw)
            payload = _stable(alert)
            row = con.execute(
                """SELECT id, fingerprint FROM alert_outbox
                   WHERE channel=? AND code=? AND status IN ('PENDING','CLAIMED')
                   ORDER BY id DESC LIMIT 1""",
                (channel, alert["code"]),
            ).fetchone()
            if row is not None:
                con.execute(
                    """UPDATE alert_outbox SET severity=?, payload=?,
                       status='PENDING', lease_until=NULL, next_attempt_at=?,
                       updated_at=? WHERE id=?""",
                    (alert["severity"], payload, now, now, row["id"]),
                )
                updated.append(alert["code"])
                continue
            fingerprint = _fingerprint(alert, channel=channel)
            try:
                con.execute(
                    """INSERT INTO alert_outbox
                       (fingerprint,channel,code,severity,status,attempts,
                        next_attempt_at,created_at,updated_at,payload)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (fingerprint, channel, alert["code"], alert["severity"],
                     "PENDING", 0, now, now, now, payload),
                )
                inserted.append(fingerprint)
            except sqlite3.IntegrityError:
                duplicate.append(fingerprint)
        con.commit()
    finally:
        con.close()
    return {"inserted": inserted, "updated": updated, "duplicates": duplicate,
            "count": len(inserted) + len(updated),
            "execution_authority": False, "transport": "NONE"}


def claim(path: str, *, limit: int = 20, lease_seconds: int = LEASE_SECONDS) -> List[Dict[str, Any]]:
    if (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
            or not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool)
            or lease_seconds <= 0):
        raise ValueError("limit and lease_seconds must be positive integers")
    now = datetime.now(timezone.utc)
    lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
    con = _connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute("""SELECT * FROM alert_outbox
            WHERE status='PENDING' AND next_attempt_at<=?
            ORDER BY id LIMIT ?""", (now.isoformat(), limit)).fetchall()
        result = []
        for row in rows:
            con.execute("UPDATE alert_outbox SET status='CLAIMED',lease_until=?,updated_at=? WHERE id=? AND status='PENDING'",
                        (lease_until, now.isoformat(), row["id"]))
            item = dict(row)
            item["status"] = "CLAIMED"
            item["lease_until"] = lease_until
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        con.commit()
        return result
    finally:
        con.close()


def acknowledge(path: str, alert_id: int, *, delivered: bool, error: str = "",
                max_attempts: int = MAX_ATTEMPTS) -> Dict[str, Any]:
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0:
        raise ValueError("alert_id must be a positive integer")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts <= 0:
        raise ValueError("max_attempts must be a positive integer")
    if not isinstance(delivered, bool):
        raise ValueError("delivered must be boolean")
    con = _connect(path)
    try:
        row = con.execute("SELECT * FROM alert_outbox WHERE id=?", (alert_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown alert id {alert_id}")
        if row["status"] == "SENT":
            return {"id": alert_id, "status": "SENT", "attempts": int(row["attempts"] or 0),
                    "execution_authority": False, "transport": "NONE"}
        if row["status"] != "CLAIMED":
            raise ValueError(f"alert {alert_id} is not claimed (status={row['status']})")
        now = datetime.now(timezone.utc)
        attempts = int(row["attempts"] or 0) + 1
        if delivered:
            status, sent_at, next_at = "SENT", now.isoformat(), now.isoformat()
            message = None
        elif attempts >= max_attempts:
            status, sent_at, next_at = "BLOCKED", None, now.isoformat()
            message = str(error or "delivery failed")[:2000]
        else:
            delay = min(3600, 2 ** min(attempts, 10))
            status, sent_at, next_at = "PENDING", None, (now + timedelta(seconds=delay)).isoformat()
            message = str(error or "delivery failed")[:2000]
        con.execute("""UPDATE alert_outbox SET status=?,attempts=?,next_attempt_at=?,lease_until=NULL,
            updated_at=?,sent_at=?,error=? WHERE id=?""",
                    (status, attempts, next_at, now.isoformat(), sent_at, message, alert_id))
        con.commit()
        return {"id": alert_id, "status": status, "attempts": attempts,
                "execution_authority": False, "transport": "NONE"}
    finally:
        con.close()


def recover_expired(path: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    con = _connect(path)
    try:
        result = con.execute(
            """UPDATE alert_outbox
               SET status='PENDING', lease_until=NULL, updated_at=?
               WHERE status='CLAIMED' AND lease_until IS NOT NULL AND lease_until<?""",
            (now, now),
        )
        con.commit()
        return {"recovered": result.rowcount, "execution_authority": False,
                "transport": "NONE"}
    finally:
        con.close()


def expire_stale(path: str, *, ttl_days: int = 7) -> Dict[str, Any]:
    """Expire PENDING alerts older than the TTL.

    An alert that has waited longer than ``ttl_days`` is stale news:
    delivering it would spam the operator with conditions that have since
    resolved (or worsened beyond usefulness). Marked EXPIRED — the live
    outbox stays the undelivered-work queue; terminal rows (EXPIRED/SENT)
    later move to the archive via :func:`archive_terminal`, keeping the
    audit trail outside the hot journal.
    """
    if int(ttl_days) < 1:
        raise ValueError("ttl_days must be at least 1")
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=int(ttl_days))).isoformat()
    now = _now()
    con = _connect(path)
    try:
        result = con.execute(
            """UPDATE alert_outbox SET status='EXPIRED', updated_at=?
               WHERE status='PENDING' AND created_at<?""",
            (now, cutoff),
        )
        con.commit()
        return {"expired": result.rowcount, "ttl_days": int(ttl_days),
                "execution_authority": False, "transport": "NONE"}
    finally:
        con.close()


def expire_duplicates(path: str, *, keep_per_code: int = 1) -> Dict[str, Any]:
    """Collapse PENDING alert piles per alert code, keeping the newest.

    Some producers re-enqueue the same code with a slightly different
    payload on every check (e.g. ALPHA_REVERSED_<name> with live IC
    numbers), which defeats fingerprint dedupe and piles hundreds of
    copies. This keeps the ``keep_per_code`` newest PENDING rows per code
    and expires the rest — the operator still gets the current signal,
    once, instead of a wall of stale repeats.
    """
    if int(keep_per_code) < 1:
        raise ValueError("keep_per_code must be at least 1")
    now = _now()
    con = _connect(path)
    try:
        rows = con.execute(
            """SELECT id, code FROM alert_outbox WHERE status='PENDING'
               ORDER BY code, id DESC""").fetchall()
        seen: Dict[str, int] = {}
        stale_ids: List[int] = []
        for row in rows:
            code = str(row["code"])
            seen[code] = seen.get(code, 0) + 1
            if seen[code] > int(keep_per_code):
                stale_ids.append(int(row["id"]))
        expired = 0
        for alert_id in stale_ids:
            result = con.execute(
                """UPDATE alert_outbox SET status='EXPIRED', updated_at=?,
                   error='superseded by newer alert with same code'
                   WHERE id=? AND status='PENDING'""",
                (now, alert_id),
            )
            expired += max(0, result.rowcount)
        con.commit()
        return {"expired": expired, "codes_collapsed": len(seen),
                "kept_per_code": int(keep_per_code),
                "execution_authority": False, "transport": "NONE"}
    finally:
        con.close()


def archive_terminal(path: str, *, archive_path: str = "alerts_archive.db",
                     batch_limit: int = 2000) -> Dict[str, Any]:
    """Move terminal outbox rows (SENT/EXPIRED/BLOCKED) to the cold archive db.

    The outbox is a work queue: PENDING/CLAIMED rows are live work, while
    SENT/EXPIRED/BLOCKED rows are history.  BLOCKED rows (delivery failed
    ``MAX_ATTEMPTS`` times) are terminal and immutable — without archiving
    them they would linger in the hot queue forever.  History is evidence, so it is moved to a
    dedicated append-only archive database rather than deleted — the live
    journal keeps its hot set small (see the 2026-09-26 review: 2,732 EXPIRED
    + 148 SENT rows, ~0.25 MB payloads but constant vacuum/headache for the
    1.1 GB journal), and the archive keeps the rows intact forever.

    - Copies rows in id order, bounded by ``batch_limit`` per call, and then
      deletes only the ids that were verifiably inserted (per-row guard), so
      a crash mid-copy loses nothing and duplicates nothing.
    - The archive side is idempotent on fingerprint (UNIQUE), so re-running
      after a partial copy is safe.
    - WAL mode is set on the archive so the live journal is never blocked.
    - ``archive_path`` is returned; ``retention_status``-style deletion stays
      an explicit operator action.
    """
    if batch_limit < 1:
        raise ValueError("batch_limit must be at least 1")
    if not archive_path:
        raise ValueError("archive_path is required")
    live = _connect(path)
    try:
        rows = live.execute(
            """SELECT id, fingerprint, channel, code, severity, status, attempts,
                      next_attempt_at, lease_until, created_at, updated_at,
                      sent_at, error, payload
               FROM alert_outbox WHERE status IN ('SENT','EXPIRED','BLOCKED')
               ORDER BY id LIMIT ?""", (int(batch_limit),)
        ).fetchall()
        if not rows:
            return {"archived": 0, "deleted": 0, "scanned": 0,
                    "remaining": int(live.execute(
                        "SELECT COUNT(*) FROM alert_outbox WHERE status IN ('SENT','EXPIRED','BLOCKED')"
                    ).fetchone()[0]),
                    "archive_path": str(archive_path),
                    "execution_authority": False, "transport": "NONE"}
        arch = sqlite3.connect(str(archive_path), timeout=10)
        try:
            arch.execute("PRAGMA journal_mode=WAL")
            arch.execute("""CREATE TABLE IF NOT EXISTS alert_outbox_archive(
                id INTEGER,
                fingerprint TEXT NOT NULL UNIQUE,
                channel TEXT NOT NULL,
                code TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sent_at TEXT,
                error TEXT,
                payload TEXT NOT NULL,
                archived_at TEXT NOT NULL
            )""")
            archived_now = 0
            moved_ids: List[int] = []
            now = _now()
            for row in rows:
                item = dict(row)
                try:
                    arch.execute(
                        """INSERT INTO alert_outbox_archive
                           (id,fingerprint,channel,code,severity,status,attempts,
                            next_attempt_at,lease_until,created_at,updated_at,
                            sent_at,error,payload,archived_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (item["id"], item["fingerprint"], item["channel"],
                         item["code"], item["severity"], item["status"],
                         int(item["attempts"] or 0), item["next_attempt_at"],
                         item["lease_until"], item["created_at"], item["updated_at"],
                         item["sent_at"], item["error"], item["payload"], now))
                    moved_ids.append(int(item["id"]))
                    archived_now += 1
                except sqlite3.IntegrityError:
                    # Already archived on a previous pass — safe to delete the
                    # live copy (evidence preserved on the archive side).
                    moved_ids.append(int(item["id"]))
            arch.commit()
        finally:
            arch.close()
        # Delete only rows whose archive insert was verified.  Delete after
        # commit so a crash between copy and delete loses nothing.
        deleted = 0
        for alert_id in moved_ids:
            cur = live.execute(
                "DELETE FROM alert_outbox WHERE id=? AND status IN ('SENT','EXPIRED','BLOCKED')",
                (alert_id,))
            deleted += max(0, cur.rowcount)
        live.commit()
        return {"archived": archived_now, "deleted": deleted,
                "scanned": len(rows), "archive_path": str(archive_path),
                "remaining": int(live.execute(
                    "SELECT COUNT(*) FROM alert_outbox WHERE status IN ('SENT','EXPIRED','BLOCKED')"
                ).fetchone()[0]),
                "execution_authority": False, "transport": "NONE"}
    finally:
        live.close()


def report_consumer_incident(path: str, *, journal_path: str = "",
                             cooldown_s: int = INCIDENT_RAISE_COOLDOWN_S) -> Dict[str, Any]:
    """Raise/resolve the outbox's own stalled-consumer incident.

    Derives ``outbox_health(path)`` and, when it reports CRITICAL, enqueues a
    CRITICAL ``ALERT_CONSUMER_STALLED`` alert — the alert pipeline raising its
    own incident so a stalled consumer can no longer silently swallow every
    downstream alert. When the outbox is healthy and an unresolved incident
    row exists, it enqueues ``ALERT_CONSUMER_RESUMED`` so the operator sees
    the recovery, and future CRITICALs raise a fresh incident.

    Dampening: while a STALLED row is already PENDING/CLAIMED (or was raised
    within ``cooldown_s``), no new row is enqueued — the dispatcher's own
    self-report re-fires every minute and must not spam a 2,732-row backlog.
    Transport-neutral like every producer here; no network, no authority.
    """
    now_dt = datetime.now(timezone.utc)
    result: Dict[str, Any] = {"health_status": None,
                              "raised": False, "resolved": False,
                              "suppressed": False,
                              "execution_authority": False,
                              "transport": "NONE"}
    try:
        health = outbox_health(path)
        result["health_status"] = health.get("status")
        con = _connect(path)
        try:
            unresolved = con.execute(
                """SELECT id, status FROM alert_outbox
                   WHERE code=? AND status IN ('PENDING','CLAIMED')
                   ORDER BY id DESC LIMIT 1""",
                (CONSUMER_STALLED_CODE,)).fetchone()
        finally:
            con.close()

        if health.get("status") == "CRITICAL":
            if unresolved is not None:
                # Already ringing — refresh payload, do not spam a new row.
                upsert_alert(path, [{
                    "code": CONSUMER_STALLED_CODE, "severity": "CRITICAL",
                    "details": {
                        "reason": "outbox_health CRITICAL: stalled consumer",
                        "pending": health.get("pending"),
                        "expired": health.get("expired"),
                        "oldest_pending_at": health.get("oldest_pending_at"),
                        "journal": journal_path or path,
                    }}])
                result["raised"] = False
                result["refreshed"] = True
                return result
            # Dampener: was one raised recently?  Scan terminal STALLED rows.
            con = _connect(path)
            try:
                last = con.execute(
                    """SELECT updated_at FROM alert_outbox WHERE code=?
                       ORDER BY id DESC LIMIT 1""",
                    (CONSUMER_STALLED_CODE,)).fetchone()
            finally:
                con.close()
            if last is not None and last["updated_at"]:
                try:
                    age = (now_dt - datetime.fromisoformat(
                        str(last["updated_at"]).replace("Z", "+00:00"))).total_seconds()
                    if 0 <= age < cooldown_s:
                        result["suppressed"] = True
                        return result
                except (TypeError, ValueError):
                    pass
            enqueue(path, [{
                "code": CONSUMER_STALLED_CODE,
                "severity": "CRITICAL",
                "details": {
                    "reason": "outbox_health CRITICAL: stalled alert consumer",
                    "pending": health.get("pending"),
                    "expired": health.get("expired"),
                    "oldest_pending_at": health.get("oldest_pending_at"),
                    "journal": journal_path or path,
                }}])
            result["raised"] = True
            return result

        # Healthy (WARN or OK): if an unresolved incident is still ringing,
        # resolve it — but dampened.  With a dead consumer nobody ever
        # acknowledges the STALLED row, so an unguarded branch would enqueue
        # a fresh RESUMED row on every cadence; only one live RESUMED notice
        # at a time, and none within the cooldown, same as the raise side.
        if unresolved is not None:
            con = _connect(path)
            try:
                res = con.execute(
                    """SELECT status, updated_at FROM alert_outbox WHERE code=?
                       ORDER BY id DESC LIMIT 1""",
                    (CONSUMER_RESUMED_CODE,)).fetchone()
            finally:
                con.close()
            resume_blocked = False
            if res is not None:
                if str(res["status"]) in ("PENDING", "CLAIMED"):
                    resume_blocked = True
                else:
                    try:
                        age = (now_dt - datetime.fromisoformat(
                            str(res["updated_at"]).replace("Z", "+00:00"))).total_seconds()
                        if 0 <= age < cooldown_s:
                            resume_blocked = True
                    except (TypeError, ValueError):
                        pass
            if resume_blocked:
                result["resolve_suppressed"] = True
            else:
                enqueue(path, [{
                    "code": CONSUMER_RESUMED_CODE, "severity": "INFO",
                    "details": {
                        "reason": "outbox health recovered; consumer is draining again",
                        "pending": health.get("pending"),
                        "expired": health.get("expired"),
                    }}])
                result["resolved"] = True
        return result
    except Exception as exc:  # fail-soft: reporting must never break callers
        result["error"] = str(exc)[:300]
        return result


def status(path: str) -> Dict[str, Any]:
    con = _connect(path)
    try:
        rows = con.execute("SELECT status,COUNT(*) AS count FROM alert_outbox GROUP BY status").fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return {"status": "OK", "counts": counts, "execution_authority": False,
                "transport": "NONE"}
    finally:
        con.close()


def outbox_health(path: str) -> Dict[str, Any]:
    """Operator-facing outbox backlog summary for health reports.

    Read-only: counts rows per status (PENDING/CLAIMED/SENT/EXPIRED) plus the
    oldest PENDING timestamp, and classifies the backlog so operators can see
    alert-pipeline state at a glance in the market_health report. Thresholds
    mirror the alert dispatcher's backlog-aware drain (BACKLOG_THRESHOLD=200):
    a handful of PENDING rows is normal mid-cadence, a large pile means the
    consumer stalled, and a large EXPIRED pile means it stalled for days.
    """
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT status,COUNT(*) AS count FROM alert_outbox GROUP BY status"
        ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        oldest_pending = con.execute(
            "SELECT MIN(created_at) FROM alert_outbox WHERE status='PENDING'"
        ).fetchone()[0]
    finally:
        con.close()
    pending = counts.get("PENDING", 0)
    expired = counts.get("EXPIRED", 0)
    if pending > 200:
        health_status = "CRITICAL"
    elif pending > 50 or expired > 200:
        health_status = "WARN"
    else:
        health_status = "OK"
    return {
        "status": health_status,
        "counts": counts,
        "pending": pending,
        "expired": expired,
        "oldest_pending_at": oldest_pending,
        "execution_authority": False,
    }
