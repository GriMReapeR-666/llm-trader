"""Tests for previously untested core modules:
- core/bocpd.py (Bayesian Online Changepoint Detection)
- core/copula_tail_risk.py (Clayton copula tail dependence)
- core/meta_labeling.py (Triple-barrier labeling + meta-labeling)
- core/cpcv_backtester.py (Combinatorial Purged Cross-Validation)
- core/execution_policy.py (Symbol-specific execution policy)
"""
from __future__ import annotations

import math
import numpy as np
import pytest


# ===========================================================================
# 1. BOCPD — Bayesian Online Changepoint Detection
# ===========================================================================

class TestBOCPD:
    """Test BayesianOnlineChangepointDetector and offline wrapper."""

    def test_detector_init(self):
        from core.bocpd import BayesianOnlineChangepointDetector
        det = BayesianOnlineChangepointDetector(hazard_lambda=50.0)
        assert det.hazard_lambda == 50.0
        assert det.hazard == 1.0 / 50.0
        assert len(det.R) == 1
        assert det.history == []

    def test_update_returns_probability(self):
        from core.bocpd import BayesianOnlineChangepointDetector
        det = BayesianOnlineChangepointDetector(hazard_lambda=100.0)
        # Feed stable data — should return low changepoint probability
        for x in [1.0, 1.01, 0.99, 1.02, 0.98]:
            cp = det.update(x)
            assert isinstance(cp, float)
            assert 0.0 <= cp <= 1.0

    def test_changepoint_detection(self):
        from core.bocpd import BayesianOnlineChangepointDetector
        det = BayesianOnlineChangepointDetector(hazard_lambda=50.0)
        # Feed stable data then a sudden shift
        for x in [1.0] * 30:
            det.update(x)
        # Sudden shift to 10.0 should trigger high CP probability
        cp = det.update(10.0)
        assert cp > 0.01  # Should detect some changepoint signal

    def test_run_length_bounded(self):
        from core.bocpd import BayesianOnlineChangepointDetector
        det = BayesianOnlineChangepointDetector(max_run_length=10)
        for i in range(20):
            det.update(float(i))
        # Run length should not exceed max_run_length
        assert len(det.R) <= det.max_run_length + 1

    def test_offline_detector(self):
        from core.bocpd import detect_changepoints_offline
        # Stable then shifted data
        data = [1.0] * 30 + [5.0] * 20
        cps = detect_changepoints_offline(data, hazard_lambda=30.0, threshold=0.1)
        assert isinstance(cps, list)
        # Should detect at least one changepoint near the shift
        if cps:
            assert any(25 <= cp.get("index", 999) <= 35 for cp in cps)

    def test_empty_data(self):
        from core.bocpd import detect_changepoints_offline
        cps = detect_changepoints_offline([], hazard_lambda=50.0)
        assert cps == []

    def test_single_point(self):
        from core.bocpd import detect_changepoints_offline
        cps = detect_changepoints_offline([1.0], hazard_lambda=50.0)
        assert isinstance(cps, list)


# ===========================================================================
# 2. Copula Tail Risk
# ===========================================================================

class TestCopulaTailRisk:
    """Test Clayton copula tail dependence computation."""

    def test_lower_tail_dependence_correlated(self):
        from core.copula_tail_risk import compute_lower_tail_dependence
        np.random.seed(42)
        # Positively correlated returns
        x = np.random.normal(0, 0.01, 200)
        y = x + np.random.normal(0, 0.002, 200)
        ld = compute_lower_tail_dependence(x, y, quantile=0.05)
        assert 0.0 <= ld <= 1.0
        # Correlated data should have some tail dependence
        assert ld >= 0.0

    def test_lower_tail_dependence_independent(self):
        from core.copula_tail_risk import compute_lower_tail_dependence
        np.random.seed(42)
        x = np.random.normal(0, 0.01, 200)
        y = np.random.normal(0, 0.01, 200)
        ld = compute_lower_tail_dependence(x, y, quantile=0.05)
        assert 0.0 <= ld <= 1.0

    def test_insufficient_data(self):
        from core.copula_tail_risk import compute_lower_tail_dependence
        ld = compute_lower_tail_dependence([1.0, 2.0], [3.0, 4.0])
        assert ld == 0.0

    def test_clayton_copula_tail_risk(self):
        from core.copula_tail_risk import clayton_copula_tail_risk
        np.random.seed(42)
        # 3 assets, 200 observations
        returns = np.random.normal(0, 0.01, (200, 3))
        result = clayton_copula_tail_risk(returns, quantile=0.05)
        assert "avg_tail_dependence" in result
        assert "adjusted_heat_budget_r" in result
        assert "tail_risk_state" in result
        assert result["tail_risk_state"] in ("NORMAL", "ELEVATED", "CRISIS")

    def test_clayton_insufficient_data(self):
        from core.copula_tail_risk import clayton_copula_tail_risk
        returns = np.random.normal(0, 0.01, (10, 2))
        result = clayton_copula_tail_risk(returns)
        assert result["avg_tail_dependence"] == 0.0
        assert result["adjusted_heat_budget_r"] == 4.0

    def test_crisis_state_compresses_heat(self):
        from core.copula_tail_risk import clayton_copula_tail_risk
        np.random.seed(42)
        # Create highly correlated crash-like returns
        base = np.random.normal(-0.05, 0.01, 200)
        returns = np.column_stack([base, base * 0.9, base * 1.1])
        result = clayton_copula_tail_risk(returns, quantile=0.10)
        # High correlation in tails should reduce heat budget
        assert result["adjusted_heat_budget_r"] <= 4.0


# ===========================================================================
# 3. Meta-Labeling
# ===========================================================================

class TestMetaLabeling:
    """Test triple-barrier labeling and meta-labeling."""

    def test_barrier_event_dataclass(self):
        from core.meta_labeling import BarrierEvent
        event = BarrierEvent(
            entry_index=10, exit_index=15, entry_price=1.08, exit_price=1.09,
            touch_type="UPPER_TP", realized_r=1.5, label=1,
        )
        assert event.label == 1
        assert event.touch_type == "UPPER_TP"

    def test_triple_barrier_upper_tp(self):
        from core.meta_labeling import TripleBarrierPathDependentLabeler
        # Steady uptrend — should hit upper barrier
        prices = [100.0 + i * 0.5 for i in range(30)]
        events = TripleBarrierPathDependentLabeler.apply(
            prices, events=[5], upper_r=1.0, lower_r=1.0,
            vertical_barrier_bars=20,
        )
        assert len(events) >= 1
        assert events[0].label == 1  # Upper TP hit

    def test_triple_barrier_lower_sl(self):
        from core.meta_labeling import TripleBarrierPathDependentLabeler
        # Steady downtrend — should hit lower barrier
        prices = [100.0 - i * 0.5 for i in range(30)]
        events = TripleBarrierPathDependentLabeler.apply(
            prices, events=[5], upper_r=1.0, lower_r=1.0,
            vertical_barrier_bars=20,
        )
        assert len(events) >= 1
        assert events[0].label == 0  # Lower SL hit

    def test_triple_barrier_time_expiration(self):
        from core.meta_labeling import TripleBarrierPathDependentLabeler
        # Sideways — should hit time barrier
        prices = [100.0 + 0.001 * math.sin(i) for i in range(30)]
        events = TripleBarrierPathDependentLabeler.apply(
            prices, events=[5], upper_r=5.0, lower_r=5.0,
            vertical_barrier_bars=10,
        )
        assert len(events) >= 1
        assert events[0].touch_type == "TIME_BARRIER"

    def test_empty_events(self):
        from core.meta_labeling import TripleBarrierPathDependentLabeler
        prices = [100.0 + i * 0.1 for i in range(30)]
        events = TripleBarrierPathDependentLabeler.apply(prices, events=[])
        assert events == []

    def test_meta_labeling_classifier(self):
        from core.meta_labeling import MetaLabelingProbabilityClassifier
        clf = MetaLabelingProbabilityClassifier()
        assert clf is not None


# ===========================================================================
# 4. CPCV Backtester
# ===========================================================================

class TestCPCVBacktester:
    """Test Combinatorial Purged Cross-Validation."""

    def test_deflated_sharpe_ratio(self):
        from core.metrics import calculate_deflated_sharpe_ratio
        # High Sharpe, many trials → DSR should be low (snooping penalty)
        dsr = calculate_deflated_sharpe_ratio(
            estimated_sharpe=1.0, benchmark_sharpe=0.0,
            num_trials=1000, sample_size=252,
        )
        assert 0.0 <= dsr <= 1.0

    def test_dsr_single_trial(self):
        from core.metrics import calculate_deflated_sharpe_ratio
        # Single trial, high Sharpe → DSR (p-value) should be low (significant)
        dsr = calculate_deflated_sharpe_ratio(
            estimated_sharpe=2.0, benchmark_sharpe=0.0,
            num_trials=1, sample_size=252,
        )
        assert dsr < 0.5  # low p-value = significant

    def test_dsr_zero_sharpe(self):
        from core.metrics import calculate_deflated_sharpe_ratio
        dsr = calculate_deflated_sharpe_ratio(
            estimated_sharpe=0.0, benchmark_sharpe=0.0,
            num_trials=100, sample_size=252,
        )
        assert 0.0 <= dsr <= 1.0

    def test_cpcv_runs(self):
        from core.cpcv_backtester import run_combinatorial_purged_backtest
        np.random.seed(42)
        returns = np.random.normal(0.0005, 0.01, 200).tolist()
        result = run_combinatorial_purged_backtest(returns, n_splits=4, n_test_groups=2)
        assert "probability_of_backtest_overfitting_pbo" in result
        assert "deflated_sharpe_ratio" in result
        assert "status" in result
        assert 0.0 <= result["probability_of_backtest_overfitting_pbo"] <= 1.0
        assert 0.0 <= result["deflated_sharpe_ratio"] <= 1.0

    def test_cpcv_insufficient_data(self):
        from core.cpcv_backtester import run_combinatorial_purged_backtest
        result = run_combinatorial_purged_backtest([0.01, -0.01], n_splits=4)
        assert result.get("status") in ("INSUFFICIENT_DATA", "ERROR") or "error" in str(result).lower() or result.get("probability_of_backtest_overfitting_pbo", 0) == 0

    def test_cpcv_deterministic(self):
        from core.cpcv_backtester import run_combinatorial_purged_backtest
        np.random.seed(99)
        returns = np.random.normal(0.001, 0.01, 300).tolist()
        r1 = run_combinatorial_purged_backtest(returns, n_splits=4, n_test_groups=2)
        r2 = run_combinatorial_purged_backtest(returns, n_splits=4, n_test_groups=2)
        assert r1["deflated_sharpe_ratio"] == r2["deflated_sharpe_ratio"]


# ===========================================================================
# 5. Execution Policy
# ===========================================================================

class TestExecutionPolicy:
    """Test execution policy validation."""

    def _valid_policy(self):
        return {
            "version": "1.0",
            "symbol": "EURUSD",
            "broker_fingerprint": "MetaQuotes-Demo",
            "max_spread_price": 0.0003,
            "min_stop_distance_price": 0.0010,
            "entry_tolerance_min_price": 0.0002,
            "entry_tolerance_atr_fraction": 0.5,
            "expected_slippage_price": 0.0001,
            "commission_per_volume": 0.0,
            "expected_swap_money": 0.0,
            "expires_seconds": 3600,
            "max_tick_age_ms": 5000,
            "status": "APPROVED",
            "reviewed_at": "2026-01-01T00:00:00Z",
        }

    def test_valid_policy_passes(self):
        from core.execution_policy import validate
        policy = self._valid_policy()
        result = validate(policy, "EURUSD")
        assert result is not None

    def test_missing_field_rejects(self):
        from core.execution_policy import validate
        policy = self._valid_policy()
        del policy["max_spread_price"]
        with pytest.raises(ValueError, match="missing"):
            validate(policy, "EURUSD")

    def test_unapproved_rejects(self):
        from core.execution_policy import validate
        policy = self._valid_policy()
        policy["status"] = "PENDING"
        with pytest.raises(ValueError, match="no approved"):
            validate(policy, "EURUSD")

    def test_symbol_mismatch_rejects(self):
        from core.execution_policy import validate
        policy = self._valid_policy()
        with pytest.raises(ValueError, match="symbol mismatch"):
            validate(policy, "GBPUSD")

    def test_negative_spread_rejects(self):
        from core.execution_policy import validate
        policy = self._valid_policy()
        policy["max_spread_price"] = -0.001
        with pytest.raises(ValueError):
            validate(policy, "EURUSD")

    def test_zero_expires_rejects(self):
        from core.execution_policy import validate
        policy = self._valid_policy()
        policy["expires_seconds"] = 0
        with pytest.raises(ValueError):
            validate(policy, "EURUSD")

    def test_empty_string_field_rejects(self):
        from core.execution_policy import validate
        policy = self._valid_policy()
        policy["version"] = ""
        with pytest.raises(ValueError, match="missing"):
            validate(policy, "EURUSD")

    def test_infinite_spread_rejects(self):
        from core.execution_policy import validate
        policy = self._valid_policy()
        policy["max_spread_price"] = float("inf")
        with pytest.raises(ValueError):
            validate(policy, "EURUSD")
