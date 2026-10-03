from datetime import datetime, timezone
from types import SimpleNamespace
import math

import pytest

from execution_planning import build_pending_entry, validate_emergency_exit_request, validate_quote


def _entry_kwargs():
    return {
        "symbol": "EURUSD", "side": "BUY", "entry": 99.0,
        "bid": 99.9, "ask": 100.1, "quote_time_msc": int(datetime.now(timezone.utc).timestamp() * 1000),
        "entry_tolerance": 0.1, "min_entry_distance": 0.0,
        "max_spread": 1.0, "expires_seconds": 60,
    }


@pytest.mark.parametrize("field", ["entry_tolerance", "min_entry_distance", "max_spread", "expires_seconds"])
def test_pending_entry_rejects_nonfinite_limits(field):
    values = _entry_kwargs()
    values[field] = math.nan
    with pytest.raises(ValueError):
        build_pending_entry(**values)


def test_pending_entry_rejects_nonfinite_quote_timestamp():
    values = _entry_kwargs()
    values["quote_time_msc"] = math.inf
    with pytest.raises(ValueError, match="quote timestamp"):
        build_pending_entry(**values)


@pytest.mark.parametrize("field,value", [("symbol", " "), ("quote_time_msc", 0),
                                           ("expires_seconds", 60.5)])
def test_pending_entry_rejects_missing_or_ambiguous_identity_and_timing(field, value):
    values = _entry_kwargs()
    values[field] = value
    with pytest.raises(ValueError):
        build_pending_entry(**values)


def test_valid_pending_entry_and_quote_are_accepted():
    planned = build_pending_entry(**_entry_kwargs())
    assert planned.order_type == "buy_limit"
    tick = SimpleNamespace(
        bid=99.9, ask=100.1,
        time_msc=int(datetime.now(timezone.utc).timestamp() * 1000),
    )
    info = SimpleNamespace(trade_mode=1)
    accepted, reason, quote = validate_quote(tick=tick, info=info, max_tick_age_ms=5000, max_spread=1.0)
    assert accepted is True
    assert reason == "quote valid"
    assert quote["spread"] == pytest.approx(0.2)


def test_validate_quote_rejects_nonfinite_values_and_limits():
    tick = SimpleNamespace(bid=math.nan, ask=100.1, time_msc=0)
    info = SimpleNamespace(trade_mode=1)
    accepted, reason, _ = validate_quote(tick=tick, info=info, max_tick_age_ms=5000, max_spread=1.0)
    assert accepted is False
    assert "malformed" in reason

    tick = SimpleNamespace(bid=99.9, ask=100.1, time_msc=0)
    accepted, reason, _ = validate_quote(tick=tick, info=info, max_tick_age_ms=math.nan, max_spread=1.0)
    assert accepted is False
    assert "malformed" in reason


def test_emergency_exit_requires_owned_ticket_magic_and_reason():
    request = validate_emergency_exit_request(
        position_ticket=123, symbol="EURUSD", side="buy", volume=0.1,
        magic=9001, reason="kill switch / stale quote",
    )
    assert request["valid"] is True
    assert request["execution_authority"] is False


@pytest.mark.parametrize("field,value", [
    ("position_ticket", 0), ("volume", math.nan), ("magic", 0),
    ("reason", ""), ("side", "hold"), ("symbol", ""),
])
def test_emergency_exit_rejects_malformed_request(field, value):
    values = {
        "position_ticket": 123, "symbol": "EURUSD", "side": "BUY",
        "volume": 0.1, "magic": 9001, "reason": "risk kill switch",
    }
    values[field] = value
    assert validate_emergency_exit_request(**values)["valid"] is False
