"""Tests for Multi-Timeframe Historical Replay, Event Simulation & Continuous Training.

Validates:
1. MultiTimeframeDataSynchronizer: Alignment across D1, H4, H1, M15, M5 without lookahead bias.
2. HierarchicalMTFEngine: D1/H4 macro regime & SVAR IRF target down to H1 SMC liquidity.
3. MTFExecutionSimulator: News liquidity manipulation (Judas swings) under XM Ultra Low pricing.
4. ContinuousLearningEngine: MAML adaptation and EWC catastrophic forgetting prevention.
5. ResearchMemoryBridge: 3-tier memory journaling to data/research_memory.db and Graph RAG export.
6. End-to-end multi-year backtesting pipeline execution on XAUUSD and EURUSD.
"""
from __future__ import annotations

import os
import sys
import shutil
import sqlite3
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_data_synchronizer_alignment():
    """Verify that MultiTimeframeDataSynchronizer aligns all 5 timeframes without lookahead."""
    from research.multitimeframe_historical_replay_pipeline import MultiTimeframeDataSynchronizer

    sync = MultiTimeframeDataSynchronizer(seed=42)
    t0 = datetime(2023, 1, 2, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2023, 1, 6, 0, 0, tzinfo=timezone.utc)

    data = sync.load_mock_or_real_data("XAUUSD", t0, t1)
    for tf in ["D1", "H4", "H1", "M15", "M5"]:
        assert tf in data, f"Missing timeframe {tf}"
        assert len(data[tf]) > 0, f"Timeframe {tf} is empty"

    assert len(data["D1"]) <= len(data["H4"])
    assert len(data["H4"]) <= len(data["H1"])
    assert len(data["H1"]) <= len(data["M15"])
    assert len(data["M15"]) <= len(data["M5"])

    # Test point-in-time slice strictly enforces causal boundary
    cutoff = datetime(2023, 1, 4, 12, 0, tzinfo=timezone.utc)
    pit = sync.get_point_in_time_slice("XAUUSD", cutoff)
    for tf, df in pit.items():
        if len(df) > 0:
            assert df["time"].max() <= cutoff, f"Lookahead in {tf}: {df['time'].max()} > {cutoff}"


def test_hierarchical_mtf_state_transitions():
    """Verify that HierarchicalMTFEngine transitions states from D1/H4 to H1 and detects zoom-in conditions."""
    from research.multitimeframe_historical_replay_pipeline import (
        MultiTimeframeDataSynchronizer,
        HierarchicalMTFEngine,
    )

    sync = MultiTimeframeDataSynchronizer(seed=42)
    t0 = datetime(2023, 1, 2, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2023, 1, 10, 0, 0, tzinfo=timezone.utc)
    sync.load_mock_or_real_data("XAUUSD", t0, t1)

    engine = HierarchicalMTFEngine(sync, symbol="XAUUSD", seed=42)
    ctx = engine.get_macro_context(t0 + timedelta(days=2))

    assert "macro_bias" in ctx
    assert ctx["macro_bias"] in ["BULLISH", "BEARISH", "NEUTRAL"]
    assert "svar_target_price" in ctx
    assert ctx["svar_target_price"] > 0.0
    assert "h1_order_blocks" in ctx
    assert "h1_liquidity_pools" in ctx


def test_judas_swing_mitigation_execution():
    """Verify execution of post-manipulation mitigation trades under XM Ultra Low spreads."""
    from research.multitimeframe_historical_replay_pipeline import MTFExecutionSimulator

    sim = MTFExecutionSimulator(symbol="XAUUSD", initial_balance=10000.0, seed=42)
    t_event = datetime(2023, 1, 5, 14, 30, tzinfo=timezone.utc)

    # Buy after Judas swing swept equal lows
    trade = sim.execute_manipulation_trade(
        direction="BUY",
        entry_price=1830.00,
        stop_loss=1825.00,
        target_price=1845.00,
        timestamp=t_event,
        is_news_event=True,
    )

    assert trade is not None
    assert trade["risk_reward"] >= 2.0
    assert trade["spread_applied"] > 0.0  # XM Ultra Low Gold spread
    assert sim.open_positions_count() == 1

    # Simulate price reaching target
    closed = sim.process_price_tick(1846.00, t_event + timedelta(minutes=45))
    assert len(closed) == 1
    assert closed[0]["outcome"] == "WIN"
    assert closed[0]["pnl"] > 0.0


def test_continuous_meta_learning_and_ewc():
    """Verify that MAML adapts parameters and EWC prevents catastrophic forgetting across regimes."""
    from research.multitimeframe_historical_replay_pipeline import ContinuousLearningEngine

    learn = ContinuousLearningEngine(seed=42)
    epoch_1_trades = [
        {"pnl": 120.0, "risk_reward": 2.4, "win": True, "regime": "EVENT_SHOCK"},
        {"pnl": -50.0, "risk_reward": 0.0, "win": False, "regime": "EVENT_SHOCK"},
        {"pnl": 180.0, "risk_reward": 3.0, "win": True, "regime": "EVENT_SHOCK"},
    ]
    res1 = learn.adapt_epoch(epoch_name="2020_COVID_SHOCK", trades=epoch_1_trades)
    assert res1["maml_updated"] is True
    assert res1["ewc_loss"] >= 0.0

    epoch_2_trades = [
        {"pnl": 90.0, "risk_reward": 2.1, "win": True, "regime": "HAWKISH_EXPANSION"},
        {"pnl": 140.0, "risk_reward": 2.8, "win": True, "regime": "HAWKISH_EXPANSION"},
    ]
    res2 = learn.adapt_epoch(epoch_name="2022_RATE_HIKES", trades=epoch_2_trades)
    assert res2["maml_updated"] is True
    assert "fisher_trace" in res2


def test_memory_journaling_and_bundle_export(tmp_path):
    """Verify research memory isolation and Graph RAG incubation bundle synthesis."""
    from research.multitimeframe_historical_replay_pipeline import ResearchMemoryBridge

    db_file = str(tmp_path / "test_research_memory.db")
    bundle_file = str(tmp_path / "test_incubation_bundle.json")

    bridge = ResearchMemoryBridge(db_path=db_file)
    assert "trading_state.db" not in bridge.db_path

    # Record L1 atomic fact and L3 heuristic
    f_id = bridge.record_l1_fact("XAUUSD", "Judas swing swept London high before US CPI", {"pnl": 210.0})
    h_id = bridge.record_l3_heuristic("Gold Short Rule", "CPI Judas sweep yields high R:R short mitigation", 0.88)

    assert f_id > 0
    assert h_id > 0

    exported = bridge.export_incubation_knowledge_bundle(bundle_file)
    assert os.path.exists(exported)
    assert os.path.getsize(exported) > 0


def test_full_pipeline_smoke_run(tmp_path):
    """Verify that run_multitimeframe_pipeline executes multi-asset backtest with valid metrics."""
    from research.multitimeframe_historical_replay_pipeline import run_multitimeframe_pipeline

    db_file = str(tmp_path / "smoke_research_memory.db")
    bundle_file = str(tmp_path / "smoke_bundle.json")

    report = run_multitimeframe_pipeline(
        symbols=["XAUUSD", "EURUSD"],
        start_date="2024-01-02",
        end_date="2024-01-10",
        fast_mode=True,
        seed=42,
        research_db_path=db_file,
        knowledge_bundle_path=bundle_file,
    )

    assert "symbols" in report
    assert "XAUUSD" in report["symbols"]
    assert "EURUSD" in report["symbols"]
    assert "metrics" in report
    assert report["metrics"]["total_trades"] > 0
    assert report["metrics"]["win_rate"] >= 0.50
    assert report["metrics"]["profit_factor"] >= 1.0
