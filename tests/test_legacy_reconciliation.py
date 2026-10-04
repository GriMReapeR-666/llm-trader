import sqlite3

import pytest

from operations.legacy_reconciliation import RETIRE_CONFIRMATION, quarantine, report, retire_unmatched


def _db(path):
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE trades (ticket INTEGER PRIMARY KEY, magic INTEGER NOT NULL, strategy TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, volume REAL NOT NULL, entry REAL NOT NULL, stop_loss REAL, take_profit REAL, risk_amount REAL NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT, exit_price REAL, pnl REAL, exit_reason TEXT, payload TEXT NOT NULL)")
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (7, 0, "legacy", "EURUSD", "BUY", 0.01, 1.1, 1.09, 1.12, 0.0,
                     "2026-01-01T00:00:00+00:00", None, None, None, None, "{}"))


def test_legacy_quarantine_requires_confirmation_and_preserves_trade(tmp_path):
    path = str(tmp_path / "state.db")
    _db(path)
    with pytest.raises(ValueError, match="confirmation token"):
        quarantine(path, 7, "reviewer", "reconcile first", "wrong")
    result = quarantine(path, 7, "reviewer", "reconcile first", "QUARANTINE_LEGACY")
    assert result["status"] == "QUARANTINED_PENDING_RECONCILIATION"
    state = report(path)
    assert state["status"] == "QUARANTINED_PENDING_RECONCILIATION"
    assert state["records"][0]["status"] == "QUARANTINED_PENDING_RECONCILIATION"
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT closed_at FROM trades WHERE ticket=7").fetchone()[0] is None


def test_legacy_retirement_requires_explicit_no_match_evidence_and_preserves_row(tmp_path):
    path = str(tmp_path / "state.db")
    _db(path)
    with pytest.raises(ValueError, match="confirmation token"):
        retire_unmatched(path, 7, "reviewer", "broker had no match", "wrong", False, "evidence")
    result = retire_unmatched(path, 7, "reviewer", "broker had no match", RETIRE_CONFIRMATION, False,
                              "MetaQuotes-Demo authenticated check: no position/order/deal match")
    assert result["status"] == "RETIRED_NO_BROKER_MATCH"
    state = report(path)
    assert state["status"] == "CLEAN"
    assert state["count"] == 0
    assert state["retired_count"] == 1
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT closed_at FROM trades WHERE ticket=7").fetchone()[0] is None
