import pytest
import sqlite3

from operations.alert_outbox import acknowledge, claim, enqueue, recover_expired, status


def test_alert_outbox_is_idempotent_and_restart_safe(tmp_path):
    path = str(tmp_path / "alerts.db")
    alert = {"code": "STALE_AGENT_HEARTBEATS", "severity": "CRITICAL", "count": 2}
    first = enqueue(path, [alert])
    second = enqueue(path, [alert])
    assert first["count"] == 1
    assert second["count"] == 0
    claimed = claim(path)
    assert len(claimed) == 1
    assert claimed[0]["payload"]["code"] == alert["code"]
    assert acknowledge(path, claimed[0]["id"], delivered=True)["status"] == "SENT"
    assert status(path)["counts"] == {"SENT": 1}


def test_alert_outbox_retries_then_blocks(tmp_path):
    path = str(tmp_path / "retry.db")
    enqueue(path, [{"code": "DATABASE_MISSING", "severity": "CRITICAL"}])
    item = claim(path)[0]
    assert acknowledge(path, item["id"], delivered=False, error="down", max_attempts=2)["status"] == "PENDING"
    # Make the bounded retry immediately claimable without waiting.
    import sqlite3
    with sqlite3.connect(path) as con:
        con.execute("UPDATE alert_outbox SET next_attempt_at='1970-01-01T00:00:00+00:00' WHERE id=?", (item["id"],))
    item = claim(path)[0]
    assert acknowledge(path, item["id"], delivered=False, error="down", max_attempts=2)["status"] == "BLOCKED"


def test_alert_outbox_rejects_malformed_inputs(tmp_path):
    path = str(tmp_path / "bad.db")
    with pytest.raises(ValueError):
        enqueue(path, [{"severity": "CRITICAL"}])
    with pytest.raises(ValueError):
        claim(path, limit=0)
    assert recover_expired(path)["execution_authority"] is False


def test_alert_ack_requires_claim_and_is_idempotent_after_delivery(tmp_path):
    path = str(tmp_path / "ack.db")
    enqueue(path, [{"code": "CLOCK_DRIFT", "severity": "WARNING"}])
    with pytest.raises(ValueError):
        acknowledge(path, 1, delivered=True)
    item = claim(path)[0]
    assert acknowledge(path, item["id"], delivered=True)["status"] == "SENT"
    assert acknowledge(path, item["id"], delivered=True)["status"] == "SENT"
    with pytest.raises(ValueError):
        acknowledge(path, item["id"], delivered=1)


def test_alert_payload_is_bounded_and_redacted(tmp_path):
    path = str(tmp_path / "redact.db")
    enqueue(path, [{"code": "TEST", "severity": "INFO",
                    "details": {"password": "secret", "nested": {"value": "ok"}}}])
    item = claim(path)[0]
    assert item["payload"]["details"]["password"] == "[REDACTED]"
    assert item["payload"]["details"]["nested"]["value"] == "ok"


def test_upsert_alert_refreshes_pending_row_in_place(tmp_path):
    """Recurring condition alerts update the unresolved row, not a new row."""
    from operations.alert_outbox import upsert_alert

    path = str(tmp_path / "upsert.db")
    enqueue(path, [{"code": "ALPHA_REVERSED_041", "severity": "CRITICAL",
                    "details": {"ic_current": -0.05}}])
    # Live metrics change every check -> different payload fingerprint.
    first = upsert_alert(path, [{"code": "ALPHA_REVERSED_041", "severity": "CRITICAL",
                                 "details": {"ic_current": -0.06}}])
    assert first["updated"] == ["ALPHA_REVERSED_041"]
    assert status(path)["counts"] == {"PENDING": 1}
    # A second identical upsert stays a single row (idempotent).
    upsert_alert(path, [{"code": "ALPHA_REVERSED_041", "severity": "CRITICAL",
                         "details": {"ic_current": -0.07}}])
    assert status(path)["counts"] == {"PENDING": 1}
    # Claimed rows are refreshed too (lease cleared, back to PENDING).
    item = claim(path)[0]
    upsert_alert(path, [{"code": "ALPHA_REVERSED_041", "severity": "CRITICAL",
                         "details": {"ic_current": -0.08}}])
    assert status(path)["counts"] == {"PENDING": 1}
    refreshed = claim(path)[0]
    assert refreshed["payload"]["details"]["ic_current"] == -0.08


def test_upsert_alert_inserts_fresh_row_after_delivery(tmp_path):
    """Once the previous alert resolved, a new occurrence starts a new row."""
    from operations.alert_outbox import upsert_alert

    path = str(tmp_path / "fresh.db")
    upsert_alert(path, [{"code": "ALPHA_REVERSED_041", "severity": "CRITICAL",
                         "details": {"ic_current": -0.05}}])
    item = claim(path)[0]
    acknowledge(path, item["id"], delivered=True)
    second = upsert_alert(path, [{"code": "ALPHA_REVERSED_041", "severity": "CRITICAL",
                                  "details": {"ic_current": -0.09}}])
    assert second["inserted"] and not second["updated"]
    counts = status(path)["counts"]
    assert counts["SENT"] == 1 and counts["PENDING"] == 1
    fresh = claim(path)[0]
    assert fresh["id"] != item["id"]
    assert fresh["payload"]["details"]["ic_current"] == -0.09


def test_archive_terminal_moves_rows_and_keeps_evidence(tmp_path):
    """SENT/EXPIRED rows leave the live outbox and land intact in the archive."""
    from operations.alert_outbox import archive_terminal, enqueue, _connect

    live = str(tmp_path / "journal.db")
    arch = str(tmp_path / "alerts_archive.db")
    enqueue(live, [{"code": "A"}, {"code": "B"}])
    # Expire one, send one.
    con = _connect(live)
    con.execute("UPDATE alert_outbox SET status='EXPIRED' WHERE code='A'")
    con.execute("UPDATE alert_outbox SET status='SENT' WHERE code='B'")
    con.commit()
    con.close()

    result = archive_terminal(live, archive_path=arch)
    assert result["archived"] == 2 and result["deleted"] == 2
    assert result["remaining"] == 0
    assert result["execution_authority"] is False

    aconn = sqlite3.connect(arch)
    rows = aconn.execute(
        "SELECT code, status, payload, archived_at FROM alert_outbox_archive ORDER BY id"
    ).fetchall()
    aconn.close()
    assert [r[0] for r in rows] == ["A", "B"]
    assert {r[1] for r in rows} == {"EXPIRED", "SENT"}
    assert all(r[2] and r[3] for r in rows)  # payloads + archive timestamps kept

    lconn = _connect(live)
    remaining = lconn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0]
    lconn.close()
    assert remaining == 0


def test_archive_terminal_is_idempotent_on_rerun(tmp_path):
    """Re-running after a partial/complete archive neither dupes nor errors."""
    from operations.alert_outbox import archive_terminal, enqueue, _connect

    live = str(tmp_path / "journal.db")
    arch = str(tmp_path / "alerts_archive.db")
    enqueue(live, [{"code": "A"}, {"code": "B"}, {"code": "C"}])
    con = _connect(live)
    con.execute("UPDATE alert_outbox SET status='EXPIRED'")
    con.commit()
    con.close()

    first = archive_terminal(live, archive_path=arch)
    assert first["archived"] == 3
    second = archive_terminal(live, archive_path=arch)  # nothing left live
    assert second["archived"] == 0 and second["deleted"] == 0

    # Simulate a crash-after-copy: same row re-inserted live, already archived.
    con = _connect(live)
    con.execute("ATTACH DATABASE ? AS arch", (arch,))
    con.execute(
        """INSERT INTO alert_outbox(fingerprint,channel,code,severity,status,attempts,
           next_attempt_at,created_at,updated_at,payload)
           SELECT fingerprint,channel,code,severity,status,attempts,next_attempt_at,
                  created_at,updated_at,payload FROM arch.alert_outbox_archive LIMIT 1""")
    con.commit()
    con.close()
    third = archive_terminal(live, archive_path=arch)
    assert third["archived"] == 0       # deduped on fingerprint
    assert third["deleted"] == 1        # live copy removed, evidence stays archived
    aconn = sqlite3.connect(arch)
    n = aconn.execute("SELECT COUNT(*) FROM alert_outbox_archive").fetchone()[0]
    aconn.close()
    assert n == 3


def test_archive_terminal_respects_batch_limit(tmp_path):
    from operations.alert_outbox import archive_terminal, enqueue, _connect

    live = str(tmp_path / "journal.db")
    arch = str(tmp_path / "alerts_archive.db")
    enqueue(live, [{"code": f"K{i}"} for i in range(5)])
    con = _connect(live)
    con.execute("UPDATE alert_outbox SET status='EXPIRED'")
    con.commit()
    con.close()

    result = archive_terminal(live, archive_path=arch, batch_limit=2)
    assert result["archived"] == 2 and result["remaining"] == 3
    result = archive_terminal(live, archive_path=arch, batch_limit=2)
    assert result["archived"] == 2 and result["remaining"] == 1


def test_archive_terminal_leaves_live_work_alone(tmp_path):
    """PENDING/CLAIMED rows are never archived."""
    from operations.alert_outbox import archive_terminal, enqueue

    live = str(tmp_path / "journal.db")
    arch = str(tmp_path / "alerts_archive.db")
    enqueue(live, [{"code": "LIVE1"}])
    result = archive_terminal(live, archive_path=arch)
    assert result["scanned"] == 0 and result["archived"] == 0
    assert status(live)["counts"]["PENDING"] == 1


def test_archive_terminal_moves_blocked_rows(tmp_path):
    """BLOCKED rows (retries exhausted) are terminal — they archive too.

    Regression: archive_terminal originally matched only SENT/EXPIRED, so a
    BLOCKED row lingered in the live hot queue forever.
    """
    from operations.alert_outbox import archive_terminal, enqueue, claim, acknowledge, status

    live = str(tmp_path / "journal.db")
    arch = str(tmp_path / "alerts_archive.db")
    enqueue(live, [{"code": "BLOCKED1"}])
    claimed = claim(live, limit=1)
    # Exhaust retries without ever delivering -> BLOCKED (max_attempts=1
    # collapses the retry ladder: first failed ack is terminal).
    acknowledge(live, int(claimed[0]["id"]), delivered=False,
                error="no transport", max_attempts=1)
    counts = status(live)["counts"]
    assert counts.get("BLOCKED") == 1

    result = archive_terminal(live, archive_path=arch)
    assert result["archived"] == 1 and result["deleted"] == 1
    assert "BLOCKED" not in status(live)["counts"]


def test_outbox_health_after_archive_has_no_stale_expired_pile(tmp_path):
    """Post-archive, health reads the hot set only — no false CRITICAL."""
    from operations.alert_outbox import archive_terminal, enqueue, outbox_health, _connect

    live = str(tmp_path / "journal.db")
    arch = str(tmp_path / "alerts_archive.db")
    enqueue(live, [{"code": f"OLD{i}"} for i in range(210)])
    con = _connect(live)
    con.execute("UPDATE alert_outbox SET status='EXPIRED'")
    con.commit()
    con.close()
    assert outbox_health(live)["status"] == "WARN"  # 210 EXPIRED pre-archive

    archive_terminal(live, archive_path=arch, batch_limit=500)
    health = outbox_health(live)
    assert health["status"] == "OK"
    assert health["expired"] == 0


def test_outbox_archive_is_wired_into_scheduler_and_executors():
    """The daily outbox_archive schedule resolves to a callable handler."""
    from operations.agent_scheduler import SCHEDULES
    from operations.executors import REGISTRY, resolve_handler

    names = [s["name"] for s in SCHEDULES]
    assert "outbox_archive" in names
    spec = REGISTRY["outbox_archive"]
    assert spec.principal == "MaintenanceAgent"
    handler = resolve_handler(spec)
    assert callable(handler)


class TestConsumerIncident:
    """outbox_health CRITICAL raises the pipeline's own incident."""

    def _seed_backlog(self, path, prefix="LOAD", pending=0):
        from operations.alert_outbox import _connect
        con = _connect(path)
        try:
            con.executemany(
                "INSERT INTO alert_outbox(fingerprint, channel, code, severity, status,"
                " attempts, next_attempt_at, created_at, updated_at, payload)"
                " VALUES (?, 'operator', ?, 'WARNING', 'PENDING', 0, '', ?, ?, '{}')",
                [(f"fp_{prefix}_{pending}_{i}", f"{prefix}_{i}",
                  "2026-09-26T08:00:00+00:00", "2026-09-26T08:00:00+00:00")
                 for i in range(pending)])
            con.commit()
        finally:
            con.close()

    def _drain_to_healthy(self, path):
        """Test-side drain: keep ~8 backlog rows so health leaves CRITICAL."""
        from operations.alert_outbox import _connect
        con = _connect(path)
        try:
            con.execute("DELETE FROM alert_outbox WHERE code LIKE 'LOAD_%' AND id % 25 <> 0")
            con.commit()
        finally:
            con.close()

    def test_critical_backlog_raises_stalled_incident(self, tmp_path):
        from operations.alert_outbox import (
            CONSUMER_STALLED_CODE, claim, report_consumer_incident, status)

        live = str(tmp_path / "journal.db")
        self._seed_backlog(live, pending=210)  # >200 -> CRITICAL
        result = report_consumer_incident(live)
        assert result["health_status"] == "CRITICAL"
        assert result["raised"] is True
        assert result["execution_authority"] is False
        counts = status(live)["counts"]
        assert counts["PENDING"] == 211  # backlog + the incident row
        # claim() orders by id, so the incident row (last) needs a full claim.
        claimed = claim(live, limit=250)
        incident = [a for a in claimed if a["code"] == CONSUMER_STALLED_CODE]
        assert incident and incident[0]["severity"] == "CRITICAL"
        assert incident[0]["payload"]["details"]["pending"] == 210

    def test_repeat_while_ringing_refreshes_not_spams(self, tmp_path):
        from operations.alert_outbox import report_consumer_incident, status

        live = str(tmp_path / "journal.db")
        self._seed_backlog(live, pending=210)
        assert report_consumer_incident(live)["raised"] is True
        for _ in range(3):  # dispatcher self-report re-fires every minute
            again = report_consumer_incident(live)
            assert again["refreshed"] is True and again["raised"] is False
        counts = status(live)["counts"]
        assert counts["PENDING"] == 211  # exactly one incident row

    def test_cooldown_suppresses_after_ack(self, tmp_path):
        """After delivery, a fresh raise is dampened within the cooldown."""
        from operations.alert_outbox import (
            acknowledge, claim, report_consumer_incident, status)

        live = str(tmp_path / "journal.db")
        self._seed_backlog(live, pending=210)
        assert report_consumer_incident(live)["raised"] is True
        # Test-side: isolate the incident row, deliver it, re-seed the backlog.
        con = _connect_isolated(live)
        item = claim(live, limit=250)
        stalled = next(a for a in item if a["code"] == "ALERT_CONSUMER_STALLED")
        acknowledge(live, stalled["id"], delivered=True)
        # Drain everything else so only the SENT incident remains, then
        # re-create a CRITICAL backlog.
        from operations.alert_outbox import _connect
        con = _connect(live)
        try:
            con.execute("DELETE FROM alert_outbox WHERE code LIKE 'LOAD_%'")
            con.commit()
        finally:
            con.close()
        self._seed_backlog(live, prefix="RELOAD", pending=210)
        result = report_consumer_incident(live)
        assert result["health_status"] == "CRITICAL"
        assert result["suppressed"] is True
        counts = status(live)["counts"]
        assert counts["PENDING"] == 210  # no second incident row

    def test_recovery_enqueues_resumed_notice(self, tmp_path):
        from operations.alert_outbox import (
            CONSUMER_RESUMED_CODE, _connect, report_consumer_incident, status)

        live = str(tmp_path / "journal.db")
        self._seed_backlog(live, pending=210)
        assert report_consumer_incident(live)["raised"] is True
        self._drain_to_healthy(live)
        result = report_consumer_incident(live)
        assert result["health_status"] in ("OK", "WARN")
        assert result["resolved"] is True
        con = _connect(live)
        try:
            n = con.execute("SELECT COUNT(*) FROM alert_outbox WHERE code=?",
                            (CONSUMER_RESUMED_CODE,)).fetchone()[0]
        finally:
            con.close()
        assert n == 1

    def test_resumed_notice_is_dampened_too(self, tmp_path):
        """Healthy-but-unresolved: RESUMED enqueued once, not every cadence."""
        from operations.alert_outbox import report_consumer_incident, status

        live = str(tmp_path / "journal.db")
        self._seed_backlog(live, pending=210)
        assert report_consumer_incident(live)["raised"] is True
        self._drain_to_healthy(live)
        assert report_consumer_incident(live)["resolved"] is True
        for _ in range(2):  # watchdog re-fires hourly
            again = report_consumer_incident(live)
            assert again.get("resolve_suppressed") is True
            assert again.get("resolved") is False
        counts = status(live)["counts"]
        # 8 LOAD + 1 STALLED (still ringing) + 1 RESUMED, never a pile.
        assert counts.get("PENDING", 0) == 10

    def test_healthy_outbox_is_silent(self, tmp_path):
        from operations.alert_outbox import report_consumer_incident, status

        live = str(tmp_path / "journal.db")
        self._seed_backlog(live, pending=5)
        result = report_consumer_incident(live)
        assert result["health_status"] == "OK"
        assert result["raised"] is False and result["resolved"] is False
        assert status(live)["counts"]["PENDING"] == 5  # nothing enqueued

    def test_fail_soft_on_bad_journal(self, tmp_path):
        from operations.alert_outbox import report_consumer_incident

        result = report_consumer_incident(str(tmp_path / "nope" / "x.db"))
        assert result["error"]
        assert result["execution_authority"] is False

    def test_dispatcher_self_report_in_return_summary(self, tmp_path, monkeypatch):
        """handle_alert_dispatch folds the incident result into its summary."""
        from agents import alert_agent
        from operations.alert_outbox import enqueue

        live = str(tmp_path / "journal.db")
        enqueue(live, [{"code": "ONE"}])
        monkeypatch.setattr(alert_agent, "DEFAULT_JOURNAL", live)
        summary = alert_agent.handle_alert_dispatch({}, None)
        assert summary["status"] == "OK"
        assert summary["incident"]["health_status"] == "OK"
        assert summary["sent"] == 1 and summary["claimed"] == 1

    def test_journal_maintenance_checkpoint_carries_watchdog(self, tmp_path, monkeypatch):
        """The hourly journal checkpoint raises the incident when CRITICAL."""
        import operations.agent_operations as ops_mod
        from agents import journal_agent

        live = str(tmp_path / "journal.db")
        self._seed_backlog(live, pending=210)

        class _StubOps:
            def __init__(self, *_a, **_k):
                pass

            def checkpoint_and_verify(self):
                return {"wal_checkpoint": {}, "integrity_ok": True}

        # journal_agent builds AgentOperations('trading_state.db') when no
        # context is given — stub it so the test never touches the real journal.
        monkeypatch.setattr(ops_mod, "AgentOperations", _StubOps)
        try:
            from operations import agent_operations as flat_ops_mod
            monkeypatch.setattr(flat_ops_mod, "AgentOperations", _StubOps)
        except ImportError:
            pass
        result = journal_agent.handle_journal_maintenance(
            {"kind": "checkpoint", "journal_path": live}, None)
        assert result["outbox_incident"]["health_status"] == "CRITICAL"
        assert result["outbox_incident"]["raised"] is True


def _connect_isolated(path):
    from operations.alert_outbox import _connect
    return _connect(path)


def test_journal_agent_dispatches_archive_outbox(tmp_path):
    """handle_journal_maintenance({'kind':'archive_outbox'}) calls archive_terminal."""
    from agents.journal_agent import handle_journal_maintenance
    from operations.alert_outbox import enqueue

    live = str(tmp_path / "journal.db")
    enqueue(live, [{"code": "X"}])
    result = handle_journal_maintenance({"kind": "archive_outbox", "journal_path": live}, None)
    assert result["archived"] == 0  # PENDING row is not terminal
    assert result["execution_authority"] is False
