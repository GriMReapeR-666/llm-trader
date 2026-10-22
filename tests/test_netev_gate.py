"""Regression tests for the net-EV expectancy gate in
core/trading_safety.DeterministicValidator.

`trade_labels` was missing from TradingStore's DDL, so

    SELECT l.label, l.r_multiple FROM trade_labels l JOIN trades t ...

raised "no such table: trade_labels" on every call, and the call-site `except`
turned that into `net_ev_ok = True`. The gate was permanently inert while
looking identical to a satisfied one.
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("", "data", "core", "intelligence", "operations", "research", "ui"):
    _p = os.path.join(_ROOT, _sub) if _sub else _ROOT
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from core.trading_safety import DeterministicValidator, TradingStore

_CLOSED = "2026-09-01T00:00:00+00:00"


@pytest.fixture
def store(tmp_path):
    return TradingStore(str(tmp_path / "netev.db"))


def _seed(store, n, r_multiple, label="LOSS", broker_evidenced=True):
    """Insert n closed trades each carrying a trade_labels row.

    When broker_evidenced, also record the order-intent submission evidence
    the gate requires: a label row is written by the same journal that opened
    the trade and grades nothing on its own.
    """
    with store._connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS order_intents (
            intent_key TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT,
            run_id TEXT, proposal_id TEXT, status TEXT, mt5_ticket INTEGER,
            payload TEXT, result TEXT, error TEXT)""")
        for i in range(n):
            ticket = 1000 + i
            con.execute(
                "INSERT OR REPLACE INTO trades (ticket, magic, strategy, symbol, side,"
                " volume, entry, stop_loss, take_profit, risk_amount, opened_at,"
                " closed_at, exit_price, pnl, exit_reason, payload, provenance)"
                " VALUES (?,1,'s','EURUSD','buy',0.01,1.0,0.9,1.2,100.0,?,?,1.0,-100.0,'sl','','mt5_broker')",
                (ticket, _CLOSED, _CLOSED))
            con.execute(
                "INSERT OR REPLACE INTO trade_labels (ticket, label, rationale,"
                " r_multiple, created_at) VALUES (?,?,?,?,?)",
                (ticket, label, "test", float(r_multiple), _CLOSED))
            if broker_evidenced:
                con.execute(
                    "INSERT OR REPLACE INTO order_intents"
                    "(intent_key,created_at,status,mt5_ticket) VALUES(?,?,?,?)",
                    (f"intent:{ticket}", _CLOSED, "FILLED", ticket))


def test_trade_labels_table_is_created(store):
    with store._connect() as con:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "trade_labels" in names, (
        "the net-EV gate joins trade_labels; without it the query always raises")


def test_gate_query_does_not_raise_on_an_empty_journal(store):
    v = DeterministicValidator(store=store, min_rr=2.0, min_ev_samples=30)
    stats = v._rolling_net_ev()          # used to raise "no such table"
    assert stats["sample_count"] == 0
    assert stats["ev_gross"] == 0.0


def test_gate_reads_real_labelled_history(store):
    """A 100%-loss journal must surface as negative expectancy."""
    _seed(store, 40, -1.0)
    v = DeterministicValidator(store=store, min_rr=2.0, min_ev_samples=30)
    stats = v._rolling_net_ev()
    assert stats["sample_count"] == 40, stats
    assert stats["ev_gross"] < 0, stats


def test_gate_would_reject_a_persistently_losing_strategy(store):
    """With enough labelled evidence the gate must report not-ok."""
    _seed(store, 40, -1.0)
    v = DeterministicValidator(store=store, min_rr=2.0, min_ev_samples=30,
                               default_friction_r=0.05)
    net_ev = v._rolling_net_ev()["ev_gross"] - 0.05
    assert net_ev < 0.0


def test_gate_grades_nothing_without_broker_evidence(store):
    """Labels without independent broker evidence must grade as zero rows.

    The 150-row synthetic fixture carried labels (the journal labels its own
    closes) and fed the gate +0.5R of fabricated expectancy; validation then
    read it as a satisfied gate instead of an unevidenced one.
    """
    _seed(store, 40, -1.0, broker_evidenced=False)
    v = DeterministicValidator(store=store, min_rr=2.0, min_ev_samples=30)
    stats = v._rolling_net_ev()
    assert stats["sample_count"] == 0, stats
    assert stats["ev_gross"] == 0.0, stats
