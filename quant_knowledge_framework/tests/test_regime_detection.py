"""
Numeric regression tests for regime detection modules.

These pin the actual numeric behaviour that the old implementations got
wrong: the broken HurstCalculator returned H ~ 4e-15 for every input, and
HMMRegimeClassifier.classify raised NotFittedError on an unfitted model.
"""

import numpy as np
import pytest

from quant_framework.modules.regime_detection import HMMRegimeClassifier, HurstCalculator


def _trending_prices(n: int = 1200, seed: int = 7) -> np.ndarray:
    """Persistent AR(1) steps -> trending price series (true H >> 0.5)."""
    rs = np.random.RandomState(seed)
    eps = rs.randn(n) * 0.01
    steps = np.zeros(n)
    for t in range(1, n):
        steps[t] = 0.8 * steps[t - 1] + eps[t]
    return 100.0 + np.cumsum(steps)


def _mean_reverting_prices(n: int = 1200, seed: int = 11) -> np.ndarray:
    """Ornstein-Uhlenbeck process -> mean-reverting series (true H < 0.5)."""
    rs = np.random.RandomState(seed)
    e = rs.randn(n)
    p = np.zeros(n)
    for t in range(1, n):
        p[t] = p[t - 1] - 0.2 * p[t - 1] + e[t]
    return 100.0 + p


def _random_walk_prices(n: int = 1200, seed: int = 3) -> np.ndarray:
    rs = np.random.RandomState(seed)
    return 100.0 + np.cumsum(rs.randn(n))


class TestHurstCalculator:
    def setup_method(self):
        self.calc = HurstCalculator()

    def test_trending_series_has_h_above_0_55(self):
        out = self.calc.calculate([float(x) for x in _trending_prices()])
        assert out["hurst_exponent"] > 0.55
        assert out["interpretation"] == "trending"

    def test_mean_reverting_series_has_h_below_0_45(self):
        out = self.calc.calculate([float(x) for x in _mean_reverting_prices()])
        assert out["hurst_exponent"] < 0.45
        assert out["interpretation"] == "mean_reverting"

    def test_random_walk_is_near_0_5(self):
        out = self.calc.calculate([float(x) for x in _random_walk_prices()])
        assert 0.40 < out["hurst_exponent"] < 0.60
        assert out["interpretation"] == "random_walk"

    def test_geometric_brownian_motion_is_random_walk(self):
        rs = np.random.RandomState(5)
        prices = 100.0 * np.exp(np.cumsum(rs.randn(1200) * 0.01))
        out = self.calc.calculate([float(x) for x in prices])
        assert 0.40 < out["hurst_exponent"] < 0.60

    def test_regression_guard_against_near_zero_hurst(self):
        # The old implementation returned H ~ 4e-15 for every series because
        # its tau estimator was mathematically wrong.  A genuine trending
        # series must produce a clearly positive, > 0.5 exponent.
        out = self.calc.calculate([float(x) for x in _trending_prices()])
        assert out["hurst_exponent"] > 0.5

    def test_confidence_is_regression_r2_in_unit_interval(self):
        out = self.calc.calculate([float(x) for x in _trending_prices()])
        assert 0.0 <= out["confidence"] <= 1.0
        assert out["confidence"] > 0.9  # log-log fit is near-perfect on synthetic data

    def test_short_series_returns_neutral_with_warning(self):
        out = self.calc.calculate([100.0 + i for i in range(10)])
        assert out["hurst_exponent"] == 0.5
        assert out["confidence"] == 0.0
        assert "warning" in out

    def test_nonpositive_prices_return_error(self):
        out = self.calc.calculate(([1.0, 0.0, -1.0, 2.0, 3.0]) * 10)
        assert "error" in out

    def test_interpretation_thresholds(self):
        trending = self.calc.calculate([float(x) for x in _trending_prices()])
        mean_rev = self.calc.calculate([float(x) for x in _mean_reverting_prices()])
        assert trending["interpretation"] != mean_rev["interpretation"]


class TestHMMRegimeClassifier:
    def _two_block_series(self) -> np.ndarray:
        """150 calm samples (sd 0.005) then 150 stressed samples (sd 0.05)."""
        rs = np.random.RandomState(42)
        return np.concatenate([rs.randn(150) * 0.005, rs.randn(150) * 0.05])

    def test_fit_returns_metadata(self):
        clf = HMMRegimeClassifier(n_states=2)
        out = clf.fit([float(x) for x in self._two_block_series()])
        assert out["fitted"] is True
        assert out["n_samples"] == 300
        assert out["n_states"] == 2
        assert set(out["state_labels"].values()) == {"calm", "high_vol"}

    def test_classify_after_explicit_fit(self):
        clf = HMMRegimeClassifier(n_states=2)
        data = self._two_block_series()
        clf.fit([float(x) for x in data])
        out = clf.classify([float(x) for x in data[-50:]])
        assert "error" not in out
        assert out["current_regime"] in (0, 1)
        assert out["regime_label"] in ("calm", "high_vol")
        assert 0.0 <= out["confidence"] <= 1.0
        assert len(out["regimes"]) == 50

    def test_classify_autofits_when_unfitted(self):
        # Regression guard: classify used to raise NotFittedError here.
        out = HMMRegimeClassifier(n_states=2).classify(
            [float(x) for x in self._two_block_series()]
        )
        assert "error" not in out
        assert out["current_regime"] in (0, 1)
        assert out["regime_label"] in ("calm", "high_vol")

    def test_high_vol_block_is_labeled_high_vol(self):
        clf = HMMRegimeClassifier(n_states=2)
        data = self._two_block_series()
        clf.fit([float(x) for x in data])
        out = clf.classify([float(x) for x in data[-50:]])
        assert out["regime_label"] == "high_vol"
        assert out["confidence"] > 0.8

    def test_classify_with_volatility_features(self):
        clf = HMMRegimeClassifier(n_states=2)
        data = self._two_block_series()
        vol = (np.abs(data) * 10.0).tolist()
        fit_out = clf.fit([float(x) for x in data], vol)
        assert fit_out["fitted"] is True
        out = clf.classify([float(x) for x in data], vol)
        assert "error" not in out
        assert out["regime_label"] in ("calm", "high_vol")

    def test_too_short_series_returns_error_not_exception(self):
        out = HMMRegimeClassifier(n_states=2).classify([0.01, -0.02, 0.01])
        assert "error" in out

    def test_nonfinite_returns_error(self):
        out = HMMRegimeClassifier(n_states=2).classify([float("nan")] * 50)
        assert "error" in out

    def test_three_states_get_ordered_labels(self):
        clf = HMMRegimeClassifier(n_states=3)
        out = clf.fit([float(x) for x in self._two_block_series() * 2])
        assert out["fitted"] is True
        assert set(out["state_labels"].values()) == {"calm", "normal", "high_vol"}
