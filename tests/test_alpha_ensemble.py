"""Tests for the Alpha Combination Engine."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alpha_ensemble import (
    AlphaQuality,
    EnsembleResult,
    combine_alphas,
    compute_alpha_quality,
    compute_ic_weights,
    expanding_ic,
    format_quality_report,
    interpret_composite,
    rank_normalize,
    rolling_ic,
    zscore_normalize,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_data() -> pd.DataFrame:
    """Generate 300 bars of OHLCV + synthetic alpha columns."""
    np.random.seed(42)
    n = 300
    close_vals = 1.1000 + np.cumsum(np.random.randn(n) * 0.0010)
    close = pd.Series(close_vals)
    # Create synthetic alphas with known correlation to forward returns
    fwd_ret = close.pct_change().shift(-1)
    noise = np.random.randn(n) * 0.001

    df = pd.DataFrame({
        "open": close_vals + np.random.randn(n) * 0.0003,
        "high": close_vals + np.abs(np.random.randn(n) * 0.0005),
        "low": close_vals - np.abs(np.random.randn(n) * 0.0005),
        "close": close_vals,
        "volume": np.random.randint(1000, 50000, size=n).astype(float),
        # Alpha 1: positively correlated with forward returns (good alpha)
        "alpha_good": fwd_ret.fillna(0).values * 100 + noise,
        # Alpha 2: negatively correlated (contrarian alpha)
        "alpha_contra": -fwd_ret.fillna(0).values * 50 + noise,
        # Alpha 3: pure noise (no predictive power)
        "alpha_noise": np.random.randn(n) * 0.001,
    })
    return df


@pytest.fixture
def minimal_data() -> pd.DataFrame:
    """Minimal dataset (80 bars) for edge case testing."""
    np.random.seed(99)
    n = 80
    close = 1.1000 + np.cumsum(np.random.randn(n) * 0.0010)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.0003,
        "high": close + np.abs(np.random.randn(n) * 0.0005),
        "low": close - np.abs(np.random.randn(n) * 0.0005),
        "close": close,
        "volume": np.random.randint(1000, 50000, size=n).astype(float),
        "alpha_a": np.random.randn(n) * 0.001,
        "alpha_b": np.random.randn(n) * 0.001,
    })


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------

class TestNormalization:
    def test_zscore_has_zero_mean(self, sample_data):
        result = zscore_normalize(sample_data["alpha_good"])
        # After enough data, mean should be near zero
        valid = result.iloc[50:]
        assert abs(valid.mean()) < 0.2

    def test_zscore_has_unit_std(self, sample_data):
        result = zscore_normalize(sample_data["alpha_good"])
        valid = result.iloc[50:]
        assert abs(valid.std() - 1.0) < 0.2

    def test_zscore_rolling(self, sample_data):
        result = zscore_normalize(sample_data["alpha_good"], window=60)
        assert len(result) == len(sample_data)
        # Should be bounded (no extreme outliers from full-history contamination)
        assert result.abs().max() < 10

    def test_rank_normalize_range(self, sample_data):
        result = rank_normalize(sample_data["alpha_good"], window=60)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 1).all()

    def test_zscore_handles_constant_series(self):
        s = pd.Series([1.0] * 100)
        result = zscore_normalize(s)
        # Constant series → all zeros (std = 0 → fillna)
        assert (result == 0.0).all()


# ---------------------------------------------------------------------------
# IC calculation tests
# ---------------------------------------------------------------------------

class TestIC:
    def test_rolling_ic_length(self, sample_data):
        ic = rolling_ic(sample_data["alpha_good"], sample_data["close"].pct_change(), window=60)
        assert len(ic) == len(sample_data)

    def test_rolling_ic_range(self, sample_data):
        ic = rolling_ic(sample_data["alpha_good"], sample_data["close"].pct_change(), window=60)
        valid = ic.dropna()
        assert (valid >= -1.01).all()
        assert (valid <= 1.01).all()

    def test_expanding_ic_no_lookahead(self, sample_data):
        """Expanding IC should not use future data."""
        ic = expanding_ic(sample_data["alpha_good"], sample_data["close"].pct_change())
        # First 40 bars should be NaN
        assert ic.iloc[:39].isna().all()
        assert ic.iloc[40:].notna().any()

    def test_good_alpha_has_positive_ic(self, sample_data):
        """Alpha correlated with forward returns should have positive IC."""
        fwd = sample_data["close"].pct_change().shift(-1)
        ic = rolling_ic(sample_data["alpha_good"], fwd, window=60)
        valid = ic.dropna()
        assert valid.mean() > 0, f"Expected positive IC, got {valid.mean()}"

    def test_noise_alpha_has_near_zero_ic(self, sample_data):
        """Pure noise alpha should have IC near zero."""
        fwd = sample_data["close"].pct_change().shift(-1)
        ic = rolling_ic(sample_data["alpha_noise"], fwd, window=60)
        valid = ic.dropna()
        assert abs(valid.mean()) < 0.15, f"Noise IC too high: {valid.mean()}"


# ---------------------------------------------------------------------------
# Alpha quality tests
# ---------------------------------------------------------------------------

class TestAlphaQuality:
    def test_quality_fields(self, sample_data):
        fwd = sample_data["close"].pct_change().shift(-1)
        q = compute_alpha_quality(
            sample_data["alpha_good"], fwd, name="alpha_good", ic_window=60
        )
        assert isinstance(q, AlphaQuality)
        assert q.name == "alpha_good"
        assert q.ic_mean != 0
        assert q.ic_std > 0

    def test_good_alpha_higher_ir_than_noise(self, sample_data):
        fwd = sample_data["close"].pct_change().shift(-1)
        q_good = compute_alpha_quality(sample_data["alpha_good"], fwd, "good", 60)
        q_noise = compute_alpha_quality(sample_data["alpha_noise"], fwd, "noise", 60)
        assert abs(q_good.ic_ir) > abs(q_noise.ic_ir)

    def test_turnover_positive(self, sample_data):
        fwd = sample_data["close"].pct_change().shift(-1)
        q = compute_alpha_quality(sample_data["alpha_good"], fwd, "good", 60)
        assert q.turnover >= 0


# ---------------------------------------------------------------------------
# Weight calculation tests
# ---------------------------------------------------------------------------

class TestWeights:
    def test_weights_sum_to_one(self):
        qualities = {
            "a": AlphaQuality(name="a", ic_ir=1.0),
            "b": AlphaQuality(name="b", ic_ir=0.5),
            "c": AlphaQuality(name="c", ic_ir=0.2),
        }
        weights = compute_ic_weights(qualities)
        assert abs(sum(weights.values()) - 1.0) < 1e-10

    def test_higher_ir_gets_higher_weight(self):
        qualities = {
            "a": AlphaQuality(name="a", ic_ir=2.0),
            "b": AlphaQuality(name="b", ic_ir=0.5),
        }
        weights = compute_ic_weights(qualities)
        assert weights["a"] > weights["b"]

    def test_negative_ir_gets_lowest_weight(self):
        qualities = {
            "a": AlphaQuality(name="a", ic_ir=-1.0),
            "b": AlphaQuality(name="b", ic_ir=0.5),
        }
        weights = compute_ic_weights(qualities)
        # With shrinkage=0.30, negative IR gets zero raw but still receives
        # shrinkage floor (equal_weight * 0.30). Key: b gets much more weight.
        assert weights["a"] < weights["b"]

    def test_all_negative_ir_equal_fallback(self):
        qualities = {
            "a": AlphaQuality(name="a", ic_ir=-1.0),
            "b": AlphaQuality(name="b", ic_ir=-0.5),
        }
        weights = compute_ic_weights(qualities)
        # All negative → fallback to equal weight
        assert abs(weights["a"] - 0.5) < 1e-10
        assert abs(weights["b"] - 0.5) < 1e-10


# ---------------------------------------------------------------------------
# Ensemble combination tests
# ---------------------------------------------------------------------------

class TestEnsemble:
    def test_combine_returns_ensemble_result(self, sample_data):
        result = combine_alphas(
            sample_data,
            ["alpha_good", "alpha_contra", "alpha_noise"],
            ic_window=60,
        )
        assert isinstance(result, EnsembleResult)
        assert len(result.composite) == len(sample_data)

    def test_composite_is_zscored(self, sample_data):
        result = combine_alphas(
            sample_data, ["alpha_good", "alpha_contra", "alpha_noise"], ic_window=60
        )
        valid = result.composite.iloc[60:]
        assert abs(valid.mean()) < 0.2
        assert abs(valid.std() - 1.0) < 0.3

    def test_weights_sum_to_one(self, sample_data):
        result = combine_alphas(
            sample_data, ["alpha_good", "alpha_contra", "alpha_noise"], ic_window=60
        )
        assert abs(sum(result.alpha_weights.values()) - 1.0) < 1e-10

    def test_good_alpha_gets_weight(self, sample_data):
        result = combine_alphas(
            sample_data, ["alpha_good", "alpha_contra", "alpha_noise"], ic_window=60
        )
        # Good alpha should get some weight (it has signal)
        assert result.alpha_weights.get("alpha_good", 0) > 0

    def test_quality_metrics_computed(self, sample_data):
        result = combine_alphas(
            sample_data, ["alpha_good", "alpha_contra", "alpha_noise"], ic_window=60
        )
        for name in ["alpha_good", "alpha_contra", "alpha_noise"]:
            assert name in result.alpha_qualities
            q = result.alpha_qualities[name]
            assert isinstance(q, AlphaQuality)

    def test_equal_weighting(self, sample_data):
        result = combine_alphas(
            sample_data,
            ["alpha_good", "alpha_contra", "alpha_noise"],
            weighting="equal",
        )
        for w in result.alpha_weights.values():
            assert abs(w - 1.0 / 3) < 1e-10

    def test_single_alpha(self, sample_data):
        result = combine_alphas(sample_data, ["alpha_good"], ic_window=60)
        assert result.n_alphas_used >= 0
        assert len(result.composite) == len(sample_data)


# ---------------------------------------------------------------------------
# Interpretation tests
# ---------------------------------------------------------------------------

class TestInterpretation:
    def test_interpret_returns_expected_keys(self, sample_data):
        result = combine_alphas(
            sample_data, ["alpha_good", "alpha_noise"], ic_window=60
        )
        interp = interpret_composite(result, current_idx=-1)
        assert "signal" in interp
        assert "strength" in interp
        assert "confidence" in interp
        assert "contributing_alphas" in interp
        assert interp["signal"] in ("LONG", "SHORT", "NEUTRAL")

    def test_interpret_confidence_levels(self, sample_data):
        result = combine_alphas(
            sample_data, ["alpha_good", "alpha_noise"], ic_window=60
        )
        interp = interpret_composite(result, current_idx=-1)
        assert interp["confidence"] in ("HIGH", "MEDIUM", "LOW")

    def test_format_quality_report(self, sample_data):
        result = combine_alphas(
            sample_data, ["alpha_good", "alpha_contra", "alpha_noise"], ic_window=60
        )
        report = format_quality_report(result)
        assert "ALPHA ENSEMBLE QUALITY REPORT" in report
        assert "alpha_good" in report


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_minimal_data(self, minimal_data):
        """Should work with 80 bars (enough for IC window=60)."""
        result = combine_alphas(
            minimal_data, ["alpha_a", "alpha_b"], ic_window=20
        )
        assert len(result.composite) == 80

    def test_no_valid_alphas_raises(self):
        df = pd.DataFrame({"close": [1.0] * 10, "open": [1.0] * 10})
        with pytest.raises(ValueError, match="No valid alpha columns"):
            combine_alphas(df, ["nonexistent_alpha"])

    def test_original_df_not_mutated(self, sample_data):
        original = sample_data.copy()
        combine_alphas(sample_data, ["alpha_good", "alpha_noise"], ic_window=60)
        pd.testing.assert_frame_equal(sample_data, original)
