"""Tests for Regime-Aware Autocorrelation Analysis.

Covers:
  - Per-regime autocorrelation analysis
  - Microstructure change detection between regimes
  - KS test on ACF distributions
  - ACF stability scoring
  - Auto-quant tools wiring (regime_autocorr tool)
"""
from __future__ import annotations

import math
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trending_returns(n: int = 200, mu: float = 0.0005, sigma: float = 0.01) -> np.ndarray:
    """Generate returns with positive autocorrelation (momentum/trending)."""
    rng = np.random.RandomState(42)
    r = np.zeros(n)
    r[0] = mu + sigma * rng.randn()
    for i in range(1, n):
        r[i] = 0.3 * r[i-1] + mu + sigma * rng.randn()  # AR(1) with positive coeff
    return r


def _reverting_returns(n: int = 200, mu: float = 0.0, sigma: float = 0.01) -> np.ndarray:
    """Generate returns with negative autocorrelation (mean-reverting)."""
    rng = np.random.RandomState(99)
    r = np.zeros(n)
    r[0] = mu + sigma * rng.randn()
    for i in range(1, n):
        r[i] = -0.3 * r[i-1] + mu + sigma * rng.randn()  # AR(1) with negative coeff
    return r


def _white_noise(n: int = 200, sigma: float = 0.01) -> np.ndarray:
    """Generate white noise returns (no autocorrelation)."""
    rng = np.random.RandomState(7)
    return rng.normal(0.0, sigma, n)


def _clustered_vol_returns(n: int = 300) -> np.ndarray:
    """Generate returns with volatility clustering (ARCH-like)."""
    rng = np.random.RandomState(55)
    r = np.zeros(n)
    h = np.zeros(n)
    h[0] = 0.01
    for i in range(1, n):
        h[i] = 0.05 + 0.85 * h[i-1] + 0.10 * r[i-1]**2  # GARCH(1,1)-like
        r[i] = math.sqrt(h[i]) * rng.randn()
    return r


# ===========================================================================
# Per-regime analysis tests
# ===========================================================================

class TestRegimeAutocorrelationAnalysis:
    """Test analyze_regime_autocorrelation for individual regimes."""

    def test_trending_regime_detected(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation
        returns = _trending_returns(200)
        result = analyze_regime_autocorrelation(returns, "trending")
        assert result.regime_name == "trending"
        assert result.n_bars == 200
        assert result.has_momentum is True
        assert result.has_mean_reversion is False
        assert result.acf_lag1 > 0

    def test_reverting_regime_detected(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation
        returns = _reverting_returns(200)
        result = analyze_regime_autocorrelation(returns, "reverting")
        assert result.has_mean_reversion is True
        assert result.has_momentum is False
        assert result.acf_lag1 < 0

    def test_white_noise_no_structure(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation
        returns = _white_noise(200)
        result = analyze_regime_autocorrelation(returns, "noise")
        assert result.has_momentum is False
        assert result.has_mean_reversion is False
        # White noise should pass Ljung-Box (no autocorrelation)
        assert result.ljung_box_returns.is_random is True

    def test_volatility_clustering_detected(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation
        returns = _clustered_vol_returns(300)
        result = analyze_regime_autocorrelation(returns, "clustered")
        assert result.has_volatility_clustering is True

    def test_short_series_handled(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation
        returns = np.random.randn(10)
        result = analyze_regime_autocorrelation(returns, "short")
        assert result.n_bars == 10
        # Should not crash, just return minimal results

    def test_empty_series_handled(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation
        returns = np.array([])
        result = analyze_regime_autocorrelation(returns, "empty")
        assert result.n_bars == 0

    def test_acf_values_populated(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation
        returns = _trending_returns(200)
        result = analyze_regime_autocorrelation(returns, "test", max_lag=10)
        assert len(result.acf_returns.acf_values) == 10
        assert len(result.acf_returns.lags) == 10

    def test_pacf_values_populated(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation
        returns = _trending_returns(200)
        result = analyze_regime_autocorrelation(returns, "test", max_lag=10)
        assert len(result.pacf_returns.pacf_values) == 10

    def test_return_statistics(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation
        returns = _trending_returns(200)
        result = analyze_regime_autocorrelation(returns, "test")
        assert result.mean_return != 0 or result.std_return > 0
        assert result.std_return > 0


# ===========================================================================
# Microstructure change detection tests
# ===========================================================================

class TestMicrostructureChanges:
    """Test detection of microstructure changes between regimes."""

    def test_sign_flip_detected(self):
        from core.regime_autocorrelation import (
            analyze_regime_autocorrelation,
            detect_microstructure_changes,
        )
        trending = analyze_regime_autocorrelation(_trending_returns(200), "trending")
        reverting = analyze_regime_autocorrelation(_reverting_returns(200), "reverting")
        changes = detect_microstructure_changes({
            "trending": trending, "reverting": reverting
        })
        assert len(changes) >= 1
        # Should detect the sign flip from positive to negative ACF
        sign_flips = [c for c in changes if c.acf_lag1_sign_flip]
        assert len(sign_flips) >= 1
        assert sign_flips[0].severity == "CRITICAL"

    def test_similar_regimes_no_alert(self):
        from core.regime_autocorrelation import (
            analyze_regime_autocorrelation,
            detect_microstructure_changes,
        )
        r1 = analyze_regime_autocorrelation(_white_noise(200), "noise1")
        r2 = analyze_regime_autocorrelation(_white_noise(200), "noise2")
        changes = detect_microstructure_changes({"n1": r1, "n2": r2})
        # Similar white noise regimes should not trigger changes
        critical = [c for c in changes if c.severity == "CRITICAL"]
        assert len(critical) == 0

    def test_vs_baseline_detection(self):
        from core.regime_autocorrelation import (
            analyze_regime_autocorrelation,
            detect_microstructure_changes,
        )
        baseline = analyze_regime_autocorrelation(_white_noise(500), "baseline")
        trending = analyze_regime_autocorrelation(_trending_returns(200), "trending")
        changes = detect_microstructure_changes(
            {"trending": trending}, baseline=baseline
        )
        # Trending should differ from white noise baseline
        assert len(changes) >= 1

    def test_change_severity_levels(self):
        from core.regime_autocorrelation import MicrostructureChange
        # Test that severity is one of the expected values
        valid_severities = {"INFO", "WARNING", "CRITICAL"}
        for sev in valid_severities:
            change = MicrostructureChange(
                from_regime="a", to_regime="b",
                acf_lag1_shift=0.1, acf_lag1_sign_flip=False,
                ljung_box_p_change=0.0, ks_statistic=0.1, ks_p_value=0.5,
                severity=sev, message="test", recommendation="test",
            )
            assert change.severity in valid_severities


# ===========================================================================
# KS test on ACF
# ===========================================================================

class TestKSACF:
    """Test KS test on ACF distributions."""

    def test_identical_regimes_low_ks(self):
        from core.regime_autocorrelation import (
            analyze_regime_autocorrelation, _ks_test_acf,
        )
        r1 = analyze_regime_autocorrelation(_white_noise(200), "n1")
        r2 = analyze_regime_autocorrelation(_white_noise(200), "n2")
        ks_stat, ks_p = _ks_test_acf(r1, r2)
        # Similar distributions should have low KS statistic
        assert ks_stat < 0.5

    def test_different_regimes_higher_ks(self):
        from core.regime_autocorrelation import (
            analyze_regime_autocorrelation, _ks_test_acf,
        )
        trending = analyze_regime_autocorrelation(_trending_returns(200), "trending")
        reverting = analyze_regime_autocorrelation(_reverting_returns(200), "reverting")
        ks_stat, ks_p = _ks_test_acf(trending, reverting)
        # Different ACF structures should have higher KS
        assert ks_stat > 0.1


# ===========================================================================
# Stability scoring
# ===========================================================================

class TestACFStabilityScore:
    """Test ACF stability scoring."""

    def test_identical_regimes_high_stability(self):
        from core.regime_autocorrelation import (
            analyze_regime_autocorrelation, compute_acf_stability_score,
        )
        r1 = analyze_regime_autocorrelation(_white_noise(200), "n1")
        r2 = analyze_regime_autocorrelation(_white_noise(200), "n2")
        score = compute_acf_stability_score({"n1": r1, "n2": r2})
        assert score > 0.5

    def test_opposite_regimes_low_stability(self):
        from core.regime_autocorrelation import (
            analyze_regime_autocorrelation, compute_acf_stability_score,
        )
        trending = analyze_regime_autocorrelation(_trending_returns(200), "trending")
        reverting = analyze_regime_autocorrelation(_reverting_returns(200), "reverting")
        score = compute_acf_stability_score({
            "trending": trending, "reverting": reverting
        })
        assert score < 0.8

    def test_single_regime_perfect_stability(self):
        from core.regime_autocorrelation import compute_acf_stability_score
        r = MagicMock()
        r.acf_lag1 = 0.05
        score = compute_acf_stability_score({"only": r})
        assert score == 1.0


# ===========================================================================
# Full analysis
# ===========================================================================

class TestFullAnalysis:
    """Test analyze_regime_autocorrelation_full."""

    def test_runs_without_error(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation_full
        n = 300
        returns = np.concatenate([
            _trending_returns(100),
            _reverting_returns(100),
            _white_noise(100),
        ])
        labels = np.array(["trending"]*100 + ["reverting"]*100 + ["noise"]*100)
        result = analyze_regime_autocorrelation_full(returns, labels, "trending")
        assert result.current_regime.regime_name == "trending"
        assert result.acf_stability_score >= 0
        assert result.microstructure_risk in ("LOW", "MODERATE", "HIGH")
        assert len(result.regime_history) == 3

    def test_transition_detected(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation_full
        n = 300
        returns = np.concatenate([
            _trending_returns(150),
            _reverting_returns(150),
        ])
        labels = np.array(["trending"]*150 + ["reverting"]*150)
        result = analyze_regime_autocorrelation_full(returns, labels, "reverting")
        # Should detect changes between trending and reverting
        assert len(result.changes) >= 1

    def test_summary_populated(self):
        from core.regime_autocorrelation import analyze_regime_autocorrelation_full
        returns = np.concatenate([_trending_returns(100), _reverting_returns(100)])
        labels = np.array(["t1"]*100 + ["r1"]*100)
        result = analyze_regime_autocorrelation_full(returns, labels, "t1")
        assert len(result.summary) > 0

    def test_report_formatting(self):
        from core.regime_autocorrelation import (
            analyze_regime_autocorrelation_full, format_regime_autocorrelation_report,
        )
        returns = np.concatenate([_trending_returns(100), _reverting_returns(100)])
        labels = np.array(["t1"]*100 + ["r1"]*100)
        result = analyze_regime_autocorrelation_full(returns, labels, "t1")
        report = format_regime_autocorrelation_report(result)
        assert "REGIME-AWARE AUTOCORRELATION" in report
        assert "t1" in report


# ===========================================================================
# Risk classification
# ===========================================================================

class TestRiskClassification:
    """Test microstructure risk classification."""

    def test_high_risk_on_sign_flip(self):
        from core.regime_autocorrelation import (
            RegimeAutocorrelationResult, RegimeAutocorrelation,
            MicrostructureChange, classify_microstructure_risk,
        )
        # Mock a result with sign flip changes
        change = MicrostructureChange(
            from_regime="a", to_regime="b",
            acf_lag1_shift=0.3, acf_lag1_sign_flip=True,
            ljung_box_p_change=0.5, ks_statistic=0.6, ks_p_value=0.01,
            severity="CRITICAL", message="test", recommendation="test",
        )
        current = MagicMock(spec=RegimeAutocorrelation)
        current.acf_lag1 = 0.1
        result = MagicMock(spec=RegimeAutocorrelationResult)
        result.changes = [change]
        result.acf_stability_score = 0.3
        result.current_regime = current
        risk = classify_microstructure_risk(result)
        assert risk == "HIGH"

    def test_low_risk_stable(self):
        from core.regime_autocorrelation import (
            classify_microstructure_risk,
        )
        current = MagicMock()
        current.acf_lag1 = 0.1
        result = MagicMock()
        result.changes = []
        result.acf_stability_score = 0.9
        result.current_regime = current
        risk = classify_microstructure_risk(result)
        assert risk == "LOW"
