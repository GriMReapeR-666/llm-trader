from operations.agent_operations import AgentOperations
from datetime import datetime, timedelta, timezone
import sqlite3
import hashlib
import pytest


def test_task_and_order_idempotency(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    run = ops.start_run("test")
    assert ops.claim_task("candle:1", "scalp", "analyse", {}, run)
    assert not ops.claim_task("candle:1", "scalp", "analyse", {}, run)
    assert ops.create_order_intent("order:1", {"symbol": "EURUSD"}, run)[0]
    assert not ops.create_order_intent("order:1", {"symbol": "EURUSD"}, run)[0]


def test_order_intent_transitions_are_bounded_and_idempotent(tmp_path):
    ops = AgentOperations(str(tmp_path / "intent-transitions.db"))
    created, _ = ops.create_order_intent("order:transition", {"symbol": "EURUSD"})
    assert created
    assert ops.resolve_order_intent("order:transition", "PENDING") is True
    assert ops.resolve_order_intent("order:transition", "PARTIAL", ticket=12) is True
    assert ops.resolve_order_intent("order:transition", "PARTIAL", ticket=12) is False
    assert ops.resolve_order_intent("order:transition", "FILLED", ticket=12) is True
    with pytest.raises(ValueError, match="invalid order intent transition"):
        ops.resolve_order_intent("order:transition", "CANCELLED")
    with pytest.raises(ValueError, match="invalid order intent status"):
        ops.resolve_order_intent("order:transition", "MADE_UP")
    with pytest.raises(KeyError, match="unknown order intent"):
        ops.resolve_order_intent("missing", "PENDING")


def test_llm_budget_rejects_duplicate_trigger(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    permitted, request_id = ops.reserve_llm(strategy="scalp", provider="test", model="test", trigger_key="bar:1", max_per_hour=1, max_per_day=1)
    assert permitted
    ops.finish_llm(request_id, success=True)
    permitted, reason = ops.reserve_llm(strategy="scalp", provider="test", model="test", trigger_key="bar:1", max_per_hour=1, max_per_day=1)
    assert not permitted
    assert reason == "duplicate trigger"


def test_stale_llm_reservation_is_expired_and_concurrency_slot_recovers(tmp_path):
    ops = AgentOperations(str(tmp_path / "llm-reservation-recovery.db"))
    now = datetime.now(timezone.utc)
    allowed, request_id = ops.reserve_llm(
        strategy="scalp", provider="test", model="test", trigger_key="crash:1",
        max_per_hour=10, max_per_day=10, max_concurrent=1,
    )
    assert allowed
    old = (now - timedelta(seconds=AgentOperations.LLM_RESERVATION_MAX_AGE_SECONDS + 1)).isoformat()
    with ops._con() as con:
        con.execute("UPDATE llm_requests SET created_at=? WHERE request_id=?", (old, request_id))

    recovered = ops.recover_stale_llm_reservations(now=now)
    assert recovered == 1
    allowed, replacement = ops.reserve_llm(
        strategy="scalp", provider="test", model="test", trigger_key="crash:2",
        max_per_hour=10, max_per_day=10, max_concurrent=1,
    )
    assert allowed
    assert replacement != request_id
    with ops._con() as con:
        statuses = dict(con.execute("SELECT request_id,status FROM llm_requests").fetchall())
    assert statuses[request_id] == "EXPIRED"
    assert statuses[replacement] == "RESERVED"


def test_llm_reservation_recovery_rejects_invalid_lease_age(tmp_path):
    ops = AgentOperations(str(tmp_path / "llm-reservation-invalid.db"))
    with pytest.raises(ValueError):
        ops.recover_stale_llm_reservations(max_age_seconds=0)


def test_llm_budget_rejects_nonfinite_or_malformed_telemetry(tmp_path):
    ops = AgentOperations(str(tmp_path / "llm-inputs.db"))
    with pytest.raises(ValueError):
        ops.reserve_llm(strategy="scalp", provider="test", model="test", trigger_key="nan-cost",
                        estimated_cost=float("nan"))
    with pytest.raises(ValueError):
        ops.reserve_llm(strategy="scalp", provider="test", model="test", trigger_key="fractional",
                        estimated_tokens=1.5)
    allowed, request_id = ops.reserve_llm(strategy="scalp", provider="test", model="test", trigger_key="finish")
    assert allowed
    with pytest.raises(ValueError):
        ops.finish_llm(request_id, success=True, cost=float("inf"))
    with pytest.raises(ValueError):
        ops.finish_llm(request_id, success=True, tokens=1.2)


def test_llm_trace_fields_are_durable(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    permitted, request_id = ops.reserve_llm(
        strategy="scalp", provider="test", model="test", trigger_key="trace:1",
        max_per_hour=10, max_per_day=10,
    )
    assert permitted
    ops.finish_llm(
        request_id, success=True, latency_ms=12.5,
        prompt_hash="prompt-hash", response_hash="response-hash",
        attempt_count=2, parse_status="SUCCESS", response_chars=321,
    )
    with ops._con() as con:
        row = con.execute(
            "SELECT prompt_hash,response_hash,attempt_count,parse_status,response_chars,status "
            "FROM llm_requests WHERE request_id=?", (request_id,)
        ).fetchone()
    assert dict(row) == {
        "prompt_hash": "prompt-hash", "response_hash": "response-hash",
        "attempt_count": 2, "parse_status": "SUCCESS", "response_chars": 321,
        "status": "SUCCESS",
    }


def test_llm_trace_schema_migrates_existing_journal(tmp_path):
    path = tmp_path / "legacy-state.db"
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE llm_requests (
            request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, completed_at TEXT,
            run_id TEXT, strategy TEXT, provider TEXT, model TEXT, trigger_key TEXT,
            status TEXT NOT NULL, prompt_hash TEXT, response_hash TEXT,
            latency_ms REAL, tokens INTEGER, cost REAL, error TEXT, payload TEXT NOT NULL
        )""")
    AgentOperations(str(path))
    with sqlite3.connect(path) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(llm_requests)")}
    assert {"estimated_tokens", "estimated_cost", "attempt_count", "parse_status", "response_chars"} <= columns


def test_execution_review_escalates_by_durable_age_and_detection_count(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    assert ops.create_order_intent("review:1", {"symbol": "EURUSD"})[0]
    ops.flag_execution_review("review:1", "broker outcome missing")
    old = (datetime.now(timezone.utc) - timedelta(seconds=2_000)).isoformat()
    with ops._con() as con:
        con.execute(
            "UPDATE execution_reviews SET first_detected_at=?,last_detected_at=? WHERE intent_key=?",
            (old, old, "review:1"),
        )
    report = ops.escalate_execution_reviews(now=datetime.now(timezone.utc))
    assert report["execution_authority"] is False
    assert report["escalated"][0]["to"] == "CRITICAL"
    row = ops.execution_reviews()[0]
    assert row["severity"] == "CRITICAL"


def test_execution_review_retry_uses_bounded_backoff_without_resubmission(tmp_path):
    ops = AgentOperations(str(tmp_path / "retry.db"))
    assert ops.create_order_intent("retry:1", {"symbol": "EURUSD"})[0]
    ops.flag_execution_review("retry:1", "history unavailable")
    assert ops.execution_review_retry_state("retry:1") == "WAIT"
    due = datetime.now(timezone.utc) + timedelta(seconds=31)
    claimed = ops.claim_due_execution_review_retries(now=due, base_seconds=30, max_seconds=60)
    assert claimed[0]["intent_key"] == "retry:1"
    assert claimed[0]["retry_count"] == 1
    assert ops.execution_review_retry_state("retry:1", now=due) == "WAIT"
    with ops._con() as con:
        row = con.execute("SELECT status,retry_count FROM execution_reviews WHERE intent_key=?", ("retry:1",)).fetchone()
    assert row["status"] == "OPEN"
    assert row["retry_count"] == 1


def test_execution_review_retry_exhaustion_is_durable(tmp_path):
    ops = AgentOperations(str(tmp_path / "retry-exhaustion.db"))
    assert ops.create_order_intent("retry:2", {"symbol": "EURUSD"})[0]
    ops.flag_execution_review("retry:2", "history unavailable")
    now = datetime.now(timezone.utc) + timedelta(days=1)
    for _ in range(5):
        with ops._con() as con:
            con.execute("UPDATE execution_reviews SET next_retry_at=? WHERE intent_key=?", (now.isoformat(), "retry:2"))
        assert ops.claim_due_execution_review_retries(now=now)
    assert ops.execution_review_retry_state("retry:2", now=now) == "EXHAUSTED"
    assert ops.claim_due_execution_review_retries(now=now) == []


def test_raw_trace_redacts_inline_secrets_and_account_ids(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    trace_id = ops.record_raw_trace(
        "llm_prompt",
        {"content": "password=super-secret login=99023225 Bearer abc.def.ghi sk-1234567890123456"},  # secret-scan: allow
    )
    with ops._con() as con:
        payload = con.execute("SELECT payload FROM raw_traces WHERE trace_id=?", (trace_id,)).fetchone()["payload"]
    assert "super-secret" not in payload
    assert "99023225" not in payload
    assert "abc.def.ghi" not in payload
    assert "1234567890123456" not in payload
    assert "REDACTED" in payload


def test_operational_events_redact_payloads_and_store_stable_hash(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    ops.event("test", "credential_probe", {
        "password": "hidden", "content": "token=secret-value", "safe": "ok",
    })
    with ops._con() as con:
        row = con.execute(
            "SELECT payload,payload_hash FROM operational_events WHERE event_type=?",
            ("credential_probe",),
        ).fetchone()
    assert "hidden" not in row["payload"]
    assert "secret-value" not in row["payload"]
    assert "REDACTED" in row["payload"]
    assert row["payload_hash"] == hashlib.sha256(row["payload"].encode("utf-8")).hexdigest()


def test_point_in_time_evidence_tables_are_append_only(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    ops.event("test", "evidence", {"value": 1})
    snapshot_id = ops.store_snapshot("candle", {"close": 1.2}, symbol="EURUSD", timeframe="M15")
    version_id = ops.version_configuration("RiskPolicy", {"risk_fraction": 0.01})
    clock = ops.record_clock_observation("provider", 1_700_000_000)
    with ops._con() as con:
        event_id = con.execute("SELECT id FROM operational_events WHERE event_type='evidence'").fetchone()[0]
        for table, key, value in (
            ("operational_events", "id", event_id),
            ("data_snapshots", "snapshot_id", snapshot_id),
            ("configuration_versions", "version_id", version_id),
            ("clock_observations", "id", con.execute("SELECT id FROM clock_observations ORDER BY id DESC LIMIT 1").fetchone()[0]),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(f"UPDATE {table} SET payload=payload WHERE {key}=?", (value,))
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(f"DELETE FROM {table} WHERE {key}=?", (value,))


def test_clock_observation_rejects_malformed_thresholds(tmp_path):
    ops = AgentOperations(str(tmp_path / "clock.db"))
    with pytest.raises(ValueError):
        ops.record_clock_observation("provider", 1_700_000_000, max_offset_seconds=float("nan"))
    with pytest.raises(ValueError):
        ops.record_clock_observation("", 1_700_000_000)


def test_retention_status_is_read_only_and_fail_safe(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    ops.event("test", "retention", {"value": 1})
    report = ops.retention_status(older_than_days=1)
    assert report["execution_authority"] is False
    assert report["deletion"] == "DISABLED_BY_DEFAULT"
    assert report["tables"]["operational_events"]["total"] == 1
    with pytest.raises(ValueError):
        ops.retention_status(older_than_days=0)


def test_retention_status_degrades_missing_tables_without_crashing(tmp_path):
    """Harness-owned tables (harness_events etc.) are created by their owning
    components, so a fresh journal lacks them: retention_status must degrade
    the per-table entry instead of crashing the whole report."""
    ops = AgentOperations(str(tmp_path / "state.db"))
    ops.event("test", "retention", {"value": 1})
    report = ops.retention_status(older_than_days=1)

    missing = report["tables"]["harness_events"]
    assert missing["status"] == "UNAVAILABLE"
    assert missing["error"]  # human-readable reason, bounded
    # Operational tables this store owns are still fully reported (the one
    # event was just created, so it is inside a 1-day retention window).
    assert report["tables"]["operational_events"]["total"] == 1
    assert report["tables"]["operational_events"]["status"] == "WITHIN_RETENTION"
    # Overall report shape and fail-closed posture are unchanged.
    assert report["execution_authority"] is False
    assert report["deletion"] == "DISABLED_BY_DEFAULT"


def test_retention_status_degrades_table_with_wrong_timestamp_column(tmp_path):
    """A table that EXISTS but lacks its expected timestamp column (schema
    drift from another component) is the same degradation contract: one
    UNAVAILABLE entry, the rest of the report intact."""
    ops = AgentOperations(str(tmp_path / "state.db"))
    ops.event("test", "retention", {"value": 1})
    with ops._con() as con:
        # harness_events present but with a foreign schema (no 'timestamp').
        con.execute("CREATE TABLE harness_events (id INTEGER PRIMARY KEY, note TEXT)")
        con.execute("INSERT INTO harness_events(note) VALUES ('schema drift')")
    report = ops.retention_status(older_than_days=1)

    drifted = report["tables"]["harness_events"]
    assert drifted["status"] == "UNAVAILABLE"
    assert "timestamp" in drifted["error"].lower() or "no such column" in drifted["error"].lower()
    # The rest of the report is unaffected.
    assert report["tables"]["operational_events"]["total"] == 1
    assert report["tables"]["macro_observations"]["status"] == "UNAVAILABLE"
    assert report["execution_authority"] is False


def test_queue_backpressure_is_persisted_and_explicit(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    run = ops.start_run("test")
    assert ops.claim_task("queue:1", "test", "analyse", {}, run)
    healthy = ops.backpressure_status(max_running=2, max_retryable_errors=1)
    assert healthy["status"] == "HEALTHY"
    pressured = ops.backpressure_status(max_running=1, max_retryable_errors=1)
    assert pressured["status"] == "BACKPRESSURE"
    assert pressured["running_pressure"] is True
    assert pressured["execution_authority"] is False


def test_schedule_claim_preserves_cadence_and_reports_missed_runs(tmp_path):
    """Claim marks a lease WITHOUT advancing cadence (2026-09-27 contract);
    last_run_at/next_run_at advance only at complete_schedule_dispatch."""
    ops = AgentOperations(str(tmp_path / "schedule-cadence.db"))
    ops.register_schedule("health", "TechnicalNet", 60, {"kind": "health"})
    now = datetime.now(timezone.utc)
    scheduled = (now - timedelta(seconds=185)).isoformat()
    with ops._con() as con:
        con.execute("UPDATE agent_schedules SET next_run_at=? WHERE schedule_name=?", (scheduled, "health"))

    due = ops.claim_due_schedules()
    assert len(due) == 1
    assert due[0]["schedule_state"] == "ON_CADENCE"
    assert due[0]["scheduled_for"] == scheduled
    assert due[0]["missed_runs"] == 3
    with ops._con() as con:
        row = con.execute("SELECT last_run_at,next_run_at,claimed_at,claim_expires_at FROM agent_schedules WHERE schedule_name='health'").fetchone()
    assert row["next_run_at"] == scheduled            # cadence untouched at claim
    assert row["last_run_at"] is None
    assert row["claimed_at"] == due[0]["claimed_at"]  # lease held
    assert row["claim_expires_at"] > now.isoformat()
    assert ops.claim_due_schedules() == []             # lease excludes re-claim

    # Dispatch completion advances cadence from the ORIGINAL due point and
    # clears the claim.
    assert ops.complete_schedule_dispatch("health", due[0]["claimed_at"]) is True
    with ops._con() as con:
        row = con.execute("SELECT last_run_at,next_run_at,claimed_at FROM agent_schedules WHERE schedule_name='health'").fetchone()
    assert row["last_run_at"] is not None and row["last_run_at"] > now.isoformat()
    assert row["next_run_at"] > row["last_run_at"]
    assert row["claimed_at"] is None
    assert ops.claim_due_schedules() == []             # not due again yet


def test_schedule_claim_skipped_status_keeps_cadence_turn(tmp_path):
    """status='SKIPPED' releases the claim without advancing: the dispatch's
    cadence turn survives an abandoned dispatch (next wave re-claims)."""
    ops = AgentOperations(str(tmp_path / "schedule-skip.db"))
    ops.register_schedule("health", "TechnicalNet", 60)
    now = datetime.now(timezone.utc)
    with ops._con() as con:
        con.execute("UPDATE agent_schedules SET next_run_at=? WHERE schedule_name='health'",
                    ((now - timedelta(seconds=120)).isoformat(),))
    claim = ops.claim_due_schedules()[0]
    assert ops.complete_schedule_dispatch("health", claim["claimed_at"], "SKIPPED") is True
    with ops._con() as con:
        row = con.execute("SELECT next_run_at,claimed_at FROM agent_schedules WHERE schedule_name='health'").fetchone()
    assert row["claimed_at"] is None
    assert ops.claim_due_schedules(), "released row must be immediately re-claimable"


def test_schedule_claim_guards_superseded_tokens_and_expired_leases(tmp_path):
    """Completions and releases only act on the CURRENT claim token; an
    expired lease (crashed dispatch) is re-claimable with cadence intact."""
    ops = AgentOperations(str(tmp_path / "schedule-guard.db"))
    ops.register_schedule("health", "TechnicalNet", 60)
    now = datetime.now(timezone.utc)
    scheduled = (now - timedelta(seconds=120)).isoformat()
    with ops._con() as con:
        con.execute("UPDATE agent_schedules SET next_run_at=? WHERE schedule_name='health'", (scheduled,))

    claim = ops.claim_due_schedules()[0]
    # A wrong token (superseded/foreign claim) must be a no-op.
    assert ops.complete_schedule_dispatch("health", "stale-token") is False
    with ops._con() as con:
        row = con.execute("SELECT next_run_at,last_run_at,claimed_at FROM agent_schedules WHERE schedule_name='health'").fetchone()
    assert row["next_run_at"] == scheduled and row["last_run_at"] is None
    assert row["claimed_at"] == claim["claimed_at"]   # lease intact

    # Crashed dispatch: lease expiry makes the row claimable again at the
    # SAME due point, and the old token can no longer complete.
    with ops._con() as con:
        con.execute("UPDATE agent_schedules SET claim_expires_at=? WHERE schedule_name='health'",
                    ((now - timedelta(seconds=1)).isoformat(),))
    reclaimed = ops.claim_due_schedules()
    assert len(reclaimed) == 1 and reclaimed[0]["scheduled_for"] == scheduled
    assert reclaimed[0]["claimed_at"] != claim["claimed_at"]
    assert ops.complete_schedule_dispatch("health", claim["claimed_at"]) is False

    # Release honors the token too.
    assert ops.release_schedule_claims([{"schedule_name": "health",
                                         "claimed_at": "wrong"}]) == 0
    assert ops.release_schedule_claims([{"schedule_name": "health",
                                         "claimed_at": reclaimed[0]["claimed_at"]}]) == 1
    with ops._con() as con:
        row = con.execute("SELECT next_run_at,claimed_at FROM agent_schedules WHERE schedule_name='health'").fetchone()
    assert row["claimed_at"] is None and row["next_run_at"] == scheduled


def test_schedule_claim_and_release_ignore_malformed_records(tmp_path):
    ops = AgentOperations(str(tmp_path / "schedule-bad.db"))
    ops.register_schedule("health", "TechnicalNet", 60)
    # Incomplete records (missing identity fields) are skipped, not fatal.
    assert ops.release_schedule_claims([{"schedule_name": "health", "scheduled_for": "x"},
                                        "not-a-dict", None, {}]) == 0
    assert ops.complete_schedule_dispatch("health", "") is False
    assert ops.complete_schedule_dispatch("", "token") is False
    with pytest.raises(ValueError):
        ops.complete_schedule_dispatch("health", "token", "RUNNING")
    with pytest.raises(ValueError):
        ops.claim_due_schedules(lease_seconds=0)


def test_schedule_claim_recovers_malformed_timestamp_without_crashing(tmp_path):
    ops = AgentOperations(str(tmp_path / "schedule-malformed.db"))
    ops.register_schedule("health", "TechnicalNet", 60)
    with ops._con() as con:
        con.execute("UPDATE agent_schedules SET next_run_at=? WHERE schedule_name=?", ("not-a-time", "health"))
    # A malformed timestamp is not due under the SQL predicate, so place it in
    # the past to exercise the recovery branch.
    with ops._con() as con:
        con.execute("UPDATE agent_schedules SET next_run_at=? WHERE schedule_name=?", ("0000-00-00", "health"))
    due = ops.claim_due_schedules()
    assert due[0]["schedule_state"] == "MALFORMED_RECOVERED"
    with pytest.raises(ValueError):
        ops.claim_due_schedules(limit=0)


def test_run_finish_and_stale_recovery_are_durable(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    run = ops.start_run("test")
    assert ops.finish_run(run, status="COMPLETED", payload={"result": "ok"})
    assert not ops.finish_run(run, status="FAILED")
    with ops._con() as con:
        row = con.execute("SELECT status,payload FROM agent_runs WHERE run_id=?", (run,)).fetchone()
    assert row["status"] == "COMPLETED"
    assert '"result": "ok"' in row["payload"]

    abandoned = ops.start_run("crashed")
    old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    with ops._con() as con:
        con.execute("UPDATE agent_runs SET heartbeat_at=? WHERE run_id=?", (old, abandoned))
    assert ops.recover_stale_runs(max_age_seconds=300) == 1
    with ops._con() as con:
        row = con.execute("SELECT status FROM agent_runs WHERE run_id=?", (abandoned,)).fetchone()
    assert row["status"] == "STALE_RECOVERED"


def test_run_finish_accepts_stop_flag_status(tmp_path):
    # The live engine's graceful-stop path finalizes the durable run with
    # status="STOP_FLAG" (mt5_trader.py stop-file branch, pinned by
    # test_trader_stop_flag.py). finish_run must accept it as a terminal
    # status instead of raising ValueError during shutdown.
    ops = AgentOperations(str(tmp_path / "stop-flag.db"))
    run = ops.start_run("trader")
    assert ops.finish_run(run, status="STOP_FLAG")
    with ops._con() as con:
        row = con.execute("SELECT status FROM agent_runs WHERE run_id=?", (run,)).fetchone()
    assert row["status"] == "STOP_FLAG"
    # Unknown statuses still fail loudly.
    with pytest.raises(ValueError):
        ops.finish_run(ops.start_run("trader"), status="BOGUS")


def test_operations_summary_recovers_abandoned_runs_before_reporting(tmp_path):
    ops = AgentOperations(str(tmp_path / "summary-recovery.db"))
    abandoned = ops.start_run("crashed")
    old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    with ops._con() as con:
        con.execute("UPDATE agent_runs SET heartbeat_at=? WHERE run_id=?", (old, abandoned))
    summary = ops.summary()
    assert summary["stale_runs_recovered"] == 1
    assert summary["running_runs"] == 0
    assert summary["execution_authority"] is False


def test_working_memory_is_restart_safe_redacted_and_point_in_time(tmp_path):
    path = tmp_path / "working-memory.db"
    ops = AgentOperations(str(path))
    ops.put_working_memory("cycle", "position", "password=hidden", kind="position",  # secret-scan: allow
                           priority=90, source_ids=["trade:1"])
    cutoff = datetime.now(timezone.utc).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    ops.put_working_memory("cycle", "future", {"value": 2}, priority=10,
                           source_ids=["future:1"], expires_at=future)
    context = AgentOperations(str(path)).working_memory_context("cycle", as_of=cutoff)
    assert [item["memory_key"] for item in context["items"]] == ["position"]
    assert context["items"][0]["payload"] == "[REDACTED_SECRET]"
    assert context["execution_authority"] is False
    assert context["items"][0]["source_ids"] == ["trade:1"]


def test_working_memory_compaction_retains_safety_state_and_records_summary(tmp_path):
    ops = AgentOperations(str(tmp_path / "working-memory-compaction.db"))
    ops.put_working_memory("cycle", "risk", {"kill_switch": True}, kind="risk",
                           priority=100)
    ops.put_working_memory("cycle", "noise-1", {"value": 1}, priority=1)
    ops.put_working_memory("cycle", "noise-2", {"value": 2}, priority=1)
    result = ops.compact_working_memory("cycle", max_active=1)
    assert result["compacted"] == 2
    context = ops.working_memory_context("cycle")
    keys = {item["memory_key"] for item in context["items"]}
    assert "risk" in keys
    assert any(item["kind"] == "COMPACTION" for item in context["items"])
    status = ops.working_memory_status("cycle")
    assert any(row["status"] == "COMPACTED" and row["count"] == 2 for row in status["counts"])


def test_working_memory_can_merge_shared_event_namespace(tmp_path):
    ops = AgentOperations(str(tmp_path / "working-memory-shared.db"))
    ops.put_working_memory("trading:event:XAUUSD", "calendar", {"high_impact": 1},
                           kind="event", priority=80, source_ids=["macro:1"])
    context = ops.working_memory_context(
        "trading:scalp:XAUUSD", include_namespaces=["trading:event:XAUUSD"]
    )
    assert context["included_namespaces"] == ["trading:scalp:XAUUSD", "trading:event:XAUUSD"]
    assert context["items"][0]["namespace"] == "trading:event:XAUUSD"
