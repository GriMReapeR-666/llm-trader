"""
Numeric tests pinning the real Lopez de Prado HRP and the Bailey/LdP
Deflated Sharpe Ratio implementations.

Regression context: HRP v1 returned equal weights 1/n regardless of
covariance; DSR v1 was a Bonferroni-style approximation with no skew or
kurtosis that returned non-probability values (e.g. dsr = 7.83).
"""

import numpy as np
import pytest
from scipy.stats import norm

from quant_framework.modules.portfolio_management import HRPOptimizer
from quant_framework.modules.strategy_development import DeflatedSharpeRatio


class TestHRPOptimizer:
    def setup_method(self):
        self.hrp = HRPOptimizer()
        self.rs = np.random.RandomState(42)

    def test_weights_sum_to_one(self):
        out = self.hrp.optimize([(self.rs.randn(400) * 0.01).tolist() for _ in range(4)])
        assert sum(out["weights"]) == pytest.approx(1.0, abs=1e-9)

    def test_not_equal_weight_for_correlated_inputs(self):
        # Regression guard: v1 returned exactly [1/3, 1/3, 1/3] here.
        r1 = (self.rs.randn(500) * 0.01).tolist()
        r2 = (0.98 * np.array(r1) + 0.0002 * self.rs.randn(500)).tolist()
        r3 = (self.rs.randn(500) * 0.01).tolist()
        out = self.hrp.optimize([r1, r2, r3])
        w = np.array(out["weights"])
        assert not np.allclose(w, 1.0 / 3.0, atol=0.01)
        # The duplicated pair splits its combined allocation symmetrically.
        assert w[0] == pytest.approx(w[1], rel=0.2)

    def test_near_identical_pair_matches_analytic_quarter_quarter_half(self):
        # Assets 1,2 identical; asset 3 independent, same vol.
        # Closed-form LdP bisection: w = [0.25, 0.25, 0.50].
        r1 = (self.rs.randn(500) * 0.01).tolist()
        r2 = (np.array(r1) + 0.0001 * self.rs.randn(500)).tolist()
        r3 = (self.rs.randn(500) * 0.01).tolist()
        w = np.array(self.hrp.optimize([r1, r2, r3])["weights"])
        assert w[0] == pytest.approx(0.25, abs=0.02)
        assert w[1] == pytest.approx(0.25, abs=0.02)
        assert w[2] == pytest.approx(0.50, abs=0.03)

    def test_low_vol_asset_gets_more_weight(self):
        low = (self.rs.randn(400) * 0.01).tolist()
        high = (self.rs.randn(400) * 0.05).tolist()
        w = np.array(self.hrp.optimize([low, high])["weights"])
        assert w[0] > 0.8
        assert w[1] < 0.2

    def test_iid_assets_get_near_equal_weights(self):
        w = np.array(self.hrp.optimize([(self.rs.randn(500) * 0.01).tolist() for _ in range(3)])["weights"])
        assert np.allclose(w, 1.0 / 3.0, atol=0.03)

    def test_single_linkage_tree_used_via_seriation(self):
        # Clustered structure must produce a quasi-diagonal ordering; verify
        # indirectly: the duplicated pair ends up adjacent in the tree order,
        # which yields symmetric within-pair weights and pair != third.
        r1 = (self.rs.randn(400) * 0.01).tolist()
        r2 = (np.array(r1) + 0.0001 * self.rs.randn(400)).tolist()
        r3 = (self.rs.randn(400) * 0.01).tolist()
        w = np.array(self.hrp.optimize([r1, r2, r3])["weights"])
        pair_share = w[0] + w[1]
        assert 0.4 < pair_share < 0.6

    def test_portfolio_volatility_consistent_with_weights(self):
        rets = [(self.rs.randn(300) * s).tolist() for s in (0.01, 0.02, 0.015)]
        cov = np.cov(np.array(rets))
        out = self.hrp.optimize(rets)
        w = np.array(out["weights"])
        expected_vol = float(np.sqrt(w @ cov @ w))
        assert out["portfolio_volatility"] == pytest.approx(expected_vol, rel=1e-6)

    def test_guards(self):
        assert "error" in self.hrp.optimize([[0.01, 0.02, 0.01]])
        assert "error" in self.hrp.optimize([[0.01, 0.02], [0.01, 0.02]])


class TestDeflatedSharpeRatio:
    def setup_method(self):
        self.dsr = DeflatedSharpeRatio()

    def test_dsr_is_a_probability(self):
        out = self.dsr.calculate(sharpe_ratio=1.5, n=252, trials=100)
        assert 0.0 <= out["dsr"] <= 1.0

    def test_report_overconfidence_case_no_longer_inflated(self):
        # v1 returned dsr = 7.83 (not a probability) for this input.
        out = self.dsr.calculate(sharpe_ratio=1.5, n=252, trials=100)
        assert out["dsr"] <= 1.0
        assert out["expected_max_sr"] > 0

    def test_small_edge_many_trials_is_not_significant(self):
        # SR 0.10 vs 1000 variants: expected max SR ~0.205 >> 0.10.
        out = self.dsr.calculate(sharpe_ratio=0.10, n=252, trials=1000)
        assert out["dsr"] < 0.5
        assert out["significant"] is False
        assert out["expected_max_sr"] > 0.10

    def test_strong_edge_few_trials_is_significant(self):
        out = self.dsr.calculate(sharpe_ratio=0.5, n=252, trials=10)
        assert out["dsr"] > 0.95
        assert out["significant"] is True

    def test_matches_scipy_reference(self):
        # Independent implementation of Bailey/LdP with default Gaussian
        # moments: the PSR denominator is sqrt(1 - 0*sr + (3-1)/4*sr^2).
        sr, n, t, gamma = 0.10, 252, 1000, 0.5772156649015329
        var_sr = 1.0 / (n - 1)
        sr0 = np.sqrt(var_sr) * ((1 - gamma) * norm.ppf(1 - 1.0 / t) + gamma * norm.ppf(1 - 1.0 / (t * np.e)))
        denom = np.sqrt(1.0 - 0.0 * sr + ((3.0 - 1.0) / 4.0) * sr ** 2)
        expected = float(norm.cdf((sr - sr0) * np.sqrt(n - 1) / denom))
        got = self.dsr.calculate(sharpe_ratio=sr, n=n, trials=t)["dsr"]
        assert got == pytest.approx(expected, abs=1e-9)

    def test_skew_and_kurtosis_reduce_dsr(self):
        base = self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=10000)
        ugly = self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=10000, skew=-1.0, kurtosis=8.0)
        assert ugly["dsr"] < base["dsr"]

    def test_more_trials_lower_dsr(self):
        dsrs = [self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=t)["dsr"] for t in (10, 100, 10000)]
        assert dsrs[0] > dsrs[1] > dsrs[2]

    def test_expected_max_sr_grows_with_trials(self):
        sr0s = [self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=t)["expected_max_sr"] for t in (10, 100, 10000)]
        assert sr0s[0] < sr0s[1] < sr0s[2]

    def test_higher_sr_higher_dsr(self):
        low = self.dsr.calculate(sharpe_ratio=0.20, n=252, trials=100)
        high = self.dsr.calculate(sharpe_ratio=0.60, n=252, trials=100)
        assert high["dsr"] > low["dsr"]

    def test_sr_variance_override(self):
        d1 = self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=100)
        d2 = self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=100, sr_variance=4.0 / 251)
        assert d2["dsr"] < d1["dsr"]

    def test_single_trial_no_selection_bias(self):
        out = self.dsr.calculate(sharpe_ratio=0.10, n=252, trials=1)
        assert out["expected_max_sr"] == 0.0
        # With SR0 = 0 and default Gaussian moments this is just PSR:
        # Phi( sr * sqrt(n-1) / sqrt(1 + (3-1)/4*sr^2) ).
        sr, n = 0.10, 252
        denom = np.sqrt(1.0 + ((3.0 - 1.0) / 4.0) * sr ** 2)
        expected = float(norm.cdf(sr * np.sqrt(n - 1) / denom))
        assert out["dsr"] == pytest.approx(expected, abs=1e-9)

    def test_p_value_complement(self):
        out = self.dsr.calculate(sharpe_ratio=0.30, n=252, trials=100)
        assert out["p_value"] == pytest.approx(1.0 - out["dsr"], abs=1e-12)

    def test_guards(self):
        assert "error" in self.dsr.calculate(sharpe_ratio=1.0, n=2)
        assert "error" in self.dsr.calculate(sharpe_ratio=1.0, n=100, kurtosis=0)
        assert "error" in self.dsr.calculate(sharpe_ratio=1.0, n=100, trials=0)
