"""Schedule-claim starvation: a scarce claim slot must go to the most-neglected
schedule, not the oldest clock.

Incident (2026-10-16). ``claim_due_schedules`` ordered the wave by
``next_run_at`` and capped the batch with ``LIMIT 20``. With 26 schedules
overdue, six rows were cut off every wave -- and the cut set never changed,
because the excluded rows kept the NEWEST due point. Being cut off meant never
dispatching, which meant never advancing ``next_run_at``, which meant staying
newest: a self-perpetuating lock. 6 of 26 schedules never ran a single turn,
including ``event_monitor`` (calendar ingestion) and ``alert_dispatch`` (the
only alert sender), while their 217 undelivered alerts sat in the outbox.

The ordering defect is that raw due point measures absolute lateness, not
neglect, so it also inverts priority across cadences: a 30-day archive 13 days
late outranked a 60s monitor 3 days late.

Fix: rank by MISSED TURNS (``overdue / interval_seconds``). A row that misses
turns climbs the queue, so the lock is unreachable.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations.agent_operations import AgentOperations  # noqa: E402


def _set_due(ops: AgentOperations, name: str, seconds_late: float) -> None:
    when = (datetime.now(timezone.utc) - timedelta(seconds=seconds_late)).isoformat()
    with ops._con() as con:
        con.execute("UPDATE agent_schedules SET next_run_at=? WHERE schedule_name=?",
                    (when, name))


def _fresh(tmp_path, name: str = "starve") -> AgentOperations:
    # A file, not ":memory:": every _con() opens its own connection, so an
    # in-memory database would be a different (empty) database each call.
    return AgentOperations(str(tmp_path / f"{name}.db"))


def test_claim_prioritises_missed_turns_over_raw_due_point(tmp_path):
    """A 60s monitor that is 1h late outranks a 7d job that is 8d late.

    Absolute lateness puts the weekly job first (8 days > 1 hour); missed turns
    puts the monitor first (60 missed turns vs 1). The monitor is the one that
    loses the most catch-up per missed turn, so it must win the scarce slot.
    """
    ops = _fresh(tmp_path)
    ops.register_schedule("monitor_60s", "EventAgent", 60)
    ops.register_schedule("weekly_job", "QuantitativeCoach", 604800)
    _set_due(ops, "monitor_60s", 3600)      # 1h late, 60 missed turns
    _set_due(ops, "weekly_job", 8 * 86400)  # 8d late, 1 missed turn

    claimed = [i["schedule_name"] for i in ops.claim_due_schedules()]

    assert claimed[0] == "monitor_60s", (
        "the most-neglected schedule must win the claim slot; "
        f"got order {claimed}")


def test_neediest_rows_never_cut_and_cut_rows_recover_once_needies_advance(tmp_path):
    """The 2026-10-16 regression: the tight-limit cut set must not be permanent.

    Under ``ORDER BY next_run_at LIMIT 20`` with 26 overdue rows the same six
    were cut from all twelve consecutive waves, because they held the newest
    due point and being cut kept that due point unchanged.

    Two properties make the lock unreachable:
      1. the neediest rows (most missed turns) are served EVERY wave, and
      2. once those rows advance, the previously-cut rows are served.
    Rows with few missed turns may be cut while needier rows are pending --
    that is the intended ordering, not starvation.
    """
    ops = _fresh(tmp_path, "lock")
    for i in range(26):
        ops.register_schedule(f"job_{i:02d}", "EventAgent", 60)
        # Missed turns grow with i (86400*(i+1)/60), so job_25 is the neediest
        # and job_00 the least. Raw due point ranks these identically, which is
        # exactly why the old ORDER BY could not distinguish them.
        _set_due(ops, f"job_{i:02d}", 86400 * (i + 1))

    waves = 12
    counts: dict[str, int] = {f"job_{i:02d}": 0 for i in range(26)}
    cut_once = None
    for _ in range(waves):
        batch = ops.claim_due_schedules(limit=20)
        got = {i["schedule_name"] for i in batch}
        for n in got:
            counts[n] += 1
        if cut_once is None:
            cut_once = sorted({f"job_{i:02d}" for i in range(26)} - got)
        # No cadence advance, exactly as a claim does: nothing but the ORDER BY
        # can change a row's fate between waves.
        ops.release_schedule_claims(
            [{"schedule_name": i["schedule_name"], "claimed_at": i["claimed_at"]}
             for i in batch])

    # Property 1: the neediest rows are never starved.
    assert counts["job_25"] == waves, f"neediest row served {counts['job_25']}/{waves}"
    assert counts["job_24"] == waves
    starved = sorted(n for n, c in counts.items() if c == 0)
    assert not any(counts[f"job_{i:02d}"] == 0 for i in range(16, 26)), \
        f"the ten neediest rows must never be cut, got starved={starved}"

    # Property 2: the cut rows recover as soon as the needies advance (the
    # dispatch step), so no row is locked out permanently.
    batch = ops.claim_due_schedules(limit=20)
    now = datetime.now(timezone.utc)
    with ops._con() as con:
        for item in batch:
            con.execute(
                "UPDATE agent_schedules SET next_run_at=?,claimed_at=NULL,claim_expires_at=NULL"
                " WHERE schedule_name=?",
                ((now + timedelta(seconds=60)).isoformat(), item["schedule_name"]))
    con.commit()
    ops.release_schedule_claims(
        [{"schedule_name": i["schedule_name"], "claimed_at": i["claimed_at"]} for i in batch])

    recovered = {i["schedule_name"] for i in ops.claim_due_schedules()}
    never = sorted(set(cut_once or ()) - recovered)
    assert not never, f"rows cut by the limit never recovered: {never}"


def test_claim_orders_by_descending_missed_turns(tmp_path):
    """The full batch is strictly ordered by missed turns, descending."""
    ops = _fresh(tmp_path)
    # Missed turns: a=30, b=5, c=1 -- deliberately NOT the same order as their
    # lateness, so an accidental pass on raw due point fails here.
    ops.register_schedule("a", "EventAgent", 60)
    ops.register_schedule("b", "EventAgent", 60)
    ops.register_schedule("c", "EventAgent", 60)
    _set_due(ops, "a", 60 * 30)
    _set_due(ops, "b", 60 * 5)
    _set_due(ops, "c", 60 * 1)

    claimed = ops.claim_due_schedules()

    assert [i["schedule_name"] for i in claimed] == ["a", "b", "c"]
    assert [i["missed_runs"] for i in claimed] == [30, 5, 1]


def test_undue_rows_still_excluded_and_not_claimed(tmp_path):
    """Starvation ordering must not resurrect schedules that are not due."""
    ops = _fresh(tmp_path)
    ops.register_schedule("due", "EventAgent", 60)
    ops.register_schedule("future", "EventAgent", 60)
    _set_due(ops, "due", 600)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with ops._con() as con:
        con.execute("UPDATE agent_schedules SET next_run_at=? WHERE schedule_name='future'",
                    (future,))

    claimed = [i["schedule_name"] for i in ops.claim_due_schedules()]

    assert claimed == ["due"]


def test_corrupt_due_point_sorts_last_not_first(tmp_path):
    """An unparseable due point must not outrank live work (2026-10-16).

    Corrupt rows still claim when there is room -- the claim's recovery path
    rewrites them -- but they must never win a contested slot, because the
    starvation rank cannot be computed for them.
    """
    ops = _fresh(tmp_path)
    ops.register_schedule("healthy", "EventAgent", 60)
    ops.register_schedule("corrupt", "EventAgent", 60)
    _set_due(ops, "healthy", 3600)
    with ops._con() as con:
        # Lexicographically < now so it passes the ``next_run_at<=?`` gate, but
        # unparseable as a date.
        con.execute("UPDATE agent_schedules SET next_run_at='0000-00-00T00:00:00+00:00'"
                    " WHERE schedule_name='corrupt'")

    claimed = ops.claim_due_schedules()

    assert claimed[0]["schedule_name"] == "healthy"
    assert claimed[-1]["schedule_name"] == "corrupt"
    assert claimed[-1]["schedule_state"] == "MALFORMED_RECOVERED"