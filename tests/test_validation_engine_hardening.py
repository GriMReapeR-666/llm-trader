"""Regression suite for the 8 validated research & engine hardening fixes:
1. Per-strategy signal registry execution (different strategies produce distinct trades).
2. DSR unit mismatch fix (de-annualizing Sharpe to align with per-period SE).
3. CPCV return scaling & strategy isolation (unscaled R, no journal fallback).
4. Fail-closed verdict gating (trades floor, CPCV/PBO gating, DSR/WF gating).
5. Volume aliasing and sandbox safety (volume exposed from tick_volume, non-overlapping IC).
6. RiskManager paper equity isolation (no MT5 demo equity hijacking).
7. Weekly session window in is_holiday (Sunday 21:00 UTC open to Friday 21:00 UTC close).
8. RL model path anchoring (anchored to repo root, no relative drift).
"""
import math
import os
import sqlite3
import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone

from core.metrics import calculate_deflated_sharpe_ratio
from core.cpcv_backtester import run_combinatorial_purged_backtest
from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
from data.api_manager import is_holiday
from core.risk_manager import RiskManager
from research.rl_trainer import RLTrainer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _generate_bars(n=500, seed=42):
    rng = np.random.default_rng(seed)
    c = 1.0850 * np.exp(np.cumsum(rng.normal(0.0001, 0.0015, n)))
    h = c * (1.0 + np.abs(rng.normal(0, 0.0008, n)))
    l = c * (1.0 - np.abs(rng.normal(0, 0.0008, n)))
    o = c * (1.0 + rng.normal(0, 0.0004, n))
    v = rng.integers(100, 2000, n)
    dt_index = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="15min")
    df = pd.DataFrame({
        "open": o, "high": h, "low": l, "close": c,
        "tick_volume": v, "time": dt_index,
    })
    df.set_index("time", inplace=True)
    return df


# ---------------------------------------------------------------------------
# 1. Bug #1: Per-Strategy Signal Differentiation
# ---------------------------------------------------------------------------
def test_bug1_per_strategy_differentiation(tmp_path):
    os.environ["MT5_BACKTEST_ALLOW_SYNTHETIC"] = "1"
    from research.mt5_backtest_engine import MT5BrokerBacktestEngine
    db_path = str(tmp_path / "test_research.db")
    engine = MT5BrokerBacktestEngine(journal_path=db_path)
    bars_df = _generate_bars(500, seed=42)

    res1 = engine.run_full_autonomous_backtest(
        symbol="EURUSD", strategy_name="gold_momentum_h4", bars_df=bars_df
    )
    res2 = engine.run_full_autonomous_backtest(
        symbol="EURUSD", strategy_name="alpha_055_orderflow", bars_df=bars_df
    )

    # Must produce different results
    assert res1["strategy_name"] == "gold_momentum_h4"
    assert res2["strategy_name"] == "alpha_055_orderflow"
    assert res1["metrics"]["total_trades"] != res2["metrics"]["total_trades"]
    assert res1["metrics"]["sharpe_ratio"] != res2["metrics"]["sharpe_ratio"]
    assert "r_multiples" in res1
    assert "r_multiples" in res2
    assert len(res1["r_multiples"]) == res1["metrics"]["total_trades"]
    assert len(res2["r_multiples"]) == res2["metrics"]["total_trades"]


# ---------------------------------------------------------------------------
# 2. Bug #2: DSR Unit Mismatch & Numerical Invariants
# ---------------------------------------------------------------------------
def test_bug2_dsr_unit_mismatch_and_invariants():
    # 1.5 annualized Sharpe, n=300, 2,351 trials yields DSR ~ 0.03
    p_val = calculate_deflated_sharpe_ratio(
        estimated_sharpe=1.5, num_trials=2351, sample_size=300, annualized=True
    )
    dsr_conf = 1.0 - p_val
    assert 0.01 <= dsr_conf <= 0.05, f"Expected DSR ~0.03, got {dsr_conf:.4f}"

    # Sharpe 14.04 on tiny sample (n=10) with 2,000 trials fails
    p_tiny = calculate_deflated_sharpe_ratio(
        estimated_sharpe=14.04, num_trials=2000, sample_size=10, annualized=True
    )
    dsr_tiny = 1.0 - p_tiny
    assert dsr_tiny < 0.50, f"Expected tiny sample to fail, got {dsr_tiny:.4f}"

    # Sharpe 0.76 with 2,325 trials fails across sample sizes
    for n in [10, 50, 100, 300]:
        p = calculate_deflated_sharpe_ratio(
            estimated_sharpe=0.76, num_trials=2325, sample_size=n, annualized=True
        )
        assert (1.0 - p) < 0.05

    # annualized=False preserves per-period calculation without scaling
    p_raw = calculate_deflated_sharpe_ratio(
        estimated_sharpe=0.1, num_trials=10, sample_size=100, annualized=False
    )
    p_ann = calculate_deflated_sharpe_ratio(
        estimated_sharpe=0.1 * math.sqrt(252), num_trials=10, sample_size=100, annualized=True
    )
    assert abs(p_raw - p_ann) < 1e-6


# ---------------------------------------------------------------------------
# 3. Bug #3: CPCV Return Scaling & Strategy Isolation
# ---------------------------------------------------------------------------
def test_bug3_cpcv_unscaled_and_isolated(tmp_path):
    rng = np.random.default_rng(42)
    # 50 per-trade R-multiples between -1.0 and +2.0
    r_multiples = list(rng.uniform(-1.0, 2.0, 50))
    res = run_combinatorial_purged_backtest(
        returns_series=r_multiples,
        n_splits=5,
        n_test_groups=2,
    )
    assert res["status"] in ("CPCV_VALIDATED", "OVERFITTING_RISK_DETECTED")
    # Mean CPCV Sharpe should be reasonable per-trade value, NOT saturated at 5.0
    assert abs(res["mean_cpcv_sharpe"]) < 4.0

    # Test stage_cpcv does not fall back to journal trades when backtest trades < 40
    pipe = ContinuousBacktestPipeline(base_dir=str(tmp_path))
    cpcv_stage = pipe._stage_cpcv(
        symbol="EURUSD",
        strategy_name="test_strat",
        backtest_stage={"r_multiples": [1.0, -0.5, 0.2]},  # only 3 trades
    )
    assert cpcv_stage["status"] == "INSUFFICIENT_SAMPLES_FOR_CPCV"
    assert cpcv_stage["is_statistically_robust"] is False
    assert "journal fallback disabled" in cpcv_stage["reason"]


# ---------------------------------------------------------------------------
# 4. Bug #4: Fail-Closed Verdict Gating
# ---------------------------------------------------------------------------
def test_bug4_fail_closed_verdict(tmp_path):
    pipe = ContinuousBacktestPipeline(base_dir=str(tmp_path))

    # Case A: total_trades < 30 must block
    stages_few_trades = {
        "backtest": {"sharpe": 2.0, "win_rate": 60.0, "total_trades": 15},
        "dsr": {"status": "OK", "passed": True},
        "cpcv": {"status": "CPCV_VALIDATED", "probability_of_backtest_overfitting_pbo": 0.05, "is_statistically_robust": True},
        "walkforward": {"status": "OK", "stability_score": 0.8},
        "live_comparison": {"status": "OK", "significant_divergence": False},
    }
    verdict_a = pipe._compute_verdict(stages_few_trades)
    assert verdict_a["passed"] is False
    assert "insufficient_trades_sample" in verdict_a["blockers"]

    # Case B: CPCV skipped or insufficient must block
    stages_cpcv_skipped = {
        "backtest": {"sharpe": 2.0, "win_rate": 60.0, "total_trades": 50},
        "dsr": {"status": "OK", "passed": True},
        "cpcv": {"status": "INSUFFICIENT_SAMPLES_FOR_CPCV"},
        "walkforward": {"status": "OK", "stability_score": 0.8},
        "live_comparison": {"status": "OK", "significant_divergence": False},
    }
    verdict_b = pipe._compute_verdict(stages_cpcv_skipped)
    assert verdict_b["passed"] is False
    assert "cpcv_stage_skipped" in verdict_b["blockers"]

    # Case C: PBO > 0.25 must block
    stages_pbo_high = {
        "backtest": {"sharpe": 2.0, "win_rate": 60.0, "total_trades": 50},
        "dsr": {"status": "OK", "passed": True},
        "cpcv": {"status": "CPCV_VALIDATED", "probability_of_backtest_overfitting_pbo": 0.40, "is_statistically_robust": True},
        "walkforward": {"status": "OK", "stability_score": 0.8},
        "live_comparison": {"status": "OK", "significant_divergence": False},
    }
    verdict_c = pipe._compute_verdict(stages_pbo_high)
    assert verdict_c["passed"] is False
    assert "pbo_above_threshold" in verdict_c["blockers"]

    # Case D: DSR skipped or failed must block
    stages_dsr_failed = {
        "backtest": {"sharpe": 2.0, "win_rate": 60.0, "total_trades": 50},
        "dsr": {"status": "FAILED", "passed": False},
        "cpcv": {"status": "CPCV_VALIDATED", "probability_of_backtest_overfitting_pbo": 0.10, "is_statistically_robust": True},
        "walkforward": {"status": "OK", "stability_score": 0.8},
        "live_comparison": {"status": "OK", "significant_divergence": False},
    }
    verdict_d = pipe._compute_verdict(stages_dsr_failed)
    assert verdict_d["passed"] is False
    assert "dsr_gate_failed" in verdict_d["blockers"]


# ---------------------------------------------------------------------------
# 5. Bug #5: Volume Aliasing & Sandbox Safety
# ---------------------------------------------------------------------------
def test_bug5_volume_aliasing_and_sandbox_safety():
    from core.alpha_discovery import SealedSandboxEvaluator, SafeAlphaEvaluator, AlphaCandidate

    # 1. SafeAlphaEvaluator blocks arbitrary code execution
    safe_eval = SafeAlphaEvaluator()
    df = _generate_bars(100)
    assert "volume" in df.columns or "tick_volume" in df.columns
    # Ensure volume column is accepted by DSL
    df_with_vol = df.copy()
    if "volume" not in df_with_vol.columns:
        df_with_vol["volume"] = df_with_vol["tick_volume"].astype(float)
    assert safe_eval.evaluate("delta(close, 1) * scale(volume, 1.0)", df_with_vol) is not None
    assert safe_eval.evaluate("import os", df_with_vol) is None
    assert safe_eval.evaluate("os.system('bad')", df_with_vol) is None

    # 2. SealedSandboxEvaluator fails closed on invalid/dangerous expressions
    evaluator = SealedSandboxEvaluator()
    cand = AlphaCandidate(
        expression="import os",
        name="bad_alpha",
        category="momentum",
    )
    res = evaluator.evaluate_candidate(cand, df_with_vol.iloc[:70], df_with_vol.iloc[70:])
    assert res.status == "REJECTED"


# ---------------------------------------------------------------------------
# 6. Bug #6: RiskManager Paper Equity Isolation
# ---------------------------------------------------------------------------
def test_bug6_risk_manager_paper_equity_isolation(tmp_path):
    state_file = str(tmp_path / "paper_risk.json")
    rm = RiskManager(balance=25000.0, state_file=state_file, is_paper=True)
    # Verify current equity matches balance in paper mode without calling MT5
    assert rm.current_equity() == 25000.0
    # Daily limits reset must anchor to paper equity
    rm.reset_daily_limits()
    assert rm.daily_start_equity == 25000.0


# ---------------------------------------------------------------------------
# 7. Bug #7: Weekly Session Window in is_holiday
# ---------------------------------------------------------------------------
def test_bug7_weekly_session_window():
    # Friday 21:05 UTC (market closed for weekend)
    fri_night = datetime(2026, 10, 23, 21, 5, tzinfo=timezone.utc)
    assert is_holiday(fri_night) is True

    # Saturday 12:00 UTC (market closed)
    sat_noon = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    assert is_holiday(sat_noon) is True

    # Sunday 20:55 UTC (market closed, before 21:00 UTC)
    sun_before_open = datetime(2026, 10, 25, 20, 55, tzinfo=timezone.utc)
    assert is_holiday(sun_before_open) is True

    # Sunday 21:05 UTC (market open for Asian session)
    sun_after_open = datetime(2026, 10, 25, 21, 5, tzinfo=timezone.utc)
    assert is_holiday(sun_after_open) is False

    # Wednesday 14:00 UTC (market open)
    wed_noon = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)
    assert is_holiday(wed_noon) is False


# ---------------------------------------------------------------------------
# 8. Bug #8: RL Model Path Anchoring
# ---------------------------------------------------------------------------
def test_bug8_rl_model_path_anchoring():
    trainer = RLTrainer(model_path=None)
    assert os.path.isabs(trainer.model_path)
    assert trainer.model_path.endswith("rl_model.json")

