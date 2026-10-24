"""Tests for P1-1: Contextual Thompson Sampling in research/contextual_bandit.py and alpha_ensemble integration."""
import os
import json
import pytest
import numpy as np
from pathlib import Path

from research.contextual_bandit import (
    ThompsonSamplingArm,
    ContextualThompsonBandit,
    bandit_report,
)
from core.alpha_ensemble import _apply_regime_adjustments, REGIME_ALPHA_MULTIPLIERS


def test_thompson_sampling_arm_updates_posterior():
    arm = ThompsonSamplingArm(alpha_prior=2.0, beta_prior=2.0)
    assert arm.samples == 0
    assert arm.posterior_win_rate() == 0.5

    # Feed 10 wins (r > 0)
    for _ in range(10):
        arm.update(1.5)

    assert arm.samples == 10
    assert arm.posterior_win_rate() > 0.8
    assert arm.posterior_mean_r() > 1.0

    # Draw Thompson samples
    samples = [arm.sample(seed=i) for i in range(100)]
    assert np.mean(samples) > 0.5


def test_contextual_thompson_bandit_selects_superior_arm():
    bandit = ContextualThompsonBandit()
    context = "LOW_VOL_TREND"
    arm_good = "momentum"
    arm_bad = "mean_reversion"

    # Train with 30 trades each
    for i in range(30):
        bandit.update(context, arm_good, 1.2 + (i % 3) * 0.1)
        bandit.update(context, arm_bad, -0.8 - (i % 3) * 0.1)

    # Arm selection under Thompson sampling should strongly favor arm_good
    selections = [
        bandit.select_arm(context, [arm_good, arm_bad], seed=s)
        for s in range(50)
    ]
    assert selections.count(arm_good) >= 45


def test_contextual_bandit_regime_multipliers_from_pnl():
    bandit = ContextualThompsonBandit(priors=REGIME_ALPHA_MULTIPLIERS)
    regime = "LOW_VOL_TREND"
    category = "reversal"

    initial_mult = bandit.get_expected_multiplier(regime, category)
    assert initial_mult == REGIME_ALPHA_MULTIPLIERS["LOW_VOL_TREND"]["reversal"]  # 0.5

    # Feed repeated strong performance to reversal in this regime
    for _ in range(25):
        bandit.update(regime, category, 2.0)

    updated_mult = bandit.get_expected_multiplier(regime, category)
    # The multiplier should have adjusted upwards from 0.5
    assert updated_mult > initial_mult


def test_export_and_integration_with_alpha_ensemble(tmp_path, monkeypatch):
    bandit = ContextualThompsonBandit(priors=REGIME_ALPHA_MULTIPLIERS)
    # Boost momentum in HIGH_VOL_CRISIS
    for _ in range(20):
        bandit.update("HIGH_VOL_CRISIS", "momentum", 2.5)

    out_file = tmp_path / "mars_alpha_multipliers.json"
    bandit.save_multipliers_artifact(str(out_file))
    assert out_file.exists()

    # Monkeypatch alpha_ensemble MARS file to point to out_file
    import core.alpha_ensemble as ae
    monkeypatch.setattr(ae, "MARS_MULTIPLIERS_FILE", str(out_file))

    weights = {"alpha_012": 0.5, "alpha_053": 0.5}  # alpha_012: momentum, alpha_053: reversal
    regime_ctx = {"primary_regime": "HIGH_VOL_CRISIS"}
    adjusted = _apply_regime_adjustments(weights, regime_ctx)

    # Momentum should receive higher weight than static 0.2/0.3 crisis ratio
    assert adjusted["alpha_012"] > 0.4
