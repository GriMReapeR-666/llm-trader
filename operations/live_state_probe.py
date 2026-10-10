"""Boot-time liveness probe + stale-state sweep.

Closes the audited P0-1 failure class: the scheduler loop died (Oct 3-8)
while ``engine.pid``, ``supervisor_state.json`` (phase RUNNING,
last_heartbeat 0.0) and ghost ``agent_runs`` rows all kept claiming the
engine was alive. Because every downstream executor is try/except-guarded,
no alarm ever fired - the whole learning chain starved silently.

Three operations, deliberately kept separable so tests exercise each alone:

``pid_alive(pid)``        OS-level process liveness (Windows + POSIX).
``probe_live_state(...)`` Read-only truth snapshot: PID lease freshness,
                          supervisor heartbeat honesty, stale schedule
                          claims, overdue schedules, ghost agent_runs.
``sweep_live_state(...)`` Recovery surface: releases expired schedule
                          claims, delegates the supervisor to an honest
                          phase, recovers ghost agent_runs. Cross-checks
                          every finding with a WARNING log so this
                          failure category can never be silent again.

``engine.pid`` is a LEASE, not a file: ``{pid, created_at, stamped_at}``.
``created_at`` is the OS process creation time, which is what defeats pid
reuse (Windows recycles pids; a bare pid number proves nothing). The
running engine re-stamps ``stamped_at`` from a daemon heartbeat thread
(``start_pid_lease_heartbeat``) - a lease written once and never
refreshed would be exactly as stale as the plain-int file it replaced.
Any lease older than ``PID_LEASE_MAX_AGE_S`` is therefore dead by
construction: nobody re-stamped it, so nobody is running.

Every path-taking function resolves its defaults late (``x or PID_FILE``)
so tests and callers can redirect state without patching module globals.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.distributed_lock import TradeLock

logger = logging.getLogger("LiveStateProbe")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID_FILE = os.path.join(_REPO_ROOT, "engine.pid")
PID_LEASE_MAX_AGE_S = 30 * 60          # 30 min. The heartbeat re-stamps every
                                       # 60 s, so 30x of slack still means a
                                       # crash: nobody can be alive and mute.
PID_LEASE_HEARTBEAT_S = 60
SUPERVISOR_STATE_FILE = os.path.join(_REPO_ROOT, "supervisor_state.json")
SUPERVISOR_HEARTBEAT_GRACE_S = 2 * 300  # 2x the slowest principal heartbeat age

#: Phases that assert the engine is running. OFFLINE/STOPPED are honest
#: already - they claim nothing, so they are never "stale" (and an operator
#: who stopped the engine on purpose must never be auto-restarted).
_LIVE_PHASES = ("BOOTING", "PREFLIGHT", "WARMUP", "RUNNING", "DEGRADED", "PAUSED",
                "SHUTDOWN_REQUESTED")
_HONEST_IDLE_PHASES = ("OFFLINE", "STOPPED")


# ---------------------------------------------------------------------------
# PID liveness
# ---------------------------------------------------------------------------
_kernel32 = None


class _FILETIME(ctypes.Structure):
    """64-bit Windows FILETIME.

    It must NOT be declared as c_ulong: on Windows (LLP64) c_ulong is 4 bytes,
    so GetProcessTimes writes 8 bytes into a 4-byte buffer and ctypes raises an
    access violation - which looks exactly like "the process is not there".
    """
    _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                ("dwHighDateTime", ctypes.c_uint32)]


def _win_kernel32():
    """kernel32 with explicit prototypes.

    The prototypes are load-bearing, not decoration: ctypes defaults a
    function's restype to c_int (32-bit), which silently TRUNCATES the 64-bit
    HANDLE OpenProcess returns and makes every later call fail - which is how
    a "live" process can be misreported as dead.
    """
    global _kernel32
    if _kernel32 is None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.CloseHandle.restype = ctypes.c_int
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.GetExitCodeProcess.restype = ctypes.c_int
        k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_ulong)]
        k32.GetProcessTimes.restype = ctypes.c_int
        k32.GetProcessTimes.argtypes = [ctypes.c_void_p] + \
            [ctypes.POINTER(_FILETIME)] * 4
        _kernel32 = k32
    return _kernel32


def _windows_process_info(pid: int) -> Tuple[bool, Optional[float]]:
    """``(alive, creation_epoch)`` for *pid* on Windows.

    OpenProcess/PROCESS_QUERY_LIMITED_INFORMATION needs no dependency.  The
    exit code is the real liveness signal: 259 (STILL_ACTIVE) means the
    process object is still executing, ANY other value means it has already
    terminated - a pid whose process exited minutes ago is dead even though
    the pid entry has not been recycled yet.

    ``ponytail: creation_epoch is Windows-only. POSIX keeps pid-only
    identity and relies on the lease stamp; upgrade path is
    /proc/<pid>/stat field 22 when a POSIX host ever runs this engine.
    """
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        STILL_ACTIVE = 259
        kernel32 = _win_kernel32()

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # Access denied = the process exists but belongs to another
            # account (SYSTEM services). Exists => not a stale artifact.
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED, None
        try:
            exit_code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True, None      # cannot disprove liveness -> assume alive
            if exit_code.value != STILL_ACTIVE:
                return False, None     # terminated: a reused pid must not pass
            created = _FILETIME()
            exited = _FILETIME()
            kernel = _FILETIME()
            user = _FILETIME()
            # All four out-params get real buffers: ctypes raises an access
            # violation when NULL is passed for a POINTER output argument.
            if kernel32.GetProcessTimes(handle, ctypes.byref(created),
                                        ctypes.byref(exited), ctypes.byref(kernel),
                                        ctypes.byref(user)):
                # FILETIME: 100 ns ticks since 1601-01-01.
                ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
                return True, (ticks / 1e7) - 11644473600.0
            return True, None
        finally:
            kernel32.CloseHandle(handle)
    except Exception as exc:                      # pragma: no cover - defensive
        logger.debug("windows process probe failed for %s: %s", pid, exc)
        return False, None


def _posix_process_info(pid: int) -> Tuple[bool, Optional[float]]:
    try:
        os.kill(pid, 0)                             # signal 0 = existence probe
        return True, None
    except ProcessLookupError:
        return False, None
    except PermissionError:
        return True, None                           # exists, not ours
    except OSError:
        return False, None


def process_info(pid: int) -> Tuple[bool, Optional[float]]:
    """``(alive, creation_epoch)``. *creation_epoch* is None when the OS
    cannot report it; callers must treat None as "cannot verify identity"."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False, None
    if pid <= 0:
        return False, None
    if os.name == "nt":
        return _windows_process_info(pid)
    return _posix_process_info(pid)


def pid_alive(pid: int) -> bool:
    """True when *pid* is a live process on this host.

    Windows uses OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) via ctypes -
    zero new dependencies, and never os.kill(pid, 0) (whose meaning on
    Windows is not "does it exist"). POSIX uses os.kill(pid, 0), which
    raises ProcessLookupError for a dead pid and PermissionError for one
    owned by another user.
    """
    return process_info(pid)[0]


# ---------------------------------------------------------------------------
# PID lease
# ---------------------------------------------------------------------------
def read_pid_lease(path: Optional[str] = None) -> Dict[str, Any]:
    """Read engine.pid. Accepts both the legacy plain-int format and the
    lease format ``{"pid": int, "created_at": float, "stamped_at": float}``."""
    path = path or PID_FILE
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        if not raw:
            return {"pid": None, "created_at": 0.0, "stamped_at": 0.0,
                    "legacy": False, "exists": True}
        try:
            return {"pid": int(raw), "created_at": 0.0, "stamped_at": 0.0,
                    "legacy": True, "exists": True}
        except ValueError:
            data = json.loads(raw)
            return {
                "pid": int(data.get("pid") or 0) or None,
                "created_at": float(data.get("created_at") or 0.0),
                "stamped_at": float(data.get("stamped_at") or 0.0),
                "legacy": False,
                "exists": True,
            }
    except FileNotFoundError:
        return {"pid": None, "created_at": 0.0, "stamped_at": 0.0,
                "legacy": False, "exists": False}
    except (OSError, ValueError, TypeError):
        # Unreadable/corrupt: treat as an artifact (dead), never as liveness.
        return {"pid": None, "created_at": 0.0, "stamped_at": 0.0,
                "legacy": False, "exists": True}


def write_pid_lease(path: Optional[str] = None, pid: Optional[int] = None) -> Dict[str, Any]:
    """Publish *pid*'s lease atomically (os.replace: a concurrent probe can
    never observe a half-written lease and mistake it for an empty file)."""
    path = path or PID_FILE
    pid = os.getpid() if pid is None else int(pid)
    payload = {
        "pid": pid,
        "created_at": process_info(pid)[1] or 0.0,
        "stamped_at": time.time(),
    }
    tmp = "%s.tmp" % path
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)
    return payload


def refresh_pid_lease(path: Optional[str] = None) -> bool:
    """Re-stamp the lease, but ONLY while this process still owns it.

    A heartbeat that resurrects a lease someone else took over would keep a
    dead engine looking alive forever - the exact lie this module exists to
    kill - so a foreign owner refuses the stamp.
    """
    path = path or PID_FILE
    lease = read_pid_lease(path)
    if lease.get("pid") not in (None, os.getpid()):
        logger.warning("pid lease %s taken over by pid %s; heartbeat stopped",
                       path, lease.get("pid"))
        return False
    if not lease.get("exists"):
        return False
    write_pid_lease(path)
    return True


_heartbeat_threads: Dict[str, threading.Thread] = {}
_heartbeat_lock = threading.Lock()


def start_pid_lease_heartbeat(path: Optional[str] = None,
                              interval_s: float = PID_LEASE_HEARTBEAT_S) -> Optional[threading.Thread]:
    """Keep the lease fresh for as long as this process lives.

    A daemon thread (not the trading loop) so the lease cannot be starved by
    a blocked cycle and cannot keep the interpreter alive at shutdown. Idempotent
    per path: repeated calls return the already-running thread.
    """
    path = path or PID_FILE
    with _heartbeat_lock:
        existing = _heartbeat_threads.get(path)
        if existing is not None and existing.is_alive():
            return existing

        def _beat() -> None:
            while True:
                time.sleep(max(5.0, float(interval_s)))
                try:
                    if not refresh_pid_lease(path):
                        return          # ownership lost: stop pretending
                except Exception as exc:                # pragma: no cover
                    logger.debug("pid lease heartbeat skipped: %s", exc)

        thread = threading.Thread(target=_beat, name="pid-lease-heartbeat",
                                  daemon=True)
        thread.start()
        _heartbeat_threads[path] = thread
        return thread


def pid_lease_stale(lease: Dict[str, Any], now: Optional[float] = None) -> bool:
    """True when the PID lease can no longer be trusted (too old)."""
    stamped = float(lease.get("stamped_at") or 0.0)
    if stamped <= 0.0:
        return False          # legacy plain-int file: no stamp, rely on OS check
    now = now if now is not None else time.time()
    return (now - stamped) > PID_LEASE_MAX_AGE_S


def engine_dead(path: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Single source of truth for "engine is dead". Returns
    ``{"dead": bool, "reason": str|None, "pid": int|None}``.

    Order matters: freshness first (a stale lease is dead regardless of what
    the pid says), then liveness, then identity.
    """
    path = path or PID_FILE
    lease = read_pid_lease(path)
    pid = lease.get("pid")
    if pid is None:
        return {"dead": True, "reason": "no_pid_file" if not lease.get("exists")
                else "empty_pid_file", "pid": None}
    if pid_lease_stale(lease, now):
        return {"dead": True, "reason": "pid_lease_stale", "pid": pid}
    alive, created = process_info(pid)
    if not alive:
        return {"dead": True, "reason": "pid_not_alive", "pid": pid}
    recorded = float(lease.get("created_at") or 0.0)
    if recorded and created and abs(recorded - created) > 2.0:
        # Fresh lease, live pid, wrong process: the pid was recycled after the
        # engine died. Windows reuses pids aggressively.
        return {"dead": True, "reason": "pid_reused", "pid": pid}
    return {"dead": False, "reason": None, "pid": pid}


def claim_pid_lease(path: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Take single-instance ownership, or refuse.

    The audit's P0-3: "single-instance liveness is a stale file, not a lease".
    Refusal is the whole safety property - a second engine thrashes the
    journal DB and races the broker (observed as 2 GB twin processes), so a
    live holder must win. The O_EXCL file lock from ``core.distributed_lock``
    serialises the check-then-write across processes; the write is then
    re-read so a racing loser sees it lost instead of silently stomping.
    """
    path = path or PID_FILE
    with TradeLock("engine_lease", timeout=5.0):
        prior = engine_dead(path, now)
        if not prior["dead"] and prior["pid"] != os.getpid():
            return {"acquired": False, "reason": "held_by_live_engine",
                    "pid": prior["pid"]}
        took_over = bool(read_pid_lease(path).get("exists"))
        write_pid_lease(path)
        owner = read_pid_lease(path).get("pid")
        if owner != os.getpid():
            return {"acquired": False, "reason": "lost_claim_race", "pid": owner}
        return {"acquired": True, "took_over": took_over, "reason": prior["reason"],
                "pid": os.getpid()}


# ---------------------------------------------------------------------------
# Read-only probe
# ---------------------------------------------------------------------------
def _supervisor_honesty(path: Optional[str] = None,
                        now: Optional[float] = None) -> Dict[str, Any]:
    """A live-looking phase with a heartbeat older than 2x cadence is a lie.

    The audited artifact was exactly this: phase RUNNING, last_heartbeat 0.0,
    no freshness check anywhere - so operations looked alive for six days.
    """
    path = path or SUPERVISOR_STATE_FILE
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"phase": None, "heartbeat": None, "heartbeat_age_s": None,
                "stale": False, "exists": False}
    if not isinstance(data, dict):
        return {"phase": None, "heartbeat": None, "heartbeat_age_s": None,
                "stale": False, "exists": True}
    phase = data.get("phase")
    hb = float(data.get("last_heartbeat") or 0.0)
    now = now if now is not None else time.time()
    if phase in _HONEST_IDLE_PHASES or not phase:
        return {"phase": phase, "heartbeat": hb, "heartbeat_age_s": None,
                "stale": False, "exists": True}
    age = (now - hb) if hb > 0.0 else None
    stale = (hb <= 0.0) or (age is not None and age > SUPERVISOR_HEARTBEAT_GRACE_S)
    return {"phase": phase, "heartbeat": hb,
            "heartbeat_age_s": round(age, 1) if age is not None else None,
            "stale": bool(stale), "exists": True}


def _read_only(db_path: str, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    """Read-only SQLite query. Read-only so a probe can never corrupt the
    journal it is inspecting (URI-encoded: '#'/'?' in a path would otherwise
    truncate the path and silently query the wrong database)."""
    import sqlite3
    from pathlib import Path
    rows: List[Dict[str, Any]] = []
    try:
        uri = "%s?mode=ro" % Path(os.path.abspath(db_path)).as_uri()
        con = sqlite3.connect(uri, uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(sql, params)]
        finally:
            con.close()
    except Exception as exc:
        logger.debug("read-only probe query skipped (%s): %s", sql.split()[1], exc)
    return rows


def _stale_claim_query(db_path: str, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Schedule claims whose lease has expired (or was never given a lease).

    ``claim_expires_at IS NULL`` on a claimed row is the case the audit hit:
    a claim whose lease column never got written stays claimed forever
    because nothing can expire it.
    """
    from datetime import datetime, timezone
    return _read_only(
        db_path,
        "SELECT schedule_name, claimed_at, claim_expires_at FROM agent_schedules "
        "WHERE claimed_at IS NOT NULL AND (claim_expires_at IS NULL OR claim_expires_at <= ?)"
        " ORDER BY schedule_name",
        (datetime.now(timezone.utc).isoformat(),),
    )


def _overdue_schedules(db_path: str) -> List[Dict[str, Any]]:
    """Enabled schedules whose due point has passed and which nothing holds.

    These are the ones that re-fire on the very next claim: the catch-up math
    in ``agent_operations.claim_due_schedules`` (lines ~917-919) collapses the
    missed intervals, so the sweep deliberately does NOT rewrite next_run_at -
    rewriting it would skip the backlog instead of replaying it.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    return _read_only(
        db_path,
        "SELECT schedule_name, next_run_at FROM agent_schedules "
        "WHERE enabled=1 AND next_run_at<=? AND (claim_expires_at IS NULL OR claim_expires_at<=?)"
        " ORDER BY next_run_at",
        (now, now),
    )


def _ghost_runs(db_path: str) -> List[Dict[str, Any]]:
    """RUNNING rows whose heartbeat stopped - ghost runs from a dead worker."""
    from datetime import datetime, timezone
    return _read_only(
        db_path,
        "SELECT run_id, heartbeat_at FROM agent_runs "
        "WHERE status='RUNNING' AND heartbeat_at < ? ORDER BY heartbeat_at",
        (datetime.now(timezone.utc).isoformat(),),
    )


def probe_live_state(db_path: str = "trading_state.db",
                     pid_path: Optional[str] = None,
                     supervisor_path: Optional[str] = None) -> Dict[str, Any]:
    """Read-only truth snapshot. Returns
    ``{"engine": ..., "supervisor": ..., "stale_claims": n, "ghost_runs": n, ...}``."""
    engine = engine_dead(pid_path)
    supervisor = _supervisor_honesty(supervisor_path)
    stale = _stale_claim_query(db_path)
    overdue = _overdue_schedules(db_path)
    ghosts = _ghost_runs(db_path)
    return {
        "engine": engine,
        "supervisor": supervisor,
        "stale_claims": len(stale),
        "stale_claim_names": [r.get("schedule_name") for r in stale],
        "overdue_schedules": len(overdue),
        "overdue_schedule_names": [r.get("schedule_name") for r in overdue],
        "ghost_runs": len(ghosts),
        "ghost_run_ids": [r.get("run_id") for r in ghosts],
    }


# ---------------------------------------------------------------------------
# Recovery sweep
# ---------------------------------------------------------------------------
def _release_stale_claims(db_path: str) -> int:
    """Release expired schedule claims so claim_due_schedules can re-claim.

    Audit root cause: claimed rows that nothing can expire hold the cadence
    hostage. Recovery clears only the lease - it does NOT re-stamp
    next_run_at, because the claim-time catch-up math then replays the whole
    missed backlog instead of skipping it. Delegated to
    ``AgentOperations.release_schedule_claims`` so the release stays
    claim-token guarded (a superseded claim can never clear a newer one).
    """
    rows = _stale_claim_query(db_path)
    if not rows:
        return 0
    try:
        from operations.agent_operations import AgentOperations
        ops = AgentOperations(db_path)
        return int(ops.release_schedule_claims(rows))
    except Exception as exc:
        logger.warning("stale claim release failed: %s", exc)
        return 0


def _downgrade_supervisor(path: Optional[str] = None) -> Dict[str, Any]:
    """Stop the supervisor claiming to run when it cannot prove a heartbeat.

    Written straight to the state file on purpose: ``Supervisor`` restores
    no phase on load (it starts at OFFLINE), so ``request_shutdown`` from a
    fresh kernel is an ILLEGAL transition OFFLINE -> SHUTDOWN_REQUESTED and
    silently persists nothing. The previous pass reported
    ``recovered: True`` for exactly that no-op. ``last_heartbeat`` is left
    untouched - it is the forensic evidence of the lie.
    """
    path = path or SUPERVISOR_STATE_FILE
    honesty = _supervisor_honesty(path)
    if not honesty.get("stale"):
        return {"recovered": False, "reason": "honest_or_absent"}
    try:
        from core.file_utils import safe_atomic_write_json
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        previous = data.get("phase")
        data["phase"] = "DEGRADED"
        events = list(data.get("events") or [])
        events.append({
            "ts": time.time(),
            "kind": "PHASE_FORCED",
            "from": previous,
            "to": "DEGRADED",
            "why": ("live_state_probe: phase %s with last_heartbeat=%s"
                    % (previous, honesty.get("heartbeat"))),
        })
        data["events"] = events[-200:]
        safe_atomic_write_json(path, data)
        logger.warning("Supervisor downgraded RUNNING->DEGRADED by live_state_probe "
                       "(heartbeat=%s, age=%ss)", honesty.get("heartbeat"),
                       honesty.get("heartbeat_age_s"))
        return {"recovered": True, "reason": "stale_heartbeat",
                "from": previous, "to": "DEGRADED"}
    except Exception as exc:
        logger.warning("Supervisor recovery failed (probe can only flag it): %s", exc)
        return {"recovered": False, "reason": str(exc)}


def _recover_agent_runs(db_path: str) -> int:
    """Delegate ghost RUNNING runs to recover_stale_runs so it marks them
    STALE_RECOVERED durably (already implemented in agent_operations)."""
    try:
        from operations.agent_operations import AgentOperations
        ops = AgentOperations(db_path)
        return int(ops.recover_stale_runs())
    except Exception as exc:
        logger.warning("ghost agent_runs recovery failed: %s", exc)
        return 0


def sweep_live_state(db_path: str = "trading_state.db",
                     pid_path: Optional[str] = None,
                     supervisor_path: Optional[str] = None) -> Dict[str, Any]:
    """Recovery surface. Probes, logs loudly, then heals what it can.

    Returns ``{"probe": ..., "released_claims": n, "recovered_runs": n,
              "supervisor": {...}}`` so the caller can decide whether it's
    safe to boot another engine instance.
    """
    probe = probe_live_state(db_path, pid_path, supervisor_path)
    engine = probe.get("engine") or {}

    if engine.get("pid") is not None and engine.get("dead"):
        logger.warning("engine.pid %s is DEAD (%s) - prior engine instance died "
                       "without cleanup; stale state will be swept",
                       engine.get("pid"), engine.get("reason"))

    supervisor = _downgrade_supervisor(supervisor_path)
    released = _release_stale_claims(db_path)
    if released:
        logger.warning("Released %d stale schedule claim(s): %s", released,
                       probe.get("stale_claim_names", []))
    runs = _recover_agent_runs(db_path)
    if runs:
        logger.warning("Recovered %d ghost agent_runs (heartbeat-stale RUNNING rows)", runs)
    if probe.get("overdue_schedules"):
        logger.warning("%d schedule(s) are overdue and will re-fire on the next "
                       "claim wave: %s", probe["overdue_schedules"],
                       probe.get("overdue_schedule_names", [])[:10])
    return {
        "probe": probe,
        "released_claims": released,
        "recovered_runs": runs,
        "overdue_schedules": probe.get("overdue_schedules", 0),
        "supervisor": supervisor,
    }