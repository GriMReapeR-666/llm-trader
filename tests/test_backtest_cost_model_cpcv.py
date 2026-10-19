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


class TestStage1DataPath:
    def test_stage1_runs_without_mt5_trader_in_sys_modules(self, tmp_path, monkeypatch):
        """Engine-as-__main__ regression (2026-10-18, caught live).

        The engine starts as `python mt5_trader.py`, so the engine module is
        registered as sys.modules['__main__'] and never as 'mt5_trader'. A
        Stage-1 pre-check keying on sys.modules['mt5_trader'].mt5 is therefore
        always-None in the live engine and produced a permanent failure loop
        (32 consecutive 'Stage 1 failed: MT5 terminal not attached' at the
        ~2.5 min research cadence). fetch_ohlc() already fails closed on its
        own; Stage 1 must simply call it and let its own guard raise when no
        terminal is reachable in this process.
        """
        import sys
        import pandas as pd
        import data.market_data_provider as mdp
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

        monkeypatch.delitem(sys.modules, "mt5_trader", raising=False)
        assert "mt5_trader" not in sys.modules

        rows = 320
        base = np.linspace(1.09, 1.11, rows)
        frame = pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=rows, freq="1h"),
                "open": base,
                "high": base + 0.0010,
                "low": base - 0.0010,
                "close": base + 0.0005,
                "tick_volume": np.full(rows, 1000.0),
            }
        )
        seen = {}

        def fake_fetch(symbol, timeframe, count):
            seen["args"] = (symbol, timeframe, count)
            return frame

        monkeypatch.setattr(mdp, "fetch_ohlc", fake_fetch)

        def fake_backtest(self, **kwargs):
            seen["bars_df"] = kwargs.get("bars_df")
            return {"status": "STUB", "sharpe": 0.11, "executed": 3}

        monkeypatch.setattr(
            ContinuousBacktestPipeline, "_run_pipeline_backtest", fake_backtest
        )

        pipe = ContinuousBacktestPipeline(base_dir=str(tmp_path))
        res = pipe.run_full_validation(
            symbol="EURUSD", timeframe="H1", bars=600,
            run_walkforward=False, run_dsr=False, compare_live=False,
        )
        backtest = res["stages"]["backtest"]
        assert backtest["status"] == "STUB", backtest
        assert seen["args"] == ("EURUSD", "H1", 600)
        assert seen["bars_df"] is frame


class TestPipelineBacktestNormalization:
    def test_engine_nested_metrics_are_flattened(self, tmp_path, monkeypatch):
        """The MT5 backtest engine reports metrics nested under 'metrics'
        (sharpe_ratio, win_rate, ...). The pipeline stages read a flat schema
        (sharpe, win_rate, ...). _run_pipeline_backtest must normalize, or
        every stage silently sees zeros (discovered 2026-10-18 via the long
        gauntlet: Stage 1 always reported sharpe=0.000/trades=0)."""
        import sys
        import pandas as pd
        import research.mt5_backtest_engine as mbe
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

        class FakeEngine:
            def run_full_autonomous_backtest(self, **kwargs):
                return {
                    "status": "COMPLETED",
                    "metrics": {
                        "sharpe_ratio": 1.7, "win_rate": 0.62, "profit_factor": 1.9,
                        "expectancy_r": 0.4, "max_drawdown_pct": 8.5, "total_trades": 42,
                    },
                    "strategy_improvement": {
                        "dsr_gate": {"n_trials": 711, "dsr": 1.0, "passed": True},
                    },
                }

        monkeypatch.setattr(mbe, "MT5BrokerBacktestEngine", FakeEngine)
        monkeypatch.setitem(sys.modules, "mt5_backtest_engine", mbe)

        pipe = ContinuousBacktestPipeline(base_dir=str(tmp_path))
        frame = pd.DataFrame({"close": [1.0] * 100})
        res = pipe._run_pipeline_backtest("EURUSD", "default", frame)
        assert res["status"] == "COMPLETED"
        assert res["sharpe"] == 1.7
        assert res["win_rate"] == 0.62
        assert res["profit_factor"] == 1.9
        assert res["expectancy_r"] == 0.4
        assert res["max_drawdown"] == 8.5
        assert res["total_trades"] == 42
        assert res["n_trades"] == 42
        assert res["n_obs"] == 100
        assert res["r_multiples"] is None
        assert res["n_trials"] == 711
        assert "_raw" in res


class TestRMultiplierExtraction:
    def test_extracts_r_multiples_from_trade_dicts(self, tmp_path, monkeypatch):
        import sys
        import pandas as pd
        import research.mt5_backtest_engine as mbe
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

        class FakeEngine:
            def run_full_autonomous_backtest(self, **kwargs):
                return {
                    "status": "COMPLETED",
                    "metrics": {
                        "sharpe_ratio": 1.2, "win_rate": 0.6, "profit_factor": 1.5,
                        "expectancy_r": 0.3, "max_drawdown_pct": 5.0, "total_trades": 3,
                        "trades": [
                            {"trade_id": 1, "r_multiple": 0.5},
                            {"trade_id": 2, "r_multiple": -0.2},
                            {"trade_id": 3, "r_multiple": None},
                        ],
                    },
                    "strategy_improvement": {"dsr_gate": {"n_trials": 100, "dsr": 1.2, "passed": True}},
                }

        monkeypatch.setattr(mbe, "MT5BrokerBacktestEngine", FakeEngine)
        monkeypatch.setitem(sys.modules, "mt5_backtest_engine", mbe)

        pipe = ContinuousBacktestPipeline(base_dir=str(tmp_path))
        frame = pd.DataFrame({"open": [1.0]*10, "close": [1.0]*10, "high": [1.01]*10, "low": [0.99]*10})
        res = pipe._run_pipeline_backtest("EURUSD", "default", frame)
        assert res["r_multiples"] == [0.5, -0.2]
        assert res["total_trades"] == 3


class TestLiveValidationGateStage:
    def test_stage_runs_and_returns_passed_flag(self, tmp_path, monkeypatch):
        import sys
        import pandas as pd
        import research.mt5_backtest_engine as mbe
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

        class FakeEngine:
            def run_full_autonomous_backtest(self, **kwargs):
                return {
                    "status": "COMPLETED",
                    "metrics": {
                        "sharpe_ratio": 1.5, "win_rate": 55.0, "profit_factor": 2.0,
                        "expectancy_r": 0.5, "max_drawdown_pct": 8.0, "total_trades": 5,
                        "trades": [{"r_multiple": 0.5}, {"r_multiple": 0.3}],
                    },
                    "strategy_improvement": {"dsr_gate": {"n_trials": 100, "dsr": 1.5, "passed": True}},
                }

        monkeypatch.setattr(mbe, "MT5BrokerBacktestEngine", FakeEngine)
        monkeypatch.setitem(sys.modules, "mt5_backtest_engine", mbe)

        pipe = ContinuousBacktestPipeline(base_dir=str(tmp_path))
        frame = pd.DataFrame({"open": [1.0]*10, "close": [1.0]*10, "high": [1.01]*10, "low": [0.99]*10})
        res = pipe.run_full_validation("EURUSD", "default", timeframe="H1", bars=10,
                                       run_walkforward=False, run_dsr=False, compare_live=False)
        assert "live_validation" in res["stages"]
        lv = res["stages"]["live_validation"]
        assert isinstance(lv, dict)
        assert "passed" in lv
        assert "gates" in lv


class TestDsrGate:
    def test_prefers_engine_dsr_result(self):
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

        pipe = ContinuousBacktestPipeline(base_dir="")
        # 40 executed trades: above the tiny-sample floor, the engine's own
        # DSR gate result is authoritative.
        backtest_res = {
            "sharpe": 7.91, "n_obs": 500, "total_trades": 40, "n_trials": 724,
            "_raw": {"strategy_improvement": {"dsr_gate": {"dsr": 1.0, "passed": True}}},
        }
        out = pipe._run_dsr_gate(backtest_res, "EURUSD", "H1")
        assert out["status"] == "OK"
        assert out["deflated_sharpe"] == 1.0
        assert out["passed"] is True

    def test_four_trade_sharpe14_never_passes(self):
        """The incident that motivated the floor: 4 executed trades annualized
        to Sharpe ~14.6 with the engine-side DSR at 1.0 stamping the run as
        validated. A run-level trade floor must reject it regardless of what
        the engine-side statistics look like."""
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

        pipe = ContinuousBacktestPipeline(base_dir="")
        backtest_res = {
            "sharpe": 14.59, "n_obs": 500, "total_trades": 4, "n_trials": 2323,
            "_raw": {"strategy_improvement": {"dsr_gate": {"dsr": 1.0, "passed": True}}},
        }
        out = pipe._run_dsr_gate(backtest_res, "EURUSD", "H1")
        assert out["status"] == "FAILED"
        assert out["reason"] == "insufficient_trades"
        assert out["passed"] is False
        assert out["n_trades"] == 4
        assert out["min_trades_required"] == 30
        # And the compound verdict must refuse to bless such a run.
        verdict = pipe._compute_verdict({
            "backtest": {"status": "COMPLETED", "sharpe": 14.59, "total_trades": 4},
            "dsr": out,
            "walkforward": {"status": "OK", "floor_20": {"met": True, "count": 20}},
        })
        assert verdict["passed"] is False
        assert "dsr_gate_failed" in verdict["blockers"]

    def test_falls_back_to_pvalue_when_no_engine_result(self):
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

        pipe = ContinuousBacktestPipeline(base_dir="")
        # 40 executed trades (above the tiny-sample floor) so this test stays
        # focused on the p-value fallback path itself.
        backtest_res = {"sharpe": 0.2, "n_obs": 500, "total_trades": 40, "n_trials": 4}
        out = pipe._run_dsr_gate(backtest_res, "EURUSD", "H1")
        assert out["status"] == "OK"
        # p-value path: passed only if deflated < 0.05
        assert isinstance(out["passed"], bool)
        assert "deflated_sharpe" in out


class TestVerdictGates:
    def test_completed_backtest_status_enables_pass(self):
        """mt5_trader.py's background loop consumes verdict.passed live. The
        backtest engine reports status 'COMPLETED', not 'OK'; keying the
        stage check on 'OK' alone kept verdict.passed False for every run,
        healthy or not (dead-wired gate discovered 2026-10-18 after the
        live stage registry was restored). Exact quantitative gates below."""
        from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

        pipe = ContinuousBacktestPipeline(base_dir="")
        good = {
            "stages": {
                "backtest": {"status": "COMPLETED", "sharpe": 1.4},
                "dsr": {"status": "OK", "deflated_sharpe": 1.6},
                "walkforward": {
                    "status": "OK",
                    "floor_20": {"met": True, "count": 20},
                },
            }
        }
        verdict = pipe._compute_verdict(good)
        assert verdict["passed"] is True, verdict
        assert verdict["status"] == "PASSED"

        # Honest failure paths must still fail:
        weak_sharpe = pipe._compute_verdict(
            {"stages": {"backtest": {"status": "COMPLETED", "sharpe": 0.2},
                         "dsr": {"status": "OK", "deflated_sharpe": 1.6},
                         "walkforward": {"status": "OK", "floor_20": {"met": True, "count": 20}}}}
        )
        assert weak_sharpe["passed"] is False
        low_dsr = pipe._compute_verdict(
            {"stages": {"backtest": {"status": "COMPLETED", "sharpe": 1.4},
                         "dsr": {"status": "SKIPPED", "deflated_sharpe": 0.0},
                         "walkforward": {"status": "OK", "floor_20": {"met": True, "count": 20}}}}
        )
        assert low_dsr["passed"] is False
