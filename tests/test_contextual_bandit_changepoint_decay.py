"""Unit tests for Thompson Sampling Changepoint Fast-Adaptation (Flag 6)."""
import pytest
from research.contextual_bandit import ThompsonSamplingArm, ContextualThompsonBandit


def test_arm_decay_reduces_inertia():
    arm = ThompsonSamplingArm(alpha_prior=2.0, beta_prior=2.0)
    # Simulate 50 wins in old regime
    for _ in range(50):
        arm.update(1.5)
    
    assert arm.wins == 50
    assert arm.alpha == 52.0
    assert arm.beta == 2.0
    
    # Apply changepoint decay with gamma = 0.40
    arm.decay(gamma=0.40)
    
    # Prior offset retained, excess decayed by 0.40
    # Expected alpha: 2.0 + 0.40 * 50 = 22.0
    assert pytest.approx(arm.alpha, 0.01) == 22.0
    assert pytest.approx(arm.beta, 0.01) == 2.0
    assert arm.samples == int(50 * 0.40)


def test_bandit_apply_changepoint_decay():
    bandit = ContextualThompsonBandit()
    # Populate crisis and calm regime arms
    for _ in range(30):
        bandit.update("calm", "momentum", 2.0)
        bandit.update("crisis", "mean_reversion", 1.5)
    
    # Before decay
    mult_before = bandit.get_expected_multiplier("calm", "momentum")
    assert mult_before > 1.2
    
    # Apply changepoint decay to all contexts
    bandit.apply_changepoint_decay(gamma=0.30)
    
    arm_calm = bandit.get_arm("calm", "momentum")
    assert arm_calm.samples <= 10


def test_fast_adaptation_after_decay():
    # Arm A gets decayed on changepoint, Arm B does not
    arm_decayed = ThompsonSamplingArm(alpha_prior=2.0, beta_prior=2.0)
    arm_stale = ThompsonSamplingArm(alpha_prior=2.0, beta_prior=2.0)
    
    for _ in range(60):
        arm_decayed.update(1.0)
        arm_stale.update(1.0)
    
    # Changepoint occurs: regime flips from winning to losing
    arm_decayed.decay(gamma=0.30)
    
    # 5 consecutive losses occur in new regime
    for _ in range(5):
        arm_decayed.update(-1.0)
        arm_stale.update(-1.0)
    
    # Decayed arm should have significantly lower posterior win rate than stale arm
    p_decayed = arm_decayed.posterior_win_rate()
    p_stale = arm_stale.posterior_win_rate()
    assert p_decayed < p_stale
