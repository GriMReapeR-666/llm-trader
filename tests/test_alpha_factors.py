"""Tests for the Alpha101 factor library."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.alpha_factors import (
    _ALPHA_REGISTRY,
    _vwap,
    alpha_001,
    alpha_005,
    alpha_009,
    alpha_012,
    alpha_014,
    alpha_017,
    alpha_019,
    alpha_022,
    alpha_023,
    alpha_025,
    alpha_028,
    alpha_030,
    alpha_034,
    alpha_035,
    alpha_036,
    alpha_037,
    alpha_038,
    alpha_041,
    alpha_043,
    alpha_053,
    alpha_055,
    alpha_060,
    alpha_101,
    compute_alpha_factors,
    decay_linear,
    delay,
    delta,
    log_safe,
    rank,
    scale,
    signed_power,
    ts_argmax,
    ts_argmin,
    ts_max,
    ts_min,
    ts_rank,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Generate 300 bars of realistic OHLCV data.

    300 bars ensures all alphas (including alpha_019 which needs 250-bar
    rolling) have enough history to produce finite values.
    """
    np.random.seed(42)
    n = 300
    close = 1.1000 + np.cumsum(np.random.randn(n) * 0.0010)
    high = close + np.abs(np.random.randn(n) * 0.0005)
    low = close - np.abs(np.random.randn(n) * 0.0005)
    opn = close + np.random.randn(n) * 0.0003
    volume = np.random.randint(1000, 50000, size=n).astype(float)
    return pd.DataFrame({
        "open": opn,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture
def short_ohlcv() -> pd.DataFrame:
    """Very short dataset (20 bars) to test graceful NaN handling."""
    np.random.seed(99)
    n = 20
    close = 1.1000 + np.cumsum(np.random.randn(n) * 0.0010)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.0003,
        "high": close + np.abs(np.random.randn(n) * 0.0005),
        "low": close - np.abs(np.random.randn(n) * 0.0005),
        "close": close,
        "volume": np.random.randint(1000, 50000, size=n).astype(float),
    })


# ---------------------------------------------------------------------------
# Primitive tests
# ---------------------------------------------------------------------------

class TestPrimitives:
    def test_delta(self, sample_ohlcv):
        result = delta(sample_ohlcv["close"], 1)
        expected = sample_ohlcv["close"].diff(1)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_delay(self, sample_ohlcv):
        result = delay(sample_ohlcv["close"], 5)
        expected = sample_ohlcv["close"].shift(5)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_rank_normalizes_0_1(self, sample_ohlcv):
        r = rank(sample_ohlcv["close"])
        assert r.min() > 0
        assert r.max() <= 1.0

    def test_scale_sums_to_k(self, sample_ohlcv):
        s = scale(sample_ohlcv["close"], k=1.0)
        assert abs(s.abs().sum() - 1.0) < 1e-10

    def test_decay_linear_length(self, sample_ohlcv):
        dl = decay_linear(sample_ohlcv["close"], 10)
        assert len(dl) == len(sample_ohlcv)
        assert dl.iloc[:9].isna().all()
        assert dl.iloc[9:].notna().all()

    def test_ts_rank_range(self, sample_ohlcv):
        r = ts_rank(sample_ohlcv["close"], 20)
        valid = r.dropna()
        assert (valid > 0).all()
        assert (valid <= 1).all()

    def test_ts_argmax(self, sample_ohlcv):
        r = ts_argmax(sample_ohlcv["close"], 10)
        valid = r.dropna()
        assert (valid >= 1).all()
        assert (valid <= 10).all()

    def test_signed_power(self, sample_ohlcv):
        s = pd.Series([-2.0, -1.0, 0.0, 1.0, 2.0])
        result = signed_power(s, 2.0)
        expected = pd.Series([-4.0, -1.0, 0.0, 1.0, 4.0])
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_log_safe_no_crash_on_negative(self, sample_ohlcv):
        result = log_safe(pd.Series([-1.0, 0.0, 1.0]))
        assert np.isfinite(result).all()


# ---------------------------------------------------------------------------
# Alpha factor tests
# ---------------------------------------------------------------------------

class TestAlphaFactors:
    def test_all_alphas_return_series(self, sample_ohlcv):
        """Every registered alpha must return a pd.Series of correct length."""
        for name, func in _ALPHA_REGISTRY.items():
            result = func(sample_ohlcv)
            assert isinstance(result, pd.Series), f"{name} did not return Series"
            assert len(result) == len(sample_ohlcv), f"{name} wrong length"

    def test_all_alphas_have_finite_values(self, sample_ohlcv):
        """At least 10% of values should be finite.

        Some alphas (e.g. ALPHA019) need 250+ bars of history, so with
        300-bar sample data only ~17% will be finite. That's expected.
        Persistence alphas (ALPHAPxx) require 480-3840 bars and are tested separately.
        """
        for name, func in _ALPHA_REGISTRY.items():
            if name.startswith("ALPHAP"):
                continue
            result = func(sample_ohlcv)
            finite_ratio = result.notna().sum() / len(result)
            assert finite_ratio >= 0.1, (
                f"{name} has only {finite_ratio:.0%} finite values"
            )

    def test_persistence_alphas_finite_with_adequate_history(self):
        """Persistence alphas require 480-3840 bars for multi-day horizons."""
        n = 4000
        close = 1.1000 + np.cumsum(np.random.randn(n) * 0.0010)
        df = pd.DataFrame({
            "close": close,
            "open": close,
            "high": close + 0.0005,
            "low": close - 0.0005,
            "volume": np.ones(n) * 1000.0,
        })
        for name, func in _ALPHA_REGISTRY.items():
            if name.startswith("ALPHAP"):
                result = func(df)
                assert result.notna().sum() > 0, f"{name} produced no finite values with {n} bars"

    def test_alpha_012_volume_price_divergence(self, sample_ohlcv):
        """Alpha#12: sign(delta(volume)) * (-1 * delta(close))."""
        a = alpha_012(sample_ohlcv)
        # Should be non-zero when both volume and price change
        assert a.notna().sum() > 50

    def test_alpha_041_geometric_midpoint(self, sample_ohlcv):
        """Alpha#41: sqrt(high*low) - vwap."""
        a = alpha_041(sample_ohlcv)
        vwap = _vwap(sample_ohlcv)
        expected = np.sqrt(sample_ohlcv["high"] * sample_ohlcv["low"]) - vwap
        pd.testing.assert_series_equal(a, expected, check_names=False)

    def test_alpha_101_composite(self, sample_ohlcv):
        """Alpha#101: ((close-open)/(high-low+0.001))*volume."""
        a = alpha_101(sample_ohlcv)
        rng = (sample_ohlcv["high"] - sample_ohlcv["low"]).replace(0, 0.001)
        expected = ((sample_ohlcv["close"] - sample_ohlcv["open"]) / rng) * sample_ohlcv["volume"]
        pd.testing.assert_series_equal(a, expected, check_names=False)

    def test_short_data_graceful(self, short_ohlcv):
        """Alphas should not crash on 20-bar data (some will be all NaN)."""
        for name, func in _ALPHA_REGISTRY.items():
            try:
                result = func(short_ohlcv)
                assert len(result) == 20
            except Exception as e:
                pytest.fail(f"{name} crashed on 20-bar data: {e}")

    def test_vwap_fallback(self, sample_ohlcv):
        """_vwap should compute typical-price VWAP when no vwap column."""
        v = _vwap(sample_ohlcv)
        assert len(v) == len(sample_ohlcv)
        assert v.notna().sum() > 50


# ---------------------------------------------------------------------------
# Integration: compute_alpha_factors
# ---------------------------------------------------------------------------

class TestComputeAlphaFactors:
    def test_compute_all(self, sample_ohlcv):
        """compute_alpha_factors with default requests adds all alpha columns."""
        result = compute_alpha_factors(sample_ohlcv)
        alpha_cols = [c for c in result.columns if c.startswith("alpha_")]
        assert len(alpha_cols) >= 15, f"Expected ≥15 alpha columns, got {len(alpha_cols)}"

    def test_compute_specific(self, sample_ohlcv):
        """Can request specific alphas."""
        result = compute_alpha_factors(sample_ohlcv, ["ALPHA012", "ALPHA101"])
        assert "alpha_012" in result.columns
        assert "alpha_101" in result.columns
        # Original columns preserved
        assert "close" in result.columns

    def test_invalid_alpha_raises(self, sample_ohlcv):
        with pytest.raises(ValueError, match="Unknown alpha factor"):
            compute_alpha_factors(sample_ohlcv, ["ALPHA999"])

    def test_original_df_unchanged(self, sample_ohlcv):
        """compute_alpha_factors must not mutate the input DataFrame."""
        original = sample_ohlcv.copy()
        compute_alpha_factors(sample_ohlcv, ["ALPHA012"])
        pd.testing.assert_frame_equal(sample_ohlcv, original)


# ---------------------------------------------------------------------------
# Integration with technical_indicators.compute_indicators
# ---------------------------------------------------------------------------

class TestIndicatorPipeline:
    def test_alpha_via_compute_indicators(self, sample_ohlcv):
        """compute_indicators should accept ALPHA requests."""
        from core.technical_indicators import compute_indicators
        result = compute_indicators(sample_ohlcv, ["RSI", "ALPHA012", "ALPHA101"])
        assert "rsi_14" in result.columns
        assert "alpha_012" in result.columns
        assert "alpha_101" in result.columns

    def test_alpha_batch_computed(self, sample_ohlcv):
        """Multiple ALPHA requests should all appear in output."""
        from core.technical_indicators import compute_indicators
        result = compute_indicators(sample_ohlcv, [
            "ALPHA001", "ALPHA005", "ALPHA009", "ALPHA012",
            "ALPHA014", "ALPHA017", "ALPHA019", "ALPHA022",
            "ALPHA023", "ALPHA025", "ALPHA036", "ALPHA037",
        ])
        for i in ["001", "005", "009", "012", "014", "017", "019", "022", "023", "025", "036", "037"]:
            assert f"alpha_{i}" in result.columns, f"alpha_{i} missing"
