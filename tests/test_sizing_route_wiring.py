"""The sizing-route refresh is scheduled, and it never rewrites the journal.

Two properties matter here: the refresh actually runs on a cadence (so the
route can fire on its own), and it measures the evidence without touching
the rows that are the evidence.
"""
import hashlib
import json
import os
import sqlite3

import pytest

from core import sizing_route


def _journal(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS trades (ticket INTEGER PRIMARY KEY, "
                "closed_at TEXT, pnl REAL, provenance TEXT, symbol TEXT)")
    con.execute("INSERT OR REPLACE INTO trades(ticket, closed_at, pnl, provenance, symbol) "
                "VALUES (1, '2026-01-01T00:00:00', 12.5, 'paper_broker', 'EURUSD')")
    con.commit()
    con.close()
    return path


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _trade_rows(path):
    con = sqlite3.connect(path)
    rows = con.execute("SELECT ticket, pnl, provenance FROM trades ORDER BY ticket").fetchall()
    con.close()
    return rows


def test_the_refresh_measures_without_rewriting_the_journal(tmp_path):
    journal = _journal(str(tmp_path / "journal.db"))
    before_digest = _digest(journal)
    before_rows = _trade_rows(journal)

    record = sizing_route._read_only_track_record(journal, target_trades=200)

    assert _digest(journal) == before_digest
    assert _trade_rows(journal) == before_rows
    assert record["closed_fills"] == 1
    assert record["admissible_rows"] == 0  # no broker reconciliation yet
    assert record["ready"] is False


def test_the_refresh_writes_a_snapshot_and_keeps_the_route_legacy(tmp_path):
    journal = _journal(str(tmp_path / "journal.db"))
    snapshot_path = str(tmp_path / "artifacts" / "sizing_route.json")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "bootstrap_state.json").write_text(
        json.dumps({"execution_authority": False}), encoding="utf-8")

    snapshot = sizing_route.refresh_route_snapshot(
        journal, artifacts_root=str(artifacts), path=snapshot_path)

    assert os.path.exists(snapshot_path)
    assert snapshot["track_record"]["execution_authority"] is False
    decision = sizing_route.select_route(snapshot)
    assert decision.route == sizing_route.LEGACY
    assert decision.reasons


def test_the_executor_registry_accepts_the_new_schedule():
    from operations import executors

    executors.validate_registry()
    spec = executors.REGISTRY["sizing_route_refresh"]
    assert spec.principal == "MaintenanceAgent"
    handler = executors.resolve_handler(spec)
    assert callable(handler)


def test_the_scheduler_registers_the_refresh_with_a_valid_agent():
    from operations.agent_contracts import validate_schedule_agents
    from operations.agent_scheduler import SCHEDULES

    entry = next(s for s in SCHEDULES if s["name"] == "sizing_route_refresh")
    assert entry["agent"] == "MaintenanceAgent"
    assert entry["interval"] > 0
    validate_schedule_agents([entry["agent"]])


def test_the_executor_handles_the_schedule_and_writes_the_snapshot(tmp_path):
    from operations import executors

    journal = _journal(str(tmp_path / "journal.db"))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "bootstrap_state.json").write_text(
        json.dumps({"execution_authority": False}), encoding="utf-8")
    snapshot_path = str(artifacts / "sizing_route.json")

    class _Context:
        pass

    handled = executors.try_handle("sizing_route_refresh", {
        "journal_path": journal,
        "artifacts_root": str(artifacts),
    }, _Context())

    assert handled is True
    assert os.path.exists(snapshot_path)
    written = json.loads(open(snapshot_path, encoding="utf-8").read())
    assert written["track_record"]["closed_fills"] == 1
    # The decision the snapshot implies is the legacy route, by far.
    assert sizing_route.select_route(written).route == sizing_route.LEGACY
