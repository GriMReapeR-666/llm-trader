"""Tests: TCE per-fill cost model in backtests + CPCV stage execution (audit gap #4).

Covers:
  - full_pipeline_backtest: TCE per-fill slippage applied to fills, honest
    cost accounting fields, legacy flat fallback via use_tce=False.
  - core.cpcv_backtester: real PBO/DSR output shape on net-of-cost returns.
  - ContinuousBacktestPipeline CPCV stage: concrete result or concrete
    insufficiency — never a silent bypass.

No gate thresholds are touched by the implementation; these tests pin the
unchanged gate behavior.
"""
from __future__ import annotations

import math
import numpy as np
import pytest


def _make_trending_bars(n=500, trend=0.001, vol=0.0003, seed=42):
    """Same generator as tests/test_full_pipeline_backtest.py."""
    np.random.seed(seed)
    price = 1.1000
    bars = []
    for i in range(n):
        change = np.random.normal(trend, vol)
        o = price
        c = price * (1 + change)
        h = max(o, c) * (1 + abs(np.random.normal(0, vol * 0.3)))
        l = min(o, c) * (1 - abs(np.random.normal(0, vol * 0.3)))
        bars.append({
            "open": round(o, 5), "high": round(h, 5),
            "low": round(l, 5), "close": round(c, 5),
            "volume": int(np.random.exponential(1000)),
        })
        price = c
    return bars


class TestTCEFillModel:
    def test_tce_applied_per_fill(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500)
        r = run_full_pipeline_backtest(bars)
        if r.total_trades == 0:
            pytest.skip("fixture produced no trades")
        assert r.cost_model == "tce_per_fill"
        assert r.avg_slippage_bps > 0
        assert r.max_fill_slippage_bps >= r.avg_slippage_bps
        assert r.total_cost > 0
        # Real slipped fills: entry_slippage recorded per trade and nonzero
        slipped = [t for t in r.trades_log if abs(t.get("entry_slippage", 0.0)) > 0]
        assert slipped, "no fills carry TCE slippage"

    def test_legacy_flat_fallback(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500)
        r = run_full_pipeline_backtest(bars, use_tce=False)
        assert r.cost_model == "flat_legacy"
        assert r.avg_slippage_bps == 0
        if r.total_trades > 0:
            # Flat model still charges spread/slippage/commission
            assert r.total_spread_cost > 0
            assert r.total_slippage_cost > 0
            assert r.total_commission > 0

    def test_to_dict_carries_cost_fields(self):
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500)
        d = run_full_pipeline_backtest(bars).to_dict()
        for key in ("cost_model", "avg_slippage_bps", "max_fill_slippage_bps",
                    "total_impact_cost", "total_cost"):
            assert key in d

    def test_tce_costs_bite_vs_flat(self):
        """Realistic fills must cost at least as much as the flat model on
        the same bars — the whole point of net-of-cost decisions."""
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500)
        r_tce = run_full_pipeline_backtest(bars, use_tce=True)
        r_flat = run_full_pipeline_backtest(bars, use_tce=False)
        assert r_tce.total_trades == r_flat.total_trades  # same fills, same gates
        if r_tce.cost_model == "tce_per_fill" and r_tce.total_trades > 0:
            tce_total = r_tce.total_cost
            flat_total = (r_flat.total_spread_cost + r_flat.total_slippage_cost
                          + r_flat.total_commission)
            assert tce_total >= flat_total * 0.99

    def test_gates_unchanged_by_cost_model(self):
        """Gate stats must be identical under both cost models — the cost
        model changes fills, not decisions."""
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = _make_trending_bars(500)
        r_tce = run_full_pipeline_backtest(bars, use_tce=True)
        r_flat = run_full_pipeline_backtest(bars, use_tce=False)
        assert r_tce.signals_generated == r_flat.signals_generated
        assert r_tce.signals_passed_gates == r_flat.signals_passed_gates


class TestCPCVExecution:
    def test_cpcv_real_output_shape(self):
        from core.cpcv_backtester import run_combinatorial_purged_backtest
        rng = np.random.default_rng(7)
        returns = list(rng.normal(0.0004, 0.003, 120))
        res = run_combinatorial_purged_backtest(returns_series=returns, n_splits=6, n_test_groups=2)
        assert res["status"] in ("CPCV_VALIDATED", "OVERFITTING_RISK_DETECTED")
        assert "mean_cpcv_sharpe" in res
        assert "deflated_sharpe_ratio" in res
        assert "probability_of_backtest_overfitting_pbo" in res
        assert "is_statistically_robust" in res
        assert 0.0 <= res["probability_of_backtest_overfitting_pbo"] <= 1.0

    def test_cpcv_insufficient_samples_honest(self):
        from core.cpcv_backtester import run_combinatorial_purged_backtest
        res = run_combinatorial_purged_backtest(returns_series=[0.001] * 10)
        assert res["status"] == "INSUFFICIENT_SAMPLES_FOR_CPCV"

    def test_pipeline_cpcv_stage_runs_or_reports_concretely(self):
        """The pipeline CPCV stage must return a concrete result or a
        concrete insufficiency — never be absent or silently skipped."""
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
        pipe = ContinuousBacktestPipeline()
        # Force the replay path off so the stage exercises the journal path
        stage = pipe._stage_cpcv("EURUSD", "default", {"status": "failed"})
        assert isinstance(stage, dict)
        assert "status" in stage
        assert "source" in stage
        assert stage["status"] != "failed"  # concrete verdict, not a bypass
        assert stage["status"] in (
            "CPCV_VALIDATED", "OVERFITTING_RISK_DETECTED",
            "INSUFFICIENT_SAMPLES_FOR_CPCV",
        )

    def test_pipeline_run_records_cpcv_stage(self):
        """A full pipeline run must include a 'cpcv' stage entry."""
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
        pipe = ContinuousBacktestPipeline()
        res = pipe.run_full_validation(symbol="EURUSD", timeframe="H1", bars=300)
        stages = res.get("stages", {})
        assert "cpcv" in stages, "CPCV stage missing from pipeline results"
        assert stages["cpcv"].get("status") in (
            "CPCV_VALIDATED", "OVERFITTING_RISK_DETECTED",
            "INSUFFICIENT_SAMPLES_FOR_CPCV", "ERROR",
        )


class TestATMNetOfCost:
    def test_validate_oos_runs_with_tce(self):
        from core.automated_training_manager import AutomatedTrainingManager
        mgr = AutomatedTrainingManager()
        rng = np.random.default_rng(11)
        closes = list(np.cumprod(1 + rng.normal(0.0002, 0.001, 300)))
        out = mgr._validate_oos("EURUSD", {"trend": {"closes": closes}})
        assert set(out.keys()) >= {"sharpe", "win_rate", "profit_factor", "wfe"}

    def test_validate_oos_empty_is_zeroed(self):
        from core.automated_training_manager import AutomatedTrainingManager
        mgr = AutomatedTrainingManager()
        out = mgr._validate_oos("EURUSD", {})
        assert out == {"sharpe": 0, "win_rate": 0, "profit_factor": 0, "wfe": 0}


class TestWalkForwardEvidence:
    def test_walkforward_reports_insufficient_instead_of_fabricating(self, tmp_path):
        """Fewer than 20 admissible returns used to be replaced by
        np.random.normal(0.05, 0.5, 60), so the stage reported "completed"
        stability/sharpe/kelly metrics computed from noise. An empty journal
        must report concrete insufficiency instead."""
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
        pipe = ContinuousBacktestPipeline(base_dir=str(tmp_path))
        res = pipe.run_full_validation(symbol="EURUSD", timeframe="H1", bars=300,
                                       run_dsr=False, compare_live=False)
        wf = res["stages"].get("walkforward", {})
        assert wf.get("status") == "INSUFFICIENT_JOURNAL_EVIDENCE", wf
        assert wf.get("samples") == 0, wf
        assert "stability_score" not in wf, "fabricated metrics leaked"
