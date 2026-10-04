"""Crash Recovery — Detects crashes and orchestrates state restoration.

From UAQP:
    Γ_deployed = (Γ, Ψ)

    On startup:
      1. Check if last checkpoint was RUNNING (crash-interrupted)
      2. If crash detected: log warning, restore from last COMPLETED checkpoint
      3. Verify Ψ Guardian heartbeat — if stale, trip the guardian

    On shutdown (graceful):
      1. atexit handler snapshots state
      2. signal handlers (SIGTERM, SIGINT) trigger graceful shutdown

    Properties:
      - Fail-closed: any corruption → halt, don't trade with bad state
      - Independent of Γ: recovery reads from persistence, not live state
      - Atomic: uses the same hash-chained checkpoint journal

References:
    UAQP Patch 1: Ψ independence — recovery never trusts Γ's self-assessment
    UAQP Part VII Phase 0: Crash recovery validation
"""
from __future__ import annotations

import _thread
import atexit
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.graph_state_persistence import (
    GraphStatePersistence,
    StateTensorSnapshot,
    StateTensorCollector,
    CheckpointStatus,
    get_persistence,
    get_collector,
    snapshot_cycle,
    restore_last_state,
)

logger = logging.getLogger("CrashRecovery")


# ============================================================================
# Recovery result
# ============================================================================
class RecoveryAction(str, Enum):
    """What the recovery system recommends."""
    CLEAN_START = "CLEAN_START"           # No crash — start fresh
    RESTORE_FROM_CHECKPOINT = "RESTORE_FROM_CHECKPOINT"  # Recover from last good state
    HALT_AND_ALERT = "HALT_AND_ALERT"     # Corruption detected — do not trade
    GUARDIAN_TRIP = "GUARDIAN_TRIP"        # Ψ stale — trip guardian


@dataclass
class RecoveryResult:
    """Outcome of the crash recovery check."""
    action: RecoveryAction
    last_good_cycle: int = 0
    crash_detected: bool = False
    guardian_stale: bool = False
    restored_snapshot: Optional[StateTensorSnapshot] = None
    reasons: List[str] = field(default_factory=list)
    recovery_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "last_good_cycle": self.last_good_cycle,
            "crash_detected": self.crash_detected,
            "guardian_stale": self.guardian_stale,
            "has_snapshot": self.restored_snapshot is not None,
            "reasons": self.reasons,
            "recovery_timestamp": self.recovery_timestamp,
        }


# ============================================================================
# Crash Recovery Manager
# ============================================================================
class CrashRecoveryManager:
    """Orchestrates crash detection, recovery, and graceful shutdown.

    Lifecycle:
      1. __init__: register atexit + signal handlers
      2. check_on_startup(): called once at system boot
      3. enable_shutdown_snapshot(): called to enable periodic snapshotting
      4. shutdown(): called explicitly or via signal
    """

    def __init__(
        self,
        persistence: Optional[GraphStatePersistence] = None,
        guardian_heartbeat_stale_seconds: float = 300.0,
    ):
        self.persistence = persistence or get_persistence()
        self.collector = get_collector()
        self.guardian_stale_threshold = guardian_heartbeat_stale_seconds
        self._shutdown_snapshot_enabled = False
        self._shutdown_in_progress = False
        self._shutdown_event = threading.Event()
        self._current_cycle: int = 0
        self._current_checkpoint_id: Optional[int] = None
        self._last_snapshot_time: float = 0.0

        # Register graceful shutdown handlers
        self._register_shutdown_hooks()

    def _register_shutdown_hooks(self) -> None:
        """Register atexit and signal handlers for graceful shutdown.

        Signal handlers ONLY set a flag (K4).  Running shutdown() inline from
        a signal handler deadlocks whenever the signal lands while the main
        thread already holds GraphStatePersistence._lock (stack captured in
        AUDIT_2026-10-04_DEEP 06 F4), and swallowing the signal left the
        engine running after SIGTERM.  A dedicated watcher thread performs
        the real shutdown when it observes the flag.
        """
        atexit.register(self._atexit_shutdown)

        self._signal_requested: Optional[int] = None
        self._original_signal_handlers: Dict[int, Any] = {}

        if hasattr(signal, "SIGTERM"):
            self._original_signal_handlers[signal.SIGTERM] = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, self._signal_flag_handler)

        if hasattr(signal, "SIGINT"):
            self._original_signal_handlers[signal.SIGINT] = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._signal_flag_handler)

        watcher = threading.Thread(
            target=self._signal_watcher,
            name="shutdown-signal-watcher",
            daemon=True,
        )
        self._signal_watcher_thread = watcher
        watcher.start()

    def _signal_flag_handler(self, signum, frame) -> None:
        """Signal handler body: set a flag and return. Nothing else (K4).

        No locks, no logging, no I/O — a plain attribute write is the only
        operation that cannot deadlock against the interrupted thread.
        """
        self._signal_requested = signum

    def _signal_watcher(self) -> None:
        """Perform the flag-driven shutdown outside signal context."""
        try:
            while self._signal_requested is None and not self._shutdown_event.is_set():
                time.sleep(0.1)
            if self._signal_requested is None:
                return
            signum = self._signal_requested
            logger.warning("signal %s received — flag set; performing graceful shutdown", signum)
            try:
                self.shutdown()
            except Exception as exc:
                logger.error("signal-driven shutdown failed: %s", exc)
            self._after_signal_shutdown(signum)
        except Exception as exc:  # the watcher must never die silently
            logger.error("shutdown signal watcher failed: %s", exc)

    def _after_signal_shutdown(self, signum: int) -> None:
        """End the process after the graceful shutdown has completed.

        The old handler returned after saving its snapshot, which left the
        engine running — SIGTERM was effectively swallowed.  Re-interrupting
        the main thread lets the main loop unwind through its normal
        KeyboardInterrupt path (which runs the full cleanup `finally`), and
        any handler we replaced gets a chance to run first.
        """
        original = self._original_signal_handlers.get(signum)
        if callable(original):
            try:
                original(signum, None)
            except BaseException as exc:  # noqa: BLE001 - chained handlers may raise
                logger.debug("original signal handler for %s ended with %r", signum, exc)
        _thread.interrupt_main()

    def check_on_startup(self) -> RecoveryResult:
        """Check system state on startup and recommend recovery action.

        This is the primary entry point called once when the system boots.
        """
        logger.info("Crash recovery: checking system state...")

        result = RecoveryResult(action=RecoveryAction.CLEAN_START)

        # 1. Check for crash-interrupted checkpoint
        was_running = self.persistence.was_crash_detected()
        if was_running:
            result.crash_detected = True
            result.reasons.append("Last checkpoint was RUNNING (crash-interrupted)")
            logger.warning("Crash detected: last checkpoint was interrupted")

        # 2. Get last completed checkpoint
        last_snapshot = self.persistence.get_last_snapshot()
        if last_snapshot is not None:
            result.last_good_cycle = last_snapshot.cycle_num
        else:
            result.reasons.append("No completed checkpoints found")

        # 3. Check Ψ Guardian heartbeat staleness
        guardian_stale = self._check_guardian_staleness(last_snapshot)
        if guardian_stale:
            result.guardian_stale = True
            result.reasons.append(
                f"Ψ Guardian heartbeat stale "
                f"(>{self.guardian_stale_threshold}s ago)"
            )
            logger.warning("Ψ Guardian heartbeat is stale")

        # 4. Verify hash chain integrity
        chain_report = self.persistence.verify_chain_integrity()
        if not chain_report["valid"]:
            result.action = RecoveryAction.HALT_AND_ALERT
            result.reasons.append(
                f"Hash chain broken at checkpoint {chain_report['broken_at']}"
            )
            logger.critical(
                "Hash chain integrity check FAILED — broken at checkpoint %s",
                chain_report["broken_at"],
            )
            return result
        # Pruning boundary: the oldest retained row predates the recorded
        # anchor (legacy history trimmed before anchors existed). Not a
        # tamper signal — the retained window is fully verified from its
        # trust root — but it must be visible, not silent.
        boundary = chain_report.get("pruning_boundary")
        if boundary is not None:
            result.reasons.append(
                f"Checkpoint history pruned before row {boundary} — "
                f"retained window verified from trust root"
            )
            logger.info(
                "Checkpoint chain pruning boundary at row %s "
                "(%d head rows verified); not a tamper signal",
                boundary,
                chain_report.get("head_verified", 0),
            )

        # 5. Decide action
        if result.crash_detected and last_snapshot is not None:
            result.action = RecoveryAction.RESTORE_FROM_CHECKPOINT
            result.restored_snapshot = last_snapshot
            logger.info(
                "Recovery: restoring from checkpoint (cycle %d)",
                last_snapshot.cycle_num,
            )
        elif guardian_stale:
            result.action = RecoveryAction.GUARDIAN_TRIP
            logger.warning("Recovery: Ψ Guardian trip recommended")
        else:
            result.action = RecoveryAction.CLEAN_START
            logger.info("Recovery: clean start (no crash detected)")

        return result

    def begin_cycle_checkpoint(self, cycle_num: int) -> Optional[int]:
        """Begin a checkpoint at the start of a trading cycle.

        Returns checkpoint ID for later completion.
        """
        self._current_cycle = cycle_num
        try:
            cp_id = self.persistence.begin_checkpoint(cycle_num)
            self._current_checkpoint_id = cp_id
            self._last_snapshot_time = time.time()
            return cp_id
        except Exception as exc:
            logger.warning("Failed to begin checkpoint for cycle %d: %s", cycle_num, exc)
            return None

    def complete_cycle_checkpoint(self, checkpoint_id: Optional[int] = None) -> bool:
        """Complete the current cycle checkpoint with a state snapshot."""
        cp_id = checkpoint_id or self._current_checkpoint_id
        if cp_id is None:
            return False

        try:
            snapshot = self.collector.collect(self._current_cycle)
            self.persistence.complete_checkpoint(cp_id, snapshot)
            self._current_checkpoint_id = None
            self._last_snapshot_time = time.time()
            return True
        except Exception as exc:
            logger.warning("Failed to complete checkpoint %d: %s", cp_id, exc)
            try:
                self.persistence.fail_checkpoint(cp_id, str(exc))
            except Exception:
                pass
            return False

    def fail_cycle_checkpoint(self, checkpoint_id: Optional[int] = None) -> None:
        """Mark the current cycle checkpoint as failed."""
        cp_id = checkpoint_id or self._current_checkpoint_id
        if cp_id is not None:
            try:
                self.persistence.fail_checkpoint(cp_id)
            except Exception:
                pass

    def enable_shutdown_snapshot(self) -> None:
        """Enable periodic snapshotting on shutdown."""
        self._shutdown_snapshot_enabled = True

    def shutdown(self) -> None:
        """Graceful shutdown: snapshot state and cleanup."""
        if self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True

        logger.info("Crash recovery: graceful shutdown initiated")

        if self._shutdown_snapshot_enabled and self._current_cycle > 0:
            try:
                cp_id = self.persistence.begin_checkpoint(self._current_cycle)
                snapshot = self.collector.collect(self._current_cycle)
                self.persistence.complete_checkpoint(cp_id, snapshot)
                logger.info(
                    "Shutdown snapshot saved for cycle %d (checkpoint %d)",
                    self._current_cycle,
                    cp_id,
                )
            except Exception as exc:
                logger.warning("Failed to save shutdown snapshot: %s", exc)

        self._shutdown_event.set()

        # Post-session hook (2026-09-27): run the scheduler tuning report
        # and archive a timestamped copy under artifacts/. Fail-soft and
        # bounded (see scripts/session_report_hook.py) - shutdown never
        # depends on it, and it is inert under pytest unless forced on.
        try:
            from scripts.session_report_hook import run_and_archive
            archive = run_and_archive()
            if archive is not None:
                logger.info("Post-session scheduler report archived: %s",
                            archive.name)
        except Exception as exc:  # noqa: BLE001 - hook must never break shutdown
            logger.debug("post-session report hook skipped: %s", exc)

        logger.info("Crash recovery: shutdown complete")

    def get_recovery_stats(self) -> Dict[str, Any]:
        """Get crash recovery statistics."""
        stats = self.persistence.get_stats()
        stats.update({
            "guardian_stale_threshold_seconds": self.guardian_stale_threshold,
            "shutdown_snapshot_enabled": self._shutdown_snapshot_enabled,
            "current_cycle": self._current_cycle,
            "has_active_checkpoint": self._current_checkpoint_id is not None,
        })
        return stats

    def _check_guardian_staleness(
        self, snapshot: Optional[StateTensorSnapshot]
    ) -> bool:
        """Check if the Ψ Guardian heartbeat is stale."""
        if snapshot is None:
            return False
        if snapshot.last_guardian_heartbeat <= 0:
            return False
        age = time.time() - snapshot.last_guardian_heartbeat
        return age > self.guardian_stale_threshold

    def _atexit_shutdown(self) -> None:
        """atexit handler — save a final snapshot if possible."""
        if self._shutdown_in_progress:
            return
        self.shutdown()


# ============================================================================
# Singleton
# ============================================================================
_recovery_instance: Optional[CrashRecoveryManager] = None
_recovery_lock = threading.Lock()


def get_recovery(persistence: Optional[GraphStatePersistence] = None) -> CrashRecoveryManager:
    """Get or create the global crash recovery singleton."""
    global _recovery_instance
    if _recovery_instance is None:
        with _recovery_lock:
            if _recovery_instance is None:
                _recovery_instance = CrashRecoveryManager(persistence)
    return _recovery_instance


def startup_recovery() -> RecoveryResult:
    """Convenience: run startup recovery check and return result."""
    recovery = get_recovery()
    return recovery.check_on_startup()


def begin_checkpoint(cycle_num: int) -> Optional[int]:
    """Convenience: begin a cycle checkpoint."""
    recovery = get_recovery()
    return recovery.begin_cycle_checkpoint(cycle_num)


def complete_checkpoint(checkpoint_id: Optional[int] = None) -> bool:
    """Convenience: complete the current cycle checkpoint."""
    recovery = get_recovery()
    return recovery.complete_cycle_checkpoint(checkpoint_id)
