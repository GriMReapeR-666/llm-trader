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
# Signal and Shutdown Flags (CC-F4)
# ============================================================================
_shutdown_event = threading.Event()
_sigint_count = 0


def shutdown_requested() -> bool:
    """True if a shutdown signal (SIGTERM or SIGINT) has been received."""
    return _shutdown_event.is_set()


def reset_shutdown_request() -> None:
    """Reset shutdown state (used in testing)."""
    global _sigint_count
    _shutdown_event.clear()
    _sigint_count = 0


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
        self._shutdown_event = _shutdown_event
        self._current_cycle: int = 0
        self._current_checkpoint_id: Optional[int] = None
        self._last_snapshot_time: float = 0.0

        # Register graceful shutdown handlers
        self._register_shutdown_hooks()

    def _register_shutdown_hooks(self) -> None:
        """Register atexit and flag-only signal handlers for graceful shutdown (CC-F4)."""
        atexit.register(self._atexit_shutdown)

        # Flag-only SIGTERM handler (CC-F4): never acquires locks or writes DB in handler
        if hasattr(signal, "SIGTERM"):
            def _sigterm_handler(signum, frame):
                logger.warning("SIGTERM received — setting shutdown event")
                _shutdown_event.set()

            try:
                signal.signal(signal.SIGTERM, _sigterm_handler)
            except (ValueError, OSError):
                pass

        # Flag-only SIGINT handler (CC-F4): sets event; second SIGINT raises KeyboardInterrupt
        if hasattr(signal, "SIGINT"):
            def _sigint_handler(signum, frame):
                global _sigint_count
                _sigint_count += 1
                logger.warning("SIGINT received (count=%d) — setting shutdown event", _sigint_count)
                _shutdown_event.set()
                if _sigint_count >= 2:
                    raise KeyboardInterrupt

            try:
                signal.signal(signal.SIGINT, _sigint_handler)
            except (ValueError, OSError):
                pass

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
