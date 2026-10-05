import pytest
import math

from research.setup_registry import SETUPS, classify
from research.setup_governance import change_gate, retirement_gate


def test_setup_registry_all_5_setups_classified():
    # Verify all 5 registered setups exist in SETUPS registry
    expected_setups = {
        "trend_pullback_v1",
        "range_reversal_v1",
        "liquidity_sweep_reversal_v1",
        "asian_range_breakout_v1",
        "macro_continuation_v1",
        "momentum_cascade_v1",
        # Engine-mode bracket fallback (see classify(strategy=...)).
        "scalp_bracket_v1",
    }
    assert set(SETUPS.keys()) == expected_setups

    # Test classification of macro_continuation_v1
    feat_macro = {"macro_continuation": True, "ema_alignment": 1, "mtf_alignment": 1, "confirmed_candle": True, "volume_confirmed": True}
    res_macro = classify(feat_macro, session="LONDON")
    assert res_macro["setup_id"] == "macro_continuation_v1"
    assert res_macro["eligible"] is True

    # Test classification of asian_range_breakout_v1
    feat_asian = {"asian_range_breakout": True, "confirmed_candle": True, "volume_confirmed": True}
    res_asian = classify(feat_asian, session="LONDON")
    assert res_asian["setup_id"] == "asian_range_breakout_v1"
    assert res_asian["eligible"] is True

    # Test classification of liquidity_sweep_reversal_v1
    feat_sweep = {"liquidity_sweep": True, "near_key_level": True, "confirmed_candle": True, "volume_confirmed": True}
    res_sweep = classify(feat_sweep, session="LONDON")
    assert res_sweep["setup_id"] == "liquidity_sweep_reversal_v1"
    assert res_sweep["eligible"] is True

    # Test classification of trend_pullback_v1
    feat_trend = {"adx": 25, "ema_alignment": 1, "mtf_alignment": 1, "near_key_level": True, "confirmed_candle": True}
    res_trend = classify(feat_trend, session="LONDON")
    assert res_trend["setup_id"] == "trend_pullback_v1"
    assert res_trend["eligible"] is True

    # Test classification of range_reversal_v1
    feat_range = {"adx": 15, "near_key_level": True, "confirmed_candle": True, "volume_confirmed": True}
    res_range = classify(feat_range, session="ASIAN")
    assert res_range["setup_id"] == "range_reversal_v1"
    assert res_range["eligible"] is True

    # Test classification of scalp_bracket_v1 (engine-mode fallback)
    feat_none = {"adx": 13.5, "ema_alignment": -1, "mtf_alignment": 0,
                 "near_key_level": True, "confirmed_candle": True,
                 "volume_confirmed": False, "liquidity_sweep": False,
                 "asian_range_breakout": True, "macro_continuation": False}
    res_bracket = classify(feat_none, session="LONDON", strategy="scalp")
    assert res_bracket["setup_id"] == "scalp_bracket_v1"
    assert res_bracket["eligible"] is True
    assert res_bracket["fallback"] is True


def test_setup_registry_non_finite_feature_fallback():
    # NaN ADX feature must return unclassified_v1
    res_nan = classify({"adx": float("nan"), "near_key_level": True})
    assert res_nan["setup_id"] == "unclassified_v1"
    assert res_nan["eligible"] is False
    assert "invalid ADX" in res_nan["reason"] or "non-finite" in res_nan["reason"]

    # Inf feature must return unclassified_v1
    res_inf = classify({"rsi": float("inf"), "near_key_level": True})
    assert res_inf["setup_id"] == "unclassified_v1"
    assert res_inf["eligible"] is False
    assert "non-finite" in res_inf["reason"]


def test_change_gate_minimum_samples_and_oos():
    # Insufficient closed samples (<100) -> disallowed
    res_insufficient = change_gate(closed_samples=45, out_of_sample_passed=True, previous_version_preserved=True)
    assert res_insufficient["allowed"] is False
    assert any("need 100 closed samples" in r for r in res_insufficient["reasons"])

    # Failed OOS -> disallowed
    res_no_oos = change_gate(closed_samples=120, out_of_sample_passed=False, previous_version_preserved=True)
    assert res_no_oos["allowed"] is False

    # Valid change gate requirements met -> allowed
    res_ok = change_gate(closed_samples=120, out_of_sample_passed=True, previous_version_preserved=True)
    assert res_ok["allowed"] is True
    assert res_ok["execution_authority"] is False


def test_retirement_gate_freeze_action():
    # Negative live expectancy -> FREEZE_AND_REQUIRE_OPERATOR_REVIEW
    res_neg_live = retirement_gate(
        live_expectancy_r=-0.5, oos_expectancy_r=0.2, degradation_fraction=0.10,
        minimum_samples=100, closed_samples=150
    )
    assert res_neg_live["retire_or_freeze"] is True
    assert res_neg_live["automatic_action"] == "FREEZE_AND_REQUIRE_OPERATOR_REVIEW"

    # Degradation > 30% -> FREEZE_AND_REQUIRE_OPERATOR_REVIEW
    res_deg = retirement_gate(
        live_expectancy_r=0.1, oos_expectancy_r=0.8, degradation_fraction=0.35,
        minimum_samples=100, closed_samples=150
    )
    assert res_deg["retire_or_freeze"] is True
    assert res_deg["automatic_action"] == "FREEZE_AND_REQUIRE_OPERATOR_REVIEW"

    # Healthy performance -> CONTINUE_MONITORING
    res_healthy = retirement_gate(
        live_expectancy_r=0.5, oos_expectancy_r=0.6, degradation_fraction=0.10,
        minimum_samples=100, closed_samples=150
    )
    assert res_healthy["retire_or_freeze"] is False
    assert res_healthy["automatic_action"] == "CONTINUE_MONITORING"


def test_setup_governance_malformed_inputs_fail_closed():
    changed = change_gate(closed_samples="bad", out_of_sample_passed="yes", previous_version_preserved=True)
    assert changed["allowed"] is False
    assert changed["execution_authority"] is False
    retired = retirement_gate(live_expectancy_r=float("nan"), oos_expectancy_r=0.1,
                              degradation_fraction="bad", max_degradation=2,
                              closed_samples="bad")
    assert retired["retire_or_freeze"] is True
    assert retired["execution_authority"] is False
