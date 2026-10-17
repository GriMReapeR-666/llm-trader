"""R-multiple evidence readers must read the real schema, and paper mode must
not reconcile the journal against itself.

Two independent breaks in the evidence chain feeding the research gates:

* **WFA/CPCV readers** selected ``r_multiple`` from the ``trades`` table.  That
  table has no such column (it lives in ``trade_labels``), so both queries
  raised on every run, a bare ``except Exception: pass`` swallowed it, and the
  walk-forward gate reported "fewer than 20 broker-reconciled r-multiples"
  forever regardless of the evidence.  The safety properties asserted here are
  the point: the >= 20 and >= 40 floors are **unchanged**, the reader admits
  only genuinely broker-reconciled R-multiples, and a reader that cannot read
  now fails loudly instead of masquerading as an empty dataset.

* **`--paper` reconciliation** built the "broker" side out of
  ``store.closed_trade_rows_between(...)`` -- the journal compared against
  itself.  It can never disagree, so it can never be evidence, and because
  journal rows carry ``pnl`` while the deal adapter reads ``profit`` every
  non-zero-P&L trade was reported as a spurious mismatch.  Paper mode has no
  independent broker side, so it now refuses instead of laundering.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.trading_safety import SCALP_MAGIC, TradeProposal, TradingStore
from intelligence.feature_schema import SCHEMA_HASHES, values_hash
from operations.daily_reconciliation import PAPER_BROKER_SOURCE, adapt_paper_deals, compare_day
from research.continuous_backtest_pipeline import (
    ContinuousBacktestPipeline,
    _broker_reconciled_r_multiples,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_ID = "reader_setup_v1"
FEATURE_VERSION = next(iter(SCHEMA_HASHES))


# ── fixtures ────────────────────────────────────────────────────────────────

def _seed_closed_trade(store: TradingStore, ticket: int, pnl: float = 10.0,
                       risk: float = 10.0, *, synthetic: bool = False) -> None:
    """One closed, deterministically labelled journal trade (a real fixture)."""
    values = {"close": 1.20478, "rsi_14": 41.5, "atr_14": 0.01363}
    payload = {"proposal_features": {
        "setup_id": SETUP_ID,
        "feature_schema_version": FEATURE_VERSION,
        "feature_schema_hash": SCHEMA_HASHES[FEATURE_VERSION],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
    }}
    if synthetic:
        # Exactly the marker the 150 quarantined production rows carry.
        payload["synthetic"] = True
        payload["source"] = "synthetic_fixture_legacy"
    proposal = TradeProposal("scalp", "EURUSD", "BUY", 1.1000, 1.0950, 1.1100, risk,
                             payload["proposal_features"])
    store.record_open_trade(ticket, proposal, 1.0, risk, payload)
    store.record_close(ticket, 1.1050, pnl, "target")
    with sqlite3.connect(store.path) as con:
        con.execute(
            """INSERT INTO trade_labels(ticket,label,rationale,r_multiple,created_at,label_rule,review_status)
               VALUES(?,?,?,?,?,?,'AUTO')""",
            (ticket, "WIN" if pnl >= 0 else "LOSS", "r_multiple", pnl / risk,
             datetime.now(timezone.utc).isoformat(), "deterministic_v1"))


def _paper_close(ticket: int, pnl: float = 10.0, magic: int = SCALP_MAGIC) -> dict:
    """A genuine PaperBroker close record (paper/history shape, not a journal row)."""
    return {"ticket": ticket, "symbol": "EURUSD", "side": "buy", "volume": 1.0,
            "price_open": 1.1000, "price_close": 1.1050, "profit": pnl,
            "magic": magic, "status": "CLOSED",
            "close_time": datetime.now(timezone.utc).timestamp()}


def _window():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now + timedelta(hours=1)


def _seed_reconciled(path: str, count: int, *, synthetic: bool = False) -> list:
    """Seed ``count`` closed+labelled trades and reconcile them like a real pass."""
    store = TradingStore(path)
    tickets = [9000 + i for i in range(count)]
    for ticket in tickets:
        _seed_closed_trade(store, ticket, pnl=10.0 if ticket % 2 else -5.0,
                           risk=10.0, synthetic=synthetic)
    start, end = _window()
    report = compare_day(store, adapt_paper_deals([_paper_close(t, 10.0 if t % 2 else -5.0)
                                                    for t in tickets]), start, end)
    del store
    return report


def _pipeline_over(path: str) -> ContinuousBacktestPipeline:
    """A pipeline whose journal is ``path`` instead of the repo's 1.4 GB store."""
    return ContinuousBacktestPipeline(base_dir=str(Path(path).parent))


def _seeded_journal(tmp_path, name: str, count: int, *, synthetic: bool = False) -> str:
    """A per-case journal directory holding the seeded ``trading_state.db``."""
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    path = str(directory / "trading_state.db")
    _seed_reconciled(path, count, synthetic=synthetic)
    return path


# ── the schema that actually holds the R-multiple ───────────────────────────

def test_trades_has_no_r_multiple_column_and_trade_labels_does(tmp_path):
    """Pins the fact the old reader got wrong, so it cannot regress silently."""
    path = str(tmp_path / "schema.db")
    TradingStore(path)
    with sqlite3.connect(path) as con:
        trades_cols = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
        labels_cols = {r[1] for r in con.execute("PRAGMA table_info(trade_labels)")}

    assert "r_multiple" not in trades_cols
    assert "pnl" in trades_cols and "risk_amount" in trades_cols
    assert "r_multiple" in labels_cols


def test_reader_does_not_raise_on_the_real_schema(tmp_path):
    """The regression: selecting ``r_multiple`` from the trades table raised
    every run and the bare except turned that into "zero evidence" instead of a
    failure."""
    path = str(tmp_path / "noschema.db")
    _seed_reconciled(path, 3)
    # No OperationalError, no bare-except no-op: it reads and returns.
    assert len(_broker_reconciled_r_multiples(path)) == 3


# ── the reader returns real, broker-reconciled R-multiples ──────────────────

def test_reader_returns_reconciled_r_multiples(tmp_path):
    path = str(tmp_path / "reconciled.db")
    report = _seed_reconciled(path, 4)
    assert report["reconciled_execution_evidence"]["written"] == 4

    multiples = _broker_reconciled_r_multiples(path)
    assert len(multiples) == 4
    # Read from trade_labels, not invented: winners +1R, losers -0.5R.
    assert sorted(multiples) == pytest.approx([-0.5, -0.5, 1.0, 1.0])


def test_reader_admits_nothing_without_independent_broker_evidence(tmp_path):
    """Closed, labelled trades that were never reconciled are not evidence."""
    path = str(tmp_path / "starved.db")
    store = TradingStore(path)
    for i in range(30):
        _seed_closed_trade(store, 7000 + i, pnl=10.0)
    del store

    assert _broker_reconciled_r_multiples(path) == []


def test_reader_keeps_synthetic_fixture_rows_quarantined(tmp_path):
    """The 150 quarantined rows must stay excluded even once broker evidence
    exists for their tickets -- reconciling a fixture still proves nothing."""
    path = str(tmp_path / "fixtures.db")
    _seed_reconciled(path, 30, synthetic=True)

    assert _broker_reconciled_r_multiples(path) == []


# ── thresholds are untouched ────────────────────────────────────────────────

def test_walkforward_threshold_is_still_twenty(tmp_path):
    """19 broker-reconciled R-multiples must not run the gate; 20 must."""
    assert _run_walkforward_stage(tmp_path, 19) == "INSUFFICIENT_JOURNAL_EVIDENCE"
    assert _run_walkforward_stage(tmp_path, 20) == "completed"


def test_cpcv_threshold_is_still_forty(tmp_path):
    assert _run_cpcv_stage(tmp_path, 39) == "INSUFFICIENT_SAMPLES_FOR_CPCV"
    assert _run_cpcv_stage(tmp_path, 40) != "INSUFFICIENT_SAMPLES_FOR_CPCV"


def _run_walkforward_stage(tmp_path, count: int) -> str:
    path = _seeded_journal(tmp_path, "wf%d" % count, count)
    result = _pipeline_over(path).run_full_validation(
        symbol="EURUSD", timeframe="H1", bars=200,
        run_dsr=False, compare_live=False)
    return result["stages"]["walkforward"]["status"]


def _run_cpcv_stage(tmp_path, count: int) -> str:
    path = _seeded_journal(tmp_path, "cpcv%d" % count, count)
    # Force the backtest-replay branch off so the journal path is exercised.
    return _pipeline_over(path)._stage_cpcv("EURUSD", "default", {"status": "failed"})["status"]


# ── no silent failure ───────────────────────────────────────────────────────

def test_walkforward_reports_a_failed_stage_when_the_reader_breaks(tmp_path, monkeypatch):
    """A broken reader must be loud.  The old bare ``except: pass`` reported
    INSUFFICIENT_JOURNAL_EVIDENCE forever, which read as a starved gate rather
    than a bug."""
    import research.continuous_backtest_pipeline as cbp

    def _boom(_db_path):
        raise sqlite3.OperationalError("no such column: r_multiple")

    monkeypatch.setattr(cbp, "_broker_reconciled_r_multiples", _boom)
    result = cbp.ContinuousBacktestPipeline(base_dir=str(tmp_path)).run_full_validation(
        symbol="EURUSD", timeframe="H1", bars=200, run_dsr=False, compare_live=False)

    wf = result["stages"]["walkforward"]
    assert wf["status"] == "failed"
    assert "no such column" in wf["error"]
    assert "samples" not in wf          # not laundered into "insufficient evidence"


def test_cpcv_reports_a_failed_stage_when_the_reader_breaks(tmp_path, monkeypatch):
    import research.continuous_backtest_pipeline as cbp

    def _boom(_db_path):
        raise sqlite3.OperationalError("no such column: r_multiple")

    monkeypatch.setattr(cbp, "_broker_reconciled_r_multiples", _boom)
    result = cbp.ContinuousBacktestPipeline(base_dir=str(tmp_path)).run_full_validation(
        symbol="EURUSD", timeframe="H1", bars=200, run_dsr=False, compare_live=False)

    assert result["stages"]["cpcv"]["status"] == "failed"
    assert "no such column" in result["stages"]["cpcv"]["error"]


# ── BUG 2: paper "reconciliation" was the journal compared to itself ─────────

def test_journal_rows_adapted_as_deals_report_every_trade_as_a_mismatch():
    """Why the --paper path was wrong: journal rows carry ``pnl``, the deal
    adapter reads ``profit``, so a faithful replay scores every non-zero trade
    as a spurious P&L difference."""
    journal_row = {"ticket": 5, "magic": SCALP_MAGIC, "pnl": 15.0,
                   "closed_at": datetime.now(timezone.utc).isoformat(),
                   "exit_reason": "target", "setup_id": SETUP_ID, "payload": "{}"}
    deal = adapt_paper_deals([journal_row])[0]
    assert deal["profit"] == 0.0
    assert deal["profit"] != journal_row["pnl"]


def test_paper_reconciliation_cli_refuses_instead_of_self_comparing(tmp_path):
    """--paper must not run a comparison that can only ever agree with itself,
    must not mint evidence, and must not write false operator work items."""
    path = str(tmp_path / "paper.db")
    store = TradingStore(path)
    for i in range(3):
        _seed_closed_trade(store, 6000 + i, pnl=15.0)
    del store

    proc = subprocess.run(
        [sys.executable, "mt5_trader.py", "journal", "reconciliation-run",
         "--paper", "--journal-path", path],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.strip())

    assert report["status"] == "NO_INDEPENDENT_BROKER_SOURCE"
    assert report["execution_authority"] is False
    assert report["execution_evidence_written"] == 0

    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM broker_execution_reports").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM daily_reconciliations").fetchone()[0] == 0
        spurious = con.execute(
            "SELECT COUNT(*) FROM execution_reviews "
            "WHERE intent_key LIKE 'recon_%'").fetchone()[0]
    assert spurious == 0, "self-comparison wrote %d false discrepancy work items" % spurious


def test_paper_reconciliation_cli_leaves_the_gate_shut(tmp_path):
    """Refusing must not accidentally look like a successful reconciliation that
    would let an operator believe the WFA/CPCV evidence exists."""
    path = str(tmp_path / "paper2.db")
    TradingStore(path)

    proc = subprocess.run(
        [sys.executable, "mt5_trader.py", "journal", "reconciliation-run",
         "--paper", "--journal-path", path],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
    assert _broker_reconciled_r_multiples(path) == []