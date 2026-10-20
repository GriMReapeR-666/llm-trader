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

from operations.keepalive_watchdog import (  # noqa: E402
    ENGINE_ARGV,
    ENGINE_ENV_DEFAULTS,
    KeepAliveWatchdog,
)
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
    kwargs.setdefault("stop_flag_path", str(tmp_path / "stop.flag"))
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

# ---------------------------------------------------------------------------
# Boot parity, stale-hang recovery, RAM bar, stop flag
# ---------------------------------------------------------------------------

class TestBootParity:
    def test_restart_argv_matches_operator_auto_boot(self):
        # llm-trade has no --no-tui flag; the old argv died on argparse.
        assert "--no-tui" not in ENGINE_ARGV
        assert ENGINE_ARGV[ENGINE_ARGV.index("--symbol") + 1] == "AUTO"
        assert ENGINE_ARGV[ENGINE_ARGV.index("--timeframe") + 1] == "AUTO"
        assert "--paper" in ENGINE_ARGV
        assert "--continuous" in ENGINE_ARGV
        assert ENGINE_ENV_DEFAULTS["DELIBERATIVE_GRAPH_SHADOW"] == "1"
        assert ENGINE_ENV_DEFAULTS["TRADE_PLANNER_MODE"] == "enforce"


def _spawn_dummy(extra_argv):
    """A real process whose command line can impersonate the engine."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"] + list(extra_argv))


def _write_stale_lease(tmp_path, pid, clock):
    (tmp_path / "engine.pid").write_text(json.dumps({
        "pid": pid, "created_at": 0.0, "stamped_at": clock() - 3600.0}))


class TestStaleHangRecovery:
    def test_stale_hung_engine_is_killed_before_restart(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        holder = _spawn_dummy(["mt5_trader.py", "llm-trade"])
        try:
            _write_stale_lease(tmp_path, holder.pid, clock)
            wd = _watchdog(tmp_path, spawn, clock, kill_stale=True)
            wd.arm(True)
            result = wd.check()
            assert result["action"] == "restarted"
            assert result["kill"]["killed"] is True
            assert result["kill"]["pid"] == holder.pid
            assert spawn.calls == 1
            holder.wait(timeout=10)
            assert holder.poll() is not None
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)

    def test_stale_foreign_pid_is_never_killed(self, tmp_path):
        """A recycled PID is not ours just because the lease names it."""
        clock = FakeClock()
        spawn = SpawnRecorder()
        holder = _spawn_dummy([])
        try:
            _write_stale_lease(tmp_path, holder.pid, clock)
            wd = _watchdog(tmp_path, spawn, clock, kill_stale=True)
            wd.arm(True)
            result = wd.check()
            assert result["action"] == "kill_failed"
            assert result["kill"]["reason"] == "identity_mismatch"
            assert spawn.calls == 0
            assert holder.poll() is None
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)

    def test_dry_run_does_not_kill(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        holder = _spawn_dummy(["mt5_trader.py", "llm-trade"])
        try:
            _write_stale_lease(tmp_path, holder.pid, clock)
            wd = _watchdog(tmp_path, spawn, clock, kill_stale=True)
            wd.arm(True)
            result = wd.check(dry_run=True)
            assert result["action"] == "would_restart"
            assert result["would_kill"] is True
            assert spawn.calls == 0
            assert holder.poll() is None
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)


class TestRamBar:
    def test_ram_bar_blocks_spawn_but_still_kills_stale(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        holder = _spawn_dummy(["mt5_trader.py", "llm-trade"])
        ram = {"gb": 0.7}
        try:
            _write_stale_lease(tmp_path, holder.pid, clock)
            wd = _watchdog(tmp_path, spawn, clock, kill_stale=True,
                           min_free_ram_gb=1.5, ram_reader=lambda: ram["gb"])
            wd.arm(True)
            result = wd.check()
            assert result["action"] == "ram_blocked"
            assert result["free_gb"] == 0.7
            assert result["kill"]["killed"] is True
            assert spawn.calls == 0
            assert wd._load()["attempts"] == 0
            holder.wait(timeout=10)

            ram["gb"] = 1.6
            result = wd.check()
            assert result["action"] == "restarted"
            assert spawn.calls == 1
            assert wd._load()["attempts"] == 1
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)

    def test_unreadable_ram_fails_closed(self, tmp_path):
        def boom():
            raise RuntimeError("no psutil")

        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock, min_free_ram_gb=1.5,
                       ram_reader=boom)
        wd.arm(True)
        assert wd.check()["action"] == "ram_blocked"
        assert spawn.calls == 0

    def test_dry_run_reports_ram_block_without_spawning(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock, min_free_ram_gb=1.5,
                       ram_reader=lambda: 0.5)
        wd.arm(True)
        result = wd.check(dry_run=True)
        assert result["action"] == "would_restart"
        assert result["ram_ok"] is False
        assert result["free_gb"] == 0.5
        assert spawn.calls == 0


class TestStopFlag:
    def test_stop_flag_disarms_and_vetoes_every_spawn(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        (tmp_path / "stop.flag").write_text("stop")
        wd = _watchdog(tmp_path, spawn, clock)
        wd.arm(True)
        result = wd.check()
        assert result["action"] == "stop_flag_disarmed"
        assert result["stop_flag"] == str(tmp_path / "stop.flag")
        assert wd._load()["desired"] is False
        assert spawn.calls == 0
        # The engine retires stop.flag to .consumed; the watchdog stays off.
        (tmp_path / "stop.flag").rename(tmp_path / "stop.flag.consumed")
        assert wd.check()["action"] == "disarmed"
        assert spawn.calls == 0

    def test_trader_stop_flag_fallback_also_disarms(self, tmp_path):
        clock = FakeClock()
        spawn = SpawnRecorder()
        (tmp_path / "trader_stop.flag").write_text("stop")
        wd = _watchdog(tmp_path, spawn, clock)
        wd.arm(True)
        result = wd.check()
        assert result["action"] == "stop_flag_disarmed"
        assert result["stop_flag"] == str(tmp_path / "trader_stop.flag")
        assert spawn.calls == 0

    def test_stop_file_env_override_also_disarms(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_stop.flag"
        custom.write_text("stop")
        monkeypatch.setenv("MT5_TRADER_STOP_FILE", str(custom))
        clock = FakeClock()
        spawn = SpawnRecorder()
        wd = _watchdog(tmp_path, spawn, clock)
        wd.arm(True)
        result = wd.check()
        assert result["action"] == "stop_flag_disarmed"
        assert result["stop_flag"] == str(custom)
        assert spawn.calls == 0
