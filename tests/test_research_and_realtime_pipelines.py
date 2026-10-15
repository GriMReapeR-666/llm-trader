"""Comprehensive functional tests for Realtime Analysis and Research Pipelines.

Tests:
1. HybridBacktestPipeline (research/hybrid_backtest_pipeline.py)
   - Tier-1 vectorized backtesting vs Tier-2 MT5 realistic replay with friction
   - Sharpe ratio decay computation & promotion gates
   - Parameter sweep across fast/slow EMAs
2. Autonomous Pipeline (operations/autonomous_pipeline.py)
   - Strategy discovery & snapshot board
   - Overfit-gate verification of registered setups
   - Scheduled autonomous cycle execution
3. Improvement Pipeline (operations/improvement_pipeline.py)
   - Parameter stability audit
   - Overfit gate state verification
   - Return diagnostics from closed journal trades
4. Continuous Backtesting Pipeline (research/continuous_backtest_pipeline.py)
   - Institutional verdict engine (_compute_verdict)
   - Combinatorial Purged Cross-Validation stage (_stage_cpcv)
   - Live vs backtest trade performance comparison (_compare_live_vs_backtest)
5. Realtime News Ingestion & Macrofundamental Pipeline Integration
   - Event normalization & source classification
   - Realtime pipeline cycle execution with fail-closed doctrine
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import numpy as np
import pandas as pd
import pytest

from operations.strategy_lifecycle import _connect as connect_lifecycle
from operations.autonomous_pipeline import board, verify, run_pipeline
from operations.improvement_pipeline import ImprovementPipeline
from research.hybrid_backtest_pipeline import HybridBacktestPipeline, BacktestMetrics, HybridVerdict
from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
from data.realtime_news_ingest import normalize_article
from intelligence.macrofundamental_pipeline import AutonomousMacroPipeline
from core.trading_safety import TradingStore


@pytest.fixture
def sample_bars_df() -> pd.DataFrame:
    """Generate realistic OHLCV bars for testing."""
    np.random.seed(42)
    n = 250
    time_index = pd.date_range("2024-01-01", periods=n, freq="15min")
    noise = np.random.randn(n) * 0.0004
    close = 1.0850 + np.cumsum(noise)
    high = close + np.abs(np.random.randn(n) * 0.0002) + 0.0001
    low = close - np.abs(np.random.randn(n) * 0.0002) - 0.0001
    open_p = close - noise / 2.0
    volume = np.random.randint(100, 500, size=n)

    df = pd.DataFrame({
        "time": time_index,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": volume,
    })
    return df


# =========================================================================
# 1. Hybrid Backtest Pipeline Tests
# =========================================================================

def test_hybrid_backtest_pipeline_evaluation(sample_bars_df):
    """Test HybridBacktestPipeline Tier-1 vs Tier-2 evaluation."""
    pipeline = HybridBacktestPipeline(sharpe_decay_threshold=25.0)

    close = sample_bars_df["close"]
    fast_ema = close.ewm(span=8, adjust=False).mean()
    slow_ema = close.ewm(span=21, adjust=False).mean()
    entries = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
    exits = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))

    verdict = pipeline.evaluate_strategy(
        sample_bars_df, entries.fillna(False), exits.fillna(False),
        symbol="EURUSD", timeframe="M15", sl_pips=25.0, tp_pips=50.0,
    )

    assert isinstance(verdict, HybridVerdict)
    assert isinstance(verdict.vbt_metrics, BacktestMetrics)
    assert hasattr(verdict.mt5_metrics, "total_return_pct")
    assert isinstance(verdict.sharpe_delta, float)
    assert isinstance(verdict.passes_promotion, bool)
    assert isinstance(verdict.rejection_reasons, list)

    verdict_dict = verdict.to_dict()
    assert "vbt_metrics" in verdict_dict
    assert "mt5_metrics" in verdict_dict
    assert "sharpe_delta" in verdict_dict


def test_hybrid_backtest_pipeline_sweep(sample_bars_df):
    """Test HybridBacktestPipeline two-tier parameter sweep."""
    pipeline = HybridBacktestPipeline(sharpe_decay_threshold=30.0)
    sweep_results = pipeline.run_two_tier_sweep(
        sample_bars_df,
        symbol="EURUSD",
        timeframe="M15",
        fast_range=(8, 12),
        slow_range=(20, 24),
        step=4,
    )

    assert isinstance(sweep_results, list)
    assert len(sweep_results) > 0
    for params, verdict in sweep_results:
        assert "fast_period" in params
        assert "slow_period" in params
        assert isinstance(verdict, HybridVerdict)


# =========================================================================
# 2. Autonomous Pipeline Tests
# =========================================================================

def test_autonomous_pipeline_operations(tmp_path):
    """Test strategy lifecycle board, verification, and tick run in operations."""
    db_path = str(tmp_path / "trading_state.db")
    con = connect_lifecycle(db_path)
    con.execute(
        "INSERT INTO strategy_lifecycle (setup_id, state, updated_at, updated_by) VALUES (?, ?, ?, ?)",
        ("macro_trend_01", "DEPLOYED", "2026-10-15T00:00:00Z", "test_runner")
    )
    con.execute(
        "INSERT INTO strategy_lifecycle (setup_id, state, updated_at, updated_by) VALUES (?, ?, ?, ?)",
        ("mean_rev_02", "PAPER_TRADING", "2026-10-15T00:00:00Z", "test_runner")
    )
    con.execute(
        "INSERT INTO strategy_lifecycle (setup_id, state, updated_at, updated_by) VALUES (?, ?, ?, ?)",
        ("decayed_03", "FLAGGED", "2026-10-15T00:00:00Z", "test_runner")
    )
    con.commit()
    con.close()

    # Board snapshot
    b = board(db_path)
    assert b["total"] == 3
    assert b["by_state"]["DEPLOYED"] == 1
    assert b["by_state"]["PAPER_TRADING"] == 1
    assert b["by_state"]["FLAGGED"] == 1

    # Verify gate check
    v = verify(db_path)
    assert v["checked"] == 3
    assert v["verified"] == 2  # DEPLOYED + PAPER_TRADING
    assert len(v["flagged"]) == 1
    assert v["flagged"][0]["setup_id"] == "decayed_03"

    # Execution tick
    res = run_pipeline(lifecycle_path=db_path)
    assert res["discovery"]["members"] == 3
    assert res["registered"] == 2
    assert res["flagged"] == 1
    assert res["skipped"] == 0


# =========================================================================
# 3. Improvement Pipeline Tests
# =========================================================================

def test_improvement_pipeline_cycle(tmp_path):
    """Test ImprovementPipeline self-improvement cycle and audit checks."""
    pipeline = ImprovementPipeline(base_dir=str(tmp_path))
    cycle_res = pipeline.run_cycle(cycle_num=1)

    assert cycle_res["cycle"] == 1
    assert "checks" in cycle_res
    assert "parameter_stability" in cycle_res["checks"]
    assert "overfit_gate" in cycle_res["checks"]
    assert "return_diagnostics" in cycle_res["checks"]

    # Parameter stability should report its availability
    assert cycle_res["checks"]["parameter_stability"]["available"] is True


# =========================================================================
# 4. Continuous Backtest Pipeline Tests
# =========================================================================

def test_continuous_backtest_pipeline_verdict_logic(tmp_path):
    """Test ContinuousBacktestPipeline._compute_verdict institutional gates."""
    pipeline = ContinuousBacktestPipeline(base_dir=str(tmp_path))

    # Passing stages
    passing_stages = {
        "backtest": {
            "status": "completed",
            "sharpe": 1.45,
            "win_rate": 0.58,
            "profit_factor": 1.65,
            "expectancy_r": 0.42,
        },
        "dsr": {"status": "completed", "passed": True},
        "walkforward": {"status": "completed", "stability_score": 0.72},
        "live_comparison": {"status": "completed", "significant_divergence": False},
    }
    v_pass = pipeline._compute_verdict(passing_stages)
    assert v_pass["passed"] is True
    assert v_pass["status"] == "PASS"
    assert len(v_pass["blockers"]) == 0

    # Failing stages (low Sharpe, low profit factor, failed DSR, live divergence)
    failing_stages = {
        "backtest": {
            "status": "completed",
            "sharpe": 0.85,
            "win_rate": 0.40,
            "profit_factor": 1.05,
            "expectancy_r": -0.10,
        },
        "dsr": {"status": "completed", "passed": False},
        "walkforward": {"status": "completed", "stability_score": 0.30},
        "live_comparison": {"status": "completed", "significant_divergence": True},
    }
    v_fail = pipeline._compute_verdict(failing_stages)
    assert v_fail["passed"] is False
    assert v_fail["status"] == "FAIL"
    assert "sharpe_below_1" in v_fail["blockers"]
    assert "profit_factor_below_1.2" in v_fail["blockers"]
    assert "negative_expectancy" in v_fail["blockers"]
    assert "dsr_gate_failed" in v_fail["blockers"]
    assert "live_backtest_divergence" in v_fail["blockers"]
    assert "low_win_rate" in v_fail["warnings"]
    assert "low_stability_score" in v_fail["warnings"]


def test_continuous_backtest_pipeline_cpcv_samples(tmp_path):
    """Test ContinuousBacktestPipeline._stage_cpcv sample handling."""
    pipeline = ContinuousBacktestPipeline(base_dir=str(tmp_path))

    # Insufficient samples (<40)
    stage_small = {"r_multiples": [0.5, -0.3, 1.2]}
    res_small = pipeline._stage_cpcv(symbol="EURUSD", strategy_name="test_strat", backtest_stage=stage_small)
    assert res_small["status"] == "INSUFFICIENT_SAMPLES_FOR_CPCV"
    assert "needs >= 40" in res_small["reason"]

    # Sufficient samples (50 R-multiples)
    np.random.seed(42)
    stage_large = {"r_multiples": (np.random.randn(50) * 0.5 + 0.1).tolist()}
    res_large = pipeline._stage_cpcv(symbol="EURUSD", strategy_name="test_strat", backtest_stage=stage_large)
    assert res_large["samples"] == 50
    assert "mean_cpcv_sharpe" in res_large


# =========================================================================
# 5. Realtime News & Macro Pipeline Integration Tests
# =========================================================================

def test_realtime_news_ingest_and_event_classification():
    """Verify realtime news normalization and source tier classification."""
    article = normalize_article(
        title="Federal Reserve raises interest rates by 25 basis points",
        url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261014.htm",
        publisher="Federal Reserve",
        provider="fed_rss",
    )
    assert article is not None
    assert article["event_class"] == "CB_MEETING"
    assert article["execution_authority"] is False
    assert "dedupe_key" in article


def test_realtime_macrofundamental_pipeline_e2e():
    """Verify autonomous macrofundamental pipeline cycle execution."""
    store = TradingStore()
    pipeline = AutonomousMacroPipeline(store=store)
    symbols = ["EURUSD", "XAUUSD"]

    res = pipeline.run_pipeline_cycle(symbols=symbols)
    assert res["status"] == "SUCCESS"
    assert res["execution_authority"] is False
    assert res["symbols_analyzed"] == symbols
    assert "regime_state" in res
    assert "architectural_health" in res
    assert len(res["verified_claims"]) >= len(symbols)
