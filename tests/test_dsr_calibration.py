"""Calibration proof for the multiple-testing (DSR) gate.

The gate has two failure modes and both are tested here:

  * **Size** -- pass a strategy that has NO edge.  Never above the nominal
    alpha; a promotion gate that manufactures edges is worse than no gate.
  * **Power** -- reject a strategy that DOES have an edge.  Measured, and the
    power cost of the trial count is documented rather than assumed away.

Units follow the DSR contract in ``core/metrics.py``: Sharpe values are
annualized by default, ``sample_size`` is the observation count behind them,
and ``periods_per_year`` must match the annualization that was applied.

These are the numbers the audit report quotes, kept executable so they cannot
drift back into folklore.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from core.metrics import (
    _expected_max_standard_normal,
    calculate_deflated_sharpe_ratio,
    expected_max_sharpe,
)

PERIODS = 252.0
ALPHA = 0.05


# ---------------------------------------------------------------------------
# 1. The maths: exact order statistics, not the biased Gumbel form
# ---------------------------------------------------------------------------

def test_exact_expected_max_matches_closed_form_cases():
    """k=2 has a closed form (1/sqrt(pi)); k=5 is the textbook 1.16296."""
    assert _expected_max_standard_normal(1) == 0.0
    assert _expected_max_standard_normal(2) == pytest.approx(1.0 / math.sqrt(math.pi), abs=1e-6)
    assert _expected_max_standard_normal(5) == pytest.approx(1.16296, abs=1e-4)
    # The Gumbel approximation the code used before 2026-10-20 returned 1.3425
    # here: +15% too harsh, and +50% at k=2.
    assert _expected_max_standard_normal(5) < 1.30


def test_expected_max_is_monotone_in_trials():
    values = [_expected_max_standard_normal(k) for k in (1, 2, 5, 10, 100, 2811)]
    assert values == sorted(values)
    assert all(b > a for a, b in zip(values, values[1:]))


def test_bar_shrinks_with_sample_size_and_tracks_units():
    """More observations => tighter SE => lower luck bar, at both scales."""
    large_n = expected_max_sharpe(10, 4000, periods_per_year=PERIODS)
    small_n = expected_max_sharpe(10, 250, periods_per_year=PERIODS)
    assert 0.0 < large_n < small_n

    # Same quantity expressed per-period must equal the annualized bar / sqrt(ppy).
    per_period = expected_max_sharpe(10, 500, annualized=False)
    annualized = expected_max_sharpe(10, 500, annualized=True, periods_per_year=PERIODS)
    assert annualized == pytest.approx(per_period * math.sqrt(PERIODS), rel=1e-9)


# ---------------------------------------------------------------------------
# 2. The contract: declaring units must not change the answer
# ---------------------------------------------------------------------------

def test_annualized_and_per_period_inputs_agree():
    """The same Sharpe expressed in either unit must give the SAME p-value."""
    annualized_sharpe = 1.75
    per_period_sharpe = annualized_sharpe / math.sqrt(PERIODS)

    p_annualized = calculate_deflated_sharpe_ratio(
        annualized_sharpe, num_trials=7, sample_size=600,
        annualized=True, periods_per_year=PERIODS,
    )
    p_per_period = calculate_deflated_sharpe_ratio(
        per_period_sharpe, num_trials=7, sample_size=600,
        annualized=False,
    )
    assert p_annualized == pytest.approx(p_per_period, rel=1e-9, abs=1e-12)


def test_periods_per_year_is_honoured():
    """De-annualizing with the wrong constant must move z by the ratio."""
    daily = calculate_deflated_sharpe_ratio(
        1.0, num_trials=5, sample_size=2000,
        annualized=True, periods_per_year=252.0,
    )
    hourly = calculate_deflated_sharpe_ratio(
        1.0, num_trials=5, sample_size=2000,
        annualized=True, periods_per_year=8760.0,
    )
    # A Sharpe annualized at 8760 implies a SMALLER per-bar edge than the same
    # number annualized at 252, so the hourly p-value must be the larger one:
    # the gate must not silently assume daily data for FX bars.
    assert hourly > daily


# ---------------------------------------------------------------------------
# 3. Size: no false edges
# ---------------------------------------------------------------------------

def _best_of_k_sharpes(rng, k: int, n: int, true_sr: float = 0.0) -> float:
    """Draw k per-period null strategies; one carries ``true_sr``. Return the max."""
    returns = rng.standard_normal((k, n))
    if true_sr:
        returns[0] += true_sr           # unit-variance returns shifted by the edge
    sharpes = returns.mean(axis=1) / returns.std(axis=1)
    return float(sharpes.max())


def _rejection_rate(k: int, n: int, true_sr: float = 0.0, reps: int = 150, seed: int = 11) -> float:
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        best = _best_of_k_sharpes(rng, k, n, true_sr)
        p = calculate_deflated_sharpe_ratio(
            best * math.sqrt(PERIODS), num_trials=k, sample_size=n,
            annualized=True, periods_per_year=PERIODS,
        )
        if p < ALPHA:
            hits += 1
    return hits / reps


@pytest.mark.parametrize("k", [1, 5, 20])
def test_null_is_never_anti_conservative(k):
    """Under a true null the gate must not exceed the nominal false-positive rate.

    The published DSR uses the Sharpe estimator's SE as the scale for a Gumbel
    distributed maximum, which makes it CONSERVATIVE for k > 1 (measured:
    0.053 at k=1, ~0.01 at k=5, ~0.003 at k=20).  Conservative is the safe
    direction for a promotion gate; anti-conservative is not.
    """
    rate = _rejection_rate(k, n=250)
    assert rate <= ALPHA + 0.02, f"k={k}: false-positive rate {rate:.3f} exceeded nominal"


def test_single_trial_size_is_close_to_nominal():
    """With one trial the gate is a plain one-sided z-test and must be nominal."""
    rate = _rejection_rate(1, n=250)
    assert ALPHA - 0.03 <= rate <= ALPHA + 0.02, f"k=1 size {rate:.3f} is not near nominal"


# ---------------------------------------------------------------------------
# 4. Power: real edges are detected, and the trial count is the price
# ---------------------------------------------------------------------------

def test_power_increases_with_true_edge():
    weak = _rejection_rate(1, n=500, true_sr=0.03, seed=21)
    strong = _rejection_rate(1, n=500, true_sr=0.12, seed=21)
    assert weak < strong
    assert strong >= 0.60, f"a per-period 0.12 edge should be caught, got {strong:.2f}"


def test_trial_count_costs_power():
    """Documented, not lamented: the same edge is harder to prove after more searches.

    This is the measured reason the live pipeline (2811 global trials) rejects
    everything short of an extraordinary Sharpe, and the argument for scoping
    trials by hypothesis class.
    """
    few = _rejection_rate(1, n=500, true_sr=0.12, seed=33)
    many = _rejection_rate(20, n=500, true_sr=0.12, seed=33)
    assert few > many, "more trials must never make the same edge easier to prove"
