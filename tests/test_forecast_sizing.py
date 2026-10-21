"""Carver (2015) sizing chain — unit tests.

The book's worked example (Tables 30-32, p.172-173) is used as ground
truth: a EUR 1,000,000 account at a 10% cash volatility target, three
instruments, and a diversification multiplier of 1.41.
"""
from core import forecast_sizing as cs


CARVER = cs.CarverConfig(periods_per_year=256)  # book's example: divide by 16


def test_book_example_daily_cash_volatility_target():
    # EUR 100,000 annualised target -> EUR 6,250 daily (p.172)
    target = cs.daily_cash_volatility_target(1_000_000, CARVER)
    assert round(target, 2) == 6250.00


def test_book_example_instrument_value_volatility():
    # US 20 year bond: 0.52%/day, $1500 block, 0.88 USD/EUR -> EUR 686 (Table 30)
    value_vol = cs.instrument_value_volatility(0.0052, 1500, 0.88)
    assert round(value_vol) == 686
def test_book_example_reproduces_table_31_and_32():
    daily_target = cs.daily_cash_volatility_target(1_000_000, CARVER)
    cases = [
        # (instrument value vol, combined forecast, weight, expected position)
        (686, 10, 0.50, 6.42),
        (841, -10, 0.25, -2.62),
        (674, -15, 0.25, -4.91),
    ]
    for value_vol, forecast, weight, expected in cases:
        scalar = cs.volatility_scalar(daily_target, value_vol, CARVER)
        subsys = cs.subsystem_position(forecast, scalar, CARVER)
        position = cs.portfolio_position(subsys, weight, 1.41)
        # The book rounds its intermediates to 2 dp, so compare with a
        # half-cent tolerance rather than to the last decimal place.
        assert abs(position - expected) < 0.02


def test_book_example_max_position_is_twice_the_scalar():
    # p.201: max position = 2 x volatility scalar x weight x multiplier
    daily_target = cs.daily_cash_volatility_target(1_000_000, CARVER)
    scalar = cs.volatility_scalar(daily_target, 841, CARVER)
    assert round(scalar, 2) == 7.43
    plan = cs.size_position(
        combined_forecast=20.0,
        equity=1_000_000,
        price_vol=841 / (100 * 1145 * 0.88),
        block_value=1145,
        fx_rate=0.88,
        instrument_weight=0.25,
        div_multiplier=1.41,
        cfg=CARVER,
    )
    assert round(plan.volatility_scalar, 2) == 7.43
    assert round(plan.max_position, 2) == round(2 * scalar * 0.25 * 1.41, 2)
    # A forecast at the cap produces exactly the maximum position.
    assert round(plan.portfolio_position, 6) == round(plan.max_position, 6)


def test_forecast_scalar_rescales_to_ten():
    assert round(cs.forecast_scalar([0.3, -0.3, 0.6]), 4) == round(10 / 0.4, 4)
    assert cs.forecast_scalar([]) == 0.0
    assert cs.forecast_scalar([0.0, 0.0]) == 0.0
    rescaled = cs.rescale_forecasts([0.3, -0.3, 0.6])
    assert round(sum(abs(f) for f in rescaled) / 3, 6) == 10.0


def test_combine_forecasts_equal_weight():
    combined = cs.combine_forecasts({"a": [2.0, 4.0], "b": [4.0, 2.0]})
    assert combined == [3.0, 3.0]


def test_combine_forecasts_caps_weight_at_twice_equal():
    # With three rules the equal weight is 1/3, so the cap is 2/3. A 10x
    # equal weight is capped down, and the total weight is allowed to fall
    # below 1 rather than being renormalised back up.
    combined = cs.combine_forecasts(
        {"a": [1.0], "b": [0.0], "c": [0.0]},
        weights={"a": 10.0, "b": 0.0, "c": 0.0},
    )
    assert abs(combined[0] - 2 / 3) < 1e-9


def test_combine_forecasts_ignores_empty_series():
    assert cs.combine_forecasts({}) == []


def test_cap_forecast():
    assert cs.cap_forecast(25.0) == 20.0
    assert cs.cap_forecast(-25.0) == -20.0
    assert cs.cap_forecast(float("nan")) == 0.0


def test_price_volatility_sma_and_ewma():
    returns = [0.01, -0.01, 0.02, -0.02, 0.01, -0.01, 0.02, -0.02]
    sma = cs.price_volatility(returns, lookback=8, method="sma")
    assert sma > 0
    ewma = cs.price_volatility(returns, method="ewma")
    assert 0 < ewma < 0.05
    assert cs.price_volatility([0.01], method="sma") == 0.0
    try:
        cs.price_volatility(returns, method="nope")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("unknown method must raise")


def test_diversification_multiplier_floors_negative_correlation():
    # Two uncorrelated instruments at half weight each -> sqrt(2) multiplier
    # (the book uses 1.41 for a three-instrument portfolio).
    identity = [[1.0, 0.0], [0.0, 1.0]]
    assert round(cs.diversification_multiplier([0.5, 0.5], identity), 4) == 1.4142
    # A negative correlation would otherwise cancel the variance entirely
    # and imply infinite diversification.
    negative = [[1.0, -1.0], [-1.0, 1.0]]
    assert round(cs.diversification_multiplier([0.5, 0.5], negative), 4) == 1.4142
    # Perfectly correlated instruments get no diversification benefit.
    positive = [[1.0, 1.0], [1.0, 1.0]]
    assert round(cs.diversification_multiplier([0.5, 0.5], positive), 4) == 1.0


def test_position_inertia_blocks_small_trades():
    # Target 133.52 rounds to 134 but is well inside 10% of 133 -> no trade.
    assert cs.target_trade(133.0, 133.52) == 0.0
    assert cs.target_trade(133.0, 150.0) == 17.0
    assert cs.target_trade(0.0, 0.05) == 0.0
    assert cs.target_trade(0.0, 3.0) == 3.0


def test_size_position_is_zero_without_volatility():
    plan = cs.size_position(
        combined_forecast=10.0,
        equity=100_000,
        price_vol=0.0,
        block_value=1000,
    )
    assert plan.volatility_scalar == 0.0
    assert plan.portfolio_position == 0.0
