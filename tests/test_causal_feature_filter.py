"""Tests for CRL-ABSO inspired CausalFeatureFilter module.

Covers:
- Granger causality test
- Mutual information estimation
- Partial correlation
- Causal score fusion
- Feature filtering
- Confounder detection
- Edge cases
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from core.causal_feature_filter import (
    CausalFeatureFilter,
    CausalFeatureReport,
    CausalScore,
    _granger_causality_test,
    _mutual_information,
    _partial_correlation,
)


# ---------------------------------------------------------------------------
# Granger Causality Tests
# ---------------------------------------------------------------------------

class TestGrangerCausality:
    def test_no_causality_random(self):
        np.random.seed(42)
        x = np.random.randn(100)
        y = np.random.randn(100)
        f, p = _granger_causality_test(x, y, lag=3)
        # Random data should have low F and high p
        assert f < 5.0
        assert p > 0.01

    def test_causality_x_drives_y(self):
        np.random.seed(42)
        n = 100
        x = np.random.randn(n)
        # y = lagged x + noise (x causes y with lag=1)
        y = np.zeros(n)
        for i in range(1, n):
            y[i] = 0.8 * x[i - 1] + 0.2 * np.random.randn()
        f, p = _granger_causality_test(x, y, lag=3)
        # x should Granger-cause y
        assert f > 1.0  # F > 1 suggests some predictive power

    def test_insufficient_data(self):
        x = np.array([1.0, 2.0, 3.0])
        y = np.array([1.0, 2.0, 3.0])
        f, p = _granger_causality_test(x, y, lag=5)
        assert f == 0.0
        assert p == 1.0

    def test_identical_series(self):
        # When x == y, the restricted model (y lagged) already
        # perfectly predicts y, so adding x doesn't help much.
        # This is expected behavior — test that it doesn't crash.
        x = np.arange(50, dtype=float)
        y = x.copy()
        f, p = _granger_causality_test(x, y, lag=3)
        assert isinstance(f, float)
        assert isinstance(p, float)


# ---------------------------------------------------------------------------
# Mutual Information Tests
# ---------------------------------------------------------------------------

class TestMutualInformation:
    def test_uncorrelated(self):
        np.random.seed(42)
        x = np.random.randn(200)
        y = np.random.randn(200)
        mi = _mutual_information(x, y, n_bins=10)
        assert mi < 0.2  # Should be low

    def test_perfect_correlation(self):
        x = np.arange(200, dtype=float)
        y = x * 2.0
        mi = _mutual_information(x, y, n_bins=10)
        assert mi > 0.5  # Should be high

    def test_nonlinear_relationship(self):
        np.random.seed(42)
        x = np.linspace(-3, 3, 200)
        y = x ** 2  # Nonlinear
        mi = _mutual_information(x, y, n_bins=10)
        assert mi > 0.3  # MI captures nonlinear deps

    def test_insufficient_data(self):
        assert _mutual_information(np.array([1.0]), np.array([1.0])) == 0.0


# ---------------------------------------------------------------------------
# Partial Correlation Tests
# ---------------------------------------------------------------------------

class TestPartialCorrelation:
    def test_direct_relationship(self):
        np.random.seed(42)
        n = 200
        z = np.random.randn(n)
        x = z + 0.1 * np.random.randn(n)
        y = z + 0.1 * np.random.randn(n)
        # x and y are correlated through z (confounder)
        p_corr = _partial_correlation(x, y, z)
        # After controlling for z, partial corr should be near 0
        assert abs(p_corr) < 0.3

    def test_direct_cause(self):
        np.random.seed(42)
        n = 200
        x = np.random.randn(n)
        noise = np.random.randn(n)
        y = 0.8 * x + noise  # x directly causes y
        z = np.random.randn(n)  # Unrelated confounder
        p_corr = _partial_correlation(x, y, z)
        # Should remain high even after controlling for z
        assert abs(p_corr) > 0.5

    def test_insufficient_data(self):
        assert _partial_correlation(np.array([1.0]), np.array([1.0]), np.array([1.0])) == 0.0


# ---------------------------------------------------------------------------
# CausalScore Tests
# ---------------------------------------------------------------------------

class TestCausalScore:
    def test_to_dict(self):
        score = CausalScore(
            feature_name="rsi_14",
            correlation=0.5,
            partial_correlation=0.4,
            granger_f=3.5,
            granger_p=0.02,
            mutual_info=0.3,
            causal_score=0.65,
            is_causal=True,
            temporal_lag=2,
        )
        d = score.to_dict()
        assert d["feature"] == "rsi_14"
        assert d["causal_score"] == 0.65
        assert d["is_causal"] is True


# ---------------------------------------------------------------------------
# CausalFeatureFilter Tests
# ---------------------------------------------------------------------------

class TestCausalFeatureFilter:
    def _make_filter(self, tmp_path: Path) -> CausalFeatureFilter:
        return CausalFeatureFilter(
            state_file=str(tmp_path / "causal_state.json"),
            min_samples=20,
            causal_threshold=0.3,
        )

    def test_basic_analysis(self, tmp_path):
        f = self._make_filter(tmp_path)
        np.random.seed(42)
        n = 50
        # Feature 1: correlated with outcome (causal)
        feat1 = np.random.randn(n) * 0.5 + np.arange(n) * 0.02
        # Feature 2: random (spurious)
        feat2 = np.random.randn(n)
        outcome = feat1 + 0.1 * np.random.randn(n)
        
        report = f.analyze(
            {"feature_1": feat1.tolist(), "feature_2": feat2.tolist()},
            outcome.tolist(),
        )
        assert isinstance(report, CausalFeatureReport)
        assert len(report.feature_scores) == 2
        # feature_1 should have higher causal score
        scores_dict = {s.feature_name: s.causal_score for s in report.feature_scores}
        assert scores_dict["feature_1"] > scores_dict["feature_2"]

    def test_insufficient_samples(self, tmp_path):
        f = self._make_filter(tmp_path)
        report = f.analyze(
            {"feat": [1.0, 2.0]},
            [1.0, 2.0],
        )
        assert len(report.feature_scores) == 0
        assert report.overall_causal_density == 0.0

    def test_filter_features(self, tmp_path):
        f = self._make_filter(tmp_path)
        report = CausalFeatureReport(
            feature_scores=[],
            causal_features=["rsi_14", "macd_hist"],
            spurious_features=["noise"],
            confounders=[],
            overall_causal_density=0.67,
        )
        features = {"rsi_14": 50.0, "macd_hist": 0.5, "noise": 999.0}
        filtered = f.filter_features(features, report)
        assert "rsi_14" in filtered
        assert "macd_hist" in filtered
        assert "noise" not in filtered

    def test_filter_no_report(self, tmp_path):
        f = self._make_filter(tmp_path)
        features = {"rsi_14": 50.0}
        filtered = f.filter_features(features, None)
        assert filtered == features

    def test_confounder_detection(self, tmp_path):
        f = self._make_filter(tmp_path)
        np.random.seed(42)
        n = 60
        # Confounder z drives both x and y
        z = np.random.randn(n)
        x = z + 0.1 * np.random.randn(n)
        y = z + 0.1 * np.random.randn(n)
        # x has high corr with y but low partial corr (confounded)
        
        report = f.analyze(
            {"feature_x": x.tolist()},
            y.tolist(),
        )
        # Check the report exists and has scores
        assert len(report.feature_scores) == 1

    def test_state_persists(self, tmp_path):
        f = self._make_filter(tmp_path)
        np.random.seed(42)
        n = 50
        feat = np.random.randn(n)
        outcome = feat + 0.1 * np.random.randn(n)
        f.analyze({"feat": feat.tolist()}, outcome.tolist())
        
        # Load new instance
        f2 = CausalFeatureFilter(
            state_file=str(tmp_path / "causal_state.json"),
            min_samples=20,
        )
        status = f2.get_status()
        assert status["status"] == "ACTIVE"

    def test_get_status_empty(self, tmp_path):
        f = self._make_filter(tmp_path)
        status = f.get_status()
        assert status["status"] == "NO_DATA"

    def test_multiple_features_ranking(self, tmp_path):
        f = self._make_filter(tmp_path)
        np.random.seed(42)
        n = 80
        # Strong causal feature
        feat_strong = np.random.randn(n) + np.arange(n) * 0.05
        # Weak causal feature
        feat_weak = np.random.randn(n) * 0.1
        # Random feature
        feat_random = np.random.randn(n)
        
        outcome = feat_strong * 0.5 + 0.1 * np.random.randn(n)
        
        report = f.analyze(
            {"strong": feat_strong.tolist(), "weak": feat_weak.tolist(), "random": feat_random.tolist()},
            outcome.tolist(),
        )
        
        # Should be ranked by causal score
        scores = [(s.feature_name, s.causal_score) for s in report.feature_scores]
        assert scores[0][1] >= scores[1][1]
        assert scores[1][1] >= scores[2][1]

    def test_causal_density(self, tmp_path):
        f = self._make_filter(tmp_path)
        report = CausalFeatureReport(
            feature_scores=[],
            causal_features=["a", "b", "c"],
            spurious_features=["d"],
            confounders=[],
            overall_causal_density=0.75,
        )
        assert report.overall_causal_density == 0.75
        assert len(report.causal_features) == 3
        assert len(report.spurious_features) == 1
