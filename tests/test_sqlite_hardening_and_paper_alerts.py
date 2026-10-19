import os
import sqlite3
import pytest
from state_manager import StateManager
from intelligence.graph_checkpointer import SqliteCheckpointSaver
from intelligence.trading_memory import TradingMemory
from core.order_execution import PaperBroker, _alert_paper_fallback, place_market_order


def test_state_manager_sqlite_pragmas(tmp_path):
    db_file = str(tmp_path / "test_state.db")
    sm = StateManager(db_file)
    con = sm.get_connection()
    try:
        cur = con.cursor()
        cur.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0].upper()
        cur.execute("PRAGMA synchronous;")
        synchronous = cur.fetchone()[0]
        cur.execute("PRAGMA busy_timeout;")
        busy_timeout = cur.fetchone()[0]
    finally:
        if hasattr(sm, "close"):
            sm.close()

    assert journal_mode == "WAL"
    # synchronous=NORMAL is 1
    assert synchronous == 1
    assert busy_timeout == 5000


def test_graph_checkpointer_sqlite_pragmas(tmp_path):
    db_file = str(tmp_path / "test_checkpointer.db")
    saver = SqliteCheckpointSaver(db_file)
    with saver._con() as con:
        cur = con.cursor()
        cur.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0].upper()
        cur.execute("PRAGMA synchronous;")
        synchronous = cur.fetchone()[0]
        cur.execute("PRAGMA busy_timeout;")
        busy_timeout = cur.fetchone()[0]

    assert journal_mode == "WAL"
    assert synchronous == 1
    assert busy_timeout == 5000


def test_trading_memory_sqlite_pragmas(tmp_path):
    db_file = str(tmp_path / "test_memory.db")
    tm = TradingMemory(db_file)
    con = tm._con()
    try:
        cur = con.cursor()
        cur.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0].upper()
        cur.execute("PRAGMA synchronous;")
        synchronous = cur.fetchone()[0]
        cur.execute("PRAGMA busy_timeout;")
        busy_timeout = cur.fetchone()[0]
    finally:
        con.close()

    assert journal_mode == "WAL"
    assert synchronous == 1
    assert busy_timeout == 5000


def test_root_trading_safety_deleted():
    assert not os.path.exists("trading_safety.py"), "Root trading_safety.py must be deleted"


def test_paper_broker_fallback_alert(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_alerts.db")
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", db_file)

    broker = PaperBroker()
    res = place_market_order("EURUSD", "BUY", 0.1, sl=1.0750, tp=1.0950, comment="test", paper=broker)
    assert res.get("retcode") == 10009

    # Verify that PAPER_BROKER_FALLBACK alert was written to the alert_outbox table in db_file
    with sqlite3.connect(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alert_outbox';")
        assert cur.fetchone() is not None, "alert_outbox table should exist"
        cur.execute("SELECT code, severity, payload FROM alert_outbox WHERE code='PAPER_BROKER_FALLBACK';")
        rows = cur.fetchall()
        assert len(rows) >= 1
        assert rows[0][0] == "PAPER_BROKER_FALLBACK"
        assert rows[0][1] == "WARNING"
