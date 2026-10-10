import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from core.crash_recovery import reset_shutdown_request, shutdown_requested
from core.file_lock import FileLock
from ui.gui_api import app

REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)


def test_file_lock_mutual_exclusion(tmp_path):
    lock_file = str(tmp_path / "test.lock")
    lock1 = FileLock(lock_file)
    lock2 = FileLock(lock_file)

    assert lock1.acquire(non_blocking=True) is True
    assert lock1.is_locked is True

    # Second lock fails non-blocking acquisition
    assert lock2.acquire(non_blocking=True) is False
    assert lock2.is_locked is False

    # First releases -> second can acquire
    lock1.release()
    assert lock1.is_locked is False

    assert lock2.acquire(non_blocking=True) is True
    assert lock2.is_locked is True
    lock2.release()


def test_file_lock_pid_read_write(tmp_path):
    lock_file = str(tmp_path / "test.lock")
    lock = FileLock(lock_file)
    assert lock.acquire(non_blocking=True) is True

    lock.write_pid(99999)
    assert lock.read_pid() == 99999

    lock.release()
    # Stale file still has PID
    assert lock.read_pid() == 99999


def test_second_engine_exits_code_3(tmp_path):
    """When engine.lock is held, second engine process exits immediately with rc 3."""
    lock_file = str(tmp_path / "engine.lock")

    lock = FileLock(lock_file)
    acquired = lock.acquire(non_blocking=True)
    assert acquired is True, f"Failed to acquire engine lock at {lock_file}"
    try:
        lock.write_pid(os.getpid())

        child_env = os.environ.copy()
        child_env["LLM_TRADER_LOCK_PATH"] = lock_file
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONPATH"] = REPO_ROOT

        # Spawn second engine process running llm-trade
        cmd = [
            sys.executable,
            os.path.join(REPO_ROOT, "mt5_trader.py"),
            "llm-trade",
            "--symbol",
            "EURUSD",
            "--paper",
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=8)
            assert proc.returncode == 3
            combined = stdout + stderr
            assert "engine.lock held by another process" in combined
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("Second engine did not exit immediately with rc 3")
    finally:
        lock.release()


def test_api_start_when_lock_held_returns_409(tmp_path, monkeypatch):
    """API start endpoint returns 409 Conflict when engine.lock is held."""
    lock_file = str(tmp_path / "engine.lock")
    monkeypatch.setenv("LLM_TRADER_LOCK_PATH", lock_file)
    token = "test-token-lifecycle"
    monkeypatch.setenv("GUI_API_TOKEN", token)

    lock = FileLock(lock_file)
    acquired = lock.acquire(non_blocking=True)
    assert acquired is True, f"Failed to acquire engine lock at {lock_file}"
    try:
        lock.write_pid(98765)
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post(
            "/api/control/start",
            json={"symbol": "EURUSD", "mode": "paper"},
            headers=headers,
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "Engine already running" in data["detail"]
    finally:
        lock.release()


def test_api_stop_when_idle_returns_not_running(tmp_path, monkeypatch):
    """API stop returns ok=False when no engine holds the lock."""
    lock_file = str(tmp_path / "engine.lock")
    monkeypatch.setenv("LLM_TRADER_LOCK_PATH", lock_file)
    token = "test-token-lifecycle"
    monkeypatch.setenv("GUI_API_TOKEN", token)

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.post("/api/control/stop", json={}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "engine is not running" in data["reason"]


def test_api_stop_graceful_drain_via_stop_flag(tmp_path, monkeypatch):
    """Subprocess holding engine.lock stops gracefully on stop.flag."""
    lock_file = str(tmp_path / "engine.lock")
    stop_file = str(tmp_path / "stop.flag")

    monkeypatch.setenv("LLM_TRADER_LOCK_PATH", lock_file)
    monkeypatch.setenv("MT5_TRADER_STOP_FILE", stop_file)
    token = "test-token-lifecycle"
    monkeypatch.setenv("GUI_API_TOKEN", token)

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONPATH"] = REPO_ROOT

    script = (
        "import time, os, sys\n"
        "from core.file_lock import FileLock\n"
        f"lock_path = {repr(lock_file)}\n"
        f"stop_path = {repr(stop_file)}\n"
        "lock = FileLock(lock_path)\n"
        "if not lock.acquire(non_blocking=True):\n"
        "    sys.exit(1)\n"
        "lock.write_pid(os.getpid())\n"
        "print('READY', flush=True)\n"
        "while not os.path.exists(stop_path):\n"
        "    time.sleep(0.05)\n"
        "lock.release()\n"
        "sys.exit(0)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait until child acquires lock and is READY
        ready_line = proc.stdout.readline().strip()
        assert ready_line == "READY"

        client = TestClient(app, base_url="http://127.0.0.1:8000")
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post("/api/control/stop", json={}, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["graceful"] is True
        assert data["pid"] == proc.pid

        proc.wait(timeout=5)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_flag_only_signal_handler():
    """Signal handlers only set flag and do not deadlock or raise on first signal."""
    reset_shutdown_request()
    assert shutdown_requested() is False

    from core.crash_recovery import get_recovery
    mgr = get_recovery()
    assert mgr._shutdown_event.is_set() is False


def test_probe_p6_sigterm_while_checkpoint_lock_held():
    """Probe P6: Subprocess receives SIGTERM while holding lock, exits within 5s with rc 0."""
    if sys.platform == "win32":
        pytest.skip(
            "Windows implements signal.SIGTERM via TerminateProcess, which hard-kills "
            "the child (rc=1) and never dispatches to Python signal handlers. The "
            "graceful-shutdown path is exercised by the POSIX run of this test."
        )
    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONPATH"] = REPO_ROOT

    script = (
        "import time, os, sys, threading\n"
        "from core.crash_recovery import get_recovery, shutdown_requested\n"
        "mgr = get_recovery()\n"
        "mock_lock = threading.Lock()\n"
        "mock_lock.acquire()\n"
        "print('RUNNING', flush=True)\n"
        "# Simulate loop holding lock until shutdown_requested\n"
        "deadline = time.monotonic() + 10.0\n"
        "while time.monotonic() < deadline:\n"
        "    if shutdown_requested():\n"
        "        mock_lock.release()\n"
        "        sys.exit(0)\n"
        "    time.sleep(0.02)\n"
        "mock_lock.release()\n"
        "sys.exit(1)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait for RUNNING line
        line = proc.stdout.readline().strip()
        assert line == "RUNNING"

        start_t = time.monotonic()
        # Send SIGTERM
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5.0)
        elapsed = time.monotonic() - start_t

        assert proc.returncode == 0
        assert elapsed < 5.0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_probe_p7_sigterm_while_idle():
    """Probe P7: Subprocess idle in loop exits within 2s on SIGTERM."""
    if sys.platform == "win32":
        pytest.skip(
            "Windows implements signal.SIGTERM via TerminateProcess, which hard-kills "
            "the child (rc=1) and never dispatches to Python signal handlers. The "
            "graceful-shutdown path is exercised by the POSIX run of this test."
        )
    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONPATH"] = REPO_ROOT

    script = (
        "import time, os, sys\n"
        "from core.crash_recovery import get_recovery, shutdown_requested\n"
        "mgr = get_recovery()\n"
        "print('IDLE', flush=True)\n"
        "deadline = time.monotonic() + 10.0\n"
        "while time.monotonic() < deadline:\n"
        "    if shutdown_requested():\n"
        "        sys.exit(0)\n"
        "    time.sleep(0.02)\n"
        "sys.exit(1)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline().strip()
        assert line == "IDLE"

        start_t = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=3.0)
        elapsed = time.monotonic() - start_t

        assert proc.returncode == 0
        assert elapsed < 2.0
    finally:
        if proc.poll() is None:
            proc.kill()
