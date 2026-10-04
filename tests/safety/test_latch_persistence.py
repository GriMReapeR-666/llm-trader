"""Pin the D9 kill-switch latch: ladder, persistence, and who may clear it.

The latch may only be cleared by explicit calm market data (VIX below the
threshold with no halt asserted). News silence, missing VIX or a restart must
never clear it.
"""
import math

import pytest

from data.kill_switch_latch import (CONFIRM_WINDOW_S, REARM_COOLDOWN_S, VIX_CLEAR_THRESHOLD,
                                     KillSwitchLatch)

T0 = 1_000_000.0


def test_ladder_constants_are_pinned():
    assert (CONFIRM_WINDOW_S, REARM_COOLDOWN_S, VIX_CLEAR_THRESHOLD) == (300.0, 900.0, 35.0)


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "latch.json")


def _latch(path):
    return KillSwitchLatch(path=path, now_fn=lambda: T0)


def test_single_tripwire_batch_does_not_latch(path):
    latch = _latch(path)
    assert latch.observe(1, ["bank run"], now=T0)["latched"] is False


def test_two_batches_inside_window_latch(path):
    latch = _latch(path)
    latch.observe(1, ["bank run"], now=T0)
    snap = latch.observe(1, ["bank run"], now=T0 + CONFIRM_WINDOW_S - 1)
    assert snap["latched"] is True and snap["trigger_source"] == "news_tripwire_confirmed"


def test_batches_outside_window_do_not_latch(path):
    latch = _latch(path)
    latch.observe(1, ["bank run"], now=T0)
    assert latch.observe(1, ["bank run"], now=T0 + CONFIRM_WINDOW_S + 1)["latched"] is False


def test_market_halt_latches_immediately(path):
    snap = _latch(path).apply_market_data(halt_flag=True, now=T0)
    assert snap["latched"] is True and snap["trigger_source"] == "market_data_halt"


@pytest.fixture
def latched(path):
    latch = _latch(path)
    latch.apply_market_data(halt_flag=True, now=T0)
    return latch


@pytest.mark.parametrize("vix", [None, VIX_CLEAR_THRESHOLD, 50.0, math.nan], ids=["missing", "at", "high", "nan"])
def test_non_calm_market_data_never_clears(latched, vix):
    assert latched.apply_market_data(vix=vix, now=T0 + 60)["latched"] is True


def test_news_silence_never_clears(latched):
    for i in range(5):
        assert latched.observe(0, [], now=T0 + 600 * (i + 1))["latched"] is True


def test_calm_vix_clears_and_starts_cooldown(latched):
    snap = latched.apply_market_data(vix=VIX_CLEAR_THRESHOLD - 0.01, now=T0 + 60)
    assert snap["latched"] is False and snap["cooldown_active"] is True


def test_calm_vix_with_halt_asserted_does_not_clear(latched):
    assert latched.apply_market_data(vix=10.0, halt_flag=True, now=T0 + 60)["latched"] is True


def test_tripwires_suppressed_during_cooldown(latched):
    latched.apply_market_data(vix=10.0, now=T0)
    latched.observe(1, ["crash"], now=T0 + 10)
    snap = latched.observe(1, ["crash"], now=T0 + 20)
    assert snap["latched"] is False and snap["suppressed_during_cooldown"] == 2
    latched.observe(1, ["crash"], now=T0 + REARM_COOLDOWN_S + 1)
    assert latched.observe(1, ["crash"], now=T0 + REARM_COOLDOWN_S + 2)["latched"] is True


def test_latch_survives_restart(latched, path):
    assert _latch(path).snapshot()["latched"] is True


@pytest.mark.xfail(strict=True, reason="TS-P6: an unreadable checkpoint loads as unlatched (fails open)")
def test_corrupt_checkpoint_after_crash_loads_latched(latched, path):
    with open(path, "w") as fh:
        fh.write('{"latched": tr')      # torn write
    assert _latch(path).snapshot()["latched"] is True


def test_every_import_path_shares_one_latch(monkeypatch):
    import os
    import sys
    import data.kill_switch_latch as packaged
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    monkeypatch.syspath_prepend(os.path.join(root, "data"))
    from data import kill_switch_latch as bare
    assert packaged.get_kill_switch_latch() is bare.get_kill_switch_latch()
