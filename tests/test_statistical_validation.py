"""Tests for core/return_diagnostics.py, core/parameter_stability.py, core/overfit_detector.py"""
from __future__ import annotations

import math
import numpy as np
import pytest

from core.return_diagnostics import (
    ljung_box_test,
    compute_acf,
    compute_pacf,
    durbin_watson,
    runs_test,
    diagnose_returns,
    diagnose_by_regime,
    _chi_squared_cdf,
    _normal_cdf,
)
from core.parameter_stability import (
    generate_param_grid,
    generate_param_grid_random,
    evaluate_variant,
    compute_stability_score,
    generate_heatmap,
    run_parameter_stability,
    ParameterGrid,
    ParameterVariant,
)
from core.overfit_detector import (
    deflated_sharpe_test,
    minimum_backtest_length,
    measure_is_oos_divergence,
    detect_stepwise_fitting,
    cv_stability_test,
    detect_strategy_overlap,
    detect_overfitting,
)


# -----------------------------------------------------------------------
# Helper: generate synthetic returns
# -----------------------------------------------------------------------

def _white_noise(n=500, seed=42):
    rng = np.random.RandomState(seed)
    return rng.normal(0, 0.01, n)


def _autocorrelated_returns(n=500, rho=0.3, seed=42):
    """Generate AR(1) returns with lag-1 autocorrelation."""
    rng = np.random.RandomState(seed)
    returns = np.zeros(n)
    returns[0] = rng.normal(0, 0.01)
    for i in range(1, n):
        returns[i] = rho * returns[i - 1] + rng.normal(0, 0.01)
    return returns


def _volatility_clustered_returns(n=500, seed=42):
    """Generate returns with ARCH-like volatility clustering."""
    rng = np.random.RandomState(seed)
    returns = np.zeros(n)
    vol = 0.01
    for i in range(1, n):
        vol = 0.05 * returns[i - 1] ** 2 + 0.94 * vol  # GARCH(1,1)-like
        returns[i] = rng.normal(0, max(vol, 1e-6))
    return returns


# -----------------------------------------------------------------------
# Tests: return_diagnostics
# -----------------------------------------------------------------------

class TestLjungBox:
    def test_white_noise_is_random(self):
        """White noise should not be rejected by Ljung-Box."""
        data = _white_noise(500)
        result = ljung_box_test(data, max_lag=20)
        # White noise: p > 0.05 typically (allow rare false positive)
        assert result.is_random
        assert result.statistic >= 0

    def test_autocorrelated_not_random(self):
        """AR(1) with rho=0.3 should be detected as autocorrelated."""
        data = _autocorrelated_returns(500, rho=0.3)
        result = ljung_box_test(data, max_lag=20)
        assert not result.is_random
        assert 1 in result.significant_lags  # Lag 1 should be significant

    def test_small_sample(self):
        """Very short series should return is_random=True (no power)."""
        result = ljung_box_test([0.01, -0.01, 0.005])
        assert result.is_random

    def test_empty_sample(self):
        result = ljung_box_test([])
        assert result.is_random
        assert result.p_value == 1.0

    def test_perfectly_periodic(self):
        """Perfectly periodic series should not be random."""
        data = [1.0 if i % 2 == 0 else -1.0 for i in range(100)]
        result = ljung_box_test(data, max_lag=10)
        assert not result.is_random


class TestACF:
    def test_white_noise_acf_small(self):
        """ACF of white noise should be within confidence band."""
        data = _white_noise(500)
        acf = compute_acf(data, max_lag=10)
        for val in acf.acf_values:
            assert abs(val) < acf.confidence_band * 2  # Allow some overflow

    def test_autocorrelated_acf_positive_lag1(self):
        """AR(1) should have positive ACF at lag 1."""
        data = _autocorrelated_returns(500, rho=0.5)
        acf = compute_acf(data, max_lag=5)
        assert acf.acf_values[0] > 0.2  # Lag-1 should be strongly positive

    def test_empty(self):
        acf = compute_acf([])
        assert acf.lags == []


class TestPACF:
    def test_white_noise_pacf_small(self):
        """PACF of white noise should be within confidence band."""
        data = _white_noise(500)
        pacf = compute_pacf(data, max_lag=10)
        for val in pacf.pacf_values:
            assert abs(val) < pacf.confidence_band * 3  # Allow larger tolerance

    def test_ar1_pacf_lag1(self):
        """AR(1) should have significant PACF at lag 1 only."""
        data = _autocorrelated_returns(500, rho=0.5)
        pacf = compute_pacf(data, max_lag=5)
        # Lag 1 should be significant
        assert abs(pacf.pacf_values[0]) > 0.2
        # Lag 2+ should be smaller
        for val in pacf.pacf_values[1:]:
            assert abs(val) < abs(pacf.pacf_values[0])


class TestDurbinWatson:
    def test_white_noise(self):
        """DW near 2.0 for white noise."""
        data = _white_noise(500)
        dw = durbin_watson(data)
        assert 1.7 < dw < 2.3  # Allow some variation

    def test_positive_autocorrelation(self):
        """DW < 2 for positively autocorrelated series."""
        data = _autocorrelated_returns(500, rho=0.5)
        dw = durbin_watson(data)
        assert dw < 2.0

    def test_empty(self):
        dw = durbin_watson([])
        assert dw == 2.0


class TestRunsTest:
    def test_white_noise_random(self):
        """White noise should pass runs test."""
        data = _white_noise(500)
        p = runs_test(data)
        assert p > 0.01  # Should not be extremely significant

    def test_alternating_not_random(self):
        """Alternating series should fail runs test."""
        data = [1.0, -1.0] * 50
        p = runs_test(data)
        assert p < 0.05


class TestDiagnoseReturns:
    def test_white_noise_diagnosis(self):
        data = _white_noise(500)
        diag = diagnose_returns(data)
        assert diag.is_white_noise
        assert diag.n_observations == 500
        assert not diag.has_linear_dependence
        assert not diag.has_volatility_clustering

    def test_autocorrelated_diagnosis(self):
        data = _autocorrelated_returns(500, rho=0.4)
        diag = diagnose_returns(data)
        assert diag.has_linear_dependence
        assert not diag.is_white_noise

    def test_volatility_clustered_diagnosis(self):
        data = _volatility_clustered_returns(500)
        diag = diagnose_returns(data)
        assert diag.has_volatility_clustering

    def test_summary_string(self):
        data = _white_noise(200)
        diag = diagnose_returns(data)
        assert "WHITE NOISE" in diag.summary


class TestDiagnoseByRegime:
    def test_splits_by_regime(self):
        data = _white_noise(200)
        regimes = [0] * 100 + [1] * 100
        results = diagnose_by_regime(data, regimes)
        assert 0 in results
        assert 1 in results

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            diagnose_by_regime([0.1, 0.2], [0])


# -----------------------------------------------------------------------
# Tests: parameter_stability
# -----------------------------------------------------------------------

class TestParamGrid:
    def test_grid_generation(self):
        grids = [
            ParameterGrid("rsi_period", [10, 14, 20]),
            ParameterGrid("stop_mult", [1.0, 1.5]),
        ]
        combos = generate_param_grid(grids)
        assert len(combos) == 6  # 3 * 2

    def test_grid_combines_all(self):
        grids = [
            ParameterGrid("a", [1, 2]),
            ParameterGrid("b", [10, 20, 30]),
            ParameterGrid("c", [100]),
        ]
        combos = generate_param_grid(grids)
        assert len(combos) == 6  # 2 * 3 * 1

    def test_random_grid(self):
        grids = [
            ParameterGrid("rsi", list(range(5, 50))),
            ParameterGrid("sl", [0.5, 1.0, 1.5, 2.0]),
        ]
        combos = generate_param_grid_random(grids, n_samples=10, seed=42)
        assert len(combos) == 10
        # Each combo should have both params
        for c in combos:
            assert "rsi" in c
            assert "sl" in c


class TestStabilityScore:
    def test_all_profitable(self):
        """All variants profitable → grade A."""
        variants = [
            ParameterVariant(
                params={"x": i}, sharpe=1.0 + 0.1 * i, sortino=1.5,
                max_drawdown=0.1, win_rate=0.6, profit_factor=1.5,
                n_trades=100, expectancy_r=0.5, calmar=2.0,
            )
            for i in range(10)
        ]
        score = compute_stability_score(variants)
        assert score.fraction_profitable == 1.0
        assert score.is_stable
        assert score.grade in ("A", "B")

    def test_all_unprofitable(self):
        """All variants unprofitable → low grade."""
        variants = [
            ParameterVariant(
                params={"x": i}, sharpe=-1.0 - 0.1 * i, sortino=-1.5,
                max_drawdown=0.5, win_rate=0.4, profit_factor=0.8,
                n_trades=100, expectancy_r=-0.5, calmar=-2.0,
            )
            for i in range(10)
        ]
        score = compute_stability_score(variants)
        assert score.fraction_profitable == 0.0
        assert not score.is_stable
        assert score.grade in ("C", "D", "F")  # Low grade (cliff=1 smooths it)

    def test_empty_variants(self):
        score = compute_stability_score([])
        assert score.n_variants == 0
        assert score.grade == "F"


class TestHeatmap:
    def test_generates_matrix(self):
        variants = [
            ParameterVariant(
                params={"x": float(i), "y": float(j)},
                sharpe=float(i + j), sortino=0, max_drawdown=0,
                win_rate=0, profit_factor=0, n_trades=0,
                expectancy_r=0, calmar=0,
            )
            for i in range(3) for j in range(4)
        ]
        hm = generate_heatmap(variants, "x", "y", "sharpe")
        assert len(hm["z_matrix"]) == 4  # y values
        assert len(hm["z_matrix"][0]) == 3  # x values


class TestRunParameterStability:
    def test_simple_strategy(self):
        """Simple strategy where performance varies smoothly with parameter."""
        def strategy(params):
            rsi = params["rsi"]
            # Best at RSI=14, degrades away
            sharpe = max(0, 1.0 - 0.05 * abs(rsi - 14))
            return {
                "sharpe": sharpe, "sortino": sharpe * 1.2,
                "max_drawdown": 0.15, "win_rate": 0.55,
                "profit_factor": 1.3, "n_trades": 100,
                "expectancy_r": 0.3, "calmar": 2.0,
            }

        grids = [ParameterGrid("rsi", list(range(8, 25)))]
        result = run_parameter_stability(grids, strategy)
        assert result.stability.fraction_profitable > 0.5
        assert result.optimal.params["rsi"] == 14

    def test_fragile_strategy(self):
        """Strategy that only works at exact parameter value."""
        def strategy(params):
            rsi = params["rsi"]
            # Only works at RSI=14
            sharpe = 2.0 if rsi == 14 else -1.0
            return {
                "sharpe": sharpe, "sortino": sharpe,
                "max_drawdown": 0.3, "win_rate": 0.5,
                "profit_factor": 1.0, "n_trades": 50,
                "expectancy_r": 0.1, "calmar": 1.0,
            }

        grids = [ParameterGrid("rsi", list(range(8, 25)))]
        result = run_parameter_stability(grids, strategy)
        assert result.stability.fraction_profitable < 0.2
        assert result.stability.grade in ("D", "F")


# -----------------------------------------------------------------------
# Tests: overfit_detector
# -----------------------------------------------------------------------

class TestDeflatedSharpe:
    def test_high_sharpe_few_trials(self):
        """High Sharpe with few trials → significant."""
        result = deflated_sharpe_test(2.0, n_trials=5, sample_size=500)
        assert result["is_significant"]
        assert result["rating"] == "SIGNIFICANT"

    def test_moderate_sharpe_many_trials(self):
        """Moderate Sharpe with many trials → likely overfit.

        NOTE (DSR units fix, Sep-18): the null benchmark is scaled by the
        SE of the per-observation SR estimator, so "moderate" must be a
        per-bar SR the null can plausibly reach. SR=1.0 per-bar over 200
        obs is ~20 SEs — a massive edge that stays significant even at
        k=1000. A genuinely moderate per-bar edge (0.10 ≈ 2 SEs) is what
        the multiple-testing gate correctly flags.
        """
        result = deflated_sharpe_test(0.10, n_trials=1000, sample_size=200)
        assert not result["is_significant"]
        assert result["rating"] in ("LIKELY_OVERFIT", "SUSPICIOUS")

    def test_low_sharpe(self):
        """Low Sharpe → not significant."""
        result = deflated_sharpe_test(0.3, n_trials=100, sample_size=200)
        assert not result["is_significant"]


class TestMinBacktestLength:
    def test_sufficient_data(self):
        result = minimum_backtest_length(5000, n_strategies_tested=1, target_sharpe=1.0)
        assert result.is_sufficient

    def test_insufficient_data(self):
        """Short sample with a small target edge and many trials → insufficient.

        NOTE (DSR units fix, Sep-18): MBL is honest in per-observation
        units now. With target_sharpe=1.0 per bar, 50 obs is 7 SEs — the
        corrected math correctly reports that as sufficient. The test's
        intent (data too short for the tested hypothesis) requires a
        target the null can rival: 0.05 per bar vs the k=100 null max
        (~2.51 SEs = 0.355 at n=50) → genuinely insufficient.
        """
        result = minimum_backtest_length(50, n_strategies_tested=100, target_sharpe=0.05)
        assert not result.is_sufficient

    def test_many_strategies_need_more_data(self):
        """More strategies → need more observations."""
        result1 = minimum_backtest_length(1000, n_strategies_tested=1)
        result2 = minimum_backtest_length(1000, n_strategies_tested=100)
        # The second case should have a less favorable ratio
        assert result2.current_ratio <= result1.current_ratio


class TestISOSODivergence:
    def test_no_divergence(self):
        """Same IS and OOS → no divergence."""
        div = measure_is_oos_divergence(1.0, 0.95, 500, 200)
        assert div.divergence_ratio < 0.1
        assert not div.is_significant

    def test_large_divergence(self):
        """Large IS vs OOS gap → significant divergence."""
        div = measure_is_oos_divergence(2.0, 0.3, 500, 200)
        assert div.degradation_pct > 50
        assert div.is_significant


class TestStepwiseFitting:
    def test_monotonic_improvement(self):
        """Monotonically improving Sharpe → stepwise fitting detected."""
        trajectory = [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.7]
        result = detect_stepwise_fitting(trajectory)
        assert result.is_stepwise_fitting
        assert result.fit_velocity > 0.1

    def test_diminishing_returns(self):
        """Improvement plateaus → no stepwise fitting."""
        trajectory = [0.5, 0.8, 0.9, 0.92, 0.93, 0.93, 0.93]
        result = detect_stepwise_fitting(trajectory)
        assert not result.is_stepwise_fitting
        assert result.diminishing_returns

    def test_short_trajectory(self):
        result = detect_stepwise_fitting([0.5, 0.6])
        assert not result.is_stepwise_fitting


class TestCVStability:
    def test_stable_folds(self):
        """Similar fold sharpes → stable."""
        result = cv_stability_test([1.0, 1.1, 0.9, 1.0, 1.05])
        assert result["stable"]
        assert result["cv"] < 0.5

    def test_unstable_folds(self):
        """Highly variable folds → unstable."""
        result = cv_stability_test([2.0, -0.5, 1.5, -1.0, 0.3])
        assert not result["stable"]


class TestStrategyOverlap:
    def test_independent_strategies(self):
        """Independent strategies → no overlap."""
        rng = np.random.RandomState(42)
        matrix = rng.normal(0, 0.01, (5, 200))
        result = detect_strategy_overlap(matrix)
        assert result["effective_trials"] >= 3

    def test_identical_strategies(self):
        """Identical strategies → high overlap."""
        rng = np.random.RandomState(42)
        base = rng.normal(0, 0.01, 200)
        matrix = np.tile(base, (5, 1)) + rng.normal(0, 0.001, (5, 200))
        result = detect_strategy_overlap(matrix)
        assert result["effective_trials"] < 3


class TestDetectOverfitting:
    def test_white_noise_no_edge(self):
        """White noise returns → likely overfit."""
        data = _white_noise(200)
        result = detect_overfitting(data, n_strategies_tested=10)
        assert result.is_overfit
        assert result.risk_level in ("HIGH", "CRITICAL")

    def test_with_is_oos(self):
        """IS vs OOS comparison."""
        is_data = _autocorrelated_returns(200, rho=0.3)
        oos_data = _white_noise(100)  # No edge OOS
        result = detect_overfitting(
            np.concatenate([is_data, oos_data]),
            is_returns=is_data,
            oos_returns=oos_data,
            n_strategies_tested=5,
        )
        assert "divergence" in result.factors

    def test_stepwise_detection(self):
        """Stepwise fitting trajectory."""
        data = _white_noise(200)
        trajectory = [0.5, 0.7, 0.9, 1.1, 1.3, 1.5]
        result = detect_overfitting(
            data,
            n_strategies_tested=1,
            sharpe_trajectory=trajectory,
        )
        assert "stepwise" in result.factors
