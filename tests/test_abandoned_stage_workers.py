"""Abandoned bounded-stage workers must be bounded and visible (2026-10-16).

Python cannot kill a blocked thread, so ``run_stage_with_deadline`` can only stop
WAITING. The abandoned thread keeps running: it keeps fetching and keeps
writing to the journal, holding a DB connection.

``pre_pulse_macro_ingest`` runs once per PULSE and unconditionally spawns four
of these, so a provider that consistently overruns its budget added one
lingering thread per pulse, unbounded. Each holds a journal connection, so the
pile-up competes with the very work the budget was meant to protect -- the
guarantee that a stage cannot stall the session silently eroded as leftovers
accumulated.

Python offers no way to cancel them, so the watchdog does the only honest
thing available: it refuses to START a stage once too many are already
lingering, and it registers the ones it abandons so the count is observable
rather than inferred from symptoms.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intelligence import boot_fetch_watchdog as bw  # noqa: E402


@pytest.fixture(autouse=True)
def clean_registry():
    bw._abandoned_stages.clear()
    yield
    for rec in list(bw._abandoned_stages.values()):
        t = rec.get("thread")
        # Release the blocker so the daemon thread can exit before the next test.
        rec["release"] = True
        if t is not None:
            t.join(timeout=2)
    bw._abandoned_stages.clear()


def _blocking_stage(release: threading.Event):
    def _fn():
        release.wait(30.0)
        return "done"
    return _fn


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_timed_out_stage_is_registered_not_dropped():
    """An abandoned worker must be observable, not silently forgotten."""
    release = threading.Event()
    outcome = bw.run_stage_with_deadline(
        "calendar_collect", _blocking_stage(release), timeout_s=0.2)

    assert outcome["timed_out"] is True
    assert "abandoned_outstanding" in outcome

    state = bw.outstanding_abandoned_stages()
    assert state["count"] == 1
    assert "calendar_collect" in state["stages"]
    assert state["stages"]["calendar_collect"]["age_s"] >= 0


def test_repeated_abandonment_of_the_same_stage_accumulates():
    """The registry counts WORKERS, not stage names.

    ``pre_pulse_macro_ingest`` runs the same four stages every pulse, so a
    provider that consistently overruns abandons the *same* stage name over and
    over. Keyed by name, each new worker overwrote the last record: the count
    stayed at 1, the cap never tripped, and the pile grew without bound -- the
    cap was inert in precisely the production case it was written for.
    """
    release = threading.Event()
    for expected in (1, 2, 3):
        out = bw.run_stage_with_deadline(
            "data_source_pipeline", _blocking_stage(release), timeout_s=0.2)
        assert out["timed_out"] is True
        assert out["abandoned_outstanding"] == expected, (
            f"expected {expected} outstanding, got {out['abandoned_outstanding']}")
    assert bw.outstanding_abandoned_stages()["count"] == 3
    release.set()


def test_finished_worker_is_reaped():
    """The registry must not report workers that have since completed."""
    release = threading.Event()
    rec = {"thread": None, "release": True}

    def _fn():
        time.sleep(0.25)
        return "done"

    outcome = bw.run_stage_with_deadline("realtime_news_ingest", _fn, timeout_s=5.0)
    assert outcome["ok"] is True
    # A stage that finished inside its budget is never abandoned.
    assert bw.outstanding_abandoned_stages()["count"] == 0


def test_cap_stops_the_pile_from_growing(monkeypatch):
    """Past the cap, a new stage must be refused instead of abandoned again."""
    release = threading.Event()
    monkeypatch.setenv("LLM_TRADER_MAX_ABANDONED_STAGES", "2")

    # Two stalls of the SAME stage -> two workers registered.
    for _ in range(2):
        out = bw.run_stage_with_deadline(
            "calendar_collect", _blocking_stage(release), timeout_s=0.2)
        assert out["timed_out"] is True, "expected the stage to be abandoned"

    assert bw.outstanding_abandoned_stages()["count"] == 2

    # The third must NOT start a third lingering worker.
    started = threading.Event()
    out = bw.run_stage_with_deadline(
        "data_source_pipeline", lambda: started.set(), timeout_s=0.2)

    assert out.get("skipped_at_cap") is True
    assert out["ok"] is False
    assert "cap" in (out["error"] or "")
    assert not started.is_set(), (
        "the stage ran even though the cap was reached -- it would have become "
        "a third lingering worker")
    assert bw.outstanding_abandoned_stages()["count"] == 2

    release.set()


def test_cap_skips_fail_open_not_fail_closed():
    """A refused stage must not raise: the caller handles a failed stage."""
    release = threading.Event()
    import os

    os.environ["LLM_TRADER_MAX_ABANDONED_STAGES"] = "0"
    try:
        out = bw.run_stage_with_deadline(
            "bulk_calendar_ingest", lambda: None, timeout_s=1.0)
        assert out.get("skipped_at_cap") is True
        assert out["result"] is None
    finally:
        os.environ.pop("LLM_TRADER_MAX_ABANDONED_STAGES", None)
        release.set()


def test_invalid_cap_override_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_MAX_ABANDONED_STAGES", "not-a-number")
    assert bw._max_abandoned() == bw.MAX_ABANDONED_STAGES

    monkeypatch.setenv("LLM_TRADER_MAX_ABANDONED_STAGES", "-3")
    assert bw._max_abandoned() == bw.MAX_ABANDONED_STAGES

    monkeypatch.setenv("LLM_TRADER_MAX_ABANDONED_STAGES", "  ")
    assert bw._max_abandoned() == bw.MAX_ABANDONED_STAGES


def test_reaped_registry_frees_the_cap(monkeypatch):
    """Workers that finish must release their slot, or the cap is permanent."""
    release = threading.Event()
    monkeypatch.setenv("LLM_TRADER_MAX_ABANDONED_STAGES", "1")

    out = bw.run_stage_with_deadline(
        "calendar_collect", _blocking_stage(release), timeout_s=0.2)
    assert out["timed_out"] is True

    blocked = bw.run_stage_with_deadline(
        "data_source_pipeline", lambda: "ok", timeout_s=1.0)
    assert blocked.get("skipped_at_cap") is True

    release.set()
    assert _wait_for(lambda: bw.outstanding_abandoned_stages()["count"] == 0), \
        "a completed worker was never reaped, so the cap stays permanently closed"

    allowed = bw.run_stage_with_deadline(
        "data_source_pipeline", lambda: "ok", timeout_s=1.0)
    assert allowed["ok"] is True
    assert allowed.get("skipped_at_cap") is None