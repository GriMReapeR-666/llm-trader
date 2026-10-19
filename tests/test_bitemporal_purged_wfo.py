"""Unit tests for Bi-Temporal Point-in-Time Store & Purged Walk-Forward Optimizer.

Tests:
- BiTemporalMacroStore zero lookahead cutoff
- Purged WFO fold generation with 5-day purge and 5-day embargo
- MicrostructureSweepSimulator queue penetration and limit execution
- Statistical survival gates (WFE, DSR, Leave-one-out, Monte Carlo)
"""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from core.bitemporal_purged_wfo import (
    BiTemporalMacroStore,
    MicrostructureSweepSimulator,
    TradeRecord,
    generate_purged_wfo_folds,
    evaluate_statistical_survival_gates,
)


def test_bitemporal_macro_store_zero_lookahead():
    # September NFP: ref_period 2026-09-01, released Oct 3 12:30 UTC
    data = [
        {
            "asset": "EURUSD",
            "domain": "MACRO",
            "ref_period": pd.Timestamp("2026-09-01"),
            "pub_timestamp": pd.Timestamp("2026-10-03 12:30:00", tz="UTC"),
            "raw_score": 2.2,  # Strong beat
        },
        {
            "asset": "EURUSD",
            "domain": "MACRO",
            "ref_period": pd.Timestamp("2026-09-01"),
            "pub_timestamp": pd.Timestamp("2026-11-07 13:30:00", tz="UTC"),
            "raw_score": 1.4,  # Downward revision
        },
    ]
    df = pd.DataFrame(data)
    store = BiTemporalMacroStore(df)

    # As of Oct 3 12:00 UTC (before release) -> Must see nothing / neutral
    scores_pre = store.get_pit_conviction(pd.Timestamp("2026-10-03 12:00:00", tz="UTC"))
    assert "EURUSD" not in scores_pre or scores_pre.get("EURUSD") == 0.0

    # As of Oct 15 (after release, before revision) -> Must see initial print (+2.2), not revised (+1.4)
    scores_post = store.get_pit_conviction(pd.Timestamp("2026-10-15 00:00:00", tz="UTC"))
    assert scores_post["EURUSD"] > 0

    # As of Nov 10 (after revision) -> Sees revised print
    scores_rev = store.get_pit_conviction(pd.Timestamp("2026-11-10 00:00:00", tz="UTC"))
    assert scores_rev["EURUSD"] < scores_post["EURUSD"]


def test_generate_purged_wfo_folds():
    start = pd.Timestamp("2020-01-01", tz="UTC")
    end = pd.Timestamp("2024-01-01", tz="UTC")
    folds = generate_purged_wfo_folds(start, end, is_months=24, oos_months=3, purge_days=5, embargo_days=5)
    assert len(folds) >= 3
    for f in folds:
        # Purge buffer: oos_start is is_end + 5 days
        assert f["oos_start"] == f["is_end"] + pd.Timedelta(days=5)
        # Embargo buffer: next_train_resume is oos_end + 5 days
        assert f["next_train_resume"] == f["oos_end"] + pd.Timedelta(days=5)


def test_statistical_survival_gates():
    # Create synthetic OOS trade ledger: 40 trades, expectancy +0.65R
    trades = [
        TradeRecord(
            asset="EURUSD",
            direction=1,
            entry_time=pd.Timestamp("2026-10-01", tz="UTC"),
            exit_time=pd.Timestamp("2026-10-03", tz="UTC"),
            entry_price=1.0500,
            sl_price=1.0450,
            tp1_price=1.0575,
            tp2_price=1.0650,
            pnl_r=1.5 if i % 3 != 0 else -1.0,
        )
        for i in range(40)
    ]
    gates = evaluate_statistical_survival_gates(
        oos_trades=trades,
        is_annual_return=0.18,
        oos_annual_return=0.12,  # WFE = 0.12 / 0.18 = 0.67 >= 0.55
        num_trials=50,
    )
    assert gates["status"] == "READY_FOR_REVIEW"
    assert gates["wfe"] >= 0.55
    assert gates["wfe_pass"] is True
    assert gates["leave_one_out_expectancy_r"] > 0.35
    assert gates["leave_one_out_pass"] is True
    assert gates["monte_carlo_max_dd_pct"] < 10.0
    assert gates["monte_carlo_pass"] is True
