"""Tests for DSR gate integration into news_gate_backtest.py."""
import pytest
import numpy as np


class TestNewsBacktestDSR:
    """Test DSR integration in news gate backtest."""

    def test_backtest_result_has_dsr_fields(self):
        """BacktestResult should have DSR fields."""
        from research.news_gate_backtest import BacktestResult
        r = BacktestResult(data_source="synthetic")
        assert hasattr(r, "dsr_confidence")
        assert hasattr(r, "dsr_pvalue")  # legacy alias
        assert hasattr(r, "dsr_passed")
        assert hasattr(r, "dsr_n_trials")
        assert hasattr(r, "dsr_raw_sharpe")
        assert hasattr(r, "dsr_expected_max")
        assert hasattr(r, "dsr_pbo_proxy")
        assert r.dsr_passed is None  # Not run by default

    def test_synthetic_run_has_dsr_fields(self):
        """run() with synthetic data should have DSR fields at defaults."""
        from research.news_gate_backtest import NewsGateBacktest
        bt = NewsGateBacktest()
        result = bt.run(n_synthetic_trades=100, n_synthetic_events=5, seed=42)
        assert hasattr(result, "dsr_pvalue")
        assert hasattr(result, "dsr_passed")
        # Synthetic run doesn't auto-run DSR (no MT5 bars)
        assert result.dsr_passed is None

    def test_summary_includes_dsr_when_present(self):
        """summary() should include DSR info when gate was run."""
        from research.news_gate_backtest import BacktestResult
        r = BacktestResult(
            data_source="synthetic",
            dsr_pvalue=0.87,
            dsr_passed=True,
            dsr_n_trials=12,
            dsr_raw_sharpe=1.5,
        )
        text = r.summary()
        assert "DSR GATE" in text
        assert "0.87" in text
        assert "PASS" in text
        assert "12" in text

    def test_summary_excludes_dsr_when_not_run(self):
        """summary() should not include DSR section when not run."""
        from research.news_gate_backtest import BacktestResult
        r = BacktestResult(data_source="synthetic")
        text = r.summary()
        assert "DSR GATE" not in text

    def test_synthetic_run_comparison(self):
        """run() should produce with_gate vs without_gate comparison."""
        from research.news_gate_backtest import NewsGateBacktest
        bt = NewsGateBacktest()
        result = bt.run(n_synthetic_trades=200, n_synthetic_events=10, seed=42)
        assert result.total_trades == 200
        assert result.events_analyzed == 10
        assert "sharpe" in result.with_gate
        assert "sharpe" in result.without_gate
        assert "win_rate" in result.with_gate
        assert "win_rate" in result.without_gate

    def test_details_field_exists(self):
        """BacktestResult should have a details dict."""
        from research.news_gate_backtest import BacktestResult
        r = BacktestResult(data_source="synthetic")
        assert hasattr(r, "details")
        assert isinstance(r.details, dict)

    def test_dsr_fields_in_result(self):
        """DSR fields should be stored correctly with alias sync."""
        from research.news_gate_backtest import BacktestResult
        r = BacktestResult(
            data_source="synthetic",
            dsr_pvalue=0.92,
            dsr_passed=True,
            dsr_n_trials=5,
            dsr_raw_sharpe=2.1,
            dsr_expected_max=1.8,
            dsr_pbo_proxy=0.1,
        )
        assert r.dsr_confidence == 0.92  # legacy alias synced to canonical
        assert r.dsr_pvalue == 0.92
        assert r.dsr_passed is True
        assert r.dsr_n_trials == 5
        assert r.dsr_raw_sharpe == 2.1
        assert r.dsr_expected_max == 1.8
        assert r.dsr_pbo_proxy == 0.1

    def test_alias_sync_canonical_to_legacy(self):
        """Setting dsr_confidence via kwargs should sync the legacy alias."""
        from research.news_gate_backtest import BacktestResult
        r = BacktestResult(data_source="synthetic", dsr_confidence=0.77)
        assert r.dsr_pvalue == 0.77
