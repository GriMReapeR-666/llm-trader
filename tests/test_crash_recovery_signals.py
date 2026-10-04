"""K4: signal handlers must only set a flag (AUDIT_2026-10-04_DEEP 06 F4).

The old handlers ran shutdown() inline from signal context: a SIGTERM landing
while GraphStatePersistence._lock was held deadlocked the process, and
otherwise the handler returned and the engine kept running (swallowed
SIGTERM).  The contract now:
  * the handler itself takes no locks and does no I/O — flag only;
  * a watcher thread performs the graceful shutdown;
  * afterwards the main thread is interrupted so the process actually ends.
"""
import signal
import threading
import time

import pytest


@pytest.fixture
def recovery(tmp_path):
    from core.graph_state_persistence import GraphStatePersistence
    from core.crash_recovery import CrashRecoveryManager

    persistence = GraphStatePersistence(str(tmp_path / "recovery.db"))
    mgr = CrashRecoveryManager(persistence=persistence, guardian_heartbeat_stale_seconds=5.0)
    # Never let the watcher interrupt pytest's main thread from these tests;
    # individual tests re-record this hook to observe it.
    mgr._after_signal_shutdown = lambda signum: None
    originals = dict(mgr._original_signal_handlers)
    yield mgr
    # Give pytest's real signal handlers back and stop the watcher.
    for sig, handler in originals.items():
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, TypeError):
            pass
    mgr.shutdown()


def test_flag_handler_returns_while_persistence_lock_held(recovery):
    """A signal landing mid-critical-section must not deadlock (K4)."""
    lock = threading.Lock()
    recovery.persistence._lock = lock
    assert lock.acquire(blocking=False)
    try:
        # Simulate the signal interrupting the main thread while it holds
        # the persistence lock.  The old handler would call shutdown() here
        # and deadlock on this very lock.
        recovery._signal_flag_handler(signal.SIGTERM, None)
    finally:
        lock.release()
    assert recovery._signal_requested == signal.SIGTERM


def test_shutdown_runs_on_watcher_thread_not_in_handler(recovery):
    """The handler must not perform shutdown; the watcher thread does."""
    seen_threads = []
    original_shutdown = recovery.shutdown

    def _record_and_shutdown():
        seen_threads.append(threading.current_thread().name)
        original_shutdown()

    recovery.shutdown = _record_and_shutdown
    after_calls = []
    recovery._after_signal_shutdown = lambda signum: after_calls.append(signum)

    recovery._signal_flag_handler(signal.SIGTERM, None)

    deadline = time.time() + 5.0
    while not (seen_threads and after_calls) and time.time() < deadline:
        time.sleep(0.05)
    assert seen_threads == ["shutdown-signal-watcher"]
    assert after_calls == [signal.SIGTERM]
    assert recovery._shutdown_event.is_set()


def test_after_signal_shutdown_interrupts_main_and_chains_original(monkeypatch):
    """After the flag-driven shutdown the process must actually end (no
    swallowed SIGTERM), and any replaced handler is chained first."""
    import core.crash_recovery as crash_recovery_mod

    mgr = crash_recovery_mod.CrashRecoveryManager.__new__(crash_recovery_mod.CrashRecoveryManager)
    chained = []
    interrupted = []
    mgr._original_signal_handlers = {signal.SIGINT: lambda s, f: chained.append((s, f))}
    monkeypatch.setattr(crash_recovery_mod._thread, "interrupt_main",
                        lambda: interrupted.append(True))

    mgr._after_signal_shutdown(signal.SIGINT)
    assert chained == [(signal.SIGINT, None)]
    assert interrupted == [True]

    # Default SIGTERM handler (SIG_DFL, not callable) → still interrupted.
    mgr2 = crash_recovery_mod.CrashRecoveryManager.__new__(crash_recovery_mod.CrashRecoveryManager)
    mgr2._original_signal_handlers = {signal.SIGTERM: signal.SIG_DFL}
    mgr2._after_signal_shutdown(signal.SIGTERM)
    assert interrupted == [True, True]
