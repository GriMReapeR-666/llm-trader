import math

import pytest

from cost_estimator import assess_costs


def _kwargs():
    return {
        "side": "BUY", "entry": 100.0, "stop_loss": 99.0,
        "take_profit": 103.0, "volume": 1.0, "bid": 99.9,
        "ask": 100.1, "tick_size": 0.01, "tick_value": 1.0,
        "commission_per_volume": 0.1, "expected_slippage_price": 0.01,
        "expected_swap": 0.1,
    }


@pytest.mark.parametrize("field", ["entry", "stop_loss", "take_profit", "volume", "bid", "ask", "tick_size", "tick_value"])
def test_nonfinite_broker_inputs_fail_closed(field):
    values = _kwargs()
    values[field] = math.nan
    result = assess_costs(**values)
    assert result.approved is False
    assert "invalid broker quote/specification" in result.reasons[0]


@pytest.mark.parametrize("field", ["commission_per_volume", "expected_slippage_price", "expected_swap"])
def test_nonfinite_or_negative_cost_assumptions_fail_closed(field):
    values = _kwargs()
    values[field] = math.inf
    result = assess_costs(**values)
    assert result.approved is False
    assert any("assumption is malformed" in reason for reason in result.reasons)

    values[field] = -1.0
    result = assess_costs(**values)
    assert result.approved is False
    assert any("assumption is malformed" in reason for reason in result.reasons)


def test_valid_calibrated_costs_can_pass_net_rr():
    result = assess_costs(**_kwargs())
    assert result.calibrated is True
    assert result.approved is True
    assert result.net_reward_risk is not None and result.net_reward_risk >= 2.0
