"""Pin MarginSafetyGate: default policy values and each limit in isolation.

Mutation testing showed every threshold constant could be doubled unnoticed.
With the defaults the margin-level floor (300 %) always binds before the
35 % margin/equity cap, so each limit is tested under a policy where only
that one limit is tight.
"""
from dataclasses import asdict

import pytest

from margin_safety import MarginRiskPolicy, MarginSafetyGate

EPS = 0.01


def test_default_policy_values_are_pinned():
    assert asdict(MarginRiskPolicy()) == {
        "max_margin_to_equity": 0.35,
        "min_free_margin_to_equity": 0.50,
        "min_margin_level_percent": 300.0,
        "stop_out_margin_level_buffer_percent": 200.0,
        "min_stop_out_buffer_r": 2.0,
        "max_new_symbol_margin_to_equity": 0.20,
        "max_driver_margin_to_equity": 0.25,
    }


def _policy(**tight):
    relaxed = dict(max_margin_to_equity=1.0, min_free_margin_to_equity=0.0,
                   min_margin_level_percent=1e-6, stop_out_margin_level_buffer_percent=1e-6,
                   min_stop_out_buffer_r=1e-6, max_new_symbol_margin_to_equity=1.0,
                   max_driver_margin_to_equity=1.0)
    relaxed.update(tight)
    return MarginRiskPolicy(**relaxed)


def _assess(policy, *, equity=10_000.0, margin=0.0, free=None, new_margin=500.0, new_risk=1.0,
            so=0.0, estimates=(), buckets=("USD",)):
    account = {"equity": equity, "margin": margin,
               "margin_free": equity - margin if free is None else free,
               "margin_so_mode": 0, "margin_so_so": so}
    return MarginSafetyGate(policy).assess(
        account=account, new_margin=new_margin, new_risk=new_risk, symbol="EURUSD",
        driver_buckets=list(buckets), position_margin_estimates=list(estimates))


# (id, policy, assess kwargs that land exactly AT the limit, kwarg to push past it, reason)
LIMITS = [
    ("margin_to_equity", _policy(max_margin_to_equity=0.35),
     dict(margin=3000.0, new_margin=500.0), ("new_margin", 500.0 + EPS),
     "projected margin/equity concentration limit"),
    ("free_margin", _policy(min_free_margin_to_equity=0.50),
     dict(free=5500.0, new_margin=500.0), ("new_margin", 500.0 + EPS),
     "projected free-margin buffer limit"),
    ("margin_level_floor", _policy(min_margin_level_percent=300.0),
     dict(equity=9000.0, margin=2500.0, new_margin=500.0), ("new_margin", 500.0 + EPS),
     "projected margin level below stop-out buffer"),
    ("stop_out_level_buffer", _policy(stop_out_margin_level_buffer_percent=200.0),
     dict(margin=3500.0, new_margin=500.0, so=50.0), ("new_margin", 500.0 + EPS),
     "projected margin level below stop-out buffer"),
    ("stop_out_buffer_r", _policy(min_stop_out_buffer_r=2.0),
     dict(margin=1500.0, new_margin=500.0, so=50.0, new_risk=4500.0), ("new_risk", 4500.0 + EPS),
     "stop-out loss buffer too close to planned risk"),
    ("symbol_concentration", _policy(max_new_symbol_margin_to_equity=0.20),
     dict(new_margin=2000.0), ("new_margin", 2000.0 + EPS),
     "symbol margin concentration limit"),
    ("driver_bucket_concentration", _policy(max_driver_margin_to_equity=0.25),
     dict(new_margin=500.0, estimates=[{"symbol": "GBPUSD", "buckets": ["USD"], "margin": 2000.0}]),
     ("new_margin", 500.0 + EPS), "driver-bucket margin concentration limit"),
]


@pytest.mark.parametrize("policy,at_limit", [pytest.param(p, a, id=i) for i, p, a, _, _ in LIMITS])
def test_exactly_at_limit_is_approved(policy, at_limit):
    result = _assess(policy, **at_limit)
    assert result.approved, result.reasons


@pytest.mark.parametrize("policy,at_limit,push,reason",
                         [pytest.param(p, a, k, r, id=i) for i, p, a, k, r in LIMITS])
def test_just_past_limit_is_rejected(policy, at_limit, push, reason):
    kwargs = dict(at_limit)
    kwargs[push[0]] = push[1]
    result = _assess(policy, **kwargs)
    assert not result.approved
    assert reason in result.reasons


def _gate_assess(**overrides):
    kwargs = dict(account={"equity": 10_000.0, "margin": 0.0, "margin_free": 10_000.0,
                           "margin_so_mode": 0, "margin_so_so": 50.0},
                  new_margin=100.0, new_risk=10.0, symbol="EURUSD", driver_buckets=["USD"],
                  position_margin_estimates=[], mode="mt5")
    kwargs.update(overrides)
    return MarginSafetyGate().assess(**kwargs)


def test_reference_snapshot_is_approved():
    assert _gate_assess().approved


@pytest.mark.parametrize("overrides,reason", [
    ({"mode": "paper"}, "outside MT5 mode"),
    ({"account": "not-a-dict"}, "account margin snapshot is malformed"),
    ({"symbol": ""}, "symbol is missing or malformed"),
    ({"new_risk": -1.0}, "new-order risk is unavailable or invalid"),
    ({"new_risk": None}, "new-order risk is unavailable or invalid"),
    ({"account": {"equity": 0.0, "margin": 0.0, "margin_free": 0.0, "margin_so_mode": 0, "margin_so_so": 50.0}},
     "equity unavailable or non-positive"),
    ({"account": {"equity": 10_000.0, "margin_free": 10_000.0, "margin_so_mode": 0, "margin_so_so": 50.0}},
     "current margin unavailable"),
    ({"account": {"equity": 10_000.0, "margin": 0.0, "margin_so_mode": 0, "margin_so_so": 50.0}},
     "free margin unavailable"),
    ({"new_margin": None}, "new-order margin unavailable"),
    ({"position_margin_estimates": ["junk"]}, "position margin estimate is malformed"),
    ({"position_margin_estimates": [{"symbol": "X", "margin": -5}]}, "position margin estimate is unavailable or invalid"),
    ({"position_margin_estimates": [{"symbol": "X", "margin": 5, "buckets": 7}]}, "buckets are malformed"),
    ({"account": {"equity": 10_000.0, "margin": 0.0, "margin_free": 10_000.0, "margin_so_mode": 1, "margin_so_so": 50.0}},
     "unsupported or missing broker stop-out mode/level"),
], ids=["not_mt5", "account_malformed", "symbol_missing", "risk_negative", "risk_none", "equity_zero",
        "margin_missing", "free_missing", "new_margin_none", "estimate_not_dict", "estimate_negative",
        "estimate_buckets_bad", "stop_out_mode_money"])
def test_malformed_input_is_rejected_with_its_reason(overrides, reason):
    result = _gate_assess(**overrides)
    assert not result.approved
    assert any(reason in r for r in result.reasons), result.reasons
