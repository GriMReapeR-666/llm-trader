"""Write-time provenance constraints across the evidence chain.

Three links are proven here:

1. A backtest cannot produce a result without declaring (and the declaration
   must be a known source), so a fixture can never be constructed silently.
2. A journal row cannot be written without a provenance value: the Python
   writer validates it and a SQLite trigger refuses raw INSERTs that omit it.
3. Promotion refuses when either link is synthetic — the journal rows a gate
   would grade, or the backtest trial recorded against the strategy.
"""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from core import provenance
from core.trading_safety import TradeProposal, TradingStore


# ── 1. Backtest declarations ────────────────────────────────────────────────

def _ohlcv(n: int = 12) -> pd.DataFrame:
    close = np.linspace(1.10, 1.11, n)
    return pd.DataFrame({
        "open": close, "high": close + 0.0005,
        "low": close - 0.0005, "close": close,
    })


def test_backtest_refuses_missing_declaration():
    from research.alpha_backtest_harness import run_backtest

    df = _ohlcv()
    entries = pd.Series(False, index=range(len(df)))
    exits = pd.Series(False, index=range(len(df)))
    with pytest.raises(ValueError, match=r"data_source\) is required"):
        run_backtest(df, entries, exits)


def test_backtest_refuses_unknown_declaration():
    from research.alpha_backtest_harness import run_backtest

    df = _ohlcv()
    entries = pd.Series(False, index=range(len(df)))
    exits = pd.Series(False, index=range(len(df)))
    with pytest.raises(ValueError, match="not a known backtest source"):
        run_backtest(df, entries, exits, data_source="vibes")


def test_backtest_result_carries_its_declaration():
    from research.alpha_backtest_harness import run_backtest

    df = _ohlcv()
    n = len(df)
    entries = pd.Series(False, index=range(n))
    exits = pd.Series(False, index=range(n))
    entries.iloc[2] = True
    exits.iloc[8] = True
    result = run_backtest(df, entries, exits, data_source="synthetic(seed=3)")
    assert result.data_source == "synthetic"
    assert result.to_dict()["data_source"] == "synthetic"


def test_vbt_engine_refuses_undeclared_run():
    from research.vbt_engine import VectorizedBacktestEngine

    engine = VectorizedBacktestEngine()
    with pytest.raises(ValueError, match=r"data_source\) is required"):
        engine.run_crossover_strategy(_ohlcv())


def test_harness_result_metadata_records_seed():
    from research.alpha_backtest_harness import run_alpha_backtest_harness

    result = run_alpha_backtest_harness(
        _ohlcv(60), alpha_cols=["alpha_012"], data_source="synthetic(seed=11)")
    assert result.ic_weighted.data_source == "synthetic"
    assert result.hold_baseline.data_source == "synthetic"
    assert result.metadata["data_source"] == "synthetic(seed=11)"


# ── 2. Journal provenance is enforced at write time ─────────────────────────

def _proposal() -> TradeProposal:
    return TradeProposal("scalp", "EURUSD", "BUY", 1.10, 1.09, 1.12, 0.8, {})


def test_open_trade_requires_provenance_keyword(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    with pytest.raises(TypeError):
        store.record_open_trade(1, _proposal(), 0.1, 10.0, {})  # type: ignore[call-arg]


def test_open_trade_refuses_unknown_provenance(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    with pytest.raises(ValueError, match="not a known journal source"):
        store.record_open_trade(1, _proposal(), 0.1, 10.0, {}, provenance="trust_me")


def test_raw_insert_without_provenance_is_aborted(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    with pytest.raises(sqlite3.IntegrityError, match="provenance"):
        with store._connect() as con:
            con.execute(
                "INSERT INTO trades(ticket,magic,strategy,symbol,side,volume,entry,"
                "risk_amount,opened_at,payload) VALUES(9,1,'s','EURUSD','BUY',0.1,"
                "1.1,10.0,'2026-01-01T00:00:00Z','{}')")


def test_raw_insert_with_unknown_provenance_is_aborted(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    with pytest.raises(sqlite3.IntegrityError, match="provenance"):
        with store._connect() as con:
            con.execute(
                "INSERT INTO trades(ticket,magic,strategy,symbol,side,volume,entry,"
                "risk_amount,opened_at,payload,provenance) VALUES(9,1,'s','EURUSD',"
                "'BUY',0.1,1.1,10.0,'2026-01-01T00:00:00Z','{}','invented')")


def test_provenance_persists_on_the_row(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    store.record_open_trade(5, _proposal(), 0.1, 10.0, {}, provenance="paper_broker")
    with store._connect() as con:
        row = con.execute("SELECT provenance FROM trades WHERE ticket=5").fetchone()
    assert row["provenance"] == "paper_broker"


def test_legacy_rows_stay_null_and_are_not_read_as_synthetic(tmp_path):
    """A pre-constraint row is undeclared, not a fixture."""
    store = TradingStore(str(tmp_path / "journal.db"))
    store.record_open_trade(5, _proposal(), 0.1, 10.0, {}, provenance="mt5_broker")
    with store._connect() as con:
        con.execute("UPDATE trades SET provenance=NULL WHERE ticket=5")
        row = con.execute("SELECT provenance FROM trades WHERE ticket=5").fetchone()
    assert row["provenance"] is None
    assert provenance.is_synthetic(None) is False


# ── 3. Promotion refuses synthetic evidence ────────────────────────────────

def _promoter_with_journal(tmp_path, rows):
    from research.auto_promotion import AutoPromoter

    db = tmp_path / "trading_state.db"
    store = TradingStore(str(db))
    with store._connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS order_intents("
                    "intent_key TEXT PRIMARY KEY, mt5_ticket INTEGER)")
        for ticket, source in rows:
            con.execute(
                "INSERT INTO trades(ticket,magic,strategy,symbol,side,volume,entry,"
                "risk_amount,opened_at,closed_at,exit_price,pnl,payload,provenance)"
                " VALUES(?,1,'strat','EURUSD','BUY',0.1,1.1,10.0,"
                "'2026-01-01T00:00:00Z','2026-01-01T01:00:00Z',1.12,10.0,'{}',?)",
                (ticket, source))
            con.execute("INSERT INTO order_intents(intent_key,mt5_ticket) VALUES(?,?)",
                        (f"intent-{ticket}", ticket))
    promoter = AutoPromoter(base_dir=str(tmp_path))
    promoter.db_path = str(db)
    return promoter


def test_promotion_gate_passes_on_broker_reconciled_rows(tmp_path):
    promoter = _promoter_with_journal(tmp_path, [(1, "mt5_broker"), (2, "mt5_broker")])
    res = promoter._check_provenance("strat")
    assert res["passed"] is True
    assert res["journal_sources"] == ["mt5_broker"]


def test_promotion_gate_refuses_a_synthetic_journal_row(tmp_path):
    promoter = _promoter_with_journal(tmp_path, [(1, "mt5_broker"),
                                                 (2, "synthetic_demo_generator")])
    res = promoter._check_provenance("strat")
    assert res["passed"] is False
    assert res["reason"] == "synthetic_journal_evidence"
    assert res["synthetic_sources"] == ["synthetic_demo_generator"]


def test_promotion_gate_refuses_an_undeclared_journal_row(tmp_path):
    promoter = _promoter_with_journal(tmp_path, [(1, "mt5_broker")])
    # Legacy rows predate the column and stay NULL; a NULL row is not evidence.
    with sqlite3.connect(promoter.db_path) as con:
        con.execute("UPDATE trades SET provenance=NULL WHERE ticket=1")
    res = promoter._check_provenance("strat")
    assert res["passed"] is False
    assert res["reason"] == "undeclared_journal_provenance"


def test_promotion_gate_refuses_a_synthetic_backtest_trial(tmp_path):
    promoter = _promoter_with_journal(tmp_path, [(1, "mt5_broker")])
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    ledger = sqlite3.connect(artifacts / "trial_ledger.db")
    ledger.execute("CREATE TABLE trials(trial_id INTEGER PRIMARY KEY, strategy_id TEXT,"
                   " metadata TEXT)")
    ledger.execute("INSERT INTO trials(strategy_id, metadata) VALUES(?,?)",
                   ("strat", json.dumps({"data_source": "synthetic(seed=9)"})))
    ledger.commit()
    ledger.close()

    res = promoter._check_provenance("strat")
    assert res["passed"] is False
    assert res["reason"] == "synthetic_backtest_evidence"
    assert res["backtest_data_source"] == "synthetic(seed=9)"


# ── 4. Pipeline verdict refuses synthetic/undeclared backtests ─────────────

def _verdict(backtest_stage):
    from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

    return ContinuousBacktestPipeline(base_dir="")._compute_verdict({
        "stages": {
            "backtest": backtest_stage,
            "dsr": {"status": "OK"},
            "walkforward": {"status": "OK", "floor_20": {"met": True, "count": 20}},
        }
    })


def test_pipeline_verdict_refuses_synthetic_seeded_run():
    verdict = _verdict({"status": "COMPLETED", "sharpe": 1.4,
                        "data_source": "synthetic(seed=7)"})
    assert verdict["passed"] is False
    assert "synthetic_data_not_authoritative" in verdict["blockers"]


def test_pipeline_verdict_refuses_undeclared_run():
    verdict = _verdict({"status": "COMPLETED", "sharpe": 1.4})
    assert verdict["passed"] is False
    assert "data_source_undeclared" in verdict["blockers"]
