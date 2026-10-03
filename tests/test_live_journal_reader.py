"""Tests for ui/live_journal_reader.py — live journal reader for OpenTUI."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import pytest


@pytest.fixture
def journal_db(tmp_path):
    """Create a minimal trading_state.db with the required schema."""
    db_path = str(tmp_path / "trading_state.db")
    con = sqlite3.connect(db_path)
    con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS trades (
            ticket INTEGER PRIMARY KEY, magic INTEGER NOT NULL,
            strategy TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
            volume REAL NOT NULL, entry REAL NOT NULL, stop_loss REAL,
            take_profit REAL, risk_amount REAL NOT NULL,
            opened_at TEXT NOT NULL, closed_at TEXT, exit_price REAL,
            pnl REAL, exit_reason TEXT, payload TEXT NOT NULL,
            setup_id TEXT, gross_pnl REAL, commission REAL, swap REAL
        );
        CREATE TABLE IF NOT EXISTS position_observations (
            id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
            ticket INTEGER NOT NULL, price REAL NOT NULL,
            unrealized_pnl REAL, r_multiple REAL,
            source TEXT NOT NULL DEFAULT 'unknown',
            quote_time_msc INTEGER, tick_age_ms REAL, spread REAL,
            mark_quality TEXT NOT NULL DEFAULT 'UNKNOWN'
        );
        CREATE TABLE IF NOT EXISTS trade_lifecycle_events (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
            ticket INTEGER, setup_key TEXT, event_type TEXT NOT NULL,
            actor TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
            setup_key TEXT NOT NULL, strategy TEXT NOT NULL,
            symbol TEXT NOT NULL, side TEXT NOT NULL,
            payload TEXT NOT NULL, approved INTEGER NOT NULL,
            reasons TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
    """)
    con.close()
    return db_path


def _insert_open_trade(con, ticket=1001, symbol="EURUSD", side="BUY",
                       strategy="scalp", volume=0.1, entry=1.1000,
                       sl=1.0950, tp=1.1100, risk=50.0,
                       opened_at=None, gross_pnl=0.0):
    if opened_at is None:
        opened_at = datetime.now(timezone.utc).isoformat()
    con.execute(
        "INSERT INTO trades(ticket,magic,strategy,symbol,side,volume,"
        "entry,stop_loss,take_profit,risk_amount,opened_at,closed_at,"
        "pnl,exit_reason,payload,setup_id,gross_pnl,commission,swap) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,NULL,?,?,?)",
        (ticket, 460001, strategy, symbol, side, volume, entry, sl, tp,
         risk, opened_at, 0.0, "", "{}", gross_pnl, 0.0, 0.0),
    )


def _insert_observation(con, ticket=1001, price=1.1010, unrealized_pnl=10.0,
                        r_multiple=0.2, mark_quality="LIVE"):
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        "INSERT INTO position_observations(observed_at,ticket,price,"
        "unrealized_pnl,r_multiple,source,mark_quality) "
        "VALUES(?,?,?,?,?,?,?)",
        (now, ticket, price, unrealized_pnl, r_multiple, "test", mark_quality),
    )


def _insert_closed_trade(con, ticket=2001, pnl=50.0, closed_at=None):
    if closed_at is None:
        closed_at = datetime.now(timezone.utc).isoformat()
    con.execute(
        "INSERT INTO trades(ticket,magic,strategy,symbol,side,volume,"
        "entry,stop_loss,take_profit,risk_amount,opened_at,closed_at,"
        "exit_price,pnl,exit_reason,payload,setup_id,gross_pnl,commission,swap) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)",
        (ticket, 460001, "scalp", "GBPUSD", "SELL", 0.1, 1.2500, 1.2550,
         1.2400, 50.0, "2026-01-01T10:00:00+00:00", closed_at, 1.2450,
         pnl, "take_profit", "{}", pnl, 0.0, 0.0),
    )


# ---------------------------------------------------------------------------
# read_open_positions
# ---------------------------------------------------------------------------

class TestReadOpenPositions:
    def test_empty_db_returns_empty(self, journal_db):
        from ui.live_journal_reader import read_open_positions
        assert read_open_positions(journal_db) == []

    def test_open_trades_returned(self, journal_db):
        from ui.live_journal_reader import read_open_positions
        con = sqlite3.connect(journal_db)
        _insert_open_trade(con, ticket=1001, symbol="EURUSD", side="BUY",
                           entry=1.1000, sl=1.0950, tp=1.1100)
        _insert_observation(con, ticket=1001, price=1.1010,
                            unrealized_pnl=10.0, r_multiple=0.2)
        con.commit()
        con.close()

        positions = read_open_positions(journal_db)
        assert len(positions) == 1
        p = positions[0]
        assert p["ticket"] == 1001
        assert p["symbol"] == "EURUSD"
        assert p["side"] == "BUY"
        assert p["entry"] == pytest.approx(1.1000)
        assert p["sl"] == pytest.approx(1.0950)
        assert p["tp"] == pytest.approx(1.1100)
        assert p["pnl"] == pytest.approx(10.0)
        assert p["unrealizedR"] == pytest.approx(0.2)

    def test_multiple_open_trades(self, journal_db):
        from ui.live_journal_reader import read_open_positions
        con = sqlite3.connect(journal_db)
        _insert_open_trade(con, ticket=1001, symbol="EURUSD")
        _insert_open_trade(con, ticket=1002, symbol="GBPUSD", side="SELL")
        con.commit()
        con.close()

        positions = read_open_positions(journal_db)
        assert len(positions) == 2
        symbols = {p["symbol"] for p in positions}
        assert symbols == {"EURUSD", "GBPUSD"}

    def test_nonexistent_db_returns_empty(self):
        from ui.live_journal_reader import read_open_positions
        assert read_open_positions("/nonexistent/path/db.sqlite") == []

    def test_observation_quality_forwarded(self, journal_db):
        from ui.live_journal_reader import read_open_positions
        con = sqlite3.connect(journal_db)
        _insert_open_trade(con)
        _insert_observation(con, mark_quality="STALE")
        con.commit()
        con.close()

        positions = read_open_positions(journal_db)
        assert positions[0]["markQuality"] == "STALE"

    def test_no_observation_defaults_to_zero(self, journal_db):
        from ui.live_journal_reader import read_open_positions
        con = sqlite3.connect(journal_db)
        _insert_open_trade(con, ticket=1003)
        con.commit()
        con.close()

        positions = read_open_positions(journal_db)
        assert len(positions) == 1
        assert positions[0]["pnl"] == 0.0
        assert positions[0]["unrealizedR"] == 0.0

    def test_age_minutes_computed(self, journal_db):
        from ui.live_journal_reader import read_open_positions
        con = sqlite3.connect(journal_db)
        opened = datetime.now(timezone.utc).isoformat()
        _insert_open_trade(con, opened_at=opened)
        con.commit()
        con.close()

        positions = read_open_positions(journal_db)
        assert positions[0]["ageMin"] >= 0.0
        assert positions[0]["ageMin"] < 1.0  # just inserted


# ---------------------------------------------------------------------------
# read_trade_history
# ---------------------------------------------------------------------------

class TestReadTradeHistory:
    def test_empty_db_returns_empty(self, journal_db):
        from ui.live_journal_reader import read_trade_history
        assert read_trade_history(journal_db) == []

    def test_closed_trades_returned(self, journal_db):
        from ui.live_journal_reader import read_trade_history
        con = sqlite3.connect(journal_db)
        _insert_closed_trade(con, ticket=2001, pnl=50.0)
        con.commit()
        con.close()

        history = read_trade_history(journal_db)
        assert len(history) == 1
        assert history[0]["ticket"] == 2001
        assert history[0]["pnl"] == 50.0

    def test_limit_respected(self, journal_db):
        from ui.live_journal_reader import read_trade_history
        con = sqlite3.connect(journal_db)
        for i in range(10):
            _insert_closed_trade(con, ticket=3000 + i, pnl=float(i))
        con.commit()
        con.close()

        history = read_trade_history(journal_db, limit=5)
        assert len(history) == 5

    def test_ordered_by_closed_at_desc(self, journal_db):
        from ui.live_journal_reader import read_trade_history
        con = sqlite3.connect(journal_db)
        t1 = "2026-01-01T10:00:00+00:00"
        t2 = "2026-01-01T12:00:00+00:00"
        _insert_closed_trade(con, ticket=4001, pnl=10.0, closed_at=t1)
        _insert_closed_trade(con, ticket=4002, pnl=20.0, closed_at=t2)
        con.commit()
        con.close()

        history = read_trade_history(journal_db)
        assert history[0]["ticket"] == 4002  # most recent first
        assert history[1]["ticket"] == 4001


# ---------------------------------------------------------------------------
# read_pnl_summary
# ---------------------------------------------------------------------------

class TestReadPnlSummary:
    def test_empty_db_returns_zeros(self, journal_db):
        from ui.live_journal_reader import read_pnl_summary
        summary = read_pnl_summary(journal_db)
        assert summary["total_trades"] == 0
        assert summary["win_rate"] == 0.0

    def test_open_risk_summed(self, journal_db):
        from ui.live_journal_reader import read_pnl_summary
        con = sqlite3.connect(journal_db)
        _insert_open_trade(con, ticket=5001, risk=50.0)
        _insert_open_trade(con, ticket=5002, risk=30.0)
        con.commit()
        con.close()

        summary = read_pnl_summary(journal_db)
        assert summary["open_positions"] == 2
        assert summary["open_risk"] == pytest.approx(80.0, abs=0.1)

    def test_win_rate_computed(self, journal_db):
        from ui.live_journal_reader import read_pnl_summary
        con = sqlite3.connect(journal_db)
        _insert_closed_trade(con, ticket=6001, pnl=50.0)
        _insert_closed_trade(con, ticket=6002, pnl=-30.0)
        _insert_closed_trade(con, ticket=6003, pnl=20.0)
        con.commit()
        con.close()

        summary = read_pnl_summary(journal_db)
        assert summary["total_trades"] == 3
        assert summary["win_count"] == 2
        assert summary["loss_count"] == 1
        assert summary["win_rate"] == pytest.approx(66.7, abs=1.0)


# ---------------------------------------------------------------------------
# read_account_state
# ---------------------------------------------------------------------------

class TestReadAccountState:
    def test_returns_default_when_no_risk_file(self, journal_db):
        from ui.live_journal_reader import read_account_state
        result = read_account_state("", journal_db)
        assert "balance" in result
        assert "equity" in result
        assert "heatUsedR" in result

    def test_risk_file_values_used(self, journal_db, tmp_path):
        from ui.live_journal_reader import read_account_state
        risk_path = str(tmp_path / "risk.json")
        with open(risk_path, "w") as f:
            json.dump({"balance": 10000, "equity": 10500,
                        "heat_used_r": 2.5, "heat_cap_r": 6.0}, f)

        result = read_account_state(risk_path, journal_db)
        assert result["balance"] == 10000
        assert result["equity"] == 10500
        assert result["heatUsedR"] == 2.5

    def test_equity_fallback_from_balance_plus_pnl(self, journal_db, tmp_path):
        from ui.live_journal_reader import read_account_state
        risk_path = str(tmp_path / "risk.json")
        with open(risk_path, "w") as f:
            json.dump({"balance": 10000, "equity": 0}, f)

        con = sqlite3.connect(journal_db)
        _insert_open_trade(con, ticket=7001, risk=50.0)
        _insert_observation(con, ticket=7001, unrealized_pnl=100.0)
        con.commit()
        con.close()

        result = read_account_state(risk_path, journal_db)
        assert result["equity"] == pytest.approx(10100.0)


# ---------------------------------------------------------------------------
# read_cycle_briefs
# ---------------------------------------------------------------------------

class TestReadCycleBriefs:
    def test_empty_db_returns_empty(self, journal_db):
        from ui.live_journal_reader import read_cycle_briefs
        assert read_cycle_briefs(journal_db) == []

    def test_lifecycle_events_returned(self, journal_db):
        from ui.live_journal_reader import read_cycle_briefs
        now = datetime.now(timezone.utc).isoformat()
        con = sqlite3.connect(journal_db)
        payload = json.dumps({"symbol": "EURUSD", "side": "BUY",
                               "strategy": "scalp"})
        con.execute(
            "INSERT INTO trade_lifecycle_events(created_at,ticket,"
            "setup_key,event_type,actor,payload) VALUES(?,?,?,?,?,?)",
            (now, None, "key1", "PROPOSED", "market_model", payload),
        )
        con.commit()
        con.close()

        briefs = read_cycle_briefs(journal_db)
        assert len(briefs) == 1
        assert briefs[0]["eventType"] == "PROPOSED"
        assert briefs[0]["symbol"] == "EURUSD"


# ---------------------------------------------------------------------------
# read_proposals
# ---------------------------------------------------------------------------

class TestReadProposals:
    def test_empty_db_returns_empty(self, journal_db):
        from ui.live_journal_reader import read_proposals
        assert read_proposals(journal_db) == []

    def test_proposals_returned(self, journal_db):
        from ui.live_journal_reader import read_proposals
        now = datetime.now(timezone.utc).isoformat()
        con = sqlite3.connect(journal_db)
        con.execute(
            "INSERT INTO proposals(created_at,setup_key,strategy,symbol,"
            "side,payload,approved,reasons) VALUES(?,?,?,?,?,?,?,?)",
            (now, "key1", "scalp", "EURUSD", "BUY", "{}", 1, '["ok"]'),
        )
        con.commit()
        con.close()

        proposals = read_proposals(journal_db)
        assert len(proposals) == 1
        assert proposals[0]["approved"] is True
        assert proposals[0]["reasons"] == ["ok"]


# ---------------------------------------------------------------------------
# _age_minutes helper
# ---------------------------------------------------------------------------

class TestAgeMinutes:
    def test_recent_timestamp(self):
        from ui.live_journal_reader import _age_minutes
        now = datetime.now(timezone.utc).isoformat()
        age = _age_minutes(now)
        assert age >= 0.0
        assert age < 1.0

    def test_old_timestamp(self):
        from ui.live_journal_reader import _age_minutes
        old = "2026-01-01T00:00:00+00:00"
        age = _age_minutes(old)
        assert age > 1000  # many hours ago

    def test_invalid_timestamp_returns_zero(self):
        from ui.live_journal_reader import _age_minutes
        assert _age_minutes("not-a-date") == 0.0
