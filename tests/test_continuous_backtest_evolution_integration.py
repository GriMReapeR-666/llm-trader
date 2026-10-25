"""Integration test verifying Autonomous Evolution Sprint wiring into ContinuousBacktestPipeline.
"""
import os
import shutil
import tempfile
import pytest
import pandas as pd
import numpy as np

from research.continuous_backtest_pipeline import ContinuousBacktestPipeline


@pytest.fixture
def temp_pipeline_dir():
    temp_dir = tempfile.mkdtemp(prefix="test_pipeline_evo_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_autonomous_evolution_triggered_on_validation_failure(temp_pipeline_dir):
    """When a strategy fails validation gates, ContinuousBacktestPipeline must
    automatically run an autonomous evolution sprint, performing autopsy and StrategyGit commits.
    """
    pipeline = ContinuousBacktestPipeline(base_dir=temp_pipeline_dir)

    # Create 1200 bars with realistic price action so that sufficient trades exist for autopsy
    np.random.seed(42)
    n = 1200
    dt_index = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    returns = np.random.normal(0, 0.002, n)
    price = 1.1000 * np.exp(np.cumsum(returns))
    highs = price * 1.002
    lows = price * 0.998
    opens = price

    bars_df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": price,
        "time": dt_index,
    })
    bars_df.set_index("time", inplace=True)

    # Run validation cycle on a candidate strategy that will fail
    res = pipeline.run_full_validation(
        symbol="EURUSD",
        strategy_name="trend_breakout",
        timeframe="H1",
        bars=n,
        bars_df=bars_df,
        data_source="synthetic",
        run_walkforward=False,
        run_dsr=False,
        compare_live=False,
    )

    assert "verdict" in res
    assert res["verdict"]["passed"] is False  # baseline must fail

    # Autonomous evolution stage must have executed
    assert "autonomous_evolution" in res["stages"]
    evo = res["stages"]["autonomous_evolution"]
    assert evo is not None
    assert evo["strategy_id"] == "trend_breakout"
    assert "autopsy_summary" in evo
    assert "evaluated_branches" in evo
    assert len(evo["evaluated_branches"]) >= 1

    branch_result = evo["evaluated_branches"][0]
    assert branch_result["status"] == "EVALUATED"
    assert branch_result["commit_hash"] is not None

    # Verify StrategyGit database was populated
    git_db_path = os.path.join(pipeline.artifacts_dir, "strategy_git.db")
    assert os.path.exists(git_db_path)
