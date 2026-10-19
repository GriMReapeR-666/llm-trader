"""Tests for the Alpha Ensemble Backtest Harness."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.alpha_backtest_harness import (
    BacktestResult,
    HarnessResult,
    _build_comparison,
    _compute_hold_return,
    _format_quality_comparison,
    _regime_analysis,
    composite_to_long_short_signals,
    composite_to_signals,
    generate_synthetic_ohlcv,
    format_harness_report,
    run_alpha_backtest_harness,
    run_backtest,
    save_harness_report,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def small_ohlcv():
    """Small dataset for fast tests."""
    return generate_synthetic_ohlcv(n_bars=500, seed=99)


@pytest.fixture
def medium_ohlcv():
    """Medium dataset for backtest quality."""
    return generate_synthetic_ohlcv(n_bars=1500, seed=42)


@pytest.fixture
def synthetic_composite():
    """A synthetic composite signal for signal-generation tests."""
    rng = np.random.RandomState(123)
    n = 500
    # Random walk that crosses thresholds
    vals = np.cumsum(rng.normal(0, 0.3, n))
    return pd.Series(vals, index=range(n))


# ── Synthetic Data Tests ────────────────────────────────────────────────────

class TestSyntheticData:
    def test_generates_correct_length(self):
        df = generate_synthetic_ohlcv(n_bars=300, seed=1)
        assert len(df) == 300

    def test_has_required_columns(self):
        df = generate_synthetic_ohlcv(n_bars=100, seed=1)
        for col in ["time", "open", "high", "low", "close", "volume"]:
            assert col in df.columns

    def test_high_ge_low(self):
        df = generate_synthetic_ohlcv(n_bars=200, seed=1)
        assert (df["high"] >= df["low"]).all()

    def test_close_within_high_low(self):
        df = generate_synthetic_ohlcv(n_bars=200, seed=1)
        assert (df["close"] <= df["high"] * 1.001).all()  # small tolerance
        assert (df["close"] >= df["low"] * 0.999).all()

    def test_volume_positive(self):
        df = generate_synthetic_ohlcv(n_bars=100, seed=1)
        assert (df["volume"] > 0).all()

    def test_deterministic_with_seed(self):
        df1 = generate_synthetic_ohlcv(n_bars=100, seed=42)
        df2 = generate_synthetic_ohlcv(n_bars=100, seed=42)
        pd.testing.assert_frame_equal(df1, df2)


# ── Signal Generation Tests ────────────────────────────────────────────────

class TestSignalGeneration:
    def test_entries_are_boolean(self, synthetic_composite):
        le, lx = composite_to_signals(synthetic_composite)
        assert le.dtype == bool
        assert lx.dtype == bool

    def test_entries_and_exits_same_length(self, synthetic_composite):
        le, lx = composite_to_signals(synthetic_composite)
        assert len(le) == len(lx) == len(synthetic_composite)

    def test_long_short_signals(self, synthetic_composite):
        le, lx, se, sx = composite_to_long_short_signals(synthetic_composite)
        assert le.dtype == bool
        assert se.dtype == bool
        assert len(le) == len(se)

    def test_no_simultaneous_entry_exit(self, synthetic_composite):
        le, lx, se, sx = composite_to_long_short_signals(synthetic_composite)
        # Long entry and long exit can't happen on same bar
        assert not ((le) & (lx)).any()
        # Short entry and short exit can't happen on same bar
        assert not ((se) & (sx)).any()

    def test_threshold_controls_frequency(self, synthetic_composite):
        le1, _, _, _ = composite_to_long_short_signals(synthetic_composite, entry_threshold=0.5)
        le2, _, _, _ = composite_to_long_short_signals(synthetic_composite, entry_threshold=2.0)
        # Higher threshold → fewer entries
        assert le1.sum() >= le2.sum()

    def test_flat_signal_no_entries(self):
        flat = pd.Series(np.zeros(200))
        le, lx, se, sx = composite_to_long_short_signals(flat)
        assert le.sum() == 0
        assert se.sum() == 0


# ── Backtest Engine Tests ───────────────────────────────────────────────────

class TestBacktestEngine:
    def test_basic_long_backtest(self, small_ohlcv):
        n = len(small_ohlcv)
        # Simple: buy at bar 50, sell at bar 100
        entries = pd.Series(False, index=range(n))
        exits = pd.Series(False, index=range(n))
        entries.iloc[50] = True
        exits.iloc[100] = True
        result = run_backtest(small_ohlcv, entries, exits, name="test")
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 1
        assert result.final_equity > 0

    def test_no_trades(self, small_ohlcv):
        n = len(small_ohlcv)
        entries = pd.Series(False, index=range(n))
        exits = pd.Series(False, index=range(n))
        result = run_backtest(small_ohlcv, entries, exits, name="test")
        assert result.total_trades == 0
        assert result.final_equity == 100_000.0

    def test_hold_baseline(self, small_ohlcv):
        result = _compute_hold_return(small_ohlcv)
        assert result.name == "buy_and_hold"
        assert result.total_trades == 1
        assert result.final_equity > 0

    def test_to_dict(self, small_ohlcv):
        n = len(small_ohlcv)
        entries = pd.Series(False, index=range(n))
        exits = pd.Series(False, index=range(n))
        entries.iloc[10] = True
        exits.iloc[20] = True
        result = run_backtest(small_ohlcv, entries, exits, name="test")
        d = result.to_dict()
        assert "total_return_pct" in d
        assert "sharpe_ratio" in d
        assert isinstance(d["total_trades"], int)

    def test_short_backtest(self, small_ohlcv):
        n = len(small_ohlcv)
        entries = pd.Series(False, index=range(n))
        exits = pd.Series(False, index=range(n))
        # Short entry at bar 50, exit at bar 80
        entries.iloc[50] = True
        exits.iloc[80] = True
        result = run_backtest(
            small_ohlcv, pd.Series(False, index=range(n)), pd.Series(False, index=range(n)),
            short_entries=entries, short_exits=exits, name="short_test",
        )
        assert result.total_trades == 1

    def test_commission_reduces_return(self, small_ohlcv):
        n = len(small_ohlcv)
        entries = pd.Series(False, index=range(n))
        exits = pd.Series(False, index=range(n))
        entries.iloc[10] = True
        exits.iloc[50] = True
        r_no_comm = run_backtest(slow_ohlcv := small_ohlcv, entries, exits, commission_pct=0.0, name="nc")
        r_with_comm = run_backtest(small_ohlcv, entries, exits, commission_pct=0.01, name="wc")
        assert r_no_comm.final_equity >= r_with_comm.final_equity


# ── Alpha Backtest Harness Tests ────────────────────────────────────────────

class TestHarness:
    def test_runs_without_error(self, medium_ohlcv):
        """Full harness runs on synthetic data."""
        result = run_alpha_backtest_harness(
            medium_ohlcv,
            alpha_cols=["alpha_012", "alpha_053", "alpha_055"],
            entry_threshold=1.0,
            exit_threshold=0.0,
        )
        assert isinstance(result, HarnessResult)
        assert isinstance(result.ic_weighted, BacktestResult)
        assert isinstance(result.equal_weight, BacktestResult)
        assert isinstance(result.hold_baseline, BacktestResult)

    def test_both_ensembles_produce_trades(self, medium_ohlcv):
        result = run_alpha_backtest_harness(
            medium_ohlcv,
            alpha_cols=["alpha_012", "alpha_053", "alpha_055"],
        )
        # At least one of them should produce trades
        total = result.ic_weighted.total_trades + result.equal_weight.total_trades
        assert total > 0, "Neither ensemble produced any trades"

    def test_comparison_has_all_metrics(self, medium_ohlcv):
        result = run_alpha_backtest_harness(
            medium_ohlcv,
            alpha_cols=["alpha_012", "alpha_053"],
        )
        c = result.comparison_summary
        for key in ["total_return_pct", "sharpe_ratio", "max_drawdown_pct", "verdict"]:
            assert key in c

    def test_verdict_structure(self, medium_ohlcv):
        result = run_alpha_backtest_harness(
            medium_ohlcv,
            alpha_cols=["alpha_012", "alpha_053"],
        )
        v = result.comparison_summary["verdict"]
        assert "ic_wins" in v
        assert "equal_wins" in v
        assert "recommendation" in v
        assert v["ic_wins"] + v["equal_wins"] <= v["total_metrics"]

    def test_quality_report_not_empty(self, medium_ohlcv):
        result = run_alpha_backtest_harness(
            medium_ohlcv,
            alpha_cols=["alpha_012", "alpha_053"],
        )
        assert len(result.alpha_quality_report) > 100

    def test_format_report(self, medium_ohlcv):
        result = run_alpha_backtest_harness(
            medium_ohlcv,
            alpha_cols=["alpha_012", "alpha_053"],
        )
        report = format_harness_report(result)
        assert "IC-WEIGHTED" in report
        assert "Sharpe" in report

    def test_save_json(self, medium_ohlcv, tmp_path):
        result = run_alpha_backtest_harness(
            medium_ohlcv,
            alpha_cols=["alpha_012", "alpha_053"],
        )
        path = str(tmp_path / "report.json")
        saved = save_harness_report(result, path)
        assert os.path.exists(saved)
        with open(saved) as f:
            data = json.load(f)
        assert "ic_weighted" in data
        assert "comparison" in data

    def test_save_html(self, medium_ohlcv, tmp_path):
        from research.alpha_backtest_harness import _save_html_report
        result = run_alpha_backtest_harness(
            medium_ohlcv,
            alpha_cols=["alpha_012", "alpha_053"],
        )
        path = str(tmp_path / "report.html")
        _save_html_report(result, path)
        assert os.path.exists(path)
        content = Path(path).read_text()
        assert "Plotly" in content
        assert "IC-Weighted" in content

    def test_regime_analysis_present(self, medium_ohlcv):
        result = run_alpha_backtest_harness(
            medium_ohlcv,
            alpha_cols=["alpha_012", "alpha_053"],
        )
        rc = result.regime_comparison
        assert rc is not None
        assert "trending_ic_sharpe" in rc
        assert "ranging_eq_sharpe" in rc
        assert rc["trending_bars"] + rc["ranging_bars"] > 0

    def test_reproducible_with_same_data(self, medium_ohlcv):
        r1 = run_alpha_backtest_harness(medium_ohlcv, alpha_cols=["alpha_012"])
        r2 = run_alpha_backtest_harness(medium_ohlcv, alpha_cols=["alpha_012"])
        assert r1.ic_weighted.total_return_pct == r2.ic_weighted.total_return_pct
        assert r1.equal_weight.total_return_pct == r2.equal_weight.total_return_pct

    def test_different_thresholds_change_trades(self, medium_ohlcv):
        r1 = run_alpha_backtest_harness(medium_ohlcv, alpha_cols=["alpha_012"], entry_threshold=0.5)
        r2 = run_alpha_backtest_harness(medium_ohlcv, alpha_cols=["alpha_012"], entry_threshold=2.0)
        # Different thresholds should produce different trade counts
        assert r1.ic_weighted.total_trades != r2.ic_weighted.total_trades or \
               r1.equal_weight.total_trades != r2.equal_weight.total_trades


# ---------------------------------------------------------------------------
# MT5 session ownership
# ---------------------------------------------------------------------------

def test_load_mt5_bars_leaves_shared_session_attached(monkeypatch):
    """Attach when needed, but never shutdown() the process-global session.

    The engine loop and other research pipelines share one MetaTrader5 IPC
    session per process; a harness-side shutdown surfaces as "MT5 terminal not
    attached in this process" on the engine's next cycle.
    """
    import sys
    import types

    import data.market_data_provider as mdp
    import research.alpha_backtest_harness as harness

    shutdowns = []
    fake_mt5 = types.SimpleNamespace(
        terminal_info=lambda: None,          # no session yet -> attach path
        initialize=lambda *a, **k: True,     # attach succeeds first try
        shutdown=lambda: shutdowns.append("shutdown"),
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)

    frame = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=60, freq="h"),
        "open": np.full(60, 1.0),
        "high": np.full(60, 1.1),
        "low": np.full(60, 0.9),
        "close": np.linspace(1.0, 1.5, 60),
        "volume": np.full(60, 1000.0),
    })
    monkeypatch.setattr(mdp, "fetch_ohlc", lambda symbol, timeframe, count: frame.copy())

    out = harness.load_mt5_bars("EURUSD", "H1", 60)

    assert shutdowns == []
    assert {"time", "open", "high", "low", "close"} <= set(out.columns)
    assert len(out) == 60
