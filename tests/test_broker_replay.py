import math

import pytest

from broker_replay import ReplayIntent, ReplaySpec, replay


def _spec(**overrides):
    values = {
        "point": 0.01,
        "contract_size": 1.0,
        "commission_per_volume": 0.0,
        "swap_per_volume_day": 1.0,
        "expected_slippage_price": 0.0,
        "spread_price": 0.0,
    }
    values.update(overrides)
    return ReplaySpec(**values)


def test_replay_uses_timestamps_for_swap_days():
    bars = [
        {"timestamp": "2026-01-01T00:00:00Z", "low": 99.5, "high": 100.5, "close": 100.0},
        {"timestamp": "2026-01-02T00:00:00Z", "low": 100.0, "high": 102.5, "close": 102.0},
    ]
    result = replay([ReplayIntent("t1", "BUY", 100.0, 99.0, 102.0, 1.0)], bars, _spec())
    trade = result["trades"][0]
    assert trade["status"] == "CLOSED"
    assert trade["exit_reason"] == "TARGET"
    assert trade["swap"] == 1.0
    assert trade["net_pnl"] == 1.0


def test_replay_records_mfe_and_mae_from_completed_bars():
    bars = [
        {"low": 99.0, "high": 100.5, "close": 100.0},
        {"low": 98.5, "high": 101.5, "close": 101.0},
        {"low": 100.0, "high": 102.0, "close": 101.5},
    ]
    trade = replay(
        [ReplayIntent("mfe-mae", "BUY", 100.0, 98.0, 102.0, 1.0)],
        bars,
        _spec(),
    )["trades"][0]
    assert trade["status"] == "CLOSED"
    assert trade["mfe_price"] == 2.0
    assert trade["mae_price"] == 1.5
    assert trade["mfe_currency"] == 2.0
    assert trade["mae_currency"] == 1.5
    assert trade["mfe_r"] == 1.0
    assert trade["mae_r"] == 0.75


def test_replay_records_sell_mfe_and_mae_with_correct_direction():
    bars = [
        {"low": 99.5, "high": 101.0, "close": 100.0},
        {"low": 97.0, "high": 100.5, "close": 98.0},
        {"low": 96.0, "high": 99.0, "close": 97.0},
    ]
    trade = replay(
        [ReplayIntent("sell-mfe-mae", "SELL", 100.0, 102.0, 96.0, 1.0)],
        bars,
        _spec(),
    )["trades"][0]
    assert trade["status"] == "CLOSED"
    assert trade["mfe_price"] == 4.0
    assert trade["mae_price"] == 1.0
    assert trade["mfe_r"] == 2.0
    assert trade["mae_r"] == 0.5


def test_replay_rejects_invalid_geometry_and_nonfinite_intent():
    intents = [
        ReplayIntent("bad-levels", "BUY", 100.0, 101.0, 102.0, 1.0),
        ReplayIntent("nan", "BUY", math.nan, 99.0, 102.0, 1.0),
    ]
    bars = [{"low": 99.0, "high": 103.0, "close": 100.0}]
    result = replay(intents, bars, _spec())
    assert [trade["status"] for trade in result["trades"]] == ["REJECTED", "REJECTED"]
    assert result["trades"][0]["reason"] == "invalid stop/target geometry"
    assert result["trades"][1]["reason"] == "invalid intent"


def test_replay_rejects_malformed_ohlc_before_scoring():
    with pytest.raises(ValueError, match="inconsistent OHLC"):
        replay([], [{"low": 101.0, "high": 100.0, "close": 100.5}], _spec())


@pytest.mark.parametrize("overrides, message", [
    ({"spread_price": math.nan}, "non-finite"),
    ({"latency_bars": 1.5}, "non-negative integer"),
    ({"closed_weekends": 1}, "boolean"),
])
def test_replay_rejects_malformed_spec(overrides, message):
    with pytest.raises(ValueError, match=message):
        replay([], [], _spec(**overrides))


def test_replay_rejects_malformed_intent_indices_without_crashing():
    result = replay(
        [ReplayIntent("bad-index", "BUY", 100.0, 99.0, 102.0, 1.0, submit_index=math.nan)],
        [{"low": 99.0, "high": 103.0, "close": 100.0}],
        _spec(),
    )
    assert result["trades"][0]["status"] == "REJECTED"
    assert result["trades"][0]["reason"] == "invalid intent"
