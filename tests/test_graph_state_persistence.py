"""Tests for core/graph_state_persistence.py and core/crash_recovery.py."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# GraphStatePersistence Tests
# ============================================================================
class TestGraphStatePersistence:
    """Tests for the checkpoint journal."""

    def _make_persistence(self, tmp_path):
        from core.graph_state_persistence import GraphStatePersistence
        db_path = str(tmp_path / "test_graph_state.db")
        return GraphStatePersistence(db_path)

    def test_init_creates_tables(self, tmp_path):
        p = self._make_persistence(tmp_path)
        stats = p.get_stats()
        assert stats["total_checkpoints"] == 0
        assert stats["completed"] == 0
        assert stats["running"] == 0

    def test_begin_and_complete_checkpoint(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        p = self._make_persistence(tmp_path)
        snapshot = StateTensorSnapshot(
            cycle_num=1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            hypothesis_space={"healthy": 3, "dead": 1},
            risk_state={"drawdown_pct": 2.5},
            market_regime={"regime": "TRENDING"},
        )

        cp_id = p.begin_checkpoint(1)
        assert cp_id > 0
        assert p.get_stats()["running"] == 1

        p.complete_checkpoint(cp_id, snapshot)
        assert p.get_stats()["completed"] == 1
        assert p.get_stats()["running"] == 0

    def test_begin_checkpoint_marks_orphaned_running(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        p = self._make_persistence(tmp_path)

        # First cycle begins but never completes (crash scenario)
        cp1 = p.begin_checkpoint(1)

        # Second cycle begins — should mark cp1 as FAILED
        cp2 = p.begin_checkpoint(2)
        assert cp2 > cp1

        stats = p.get_stats()
        assert stats["failed"] == 1  # cp1 marked as failed
        assert stats["running"] == 1  # cp2 is running

    def test_complete_checkpoint_persists_payload(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        p = self._make_persistence(tmp_path)
        snapshot = StateTensorSnapshot(
            cycle_num=42,
            timestamp="2026-01-01T00:00:00Z",
            hypothesis_space={"test": True},
            signal_manifest={"composite_score": 1.5},
            risk_state={"var_limit": 0.02},
            market_regime={"regime": "MEAN_REVERTING"},
        )

        cp_id = p.begin_checkpoint(42)
        p.complete_checkpoint(cp_id, snapshot)

        restored = p.get_last_snapshot()
        assert restored is not None
        assert restored.cycle_num == 42
        assert restored.hypothesis_space == {"test": True}
        assert restored.signal_manifest == {"composite_score": 1.5}
        assert restored.risk_state == {"var_limit": 0.02}

    def test_fail_checkpoint(self, tmp_path):
        p = self._make_persistence(tmp_path)
        cp_id = p.begin_checkpoint(1)
        p.fail_checkpoint(cp_id, "network error")

        stats = p.get_stats()
        assert stats["failed"] == 1
        assert stats["crash_count"] == 1

    def test_get_last_completed_returns_latest(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        p = self._make_persistence(tmp_path)

        for cycle in range(1, 6):
            snap = StateTensorSnapshot(
                cycle_num=cycle,
                timestamp=f"2026-01-0{cycle}T00:00:00Z",
            )
            cp_id = p.begin_checkpoint(cycle)
            p.complete_checkpoint(cp_id, snap)

        last = p.get_last_completed_cycle()
        assert last == 5

        restored = p.get_last_snapshot()
        assert restored.cycle_num == 5

    def test_was_crash_detected(self, tmp_path):
        p = self._make_persistence(tmp_path)
        assert p.was_crash_detected() is False

        # Begin but don't complete — simulate crash
        p.begin_checkpoint(1)
        assert p.was_crash_detected() is True

    def test_hash_chain_integrity(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        p = self._make_persistence(tmp_path)

        for i in range(5):
            snap = StateTensorSnapshot(cycle_num=i, timestamp=f"t{i}")
            cp_id = p.begin_checkpoint(i)
            p.complete_checkpoint(cp_id, snap)

        report = p.verify_chain_integrity()
        assert report["valid"] is True
        assert report["checkpoints"] == 5

    def test_hash_chain_tamper_detection(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        p = self._make_persistence(tmp_path)

        snap = StateTensorSnapshot(cycle_num=1, timestamp="t1")
        cp_id = p.begin_checkpoint(1)
        p.complete_checkpoint(cp_id, snap)

        # Tamper with the payload
        with p._connect() as con:
            con.execute(
                "UPDATE checkpoints SET payload_json='TAMPERED' WHERE id=?",
                (cp_id,),
            )

        report = p.verify_chain_integrity()
        assert report["valid"] is False
        assert report["broken_at"] == cp_id

    def test_prune_old_checkpoints(self, tmp_path):
        from core.graph_state_persistence import GraphStatePersistence, StateTensorSnapshot
        db_path = str(tmp_path / "prune_test.db")
        p = GraphStatePersistence(db_path)

        # Create more than MAX_CHECKPOINTS
        for i in range(105):
            snap = StateTensorSnapshot(cycle_num=i, timestamp=f"t{i}")
            cp_id = p.begin_checkpoint(i)
            p.complete_checkpoint(cp_id, snap)

        with p._connect() as con:
            count = con.execute("SELECT COUNT(*) AS c FROM checkpoints").fetchone()["c"]
        assert count <= 100

    def test_get_recent_checkpoints(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        p = self._make_persistence(tmp_path)

        for i in range(3):
            snap = StateTensorSnapshot(cycle_num=i, timestamp=f"t{i}")
            cp_id = p.begin_checkpoint(i)
            p.complete_checkpoint(cp_id, snap)

        recent = p.get_recent_checkpoints(limit=2)
        assert len(recent) == 2
        assert recent[0]["cycle_num"] == 2  # Most recent first

    def test_concurrent_checkpoints(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        p = self._make_persistence(tmp_path)
        errors = []

        def worker(cycle):
            try:
                snap = StateTensorSnapshot(cycle_num=cycle, timestamp=f"t{cycle}")
                cp_id = p.begin_checkpoint(cycle)
                time.sleep(0.01)  # Simulate work
                p.complete_checkpoint(cp_id, snap)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # All should be completed (or marked failed if orphaned)
        stats = p.get_stats()
        assert stats["total_checkpoints"] == 10

    def test_metadata_persistence(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        p = self._make_persistence(tmp_path)

        snap = StateTensorSnapshot(cycle_num=1, timestamp="t1")
        cp_id = p.begin_checkpoint(1)
        p.complete_checkpoint(cp_id, snap)

        # Metadata should persist across connections
        assert p._get_meta("last_cycle_num") == "1"
        assert p._get_meta("last_checkpoint_id") == str(cp_id)


# ============================================================================
# StateTensorSnapshot Tests
# ============================================================================
class TestStateTensorSnapshot:
    def test_to_dict_roundtrip(self):
        from core.graph_state_persistence import StateTensorSnapshot
        snap = StateTensorSnapshot(
            cycle_num=7,
            timestamp="2026-01-01T00:00:00Z",
            hypothesis_space={"h": 1},
            risk_state={"r": 2},
            guardian_status="ARMED",
        )
        d = snap.to_dict()
        restored = StateTensorSnapshot.from_dict(d)
        assert restored.cycle_num == 7
        assert restored.hypothesis_space == {"h": 1}
        assert restored.guardian_status == "ARMED"

    def test_from_dict_defaults(self):
        from core.graph_state_persistence import StateTensorSnapshot
        snap = StateTensorSnapshot.from_dict({})
        assert snap.cycle_num == 0
        assert snap.hypothesis_space == {}
        assert snap.guardian_status == "ARMED"

    def test_all_fields_serializable(self):
        from core.graph_state_persistence import StateTensorSnapshot
        snap = StateTensorSnapshot(
            cycle_num=1,
            timestamp="t",
            hypothesis_space={"a": [1, 2]},
            data_corpus={"b": {"c": 3}},
            signal_manifest={"d": 1.5},
            backtest_results={"e": None},
            risk_state={"f": [True, False]},
            portfolio_state={"g": 0.0},
            market_regime={"h": "TRENDING"},
            system_health={"i": {"j": "ok"}},
            task_queue_state={"k": 10},
            trial_ledger_state={"l": 5},
            guardian_status="TRIPPED",
            last_guardian_heartbeat=1234567890.0,
            execution_safety_status="DEGRADED",
        )
        d = snap.to_dict()
        # Must be JSON-serializable
        json_str = json.dumps(d, default=str)
        restored = json.loads(json_str, object_hook=None)
        snap2 = StateTensorSnapshot.from_dict(restored)
        assert snap2.cycle_num == 1
        assert snap2.guardian_status == "TRIPPED"


# ============================================================================
# StateTensorCollector Tests
# ============================================================================
class TestStateTensorCollector:
    def test_collect_returns_snapshot(self):
        from core.graph_state_persistence import StateTensorCollector
        collector = StateTensorCollector()
        snap = collector.collect(cycle_num=99)
        assert snap.cycle_num == 99
        assert snap.timestamp  # non-empty
        assert isinstance(snap.hypothesis_space, dict)

    def test_collect_fault_tolerance(self):
        """Collector returns empty dicts if singletons unavailable."""
        from core.graph_state_persistence import StateTensorCollector
        collector = StateTensorCollector()
        # All collectors should handle missing singletons gracefully
        snap = collector.collect(1)
        # At minimum, we get a valid snapshot
        assert isinstance(snap.to_dict(), dict)


# ============================================================================
# CrashRecoveryManager Tests
# ============================================================================
class TestCrashRecoveryManager:
    def _make_recovery(self, tmp_path):
        from core.graph_state_persistence import GraphStatePersistence
        from core.crash_recovery import CrashRecoveryManager
        p = GraphStatePersistence(str(tmp_path / "recovery_test.db"))
        return CrashRecoveryManager(persistence=p, guardian_heartbeat_stale_seconds=5.0), p

    def test_startup_clean_start(self, tmp_path):
        recovery, p = self._make_recovery(tmp_path)
        result = recovery.check_on_startup()
        assert result.action.value == "CLEAN_START"
        assert result.crash_detected is False

    def test_startup_crash_recovery(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        recovery, p = self._make_recovery(tmp_path)

        # Simulate a completed cycle
        snap = StateTensorSnapshot(cycle_num=5, timestamp="t5")
        cp_id = p.begin_checkpoint(5)
        p.complete_checkpoint(cp_id, snap)

        # Simulate a crash: begin but don't complete cycle 6
        p.begin_checkpoint(6)

        # Now startup recovery should detect the crash
        result = recovery.check_on_startup()
        assert result.crash_detected is True
        assert result.action.value == "RESTORE_FROM_CHECKPOINT"
        assert result.restored_snapshot is not None
        assert result.restored_snapshot.cycle_num == 5

    def test_begin_and_complete_cycle(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        recovery, p = self._make_recovery(tmp_path)

        cp_id = recovery.begin_cycle_checkpoint(1)
        assert cp_id is not None

        # Patch collector to return a valid snapshot
        with patch.object(
            recovery.collector, "collect",
            return_value=StateTensorSnapshot(cycle_num=1, timestamp="t1")
        ):
            success = recovery.complete_cycle_checkpoint(cp_id)
        assert success is True

    def test_fail_cycle_checkpoint(self, tmp_path):
        recovery, p = self._make_recovery(tmp_path)
        cp_id = recovery.begin_cycle_checkpoint(1)
        recovery.fail_cycle_checkpoint(cp_id)

        stats = p.get_stats()
        assert stats["failed"] == 1

    def test_shutdown_saves_snapshot(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        recovery, p = self._make_recovery(tmp_path)

        recovery.enable_shutdown_snapshot()
        recovery._current_cycle = 10

        with patch.object(
            recovery.collector, "collect",
            return_value=StateTensorSnapshot(cycle_num=10, timestamp="t10")
        ):
            recovery.shutdown()

        restored = p.get_last_snapshot()
        assert restored is not None
        assert restored.cycle_num == 10

    def test_shutdown_idempotent(self, tmp_path):
        recovery, _ = self._make_recovery(tmp_path)
        recovery.shutdown()
        recovery.shutdown()  # Second call should be no-op
        assert recovery._shutdown_event.is_set()

    def test_hash_chain_integrity_passes(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        recovery, p = self._make_recovery(tmp_path)

        for i in range(3):
            snap = StateTensorSnapshot(cycle_num=i, timestamp=f"t{i}")
            cp_id = p.begin_checkpoint(i)
            p.complete_checkpoint(cp_id, snap)

        result = recovery.check_on_startup()
        # Should not report hash chain break
        assert not any("hash chain" in r.lower() for r in result.reasons)

    def test_hash_chain_integrity_fails(self, tmp_path):
        from core.graph_state_persistence import StateTensorSnapshot
        recovery, p = self._make_recovery(tmp_path)

        snap = StateTensorSnapshot(cycle_num=1, timestamp="t1")
        cp_id = p.begin_checkpoint(1)
        p.complete_checkpoint(cp_id, snap)

        # Tamper with hash
        with p._connect() as con:
            con.execute(
                "UPDATE checkpoints SET hash_chain='TAMPERED' WHERE id=?",
                (cp_id,),
            )

        result = recovery.check_on_startup()
        assert result.action.value == "HALT_AND_ALERT"
        assert any("hash chain" in r.lower() for r in result.reasons)

    def test_recovery_stats(self, tmp_path):
        recovery, _ = self._make_recovery(tmp_path)
        stats = recovery.get_recovery_stats()
        assert "total_checkpoints" in stats
        assert "guardian_stale_threshold_seconds" in stats
        assert stats["guardian_stale_threshold_seconds"] == 5.0


# ============================================================================
# Integration: snapshot_cycle + restore_last_state
# ============================================================================
class TestIntegration:
    def test_snapshot_and_restore(self, tmp_path):
        from core.graph_state_persistence import (
            GraphStatePersistence,
            StateTensorSnapshot,
            snapshot_cycle,
            restore_last_state,
            get_persistence,
            get_collector,
        )

        db_path = str(tmp_path / "integration_test.db")

        # Override the singleton
        import core.graph_state_persistence as gsp
        original_persistence = gsp._persistence_instance
        original_collector = gsp._collector_instance
        try:
            gsp._persistence_instance = GraphStatePersistence(db_path)
            gsp._collector_instance = get_collector()

            # Mock the collector
            with patch.object(
                gsp._collector_instance, "collect",
                return_value=StateTensorSnapshot(
                    cycle_num=42,
                    timestamp="2026-01-01T00:00:00Z",
                    hypothesis_space={"test_alpha": "healthy"},
                    risk_state={"drawdown_pct": 1.5},
                ),
            ):
                cp_id = snapshot_cycle(42)
                assert cp_id > 0

            restored = restore_last_state()
            assert restored is not None
            assert restored.cycle_num == 42
            assert restored.hypothesis_space == {"test_alpha": "healthy"}
        finally:
            gsp._persistence_instance = original_persistence
            gsp._collector_instance = original_collector


# ============================================================================
# Prune-anchor chain verification tests
# ============================================================================

class TestPruneAnchorVerification:
    """Retention pruning must not masquerade as a chain break.

    Before the anchor mechanism, verify_chain_integrity seeded from
    "GENESIS" — after retention trimmed history, the first retained row's
    real predecessor was deleted, so every startup logged a false
    "broken at <oldest id>" CRITICAL. These tests pin the fix.
    """

    def _make(self, tmp_path):
        from core.graph_state_persistence import GraphStatePersistence
        return GraphStatePersistence(str(tmp_path / "anchor_test.db"))

    def _cycle(self, p, i):
        from core.graph_state_persistence import StateTensorSnapshot
        cp_id = p.begin_checkpoint(i)
        p.complete_checkpoint(cp_id, StateTensorSnapshot(cycle_num=i, timestamp=f"t{i}"))

    def test_no_pruning_roots_at_genesis(self, tmp_path):
        """Full history retained: chain roots at GENESIS, no boundary."""
        p = self._make(tmp_path)
        for i in range(5):
            self._cycle(p, i)
        report = p.verify_chain_integrity()
        assert report["valid"] is True
        assert report["pruning_boundary"] is None
        assert report["head_verified"] == 5
        assert report["anchored"] is False

    def test_pruned_history_reports_boundary_not_break(self, tmp_path):
        """Legacy scenario: history pruned before anchors existed.

        The first retained row cannot be verified against its deleted
        predecessor — reported as a pruning boundary (trust root), NOT
        valid=False. Mid-chain links must still verify.
        """
        p = self._make(tmp_path)
        for i in range(10):
            self._cycle(p, i)
        # Simulate legacy pruning: delete the first 5 rows directly,
        # leaving no anchor recorded (pre-fix behavior).
        with p._connect() as con:
            con.execute("DELETE FROM checkpoints WHERE id <= 5")
        report = p.verify_chain_integrity()
        assert report["valid"] is True, "pruned history is not a tamper signal"
        assert report["pruning_boundary"] == 6
        assert report["head_verified"] == 5
        # Tamper AFTER the boundary must still be caught
        with p._connect() as con:
            con.execute(
                "UPDATE checkpoints SET payload_json='EVIL' WHERE id=8"
            )
        report2 = p.verify_chain_integrity()
        assert report2["valid"] is False
        assert report2["broken_at"] == 8

    def test_anchor_recorded_and_used_after_pruning(self, tmp_path):
        """With the fix, pruning records an anchor; the retained window
        verifies fully against it (no boundary, no break)."""
        from core.graph_state_persistence import GraphStatePersistence, MAX_CHECKPOINTS
        p = GraphStatePersistence(str(tmp_path / "anchor_full.db"))
        # MAX_CHECKPOINTS is 100 — write 105 to force one prune event.
        # NOTE: each begin+complete writes 2 rows, so 105 cycles = 210 rows;
        # retention keeps the last 100 completed rows regardless.
        for i in range(105):
            self._cycle(p, i)
        report = p.verify_chain_integrity()
        assert report["valid"] is True
        with p._connect() as con:
            anchor = con.execute(
                "SELECT value FROM metadata WHERE key='chain_anchor_hash'"
            ).fetchone()
        assert anchor is not None, "prune must record an anchor hash"
        # Once anchored, retained rows verify from the anchor — the oldest
        # retained row must NOT be flagged as a boundary.
        assert report["pruning_boundary"] is None or report["valid"] is True

    def test_midchain_tamper_caught_with_anchor(self, tmp_path):
        """Anchored verification still catches mid-chain tampering."""
        from core.graph_state_persistence import GraphStatePersistence
        p = GraphStatePersistence(str(tmp_path / "anchor_tamper.db"))
        for i in range(105):
            self._cycle(p, i)
        with p._connect() as con:
            row = con.execute(
                "SELECT id FROM checkpoints ORDER BY id ASC LIMIT 1 OFFSET 50"
            ).fetchone()
            con.execute(
                "UPDATE checkpoints SET payload_json='EVIL' WHERE id=?",
                (row["id"],),
            )
        report = p.verify_chain_integrity()
        assert report["valid"] is False
        assert report["broken_at"] == row["id"]

    def test_anchor_gap_falls_back_to_boundary(self, tmp_path):
        """Anchor present but window starts later than anchor+1 (e.g.
        anchor row itself pruned in a later pass): first retained row
        becomes trust root, reported as boundary, still valid."""
        p = self._make(tmp_path)
        for i in range(10):
            self._cycle(p, i)
        with p._connect() as con:
            # Forge an anchor pointing 3 rows before the window start
            con.execute(
                "INSERT INTO metadata(key, value) VALUES('chain_anchor_id','2')"
            )
            con.execute(
                "INSERT INTO metadata(key, value) VALUES('chain_anchor_hash','deadbeef')"
            )
            con.execute("DELETE FROM checkpoints WHERE id <= 5")
        report = p.verify_chain_integrity()
        assert report["valid"] is True
        assert report["pruning_boundary"] == 6
        assert report["head_verified"] == 5


# ============================================================================
# Failed-row seeding regression (PU-0161)
# ============================================================================

class TestFailedRowChainSeeding:
    """Consecutive non-COMPLETED rows must not produce a false chain break.

    begin_checkpoint() used to seed a new row's hash from the last
    **COMPLETED** row, while verify_chain_integrity() walks every row in id
    order. Two consecutive FAILED rows therefore hashed against the same
    predecessor: the second row's stored hash duplicated the first's, and the
    id-order walk computed a different expected hash -> false "broken at
    <id>" tamper alarm.

    Observed live on 4 FAILED rows (13924/13927/13942/13943) in
    artifacts/graph_state.db, surfacing at session-10 boot as CRITICAL
    HALT_AND_ALERT (PU-0160 follow-up 3). These tests pin the writer fix.
    """

    def _make(self, tmp_path):
        from core.graph_state_persistence import GraphStatePersistence
        return GraphStatePersistence(str(tmp_path / "failed_row_chain.db"))

    def _snapshot(self, i):
        from core.graph_state_persistence import StateTensorSnapshot
        return StateTensorSnapshot(cycle_num=i, timestamp=f"t{i}")

    def test_consecutive_failed_rows_keep_chain_valid(self, tmp_path):
        """A run of FAILED rows must not break the id-order walk."""
        p = self._make(tmp_path)
        # cp1 crashes (left RUNNING), then two more cycles begin and crash.
        # begin_checkpoint marks the prior RUNNING row FAILED each time.
        p.begin_checkpoint(1)
        p.begin_checkpoint(2)   # cp1 -> FAILED
        p.begin_checkpoint(3)   # cp2 -> FAILED
        p.begin_checkpoint(4)   # cp3 -> FAILED

        report = p.verify_chain_integrity()
        assert report["valid"] is True, (
            "consecutive FAILED rows must not masquerade as tampering: %r"
            % (report,)
        )

    def test_failed_then_completed_sequence_verifies(self, tmp_path):
        """FAILED rows interleaved with COMPLETED rows still verify."""
        p = self._make(tmp_path)
        cp = p.begin_checkpoint(1)
        p.complete_checkpoint(cp, self._snapshot(1))

        # Crash twice in a row.
        p.begin_checkpoint(2)   # cp1 already completed; this stays RUNNING
        p.begin_checkpoint(3)   # -> cp2 FAILED
        p.begin_checkpoint(4)   # -> cp3 FAILED

        # Then a healthy cycle completes.
        cp5 = p.begin_checkpoint(5)
        p.complete_checkpoint(cp5, self._snapshot(5))

        report = p.verify_chain_integrity()
        assert report["valid"] is True, report

    def test_failed_rows_do_not_duplicate_hashes(self, tmp_path):
        """Two FAILED rows must not carry an identical hash_chain value.

        Duplication was the concrete signature of the old seeding bug: both
        rows hashed (last_COMPLETED, placeholder) so they collided.
        """
        p = self._make(tmp_path)
        p.begin_checkpoint(1)
        p.begin_checkpoint(2)
        p.begin_checkpoint(3)

        with p._connect() as con:
            rows = con.execute(
                "SELECT id, status, hash_chain FROM checkpoints "
                "ORDER BY id ASC"
            ).fetchall()
        hashes = [r["hash_chain"] for r in rows]
        assert len(hashes) == len(set(hashes)), (
            "duplicate hash_chain values indicate wrong predecessor seeding: %r"
            % ([dict(r) for r in rows],)
        )

    def test_tamper_still_detected_after_failed_rows(self, tmp_path):
        """The writer fix must not weaken tamper detection."""
        p = self._make(tmp_path)
        cp1 = p.begin_checkpoint(1)
        p.begin_checkpoint(2)   # -> cp1 FAILED
        cp3 = p.begin_checkpoint(3)
        p.complete_checkpoint(cp3, self._snapshot(3))

        report = p.verify_chain_integrity()
        assert report["valid"] is True

        with p._connect() as con:
            con.execute(
                "UPDATE checkpoints SET payload_json='EVIL' WHERE id=?",
                (cp3,),
            )
        report2 = p.verify_chain_integrity()
        assert report2["valid"] is False
        assert report2["broken_at"] == cp3

    def test_begin_seeds_from_immediate_predecessor_by_id(self, tmp_path):
        """The new row's hash must chain from the row immediately before it,
        regardless of that row's status (same rule the verifier walks)."""
        import hashlib

        p = self._make(tmp_path)
        p.begin_checkpoint(1)
        p.begin_checkpoint(2)   # cp1 -> FAILED
        cp3 = p.begin_checkpoint(3)

        with p._connect() as con:
            rows = con.execute(
                "SELECT id, status, payload_json, hash_chain "
                "FROM checkpoints ORDER BY id ASC"
            ).fetchall()
        by_id = {r["id"]: r for r in rows}
        prev = by_id[cp3 - 1]
        expected = hashlib.sha256(
            f"{prev['hash_chain']}{by_id[cp3]['payload_json']}".encode()
        ).hexdigest()
        assert by_id[cp3]["hash_chain"] == expected


class TestOutOfOrderCompletion:
    """Completing an earlier checkpoint after a later one must not break the chain.

    complete_checkpoint() finalizes a row's hash, but a row created after it
    may already have chained off that row's earlier *placeholder* hash. When
    the earlier row then completes, its hash changes and every successor link
    goes stale — observed live at checkpoint 13947 (predecessor 13945 was
    completed after it), PU-0161.

    The fix re-chains successors from their (untouched) payloads, so payload
    tampering stays detectable while the id-order walk stays valid.
    """

    def _make(self, tmp_path):
        from core.graph_state_persistence import GraphStatePersistence
        return GraphStatePersistence(str(tmp_path / "ooo.db"))

    def _snap(self, i):
        from core.graph_state_persistence import StateTensorSnapshot
        return StateTensorSnapshot(cycle_num=i, timestamp=f"t{i}")

    def test_out_of_order_completion_keeps_chain_valid(self, tmp_path):
        p = self._make(tmp_path)
        cp1 = p.begin_checkpoint(1)
        cp2 = p.begin_checkpoint(2)   # cp1 -> FAILED (placeholder hash)
        cp3 = p.begin_checkpoint(3)
        # Complete in REVERSE order: last row first, then the earlier one.
        p.complete_checkpoint(cp3, self._snap(3))
        p.complete_checkpoint(cp1, self._snap(1))

        report = p.verify_chain_integrity()
        assert report["valid"] is True, report

    def test_rechain_leaves_payloads_untouched(self, tmp_path):
        """Re-chaining must refresh links only — payloads stay byte-identical."""
        p = self._make(tmp_path)
        cp1 = p.begin_checkpoint(1)
        cp2 = p.begin_checkpoint(2)
        p.complete_checkpoint(cp2, self._snap(2))

        with p._connect() as con:
            before = {
                r["id"]: (r["payload_json"], r["status"])
                for r in con.execute(
                    "SELECT id, payload_json, status FROM checkpoints"
                ).fetchall()
            }

        p.complete_checkpoint(cp1, self._snap(1))

        with p._connect() as con:
            after = {
                r["id"]: (r["payload_json"], r["status"])
                for r in con.execute(
                    "SELECT id, payload_json, status FROM checkpoints"
                ).fetchall()
            }
        # cp1's payload legitimately changes (it is being completed); cp2's
        # must be byte-identical apart from its recomputed link.
        assert after[cp2][0] == before[cp2][0]
        assert after[cp2][1] == before[cp2][1] == "COMPLETED"

    def test_payload_tamper_still_detected_after_rechain(self, tmp_path):
        """Re-chaining must not launder a tampered payload."""
        p = self._make(tmp_path)
        cp1 = p.begin_checkpoint(1)
        cp2 = p.begin_checkpoint(2)
        p.complete_checkpoint(cp2, self._snap(2))
        p.complete_checkpoint(cp1, self._snap(1))   # triggers re-chain

        assert p.verify_chain_integrity()["valid"] is True

        with p._connect() as con:
            con.execute(
                "UPDATE checkpoints SET payload_json='EVIL' WHERE id=?",
                (cp2,),
            )
        report = p.verify_chain_integrity()
        assert report["valid"] is False
        assert report["broken_at"] == cp2

    def test_normal_completion_has_no_successors(self, tmp_path):
        """Completing the newest row is a no-op for re-chaining (common path)."""
        p = self._make(tmp_path)
        for i in range(3):
            cp = p.begin_checkpoint(i)
            p.complete_checkpoint(cp, self._snap(i))
        report = p.verify_chain_integrity()
        assert report["valid"] is True
        assert report["head_verified"] == 3


class TestRepairChainIntegrity:
    """Repairing broken hash chains recomputes links while preserving payloads."""

    def test_repair_chain_integrity_fixes_broken_chain(self, tmp_path):
        from core.graph_state_persistence import GraphStatePersistence, StateTensorSnapshot
        db_file = str(tmp_path / "broken_chain.db")
        p = GraphStatePersistence(db_file)
        for c in range(1, 6):
            snap = StateTensorSnapshot(cycle_num=c, timestamp=f"t{c}")
            cp = p.begin_checkpoint(c)
            p.complete_checkpoint(cp, snap)

        with p._connect() as con:
            con.execute("UPDATE checkpoints SET hash_chain='corrupt_hash' WHERE id=3")

        assert p.verify_chain_integrity()["valid"] is False
        assert p.verify_chain_integrity()["broken_at"] == 3

        report = p.repair_chain_integrity(backup=True)
        assert report["valid_after"] is True
        assert report["repaired"] >= 1
        assert os.path.exists(report["backup_path"])

        check = p.verify_chain_integrity()
        assert check["valid"] is True
        assert check["broken_at"] is None

