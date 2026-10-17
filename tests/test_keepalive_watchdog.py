"""Remediation item 2: keep-alive watchdog tests.

The watchdog exists because the audited engine died on Oct 3 and nothing
noticed for six days. These tests pin the four properties that make it
safe to leave running unattended:

    * disarmed by default - it never fights an operator who stopped the engine,
    * a LIVE pid lease refuses the restart (no twin engines, ever),
    * restarts are bounded: a crash-loop costs at most max_attempts spawns,
    * it heals boot state before respawning, and it never writes the lease
      itself (a watchdog-written lease would fake the liveness it proves).

All state is tmp_path; no real engine is ever spawned (the spawner is injected).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from operations.keepalive_watchdog import KeepAliveWatchdog  # noqa: E402
from operations.live_state_probe import write_pid_lease  # noqa: E402


class FakeClock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SpawnRecorder:
    """Injected spawner: records the intent, never launches an engine."""

    def __init__(self, ok: bool = True):
        self.calls = 0
        self.ok = ok

    def __call__(self) -> bool:
        self.calls += 1
        return self.ok


def _watchdog(tmp_path, spawn, clock, **kwargs):
    return KeepAliveWatchdog(
        pid_path=str(tmp_path / "engine.pid"),
        state_path=str(tmp_path / "engine_watchdog.json"),
        db_path=str(tmp_path / "trading_state.db"),
        supervisor_path=str(tmp_path / "supervisor_state.json"),
        spawn=spawn,
        now=clock,
        sleep=lambda _s: None,
        **kwargs)


class TestOperatorIntent:
    def test_disarmed_by_default_never_spawns(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock)
        # No engine.pid at all -> dead engine; disarmed must still do nothing.
        assert wd.check()["action"] == "disarmed"
        assert wd.check()["action"] == "disarmed"
        assert spawn.calls == 0

    def test_arm_then_disarm_roundtrip(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock)
        assert wd.arm(True)["desired"] is True
        assert wd.check()["action"] in ("restarted", "would_restart")
        wd.arm(False)
        assert wd._load()["desired"] is False
        assert spawn.calls == 1

    def test_arming_clears_a_spent_budget(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock, max_attempts=1)
        wd.arm(True)
        wd.check()
        assert wd.check()["action"] == "exhausted"
        wd.arm(True)
        assert wd.check()["action"] == "restarted"


class TestLivenessGate:
    def test_live_pid_lease_refuses_a_restart(self, tmp_path):
        """A live engine must never be doubled up by the watchdog."""
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock)
        wd.arm(True)
        # Hold the lease with a real live process that is not the watchdog.
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            write_pid_lease(str(tmp_path / "engine.pid"), pid=holder.pid)
            # write_pid_lease stamps created_at for `holder.pid` (it is alive),
            # so the lease is fresh AND identifies the right process.
            result = wd.check()
            assert result["action"] == "alive"
            assert result["engine"]["pid"] == holder.pid
            assert spawn.calls == 0
        finally:
            holder.kill()
            holder.wait()

    def test_dead_engine_is_restarted_and_budget_recorded(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock)
        wd.arm(True)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        (tmp_path / "engine.pid").write_text(
            json.dumps({"pid": dead.pid, "stamped_at": clock()}))
        result = wd.check()
        assert result["action"] == "restarted"
        assert result["attempts"] == 1
        assert spawn.calls == 1
        state = wd._load()
        assert state["attempts"] == 1
        assert state["restarts"][-1]["reason"] == "pid_not_alive"

    def test_alive_engine_resets_the_budget(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock)
        wd.arm(True)
        wd.check()
        assert wd._load()["attempts"] == 1
        write_pid_lease(str(tmp_path / "engine.pid"))   # engine came back
        result = wd.check()
        assert result["action"] == "alive"
        assert result["attempts"] == 0
        assert wd._load()["attempts"] == 0


class TestBoundedRestarts:
    def test_crash_loop_is_bounded(self, tmp_path):
        """No infinite crash-loop: at most max_attempts spawns per episode."""
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock, max_attempts=3, base_backoff_s=1.0)
        wd.arm(True)
        actions = []
        for _ in range(12):
            actions.append(wd.check()["action"])
            clock.advance(120)          # past any backoff window
        assert spawn.calls == 3
        assert actions[-1] == "exhausted"
        assert "exhausted" in actions

    def test_backoff_gates_the_next_attempt(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock, max_attempts=5, base_backoff_s=60.0)
        wd.arm(True)
        assert wd.check()["action"] == "restarted"
        # Immediately after a restart we are in backoff: no second spawn.
        assert wd.check()["action"] == "backoff"
        assert spawn.calls == 1
        # After the backoff window the next attempt is allowed.
        clock.advance(61)
        assert wd.check()["action"] == "restarted"
        assert spawn.calls == 2

    def test_backoff_grows_and_is_capped(self, tmp_path):
        clock = FakeClock()
        wd = _watchdog(tmp_path, SpawnRecorder(), clock,
                       max_attempts=10, base_backoff_s=30.0, backoff_cap_s=120.0)
        assert wd.backoff_s(1) == 30.0
        assert wd.backoff_s(2) == 60.0
        assert wd.backoff_s(3) == 120.0
        assert wd.backoff_s(9) == 120.0          # capped


class TestBootSweepIntegration:
    def test_restart_sweeps_boot_state_first(self, tmp_path):
        """The respawn is a boot path: it must inherit honest state, not the
        ghost RUNNING runs / expired claims / lying phase of the corpse."""
        import sqlite3
        from datetime import datetime, timedelta, timezone

        db = tmp_path / "trading_state.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE agent_schedules (schedule_name TEXT PRIMARY KEY, agent TEXT,"
            " interval_seconds INTEGER, next_run_at TEXT, last_run_at TEXT,"
            " enabled INTEGER DEFAULT 1, payload TEXT DEFAULT '{}',"
            " claimed_at TEXT, claim_expires_at TEXT)")
        con.execute(
            "CREATE TABLE agent_runs (run_id TEXT PRIMARY KEY, agent TEXT, started_at TEXT,"
            " heartbeat_at TEXT, status TEXT, config_hash TEXT, code_version TEXT, payload TEXT)")
        now = datetime.now(timezone.utc)
        con.execute(
            "INSERT INTO agent_schedules VALUES ('dead_claim','A',60,?,?,1,'{}',?,?)",
            ((now - timedelta(hours=2)).isoformat(), (now - timedelta(hours=2)).isoformat(),
             (now - timedelta(hours=2)).isoformat(), (now - timedelta(hours=1)).isoformat()))
        con.execute(
            "INSERT INTO agent_runs VALUES ('ghost-run','trader',?,?,'RUNNING','','','{}')",
            ((now - timedelta(hours=5)).isoformat(), (now - timedelta(hours=5)).isoformat()))
        con.commit()
        con.close()
        (tmp_path / "supervisor_state.json").write_text(json.dumps(
            {"phase": "RUNNING", "last_heartbeat": 0.0, "events": []}))

        clock = FakeClock()
        wd = _watchdog(tmp_path, SpawnRecorder(), clock)
        wd.arm(True)
        result = wd.check()
        assert result["action"] == "restarted"
        sweep = result["sweep"]
        assert sweep["released_claims"] == 1
        assert sweep["recovered_runs"] == 1
        assert sweep["supervisor"]["recovered"] is True
        assert json.loads((tmp_path / "supervisor_state.json").read_text())["phase"] == "DEGRADED"

    def test_watchdog_never_writes_the_lease(self, tmp_path):
        """A watchdog-written lease would fake exactly the liveness it proves."""
        clock = FakeClock()
        wd = _watchdog(tmp_path, SpawnRecorder(), clock)
        wd.arm(True)
        wd.check()
        lease = tmp_path / "engine.pid"
        assert not lease.exists() or json.loads(lease.read_text()).get("pid") != os.getpid()


class TestDryRunAndCliSafety:
    def test_dry_run_never_spawns(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock)
        wd.arm(True)
        assert wd.check(dry_run=True)["action"] == "would_restart"
        assert spawn.calls == 0

    def test_max_attempts_must_be_positive(self, tmp_path):
        with pytest.raises(ValueError):
            _watchdog(tmp_path, SpawnRecorder(), FakeClock(), max_attempts=0)

    def test_corrupt_state_file_is_treated_as_disarmed(self, tmp_path):
        (tmp_path / "engine_watchdog.json").write_text("{not json")
        wd = _watchdog(tmp_path, SpawnRecorder(), FakeClock())
        assert wd.check()["action"] == "disarmed"