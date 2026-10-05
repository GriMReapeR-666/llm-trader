from datetime import datetime, timezone
import math

import pytest

from core.execution_policy import validate


def _policy():
    return {
        "version": "execution-policy-v1", "symbol": "EURUSD",
        "broker_fingerprint": "abc123", "max_spread_price": 0.001,
        "min_stop_distance_price": 0.0001, "entry_tolerance_min_price": 0.0001,
        "entry_tolerance_atr_fraction": 0.1, "expected_slippage_price": 0.0001,
        "commission_per_volume": 0.0, "expected_swap_money": 0.0,
        "expires_seconds": 900, "max_tick_age_ms": 5000,
        "status": "APPROVED", "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.parametrize("field", ["max_spread_price", "entry_tolerance_atr_fraction", "expected_slippage_price"])
def test_nonfinite_policy_numbers_fail_closed(field):
    policy = _policy()
    policy[field] = math.nan
    with pytest.raises(ValueError, match="execution policy field"):
        validate(policy, "EURUSD")


@pytest.mark.parametrize("field", ["expires_seconds", "max_tick_age_ms"])
def test_policy_timing_values_must_be_positive_integers(field):
    policy = _policy()
    policy[field] = 1.5
    with pytest.raises(ValueError, match="positive integer"):
        validate(policy, "EURUSD")


def test_policy_requires_nonempty_broker_fingerprint():
    policy = _policy()
    policy["broker_fingerprint"] = "   "
    with pytest.raises(ValueError, match="broker_fingerprint"):
        validate(policy, "EURUSD")


def test_policy_review_timestamp_must_be_timezone_aware_and_not_future():
    policy = _policy()
    policy["reviewed_at"] = "2026-08-08T00:00:00"
    with pytest.raises(ValueError, match="timezone"):
        validate(policy, "EURUSD")
    policy = _policy()
    policy["reviewed_at"] = "2099-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="future"):
        validate(policy, "EURUSD")


def test_valid_policy_still_validates():
    assert validate(_policy(), "EURUSD")["status"] == "APPROVED"
