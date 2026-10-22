"""The consolidated sizing route — order of operations, bounds, and the cap.

The migration question this file answers: if the live route switched from
"flat fraction of equity" to the forecast-driven chain, would risk go up?
The answer must be measurable, so it is measured here.
"""
from core import forecast_sizing as fs
from core.sizing_spine import (
    DEFAULT_RISK_CAP,
    MAX_MULTIPLIER,
    MIN_MULTIPLIER,
    SizingInputs,
    legacy_risk_amount,
    size_position,
)

EQUITY = 100_000.0


def _inputs(**overrides):
    base = dict(
        combined_forecast=10.0,
        equity=EQUITY,
        price_vol=0.01,
        block_value=1000.0,
        risk_per_block=100.0,
    )
    base.update(overrides)
    return SizingInputs(**base)


def test_absolute_size_is_derived_from_the_forecast_first():
    low = size_position(_inputs(combined_forecast=2.0))
    high = size_position(_inputs(combined_forecast=20.0))
    assert abs(low.carver_position) < abs(high.carver_position)
    # A flat forecast of 10 is one volatility scalar.
    plan = fs.size_position(
        combined_forecast=10.0,
        equity=EQUITY,
        price_vol=0.01,
        block_value=1000.0,
    )
    assert abs(low.carver_position) > 0
    assert plan.subsystem_position > 0


def test_multipliers_are_applied_after_the_absolute_size():
    plain = size_position(_inputs())
    boosted = size_position(_inputs(confidence_multiplier=1.35))
    assert round(boosted.carver_position, 8) == round(plain.carver_position, 8)
    assert boosted.adjusted_position > plain.adjusted_position


def test_multipliers_are_bounded():
    huge = size_position(_inputs(confidence_multiplier=100.0))
    bounded = size_position(_inputs(confidence_multiplier=MAX_MULTIPLIER))
    assert round(huge.adjusted_position, 8) == round(bounded.adjusted_position, 8)
    tiny = size_position(_inputs(regime_multiplier=0.0))
    floor = size_position(_inputs(regime_multiplier=MIN_MULTIPLIER))
    assert round(tiny.adjusted_position, 8) == round(floor.adjusted_position, 8)


def test_risk_cap_holds_even_with_the_largest_multipliers():
    result = size_position(
        _inputs(
            combined_forecast=20.0,
            risk_per_block=1000.0,
            confidence_multiplier=MAX_MULTIPLIER,
            regime_multiplier=MAX_MULTIPLIER,
        )
    )
    assert result.risk_amount > result.risk_limit
    assert result.capped_risk_amount == result.risk_limit
    assert result.risk_limit == EQUITY * DEFAULT_RISK_CAP


def test_route_never_exceeds_the_live_rules_risk():
    """The consolidation must not raise risk above what the live rule uses."""
    legacy = legacy_risk_amount(EQUITY)
    for forecast in (-20.0, -10.0, -2.0, 0.0, 2.0, 10.0, 20.0):
        result = size_position(_inputs(combined_forecast=forecast))
        assert result.capped_risk_amount <= legacy


def test_final_position_reflects_the_cap():
    result = size_position(
        _inputs(combined_forecast=20.0, risk_per_block=1000.0,
                confidence_multiplier=MAX_MULTIPLIER,
                regime_multiplier=MAX_MULTIPLIER)
    )
    # 1000 risk limit / 1000 per block
    assert result.final_position == 1.0


def test_sign_follows_the_forecast():
    long = size_position(_inputs(combined_forecast=10.0))
    short = size_position(_inputs(combined_forecast=-10.0))
    assert long.final_position > 0
    assert short.final_position < 0
