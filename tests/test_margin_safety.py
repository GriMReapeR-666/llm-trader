import math

import pytest

from margin_safety import MarginRiskPolicy, MarginSafetyGate


def _account(**overrides):
    value = {
        "equity": 10_000.0,
        "margin": 1_000.0,
        "margin_free": 9_000.0,
        "margin_so_mode": 0,
        "margin_so_so": 50.0,
    }
    value.update(overrides)
    return value


def test_valid_margin_snapshot_is_approved():
    result = MarginSafetyGate().assess(
        account=_account(), new_margin=500.0, new_risk=100.0,
        symbol="EURUSD", driver_buckets=["USD"], position_margin_estimates=[],
    )
    assert result.approved is True
    assert result.projected_margin == 1500.0


@pytest.mark.parametrize("kwargs", [
    {"equity": math.nan},
    {"margin_free": math.inf},
])
def test_nonfinite_account_values_fail_closed(kwargs):
    result = MarginSafetyGate().assess(
        account=_account(**kwargs), new_margin=500.0, new_risk=100.0,
        symbol="EURUSD", driver_buckets=["USD"], position_margin_estimates=[],
    )
    assert result.approved is False


def test_malformed_position_estimate_fails_closed():
    result = MarginSafetyGate().assess(
        account=_account(), new_margin=500.0, new_risk=100.0,
        symbol="EURUSD", driver_buckets=["USD"],
        position_margin_estimates=[{"margin": "bad", "buckets": ["USD"]}],
    )
    assert result.approved is False
    assert "position margin estimate is unavailable or invalid" in result.reasons


def test_policy_rejects_nonfinite_or_out_of_range_values():
    with pytest.raises(ValueError):
        MarginRiskPolicy(max_margin_to_equity=math.inf)
    with pytest.raises(ValueError):
        MarginRiskPolicy(min_free_margin_to_equity=1.1)
