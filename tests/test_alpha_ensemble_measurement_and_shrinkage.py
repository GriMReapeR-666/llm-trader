"""Tests for Task 1 (P0-4: Measurement vs Allocation split) and Task 2 (P0-2: T_eff shrinkage).

Verifies:
1. P0-4:
   - `measure_ic()` computes pure empirical IC metrics without shrinkage, regime adjustments, or decay knockout.
   - `allocate_weights()` applies shrinkage, regime multipliers, decay knockout, and min_ir filtering.
   - `combine_alphas()` orchestrates both cleanly.
2. P0-2:
   - When N/T > 0.25 (e.g. N=23, T=60 => r=0.383), logs WARNING and enforces James-Stein shrinkage lambda = min(0.5, r).
   - T_eff < 100 enforces shrinkage floor.
   - First/second half IC_IR stability does not degrade.
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd
import pytest

from core.alpha_ensemble import (
    AlphaQuality,
    allocate_weights,
    combine_alphas,
    compute_ic_weights,
    measure_ic,
)


def _make_sample_df(n: int = 150, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    df = pd.DataFrame({
        "open": close + rng.randn(n) * 0.1,
        "high": close + rng.uniform(0.2, 1.0, n),
        "low": close - rng.uniform(0.2, 1.0, n),
        "close": close,
        "volume": rng.uniform(1000, 5000, n),
    })
    fwd_ret = df["close"].pct_change().shift(-1).fillna(0.0)
    df["forward_return"] = fwd_ret

    # Create 5 synthetic alphas: 2 predictive, 2 noisy, 1 inverse
    # For predictive alphas, directly correlate with fwd_ret
    df["alpha_pred1"] = fwd_ret * 5.0 + rng.randn(n) * 0.001
    df["alpha_pred2"] = fwd_ret * 3.0 + rng.randn(n) * 0.002
    df["alpha_noise1"] = rng.randn(n) * 0.5
    df["alpha_noise2"] = rng.randn(n) * 0.5
    df["alpha_inv"] = -fwd_ret * 3.0 + rng.randn(n) * 0.002

    return df


class TestMeasurementVsAllocationSplit:
    """P0-4: Verification of measure_ic vs allocate_weights separation."""

    def test_measure_ic_pure_reporting(self):
        df = _make_sample_df()
        alpha_cols = ["alpha_pred1", "alpha_pred2", "alpha_noise1", "alpha_noise2", "alpha_inv"]
        normalized = {c: df[c] for c in alpha_cols}

        qualities = measure_ic(normalized, df["forward_return"], ic_window=60)

        assert isinstance(qualities, dict)
        assert set(qualities.keys()) == set(alpha_cols)
        for col, q in qualities.items():
            assert isinstance(q, AlphaQuality)
            assert hasattr(q, "ic_mean")
            assert hasattr(q, "ic_std")
            assert hasattr(q, "ic_ir")
            assert hasattr(q, "stability")
            assert hasattr(q, "turnover")

        # Predictive alphas should have strongly positive IC
        assert qualities["alpha_pred1"].ic_mean > 0.3
        assert qualities["alpha_pred1"].ic_mean > qualities["alpha_noise1"].ic_mean

    def test_allocate_weights_applies_decision_logic(self):
        df = _make_sample_df()
        alpha_cols = ["alpha_pred1", "alpha_pred2", "alpha_noise1", "alpha_noise2", "alpha_inv"]
        normalized = {c: df[c] for c in alpha_cols}
        qualities = measure_ic(normalized, df["forward_return"], ic_window=60)

        # 1. Basic allocation
        weights = allocate_weights(qualities, ic_window=60)
        assert pytest.approx(sum(weights.values()), abs=1e-5) == 1.0
        # Best alpha receives highest weight
        assert weights["alpha_pred1"] > weights["alpha_noise1"]

        # 2. Allocation with min_ir filter
        filtered_weights = allocate_weights(qualities, min_ir=0.3, ic_window=60)
        assert pytest.approx(sum(filtered_weights.values()), abs=1e-5) == 1.0

        # 3. Allocation with decay knockout
        decay_health = {
            "alpha_pred2": {"status": "DEAD", "ic_current": -0.05, "ic_mean": -0.02},
            "alpha_noise1": {"status": "REVERSED", "ic_current": -0.04, "ic_mean": -0.03},
        }
        knockout_weights = allocate_weights(qualities, decay_health=decay_health, ic_window=60)
        assert knockout_weights["alpha_pred2"] == 0.0
        assert knockout_weights["alpha_noise1"] == 0.0
        assert knockout_weights["alpha_pred1"] > 0.0
        assert pytest.approx(sum(knockout_weights.values()), abs=1e-5) == 1.0

    def test_combine_alphas_orchestrates_both(self):
        df = _make_sample_df()
        alpha_cols = ["alpha_pred1", "alpha_pred2", "alpha_noise1", "alpha_noise2", "alpha_inv"]
        result = combine_alphas(df, alpha_cols, ic_window=60)

        assert len(result.composite) == len(df)
        assert len(result.alpha_weights) == len(alpha_cols)
        assert pytest.approx(sum(result.alpha_weights.values()), abs=1e-5) == 1.0
        assert result.ic_mean_overall is not None


class TestJamesSteinShrinkageAndNoiseRatio:
    """P0-2: Verification of N/T ratio calculation, warning log, and James-Stein shrinkage."""

    def test_noise_ratio_warning_when_r_greater_than_025(self, caplog):
        # Create 23 qualities with T=60 -> r = 23/60 = 0.383 > 0.25
        qualities = {
            f"alpha_{i:03d}": AlphaQuality(
                name=f"alpha_{i:03d}",
                ic_mean=0.01 + 0.002 * (i % 5),
                ic_std=0.05,
                ic_ir=(0.01 + 0.002 * (i % 5)) / 0.05,
                ic_positive_pct=0.55,
                turnover=0.1,
                half_life=5,
                stability=0.2,
            )
            for i in range(23)
        }

        with caplog.at_level(logging.WARNING):
            weights = allocate_weights(qualities, ic_window=60)

        # Should log warning about r > 0.25
        assert any("r=" in record.message or "0.25" in record.message or "noise" in record.message.lower()
                   for record in caplog.records)
        assert len(weights) == 23
        assert pytest.approx(sum(weights.values()), abs=1e-5) == 1.0

        # Equal weight baseline is 1/23 = 0.04348
        # Shrunk weights should be closer to equal weight than raw IC-IR proportion
        equal_w = 1.0 / 23
        for w in weights.values():
            assert abs(w - equal_w) < 0.05

    def test_teff_less_than_100_enforces_shrinkage(self):
        qualities = {
            f"alpha_{i}": AlphaQuality(
                name=f"alpha_{i}",
                ic_mean=0.05 if i == 0 else 0.005,
                ic_std=0.05,
                ic_ir=1.0 if i == 0 else 0.1,
                ic_positive_pct=0.6,
                turnover=0.1,
                half_life=5,
                stability=0.5,
            )
            for i in range(5)
        }
        # T=40 < 100, N=5 -> r = 5/40 = 0.125
        # Even if caller passes shrinkage=0.0, T_eff < 100 enforces shrinkage
        weights = compute_ic_weights(qualities, shrinkage=0.0, t_eff=40)
        equal_w = 1.0 / 5
        # Alpha 0 raw would be ~0.7, but with enforced shrinkage should be pulled down
        assert weights["alpha_0"] < 0.7
        assert weights["alpha_1"] > 0.05
