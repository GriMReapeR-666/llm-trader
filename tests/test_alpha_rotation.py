"""Tests for the Alpha Rotation Engine."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from core.alpha_rotation import (
    ALL_ALPHA_NAMES,
    AlphaCandidate,
    AlphaRotationEngine,
    RotationEvent,
    SourceCandidate,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_engine(tmp_path):
    """Fresh engine with temp state file."""
    return AlphaRotationEngine(
        state_file=str(tmp_path / "rotation_state.json"),
        min_alphas=3,
        max_alphas=10,
        cooldown_bars=5,
    )


FULL_POOL = list(ALL_ALPHA_NAMES)


def _make_health(**overrides):
    """Build a full health dict for the pool, with overrides for specific alphas."""
    base = {name: {"status": "HEALTHY", "ic_mean": 0.02, "ic_ir": 0.2} for name in FULL_POOL}
    base.update(overrides)
    return base


@pytest.fixture
def health_all_healthy():
    """Health dict where all alphas are HEALTHY."""
    return _make_health(
        alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5, "ic_current": 0.04, "ic_trend": 0.001},
        alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3, "ic_current": 0.03, "ic_trend": 0.0005},
        alpha_055={"status": "HEALTHY", "ic_mean": 0.02, "ic_ir": 0.2, "ic_current": 0.02, "ic_trend": 0.0001},
        alpha_034={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1, "ic_current": 0.01, "ic_trend": 0.0},
        alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05, "ic_current": 0.005, "ic_trend": -0.0001},
    )


@pytest.fixture
def health_one_dead():
    """Health dict with one DEAD alpha."""
    return _make_health(
        alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5, "ic_current": 0.04, "ic_trend": 0.001},
        alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3, "ic_current": 0.03, "ic_trend": 0.0005},
        alpha_055={"status": "DEAD", "ic_mean": -0.02, "ic_ir": -0.2, "ic_current": -0.03, "ic_trend": -0.002},
        alpha_034={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1, "ic_current": 0.01, "ic_trend": 0.0},
        alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05, "ic_current": 0.005, "ic_trend": -0.0001},
    )


@pytest.fixture
def pool_with_candidates():
    """Full pool of all available alpha names."""
    return list(ALL_ALPHA_NAMES)


# ── Basic State Tests ──────────────────────────────────────────────────────

class TestBasicState:
    def test_default_active_set(self, fresh_engine):
        active = fresh_engine.get_active_set()
        assert len(active) > 0
        assert "alpha_012" in active

    def test_empty_state_uses_defaults(self, fresh_engine):
        assert fresh_engine._active_set == []
        active = fresh_engine.get_active_set()
        assert len(active) >= fresh_engine.min_alphas

    def test_get_all_pool(self, fresh_engine, pool_with_candidates):
        pool = fresh_engine.get_all_pool()
        assert len(pool) > 0

    def test_status_structure(self, fresh_engine):
        status = fresh_engine.get_status()
        assert "active_set" in status
        assert "active_count" in status
        assert "pool_size" in status
        assert "total_rotations" in status

    def test_format_status(self, fresh_engine):
        report = fresh_engine.format_status()
        assert "ALPHA ROTATION ENGINE STATUS" in report
        assert "Active alphas" in report


# ── Rotation Logic Tests ───────────────────────────────────────────────────

class TestRotationLogic:
    def test_no_rotation_when_all_healthy(self, fresh_engine, health_all_healthy):
        events = fresh_engine.update(health_all_healthy, FULL_POOL, current_bar=100)
        assert len(events) == 0

    def test_rotation_when_alpha_dead(self, fresh_engine, health_one_dead):
        events = fresh_engine.update(health_one_dead, FULL_POOL, current_bar=100)
        assert len(events) == 1
        event = events[0]
        assert event.removed_alpha == "alpha_055"
        assert event.promoted_alpha != "alpha_055"
        assert "DEAD" in event.reason or "ic_mean" in event.reason

    def test_dead_alpha_removed_from_active(self, fresh_engine, health_one_dead):
        fresh_engine.update(health_one_dead, FULL_POOL, current_bar=100)
        active = fresh_engine.get_active_set()
        assert "alpha_055" not in active

    def test_candidate_promoted_to_active(self, fresh_engine):
        health = _make_health(
            alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
            alpha_055={"status": "DEAD", "ic_mean": -0.02, "ic_ir": -0.2},
            alpha_034={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1},
            alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05},
            alpha_009={"status": "HEALTHY", "ic_mean": 0.035, "ic_ir": 0.4, "ic_current": 0.035, "ic_trend": 0.001},
        )
        fresh_engine.update(health, FULL_POOL, current_bar=100)
        active = fresh_engine.get_active_set()
        # alpha_009 has high IC_IR and should be promoted
        assert "alpha_009" in active

    def test_min_alphas_enforced(self, fresh_engine):
        """Even if all alphas are DEAD, min_alphas should be maintained."""
        dead_health = {
            name: {"status": "DEAD", "ic_mean": -0.05, "ic_ir": -0.5, "ic_current": -0.05, "ic_trend": -0.005}
            for name in FULL_POOL
        }
        fresh_engine.update(dead_health, FULL_POOL, current_bar=100)
        active = fresh_engine.get_active_set()
        assert len(active) >= fresh_engine.min_alphas

    def test_evicts_dead_alpha_without_replacement_when_above_min_alphas(self, fresh_engine):
        """When pool has no candidate, dead alpha is evicted without replacement if above min_alphas."""
        health = {name: {"status": "DEAD", "ic_mean": -0.05, "ic_ir": -0.5} for name in FULL_POOL}
        active = fresh_engine.get_active_set()[:5]
        fresh_engine._active_set = list(active)
        for a in active[:4]:
            health[a] = {"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5}
        health[active[4]] = {"status": "DEAD", "ic_mean": -0.05, "ic_ir": -0.5}

        events = fresh_engine.update(health, FULL_POOL, current_bar=100)
        assert len(events) == 1
        assert events[0].removed_alpha == active[4]
        assert events[0].promoted_alpha == "NONE"
        assert active[4] not in fresh_engine.get_active_set()
        assert len(fresh_engine.get_active_set()) == 4

    def test_reversed_alpha_triggers_rotation(self, fresh_engine):
        health = _make_health(
            alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
            alpha_055={"status": "REVERSED", "ic_mean": -0.03, "ic_ir": -0.3, "ic_current": -0.04, "ic_trend": -0.003},
            alpha_034={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1},
            alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05},
        )
        events = fresh_engine.update(health, FULL_POOL, current_bar=100)
        assert len(events) >= 1
        assert events[0].removed_alpha == "alpha_055"

    def test_rotation_count_increments(self, fresh_engine, health_one_dead):
        fresh_engine.update(health_one_dead, FULL_POOL, current_bar=100)
        assert fresh_engine._total_rotations == 1

    def test_rotation_event_recorded(self, fresh_engine, health_one_dead):
        fresh_engine.update(health_one_dead, FULL_POOL, current_bar=100)
        history = fresh_engine._rotation_history
        assert len(history) >= 1
        assert isinstance(history[-1], RotationEvent)


# ── Cooldown Tests ─────────────────────────────────────────────────────────

class TestCooldown:
    def test_dead_alpha_cooldown(self, fresh_engine):
        """After rotation, the dead alpha should be on cooldown."""
        health_dead = _make_health(
            alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
            alpha_055={"status": "DEAD", "ic_mean": -0.02, "ic_ir": -0.2},
            alpha_034={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1},
            alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05},
        )
        # First rotation: alpha_055 removed
        fresh_engine.update(health_dead, FULL_POOL, current_bar=100)
        assert "alpha_055" not in fresh_engine.get_active_set()

        # alpha_055 recovers but is on cooldown
        health_recovered = _make_health(
            alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
            alpha_055={"status": "HEALTHY", "ic_mean": 0.05, "ic_ir": 0.6},  # recovered!
            alpha_034={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1},
            alpha_017={"status": "DEAD", "ic_mean": -0.01, "ic_ir": -0.1},  # another one dies
        )
        # Only 3 bars since rotation (< cooldown_bars=5)
        fresh_engine.update(health_recovered, FULL_POOL, current_bar=103)
        # alpha_055 should NOT be re-promoted yet (on cooldown)
        active = fresh_engine.get_active_set()
        assert "alpha_055" not in active

    def test_cooldown_expires(self, fresh_engine):
        """After cooldown expires, a dead alpha can be re-promoted."""
        health_dead = _make_health(
            alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
            alpha_055={"status": "DEAD", "ic_mean": -0.02, "ic_ir": -0.2},
            alpha_034={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1},
            alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05},
        )
        fresh_engine.update(health_dead, FULL_POOL, current_bar=100)

        # alpha_055 recovers AND cooldown expires; alpha_053 dies
        health_recovered = _make_health(
            alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            alpha_053={"status": "DEAD", "ic_mean": -0.01, "ic_ir": -0.1},
            alpha_055={"status": "HEALTHY", "ic_mean": 0.05, "ic_ir": 0.6},
            alpha_034={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1},
            alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05},
        )
        # 10 bars since first rotation (> cooldown_bars=5)
        fresh_engine.update(health_recovered, FULL_POOL, current_bar=110)
        active = fresh_engine.get_active_set()
        # alpha_053 should be removed (DEAD)
        assert "alpha_053" not in active


# ── Candidate Selection Tests ──────────────────────────────────────────────

class TestCandidateSelection:
    def test_best_candidate_by_ic_ir(self, fresh_engine):
        active = ["alpha_012", "alpha_053"]
        health = _make_health(
            alpha_014={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.4},
            alpha_022={"status": "HEALTHY", "ic_mean": 0.05, "ic_ir": 0.2},  # higher IC but lower IR
            alpha_025={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1},
        )
        candidate = fresh_engine._find_best_candidate(active, health, current_bar=100)
        assert candidate is not None
        # alpha_014 has highest IC_IR (0.4)
        assert candidate.name == "alpha_014"

    def test_no_candidate_below_ic_threshold(self, fresh_engine):
        fresh_engine.promotion_ic_threshold = 0.05
        active = ["alpha_012"]
        health = _make_health(
            alpha_014={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1},
            alpha_022={"status": "HEALTHY", "ic_mean": 0.02, "ic_ir": 0.2},
        )
        candidate = fresh_engine._find_best_candidate(active, health, current_bar=100)
        # All have ic_mean < 0.05 threshold (default pool alphas have 0.02)
        assert candidate is None or candidate.ic_mean >= 0.05

    def test_candidate_not_in_active_set(self, fresh_engine):
        active = ["alpha_012", "alpha_014"]
        health = _make_health(
            alpha_014={"status": "HEALTHY", "ic_mean": 0.05, "ic_ir": 0.6},
            alpha_022={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
        )
        candidate = fresh_engine._find_best_candidate(active, health, current_bar=100)
        # alpha_014 is in active set, should not be promoted
        assert candidate is None or candidate.name != "alpha_014"
        if candidate:
            assert candidate.name == "alpha_022"

    def test_candidate_skips_dead_status(self, fresh_engine):
        active = ["alpha_012"]
        health = _make_health(
            alpha_014={"status": "DEAD", "ic_mean": -0.03, "ic_ir": -0.3},
            alpha_022={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
        )
        candidate = fresh_engine._find_best_candidate(active, health, current_bar=100)
        assert candidate is not None
        assert candidate.name == "alpha_022"


# ── Discovered-Candidate Feed Tests ─────────────────────────────────────────

class TestDiscoveredCandidates:
    """Discovered candidates compete under the SAME unchanged gates."""

    def test_discovered_candidate_rejected_below_threshold_with_reason(
        self, fresh_engine, health_all_healthy
    ):
        """Honest baseline: ic_mean=-0.168 must FAIL the 0.02 gate, with the
        concrete gate verdict recorded — never a forced promotion."""
        dc = SourceCandidate(
            name="alpha_d2434_0",
            ic_mean=-0.1682,
            ic_ir=-0.832,
            source="discovery",
            expression="signed_power(ts_rank(delta(close, 5), 10), 2)",
            status="VALIDATED",
        )
        fresh_engine.update(health_all_healthy, FULL_POOL, current_bar=100,
                            discovered_candidates=[dc])
        active = fresh_engine.get_active_set()
        # Must NOT be promoted
        assert "alpha_d2434_0" not in active
        # Concrete gate verdict recorded
        decisions = fresh_engine.get_discovery_decisions()
        assert len(decisions) >= 1
        assert decisions[0]["verdict"] == "REJECT"
        assert decisions[0]["ic_mean"] == -0.1682
        assert decisions[0]["threshold"] == fresh_engine.promotion_ic_threshold
        assert "promotion_ic_threshold" in decisions[0]["reason"]

    def test_discovered_candidate_promoted_when_passing_gates(self, fresh_engine):
        """A discovered candidate with strong metrics beats the static pool
        and is promoted, replacing the dead alpha."""
        health = _make_health(
            alpha_055={"status": "DEAD", "ic_mean": -0.02, "ic_ir": -0.2},
        )
        dc = SourceCandidate(
            name="alpha_d2434_1",
            ic_mean=0.12,
            ic_ir=0.9,
            source="discovery",
            expression="ts_rank(delta(close, 5), 10)",
        )
        events = fresh_engine.update(health, FULL_POOL, current_bar=100,
                                     discovered_candidates=[dc])
        assert len(events) == 1
        assert events[0].promoted_alpha == "alpha_d2434_1"
        assert "discovered" in events[0].reason
        assert "alpha_d2434_1" in fresh_engine.get_active_set()
        # Pending materialization is recorded for the consumer
        pend = fresh_engine._pending_discovery_promotions.get("alpha_d2434_1")
        assert pend is not None
        assert pend["expression"] == "ts_rank(delta(close, 5), 10)"

    def test_static_pool_candidate_wins_tie_by_ranking(self, fresh_engine):
        """When a static-pool candidate outranks the discovered one, the
        static candidate is promoted — discovery does not jump the queue."""
        # Pick a pool alpha that is NOT currently active (default set holds 17).
        active_now = set(fresh_engine.get_active_set())
        static_pick = next(n for n in FULL_POOL if n not in active_now)
        health = _make_health(
            alpha_055={"status": "DEAD", "ic_mean": -0.02, "ic_ir": -0.2},
        )
        health[static_pick] = {
            "status": "HEALTHY", "ic_mean": 0.035, "ic_ir": 0.4,
            "ic_current": 0.035, "ic_trend": 0.001,
        }
        dc = SourceCandidate(
            name="alpha_d2434_2", ic_mean=0.03, ic_ir=0.2, source="discovery",
            expression="delta(close, 5)",
        )
        events = fresh_engine.update(health, FULL_POOL, current_bar=100,
                                     discovered_candidates=[dc])
        assert len(events) == 1
        assert events[0].promoted_alpha == static_pick

    def test_discovered_candidate_passing_gate_announced(
        self, fresh_engine, health_all_healthy
    ):
        """A discovered candidate clearing the threshold is announced as
        gate-passing and ranked alongside the static pool."""
        dc = SourceCandidate(
            name="alpha_d2434_3", ic_mean=0.08, ic_ir=0.7, source="discovery",
            expression="scale(delta(close, 5))",
        )
        fresh_engine.update(health_all_healthy, FULL_POOL, current_bar=100,
                            discovered_candidates=[dc])
        decisions = fresh_engine.get_discovery_decisions()
        assert any(
            d["verdict"] == "PASS_GATE" and d["name"] == "alpha_d2434_3"
            for d in decisions
        )

    def test_discovered_candidates_replaced_each_update(
        self, fresh_engine, health_all_healthy
    ):
        """The feed is replaced wholesale — a candidate no longer offered by
        discovery is no longer considered (no stale carryover)."""
        dc = SourceCandidate(
            name="alpha_d2434_4", ic_mean=0.10, ic_ir=0.8, source="discovery",
            expression="delta(close, 5)",
        )
        fresh_engine.update(health_all_healthy, FULL_POOL, current_bar=100,
                            discovered_candidates=[dc])
        # Next cycle: discovery no longer lists it
        fresh_engine.update(health_all_healthy, FULL_POOL, current_bar=101)
        assert fresh_engine._discovered_candidates == []

    def test_materialization_ack_clears_pending(self, fresh_engine):
        """record_discovery_materialized clears the pending record on ack."""
        health = _make_health(
            alpha_055={"status": "DEAD", "ic_mean": -0.02, "ic_ir": -0.2},
        )
        dc = SourceCandidate(
            name="alpha_d2434_5", ic_mean=0.12, ic_ir=0.9, source="discovery",
            expression="ts_rank(delta(close, 5), 10)",
        )
        fresh_engine.update(health, FULL_POOL, current_bar=100,
                            discovered_candidates=[dc])
        assert "alpha_d2434_5" in fresh_engine._pending_discovery_promotions
        fresh_engine.record_discovery_materialized(
            "alpha_d2434_5", True, "column added to live dataframe"
        )
        assert "alpha_d2434_5" not in fresh_engine._pending_discovery_promotions

    def test_materialization_pending_survives_restart(self, tmp_path, health_all_healthy):
        """Pending materialization persists across engine restarts (honest
        resume — the promoted expression is not silently dropped)."""
        state_file = str(tmp_path / "rotation_state.json")
        engine1 = AlphaRotationEngine(
            state_file=state_file, min_alphas=3, max_alphas=10, cooldown_bars=5,
        )
        health = _make_health(
            alpha_055={"status": "DEAD", "ic_mean": -0.02, "ic_ir": -0.2},
        )
        dc = SourceCandidate(
            name="alpha_d2434_6", ic_mean=0.12, ic_ir=0.9, source="discovery",
            expression="ts_rank(delta(close, 5), 10)",
        )
        engine1.update(health, FULL_POOL, current_bar=100, discovered_candidates=[dc])
        engine2 = AlphaRotationEngine(
            state_file=state_file, min_alphas=3, max_alphas=10, cooldown_bars=5,
        )
        assert "alpha_d2434_6" in engine2._pending_discovery_promotions
        assert (engine2._pending_discovery_promotions["alpha_d2434_6"]
                ["expression"] == "ts_rank(delta(close, 5), 10)")

    def test_no_candidates_no_verdict_noise(self, fresh_engine, health_all_healthy):
        """With an empty feed, no discovery decisions are recorded."""
        fresh_engine.update(health_all_healthy, FULL_POOL, current_bar=100)
        assert fresh_engine.get_discovery_decisions() == []

    def test_discovery_decisions_persisted(self, tmp_path, health_all_healthy):
        """Gate decisions are persisted in the rotation state (honest journal)."""
        state_file = str(tmp_path / "rotation_state.json")
        engine1 = AlphaRotationEngine(
            state_file=state_file, min_alphas=3, max_alphas=10, cooldown_bars=5,
        )
        dc = SourceCandidate(
            name="alpha_d2434_7", ic_mean=-0.05, ic_ir=-0.3, source="discovery",
            expression="delta(close, 5)",
        )
        engine1.update(health_all_healthy, FULL_POOL, current_bar=100,
                       discovered_candidates=[dc])
        engine2 = AlphaRotationEngine(
            state_file=state_file, min_alphas=3, max_alphas=10, cooldown_bars=5,
        )
        decisions = engine2.get_discovery_decisions()
        assert any(d["name"] == "alpha_d2434_7" and d["verdict"] == "REJECT"
                   for d in decisions)


# ── Persistence Tests ──────────────────────────────────────────────────────

class TestPersistence:
    def test_state_saved_after_rotation(self, fresh_engine, health_one_dead, pool_with_candidates, tmp_path):
        fresh_engine.update(health_one_dead, pool_with_candidates, current_bar=100)
        state_file = tmp_path / "rotation_state.json"
        # State should be saved
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert "active_set" in data
        assert "dead_timestamps" in data

    def test_state_loaded_on_init(self, tmp_path):
        state = {
            "active_set": ["alpha_012", "alpha_053", "alpha_055"],
            "all_pool": ["alpha_012", "alpha_053", "alpha_055", "alpha_034"],
            "dead_timestamps": {"alpha_034": 50},
            "total_rotations": 3,
            "rotation_history": [
                {"timestamp": 40, "removed": "alpha_034", "promoted": "alpha_017", "reason": "DEAD"}
            ],
            "last_health": {},
        }
        state_file = tmp_path / "rotation_state.json"
        state_file.write_text(json.dumps(state))

        engine = AlphaRotationEngine(state_file=str(state_file))
        assert engine.get_active_set() == ["alpha_012", "alpha_053", "alpha_055"]
        assert engine._total_rotations == 3
        assert "alpha_034" in engine._dead_timestamps

    def test_corrupt_state_file_handled(self, tmp_path):
        state_file = tmp_path / "bad.json"
        state_file.write_text("not json {{{")
        engine = AlphaRotationEngine(state_file=str(state_file))
        # Should fall back to defaults
        active = engine.get_active_set()
        assert len(active) > 0


# ── Integration Test ───────────────────────────────────────────────────────

class TestIntegration:
    def test_multiple_rotation_cycles(self, fresh_engine):
        """Simulate multiple cycles with rotating alpha health."""
        # Cycle 1: alpha_055 dies
        h1 = _make_health(
            alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
            alpha_055={"status": "DEAD", "ic_mean": -0.02, "ic_ir": -0.2},
            alpha_034={"status": "HEALTHY", "ic_mean": 0.01, "ic_ir": 0.1},
            alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05},
        )
        e1 = fresh_engine.update(h1, FULL_POOL, current_bar=100)
        assert len(e1) == 1
        assert e1[0].removed_alpha == "alpha_055"

        # Cycle 2: alpha_034 degrades (not dead yet)
        h2 = _make_health(
            alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
            alpha_034={"status": "DEGRADED", "ic_mean": 0.005, "ic_ir": 0.05},
            alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05},
        )
        e2 = fresh_engine.update(h2, FULL_POOL, current_bar=110)
        # DEGRADED doesn't trigger rotation (only DEAD/REVERSED)
        assert len(e2) == 0

        # Cycle 3: alpha_034 goes DEAD
        h3 = _make_health(
            alpha_012={"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            alpha_053={"status": "HEALTHY", "ic_mean": 0.03, "ic_ir": 0.3},
            alpha_034={"status": "DEAD", "ic_mean": -0.01, "ic_ir": -0.1},
            alpha_017={"status": "HEALTHY", "ic_mean": 0.005, "ic_ir": 0.05},
        )
        e3 = fresh_engine.update(h3, FULL_POOL, current_bar=120)
        assert len(e3) == 1
        assert e3[0].removed_alpha == "alpha_034"

        # Verify total rotations
        assert fresh_engine._total_rotations == 2

    def test_report_format(self, fresh_engine, health_one_dead):
        fresh_engine.update(health_one_dead, FULL_POOL, current_bar=100)
        report = fresh_engine.format_status()
        assert "alpha_055" not in report or "DEAD" in report
        assert "Rotations:" in report


# ── Test Fixture Quarantine Tests ──────────────────────────────────────────

class TestTestFixtureQuarantine:
    def test_load_state_filters_quarantined_fixtures(self, tmp_path):
        state_path = tmp_path / "polluted_state.json"
        polluted_state = {
            "active_set": ["alpha_012", "alpha_dead", "mock_signal", "alpha_014"],
            "all_pool": ["alpha_012", "alpha_reversed", "alpha_014", "fixture_test"],
            "dead_timestamps": {"alpha_dead": 100, "alpha_017": 50},
            "total_rotations": 3,
            "last_rotation_bar": 100,
            "last_health": {
                "alpha_012": {"status": "HEALTHY", "ic_mean": 0.05},
                "alpha_dead": {"status": "DEAD", "ic_mean": -0.1},
                "alpha_reversed": {"status": "REVERSED", "ic_mean": -0.9},
            },
            "rotation_history": [
                {
                    "timestamp": 100,
                    "removed": "alpha_dead",
                    "promoted": "alpha_012",
                    "reason": "DEAD",
                    "removed_ic": -0.1,
                    "promoted_ic": 0.05,
                },
                {
                    "timestamp": 105,
                    "removed": "alpha_017",
                    "promoted": "alpha_014",
                    "reason": "DEAD",
                    "removed_ic": -0.05,
                    "promoted_ic": 0.04,
                },
            ],
        }
        state_path.write_text(json.dumps(polluted_state))

        engine = AlphaRotationEngine(state_file=str(state_path))
        active = engine.get_active_set()
        pool = engine.get_all_pool()

        # Quarantined fixtures removed from active_set and all_pool
        assert "alpha_dead" not in active
        assert "mock_signal" not in active
        assert "alpha_reversed" not in pool
        assert "fixture_test" not in pool
        assert "alpha_012" in active
        assert "alpha_014" in active

        # Quarantined fixtures removed from dead_timestamps and last_health
        assert "alpha_dead" not in engine._dead_timestamps
        assert "alpha_017" in engine._dead_timestamps
        assert "alpha_dead" not in engine._last_health
        assert "alpha_reversed" not in engine._last_health
        assert "alpha_012" in engine._last_health

        # Rotation history sanitized
        assert len(engine._rotation_history) == 1
        assert engine._rotation_history[0].removed_alpha == "alpha_017"

    def test_update_filters_quarantined_fixtures(self, fresh_engine):
        incoming_health = {
            "alpha_012": {"status": "HEALTHY", "ic_mean": 0.04, "ic_ir": 0.5},
            "alpha_dead": {"status": "DEAD", "ic_mean": -0.12, "ic_ir": -0.5},
            "alpha_reversed": {"status": "REVERSED", "ic_mean": -0.95, "ic_ir": -0.9},
        }
        incoming_pool = ["alpha_012", "alpha_014", "test_mock_alpha", "alpha_noise"]

        fresh_engine.update(incoming_health, all_alpha_names=incoming_pool, current_bar=100)

        # last_health only has real alphas
        assert "alpha_dead" not in fresh_engine._last_health
        assert "alpha_reversed" not in fresh_engine._last_health
        assert "alpha_012" in fresh_engine._last_health

        # all_pool only has real alphas
        assert "test_mock_alpha" not in fresh_engine.get_all_pool()
        assert "alpha_noise" not in fresh_engine.get_all_pool()
        assert "alpha_012" in fresh_engine.get_all_pool()
        assert "alpha_014" in fresh_engine.get_all_pool()

    def test_save_state_sanitizes_saved_json(self, tmp_path):
        state_path = tmp_path / "saved_state.json"
        engine = AlphaRotationEngine(state_file=str(state_path))
        # Intentionally inject test fixtures into internal state
        engine._active_set.append("alpha_dead")
        engine._all_pool.append("alpha_reversed")
        engine._dead_timestamps["alpha_dead"] = 50
        engine._last_health["alpha_dead"] = {"status": "DEAD"}

        engine._save_state()

        saved = json.loads(state_path.read_text())
        assert "alpha_dead" not in saved["active_set"]
        assert "alpha_reversed" not in saved["all_pool"]
        assert "alpha_dead" not in saved["dead_timestamps"]
        assert "alpha_dead" not in saved["last_health"]
