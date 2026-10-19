"""P0-1/P0-3: live-state probe / sweep / PID-lease tests.

Covers the audit contract of operations/live_state_probe:

    * a DEAD pid is detected (the Windows GetExitCodeProcess path, which the
      first pass got backwards - it reported every terminated process as alive),
    * a stale lease is dead even when the pid number is live (pid reuse),
    * a live lease is honoured and REFUSES a second spawn,
    * sweep closes ghost RUNNING runs, downgrades a lying supervisor phase,
      releases expired schedule claims (including the never-leased rows whose
      claim_expires_at is NULL), and leaves the live claim untouched,
    * after the sweep the overdue schedule is immediately re-claimable through
      the EXISTING catch-up math (AgentOperations.claim_due_schedules), i.e.
      the sweep heals cadence instead of skipping the backlog.

Every test drives tmp paths: no real trading_state.db, engine.pid or
supervisor_state.json is read or written.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from operations.live_state_probe import (  # noqa: E402
    claim_pid_lease, engine_dead, pid_alive, pid_lease_stale, probe_live_state,
    read_pid_lease, refresh_pid_lease, sweep_live_state, write_pid_lease,
    PID_LEASE_MAX_AGE_S, SUPERVISOR_HEARTBEAT_GRACE_S, _supervisor_honesty,
)


class TestPidLiveness:
    def test_own_pid_is_alive(self):
        assert pid_alive(os.getpid()) is True

    def test_zero_and_negative_pid_rejected(self):
        assert pid_alive(0) is False
        assert pid_alive(-3) is False

    def test_exited_process_is_dead(self):
        """A pid whose process already exited is DEAD.

        This is the audit failure inverted: the first pass returned True for
        every process that had terminated (its `... and False or True`
        expression could only ever evaluate to True), which is precisely how a
        dead engine kept passing the single-instance guard.
        """
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        time.sleep(0.3)
        assert pid_alive(child.pid) is False


class TestPidLease:
    def test_lease_write_read_roundtrip(self, tmp_path):
        lease_path = str(tmp_path / "engine.pid")
        before = time.time()
        write_pid_lease(lease_path)
        lease = read_pid_lease(lease_path)
        assert lease["pid"] == os.getpid()
        assert lease["legacy"] is False
        assert lease["exists"] is True
        assert before - 1 <= lease["stamped_at"] <= time.time() + 1
        assert lease["created_at"] > 0        # OS process creation time recorded

    def test_legacy_plain_int_pid_file_accepted(self, tmp_path):
        lease_path = str(tmp_path / "engine.pid")
        with open(lease_path, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        lease = read_pid_lease(lease_path)
        assert lease["pid"] == os.getpid()
        assert lease["legacy"] is True
        # Legacy has no stamp -> never lease-stale; OS check is the truth.
        assert pid_lease_stale(lease) is False

    def test_stale_lease_is_treated_as_dead_even_if_pid_reused(self, tmp_path):
        lease_path = str(tmp_path / "engine.pid")
        with open(lease_path, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "stamped_at": time.time() - PID_LEASE_MAX_AGE_S - 60},
                      fh)
        lease = read_pid_lease(lease_path)
        assert pid_lease_stale(lease) is True
        verdict = engine_dead(lease_path)
        assert verdict["dead"] is True
        assert verdict["reason"] == "pid_lease_stale"

    def test_fresh_lease_with_foreign_creation_time_is_pid_reuse(self, tmp_path):
        """Fresh stamp + live pid + wrong process = the pid was recycled."""
        lease_path = str(tmp_path / "engine.pid")
        with open(lease_path, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "created_at": time.time() - 86400,
                       "stamped_at": time.time()}, fh)
        verdict = engine_dead(lease_path)
        assert verdict["dead"] is True
        assert verdict["reason"] == "pid_reused"

    def test_live_fresh_lease_not_dead(self, tmp_path):
        lease_path = str(tmp_path / "engine.pid")
        write_pid_lease(lease_path)
        verdict = engine_dead(lease_path)
        assert verdict["dead"] is False
        assert verdict["reason"] is None

    def test_missing_pid_file_means_dead(self, tmp_path):
        verdict = engine_dead(str(tmp_path / "nope.pid"))
        assert verdict["dead"] is True
        assert verdict["reason"] == "no_pid_file"
        assert verdict["pid"] is None

    def test_empty_pid_file_means_dead(self, tmp_path):
        lease_path = tmp_path / "engine.pid"
        lease_path.write_text("")
        verdict = engine_dead(str(lease_path))
        assert verdict["dead"] is True
        assert verdict["reason"] == "empty_pid_file"

    def test_dead_pid_lease_detected(self, tmp_path):
        """The audit's own artifact shape: fresh stamp, pid long gone."""
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        lease_path = tmp_path / "engine.pid"
        lease_path.write_text(json.dumps({"pid": child.pid, "stamped_at": time.time()}))
        verdict = engine_dead(str(lease_path))
        assert verdict["dead"] is True
        assert verdict["reason"] == "pid_not_alive"


class TestLeaseRefreshAndClaim:
    def test_refresh_keeps_a_long_running_engine_alive(self, tmp_path):
        """The lease must be re-stamped, or a healthy engine goes 'dead' at
        PID_LEASE_MAX_AGE_S and the watchdog kills it."""
        lease_path = str(tmp_path / "engine.pid")
        write_pid_lease(lease_path)
        # age the stamp past the lease window, as a 30-min-old engine would be
        with open(lease_path, "w", encoding="utf-8") as fh:
            stale = read_pid_lease(lease_path)
            stale["stamped_at"] = time.time() - PID_LEASE_MAX_AGE_S - 5
            fh.write(json.dumps(stale))
        assert engine_dead(lease_path)["dead"] is True
        assert refresh_pid_lease(lease_path) is True
        assert engine_dead(lease_path)["dead"] is False

    def test_refresh_refuses_when_another_owner_took_the_lease(self, tmp_path):
        lease_path = tmp_path / "engine.pid"
        lease_path.write_text(json.dumps({"pid": 424242, "stamped_at": time.time()}))
        assert refresh_pid_lease(str(lease_path)) is False

    def test_live_lease_refuses_a_second_spawn(self, tmp_path):
        """P0-3: single-instance liveness is a lease, not a file."""
        lease_path = tmp_path / "engine.pid"
        # Hold the lease with a real live process that is not the watchdog, so
        # the refusal comes from the OS check and not from a missing file.
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            write_pid_lease(str(lease_path), pid=holder.pid)
            verdict = claim_pid_lease(str(lease_path))
            assert verdict["acquired"] is False
            assert verdict["reason"] == "held_by_live_engine"
            assert verdict["pid"] == holder.pid
            # The holder's lease must be untouched by the refused claimant.
            assert read_pid_lease(str(lease_path))["pid"] == holder.pid
        finally:
            holder.kill()
            holder.wait()

    def test_claim_takes_over_a_dead_lease(self, tmp_path):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        lease_path = tmp_path / "engine.pid"
        lease_path.write_text(json.dumps({"pid": child.pid, "stamped_at": time.time()}))
        verdict = claim_pid_lease(str(lease_path))
        assert verdict["acquired"] is True
        assert verdict["took_over"] is True
        assert read_pid_lease(str(lease_path))["pid"] == os.getpid()

    def test_claim_on_empty_path_is_acquired(self, tmp_path):
        verdict = claim_pid_lease(str(tmp_path / "engine.pid"))
        assert verdict["acquired"] is True
        assert verdict["took_over"] is False


def _make_journal(path: str) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE agent_schedules ("
        "schedule_name TEXT PRIMARY KEY, agent TEXT, interval_seconds INTEGER,"
        " next_run_at TEXT, last_run_at TEXT, enabled INTEGER DEFAULT 1,"
        " payload TEXT DEFAULT '{}', claimed_at TEXT, claim_expires_at TEXT)")
    con.execute(
        "CREATE TABLE agent_runs (run_id TEXT PRIMARY KEY, agent TEXT,"
        " started_at TEXT, heartbeat_at TEXT, status TEXT, config_hash TEXT,"
        " code_version TEXT, payload TEXT)")
    now = datetime.now(timezone.utc)
    # Stale claim whose lease expired 1 h ago.
    con.execute(
        "INSERT INTO agent_schedules VALUES (?, 'A', 60, ?, ?, 1, '{}', ?, ?)",
        ("stale_sched", (now - timedelta(hours=3)).isoformat(),
         (now - timedelta(hours=2)).isoformat(),
         (now - timedelta(hours=2)).isoformat(),
         (now - timedelta(hours=1)).isoformat()))
    # Stale claim with NO lease at all (claim_expires_at IS NULL): the shape the
    # first pass counted but never cleared.
    con.execute(
        "INSERT INTO agent_schedules VALUES (?, 'A', 60, ?, ?, 1, '{}', ?, NULL)",
        ("unleased_sched", (now - timedelta(hours=3)).isoformat(),
         (now - timedelta(hours=2)).isoformat(),
         (now - timedelta(hours=2)).isoformat()))
    # One live claim that must survive the sweep.
    con.execute(
        "INSERT INTO agent_schedules VALUES (?, 'A', 60, ?, ?, 1, '{}', ?, ?)",
        ("live_sched", (now + timedelta(minutes=1)).isoformat(),
         (now - timedelta(seconds=10)).isoformat(),
         now.isoformat(),
         (now + timedelta(minutes=15)).isoformat()))
    # One ghost RUNNING run with old heartbeat.
    con.execute(
        "INSERT INTO agent_runs VALUES (?, 'trader', ?, ?, 'RUNNING', '', '', '{}')",
        (f"ghost-{uuid.uuid4().hex[:8]}",
         (now - timedelta(hours=5)).isoformat(),
         (now - timedelta(hours=5)).isoformat()))
    con.commit()
    con.close()


def _supervisor_file(tmp_path, phase="RUNNING", heartbeat=0.0):
    path = tmp_path / "supervisor_state.json"
    path.write_text(json.dumps({
        "phase": phase, "booted_at": 0.0, "last_heartbeat": heartbeat,
        "components": {}, "principals": {}, "events": []}))
    return str(path)


class TestProbeAndSweep:
    def test_probe_reports_stale_and_ghost(self, tmp_path):
        db = str(tmp_path / "probe.db")
        _make_journal(db)
        probe = probe_live_state(db, str(tmp_path / "engine.pid"),
                                 _supervisor_file(tmp_path))
        assert probe["stale_claims"] == 2
        assert "stale_sched" in probe["stale_claim_names"]
        assert "unleased_sched" in probe["stale_claim_names"]
        assert probe["ghost_runs"] == 1
        assert probe["overdue_schedules"] >= 2   # the Oct-3 style backlog

    def test_sweep_releases_only_stale_claims(self, tmp_path, caplog):
        db = str(tmp_path / "sweep.db")
        _make_journal(db)
        with caplog.at_level("WARNING"):
            result = sweep_live_state(db, str(tmp_path / "engine.pid"),
                                      _supervisor_file(tmp_path))
        assert result["released_claims"] == 2
        assert result["recovered_runs"] == 1
        # Warning log lines: the anti-silent-failure guarantee.
        warn_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("stale schedule claim" in m.lower() for m in warn_messages)
        assert any("ghost agent_runs" in m.lower() for m in warn_messages)
        # Verify SQL effect: stale released (expired AND unleased), live untouched.
        con = sqlite3.connect(db)
        claimed = {row[0] for row in con.execute(
            "SELECT schedule_name FROM agent_schedules WHERE claimed_at IS NOT NULL")}
        con.close()
        assert "stale_sched" not in claimed
        assert "unleased_sched" not in claimed
        assert "live_sched" in claimed

    def test_sweep_closes_ghost_running_runs(self, tmp_path):
        db = str(tmp_path / "ghost.db")
        _make_journal(db)
        sweep_live_state(db, str(tmp_path / "engine.pid"),
                         _supervisor_file(tmp_path))
        con = sqlite3.connect(db)
        statuses = dict(con.execute("SELECT run_id, status FROM agent_runs"))
        con.close()
        assert list(statuses.values()) == ["STALE_RECOVERED"]

    def test_sweep_downgrades_lying_supervisor(self, tmp_path):
        """P0-1: phase RUNNING with last_heartbeat 0.0 must stop claiming RUNNING.

        The first pass delegated this to Supervisor.request_shutdown(), which is
        an ILLEGAL transition (the kernel restores no phase on load, so it sits
        at OFFLINE) and persisted nothing - while still reporting recovered=True.
        """
        sup_path = _supervisor_file(tmp_path, phase="RUNNING", heartbeat=0.0)
        result = sweep_live_state(str(tmp_path / "none.db"), str(tmp_path / "engine.pid"),
                                  sup_path)
        assert result["supervisor"]["recovered"] is True
        data = json.loads(open(sup_path, encoding="utf-8").read())
        assert data["phase"] == "DEGRADED"
        assert data["last_heartbeat"] == 0.0      # forensic evidence preserved
        assert any(e.get("kind") == "PHASE_FORCED" for e in data["events"])

    def test_sweep_downgrades_supervisor_older_than_two_cadences(self, tmp_path):
        sup_path = _supervisor_file(
            tmp_path, phase="RUNNING",
            heartbeat=time.time() - (SUPERVISOR_HEARTBEAT_GRACE_S + 60))
        sweep_live_state(str(tmp_path / "none.db"), str(tmp_path / "engine.pid"), sup_path)
        assert json.loads(open(sup_path, encoding="utf-8").read())["phase"] == "DEGRADED"

    def test_honest_supervisor_is_left_alone(self, tmp_path):
        fresh = _supervisor_file(tmp_path, phase="RUNNING", heartbeat=time.time())
        assert sweep_live_state(str(tmp_path / "none.db"),
                                str(tmp_path / "engine.pid"), fresh)["supervisor"]["recovered"] is False
        assert json.loads(open(fresh, encoding="utf-8").read())["phase"] == "RUNNING"

        stopped = _supervisor_file(tmp_path, phase="STOPPED", heartbeat=0.0)
        honesty = _supervisor_honesty(stopped)
        assert honesty["stale"] is False          # honest idle, not a lie

    def test_sweep_never_touches_the_real_state_files(self, tmp_path):
        """Every path is injected: repo engine.pid / supervisor_state.json are
        not consulted, so a test run cannot heal (or corrupt) live state."""
        db = str(tmp_path / "iso.db")
        _make_journal(db)
        probe = probe_live_state(db, str(tmp_path / "engine.pid"),
                                 _supervisor_file(tmp_path))
        assert probe["engine"]["dead"] is True
        assert probe["engine"]["reason"] == "no_pid_file"
        assert probe["supervisor"]["exists"] is True

    def test_probe_on_missing_db_is_safe(self, tmp_path):
        probe = probe_live_state(str(tmp_path / "nonexistent.db"),
                                 str(tmp_path / "engine.pid"),
                                 str(tmp_path / "none.json"))
        assert probe["stale_claims"] == 0
        assert probe["ghost_runs"] == 0
        assert probe["supervisor"]["stale"] is False


class TestCadenceSelfHeal:
    def test_swept_schedule_refires_through_the_existing_catchup_math(self, tmp_path):
        """The sweep must not invent cadence math: after releasing the claim,
        the EXISTING claim_due_schedules catch-up (agent_operations ~917-919)
        must immediately re-claim the overdue row and report the missed runs."""
        from operations.agent_operations import AgentOperations

        db = str(tmp_path / "cadence.db")
        _make_journal(db)
        sweep_live_state(db, str(tmp_path / "engine.pid"), _supervisor_file(tmp_path))

        ops = AgentOperations(db)
        due = {item["schedule_name"]: item for item in ops.claim_due_schedules()}
        assert "stale_sched" in due, "released claim must be re-claimable at once"
        assert due["stale_sched"]["missed_runs"] >= 1, "catch-up math ran"
        assert due["stale_sched"]["schedule_state"] == "ON_CADENCE"
        # A live claim held by another worker is not stolen.
        assert "live_sched" not in due