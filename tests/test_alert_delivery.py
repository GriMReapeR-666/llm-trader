import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from alert_delivery import (
    build_sender,
    deliver_pending,
    render_message,
    resolve_webhook_url,
)
from alert_outbox import enqueue, status


def _enqueue_two(path):
    enqueue(path, [
        {"code": "UNRESOLVED_ORDER_INTENTS", "severity": "CRITICAL", "count": 3},
        {"code": "STALE_POSITION_MARKS", "severity": "WARNING", "count": 1},
    ])


def _force_due(path):
    with sqlite3.connect(path) as con:
        con.execute("UPDATE alert_outbox SET next_attempt_at='1970-01-01T00:00:00+00:00'")


def test_file_transport_delivers_once_and_is_idempotent(tmp_path):
    db = str(tmp_path / "journal.db")
    out = str(tmp_path / "alerts.jsonl")
    _enqueue_two(db)

    result = deliver_pending(db, transport="file", file_path=out)
    assert result["transport"] == "FILE"
    assert result["claimed"] == 2
    assert len(result["delivered"]) == 2
    assert result["execution_authority"] is False
    assert status(db)["counts"] == {"SENT": 2}

    lines = [json.loads(line) for line in open(out, encoding="utf-8")]
    assert {line["code"] for line in lines} == {"UNRESOLVED_ORDER_INTENTS", "STALE_POSITION_MARKS"}
    assert all(line["severity"] in {"CRITICAL", "WARNING"} for line in lines)
    assert all(isinstance(line["id"], int) for line in lines)

    again = deliver_pending(db, transport="file", file_path=out)
    assert again["claimed"] == 0
    assert len(lines) == 2


def test_delivery_failure_retries_then_blocks_without_silent_loss(tmp_path):
    db = str(tmp_path / "journal.db")
    out = str(tmp_path / "never.jsonl")
    enqueue(db, [{"code": "DATABASE_MISSING", "severity": "CRITICAL"}])

    def broken(message, alert):
        raise RuntimeError("transport down")

    first = deliver_pending(db, transport="file", file_path=out,
                            max_attempts=2, sender=broken)
    assert first["failed"][0]["status"] == "PENDING"
    assert "transport down" in first["failed"][0]["error"]

    _force_due(db)
    second = deliver_pending(db, transport="file", file_path=out,
                             max_attempts=2, sender=broken)
    assert second["failed"][0]["status"] == "BLOCKED"
    assert status(db)["counts"] == {"BLOCKED": 1}


def test_crash_between_claim_and_acknowledge_recovers_exactly_once(tmp_path):
    db = str(tmp_path / "journal.db")
    out = str(tmp_path / "recovered.jsonl")
    enqueue(db, [{"code": "CLOCK_DRIFT", "severity": "WARNING"}])

    # Simulate a worker crash after claiming but before acknowledging by
    # expiring the lease behind its back.
    from alert_outbox import claim
    claimed = claim(db, lease_seconds=120)
    assert len(claimed) == 1
    with sqlite3.connect(db) as con:
        con.execute("UPDATE alert_outbox SET lease_until='1970-01-01T00:00:00+00:00' WHERE id=?",
                    (claimed[0]["id"],))

    result = deliver_pending(db, transport="file", file_path=out)
    assert result["recovered_leases"] == 1
    assert len(result["delivered"]) == 1
    assert status(db)["counts"] == {"SENT": 1}
    assert len(open(out, encoding="utf-8").readlines()) == 1


def test_mixed_batch_isolates_failures(tmp_path):
    db = str(tmp_path / "journal.db")
    out = str(tmp_path / "mixed.jsonl")
    _enqueue_two(db)

    def flaky(message, alert):
        if alert["payload"]["code"] == "STALE_POSITION_MARKS":
            raise OSError("disk error")

    result = deliver_pending(db, transport="file", file_path=out,
                             max_attempts=5, sender=flaky)
    assert [row["code"] for row in result["delivered"]] == ["UNRESOLVED_ORDER_INTENTS"]
    assert [row["code"] for row in result["failed"]] == ["STALE_POSITION_MARKS"]
    assert result["failed"][0]["status"] == "PENDING"


def test_configuration_fails_closed_before_claiming(tmp_path):
    db = str(tmp_path / "journal.db")
    _enqueue_two(db)

    with pytest.raises(ValueError):
        build_sender("file", file_path="")
    with pytest.raises(ValueError):
        build_sender("file", file_path=str(tmp_path / "missing_dir" / "out.jsonl"))
    with pytest.raises(ValueError):
        build_sender("webhook", webhook_url=None)
    with pytest.raises(ValueError):
        build_sender("webhook", webhook_url="ftp://example.com")
    with pytest.raises(ValueError):
        build_sender("carrier-pigeon")

    with pytest.raises(ValueError):
        deliver_pending(db, transport="file", file_path=None)
    with pytest.raises(ValueError):
        deliver_pending(db, transport="webhook")

    # Nothing was claimed or mutated by the failed configuration attempts.
    assert status(db)["counts"] == {"PENDING": 2}


class _WebhookHandler(BaseHTTPRequestHandler):
    response_code = 200

    def do_POST(self):  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", 0))
        self.server.received.append(json.loads(self.rfile.read(length).decode("utf-8")))
        self.send_response(self.response_code)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def webhook_server():
    server = HTTPServer(("127.0.0.1", 0), _WebhookHandler)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_webhook_transport_delivers_to_live_local_endpoint(tmp_path, webhook_server):
    db = str(tmp_path / "journal.db")
    url = f"http://127.0.0.1:{webhook_server.server_port}/alert"
    enqueue(db, [{"code": "PROVIDER_CIRCUIT_OPEN", "severity": "CRITICAL"}])

    result = deliver_pending(db, transport="webhook", webhook_url=url)
    assert len(result["delivered"]) == 1
    assert result["delivered"][0]["status"] == "SENT"
    assert status(db)["counts"] == {"SENT": 1}

    assert len(webhook_server.received) == 1
    body = webhook_server.received[0]
    assert body["code"] == "PROVIDER_CIRCUIT_OPEN"
    assert body["channel"] == "operator"
    assert isinstance(body["attempt"], int)


def test_webhook_http_error_maps_to_retryable_failure(tmp_path):
    db = str(tmp_path / "journal.db")
    enqueue(db, [{"code": "BACKUP_OVERDUE", "severity": "WARNING"}])
    sender = build_sender("webhook", webhook_url="http://127.0.0.1:1/nope")

    result = deliver_pending(db, transport="webhook", webhook_url="http://127.0.0.1:1/nope",
                             max_attempts=5, sender=sender)
    assert result["failed"][0]["status"] == "PENDING"
    assert "webhook" in result["failed"][0]["error"].lower()


def test_render_message_is_bounded_and_typed(tmp_path):
    db = str(tmp_path / "journal.db")
    enqueue(db, [{"code": "X" * 500, "severity": "INFO"}])
    from alert_outbox import claim
    item = claim(db)[0]
    message = render_message(item, channel=item["channel"])
    parsed = json.loads(message)
    assert len(parsed["code"]) <= 200
    assert set(parsed) >= {"id", "channel", "code", "severity", "attempt", "delivered_at"}


def test_webhook_url_resolution_prefers_explicit_then_env(monkeypatch):
    monkeypatch.delenv("TRADING_ALERT_WEBHOOK_URL", raising=False)
    assert resolve_webhook_url() is None
    monkeypatch.setenv("TRADING_ALERT_WEBHOOK_URL", "https://hooks.example.com/abc")
    assert resolve_webhook_url() == "https://hooks.example.com/abc"
    assert resolve_webhook_url("https://explicit.example.com") == "https://explicit.example.com"
