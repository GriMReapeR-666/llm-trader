"""Regression: the GUI single-instance guard must survive the engine.pid lease.

`engine.pid` stopped being a bare integer when P0-3 made it a heartbeat lease
(`{pid, created_at, stamped_at}`; see `operations/live_state_probe.py`). The
GUI still parsed it with `int(open(path).read())`, and the surrounding
`except (ValueError, OSError): pass` turned that ValueError into "there is no
engine" — so the guard that exists to prevent a duplicate engine was silently
disabled, and `start_trading` would happily spawn a twin against a live book.

Both call sites now route through `live_state_probe.engine_dead()`, which
tolerates the legacy plain-int format, checks lease freshness, and detects pid
reuse. These tests pin that property so the next refactor cannot reintroduce a
bare-int parse of a JSON file.

No test here spawns an engine or touches the real `engine.pid`: every path is
tmp, and the liveness verdict is driven off a real short-lived child process
so "is this pid alive" is answered by the OS rather than by a mock.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from operations.live_state_probe import (
    engine_dead,
    read_pid_lease,
    write_pid_lease,
)


@pytest.fixture
def live_child():
    """A real running child process, so pid liveness is answered by the OS."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield proc
    finally:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception:
            pass


def test_json_lease_is_not_readable_as_a_bare_int(tmp_path):
    """The premise of the regression: a lease is JSON, so int() must fail."""
    lease_path = tmp_path / "engine.pid"
    lease_path.write_text(json.dumps({"pid": 4242, "created_at": 1.0, "stamped_at": 2.0}))

    with pytest.raises(ValueError):
        int(lease_path.read_text().strip())

    # ...and the reader understands it regardless.
    assert read_pid_lease(str(lease_path))["pid"] == 4242


def test_legacy_plain_int_lease_is_still_understood(tmp_path):
    """A pre-lease engine.pid must keep working; the guard is not lease-only."""
    lease_path = tmp_path / "engine.pid"
    lease_path.write_text("12345")

    assert read_pid_lease(str(lease_path))["pid"] == 12345


def test_engine_dead_reports_a_live_lease_as_not_dead(tmp_path, live_child):
    lease_path = str(tmp_path / "engine.pid")
    write_pid_lease(lease_path, pid=live_child.pid)

    verdict = engine_dead(lease_path)
    assert verdict["dead"] is False
    assert verdict["pid"] == live_child.pid


def test_engine_dead_reports_an_exited_pid_as_dead(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=10)
    lease_path = str(tmp_path / "engine.pid")
    write_pid_lease(lease_path, pid=proc.pid)

    verdict = engine_dead(lease_path)
    assert verdict["dead"] is True
    assert verdict["reason"] == "pid_not_alive"


def test_absent_lease_is_dead_not_an_error(tmp_path):
    verdict = engine_dead(str(tmp_path / "engine.pid"))
    assert verdict["dead"] is True
    assert verdict["pid"] is None


def test_gui_spawn_guard_uses_the_probe_not_a_bare_int():
    """Guard against reintroducing `int(open(engine.pid).read())`.

    Grep-level tripwire: the old pattern failed *silently* because the ValueError
    was swallowed, so no runtime test would notice its return. Asserting the
    call sites simply do not exist is the only cheap regression net.
    """
    gui_api = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "gui_api.py"
    )
    source = open(gui_api, encoding="utf-8").read()

    assert 'int(open(_pid_file' not in source, (
        "gui_api parses engine.pid as a bare int; engine.pid is a JSON lease"
    )
    assert source.count("engine_dead(") >= 2, (
        "both the spawn guard and the stop endpoint must route through engine_dead()"
    )


def test_spawn_guard_does_not_fail_open_on_probe_error(tmp_path, monkeypatch):
    """An undecidable liveness must refuse the spawn, never permit it.

    The original defect was a fail-open: any parse error became "no engine,
    proceed". A false refusal is recoverable; a twin engine racing a live book
    is not.
    """
    import ui.gui_api as gui_api

    def _boom(_path):
        raise OSError("probe exploded")

    monkeypatch.setattr("operations.live_state_probe.engine_dead", _boom)

    # start_trading builds its command before reaching the guard, so drive the
    # guard's contract directly: with the probe broken it must not report that
    # the engine is absent.
    result = _invoke_guard_with_broken_probe(gui_api, monkeypatch)
    assert result.get("status") == "probe_failed"
    assert result.get("pid") is None


def _invoke_guard_with_broken_probe(gui_api, monkeypatch):
    """Call start_trading with a broken probe and prove it never spawns."""
    import subprocess as real_subprocess

    import ui.gui_api as api

    def _fake_popen(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("start_trading spawned a process despite a broken probe")

    # start_trading does `import subprocess` locally, so patch the real module
    # object rather than a name on gui_api.
    monkeypatch.setattr(real_subprocess, "Popen", _fake_popen)
    return api.start_trading({"continuous": False, "loops": 1})


def test_stale_lease_is_dead_even_when_the_pid_is_alive(tmp_path, live_child):
    """A frozen engine must read as dead, or the guard blocks a restart forever."""
    lease_path = tmp_path / "engine.pid"
    lease = write_pid_lease(str(lease_path), pid=live_child.pid)
    # Rewind the stamp past the freshness window without touching the pid.
    lease["stamped_at"] = float(lease["stamped_at"]) - 10_000
    lease_path.write_text(json.dumps(lease))

    verdict = engine_dead(str(lease_path))
    assert verdict["dead"] is True
    assert verdict["reason"] == "pid_lease_stale"
    # Sanity: the pid itself is still running, so staleness is what decided it.
    assert engine_dead(str(lease_path))["pid"] == live_child.pid
    assert time.time() > 0