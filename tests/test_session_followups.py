"""Pin tests for run_session_followups' wait-loop liveness heuristic.

A hard crash emits no run_finished, so the wait loop must not trust an
"open" run row alone: _is_live also requires a heartbeat newer than the
run's start, and treats a heartbeat older than HEARTBEAT_STALE_S (15 min)
as an ended (dead) session. These tests pin:
- graceful end (run_finished >= run_started) -> not live;
- open run + fresh heartbeat -> live (keep waiting);
- open run + stale heartbeat -> treated as ended (the 2026-09-28 torch-crash
  gap: the executor would otherwise idle to its 240-min deadline);
- malformed/missing heartbeat -> not live (fail closed).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "scripts" / "run_session_followups.py"
_spec = importlib.util.spec_from_file_location("run_session_followups", SPEC_PATH)
mod = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_session_followups", mod)
_spec.loader.exec_module(mod)


@pytest.fixture()
def journal(tmp_path):
    con = sqlite3.connect(tmp_path / "trading_state.db")
    con.execute("CREATE TABLE operational_events (id INTEGER PRIMARY KEY,"
                " event_type TEXT, created_at TEXT)")
    con.execute("CREATE TABLE agent_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " heartbeat_at TEXT)")
    yield con
    con.close()


def _seed(con, run_started, run_finished=None, heartbeat=None):
    con.execute("INSERT INTO operational_events (event_type, created_at) VALUES ('run_started', ?)",
                (run_started,))
    if run_finished:
        con.execute("INSERT INTO operational_events (event_type, created_at) VALUES ('run_finished', ?)",
                    (run_finished,))
    con.execute("INSERT INTO agent_runs (heartbeat_at) VALUES (?)", (heartbeat,))
    con.commit()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_finished_session_is_not_live(journal):
    now = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)
    _seed(journal, _iso(now - timedelta(hours=2)), _iso(now - timedelta(minutes=5)),
          _iso(now - timedelta(minutes=5)))
    state = mod._is_live(journal, now_utc=now)
    assert state["open"] is False
    assert state["live"] is False


def test_open_run_with_fresh_heartbeat_is_live(journal):
    now = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)
    _seed(journal, _iso(now - timedelta(minutes=10)), None,
          _iso(now - timedelta(seconds=5)))
    state = mod._is_live(journal, now_utc=now)
    assert state["open"] is True
    assert state["live"] is True


def test_open_run_with_stale_heartbeat_is_treated_as_ended(journal):
    """The 2026-09-28 torch-crash gap: open run, no run_finished, heartbeat
    frozen 40 minutes ago -> the wait loop must proceed, not idle."""
    now = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)
    _seed(journal, _iso(now - timedelta(minutes=50)), None,
          _iso(now - timedelta(minutes=40)))
    state = mod._is_live(journal, now_utc=now)
    assert state["open"] is True
    assert state["live"] is False
    assert state["stale_heartbeat"] == _iso(now - timedelta(minutes=40))


def test_open_run_with_malformed_or_missing_heartbeat_fails_closed(journal):
    now = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)
    _seed(journal, _iso(now - timedelta(minutes=10)), None, None)
    assert mod._is_live(journal, now_utc=now)["live"] is False
    _seed(journal, _iso(now - timedelta(minutes=10)), None, "not-a-timestamp")
    assert mod._is_live(journal, now_utc=now)["live"] is False
