"""Unit tests for P10: Self-Equation Extractor & Recognizer (SEER)
Symbolic Recovery of Feedback Functions in Algorithmic Trading Signals (Merlini et al. 2026).

Validates:
- Linear feedback recovery (f(u) = lambda * u).
- Non-linear quadratic feedback recovery (f(u) = lambda * u^2 * sgn(u)) where linear models fail.
- Asymmetric ReLU feedback recovery (f(u) = lambda * max(0, u)).
- Negative control: Pure noise results in NOISE_FLOOR with is_impact_detected = False.
- SNR detection limit: Low SNR correctly identified as noise floor rather than spurious fit.
"""
from __future__ import annotations

import numpy as np
import pytest

from research.seer_symbolic_recovery import (
    FeedbackModelResult,
    FeedbackRegime,
    SEERRecoveryEngine,
)


def test_seer_linear_feedback_recovery():
    """Verify SEER accurately recovers linear market feedback with R^2 > 0.95."""
    engine = SEERRecoveryEngine()
    u, y = engine.simulate_synthetic_benchmark(
        regime="linear",
        n_samples=2000,
        lambda_val=0.05,
        noise_std=0.002,
        seed=42,
    )

    res = engine.fit(u, y)
    assert res.regime == FeedbackRegime.LINEAR
    assert res.is_impact_detected is True
    assert res.r_squared > 0.95
    assert res.lambda_coeff == pytest.approx(0.05, abs=0.005)
    assert "u" in res.equation
    assert res.snr_db > 10.0


def test_seer_quadratic_feedback_recovery():
    """Verify SEER recovers quadratic feedback f(u) = lambda * u^2 * sgn(u)
    where linear regression completely collapses (R^2 ~ 0).
    """
    engine = SEERRecoveryEngine()
    u, y = engine.simulate_synthetic_benchmark(
        regime="quadratic",
        n_samples=2000,
        lambda_val=0.05,
        noise_std=0.002,
        seed=42,
    )

    res = engine.fit(u, y)
    assert res.regime == FeedbackRegime.QUADRATIC
    assert res.is_impact_detected is True
    assert res.r_squared > 0.95
    assert res.lambda_coeff == pytest.approx(0.05, abs=0.005)
    assert "u^2" in res.equation


def test_seer_relu_asymmetric_feedback_recovery():
    """Verify SEER recovers asymmetric ReLU feedback (one-sided buying impact)."""
    engine = SEERRecoveryEngine()
    u, y = engine.simulate_synthetic_benchmark(
        regime="relu",
        n_samples=2000,
        lambda_val=0.08,
        noise_std=0.002,
        seed=42,
    )

    res = engine.fit(u, y)
    assert res.regime == FeedbackRegime.ASYMMETRIC_RELU
    assert res.is_impact_detected is True
    assert res.r_squared > 0.90
    assert res.lambda_coeff == pytest.approx(0.08, abs=0.01)
    assert "max(0, u)" in res.equation


def test_seer_negative_control_noise_floor():
    """Verify that unmanipulated/noise-only data produces NOISE_FLOOR
    and is_impact_detected = False (Merlini 2026 negative control).
    """
    engine = SEERRecoveryEngine()
    np.random.seed(42)
    u = np.random.uniform(-1.0, 1.0, size=2000)
    y_noise = np.random.normal(0.0, 0.005, size=2000)  # No signal injection

    res = engine.fit(u, y_noise)
    assert res.regime == FeedbackRegime.NOISE_FLOOR
    assert res.is_impact_detected is False
    assert res.alpha_decay_risk == "NEGLIGIBLE"
    assert res.r_squared < 0.05


def test_seer_snr_sensitivity_threshold():
    """Verify that weak signals below detection floor (e.g. SNR < -15 dB)
    are safely rejected as noise floor rather than spuriously fit.
    """
    engine = SEERRecoveryEngine()
    u, y = engine.simulate_synthetic_benchmark(
        regime="linear",
        n_samples=2000,
        lambda_val=0.0001,  # Sub-threshold impact
        noise_std=0.005,
        seed=42,
    )

    res = engine.fit(u, y)
    # Below detection limit, should default to NOISE_FLOOR
    assert res.regime == FeedbackRegime.NOISE_FLOOR
    assert res.is_impact_detected is False
