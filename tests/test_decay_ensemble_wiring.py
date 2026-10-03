"""Tests for wiring AlphaDecayMonitor health into AlphaEnsemble re-weighting.

Verifies that:
- DEAD/REVERSED alphas get zeroed weight in ensemble
- DEGRADED alphas get halved weight
- Healthy alphas are unaffected
- All dead → equal weight fallback
- No decay_health → no change
- Orthogonalization in ensemble is backward compatible
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.alpha_ensemble import (
    _apply_decay_health,
    combine_alphas,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_df(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Create synthetic OHLCV + alpha DataFrame for testing."""
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    high = close + rng.uniform(0.2, 1.0, n)
    low = close - rng.uniform(0.2, 1.0, n)
    opn = close + rng.randn(n) * 0.1
    vol = rng.uniform(1000, 5000, n)

    df = pd.DataFrame({
        "open": opn, "high": high, "low": low, "close": close, "volume": vol,
    })

    # Generate synthetic alpha signals with varying correlation to returns
    returns = df["close"].pct_change()
    for i, name in enumerate(["alpha_001", "alpha_012", "alpha_053", "alpha_036"]):
        noise = rng.randn(n) * 0.5
        signal = returns.shift(1) * (0.3 + i * 0.1) + noise
        df[name] = signal

    return df


def _alpha_cols() -> list[str]:
    return ["alpha_001", "alpha_012", "alpha_053", "alpha_036"]


# ---------------------------------------------------------------------------
# _apply_decay_health tests
# ---------------------------------------------------------------------------

class TestApplyDecayHealth:
    """Unit tests for _apply_decay_health function."""

    def test_no_health_no_change(self):
        weights = {"a": 0.4, "b": 0.6}
        result = _apply_decay_health(weights, {})
        assert result == weights

    def test_none_health_no_change(self):
        weights = {"a": 0.4, "b": 0.6}
        result = _apply_decay_health(weights, None)
        assert result == weights

    def test_healthy_alpha_unchanged(self):
        weights = {"a": 0.5, "b": 0.5}
        health = {"a": {"status": "HEALTHY", "ic_current": 0.05}}
        result = _apply_decay_health(weights, health)
        assert result["a"] == 0.5
        assert result["b"] == 0.5

    def test_dead_alpha_zeroed(self):
        weights = {"a": 0.5, "b": 0.5}
        health = {"a": {"status": "DEAD", "ic_current": -0.01}}
        result = _apply_decay_health(weights, health)
        assert result["a"] == 0.0
        assert result["b"] > 0.0

    def test_reversed_alpha_zeroed(self):
        weights = {"a": 0.4, "b": 0.6}
        health = {"b": {"status": "REVERSED", "ic_current": -0.03}}
        result = _apply_decay_health(weights, health)
        assert result["b"] == 0.0
        assert result["a"] > 0.0

    def test_degraded_alpha_halved(self):
        weights = {"a": 0.4, "b": 0.6}
        health = {"a": {"status": "DEGRADED", "ic_current": 0.01}}
        result = _apply_decay_health(weights, health)
        # After halving a→0.2 and keeping b→0.6, total=0.8, so renormalized:
        # a = 0.2/0.8 = 0.25, b = 0.6/0.8 = 0.75
        assert result["a"] == pytest.approx(0.25, abs=1e-6)
        assert result["b"] == pytest.approx(0.75, abs=1e-6)

    def test_renormalization(self):
        """After zeroing one alpha, remaining should be renormalized."""
        weights = {"a": 0.5, "b": 0.5}
        health = {"a": {"status": "DEAD", "ic_current": -0.02}}
        result = _apply_decay_health(weights, health)
        assert result["a"] == 0.0
        assert result["b"] == pytest.approx(1.0, abs=1e-6)

    def test_all_dead_equal_fallback(self):
        weights = {"a": 0.5, "b": 0.5}
        health = {
            "a": {"status": "DEAD", "ic_current": -0.02},
            "b": {"status": "REVERSED", "ic_current": -0.03},
        }
        result = _apply_decay_health(weights, health)
        # All zeroed → equal weight fallback
        assert result["a"] == pytest.approx(0.5, abs=1e-6)
        assert result["b"] == pytest.approx(0.5, abs=1e-6)

    def test_unknown_health_preserved(self):
        """Alphas not in health dict keep their original weights."""
        weights = {"a": 0.3, "b": 0.7}
        health = {"a": {"status": "DEAD", "ic_current": -0.02}}
        result = _apply_decay_health(weights, health)
        assert result["a"] == 0.0
        assert result["b"] == pytest.approx(1.0, abs=1e-6)

    def test_mixed_statuses(self):
        weights = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
        health = {
            "a": {"status": "DEAD", "ic_current": -0.02},
            "b": {"status": "DEGRADED", "ic_current": 0.01},
            "c": {"status": "HEALTHY", "ic_current": 0.05},
            "d": {"status": "REVERSED", "ic_current": -0.03},
        }
        result = _apply_decay_health(weights, health)
        assert result["a"] == 0.0
        assert result["d"] == 0.0
        assert result["c"] > result["b"]  # healthy > degraded


# ---------------------------------------------------------------------------
# Integration tests: combine_alphas with decay_health
# ---------------------------------------------------------------------------

class TestCombineAlphasDecayHealth:
    """Integration tests: combine_alphas with decay_health parameter."""

    def test_dead_alpha_removed_from_composite(self):
        df = _make_df()
        cols = _alpha_cols()
        health = {
            "alpha_001": {"status": "DEAD", "ic_current": -0.02},
        }
        result = combine_alphas(df, cols, decay_health=health)
        assert result.alpha_weights["alpha_001"] == 0.0
        assert result.alpha_weights.get("alpha_012", 0) > 0.0

    def test_all_healthy_unchanged(self):
        df = _make_df()
        cols = _alpha_cols()
        health = {c: {"status": "HEALTHY", "ic_current": 0.05} for c in cols}
        result_with = combine_alphas(df, cols, decay_health=health)
        result_without = combine_alphas(df, cols, decay_health=None)
        # Weights should be identical
        for c in cols:
            assert result_with.alpha_weights[c] == pytest.approx(
                result_without.alpha_weights[c], abs=1e-6
            )

    def test_degraded_alpha_weight_reduced(self):
        df = _make_df()
        cols = _alpha_cols()
        health = {
            "alpha_012": {"status": "DEGRADED", "ic_current": 0.01},
        }
        result_with = combine_alphas(df, cols, decay_health=health)
        result_without = combine_alphas(df, cols, decay_health=None)
        # Degraded alpha weight should be lower
        assert result_with.alpha_weights["alpha_012"] < result_without.alpha_weights["alpha_012"]

    def test_composite_shape_unchanged(self):
        """Composite signal length should match input DataFrame length."""
        df = _make_df(150)
        cols = _alpha_cols()
        health = {"alpha_001": {"status": "DEAD", "ic_current": -0.01}}
        result = combine_alphas(df, cols, decay_health=health)
        assert len(result.composite) == len(df)
        assert len(result.composite_raw) == len(df)

    def test_orthogonalize_with_decay_health(self):
        """Both orthogonalization and decay_health can be used together."""
        df = _make_df()
        cols = _alpha_cols()
        health = {"alpha_001": {"status": "DEAD", "ic_current": -0.01}}
        result = combine_alphas(
            df, cols,
            orthogonalize=True,
            decay_health=health,
        )
        assert result.alpha_weights["alpha_001"] == 0.0
        assert len(result.composite) == len(df)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestDecayEnsembleEdgeCases:
    def test_empty_decay_health(self):
        df = _make_df()
        cols = _alpha_cols()
        r1 = combine_alphas(df, cols, decay_health={})
        r2 = combine_alphas(df, cols, decay_health=None)
        for c in cols:
            assert r1.alpha_weights[c] == pytest.approx(r2.alpha_weights[c], abs=1e-6)

    def test_nonexistent_alpha_in_health(self):
        """Health dict references alpha not in ensemble — should be ignored."""
        df = _make_df()
        cols = _alpha_cols()
        health = {"nonexistent_alpha": {"status": "DEAD", "ic_current": -0.05}}
        result = combine_alphas(df, cols, decay_health=health)
        # Original weights should be preserved (nonexistent alpha ignored)
        assert all(w > 0 for w in result.alpha_weights.values())

    def test_single_alpha_survives_decay(self):
        df = _make_df()
        cols = _alpha_cols()
        health = {
            "alpha_001": {"status": "DEAD", "ic_current": -0.02},
            "alpha_012": {"status": "DEAD", "ic_current": -0.02},
            "alpha_053": {"status": "REVERSED", "ic_current": -0.03},
        }
        result = combine_alphas(df, cols, decay_health=health)
        # alpha_036 is the only surviving alpha
        assert result.alpha_weights["alpha_036"] == pytest.approx(1.0, abs=1e-6)
