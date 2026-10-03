"""
Numeric-correctness suite for quant_framework modules.

Design goal: every test pins a value that can be derived by hand or by an
independent implementation, so that the four documented implementation bugs
would have been caught before release:

  1. HurstCalculator returned H ~ 4e-15 for every input (broken tau
     estimator) and always classified "mean_reverting".
  2. HRPOptimizer returned equal weights 1/n regardless of covariance
     (no clustering, no bisection).
  3. ExpectancyCalculator.r_multiple reported the R-multiple in the
     `dollar_profit` field (unit bug).
  4. DeflatedSharpeRatio was a Bonferroni approximation producing
     non-probability "DSR" values (e.g. 7.83) with no skew/kurtosis.

Regression guards dedicated to those bugs are marked [BUG-1]..[BUG-4].
Analytic golden values for the remaining tools are included so any future
math regression fails loudly.
"""

import numpy as np
import pytest
from scipy.stats import norm

from quant_framework.modules.order_execution import AlmgrenChrissCalculator, DynamicSpreadCalculator
from quant_framework.modules.portfolio_management import HRPOptimizer, KellyCriterion
from quant_framework.modules.quant_utilities import DrawdownAnalyzer, ExpectancyCalculator
from quant_framework.modules.regime_detection import HMMRegimeClassifier, HurstCalculator
from quant_framework.modules.strategy_development import (
    DeflatedSharpeRatio,
    TripleBarrierLabeler,
    WalkForwardOptimizer,
)

GAMMA_EM = 0.5772156649015329  # Euler-Mascheroni


def _ar1_trend_prices(n=1200, phi=0.8, seed=7):
    """Persistent AR(1) steps: theoretical H of the increments is 1.5 (H_price -> ~0.75)."""
    rs = np.random.RandomState(seed)
    eps = rs.randn(n) * 0.01
    steps = np.zeros(n)
    for t in range(1, n):
        steps[t] = phi * steps[t - 1] + eps[t]
    return (100.0 + np.cumsum(steps)).tolist()


def _ou_prices(n=1200, theta=0.2, seed=11):
    rs = np.random.RandomState(seed)
    p = np.zeros(n)
    e = rs.randn(n)
    for t in range(1, n):
        p[t] = p[t - 1] - theta * p[t - 1] + e[t]
    return (100.0 + p).tolist()


def _random_walk_prices(n=1200, seed=3):
    rs = np.random.RandomState(seed)
    return (100.0 + np.cumsum(rs.randn(n))).tolist()


class TestHurstNumericCorrectness:
    """[BUG-1] The old tau estimator returned H ~ 4e-15 for all inputs."""

    def setup_method(self):
        self.calc = HurstCalculator()

    def test_bug1_trending_series_hurst_above_half(self):
        out = self.calc.calculate(_ar1_trend_prices())
        assert out["hurst_exponent"] > 0.55, out
        assert out["interpretation"] == "trending"

    def test_bug1_mean_reverting_series_hurst_below_half(self):
        out = self.calc.calculate(_ou_prices())
        assert out["hurst_exponent"] < 0.45, out
        assert out["interpretation"] == "mean_reverting"

    def test_bug1_random_walk_hurst_near_half(self):
        out = self.calc.calculate(_random_walk_prices())
        assert 0.40 < out["hurst_exponent"] < 0.60, out
        assert out["interpretation"] == "random_walk"

    def test_bug1_hurst_not_degenerate(self):
        # The old bug produced H < 1e-6 for EVERY series; genuine estimates
        # on these three canonical processes must all sit well away from 0.
        for series in (_ar1_trend_prices(), _ou_prices(), _random_walk_prices()):
            assert self.calc.calculate(series)["hurst_exponent"] > 0.05

    def test_strong_persistence_pushes_h_toward_one(self):
        # cumsum of AR(1) steps with phi=0.95: increments are highly
        # persistent, so tau grows nearly linearly over lags 2..20 -> H >> 0.5.
        # (A deterministic linear trend is NOT such a case: its lag-differences
        # have zero variance and the estimator correctly refuses to scale.)
        rs = np.random.RandomState(21)
        eps = rs.randn(1200) * 0.01
        steps = np.zeros(1200)
        for t in range(1, 1200):
            steps[t] = 0.95 * steps[t - 1] + eps[t]
        out = self.calc.calculate((100.0 + np.cumsum(steps)).tolist())
        assert out["hurst_exponent"] > 0.75, out
        assert out["interpretation"] == "trending"

    def test_iid_levels_are_anti_persistent(self):
        # iid price LEVELS: the lag-L difference has constant variance sqrt(2)*sd
        # for every L, so tau is flat and the log-log slope collapses to ~0.
        # This pins the estimator's documented anti-persistence behavior.
        rs = np.random.RandomState(9)
        out = self.calc.calculate((100.0 + rs.randn(2000) * 0.01).tolist(), max_lag=100)
        assert out["hurst_exponent"] < 0.35, out
        assert out["interpretation"] == "mean_reverting"

    def test_confidence_is_r_squared_of_loglog_fit(self):
        # Stochastic persistent series: the log-log fit is nearly perfect.
        out = self.calc.calculate(_ar1_trend_prices())
        assert 0.9 < out["confidence"] <= 1.0, out


class TestHRPNumericCorrectness:
    """[BUG-2] The old HRP returned exactly 1/n for every covariance."""

    def setup_method(self):
        self.hrp = HRPOptimizer()
        self.rs = np.random.RandomState(42)

    def test_bug2_not_equal_weight_on_correlated_input(self):
        r1 = (self.rs.randn(500) * 0.01).tolist()
        r2 = (0.98 * np.array(r1) + 0.0002 * self.rs.randn(500)).tolist()
        r3 = (self.rs.randn(500) * 0.01).tolist()
        w = np.array(self.hrp.optimize([r1, r2, r3])["weights"])
        assert not np.allclose(w, 1.0 / 3.0, atol=0.01), w

    def test_bug2_analytic_duplicate_pair_quarter_quarter_half(self):
        # Closed-form LdP bisection: identical pair {1,2} + independent {3}.
        r1 = (self.rs.randn(500) * 0.01).tolist()
        r2 = (np.array(r1) + 0.0001 * self.rs.randn(500)).tolist()
        r3 = (self.rs.randn(500) * 0.01).tolist()
        w = np.array(self.hrp.optimize([r1, r2, r3])["weights"])
        assert w[0] == pytest.approx(0.25, abs=0.02), w
        assert w[1] == pytest.approx(0.25, abs=0.02), w
        assert w[2] == pytest.approx(0.50, abs=0.03), w

    def test_bug2_vol_weighting_two_assets(self):
        # Two independent assets: the single bisection splits by cluster
        # VARIANCE, so w_low = sig_high^2 / (sig_low^2 + sig_high^2) = 25/26
        # for 1% vs 5% vol (NOT 5/6 - inverse variance, not inverse vol).
        low = (self.rs.randn(4000) * 0.01).tolist()
        high = (self.rs.randn(4000) * 0.05).tolist()
        w = np.array(self.hrp.optimize([low, high])["weights"])
        assert w[0] == pytest.approx(25.0 / 26.0, abs=0.05), w
        assert w[1] == pytest.approx(1.0 / 26.0, abs=0.05), w

    def test_weights_sum_to_one_and_nonnegative(self):
        out = self.hrp.optimize([(self.rs.randn(300) * 0.02).tolist() for _ in range(5)])
        w = np.array(out["weights"])
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert (w >= 0).all()

    def test_portfolio_volatility_matches_wtw(self):
        rets = [(self.rs.randn(400) * s).tolist() for s in (0.01, 0.03, 0.02, 0.015)]
        cov = np.cov(np.array(rets))
        out = self.hrp.optimize(rets)
        w = np.array(out["weights"])
        assert out["portfolio_volatility"] == pytest.approx(float(np.sqrt(w @ cov @ w)), rel=1e-9)


class TestRMultipleNumericCorrectness:
    """[BUG-3] `dollar_profit` used to carry the R-multiple, not P&L."""

    def setup_method(self):
        self.calc = ExpectancyCalculator()

    def test_bug3_dollar_profit_is_absolute_pnl(self):
        # entry 100, exit 110, stop 95: risk = 5, P&L = +10, R = +2.
        out = self.calc.r_multiple(entry=100.0, exit=110.0, stop=95.0)
        assert out["r_multiple"] == pytest.approx(2.0)
        assert out["dollar_profit"] == pytest.approx(10.0)

    def test_bug3_loss_side_units(self):
        # entry 100, exit 98, stop 95: risk = 5, P&L = -2, R = -0.4.
        out = self.calc.r_multiple(entry=100.0, exit=98.0, stop=95.0)
        assert out["r_multiple"] == pytest.approx(-0.4)
        assert out["dollar_profit"] == pytest.approx(-2.0)

    def test_stop_exit_is_minus_one_r(self):
        out = self.calc.r_multiple(entry=100.0, exit=95.0, stop=95.0)
        assert out["r_multiple"] == pytest.approx(-1.0)
        assert out["dollar_profit"] == pytest.approx(-5.0)

    def test_price_decline_on_long_is_negative_r(self):
        # The tool is long-entry by contract (r = (exit-entry)/|entry-stop|):
        # entry 50, exit 45, stop 52 -> risk 2, price change -5, R = -2.5.
        # Pins sign coherence: R and dollar_profit share sign, not scale.
        out = self.calc.r_multiple(entry=50.0, exit=45.0, stop=52.0)
        assert out["r_multiple"] == pytest.approx(-2.5)
        assert out["dollar_profit"] == pytest.approx(-5.0)

    def test_expectancy_golden_value(self):
        # EV = 0.4*2.0 - 0.6*1.0 = 0.2
        out = self.calc.calculate(win_rate=0.4, avg_win_r=2.0, avg_loss_r=1.0)
        assert out["ev"] == pytest.approx(0.2)
        assert out["profitable"] is True

    def test_expectancy_breakeven(self):
        out = self.calc.calculate(win_rate=1.0 / 3.0, avg_win_r=2.0, avg_loss_r=1.0)
        assert out["ev"] == pytest.approx(0.0, abs=1e-12)
        assert out["profitable"] is False


class TestDSRNumericCorrectness:
    """[BUG-4] The old DSR was not a probability and ignored skew/kurtosis."""

    def setup_method(self):
        self.dsr = DeflatedSharpeRatio()

    def _reference(self, sr, n, t, skew=0.0, kurt=3.0, var_sr=None):
        var = var_sr if var_sr else 1.0 / (n - 1)
        if t <= 1:
            sr0 = 0.0
        else:
            sr0 = np.sqrt(var) * ((1 - GAMMA_EM) * norm.ppf(1 - 1.0 / t)
                                  + GAMMA_EM * norm.ppf(1 - 1.0 / (t * np.e)))
        denom = np.sqrt(1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr ** 2)
        return float(norm.cdf((sr - sr0) * np.sqrt(n - 1) / denom))

    def test_bug4_dsr_is_probability_for_the_7_83_case(self):
        # The old code returned dsr = 7.83 for exactly this input.
        out = self.dsr.calculate(sharpe_ratio=1.5, n=252, trials=100)
        assert 0.0 <= out["dsr"] <= 1.0, out

    def test_matches_independent_scipy_implementation(self):
        for sr, n, t, skew, kurt in [
            (0.10, 252, 1000, 0.0, 3.0),
            (0.30, 252, 100, -0.5, 5.0),
            (0.05, 500, 10000, 1.0, 2.0),
        ]:
            got = self.dsr.calculate(sharpe_ratio=sr, n=n, trials=t, skew=skew, kurtosis=kurt)["dsr"]
            assert got == pytest.approx(self._reference(sr, n, t, skew, kurt), abs=1e-9), (sr, n, t)

    def test_bug4_multiple_testing_rejects_small_edge(self):
        # SR 0.10 cannot beat the expected max of 1000 pure-noise variants.
        out = self.dsr.calculate(sharpe_ratio=0.10, n=252, trials=1000)
        assert out["dsr"] < 0.5
        assert out["expected_max_sr"] > 0.10
        assert out["significant"] is False

    def test_skew_kurtosis_enter_the_math(self):
        base = self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=10000)
        ugly = self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=10000, skew=-1.0, kurtosis=8.0)
        assert ugly["dsr"] < base["dsr"]

    def test_p_value_and_significance_contract(self):
        out = self.dsr.calculate(sharpe_ratio=0.5, n=252, trials=10)
        assert out["p_value"] == pytest.approx(1.0 - out["dsr"], abs=1e-12)
        assert out["significant"] == (out["dsr"] > 0.95)

    def test_monotone_in_trials_and_sr(self):
        dsrs = [self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=t)["dsr"] for t in (10, 100, 10000)]
        assert dsrs[0] > dsrs[1] > dsrs[2]
        lo = self.dsr.calculate(sharpe_ratio=0.2, n=252, trials=100)["dsr"]
        hi = self.dsr.calculate(sharpe_ratio=0.6, n=252, trials=100)["dsr"]
        assert hi > lo


class TestRemainingToolsGoldenValues:
    """Analytic pins for every other tool, so math regressions fail loudly."""

    def test_kelly_golden(self):
        out = KellyCriterion().calculate(win_rate=0.6, win_loss_ratio=1.5, fraction=0.25)
        assert out["kelly_fraction"] == pytest.approx(1.0 / 3.0, abs=1e-9)
        assert out["applied_fraction"] == pytest.approx(1.0 / 12.0, abs=1e-9)

    def test_kelly_rejects_garbage(self):
        assert "error" in KellyCriterion().calculate(win_rate=1.5, win_loss_ratio=1.0)
        assert "error" in KellyCriterion().calculate(win_rate=0.5, win_loss_ratio=0.0)

    def test_drawdown_golden(self):
        # Drawdowns: 110->90 = 20/110, 110->95 = 15/110, 120->80 = 40/120.
        # The deepest is the LAST one: max DD = 40/120 = 1/3.
        out = DrawdownAnalyzer().analyze(equity_curve=[100, 110, 90, 95, 120, 80, 130])
        assert out["max_drawdown"] == pytest.approx(40.0 / 120.0, abs=1e-12)
        assert out["max_drawdown_duration"] == 2  # longest underwater run = 2 bars

    def test_drawdown_no_dd(self):
        out = DrawdownAnalyzer().analyze(equity_curve=[100, 101, 102, 103])
        assert out["max_drawdown"] == 0.0
        assert out["calmar_ratio"] == 0.0

    def test_spread_golden(self):
        out = DynamicSpreadCalculator().calculate(
            base_spread_bps=2.0, volatility_ratio=2.0, volume_ratio=2.0, session_multiplier=1.0
        )
        # 2.0 * (1 + 0.75*1 + 0.50*1) * 1.0 = 4.5
        assert out["dynamic_spread_bps"] == pytest.approx(4.5, abs=1e-12)
        assert out["half_spread_cost"] == pytest.approx(2.25, abs=1e-12)

    def test_almgren_chriss_golden(self):
        out = AlmgrenChrissCalculator().calculate(
            order_size=10_000, adv=1_000_000, price=100.0, volatility=0.02, duration_bars=1
        )
        participation = 0.01
        i_perm = 0.314 * 0.02 * 100.0 * participation          # 0.0628
        i_temp = 0.142 * 0.02 * 100.0 * ((10_000 / (1_000_000 / 78)) ** 0.5)
        expected_bps = (i_perm + i_temp) / 100.0 * 10_000
        assert out["total_impact_bps"] == pytest.approx(expected_bps, rel=1e-9)
        assert out["participation_ratio"] == pytest.approx(participation, abs=1e-12)

    def test_wfo_detects_degenerate_strategy(self):
        # Pure-noise returns: OOS Sharpe must not be systematically positive.
        rs = np.random.RandomState(0)
        out = WalkForwardOptimizer().optimize((rs.randn(600) * 0.01).tolist())
        assert out["robust"] is False
        assert out["wfe"] < 0.6

    def test_triple_barrier_golden_time_exit(self):
        out = TripleBarrierLabeler().label(
            prices=[100, 101, 102, 103, 104, 105, 106],
            entry_price=100.0, take_profit=0.05, stop_loss=-0.05, max_hold=20,
        )
        # returns = 0.01*i: first >= 5% at bar 5 (< max_hold) -> TP exit.
        assert out["label"] == 1
        assert out["exit_bar"] == 5
        assert out["return"] == pytest.approx(0.05)

    def test_triple_barrier_time_exit_beyond_max_hold(self):
        # returns = 0.002*i: 5% is only reached at bar 25, after max_hold=20.
        out = TripleBarrierLabeler().label(
            prices=[100.0 * (1 + 0.002 * i) for i in range(26)],
            entry_price=100.0, take_profit=0.05, stop_loss=-0.05, max_hold=20,
        )
        assert out["label"] == 0
        assert out["exit_bar"] == 19

    def test_triple_barrier_stop(self):
        out = TripleBarrierLabeler().label(
            prices=[100, 99, 97, 94],
            entry_price=100.0, take_profit=0.05, stop_loss=-0.05, max_hold=20,
        )
        assert out["label"] == -1
        assert out["exit_bar"] == 3

    def test_hmm_flow_end_to_end(self):
        rs = np.random.RandomState(42)
        data = np.concatenate([rs.randn(150) * 0.005, rs.randn(150) * 0.05]).tolist()
        clf = HMMRegimeClassifier(n_states=2)
        fit = clf.fit(data)
        assert fit["fitted"] is True
        assert set(fit["state_labels"].values()) == {"calm", "high_vol"}
        out = clf.classify(data[-50:])
        assert out["regime_label"] == "high_vol"
        assert out["confidence"] > 0.8
