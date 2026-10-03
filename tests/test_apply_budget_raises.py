"""Cross-check tests for apply_schedule_budget_raises.py's --verify mode.

The applier refuses to edit _SCHEDULE_HANDLER_BUDGETS until the report's
verdicts survive two checks:

1. Boundary re-derivation: every raise/watch row is re-classified from its
   own evidence using the SAME pinned thresholds as
   tests/test_scheduler_tuning_report.py (RAISE = due>=20 AND rate>=2% AND
   (p95 over-by>=5s OR median>=2s); AT_CAP at the interval/2 cap; else
   WATCH; inclusive boundaries; suggestion never lowers).
2. Journal provenance: overruns/due counts, trip rate, p95/median over-by,
   p95 elapsed and max budget are re-derived from the journal named in the
   report; a mismatch means the artifact does not describe that journal.

--apply runs the same cross-check and refuses (exit 1) on contradiction
unless --force. Nothing here touches the real orchestrator source or the
committed report artifact.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Load the applier by path: it is a scripts/ tool without a package layout.
_APPLIER = next(SCRIPTS.glob("apply*schedule*budget*.py"))
_spec = importlib.util.spec_from_file_location("apply_budget_raises_under_test", _APPLIER)
MOD = importlib.util.module_from_spec(_spec)
sys.modules["apply_budget_raises_under_test"] = MOD
_spec.loader.exec_module(MOD)


def _row(**kw) -> dict:
    base = {
        "schedule": "incident_sweep", "overruns": 3, "due_dispatches": 100,
        "trip_rate": 0.03, "current_budget_s": 5.0, "median_over_by_s": 9.0,
        "p95_over_by_s": 9.0, "p95_elapsed_s": 19.0, "suggested_budget_s": 28.5,
        "verdict": "RAISE", "rationale": "3.0% trip rate, p95 over by 9.0s",
    }
    base.update(kw)
    return base


def _report(raise_rows=(), watch_rows=(), journal="missing.db") -> dict:
    def _as_rows(value):
        if value is None:
            return []
        return list(value) if isinstance(value, (list, tuple)) else [value]
    return {
        "generated_at": "2026-09-27T16:05:34+00:00", "journal": journal,
        "budget_raise_candidates": {"raise": _as_rows(raise_rows),
                                    "watch": _as_rows(watch_rows)},
    }


def _seed_journal(db: Path, dues: int = 100, overruns: int = 3,
                  schedule: str = "incident_sweep") -> None:
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE operational_events(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT,
        actor TEXT, event_type TEXT NOT NULL, correlation_id TEXT,
        severity TEXT, payload TEXT NOT NULL, payload_hash TEXT)""")
    for i in range(dues):
        con.execute("INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                    ("2026-09-27T12:00:00+00:00", "schedule_due",
                     json.dumps({"schedule": schedule})))
    for _ in range(overruns):
        con.execute("INSERT INTO operational_events(created_at,event_type,payload) VALUES(?,?,?)",
                    ("2026-09-27T12:01:00+00:00", "schedule_handler_overrun",
                     json.dumps({"schedule": schedule, "budget_s": 5.0,
                                 "elapsed_s": 19.0, "over_by_s": 9.0})))
    con.commit()
    con.close()


class TestBoundaryCrossCheck:
    """Verdicts that contradict the pinned thresholds are refused."""

    def test_exact_raise_row_verifies(self):
        ok, problems, status = MOD._verify_report(_report(_row()), tolerance=0.1)
        assert ok and problems == []
        assert status == "unverified"  # journal missing: boundaries only

    def test_rate_just_below_2pct_cannot_be_raise(self):
        row = _row(trip_rate=0.0199, overruns=1, due_dispatches=100)
        ok, problems, _ = MOD._verify_report(_report(row))
        assert not ok
        assert any("contradicts the pinned" in p and "WATCH" in p for p in problems)

    def test_thin_baseline_cannot_be_raise(self):
        row = _row(due_dispatches=19, trip_rate=0.5)
        ok, problems, _ = MOD._verify_report(_report(row))
        assert not ok and any("WATCH" in p for p in problems)

    def test_severity_just_below_cannot_be_raise(self):
        row = _row(p95_over_by_s=4.9, median_over_by_s=1.9)
        ok, problems, _ = MOD._verify_report(_report(row))
        assert not ok and any("WATCH" in p for p in problems)

    def test_at_cap_row_verifies(self):
        # budget 60 on a 60s cadence: cap 30 binds -> suggested stays 60.
        row = _row(schedule="event_monitor", current_budget_s=60.0,
                   p95_elapsed_s=78.0, suggested_budget_s=60.0, verdict="AT_CAP")
        ok, problems, _ = MOD._verify_report(_report(row))
        assert ok and problems == []

    def test_at_cap_mislabeled_raise_is_refused(self):
        row = _row(schedule="event_monitor", current_budget_s=60.0,
                   p95_elapsed_s=78.0, suggested_budget_s=60.0, verdict="RAISE")
        ok, problems, _ = MOD._verify_report(_report(row))
        assert not ok and any("AT_CAP" in p for p in problems)

    def test_no_interval_caps_at_doubled_budget(self):
        # Unregistered schedule: cap = current*2 (40s) binds over p95*1.5
        # (45s). (A registry name would get the interval/2 cap instead.)
        row = _row(schedule="unknown_schedule", current_budget_s=20.0,
                   p95_elapsed_s=30.0, suggested_budget_s=40.0)
        ok, _, _ = MOD._verify_report(_report(row))
        assert ok

    def test_suggested_outside_tolerance_is_refused(self):
        row = _row(suggested_budget_s=45.0)  # recomputed 28.5
        ok, problems, _ = MOD._verify_report(_report(row))
        assert not ok
        assert any("outside tolerance" in p for p in problems)

    def test_suggested_within_tolerance_passes(self):
        row = _row(suggested_budget_s=28.55)
        ok, problems, _ = MOD._verify_report(_report(row))
        assert ok and problems == []

    def test_watch_rows_are_cross_checked_too(self):
        row = _row(due_dispatches=19, trip_rate=0.5, verdict="WATCH")
        ok, _, _ = MOD._verify_report(_report(watch_rows=[row]))
        assert ok


class TestJournalProvenance:
    """Row evidence must match what the named journal actually contains."""

    def test_matching_journal_confirms(self, tmp_path):
        db = tmp_path / "j.db"
        _seed_journal(db)
        ok, problems, status = MOD._verify_report(_report(_row(), journal=str(db)))
        assert ok and problems == [] and status == "confirmed"

    def test_overrun_count_mismatch_is_refused(self, tmp_path):
        db = tmp_path / "j.db"
        _seed_journal(db, overruns=4)
        ok, problems, _ = MOD._verify_report(_report(_row(), journal=str(db)))
        assert not ok
        assert any("overruns 3 != journal count 4" in p for p in problems)

    def test_due_count_mismatch_is_refused(self, tmp_path):
        db = tmp_path / "j.db"
        _seed_journal(db, dues=200)
        ok, problems, _ = MOD._verify_report(_report(_row(), journal=str(db)))
        assert not ok and any("due_dispatches" in p for p in problems)

    def test_trip_rate_mismatch_is_refused(self, tmp_path):
        db = tmp_path / "j.db"
        _seed_journal(db)
        row = _row(trip_rate=0.5)
        ok, problems, _ = MOD._verify_report(_report(row, journal=str(db)))
        assert not ok and any("trip_rate" in p for p in problems)

    def test_percentile_mismatch_is_refused(self, tmp_path):
        db = tmp_path / "j.db"
        _seed_journal(db)
        row = _row(p95_over_by_s=99.0, p95_elapsed_s=99.0)
        ok, problems, _ = MOD._verify_report(_report(row, journal=str(db)))
        assert not ok
        assert any("p95_over_by_s" in p for p in problems)
        assert any("p95_elapsed_s" in p for p in problems)

    def test_missing_journal_downgrades_to_unverified(self, tmp_path):
        ok, problems, status = MOD._verify_report(
            _report(_row(), journal=str(tmp_path / "nope.db")))
        assert ok and problems == [] and status == "unverified"

    def test_no_journal_field_is_unverified(self):
        ok, _, status = MOD._verify_report(_report(_row(), journal=""))
        assert ok and status == "unverified"

    def test_wrong_journal_structure_fails_closed(self, tmp_path):
        db = tmp_path / "j.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE other(x)")
        con.commit()
        con.close()
        ok, problems, status = MOD._verify_report(_report(_row(), journal=str(db)))
        assert not ok and status == "failed"
        assert any("operational_events" in p for p in problems)

    def test_empty_report_has_nothing_to_verify(self):
        ok, problems, status = MOD._verify_report(_report())
        assert ok and problems == [] and status == "no_rows"


class TestApplyGate:
    """--apply runs the cross-check first; a failed check blocks the write."""

    @staticmethod
    def _orchestrator(tmp_path: Path) -> Path:
        orch = tmp_path / "llm_orchestrator.py"
        orch.write_text(
            "_SCHEDULE_HANDLER_BUDGETS: Dict[str, float] = {\n"
            '    "incident_sweep": 5.0,\n'
            '    "event_monitor": 60.0,\n'
            "    }\n",
            encoding="utf-8")
        return orch

    def test_apply_blocked_on_failed_verification(self, tmp_path, monkeypatch):
        rep = tmp_path / "report.json"
        rep.write_text(json.dumps(_report(_row(trip_rate=0.001))), encoding="utf-8")
        orch = self._orchestrator(tmp_path)
        monkeypatch.setattr(MOD, "ORCHESTRATOR", orch)
        monkeypatch.setattr(sys, "argv",
                            ["apply.py", "--report", str(rep), "--apply"])
        assert MOD.main() == 1
        assert "incident_sweep" in orch.read_text(encoding="utf-8")
        assert "5.0" in orch.read_text(encoding="utf-8")  # untouched

    def test_force_overrides_failed_verification(self, tmp_path, monkeypatch):
        rep = tmp_path / "report.json"
        rep.write_text(json.dumps(_report(_row(trip_rate=0.001))), encoding="utf-8")
        orch = self._orchestrator(tmp_path)
        monkeypatch.setattr(MOD, "ORCHESTRATOR", orch)
        monkeypatch.setattr(sys, "argv",
                            ["apply.py", "--report", str(rep), "--apply", "--force"])
        assert MOD.main() == 0
        assert "28.5" in orch.read_text(encoding="utf-8")

    def test_apply_runs_after_clean_verification(self, tmp_path, monkeypatch):
        db = tmp_path / "j.db"
        _seed_journal(db)
        rep = tmp_path / "report.json"
        rep.write_text(json.dumps(_report(_row(), journal=str(db))), encoding="utf-8")
        orch = self._orchestrator(tmp_path)
        monkeypatch.setattr(MOD, "ORCHESTRATOR", orch)
        monkeypatch.setattr(sys, "argv",
                            ["apply.py", "--report", str(rep), "--apply"])
        assert MOD.main() == 0
        text = orch.read_text(encoding="utf-8")
        assert "28.5" in text and "raised from 5.0s" in text

    def test_verify_mode_exit_codes(self, tmp_path, monkeypatch):
        rep = tmp_path / "report.json"
        rep.write_text(json.dumps(_report(_row())), encoding="utf-8")
        monkeypatch.setattr(sys, "argv",
                            ["apply.py", "--report", str(rep), "--verify"])
        assert MOD.main() == 0
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(_report(_row(trip_rate=0.001))), encoding="utf-8")
        monkeypatch.setattr(sys, "argv",
                            ["apply.py", "--report", str(bad), "--verify"])
        assert MOD.main() == 1

    def test_verify_with_no_verdict_rows_is_a_noop(self, tmp_path, monkeypatch):
        rep = tmp_path / "report.json"
        rep.write_text(json.dumps(_report()), encoding="utf-8")
        monkeypatch.setattr(sys, "argv",
                            ["apply.py", "--report", str(rep), "--verify"])
        assert MOD.main() == 0
