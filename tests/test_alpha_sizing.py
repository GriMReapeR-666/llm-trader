"""Tests for core/alpha_sizing.py — alpha-driven position sizing model."""
from __future__ import annotations

import pytest
from core.alpha_sizing import (
    compute_alpha_sizing,
    alpha_sizing_from_ensemble,
    AlphaSizingResult,
    ALIGN_STRONG, ALIGN_MODERATE, ALIGN_WEAK,
    CONTRA_STRONG, CONTRA_MODERATE, CONTRA_WEAK,
    NEUTRAL,
    MIN_MULTIPLIER, MAX_MULTIPLIER,
)


# ---------------------------------------------------------------------------
# Alignment tests
# ---------------------------------------------------------------------------

class TestAlphaSizingAlignment:
    def test_strong_bullish_buy(self):
        """Composite +1.5 with BUY → strong alignment → boost."""
        r = compute_alpha_sizing(1.5, "BUY", overall_ic=0.05)
        assert r.alignment == "aligned"
        assert r.multiplier > 1.0
        assert r.confidence_band == "high"

    def test_strong_bearish_sell(self):
        """Composite -1.5 with SELL → strong alignment → boost."""
        r = compute_alpha_sizing(-1.5, "SELL", overall_ic=0.05)
        assert r.alignment == "aligned"
        assert r.multiplier > 1.0

    def test_strong_bullish_sell_contradicts(self):
        """Composite +1.5 with SELL → contradiction → reduce."""
        r = compute_alpha_sizing(1.5, "SELL", overall_ic=0.05)
        assert r.alignment == "contradicts"
        assert r.multiplier < 1.0

    def test_strong_bearish_buy_contradicts(self):
        """Composite -1.5 with BUY → contradiction → reduce."""
        r = compute_alpha_sizing(-1.5, "BUY", overall_ic=0.05)
        assert r.alignment == "contradicts"
        assert r.multiplier < 1.0

    def test_neutral_composite(self):
        """Composite 0.0 → neutral → standard sizing (before IC/confidence discounts)."""
        r = compute_alpha_sizing(0.0, "BUY", overall_ic=0.05, composite_confidence="high")
        assert r.alignment == "neutral"
        assert r.multiplier == pytest.approx(NEUTRAL, abs=0.01)

    def test_moderate_alignment(self):
        """Composite +0.7 with BUY → moderate alignment."""
        r = compute_alpha_sizing(0.7, "BUY", overall_ic=0.05, composite_confidence="high")
        assert r.alignment == "aligned"
        assert r.multiplier > 1.0
        assert r.confidence_band == "medium"

    def test_weak_alignment(self):
        """Composite +0.3 with BUY → weak alignment."""
        r = compute_alpha_sizing(0.3, "BUY", overall_ic=0.05, composite_confidence="high")
        assert r.alignment == "aligned"
        assert r.multiplier > 1.0
        assert r.confidence_band == "low"

    def test_moderate_contradiction(self):
        """Composite -0.7 with BUY → moderate contradiction."""
        r = compute_alpha_sizing(-0.7, "BUY", overall_ic=0.05, composite_confidence="high")
        assert r.alignment == "contradicts"
        assert r.multiplier < 1.0

    def test_weak_contradiction(self):
        """Composite -0.3 with BUY → weak contradiction."""
        r = compute_alpha_sizing(-0.3, "BUY", overall_ic=0.05, composite_confidence="high")
        assert r.alignment == "contradicts"
        assert r.multiplier < 1.0


# ---------------------------------------------------------------------------
# IC quality tests
# ---------------------------------------------------------------------------

class TestAlphaSizingICQuality:
    def test_strong_ic_no_discount(self):
        r = compute_alpha_sizing(1.0, "BUY", overall_ic=0.06)
        assert r.ic_quality == "strong"
        # Strong IC → no discount → higher multiplier
        r_no_ic = compute_alpha_sizing(1.0, "BUY", overall_ic=0.005)
        assert r.multiplier >= r_no_ic.multiplier

    def test_weak_ic_discounts(self):
        r = compute_alpha_sizing(1.0, "BUY", overall_ic=0.015)
        assert r.ic_quality == "weak"
        assert r.multiplier < ALIGN_MODERATE  # discounted below raw

    def test_unknown_ic_heavy_discount(self):
        r = compute_alpha_sizing(1.0, "BUY", overall_ic=0.0)
        assert r.ic_quality == "unknown"
        assert r.multiplier < ALIGN_MODERATE


# ---------------------------------------------------------------------------
# Direction tests
# ---------------------------------------------------------------------------

class TestAlphaSizingDirection:
    def test_buy_requires_positive_composite(self):
        """BUY is aligned with positive composite."""
        r = compute_alpha_sizing(1.0, "BUY")
        assert r.alignment == "aligned"

    def test_sell_requires_negative_composite(self):
        """SELL is aligned with negative composite."""
        r = compute_alpha_sizing(-1.0, "SELL")
        assert r.alignment == "aligned"

    def test_opposite_direction_contradicts(self):
        """BUY with negative composite contradicts."""
        r = compute_alpha_sizing(-1.0, "BUY")
        assert r.alignment == "contradicts"

    def test_zero_composite_neutral_for_both(self):
        """Zero composite is neutral regardless of direction."""
        r_buy = compute_alpha_sizing(0.0, "BUY")
        r_sell = compute_alpha_sizing(0.0, "SELL")
        assert r_buy.alignment == "neutral"
        assert r_sell.alignment == "neutral"
        assert r_buy.multiplier == r_sell.multiplier


# ---------------------------------------------------------------------------
# Clamping tests
# ---------------------------------------------------------------------------

class TestAlphaSizingClamping:
    def test_minimum_multiplier(self):
        """Multiplier never goes below MIN_MULTIPLIER."""
        r = compute_alpha_sizing(-2.0, "BUY", overall_ic=0.0)
        assert r.multiplier >= MIN_MULTIPLIER

    def test_maximum_multiplier(self):
        """Multiplier never exceeds MAX_MULTIPLIER."""
        r = compute_alpha_sizing(2.0, "BUY", overall_ic=0.1, composite_confidence="high")
        assert r.multiplier <= MAX_MULTIPLIER

    def test_composite_clamped(self):
        """Extreme composite values are clamped."""
        r = compute_alpha_sizing(10.0, "BUY")
        assert r.composite == 2.0
        r2 = compute_alpha_sizing(-10.0, "SELL")
        assert r2.composite == -2.0


# ---------------------------------------------------------------------------
# Confidence discount tests
# ---------------------------------------------------------------------------

class TestAlphaSizingConfidence:
    def test_high_confidence_no_discount(self):
        r = compute_alpha_sizing(1.0, "BUY", composite_confidence="high")
        r_low = compute_alpha_sizing(1.0, "BUY", composite_confidence="low")
        assert r.multiplier >= r_low.multiplier

    def test_unknown_confidence_discounts(self):
        r = compute_alpha_sizing(1.0, "BUY", composite_confidence="unknown")
        assert r.multiplier < ALIGN_MODERATE


# ---------------------------------------------------------------------------
# ensemble convenience function
# ---------------------------------------------------------------------------

class TestAlphaSizingFromEnsemble:
    def test_extracts_composite(self):
        ensemble = {
            "composite_value": 1.2,
            "composite_confidence": "high",
            "overall_ic": 0.04,
            "n_alphas_used": 15,
        }
        r = alpha_sizing_from_ensemble(ensemble, "BUY")
        assert r.composite == 1.2
        assert r.alignment == "aligned"

    def test_missing_fields_defaults(self):
        r = alpha_sizing_from_ensemble({}, "BUY")
        assert r.composite == 0.0
        assert r.alignment == "neutral"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestAlphaSizingEdgeCases:
    def test_nan_composite(self):
        """NaN composite should default to neutral."""
        r = compute_alpha_sizing(float("nan"), "BUY")
        assert r.alignment == "neutral"

    def test_inf_composite(self):
        """Inf composite should be treated as neutral (not finite)."""
        r = compute_alpha_sizing(float("inf"), "BUY")
        assert r.alignment == "neutral"  # inf → 0.0 (not finite)

    def test_invalid_direction(self):
        """Invalid direction should be neutral."""
        r = compute_alpha_sizing(1.0, "HOLD", overall_ic=0.05, composite_confidence="high")
        assert r.alignment == "neutral"
        assert r.multiplier == pytest.approx(NEUTRAL, abs=0.01)

    def test_multiplier_in_reason(self):
        """Reason string should contain the multiplier."""
        r = compute_alpha_sizing(1.5, "BUY", overall_ic=0.05)
        assert "BUY" in r.reason
        assert "1.5" in r.reason or "aligned" in r.reason
