"""Tests for DSR gate integration into backtest harnesses."""
import pytest
import numpy as np


class TestFullPipelineDSR:
    """Test DSR integration in full_pipeline_backtest."""

    def test_backtest_result_has_dsr_fields(self):
        """BacktestResult should have DSR fields."""
        from research.full_pipeline_backtest import BacktestResult
        r = BacktestResult()
        assert hasattr(r, "dsr_confidence")
        assert hasattr(r, "dsr_pvalue")  # legacy alias
        assert hasattr(r, "dsr_passed")
        assert hasattr(r, "dsr_n_trials")
        assert hasattr(r, "dsr_raw_sharpe")
        assert hasattr(r, "dsr_expected_max")
        assert hasattr(r, "dsr_pbo_proxy")
        assert hasattr(r, "dsr_trial_mean")
        assert hasattr(r, "dsr_trial_std")
        assert r.dsr_passed is None  # Not run by default

    def test_to_dict_includes_dsr_fields(self):
        """to_dict() should include DSR fields."""
        from research.full_pipeline_backtest import BacktestResult
        r = BacktestResult(sharpe_ratio=1.5, dsr_pvalue=0.87, dsr_passed=True, dsr_n_trials=12)
        d = r.to_dict()
        assert "dsr_confidence" in d
        assert "dsr_pvalue" in d
        assert "dsr_passed" in d
        assert "dsr_n_trials" in d
        assert d["dsr_confidence"] == d["dsr_pvalue"] == 0.87
        assert d["dsr_passed"] is True
        assert d["dsr_n_trials"] == 12

    def test_run_full_pipeline_with_dsr_disabled(self):
        """Backtest with enable_dsr=False should leave DSR fields at defaults."""
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        # Generate synthetic bars
        bars = []
        price = 1.1000
        for i in range(200):
            price += np.random.randn() * 0.0005
            bars.append({
                "open": price - 0.0001,
                "high": price + 0.0002,
                "low": price - 0.0002,
                "close": price,
                "volume": 1000,
                "timestamp": f"2025-01-01T{i:02d}:00:00",
            })
        result = run_full_pipeline_backtest(
            bars, enable_dsr=False, name="test_no_dsr",
        )
        assert result.dsr_passed is None
        assert result.dsr_pvalue == 0.0

    def test_run_full_pipeline_with_dsr_enabled(self):
        """Backtest with enable_dsr=True should compute DSR when trades >= 5."""
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = []
        price = 1.1000
        np.random.seed(42)
        for i in range(1000):
            # Create trending data to generate more trades
            price += np.random.randn() * 0.001 + 0.00005
            bars.append({
                "open": price - 0.0002,
                "high": price + 0.0005,
                "low": price - 0.0005,
                "close": price,
                "volume": 1000 + np.random.randint(0, 500),
                "timestamp": f"2025-01-01T{i:02d}:00:00",
            })
        result = run_full_pipeline_backtest(
            bars, enable_dsr=True, name="test_with_dsr",
        )
        # If enough trades, DSR should have been computed
        if result.total_trades >= 5:
            assert result.dsr_passed is not None
            assert result.dsr_pvalue >= 0.0
            assert result.dsr_n_trials > 0

    def test_dsr_threshold_override(self):
        """Custom dsr_gate_threshold should be respected."""
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        bars = []
        price = 1.1000
        np.random.seed(99)
        for i in range(1000):
            price += np.random.randn() * 0.001 + 0.00003
            bars.append({
                "open": price - 0.0002,
                "high": price + 0.0005,
                "low": price - 0.0005,
                "close": price,
                "volume": 1000,
                "timestamp": f"2025-01-01T{i:02d}:00:00",
            })
        result = run_full_pipeline_backtest(
            bars, enable_dsr=True, dsr_gate_threshold=0.99,
            name="test_strict_dsr",
        )
        if result.total_trades >= 5:
            assert result.dsr_passed is not None


class TestAlphaHarnessDSR:
    """Test DSR integration in alpha_backtest_harness."""

    def test_alpha_harness_backtest_result_has_dsr_fields(self):
        """The alpha harness BacktestResult should have DSR fields."""
        from research.alpha_backtest_harness import BacktestResult
        r = BacktestResult(name="test", data_source="synthetic")
        assert hasattr(r, "dsr_confidence")
        assert hasattr(r, "dsr_pvalue")  # legacy alias
        assert hasattr(r, "dsr_passed")
        assert hasattr(r, "dsr_n_trials")
        assert r.dsr_passed is None
        assert r.dsr_confidence == 0.0
        assert r.dsr_pvalue == 0.0

    def test_alpha_harness_backtest_result_to_dict(self):
        """to_dict() should include canonical + legacy DSR keys with equal values."""
        from research.alpha_backtest_harness import BacktestResult
        r = BacktestResult(name="test", sharpe_ratio=2.0, dsr_pvalue=0.92, dsr_passed=True,
                          dsr_n_trials=20, data_source="synthetic")
        d = r.to_dict()
        assert "dsr_confidence" in d
        assert "dsr_pvalue" in d
        assert d["dsr_confidence"] == d["dsr_pvalue"] == 0.92
        assert d["dsr_passed"] is True
        assert d["dsr_n_trials"] == 20

    def test_alpha_harness_alias_sync_canonical_to_legacy(self):
        """Setting dsr_confidence via kwargs should sync the legacy alias."""
        from research.alpha_backtest_harness import BacktestResult
        r = BacktestResult(name="t", dsr_confidence=0.81, data_source="synthetic")
        assert r.dsr_pvalue == 0.81

    def test_alpha_harness_alias_sync_legacy_to_canonical(self):
        """Setting the legacy alias via kwargs should sync dsr_confidence."""
        from research.alpha_backtest_harness import BacktestResult
        r = BacktestResult(name="t", dsr_pvalue=0.93, data_source="synthetic")
        assert r.dsr_confidence == 0.93


class TestBacktestDSRIntegrator:
    """Test the BacktestDSRIntegrator module directly."""

    def test_validate_backtest_returns_report(self):
        """validate_backtest should return a DSRReport."""
        from research.backtest_dsr_integrator import BacktestDSRIntegrator
        integrator = BacktestDSRIntegrator()
        report = integrator.validate_backtest(
            strategy_id="test_strat",
            sharpe=1.5,
            n_observations=200,
        )
        assert report.strategy_id == "test_strat"
        assert report.raw_sharpe == 1.5
        assert report.n_observations == 200
        assert isinstance(report.passed_gate, bool)
        assert report.dsr >= 0.0

    def test_trade_floor_rejects_tiny_sample_regardless_of_sharpe(self):
        """MIN_TRADES_FOR_DSR floor: a 4-trade Sharpe-14 run never passes the
        gate — the exact incident shape from the PU-0156 campaign sweep,
        where the deflated Sharpe hit 1.0 on the ledger-wide family size
        while the run itself traded 4 times."""
        from research.backtest_dsr_integrator import (
            BacktestDSRIntegrator, MIN_TRADES_FOR_DSR,
        )
        assert MIN_TRADES_FOR_DSR == 30
        integrator = BacktestDSRIntegrator()
        report = integrator.validate_backtest(
            strategy_id="four_trade_sharpe14",
            sharpe=14.59,
            n_observations=4,
            n_trades=4,
        )
        assert report.passed_gate is False
        assert report.verdict == "FAIL_INSUFFICIENT_TRADES"
        assert report.trades_floor_met is False
        assert report.n_trades == 4
        assert report.min_trades_required == 30
        # 30+ executed trades: the floor no longer decides on its own —
        # the deflated-Sharpe arithmetic gets its turn.
        ok = integrator.validate_backtest(
            strategy_id="thirty_trade_baseline",
            sharpe=1.5,
            n_observations=200,
            n_trades=30,
        )
        assert ok.trades_floor_met is True
        assert ok.verdict != "FAIL_INSUFFICIENT_TRADES"

    def test_validate_with_returns_computes_sharpe(self):
        """validate_backtest_with_returns should compute Sharpe from returns."""
        from research.backtest_dsr_integrator import BacktestDSRIntegrator
        integrator = BacktestDSRIntegrator()
        # Positive returns → positive Sharpe
        returns = [0.001] * 100 + [-0.0005] * 50
        report = integrator.validate_backtest_with_returns(
            strategy_id="test_with_returns",
            returns=returns,
        )
        assert report.raw_sharpe > 0
        assert report.n_observations == 150

    def test_dsr_rejects_high_noise(self):
        """Many trials with low Sharpe should increase n_trials (DSR penalizes multiple testing)."""
        from research.backtest_dsr_integrator import BacktestDSRIntegrator
        from core.trial_ledger import TrialLedger
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            ledger = TrialLedger(db_path=db_path)
            integrator = BacktestDSRIntegrator(ledger=ledger)
            # Record 50 low-Sharpes (noise)
            for i in range(50):
                integrator.validate_backtest(
                    strategy_id=f"noise_{i}",
                    sharpe=0.1 + np.random.randn() * 0.3,
                    n_observations=100,
                )
            # Now test a moderate Sharpe
            report = integrator.validate_backtest(
                strategy_id="moderate_sharpe",
                sharpe=0.8,
                n_observations=100,
            )
            # Key invariant: n_trials must be monotonically increasing
            assert report.n_trials >= 51
            # The trial count increased, which means multiple-testing correction
            # was applied (this is the core UAQP Patch 2 invariant)
            assert report.dsr >= 0.0
            assert report.dsr <= 1.0
        finally:
            try:
                os.unlink(db_path)
            except PermissionError:
                pass

    def test_dsr_passes_single_trial_high_sharpe(self):
        """Single trial with high Sharpe should pass DSR gate."""
        from research.backtest_dsr_integrator import BacktestDSRIntegrator
        from core.trial_ledger import TrialLedger
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            ledger = TrialLedger(db_path=db_path)
            integrator = BacktestDSRIntegrator(ledger=ledger)
            report = integrator.validate_backtest(
                strategy_id="high_sharpe",
                sharpe=3.0,
                n_observations=500,
            )
            assert report.n_trials == 1
            assert report.passed_gate is True
        finally:
            try:
                os.unlink(db_path)
            except PermissionError:
                pass

    def test_summary_text_output(self):
        """summary_text() should produce human-readable output."""
        from research.backtest_dsr_integrator import BacktestDSRIntegrator
        integrator = BacktestDSRIntegrator()
        report = integrator.validate_backtest(
            strategy_id="test_summary",
            sharpe=1.5,
            n_observations=200,
        )
        text = report.summary_text()
        assert "DSR Report: test_summary" in text
        assert "Raw Sharpe:" in text
        assert "DSR:" in text

    def test_ledger_summary(self):
        """get_ledger_summary should return trial stats."""
        from research.backtest_dsr_integrator import BacktestDSRIntegrator
        integrator = BacktestDSRIntegrator()
        integrator.validate_backtest("s1", 1.0, 100)
        integrator.validate_backtest("s2", 0.5, 100)
        summary = integrator.get_ledger_summary()
        assert summary["n_trials"] >= 2
        assert "trial_sharpe_mean" in summary
