"""Tests for Deterministic Historical Event, News & Meta-RL Training Suite."""
from __future__ import annotations

import os
import json
import sqlite3
import pytest
from pathlib import Path

from research.deterministic_historical_scenario_trainer import (
    DeterministicHistoricalScenarioTrainer,
    HISTORICAL_DAY_SCENARIOS,
    generate_deterministic_day_candles,
    set_deterministic_seed,
)


def test_scenario_definitions_complete():
    """Verify all 5 major macroeconomic conditions are represented with complete metadata."""
    assert len(HISTORICAL_DAY_SCENARIOS) == 5
    required_keys = [
        "day", "date", "regime_name", "description", "terminal_symbol",
        "expected_direction", "news_dispatches", "economic_event",
        "maml_profile", "base_prices", "drifts", "vols"
    ]
    for scen in HISTORICAL_DAY_SCENARIOS:
        for k in required_keys:
            assert k in scen, f"Missing {k} in scenario Day {scen.get('day')}"
        assert len(scen["news_dispatches"]) >= 2
        assert "name" in scen["economic_event"]
        assert "z_score" in scen["economic_event"]


def test_candle_generation_is_strictly_deterministic():
    """Verify identical seeds produce bit-identical candles across all symbols."""
    scen = HISTORICAL_DAY_SCENARIOS[0]
    candles_a = generate_deterministic_day_candles(scen, n_bars=30, seed=42)
    candles_b = generate_deterministic_day_candles(scen, n_bars=30, seed=42)
    candles_diff_seed = generate_deterministic_day_candles(scen, n_bars=30, seed=999)

    for sym in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]:
        assert len(candles_a[sym]) == 30
        for bar_a, bar_b in zip(candles_a[sym], candles_b[sym]):
            assert bar_a["open"] == bar_b["open"]
            assert bar_a["high"] == bar_b["high"]
            assert bar_a["low"] == bar_b["low"]
            assert bar_a["close"] == bar_b["close"]
            assert bar_a["volume"] == bar_b["volume"]
        
        # Verify different seed produces different prices
        assert candles_a[sym][10]["close"] != candles_diff_seed[sym][10]["close"]


def test_full_deterministic_replay_execution(tmp_path: Path):
    """Verify full end-to-end replay runs deterministically and populates 3-tier memory."""
    db_file = str(tmp_path / "test_research_memory.db")
    trainer = DeterministicHistoricalScenarioTrainer(seed=42, db_path=db_file)
    result = trainer.run_full_historical_training()

    assert result["status"] == "COMPLETED"
    assert result["deterministic_seed"] == 42
    assert len(result["scorecards"]) == 5
    assert result["cumulative_pnl"] > 0.0

    # Verify 3-Tier Memory in isolated database
    con = sqlite3.connect(db_file)
    cur = con.cursor()

    # L1 Atomic Facts
    facts = cur.execute("SELECT count(*) FROM atomic_facts").fetchone()[0]
    assert facts >= 10  # At least 2 facts per day

    # L2 Scenario Documents
    scenarios = cur.execute("SELECT count(*) FROM scenario_documents WHERE status = 'APPROVED'").fetchone()[0]
    assert scenarios == 5  # Exactly 1 scenario document per day

    # L3 Verified Core Heuristics
    heuristics = cur.execute("SELECT count(*) FROM verified_memories WHERE status = 'APPROVED'").fetchone()[0]
    assert heuristics >= 5  # At least 1 verified heuristic per day

    con.close()


def test_seed_reproducibility_invariance(tmp_path: Path):
    """Verify two independent runs with seed=42 yield identical PnL, actions, and scores."""
    db1 = str(tmp_path / "mem1.db")
    db2 = str(tmp_path / "mem2.db")

    t1 = DeterministicHistoricalScenarioTrainer(seed=42, db_path=db1)
    res1 = t1.run_full_historical_training()

    t2 = DeterministicHistoricalScenarioTrainer(seed=42, db_path=db2)
    res2 = t2.run_full_historical_training()

    assert res1["cumulative_pnl"] == res2["cumulative_pnl"]
    for s1, s2 in zip(res1["scorecards"], res2["scorecards"]):
        assert s1["day"] == s2["day"]
        assert s1["regime"] == s2["regime"]
        assert s1["symbol"] == s2["symbol"]
        assert s1["action"] == s2["action"]
        assert s1["pnl"] == s2["pnl"]
        assert s1["z_score"] == s2["z_score"]


def test_production_database_isolation_invariant():
    """Verify zero modifications are made to live trading_state.db."""
    live_db = "trading_state.db"
    initial_mtime = os.path.getmtime(live_db) if os.path.exists(live_db) else None
    
    # Run historical scenario trainer
    trainer = DeterministicHistoricalScenarioTrainer(seed=777)
    _ = trainer.run_full_historical_training()

    if initial_mtime is not None:
        after_mtime = os.path.getmtime(live_db)
        assert initial_mtime == after_mtime, "CRITICAL INVARIANT VIOLATION: live trading_state.db was modified during backtest!"
