"""Unit tests for scheduler_tuning_report.py's RAISE/AT_CAP/WATCH verdict logic.

Feeds synthetic overrun evidence through the (pure) classifier so the
thresholds are pinned before the next live session:

- RAISE requires BOTH frequency (>=2% trip rate) and severity (p95 over-by
  >=5s OR median over-by >=2s) over a >=20-dispatch baseline.
- AT_CAP when the measured suggestion cannot exceed the current budget
  (interval/2 heuristic cap; the schedule interval needs raising instead).
- Everything else WATCH; boundaries are inclusive; verdicts never lower.
The end-to-end test feeds a real synthetic journal (overruns + schedule_due
rows) through main() and pins both the printed verdict and the artifact:
--artifact keeps tests away from the committed report JSON.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from scheduler_tuning_report import (  # noqa: E402
    _classify_overruns,
    _percentile,
    main as report_main,
)


def _overruns(count: int, elapsed: float, over_by: float, budget: float) -> dict:
    return {"count": count, "budgets": [budget] * count,
            "elapsed": [elapsed] * count, "over_by": [over_by] * count}


class TestThresholdPinning:
    """The exact numeric boundaries of the RAISE/AT_CAP/WATCH rules."""

    def test_raise_at_exact_thresholds(self):
        # Exactly 2% trip rate (1/50), p95 over-by exactly 5s, 50 dispatches.
        overruns = {"s": _overruns(count=1, elapsed=10.0, over_by=5.0, budget=10.0)}
        raise_rows, _ = _classify_overruns(
            overruns, due_denominators={"s": 50}, intervals={"s": 60},
            budgets={"s": 10.0})
        assert len(raise_rows) == 1
        row = raise_rows[0]
        assert row["verdict"] == "RAISE"
        assert row["trip_rate"] == 0.02
        assert row["suggested_budget_s"] == 15.0   # p95_elapsed(10) * 1.5, under the 30s cap

    def test_watch_below_frequency_threshold(self):
        # 1.9% trip rate (< 2%): WATCH even with severe over-by.
        overruns = {"s": _overruns(count=19, elapsed=20.0, over_by=10.0, budget=10.0)}
        raise_rows, watch_rows = _classify_overruns(
            overruns, due_denominators={"s": 1000}, intervals={"s": 60},
            budgets={"s": 10.0})
        assert raise_rows == []
        assert len(watch_rows) == 1
        assert watch_rows[0]["verdict"] == "WATCH"
        assert "only 1.90% trip rate" in watch_rows[0]["rationale"]

    def test_watch_below_severity_threshold(self):
        # High frequency but trivial over-by (<5s p95, <2s median): WATCH.
        overruns = {"s": _overruns(count=5, elapsed=11.0, over_by=1.0, budget=10.0)}
        raise_rows, watch_rows = _classify_overruns(
            overruns, due_denominators={"s": 100}, intervals={"s": 60},
            budgets={"s": 10.0})
        assert raise_rows == []
        assert watch_rows[0]["verdict"] == "WATCH"
        assert "over-by seconds are trivial" in watch_rows[0]["rationale"]

    def test_watch_thin_dispatch_baseline(self):
        # Severe + frequent, but only 19 dispatches: WATCH.
        overruns = {"s": _overruns(count=5, elapsed=30.0, over_by=20.0, budget=10.0)}
        raise_rows, watch_rows = _classify_overruns(
            overruns, due_denominators={"s": 19}, intervals={"s": 60},
            budgets={"s": 10.0})
        assert raise_rows == []
        assert watch_rows[0]["verdict"] == "WATCH"
        assert "insufficient dispatch baseline" in watch_rows[0]["rationale"]

    def test_median_severity_lone_satisfies_raise(self):
        # p95 over-by < 5s but median >= 2s: severity satisfied via median.
        overruns = {"s": _overruns(count=20, elapsed=15.0, over_by=2.5, budget=10.0)}
        raise_rows, _ = _classify_overruns(
            overruns, due_denominators={"s": 500}, intervals={"s": 60},
            budgets={"s": 10.0})
        assert len(raise_rows) == 1 and raise_rows[0]["verdict"] == "RAISE"

    def test_at_cap_when_suggestion_cannot_exceed_current(self):
        # budget 60s on a 60s cadence: cap = 30 < 60, so the raise never
        # lowers -> suggested == current -> AT_CAP despite qualifying rates.
        overruns = {"s": _overruns(count=13, elapsed=78.0, over_by=18.0, budget=60.0)}
        raise_rows, watch_rows = _classify_overruns(
            overruns, due_denominators={"s": 420}, intervals={"s": 60},
            budgets={"s": 60.0})
        assert watch_rows == []
        assert len(raise_rows) == 1
        row = raise_rows[0]
        assert row["verdict"] == "AT_CAP"
        assert row["suggested_budget_s"] == 60.0
        assert "raise the schedule interval instead" in row["rationale"]

    def test_no_interval_doubles_budget_as_cap(self):
        # Unknown interval: cap = current * 2 (20s), which BINDS over the
        # p95*1.5 suggestion (45s) -> RAISE capped at the doubled budget.
        overruns = {"s": _overruns(count=3, elapsed=30.0, over_by=20.0, budget=10.0)}
        raise_rows, _ = _classify_overruns(
            overruns, due_denominators={"s": 100}, intervals={},
            budgets={"s": 10.0})
        assert len(raise_rows) == 1
        assert raise_rows[0]["verdict"] == "RAISE"
        assert raise_rows[0]["suggested_budget_s"] == 20.0

    def test_verdict_never_lowers(self):
        # p95 elapsed * 1.5 (15s) < current (20s): suggestion floors at the
        # current budget; with qualifying evidence this is AT_CAP, not 15s.
        overruns = {"s": _overruns(count=4, elapsed=10.0, over_by=2.5, budget=20.0)}
        raise_rows, _ = _classify_overruns(
            overruns, due_denominators={"s": 100}, intervals={"s": 60},
            budgets={"s": 20.0})
        assert len(raise_rows) == 1
        assert raise_rows[0]["verdict"] == "AT_CAP"
        assert raise_rows[0]["suggested_budget_s"] == 20.0

    def test_percentile_boundaries(self):
        assert _percentile([], 0.95) == 0.0
        assert _percentile([5.0], 0.95) == 5.0
        # round() (banker's rounding) picks the UPPER middle element for
        # even counts: index round(0.5*3)=2. Pinned as-is: the report's
        # verdict math has always used this element-pick, while the
        # dashboard's SQL median averages the middle rows (a separate,
        # deliberately different implementation).
        assert _percentile([10.0, 20.0, 30.0, 40.0], 0.5) == 30.0
        assert _percentile([10.0, 20.0, 30.0, 40.0], 0.95) == 40.0  # ceil(5%) from top


class TestSyntheticJournalEndToEnd:
    """Overruns + schedule_due rows through main() with a redirected artifact."""

    @staticmethod
    def _seed_journal(path: Path) -> None:
        con = sqlite3.connect(path)
        con.execute("""CREATE TABLE operational_events(
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT,
            actor TEXT, event_type TEXT NOT NULL, correlation_id TEXT,
            severity TEXT, payload TEXT NOT NULL, payload_hash TEXT)""")
        stamp = "2026-09-27T12:00:00+00:00"
        rows = []
        # 'incident_sweep' (registry interval 60s): qualifies for RAISE
        # (5% trip, p95 over-by 9s) with cap 30s > p95*1.5 = 28.5s.
        for _ in range(100):
            rows.append(("schedule_due", json.dumps({"schedule": "incident_sweep"})))
        for _ in range(5):
            rows.append(("schedule_handler_overrun",
                         json.dumps({"schedule": "incident_sweep", "budget_s": 5.0,
                                     "elapsed_s": 19.0, "over_by_s": 9.0})))
        # 'event_monitor' (registry interval 60s, budget 60s): qualifies on
        # rate+severity but sits AT its interval/2 cap (30s < 60s).
        for _ in range(100):
            rows.append(("schedule_due", json.dumps({"schedule": "event_monitor"})))
        for _ in range(3):
            rows.append(("schedule_handler_overrun",
                         json.dumps({"schedule": "event_monitor", "budget_s": 60.0,
                                     "elapsed_s": 78.0, "over_by_s": 18.0})))
        for i, (et, payload) in enumerate(rows):
            con.execute(
                "INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                (stamp, et, payload))
        con.commit()
        con.close()

    def test_main_classifies_and_writes_redirected_artifact(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "journal.db"
        self._seed_journal(db)
        artifact = tmp_path / "report.json"
        monkeypatch.setattr(sys, "argv", [
            "scheduler_tuning_report.py", "--db", str(db),
            "--artifact", str(artifact)])
        assert report_main() == 0
        out = capsys.readouterr().out
        assert "RAISE" in out and "incident_sweep" in out
        assert "AT_CAP" in out and "event_monitor" in out
        data = json.loads(artifact.read_text(encoding="utf-8"))
        by_name = {r["schedule"]: r for r in data["budget_raise_candidates"]["raise"]}
        assert by_name["incident_sweep"]["verdict"] == "RAISE"
        assert by_name["incident_sweep"]["suggested_budget_s"] == 28.5  # 19*1.5, cap 30s
        assert by_name["event_monitor"]["verdict"] == "AT_CAP"
        assert by_name["event_monitor"]["suggested_budget_s"] == 60.0


class TestLiveSessionHeuristic:
    """'Live' = an OPEN run with fresh activity, not mere recency.

    Regression: the old 6h-recency window flagged a finished 5-minute
    maintenance pass as 'live session: DETECTED' for hours after it ended.
    Pinned through main() on synthetic journals (timestamps relative to
    real 'now', since the heuristic compares against the wall clock).
    """

    @staticmethod
    def _seed_run(path: Path, start: datetime, dues=(), finish: datetime | None = None):
        con = sqlite3.connect(path)
        con.execute("""CREATE TABLE operational_events(
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT,
            actor TEXT, event_type TEXT NOT NULL, correlation_id TEXT,
            severity TEXT, payload TEXT NOT NULL, payload_hash TEXT)""")
        con.execute("INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                    (start.isoformat(), "run_started", "{}"))
        for name in dues:
            con.execute("INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                        ((start + timedelta(seconds=30)).isoformat(),
                         "schedule_due", json.dumps({"schedule": name})))
        if finish is not None:
            con.execute("INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                        (finish.isoformat(), "run_finished", "{}"))
        con.commit()
        con.close()

    def test_finished_maintenance_run_is_not_live(self, tmp_path, monkeypatch, capsys):
        # The exact regression: a 5-minute run that finished seconds ago
        # (well inside the old 6h recency window) with dues during it.
        now = datetime.now(timezone.utc)
        db = tmp_path / "j.db"
        self._seed_run(db, start=now - timedelta(minutes=5),
                       dues=("market_health", "incident_sweep"),
                       finish=now - timedelta(seconds=30))
        artifact = tmp_path / "r.json"
        monkeypatch.setattr(sys, "argv", [
            "scheduler_tuning_report.py", "--db", str(db), "--artifact", str(artifact)])
        assert report_main() == 0
        out = capsys.readouterr().out
        assert "live session: not detected" in out
        assert "run_finished" in out  # evidence shows the finish stamp
        data = json.loads(artifact.read_text(encoding="utf-8"))
        assert data["live_session"] is False
        assert data["live_session_evidence"]["open_run"] is False

    def test_open_run_with_fresh_activity_is_live(self, tmp_path, monkeypatch, capsys):
        now = datetime.now(timezone.utc)
        db = tmp_path / "j.db"
        self._seed_run(db, start=now - timedelta(minutes=3),
                       dues=("market_health",))
        artifact = tmp_path / "r.json"
        monkeypatch.setattr(sys, "argv", [
            "scheduler_tuning_report.py", "--db", str(db), "--artifact", str(artifact)])
        assert report_main() == 0
        assert "live session: DETECTED" in capsys.readouterr().out
        data = json.loads(artifact.read_text(encoding="utf-8"))
        assert data["live_session"] is True
        assert data["live_session_evidence"]["open_run"] is True
        assert data["live_session_evidence"]["recent_activity"] is True

    def test_open_but_quiet_run_is_not_live(self, tmp_path, monkeypatch, capsys):
        # Crash edge: run_started with no run_finished, but silent for
        # >15 minutes -> not live despite the technically open run.
        now = datetime.now(timezone.utc)
        db = tmp_path / "j.db"
        self._seed_run(db, start=now - timedelta(minutes=30),
                       dues=("market_health",))
        artifact = tmp_path / "r.json"
        monkeypatch.setattr(sys, "argv", [
            "scheduler_tuning_report.py", "--db", str(db), "--artifact", str(artifact)])
        assert report_main() == 0
        assert "live session: not detected" in capsys.readouterr().out
        data = json.loads(artifact.read_text(encoding="utf-8"))
        assert data["live_session"] is False
        assert data["live_session_evidence"]["open_run"] is True
        assert data["live_session_evidence"]["recent_activity"] is False


class TestBootStageTimeouts:
    """boot_fetch_stage_timeout telemetry in the tuning report.

    The boot watchdog emits these when a pre-pulse ingest stage misses its
    deadline (the hung-provider signature). They must appear in the same
    post-session duty report as handler overruns: per-stage trips, budgets,
    p95 elapsed, a suggested budget, and the artifact key.
    """

    @staticmethod
    def _seed_timeouts(db_path: Path, rows: list[tuple[str, str, str]]):
        con = sqlite3.connect(db_path)
        con.execute("""CREATE TABLE operational_events(
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT,
            actor TEXT, event_type TEXT NOT NULL, correlation_id TEXT,
            severity TEXT, payload TEXT NOT NULL, payload_hash TEXT)""")
        for created_at, event_type, payload in rows:
            con.execute(
                "INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                (created_at, event_type, payload))
        con.commit()
        con.close()

    def test_aggregator_folds_rows_per_stage(self):
        from scheduler_tuning_report import _aggregate_boot_stage_timeouts
        rows = [
            {"created_at": "2026-09-30T05:07:20+00:00", "payload": json.dumps(
                {"stage": "data_source_pipeline", "budget_s": 45.0, "elapsed_s": 45.2})},
            {"created_at": "2026-09-30T05:07:50+00:00", "payload": json.dumps(
                {"stage": "data_source_pipeline", "budget_s": 45.0, "elapsed_s": 52.0})},
            {"created_at": "2026-09-30T05:07:55+00:00", "payload": json.dumps(
                {"stage": "calendar_collect", "budget_s": 30.0, "elapsed_s": 30.1})},
            {"created_at": "2026-09-30T05:07:56+00:00", "payload": "not json"},
        ]
        stages = _aggregate_boot_stage_timeouts(rows)
        assert set(stages) == {"data_source_pipeline", "calendar_collect"}
        pipe = stages["data_source_pipeline"]
        assert pipe["trips"] == 2
        assert pipe["budget_s"] == 45.0
        assert pipe["elapsed_p95_s"] == 52.0
        assert pipe["suggested_budget_s"] == 78.0  # p95 52 * 1.5
        assert pipe["first_at"].startswith("2026-09-30T05:07:20")
        assert pipe["last_at"].startswith("2026-09-30T05:07:50")
        assert stages["calendar_collect"]["trips"] == 1

    def test_aggregator_handles_degenerate_payloads(self):
        from scheduler_tuning_report import _aggregate_boot_stage_timeouts
        stages = _aggregate_boot_stage_timeouts([
            {"created_at": "2026-09-30T05:07:20+00:00", "payload": json.dumps({})},
            {"created_at": None, "payload": json.dumps(
                {"stage": "x", "budget_s": "bogus", "elapsed_s": None})},
        ])
        assert stages["?"]["trips"] == 1
        assert stages["?"]["budget_s"] is None
        assert stages["?"]["suggested_budget_s"] is None
        assert stages["x"]["budget_s"] is None and stages["x"]["elapsed_p95_s"] is None

    def test_main_reports_timeouts_and_artifact_key(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "j.db"
        self._seed_timeouts(db, [
            ("2026-09-30T05:07:20+00:00", "run_started", "{}"),
            ("2026-09-30T05:07:22+00:00", "boot_fetch_stage_timeout", json.dumps(
                {"stage": "data_source_pipeline", "budget_s": 45.0, "elapsed_s": 45.2})),
            ("2026-09-30T05:07:40+00:00", "boot_fetch_stage_timeout", json.dumps(
                {"stage": "data_source_pipeline", "budget_s": 45.0, "elapsed_s": 60.0})),
            ("2026-09-30T05:08:00+00:00", "schedule_due", json.dumps({"schedule": "market_health"})),
        ])
        artifact = tmp_path / "r.json"
        monkeypatch.setattr(sys, "argv", [
            "scheduler_tuning_report.py", "--db", str(db), "--artifact", str(artifact)])
        assert report_main() == 0
        out = capsys.readouterr().out
        assert "boot-fetch watchdog trips" in out
        assert "data_source_pipeline: 2 trips" in out
        assert "suggested 90.0s" in out  # p95 60 * 1.5
        data = json.loads(artifact.read_text(encoding="utf-8"))
        pipe = data["boot_stage_timeouts"]["data_source_pipeline"]
        assert pipe["trips"] == 2 and pipe["suggested_budget_s"] == 90.0

    def test_main_without_timeouts_prints_placeholder(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "j.db"
        self._seed_timeouts(db, [
            ("2026-09-30T05:07:20+00:00", "run_started", "{}"),
            ("2026-09-30T05:08:00+00:00", "schedule_due", json.dumps({"schedule": "market_health"})),
        ])
        artifact = tmp_path / "r.json"
        monkeypatch.setattr(sys, "argv", [
            "scheduler_tuning_report.py", "--db", str(db), "--artifact", str(artifact)])
        assert report_main() == 0
        out = capsys.readouterr().out
        assert "no boot_fetch_stage_timeout events yet" in out
        data = json.loads(artifact.read_text(encoding="utf-8"))
        assert data["boot_stage_timeouts"] == {}
