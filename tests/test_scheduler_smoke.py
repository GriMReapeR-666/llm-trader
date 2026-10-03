"""Tests for the marker-footed scheduler smoke (scripts/scheduler_smoke.py).

All checks run against sandbox fixtures — no live journal, no autonomy state,
no network.  The pure step functions take their inputs as parameters, so the
tests exercise the real logic without touching the repo-root runtime state.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMOKE_PATH = ROOT / "scripts" / "scheduler_smoke.py"

_spec = importlib.util.spec_from_file_location("scheduler_smoke", SMOKE_PATH)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def _journal(tmp_path: Path) -> Path:
    """Create a minimal journal with the core scheduler tables."""
    db = tmp_path / "trading_state.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE agent_runs (
          run_id TEXT PRIMARY KEY, agent TEXT NOT NULL, started_at TEXT NOT NULL,
          heartbeat_at TEXT NOT NULL, status TEXT NOT NULL, config_hash TEXT,
          code_version TEXT, payload TEXT NOT NULL
        );
        CREATE TABLE operational_events (
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT,
          actor TEXT NOT NULL, event_type TEXT NOT NULL, correlation_id TEXT,
          severity TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE agent_schedules (
          schedule_name TEXT PRIMARY KEY, agent TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
          next_run_at TEXT NOT NULL, last_run_at TEXT, enabled INTEGER NOT NULL DEFAULT 1,
          payload TEXT NOT NULL
        );
        """
    )
    con.commit()
    con.close()
    return db


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _seed_registry(db: Path) -> None:
    """Insert rows matching the real code registry so drift checks pass."""
    registry = smoke._load_schedule_registry()
    con = sqlite3.connect(db)
    for item in registry:
        con.execute(
            "INSERT INTO agent_schedules VALUES (?,?,?,?,?,?,?)",
            (item["name"], item["agent"], item["interval"],
             "2026-01-01T00:00:00+00:00", None, 1, json.dumps(item["payload"])),
        )
    con.commit()
    con.close()


class TestJournalStep:
    def test_missing_journal_fails(self, tmp_path):
        ok, note = smoke.step_journal(tmp_path / "absent.db")
        assert ok is False
        assert "missing" in note

    def test_healthy_journal_passes(self, tmp_path):
        db = _journal(tmp_path)
        ok, note = smoke.step_journal(db)
        assert ok is True
        assert "quick_check: ok" in note
        assert "integrity_check: ok" in note

    def test_journal_missing_core_tables_fails(self, tmp_path):
        db = tmp_path / "trading_state.db"
        sqlite3.connect(db).execute("CREATE TABLE other (x)")
        ok, note = smoke.step_journal(db)
        assert ok is False
        assert "missing core tables" in note


class TestScheduleDrift:
    def _rows(self, overrides=None):
        base = [
            {"schedule_name": "alpha", "agent": "A", "interval_seconds": 60,
             "next_run_at": "", "last_run_at": None, "enabled": 1,
             "payload": json.dumps({"kind": "health"})},
            {"schedule_name": "beta", "agent": "B", "interval_seconds": 3600,
             "next_run_at": "", "last_run_at": None, "enabled": 1,
             "payload": json.dumps({"kind": "review"})},
        ]
        for row, patch in zip(base, overrides or [{}, {}]):
            row.update(patch)
        return base

    def _registry(self):
        return [
            {"name": "alpha", "agent": "A", "interval": 60, "payload": {"kind": "health"}},
            {"name": "beta", "agent": "B", "interval": 3600, "payload": {"kind": "review"}},
        ]

    def test_matching_registry_passes(self):
        ok, note = smoke._schedule_drift(self._rows(), self._registry())
        assert ok is True
        assert "matches code" in note

    def test_unregistered_table_row_fails(self):
        rows = self._rows() + [
            {"schedule_name": "ghost", "agent": "X", "interval_seconds": 1,
             "next_run_at": "", "last_run_at": None, "enabled": 1, "payload": "{}"},
        ]
        ok, note = smoke._schedule_drift(rows, self._registry())
        assert ok is False
        assert "unregistered rows" in note

    def test_missing_registration_fails(self):
        # Registry knows a schedule the table has no row for.
        registry = self._registry() + [
            {"name": "gamma", "agent": "C", "interval": 30, "payload": {"kind": "sweep"}},
        ]
        ok, note = smoke._schedule_drift(self._rows(), registry)
        assert ok is False
        assert "NOT registered" in note and "gamma" in note

    def test_interval_drift_fails(self):
        ok, note = smoke._schedule_drift(
            self._rows([{"interval_seconds": 61}, {}]), self._registry())
        assert ok is False
        assert "alpha: interval" in note

    def test_payload_kind_drift_fails(self):
        ok, note = smoke._schedule_drift(
            self._rows([{}, {"payload": json.dumps({"kind": "other"})}]), self._registry())
        assert ok is False
        assert "beta: payload kind" in note

    def test_step_against_real_journal_and_registry(self, tmp_path):
        # The real registry must import; the sandbox journal seeds rows from it.
        registry = smoke._load_schedule_registry()
        assert len(registry) > 10
        db = _journal(tmp_path)
        _seed_registry(db)
        ok, note = smoke.step_schedules(db, registry=registry)
        assert ok is True, note


class TestEventsStep:
    def test_critical_severity_gates(self, tmp_path):
        db = _journal(tmp_path)
        now = datetime.now(timezone.utc)
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO operational_events VALUES (?,?,?,?,?,?,?,?,?)",
            (1, _iso(now), None, "actor", "evt", None, "critical", "{}", ""),
        )
        con.execute(
            "INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?)",
            ("r1", "A", _iso(now), _iso(now), "RUNNING", None, None, "{}"),
        )
        con.commit()
        con.close()
        ok, note = smoke.step_events(db, stale_minutes=15, now=now)
        assert ok is False
        assert "critical/fatal" in note

    def test_stale_running_is_warning_not_gate(self, tmp_path):
        db = _journal(tmp_path)
        now = datetime.now(timezone.utc)
        stale_hb = now - timedelta(minutes=120)
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?)",
            ("r1", "A", _iso(stale_hb), _iso(stale_hb), "RUNNING", None, None, "{}"),
        )
        con.commit()
        con.close()
        ok, note = smoke.step_events(db, stale_minutes=15, now=now)
        assert ok is True
        assert "WARNING" in note and "RUNNING" in note

    def test_fresh_running_and_no_critical_passes(self, tmp_path):
        db = _journal(tmp_path)
        now = datetime.now(timezone.utc)
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?)",
            ("r1", "A", _iso(now), _iso(now), "RUNNING", None, None, "{}"),
        )
        con.commit()
        con.close()
        ok, note = smoke.step_events(db, stale_minutes=15, now=now)
        assert ok is True
        assert "WARNING" not in note


class TestAutonomyStep:
    def test_absent_state_is_ok(self, tmp_path):
        ok, note = smoke.step_autonomy(tmp_path / "autonomy_state.json")
        assert ok is True
        assert "absent" in note

    def test_malformed_json_fails(self, tmp_path):
        p = tmp_path / "autonomy_state.json"
        p.write_text("{not json", encoding="utf-8")
        ok, note = smoke.step_autonomy(p)
        assert ok is False
        assert "unreadable/malformed" in note

    def test_unknown_level_fails(self, tmp_path):
        p = tmp_path / "autonomy_state.json"
        p.write_text(json.dumps({"level": "L99_BANANA", "halted": False, "paused": False}),
                     encoding="utf-8")
        ok, note = smoke.step_autonomy(p)
        assert ok is False
        assert "not in known levels" in note

    def test_non_bool_flag_fails(self, tmp_path):
        p = tmp_path / "autonomy_state.json"
        p.write_text(json.dumps({"level": "L1_PAPER", "halted": "no", "paused": False}),
                     encoding="utf-8")
        ok, note = smoke.step_autonomy(p)
        assert ok is False
        assert "halted" in note

    def test_healthy_state_with_real_vocabulary_passes(self, tmp_path):
        # Uses the real LEVELS import (operations/autonomy is import-safe,
        # side-effect free at module scope) to lock the vocabulary check.
        from operations.autonomy import LEVELS

        p = tmp_path / "autonomy_state.json"
        p.write_text(json.dumps({"level": "L1_PAPER", "halted": False,
                                 "paused": False, "history": []}), encoding="utf-8")
        ok, note = smoke.step_autonomy(p)
        assert ok is True, note
        assert "L1_PAPER" in note


class TestReportAndVerdict:
    def test_all_green_verdict_and_marker(self):
        report = smoke.build_report([("a", True, "ok"), ("b", True, "ok")])
        assert report["verdict"] == "GREEN"
        assert report["failures"] == []
        assert report["marker"].startswith("END-MARKER-SMOKE-")

    def test_any_failure_is_red_and_lists_steps(self):
        report = smoke.build_report([("a", True, "ok"), ("b", False, "bad")])
        assert report["verdict"] == "RED"
        assert report["failures"] == ["b"]

    def test_json_shape_roundtrip(self):
        report = smoke.build_report([("a", True, "note")])
        parsed = json.loads(json.dumps(report))
        assert parsed["verdict"] == "GREEN"
        assert parsed["steps"][0]["name"] == "a"


class TestWiring:
    def test_suites_match_the_committed_battery(self):
        assert smoke.SUITES[0] == "tests/test_agent_operations.py"
        assert "tests/test_scheduler_pulse.py" in smoke.SUITES
        assert len(smoke.SUITES) == 8

    def test_module_is_callable_without_live_state(self, tmp_path, monkeypatch):
        # main() with --skip-tests and a sandbox journal must run end to end
        # and write a marker-footed report, exiting 0 on green.
        db = _journal(tmp_path)
        _seed_registry(db)
        state = tmp_path / "autonomy_state.json"
        state.write_text(json.dumps({"level": "L1_PAPER", "halted": False,
                                     "paused": False, "history": []}), encoding="utf-8")
        monkeypatch.setattr(smoke, "JOURNAL", db)
        monkeypatch.setattr(smoke, "AUTONOMY_STATE", state)
        monkeypatch.setattr(smoke, "ARTIFACTS", tmp_path / "artifacts")
        monkeypatch.setattr(sys, "argv", ["scheduler_smoke.py", "--skip-tests"])
        rc = smoke.main()
        assert rc == 0
        report_text = (tmp_path / "artifacts" / "scheduler_smoke_report.txt").read_text(encoding="utf-8")
        assert "SCHEDULER-SMOKE-VERDICT: GREEN" in report_text
        assert report_text.rstrip().splitlines()[-1].startswith("END-MARKER-SMOKE-")
