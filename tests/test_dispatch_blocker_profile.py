"""Unit tests for dispatch_blocker_profile.py's blocker-attribution rules.

Pins the exact thresholds before the next live session, mirroring the
tuning-report treatment:

Structural mode (waves reconstructed from claim clustering, gap <= 2s):
- CLAIM_ORDER_TAIL iff median in-wave claim position >= 0.6 OR
  tail-position share (position >= 2/3) >= 0.4; boundaries inclusive.
- HANDLER_COST_UPSTREAM otherwise; NEVER_CLAIMED when never in a wave.

Telemetry mode (dispatcher's own events):
- own overruns -> HANDLER_COST (evidence carries p50/p95 vs budget);
- deferrals without own overruns -> CLAIM_ORDER_TAIL; neither -> AMBIGUOUS.

The oracle test rebuilds the 2026-09-26 baseline attributions
(position_reconcile/event_monitor/alert_dispatch... = CLAIM_ORDER_TAIL,
market_health/event_triage/catalog_measurement_pass = HANDLER_COST_UPSTREAM)
from synthetic waves, so a threshold drift fails loudly. The end-to-end test
drives main() against a synthetic journal -- no live DB needed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from dispatch_blocker_profile import (  # noqa: E402
    _classify_structural,
    _classify_telemetry,
    _percentile,
    _reconstruct_waves,
    main as profile_main,
)


def _wave(position: int, size: int, name: str = "s") -> list:
    """A wave of `size` claims with `name` sitting at `position` (0-based)."""
    others = [f"f{i}" for i in range(size - 1)]
    return others[:position] + [name] + others[position:]


class TestWaveReconstruction:
    """The sub-2s clustering rule that turns claim stamps into waves."""

    def test_gap_le_2s_joins_wave(self):
        stamps = ["2026-09-26T10:00:00+00:00", "2026-09-26T10:00:02+00:00"]
        names = dict.fromkeys(stamps, "a")
        waves = _reconstruct_waves(stamps, names)
        assert waves == [["a", "a"]]  # exactly 2.0s: same wave (<= 2.0)

    def test_gap_gt_2s_splits_wave(self):
        stamps = ["2026-09-26T10:00:00+00:00", "2026-09-26T10:00:02.000001+00:00"]
        names = dict.fromkeys(stamps, "a")
        waves = _reconstruct_waves(stamps, names)
        assert waves == [["a"], ["a"]]

    def test_three_stamps_two_waves(self):
        stamps = ["2026-09-26T10:00:00+00:00",
                  "2026-09-26T10:00:01+00:00",
                  "2026-09-26T10:00:04+00:00",
                  "2026-09-26T10:00:05.5+00:00"]
        names = dict.fromkeys(stamps, "x")
        waves = _reconstruct_waves(stamps, names)
        assert [len(w) for w in waves] == [2, 2]

    def test_unknown_stamp_name_falls_back_to_placeholder(self):
        stamps = ["2026-09-26T10:00:00+00:00"]
        waves = _reconstruct_waves(stamps, {})
        assert waves == [["?"]]


class TestStructuralThresholds:
    """The 0.6 median-position and 0.4 tail-share boundaries, inclusive."""

    def test_median_position_boundary_060_claims_tail(self):
        # Positions [0.6, 0.6, 0.6] (index 3 of a 6-claim wave): median 0.60
        # sits exactly on the boundary -> CLAIM_ORDER_TAIL (inclusive). Tail
        # share is 0 here (0.6 < 2/3), so this is the median path alone.
        waves = [_wave(3, 6), _wave(3, 6), _wave(3, 6)]
        c = _classify_structural("s", waves)
        assert c["verdict"] == "CLAIM_ORDER_TAIL"
        assert c["med_pos"] == pytest.approx(0.6)
        assert c["tail_share"] == 0.0

    def test_median_position_just_below_boundary_is_upstream(self):
        # Positions [0.5, 0.5, 0.5]: median 0.50 < 0.6 and tail share 0/3.
        waves = [_wave(0, 3), _wave(0, 3), _wave(0, 3)]
        assert _classify_structural("s", waves)["verdict"] == "HANDLER_COST_UPSTREAM"

    def test_tail_share_boundary_040_claims_tail(self):
        # 2 of 5 waves tail-positioned: share 0.40 -> CLAIM_ORDER_TAIL.
        waves = [_wave(0, 3), _wave(0, 3), _wave(2, 3), _wave(2, 3), _wave(0, 3)]
        c = _classify_structural("s", waves)
        assert c["verdict"] == "CLAIM_ORDER_TAIL"
        assert c["tail_share"] == pytest.approx(0.4)

    def test_tail_share_just_below_boundary_is_upstream(self):
        # 2 of 6 waves tail-positioned: share 0.333 < 0.4, median pos < 0.6.
        waves = [_wave(0, 3), _wave(0, 3), _wave(0, 3), _wave(2, 3), _wave(2, 3), _wave(0, 3)]
        c = _classify_structural("s", waves)
        assert c["verdict"] == "HANDLER_COST_UPSTREAM"
        assert c["tail_share"] == pytest.approx(2 / 6)

    def test_tail_share_alone_overrides_low_median(self):
        # 3 head + 2 tail waves: tail share exactly 0.40 (>= 0.4) while the
        # median pick is 0.0 (< 0.6) - the OR rule pins CLAIM_ORDER_TAIL.
        waves = [_wave(0, 3), _wave(0, 3), _wave(0, 3), _wave(2, 3), _wave(2, 3)]
        c = _classify_structural("s", waves)
        assert c["verdict"] == "CLAIM_ORDER_TAIL"
        assert c["med_pos"] == pytest.approx(0.0)
        assert c["tail_share"] == pytest.approx(0.4)

    def test_median_alone_overrides_low_tail_share(self):
        # Tail share 1/3 < 0.4 but median position 1.0 >= 0.6.
        waves = [_wave(2, 3), _wave(2, 3), _wave(0, 3)]
        assert _classify_structural("s", waves)["verdict"] == "CLAIM_ORDER_TAIL"

    def test_never_claimed(self):
        waves = [["a", "b"], ["c"]]  # waves that never contain "s"
        c = _classify_structural("s", waves)
        assert c["verdict"] == "NEVER_CLAIMED"
        assert c["n_seen"] == 0 and c["med_pos"] is None and c["tail_share"] is None

    def test_single_element_wave_position_is_zero(self):
        # max(1, len-1) guard: a lone claim sits at position 0.0, not inf.
        c = _classify_structural("s", [["s"], ["s"], ["s"]])
        assert c["verdict"] == "HANDLER_COST_UPSTREAM"
        assert c["med_pos"] == 0.0

    def test_duplicate_claims_count_first_position(self):
        # w.index() takes the first occurrence - the pre-existing semantics.
        c = _classify_structural("s", [["s", "s", "s"]])
        assert c["n_seen"] == 1 and c["med_pos"] == 0.0

    def test_percentile_semantics_unchanged(self):
        # Same round()-element-pick the tuning report pins: upper middle.
        assert _percentile([], 0.5) == 0.0
        assert _percentile([10.0, 20.0, 30.0, 40.0], 0.5) == 30.0


class TestTelemetryThresholds:
    """Overrun -> HANDLER_COST, deferral-only -> CLAIM_ORDER_TAIL, else AMBIGUOUS."""

    def test_own_overruns_win_over_deferrals(self):
        own = {"elapsed": [5.0, 10.0, 20.0, 40.0, 50.0], "budgets": [10.0] * 5}
        c = _classify_telemetry("s", own, deferrals=3, budgets={"s": 10.0})
        assert c["verdict"] == "HANDLER_COST"
        assert c["n_overruns"] == 5
        assert c["p50"] == 20.0 and c["p95"] == 50.0
        assert c["budget"] == 10.0

    def test_deferrals_without_own_overruns_claim_tail(self):
        c = _classify_telemetry("s", None, deferrals=7, budgets={"s": 10.0})
        assert c["verdict"] == "CLAIM_ORDER_TAIL"
        assert c["deferrals"] == 7

    def test_neither_overruns_nor_deferrals_is_ambiguous(self):
        assert _classify_telemetry("s", None, 0, {})["verdict"] == "AMBIGUOUS"
        assert _classify_telemetry("s", None, 0, {"s": 10.0})["verdict"] == "AMBIGUOUS"

    def test_budget_falls_back_to_max_observed(self):
        # Budget unknown in the registry dict: evidence uses the events' own
        # max recorded budget, never zero.
        own = {"elapsed": [30.0], "budgets": [8.0, 12.0]}
        c = _classify_telemetry("s", own, deferrals=0, budgets={})
        assert c["verdict"] == "HANDLER_COST"
        assert c["budget"] == 12.0

    def test_registry_budget_takes_precedence(self):
        own = {"elapsed": [30.0], "budgets": [8.0]}
        c = _classify_telemetry("s", own, deferrals=0, budgets={"s": 15.0})
        assert c["budget"] == 15.0


class TestStructuralOracleFixture:
    """Rebuilds the 2026-09-26 baseline stats from synthetic waves.

    The oracle profile reported, for the baseline session:
    position_reconcile -> CLAIM_ORDER_TAIL (median position 0.75, tail
    share 51%); market_health -> HANDLER_COST_UPSTREAM (median 0.00, tail
    2%). Synthetic waves sized to hit those exact stats pin the verdicts,
    so threshold or helper drift fails loudly.
    """

    @staticmethod
    def _tail_wave() -> list:
        # 5-claim wave, "s" at index 3: position 3/4 = 0.75 (tail: >= 2/3).
        return ["a", "b", "c", "s", "d"]

    @staticmethod
    def _head_wave() -> list:
        return ["s", "a", "b", "c", "d"]  # position 0.0

    def test_oracle_tail_attributions(self):
        # 51 tail + 49 head waves: median pick = sorted[round(99/2)] =
        # sorted[50] = 0.75, tail share 51/100 = 0.51 -> CLAIM_ORDER_TAIL.
        waves = [self._tail_wave() for _ in range(51)] + \
                [self._head_wave() for _ in range(49)]
        c = _classify_structural("s", waves)
        assert c["n_seen"] == 100
        assert c["verdict"] == "CLAIM_ORDER_TAIL"
        assert c["med_pos"] == pytest.approx(0.75)
        assert c["tail_share"] == pytest.approx(0.51)

    def test_oracle_upstream_attributions(self):
        # 2 tail + 98 head waves: median pick 0.00, tail share 0.02 ->
        # HANDLER_COST_UPSTREAM despite the long waves.
        waves = [self._tail_wave() for _ in range(2)] + \
                [self._head_wave() for _ in range(98)]
        c = _classify_structural("s", waves)
        assert c["verdict"] == "HANDLER_COST_UPSTREAM"
        assert c["med_pos"] == pytest.approx(0.0)
        assert c["tail_share"] == pytest.approx(0.02)


class TestSyntheticJournalEndToEnd:
    """main() through synthetic journals: telemetry mode + structural mode."""

    @staticmethod
    def _create_table(con: sqlite3.Connection) -> None:
        con.execute("""CREATE TABLE operational_events(
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT,
            actor TEXT, event_type TEXT NOT NULL, correlation_id TEXT,
            severity TEXT, payload TEXT NOT NULL, payload_hash TEXT)""")

    def test_telemetry_mode_attribution(self, tmp_path, monkeypatch, capsys):
        # Dues every 80s against the real registry's 60s interval put both
        # schedules at duty ~0.76 < 0.8 (starved). Three wave_deadline skip
        # batches defer event_monitor (no own overruns) -> CLAIM_ORDER_TAIL;
        # incident_sweep has neither overruns nor deferrals -> AMBIGUOUS.
        db = tmp_path / "journal.db"
        con = sqlite3.connect(db)
        self._create_table(con)
        start = datetime(2026, 9, 27, 12, 0, 0)

        def stamp(offset: int) -> str:
            return (start + timedelta(seconds=offset)).isoformat()

        for i in range(100):
            for name in ("incident_sweep", "event_monitor"):
                con.execute(
                    "INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                    (stamp(i * 80), "schedule_due", json.dumps({"schedule": name})))
        for i in range(3):
            con.execute(
                "INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                (stamp(8000 + i * 10), "schedule_skipped",
                 json.dumps({"reason": "wave_deadline",
                             "schedules": ["event_monitor"]})))
        con.commit()
        con.close()
        monkeypatch.setattr(sys, "argv", [
            "dispatch_blocker_profile.py", "--db", str(db),
            "--session-start", "2026-09-27T12:00:00",
            "--session-end", "2026-09-27T14:13:59"])
        assert profile_main() == 0
        out = capsys.readouterr().out
        assert "mode: dispatcher telemetry" in out
        assert "CLAIM_ORDER_TAIL" in out and "event_monitor" in out
        assert "deferred before dispatch in 3 wave(s)" in out
        assert "deferral reasons in window: wave_deadline: 3" in out
        assert "AMBIGUOUS" in out and "incident_sweep" in out
        assert "no starved schedules" not in out

    def test_structural_mode_attribution(self, tmp_path, monkeypatch, capsys):
        # No overrun/skipped events at all -> structural mode. Five tight
        # waves (1s in-wave gaps) spaced 120s apart; incident_sweep claimed
        # last in each wave -> median position 1.00, tail share 100% ->
        # CLAIM_ORDER_TAIL. The 8-minute span needs --min-span-hours < 2.
        db = tmp_path / "journal.db"
        con = sqlite3.connect(db)
        self._create_table(con)
        start = datetime(2026, 9, 27, 10, 0, 0)
        for w in range(5):
            for i, name in enumerate(["a", "b", "c", "incident_sweep"]):
                con.execute(
                    "INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                    ((start + timedelta(seconds=w * 120 + i)).isoformat(),
                     "schedule_due", json.dumps({"schedule": name})))
        con.commit()
        con.close()
        monkeypatch.setattr(sys, "argv", [
            "dispatch_blocker_profile.py", "--db", str(db),
            "--session-start", "2026-09-27T10:00:00",
            "--session-end", "2026-09-27T10:08:59",
            "--min-span-hours", "0.01"])
        assert profile_main() == 0
        out = capsys.readouterr().out
        assert "mode: structural" in out
        assert "5 waves reconstructed" in out
        assert "CLAIM_ORDER_TAIL" in out and "incident_sweep" in out
        assert "median position 1.00, tail-position share 100%" in out
        assert "no starved schedules" not in out
