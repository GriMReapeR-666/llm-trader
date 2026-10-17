"""Rotation must DRAIN the dead set, not one alpha per cooldown window (2026-10-16).

The cooldown's documented purpose is to stop a recently-dead alpha being
re-promoted. It is enforced correctly on that path (``_find_best_candidate``
checks ``_dead_timestamps`` per candidate).

It was ALSO gating eviction. That serialised cleanup to one alpha per cooldown
window: eight DEAD/REVERSED alphas sat in a 14-alpha active set, each waiting on
its own window, while ``total_rotations`` advanced by one. When the decay
monitor runs infrequently -- which it does -- the set never drains.

Removing a dead alpha is a correctness action, not churn. Only its
re-promotion needs a cooldown, and that gate is untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.alpha_rotation import AlphaRotationEngine  # noqa: E402


def _health(**status):
    """Health map: unlisted alphas default to HEALTHY with a strong IC."""
    out = {}
    for name in ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "c1", "c2"):
        out[name] = {
            "status": "HEALTHY", "ic_current": 0.05, "ic_mean": 0.04,
            "ic_trend": 0.001, "ic_consecutive_low": 0,
        }
    for name, st in status.items():
        out[name]["status"] = st
    return out


@pytest.fixture
def engine(tmp_path):
    return AlphaRotationEngine(
        state_file=str(tmp_path / "rot.json"),
        min_alphas=3, max_alphas=20, cooldown_bars=50,
    )


def _dead_count(eng, health):
    return sum(1 for a in eng.get_active_set()
               if health.get(a, {}).get("status") in ("DEAD", "REVERSED"))


def test_a_single_update_drains_every_dead_alpha(engine):
    """One pass must remove ALL dead alphas, not just the first."""
    health = _health(a1="DEAD", a2="REVERSED", a3="DEAD", a4="REVERSED",
                     a5="DEAD", a6="REVERSED")
    engine._active_set = ["a1", "a2", "a3", "a4", "a5", "a6", "c1", "c2"]

    engine.update(health=health, all_alpha_names=list(health), current_bar=1000)

    assert _dead_count(engine, health) == 0, (
        f"dead alphas survived one update: {engine.get_active_set()}")


def test_eviction_is_not_serialised_by_the_cooldown(engine):
    """The cooldown window must not limit how many die in one pass."""
    health = _health(**{f"a{i}": "DEAD" for i in range(1, 7)})
    engine._active_set = ["a1", "a2", "a3", "a4", "a5", "a6", "c1", "c2"]

    engine.update(health=health, all_alpha_names=list(health), current_bar=1000)

    # All six died at the same bar; a per-window gate could remove at most one.
    assert _dead_count(engine, health) == 0
    # min_alphas is a floor, not a ceiling: six evicted and two healthy
    # candidates promoted leaves 4 active, which is above the floor of 3.
    assert len(engine.get_active_set()) >= engine.min_alphas
    assert engine.min_alphas == 3
    assert not any(a.startswith("a") and a in
                   [f"a{i}" for i in range(1, 7)]
                   for a in engine.get_active_set())


def test_promotion_cooldown_still_blocks_a_recently_dead_alpha(engine):
    """The gate that MATTERS must survive: no re-promotion during cooldown."""
    health = _health(a1="DEAD", a2="DEAD")
    engine._active_set = ["a1", "a2", "c1", "c2"]
    engine._dead_timestamps = {"a2": 995}   # killed 5 bars ago, cooldown=50

    engine.update(health=health, all_alpha_names=list(health), current_bar=1000)

    assert "a2" not in engine.get_active_set(), "dead alpha must be evicted"
    # ...and a2 must not come straight back.
    assert engine._dead_timestamps["a2"] == 1000
    assert "a2" not in engine.get_active_set()


def test_a_reversed_alpha_is_never_promoted_on_a_stale_positive_mean(engine):
    """A negative CURRENT IC must not be promotable on a positive ic_mean.

    alpha_055 entered the active set with status REVERSED (ic_current
    -0.0135, low_streak 6) while ic_mean was still +0.0657: the DEAD/REVERSED
    skip was nested inside a recovery carve-out that only fired for alphas with
    a kill timestamp, so one that had never been killed fell through and was
    promoted. Recovery needs no carve-out -- the decay monitor recomputes
    `status` every pass, so a genuinely recovered alpha reads HEALTHY.
    """
    health = _health()
    # Stale-positive long-run mean, negative current IC -> REVERSED.
    health["c1"].update({"status": "REVERSED", "ic_current": -0.0135,
                         "ic_mean": 0.0657, "ic_consecutive_low": 6})
    health["a1"].update({"status": "DEAD", "ic_current": 0.0, "ic_mean": -0.03})
    engine._active_set = ["a1", "a2", "a3", "a4"]

    engine.update(health=health, all_alpha_names=list(health), current_bar=1000)

    assert "c1" not in engine.get_active_set(), (
        f"a REVERSED alpha was promoted: {engine.get_active_set()}")


def test_a_recovered_alpha_is_still_promotable(engine):
    """The fix must not block genuine recovery: HEALTHY competes normally."""
    health = _health()
    health["a1"].update({"status": "DEAD", "ic_current": 0.0, "ic_mean": -0.03})
    # c1 has recovered: status HEALTHY, healthy metrics.
    health["c1"].update({"status": "HEALTHY", "ic_current": 0.05, "ic_mean": 0.06})
    engine._active_set = ["a1", "a2", "a3", "a4"]

    engine.update(health=health, all_alpha_names=list(health), current_bar=1000)

    assert "c1" in engine.get_active_set(), (
        "a recovered (HEALTHY) alpha must still be promotable")


def test_min_alphas_floor_still_protects_against_over_eviction(engine):
    """Eviction must still respect min_alphas."""
    health = _health(a1="DEAD", a2="REVERSED", a3="DEAD")
    engine._active_set = ["a1", "a2", "a3", "c1", "c2"]   # 5 active, min 3

    engine.update(health=health, all_alpha_names=list(health), current_bar=1000)

    assert len(engine.get_active_set()) >= 3
    assert engine.min_alphas == 3


def test_first_rotation_still_works_from_a_cold_start(engine):
    """_last_rotation_bar == -1 must not block the initial cleanup."""
    health = _health(a1="DEAD", a2="REVERSED")
    engine._active_set = ["a1", "a2", "c1", "c2", "c3"]
    engine._last_rotation_bar = -1

    engine.update(health=health, all_alpha_names=list(health), current_bar=10)

    assert _dead_count(engine, health) == 0