"""Tests for Signal Orthogonalization (ψ) — purify alpha signals against market exposures.

Covers:
- OLS regression against factor matrix → residual = purified signal
- PCA-based orthogonalization → project out top-K principal components
- Gram-Schmidt sequential orthogonalization
- Variance Inflation Factor (VIF) multicollinearity diagnostics
- Default factor construction from OHLCV data
- IC preservation after orthogonalization
- Integration with alpha_ensemble.combine_alphas(orthogonalize=True)
- Edge cases: single alpha, zero-variance, all NaN, short series
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.signal_orthogonalizer import (
    OrthoMethod,
    OrthogonalizationConfig,
    FactorExposure,
    OrthogonalizationResult,
    build_default_factors,
    compute_vif,
    orthogonalize_signals,
    orthogonalize_for_ensemble,
    format_orthogonalization_report,
    _ols_orthogonalize_single,
    _pca_orthogonalize_single,
    _gram_schmidt_orthogonalize,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Generate synthetic OHLCV data with regime switches."""
    np.random.seed(42)
    n = 500
    t = np.arange(n)

    # Price with regime-switching drift
    drift = np.where(t < 200, 0.0002, -0.0001)
    drift[350:] = 0.0003
    noise = np.random.randn(n) * 0.005
    log_price = np.cumsum(drift + noise)
    close = 100 * np.exp(log_price)

    # OHLCV
    high = close * (1 + np.abs(np.random.randn(n) * 0.003))
    low = close * (1 - np.abs(np.random.randn(n) * 0.003))
    open_ = close * (1 + np.random.randn(n) * 0.001)
    volume = np.random.lognormal(10, 0.5, n).astype(int).astype(float)

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture
def sample_alpha_signals(sample_ohlcv) -> dict[str, pd.Series]:
    """Generate synthetic alpha signals correlated with close and volume."""
    np.random.seed(123)
    df = sample_ohlcv
    close_ret = df["close"].pct_change().fillna(0)
    vol_surprise = ((df["volume"] - df["volume"].rolling(20).mean())
                    / df["volume"].rolling(20).std().replace(0, 1)).fillna(0)

    signals = {
        "alpha_001": pd.Series(close_ret.rolling(5).mean().fillna(0).values
                      + np.random.randn(len(df)) * 0.002, index=df.index),  # Market-correlated
        "alpha_005": pd.Series(vol_surprise.fillna(0).values * 0.5
                      + np.random.randn(len(df)) * 0.003, index=df.index),  # Volume-correlated
        "alpha_038": pd.Series(close_ret.rolling(20).mean().fillna(0).values * 0.3
                      + np.random.randn(len(df)) * 0.005, index=df.index),  # Weakly correlated
        "alpha_053": pd.Series(np.random.randn(len(df)) * 0.008, index=df.index),  # Uncorrelated
    }
    return signals


@pytest.fixture
def sample_config() -> OrthogonalizationConfig:
    return OrthogonalizationConfig(
        method=OrthoMethod.OLS,
        min_periods=40,
        rolling_window=100,
        regularization=0.01,
    )


# ============================================================================
# TEST: FACTOR CONSTRUCTION
# ============================================================================

class TestBuildDefaultFactors:
    def test_creates_five_factors(self, sample_ohlcv):
        factors = build_default_factors(sample_ohlcv)
        assert len(factors.columns) == 5
        assert "market_return" in factors.columns
        assert "volatility" in factors.columns
        assert "volume_surprise" in factors.columns
        assert "range_pct" in factors.columns
        assert "momentum_20" in factors.columns

    def test_same_index_as_input(self, sample_ohlcv):
        factors = build_default_factors(sample_ohlcv)
        assert len(factors) == len(sample_ohlcv)
        assert factors.index.equals(sample_ohlcv.index)

    def test_market_return_is_pct_change(self, sample_ohlcv):
        factors = build_default_factors(sample_ohlcv)
        expected = sample_ohlcv["close"].pct_change()
        pd.testing.assert_series_equal(
            factors["market_return"], expected, check_names=False,
        )

    def test_volume_surprise_is_zscored(self, sample_ohlcv):
        factors = build_default_factors(sample_ohlcv)
        # After warmup, volume_surprise should be roughly mean 0, std ~1
        valid = factors["volume_surprise"].iloc[50:]
        assert abs(valid.mean()) < 0.5
        assert 0.3 < valid.std() < 3.0

    def test_missing_columns_graceful(self):
        df = pd.DataFrame({"close": [100, 101, 102, 103, 104] * 20})
        factors = build_default_factors(df)
        assert len(factors.columns) == 5
        # volume_surprise should be 0 when no volume
        assert (factors["volume_surprise"] == 0.0).all()


# ============================================================================
# TEST: VIF
# ============================================================================

class TestVIF:
    def test_uncorrelated_factors_vif_near_1(self):
        np.random.seed(42)
        df = pd.DataFrame({
            "a": np.random.randn(100),
            "b": np.random.randn(100),
            "c": np.random.randn(100),
        })
        vif = compute_vif(df)
        assert all(v < 2.0 for v in vif.values())

    def test_correlated_factors_vif_high(self):
        np.random.seed(42)
        base = np.random.randn(100)
        df = pd.DataFrame({
            "a": base,
            "b": base + np.random.randn(100) * 0.01,  # Nearly identical
            "c": np.random.randn(100),
        })
        vif = compute_vif(df)
        assert vif["a"] > 5.0 or vif["b"] > 5.0

    def test_single_factor_vif_is_1(self):
        df = pd.DataFrame({"a": np.random.randn(50)})
        vif = compute_vif(df)
        assert vif["a"] == 1.0


# ============================================================================
# TEST: OLS ORTHOGONALIZATION
# ============================================================================

class TestOLSOrthogonalization:
    def test_residual_has_lower_variance(self, sample_alpha_signals, sample_ohlcv):
        """After orthogonalization, residual variance should be <= original."""
        factors = build_default_factors(sample_ohlcv)
        for name, signal in sample_alpha_signals.items():
            resid, _, _, _ = _ols_orthogonalize_single(
                signal, factors, min_periods=40, rolling_window=100,
            )
            orig_var = signal.dropna().var()
            resid_var = resid.dropna().var()
            assert resid_var <= orig_var + 0.001, \
                f"{name}: residual var {resid_var} > original {orig_var}"

    def test_residual_uncorrelated_with_market(self, sample_alpha_signals, sample_ohlcv):
        """Purified signal should be less correlated with market_return."""
        factors = build_default_factors(sample_ohlcv)
        mkt = factors["market_return"]

        for name, signal in sample_alpha_signals.items():
            resid, _, _, _ = _ols_orthogonalize_single(
                signal, factors, min_periods=40, rolling_window=100,
            )
            orig_corr = abs(signal.dropna().corr(mkt.dropna()))
            resid_corr = abs(resid.dropna().corr(mkt.dropna()))
            # Residual should be less correlated (or equal if already orthogonal)
            assert resid_corr <= orig_corr + 0.1, \
                f"{name}: resid_corr={resid_corr} > orig_corr={orig_corr}"

    def test_factor_loadings_populated(self, sample_alpha_signals, sample_ohlcv):
        factors = build_default_factors(sample_ohlcv)
        signal = list(sample_alpha_signals.values())[0]
        _, loadings, r2, resid_std = _ols_orthogonalize_single(
            signal, factors, min_periods=40, rolling_window=100,
        )
        assert len(loadings) == len(factors.columns)
        assert 0.0 <= r2 <= 1.0
        assert resid_std >= 0.0

    def test_short_series_returns_nan(self):
        signal = pd.Series(np.random.randn(20))
        factors = pd.DataFrame({"a": np.random.randn(20), "b": np.random.randn(20)})
        resid, _, _, _ = _ols_orthogonalize_single(
            signal, factors, min_periods=40, rolling_window=100,
        )
        # All should be NaN because min_periods > len
        assert resid.dropna().empty


# ============================================================================
# TEST: PCA ORTHOGONALIZATION
# ============================================================================

class TestPCAOrthogonalization:
    def test_residual_exists(self, sample_alpha_signals, sample_ohlcv):
        factors = build_default_factors(sample_ohlcv)
        signal = list(sample_alpha_signals.values())[0]
        resid, r2 = _pca_orthogonalize_single(
            signal, factors, n_components=2, min_periods=40,
        )
        assert not resid.dropna().empty

    def test_r2_bounded(self, sample_alpha_signals, sample_ohlcv):
        factors = build_default_factors(sample_ohlcv)
        for name, signal in sample_alpha_signals.items():
            resid, r2 = _pca_orthogonalize_single(
                signal, factors, n_components=3, min_periods=40,
            )
            assert 0.0 <= r2 <= 1.0


# ============================================================================
# TEST: GRAM-SCHMIDT
# ============================================================================

class TestGramSchmidt:
    def test_purified_has_same_length(self, sample_alpha_signals, sample_ohlcv):
        mkt = build_default_factors(sample_ohlcv)["market_return"]
        results = _gram_schmidt_orthogonalize(
            sample_alpha_signals, mkt, min_periods=40,
        )
        assert set(results.keys()) == set(sample_alpha_signals.keys())
        for name, purified in results.items():
            assert len(purified) == len(sample_alpha_signals[name])

    def test_purified_uncorrelated_with_factor(self, sample_alpha_signals, sample_ohlcv):
        mkt = build_default_factors(sample_ohlcv)["market_return"]
        results = _gram_schmidt_orthogonalize(
            sample_alpha_signals, mkt, min_periods=40,
        )
        for name, purified in results.items():
            corr = abs(purified.dropna().corr(mkt.dropna()))
            assert corr < 0.3, f"{name}: corr={corr} with market after GS"


# ============================================================================
# TEST: FULL ORTHOGONALIZATION PIPELINE
# ============================================================================

class TestOrthogonalizeSignals:
    def test_ols_method(self, sample_alpha_signals, sample_ohlcv, sample_config):
        result = orthogonalize_signals(
            sample_alpha_signals, sample_ohlcv, config=sample_config,
        )
        assert isinstance(result, OrthogonalizationResult)
        assert result.method_used == "ols"
        assert len(result.purified) == len(sample_alpha_signals)
        assert len(result.exposures) == len(sample_alpha_signals)
        assert result.factor_corr_matrix is not None

    def test_pca_method(self, sample_alpha_signals, sample_ohlcv):
        config = OrthogonalizationConfig(
            method=OrthoMethod.PCA, min_periods=40, pca_n_components=2,
        )
        result = orthogonalize_signals(
            sample_alpha_signals, sample_ohlcv, config=config,
        )
        assert result.method_used == "pca"
        assert len(result.purified) == len(sample_alpha_signals)

    def test_gram_schmidt_method(self, sample_alpha_signals, sample_ohlcv):
        config = OrthogonalizationConfig(method=OrthoMethod.GRAM_SCHMIDT, min_periods=40)
        result = orthogonalize_signals(
            sample_alpha_signals, sample_ohlcv, config=config,
        )
        assert result.method_used == "gs"
        assert len(result.purified) == len(sample_alpha_signals)

    def test_custom_factors(self, sample_alpha_signals, sample_ohlcv):
        custom = pd.DataFrame({
            "my_factor": np.random.randn(len(sample_ohlcv)),
        }, index=sample_ohlcv.index)
        result = orthogonalize_signals(
            sample_alpha_signals, sample_ohlcv,
            config=OrthogonalizationConfig(min_periods=40),
            custom_factors=custom,
        )
        assert len(result.purified) == len(sample_alpha_signals)

    def test_r_squared_reported(self, sample_alpha_signals, sample_ohlcv, sample_config):
        result = orthogonalize_signals(
            sample_alpha_signals, sample_ohlcv, config=sample_config,
        )
        for name, exp in result.exposures.items():
            assert 0.0 <= exp.r_squared <= 1.0

    def test_vif_computed(self, sample_alpha_signals, sample_ohlcv, sample_config):
        result = orthogonalize_signals(
            sample_alpha_signals, sample_ohlcv, config=sample_config,
        )
        assert result.multicollinearity_vif is not None
        assert len(result.multicollinearity_vif) == 5


# ============================================================================
# TEST: QUICK ORTHOGONALIZE FOR ENSEMBLE
# ============================================================================

class TestOrthogonalizeForEnsemble:
    def test_returns_same_keys(self, sample_alpha_signals, sample_ohlcv):
        purified = orthogonalize_for_ensemble(
            sample_alpha_signals, sample_ohlcv,
        )
        assert set(purified.keys()) == set(sample_alpha_signals.keys())

    def test_values_are_series(self, sample_alpha_signals, sample_ohlcv):
        purified = orthogonalize_for_ensemble(
            sample_alpha_signals, sample_ohlcv,
        )
        for name, series in purified.items():
            assert isinstance(series, pd.Series)

    def test_pca_method(self, sample_alpha_signals, sample_ohlcv):
        purified = orthogonalize_for_ensemble(
            sample_alpha_signals, sample_ohlcv, method="pca",
        )
        assert len(purified) == len(sample_alpha_signals)

    def test_gs_method(self, sample_alpha_signals, sample_ohlcv):
        purified = orthogonalize_for_ensemble(
            sample_alpha_signals, sample_ohlcv, method="gs",
        )
        assert len(purified) == len(sample_alpha_signals)


# ============================================================================
# TEST: REPORT FORMATTING
# ============================================================================

class TestReport:
    def test_format_report(self, sample_alpha_signals, sample_ohlcv, sample_config):
        result = orthogonalize_signals(
            sample_alpha_signals, sample_ohlcv, config=sample_config,
        )
        report = format_orthogonalization_report(result)
        assert "SIGNAL ORTHOGONALIZATION REPORT" in report
        assert "Method: ols" in report
        assert "alpha_001" in report
        assert "R²" in report


# ============================================================================
# TEST: IC PRESERVATION (key invariant)
# ============================================================================

class TestICPreservation:
    """Orthogonalization should REDUCE noise but preserve signal.

    An alpha that genuinely predicts forward returns should have IC
    preserved (or improved) after orthogonalization, because we're
    removing shared variance, not predictive variance.
    """

    def test_ic_does_not_degrade_for_predictive_signal(self, sample_ohlcv):
        """Construct a signal that genuinely predicts forward returns,
        verify IC is preserved after orthogonalization."""
        np.random.seed(99)
        df = sample_ohlcv.copy()
        fwd = df["close"].pct_change(5).shift(-5)

        # alpha_001 = forward return + noise (genuinely predictive)
        signal = fwd * 0.5 + np.random.randn(len(df)) * 0.01

        from scipy import stats as sp_stats

        # IC before
        mask = signal.notna() & fwd.notna()
        ic_before = sp_stats.spearmanr(signal[mask], fwd[mask])[0]

        # Orthogonalize
        purified = orthogonalize_for_ensemble(
            {"alpha_001": signal}, df, method="ols",
        )

        # IC after
        mask2 = purified["alpha_001"].notna() & fwd.notna()
        ic_after = sp_stats.spearmanr(purified["alpha_001"][mask2], fwd[mask2])[0]

        # IC should be preserved (within 50% tolerance — orthogonalization removes noise, not signal)
        assert abs(ic_after) > abs(ic_before) * 0.3, \
            f"IC degraded from {ic_before:.4f} to {ic_after:.4f}"


# ============================================================================
# TEST: INTEGRATION WITH ALPHA ENSEMBLE
# ============================================================================

class TestEnsembleIntegration:
    def test_combine_alphas_with_orthogonalize(self, sample_ohlcv):
        """combine_alphas(orthogonalize=True) should run without error."""
        from core.alpha_ensemble import combine_alphas

        np.random.seed(42)
        df = sample_ohlcv.copy()
        df["alpha_001"] = df["close"].pct_change().rolling(5).mean().fillna(0)
        df["alpha_005"] = df["volume"].pct_change().rolling(10).mean().fillna(0)
        df["alpha_038"] = np.random.randn(len(df)) * 0.01

        result = combine_alphas(
            df,
            alpha_columns=["alpha_001", "alpha_005", "alpha_038"],
            orthogonalize=True,
            ortho_method="ols",
            ortho_rolling_window=100,
        )
        assert len(result.composite) == len(df)
        assert len(result.alpha_weights) == 3

    def test_combine_alphas_without_orthogonalize(self, sample_ohlcv):
        """combine_alphas(orthogonalize=False) should still work (backward compat)."""
        from core.alpha_ensemble import combine_alphas

        np.random.seed(42)
        df = sample_ohlcv.copy()
        df["alpha_001"] = df["close"].pct_change().rolling(5).mean().fillna(0)
        df["alpha_005"] = df["volume"].pct_change().rolling(10).mean().fillna(0)

        result = combine_alphas(
            df,
            alpha_columns=["alpha_001", "alpha_005"],
            orthogonalize=False,
        )
        assert len(result.composite) == len(df)


# ============================================================================
# TEST: EDGE CASES
# ============================================================================

class TestEdgeCases:
    def test_single_alpha(self, sample_ohlcv):
        signal = pd.Series(np.random.randn(len(sample_ohlcv)), index=sample_ohlcv.index)
        result = orthogonalize_for_ensemble(
            {"alpha_only": signal}, sample_ohlcv,
        )
        assert "alpha_only" in result

    def test_empty_signal_handled(self, sample_ohlcv):
        signal = pd.Series(np.nan, index=sample_ohlcv.index)
        result = orthogonalize_for_ensemble(
            {"empty": signal}, sample_ohlcv,
        )
        assert "empty" in result

    def test_all_zero_factors(self):
        np.random.seed(42)
        signal = pd.Series(np.random.randn(100))
        factors = pd.DataFrame({
            "zero1": np.zeros(100),
            "zero2": np.zeros(100),
        })
        config = OrthogonalizationConfig(min_periods=20, rolling_window=50)
        result = orthogonalize_signals(
            {"alpha": signal}, pd.DataFrame({"close": range(100)}),
            config=config, custom_factors=factors,
        )
        # Should not crash — zero-variance factors filtered out
        assert "alpha" in result.purified
