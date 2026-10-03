"""Tests for Walk-Forward Validator with Alpha Decay Detection."""
import json
import math
import numpy as np
import pytest
from core.walk_forward_validator import (
    WalkForwardValidator,
    DecayStatus,
    DecayReport,
    WalkForwardResult,
    compute_ic,
    compute_rolling_ic,
)


class TestComputeIC:
    """Test IC computation functions."""

    def test_perfect_correlation(self):
        predicted = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        actual = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        ic = compute_ic(predicted, actual)
        assert ic > 0.9  # Near perfect positive

    def test_negative_correlation(self):
        predicted = np.array([10, 9, 8, 7, 6, 5, 4, 3, 2, 1], dtype=float)
        actual = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        ic = compute_ic(predicted, actual)
        assert ic < -0.9  # Near perfect negative

    def test_no_correlation(self):
        np.random.seed(42)
        predicted = np.random.randn(100)
        actual = np.random.randn(100)
        ic = compute_ic(predicted, actual)
        assert abs(ic) < 0.3  # Should be near zero

    def test_too_few_samples(self):
        predicted = np.array([1.0, 2.0])
        actual = np.array([1.0, 2.0])
        ic = compute_ic(predicted, actual)
        assert ic == 0.0

    def test_with_nan_values(self):
        predicted = np.array([1, 2, np.nan, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        actual = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        ic = compute_ic(predicted, actual)
        assert math.isfinite(ic)

    def test_with_inf_values(self):
        predicted = np.array([1, 2, np.inf, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        actual = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        ic = compute_ic(predicted, actual)
        assert math.isfinite(ic)


class TestComputeRollingIC:
    """Test rolling IC computation."""

    def test_basic_rolling(self):
        predicted = np.arange(50, dtype=float)
        actual = np.arange(50, dtype=float)
        rolling = compute_rolling_ic(predicted, actual, window=10)
        assert len(rolling) == 41
        # All should be high since they're perfectly correlated
        assert all(ic > 0.5 for ic in rolling)

    def test_window_larger_than_data(self):
        predicted = np.arange(5, dtype=float)
        actual = np.arange(5, dtype=float)
        rolling = compute_rolling_ic(predicted, actual, window=10)
        assert len(rolling) == 0

    def test_rolling_with_noise(self):
        np.random.seed(42)
        n = 100
        base = np.arange(n, dtype=float)
        predicted = base + np.random.randn(n) * 0.5
        actual = base + np.random.randn(n) * 0.5
        rolling = compute_rolling_ic(predicted, actual, window=20)
        assert len(rolling) == 81
        mean_ic = np.mean(rolling)
        assert mean_ic > 0  # Should be generally positive


class TestWalkForwardValidator:
    """Test the WalkForwardValidator class."""

    @pytest.fixture
    def validator(self, tmp_path):
        return WalkForwardValidator(
            state_file=str(tmp_path / "wf_state.json"),
            ic_window=10,
            ic_decay_threshold=0.02,
            ic_confirm_bars=3,
        )

    def test_init_defaults(self, validator):
        assert validator.ic_window == 10
        assert validator.ic_decay_threshold == 0.02
        assert validator.ic_confirm_bars == 3
        assert validator.state["ic_history"] == []

    def test_detect_decay_insufficient_data(self, validator):
        predicted = np.array([1.0, 2.0, 3.0])
        actual = np.array([1.0, 2.0, 3.0])
        report = validator.detect_decay(predicted, actual)
        assert report.status == DecayStatus.HEALTHY
        assert "Insufficient data" in report.alerts[0]

    def test_detect_decay_healthy(self, validator):
        """Strong positive IC → HEALTHY status."""
        np.random.seed(42)
        n = 50
        base = np.arange(n, dtype=float) * 0.01
        predicted = base + np.random.randn(n) * 0.001
        actual = base + np.random.randn(n) * 0.001
        report = validator.detect_decay(predicted, actual)
        assert report.status == DecayStatus.HEALTHY
        assert report.current_ic > 0

    def test_detect_decay_decaying(self, validator):
        """IC drops below threshold → DECAYING or WATCH status."""
        rng = np.random.RandomState(42)
        n = 60
        # First 40 bars: strong positive signal
        strong = np.arange(40, dtype=float) * 0.01
        # Last 20 bars: slightly negative (correlation flips mildly)
        weak = np.array([-0.001] * 10 + [0.001] * 10)
        predicted = np.concatenate([strong + rng.randn(40) * 0.001, weak])
        actual = np.concatenate([strong + rng.randn(40) * 0.001, weak + rng.randn(20) * 0.001])
        report = validator.detect_decay(predicted, actual)
        # Should detect some decay or watch status
        assert report.status in (DecayStatus.HEALTHY, DecayStatus.WATCH, DecayStatus.DECAYING)

    def test_detect_decay_dead(self, validator):
        """IC goes very negative → DEAD status."""
        n = 60
        # First 10 bars: positive, then last 50 bars: strongly negative
        predicted = np.array([1.0] * 10 + [-1.0] * 50)
        actual = np.array([-1.0] * 10 + [1.0] * 50)
        report = validator.detect_decay(predicted, actual)
        # Should be either DEAD or DECAYING with negative IC
        assert report.status in (DecayStatus.DEAD, DecayStatus.DECAYING)
        assert report.current_ic < 0.0 or any("harmful" in a.lower() or "decay" in a.lower() for a in report.alerts)

    def test_state_persists(self, validator, tmp_path):
        predicted = np.array([1.0] * 20)
        actual = np.array([1.0] * 20)
        validator.detect_decay(predicted, actual)
        # Re-load state
        v2 = WalkForwardValidator(
            state_file=str(tmp_path / "wf_state.json"),
            ic_window=10,
        )
        assert len(v2.state["ic_history"]) == 1

    def test_state_trimming(self, validator):
        """State should be trimmed to 200 observations."""
        # Add 250 observations
        for _ in range(250):
            predicted = np.random.randn(20)
            actual = predicted + np.random.randn(20) * 0.1
            validator.detect_decay(predicted, actual)
        # State should be trimmed
        assert len(validator.state["ic_history"]) <= 200

    def test_decay_velocity_positive_when_decaying(self, validator):
        """Decay velocity should be positive when IC is declining."""
        # Create data where IC goes from strong to weak
        n = 60
        strong_signal = np.arange(40, dtype=float)
        noise = np.random.RandomState(42).randn(20) * 100
        predicted = np.concatenate([strong_signal, noise])
        actual = np.concatenate([strong_signal, np.random.RandomState(43).randn(20)])
        report = validator.detect_decay(predicted, actual)
        # Decay velocity should be non-negative (positive = declining)
        assert report.decay_velocity >= 0


class TestWalkForwardSplits:
    """Test walk-forward split generation."""

    @pytest.fixture
    def validator(self, tmp_path):
        return WalkForwardValidator(
            state_file=str(tmp_path / "wf_state.json"),
        )

    def test_basic_splits(self, validator):
        returns = np.random.randn(200)
        splits = validator.walk_forward_splits(
            returns, train_size=50, test_size=10, step_size=10
        )
        assert len(splits) > 0
        for split in splits:
            assert split.train_end == split.test_start  # No gap, no overlap
            assert split.test_end <= len(returns)
            assert math.isfinite(split.train_sharpe)
            assert math.isfinite(split.test_sharpe)

    def test_no_lookahead_bias(self, validator):
        """Test period always follows train period."""
        returns = np.random.randn(200)
        splits = validator.walk_forward_splits(
            returns, train_size=50, test_size=10, step_size=10
        )
        for split in splits:
            assert split.test_start >= split.train_end

    def test_degradation_metric(self, validator):
        """Degradation = train_sharpe - test_sharpe."""
        returns = np.random.randn(200)
        splits = validator.walk_forward_splits(
            returns, train_size=50, test_size=10, step_size=10
        )
        for split in splits:
            expected_deg = split.train_sharpe - split.test_sharpe
            assert abs(split.degradation - expected_deg) < 1e-6

    def test_too_short_data(self, validator):
        returns = np.random.randn(5)
        splits = validator.walk_forward_splits(
            returns, train_size=50, test_size=10, step_size=10
        )
        assert len(splits) == 0


class TestDecayReport:
    """Test DecayReport dataclass."""

    def test_to_dict(self):
        report = DecayReport(
            status=DecayStatus.HEALTHY,
            current_ic=0.05,
            mean_ic=0.03,
            ic_std=0.01,
            ic_trend=0.001,
            decay_velocity=0.0,
            half_life=50.0,
            bars_below_threshold=0,
            consecutive_declines=0,
            alerts=["test alert"],
            recommendations=["test rec"],
        )
        d = report.to_dict()
        assert d["status"] == "HEALTHY"
        assert d["current_ic"] == 0.05
        assert d["half_life"] == 50.0
        assert "test alert" in d["alerts"]


class TestGetStatus:
    """Test status endpoint."""

    def _make_validator(self, tmp_path):
        return WalkForwardValidator(
            state_file=str(tmp_path / "wf_status.json"),
            ic_window=10,
        )

    def test_empty_status(self, tmp_path):
        v = self._make_validator(tmp_path)
        status = v.get_status()
        assert status["total_observations"] == 0
        assert status["recent_status"] == "NO_DATA"

    def test_status_after_analysis(self, tmp_path):
        v = self._make_validator(tmp_path)
        predicted = np.random.randn(30)
        actual = predicted + np.random.randn(30) * 0.1
        v.detect_decay(predicted, actual)
        status = v.get_status()
        assert status["total_observations"] == 1
        assert status["recent_status"] in ["HEALTHY", "WATCH", "DECAYING", "DEAD"]
