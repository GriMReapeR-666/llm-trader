"""Unit tests verifying resolution of Flags 1, 4, 5, 7 in TrialLedger and ContinuousBacktest."""
import sqlite3
import tempfile
import numpy as np
import pandas as pd
import pytest

from core.trial_ledger import TrialLedger
from research.continuous_backtest_pipeline import ContinuousBacktestPipeline


def test_trial_ledger_effective_trials_deduplication():
    """Repeated cron evaluations of the same strategy do not inflate effective trials."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    ledger = TrialLedger(db_path=db_path)
    
    # Simulate 50 cron executions of the exact same 2 strategies in mean_reversion class
    for _ in range(50):
        ledger.record_trial("statarb_eur_chf", sharpe=1.2, n_observations=100, strategy_class="mean_reversion")
        ledger.record_trial("meanrev_bollinger", sharpe=0.9, n_observations=100, strategy_class="mean_reversion")
    
    # Raw trial count is 100
    assert ledger.n_trials == 100
    assert ledger.n_trials_for_class("mean_reversion") == 100
    
    # Effective trials for class must be exactly 2
    n_eff = ledger.n_effective_trials_for_class("mean_reversion")
    assert n_eff == 2
    
    # DSR evaluation should deflate against effective_trials (2), not the repetitive cron ticks (100)
    # With n=500 and Sharpe=2.0, k=2 yields DSR confidence > 0.95 (PASS), whereas k=100 yields ~0.62 (FAIL)
    res = ledger.evaluate_dsr(estimated_sharpe=2.0, n_observations=500, strategy_class="mean_reversion", record=False)
    assert res.effective_trials == 2
    assert res.is_significant is True


def test_continuous_backtest_symbol_mapping_statarb():
    """statarb_eur_chf automatically maps to EURCHF even if generic EURUSD symbol requested."""
    pipeline = ContinuousBacktestPipeline(base_dir=tempfile.mkdtemp())
    sym = pipeline._resolve_target_symbol("statarb_eur_chf", "EURUSD")
    assert sym == "EURCHF"
    
    # Non-statarb keeps requested symbol
    sym_other = pipeline._resolve_target_symbol("kronos_h1", "EURUSD")
    assert sym_other == "EURUSD"


def test_walkforward_paper_probation_unlocked():
    """When broker fills < 20 but verified paper trades >= 20 exist, walk-forward unlocks with probation."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE trades (
            ticket INTEGER PRIMARY KEY,
            symbol TEXT,
            pnl REAL,
            risk_amount REAL,
            source TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE trade_labels (
            ticket INTEGER PRIMARY KEY,
            r_multiple REAL
        )
    """)
    
    # Insert 25 paper trades with profitable R-multiples
    for i in range(1, 26):
        r = 1.2 if i % 2 == 0 else -0.8
        conn.execute("INSERT INTO trades VALUES (?, 'EURUSD', ?, 10.0, 'paper')", (i, r * 10.0))
        conn.execute("INSERT INTO trade_labels VALUES (?, ?)", (i, r))
    conn.commit()
    conn.close()
    
    pipeline = ContinuousBacktestPipeline(base_dir=tempfile.mkdtemp())
    stage_res = pipeline._run_walkforward_with_db(db_path=db_path, symbol="EURUSD")
    
    assert stage_res["status"] in ("OK", "PROBATION_PAPER_OK")
    assert stage_res["samples"] >= 20
    assert stage_res.get("is_probation") is True

    # Test verdict computation does not block probation
    stages = {
        "backtest": {"status": "COMPLETED", "sharpe": 1.4, "profit_factor": 1.5, "expectancy_r": 0.3, "total_trades": 80, "data_source": "measured"},
        "dsr": {"status": "OK", "passed": True},
        "cpcv": {"status": "CPCV_VALIDATED", "probability_of_backtest_overfitting_pbo": 0.05, "is_statistically_robust": True},
        "walkforward": stage_res,
    }
    verdict = pipeline._compute_verdict(stages)
    assert verdict["passed"] is True
    assert "walkforward_skipped" not in verdict["blockers"]
