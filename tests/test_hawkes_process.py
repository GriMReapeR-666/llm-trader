"""Tests for Hawkes self-exciting point-process calibration."""
import math
import numpy as np
import pytest

from core.hawkes_process import (
    HawkesResult,
    calibrate_hawkes_process,
    hawkes_log_likelihood,
)


def test_hawkes_insufficient_timestamps():
    """Returns safe fallback when fewer than minimum required timestamps provided."""
    timestamps = [1.0, 2.0, 3.0]
    result = calibrate_hawkes_process(timestamps)
    assert isinstance(result, HawkesResult)
    assert result.regime == "INSUFFICIENT_DATA"
    assert result.branching_ratio == 0.0
    assert result.is_cascade is False
    assert result.mu == 0.0
    assert result.alpha == 0.0
    assert result.beta == 1.0


def test_hawkes_regime_classification():
    """Verifies branching ratio threshold classifications."""
    # Super-critical
    r_super = HawkesResult(mu=0.5, alpha=1.1, beta=1.0, branching_ratio=1.1, log_likelihood=0.0, event_count=100)
    assert r_super.regime == "SUPER_CRITICAL"
    assert r_super.is_cascade is True

    # Momentum cascade
    r_cascade = HawkesResult(mu=0.5, alpha=0.88, beta=1.0, branching_ratio=0.88, log_likelihood=0.0, event_count=100)
    assert r_cascade.regime == "MOMENTUM_CASCADE"
    assert r_cascade.is_cascade is True

    # Moderate clustering
    r_mod = HawkesResult(mu=0.5, alpha=0.50, beta=1.0, branching_ratio=0.50, log_likelihood=0.0, event_count=100)
    assert r_mod.regime == "MODERATE_CLUSTERING"
    assert r_mod.is_cascade is False

    # Poisson baseline
    r_poisson = HawkesResult(mu=0.5, alpha=0.15, beta=1.0, branching_ratio=0.15, log_likelihood=0.0, event_count=100)
    assert r_poisson.regime == "POISSON_BASELINE"
    assert r_poisson.is_cascade is False


def test_hawkes_synthetic_cascade_burst():
    """A rapid cascade burst of trade arrivals should trigger MOMENTUM_CASCADE or SUPER_CRITICAL."""
    np.random.seed(42)
    timestamps = []
    t = 0.0
    for cluster in range(8):
        t += 3.0  # inter-cluster delay
        timestamps.append(t)
        for _ in range(7):  # child cascade burst events
            t += np.random.uniform(0.001, 0.02)
            timestamps.append(t)

    result = calibrate_hawkes_process(timestamps)
    assert result.event_count == len(timestamps)
    assert result.branching_ratio >= 0.70
    assert result.is_cascade is True
    assert result.regime in ("MOMENTUM_CASCADE", "SUPER_CRITICAL")
    assert math.isfinite(result.branching_ratio)


def test_hawkes_synthetic_uniform_poisson():
    """Evenly spaced Poisson-like arrivals should yield low branching ratio (POISSON_BASELINE)."""
    # 50 arrivals spaced evenly by 2.0 seconds (no clustering)
    timestamps = [float(i * 2.0) for i in range(50)]
    result = calibrate_hawkes_process(timestamps)
    assert result.branching_ratio < 0.40
    assert result.is_cascade is False
    assert result.regime in ("POISSON_BASELINE", "MODERATE_CLUSTERING")


def test_hawkes_log_likelihood_computation():
    """Verify log-likelihood computation produces finite negative log-likelihood."""
    timestamps = np.array([0.0, 0.5, 0.7, 0.8, 1.2, 1.3, 1.35, 2.0])
    ll = hawkes_log_likelihood(mu=0.5, alpha=0.4, beta=1.0, timestamps=timestamps)
    assert math.isfinite(ll)
    assert isinstance(ll, float)


def test_hawkes_to_dict():
    """Verify serialization to dictionary."""
    res = HawkesResult(mu=0.2, alpha=0.85, beta=1.0, branching_ratio=0.85, log_likelihood=-12.345, event_count=50)
    d = res.to_dict()
    assert d["mu"] == 0.2
    assert d["alpha"] == 0.85
    assert d["beta"] == 1.0
    assert d["branching_ratio"] == 0.85
    assert d["regime"] == "MOMENTUM_CASCADE"
    assert d["is_cascade"] is True
    assert d["event_count"] == 50
