from datetime import datetime, timezone
from types import SimpleNamespace
import math

import pytest

from broker_preflight import persist_preflight, validate_envelope
from execution_policy import load, candidate_for_store, approve_for_store
from trading_safety import TradingStore


def _account():
    return SimpleNamespace(login=1, server="demo", currency="USD", leverage=100,
                           balance=10000, equity=10000, margin=0, margin_free=10000,
                           margin_level=None, margin_so_mode=0, margin_so_call=50,
                           margin_so_so=30, trade_allowed=True, trade_expert=True)


def _symbol():
    return SimpleNamespace(name="XAUUSD", trade_mode=1, visible=True, point=0.01,
                           digits=2, spread=20, trade_contract_size=100,
                           trade_tick_size=0.01, trade_tick_value=1.0, volume_min=0.01,
                           volume_max=10.0, volume_step=0.01, trade_stops_level=50,
                           trade_freeze_level=10, filling_mode=1, order_mode=1,
                           expiration_mode=1, margin_initial=0, margin_maintenance=0,
                           currency_base="XAU", currency_profit="USD",
                           currency_margin="USD")


def test_preflight_requires_complete_authenticated_contract(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    fake_mt5 = SimpleNamespace(account_info=_account, symbol_info=lambda symbol: _symbol(),
                               symbol_info_tick=lambda symbol: SimpleNamespace(bid=2300.0, ask=2300.2, time_msc=1))
    envelope = persist_preflight(store, fake_mt5, ["XAUUSD"])
    assert envelope["validation"]["valid"] is True
    assert validate_envelope(envelope, ["XAUUSD"])["status"] == "READY"

    policy = {
        "version": "execution-policy-v1", "symbol": "XAUUSD",
        "broker_fingerprint": envelope["fingerprint"], "max_spread_price": 1.0,
        "min_stop_distance_price": 0.5, "entry_tolerance_min_price": 0.1,
        "entry_tolerance_atr_fraction": 0.1, "expected_slippage_price": 0.1,
        "commission_per_volume": 0.0, "expected_swap_money": 0.0,
        "expires_seconds": 900, "max_tick_age_ms": 5000, "status": "APPROVED",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    store.set_state("execution_policies", {"XAUUSD": policy})
    assert load(store, "XAUUSD")["symbol"] == "XAUUSD"

    invalid = {**envelope, "report": {**envelope["report"],
                                       "account": {**envelope["report"]["account"], "trade_allowed": False}}}
    assert validate_envelope(invalid, ["XAUUSD"])["valid"] is False


def test_policy_fails_on_broker_fingerprint_mismatch(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    store.set_state("broker_preflight", {"fingerprint": "current", "changed": False,
                                          "validation": {"valid": True}})
    policy = {
        "version": "v1", "symbol": "EURUSD", "broker_fingerprint": "old",
        "max_spread_price": 1.0, "min_stop_distance_price": 0.1,
        "entry_tolerance_min_price": 0.1, "entry_tolerance_atr_fraction": 0.1,
        "expected_slippage_price": 0.0, "commission_per_volume": 0.0,
        "expected_swap_money": 0.0, "expires_seconds": 900, "max_tick_age_ms": 5000,
        "status": "APPROVED", "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    store.set_state("execution_policies", {"EURUSD": policy})
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        load(store, "EURUSD")


def test_policy_load_requires_explicit_valid_preflight(tmp_path):
    store = TradingStore(str(tmp_path / "missing-validation.db"))
    store.set_state("broker_preflight", {"fingerprint": "current", "changed": False})
    policy = {
        "version": "v1", "symbol": "EURUSD", "broker_fingerprint": "current",
        "max_spread_price": 1.0, "min_stop_distance_price": 0.1,
        "entry_tolerance_min_price": 0.1, "entry_tolerance_atr_fraction": 0.1,
        "expected_slippage_price": 0.0, "commission_per_volume": 0.0,
        "expected_swap_money": 0.0, "expires_seconds": 900, "max_tick_age_ms": 5000,
        "status": "APPROVED", "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    store.set_state("execution_policies", {"EURUSD": policy})
    with pytest.raises(ValueError, match="preflight is incomplete"):
        load(store, "EURUSD")


def test_policy_load_requires_symbol_specific_preflight_evidence(tmp_path):
    store = TradingStore(str(tmp_path / "missing-symbol.db"))
    store.set_state("broker_preflight", {
        "fingerprint": "current", "changed": False,
        "validation": {"valid": True, "symbols": ["GBPJPY"]},
        "report": {"symbols": {"GBPJPY": {}}},
    })
    policy = {
        "version": "v1", "symbol": "EURUSD", "broker_fingerprint": "current",
        "max_spread_price": 1.0, "min_stop_distance_price": 0.1,
        "entry_tolerance_min_price": 0.1, "entry_tolerance_atr_fraction": 0.1,
        "expected_slippage_price": 0.0, "commission_per_volume": 0.0,
        "expected_swap_money": 0.0, "expires_seconds": 900, "max_tick_age_ms": 5000,
        "status": "APPROVED", "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    store.set_state("execution_policies", {"EURUSD": policy})
    with pytest.raises(ValueError, match="missing the EURUSD symbol contract"):
        load(store, "EURUSD")


def test_policy_load_rejects_malformed_persisted_preflight(tmp_path):
    store = TradingStore(str(tmp_path / "malformed-preflight-state.db"))
    store.set_state("broker_preflight", "not-an-object")
    store.set_state("execution_policies", {"EURUSD": {
        "version": "v1", "symbol": "EURUSD", "broker_fingerprint": "current",
        "max_spread_price": 1.0, "min_stop_distance_price": 0.1,
        "entry_tolerance_min_price": 0.1, "entry_tolerance_atr_fraction": 0.1,
        "expected_slippage_price": 0.0, "commission_per_volume": 0.0,
        "expected_swap_money": 0.0, "expires_seconds": 900, "max_tick_age_ms": 5000,
        "status": "APPROVED", "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }})
    with pytest.raises(ValueError, match="malformed"):
        load(store, "EURUSD")


def test_policy_candidate_and_reviewed_approval_are_preflight_bound(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    with pytest.raises(ValueError, match="preflight is unavailable"):
        candidate_for_store(store, "XAUUSD")

    fake_mt5 = SimpleNamespace(account_info=_account, symbol_info=lambda symbol: _symbol(),
                               symbol_info_tick=lambda symbol: SimpleNamespace(bid=2300.0, ask=2300.2, time_msc=1))
    envelope = persist_preflight(store, fake_mt5, ["XAUUSD"])
    candidate = candidate_for_store(store, "XAUUSD")
    assert candidate["status"] == "CANDIDATE"
    assert candidate["broker_fingerprint"] == envelope["fingerprint"]
    assert candidate["execution_authority"] is False

    completed = {**candidate,
                 "max_spread_price": 1.0, "min_stop_distance_price": 0.5,
                 "entry_tolerance_min_price": 0.1, "entry_tolerance_atr_fraction": 0.1,
                 "expected_slippage_price": 0.1, "commission_per_volume": 0.0,
                 "expected_swap_money": 0.0}
    approved = approve_for_store(store, "XAUUSD", completed, "reviewer-1")
    assert approved["status"] == "APPROVED"
    assert approved["reviewed_by"] == "reviewer-1"
    assert approved["execution_authority"] is False
    assert load(store, "XAUUSD")["reviewed_by"] == "reviewer-1"


def test_preflight_rejects_nonfinite_and_malformed_contract_fields(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    fake_mt5 = SimpleNamespace(account_info=_account, symbol_info=lambda symbol: _symbol(),
                               symbol_info_tick=lambda symbol: SimpleNamespace(bid=2300.0, ask=2300.2, time_msc=1))
    envelope = persist_preflight(store, fake_mt5, ["XAUUSD"])

    bad_account = {**envelope, "report": {**envelope["report"],
                                           "account": {**envelope["report"]["account"], "leverage": math.nan}}}
    result = validate_envelope(bad_account, ["XAUUSD"])
    assert result["valid"] is False
    assert any("leverage" in reason for reason in result["reasons"])

    bad_symbol = {**envelope, "report": {**envelope["report"],
                                          "symbols": {"XAUUSD": {**envelope["report"]["symbols"]["XAUUSD"],
                                                                   "trade_tick_value": math.inf}}}}
    result = validate_envelope(bad_symbol, ["XAUUSD"])
    assert result["valid"] is False
    assert any("tick size/value" in reason for reason in result["reasons"])

    reversed_volume = {**envelope, "report": {**envelope["report"],
                                               "symbols": {"XAUUSD": {**envelope["report"]["symbols"]["XAUUSD"],
                                                                        "volume_min": 20.0, "volume_max": 10.0}}}}
    result = validate_envelope(reversed_volume, ["XAUUSD"])
    assert result["valid"] is False
    assert any("maximum is below minimum" in reason for reason in result["reasons"])


def test_preflight_validator_returns_invalid_for_malformed_envelope_shapes():
    malformed = validate_envelope({"report": "not-an-object", "fingerprint": "fp"}, None)
    assert malformed["valid"] is False
    assert malformed["execution_authority"] is False
    assert any("report is malformed" in reason for reason in malformed["reasons"])

    malformed_symbols = validate_envelope({"report": {"account": {}, "symbols": []},
                                            "fingerprint": "fp"}, ["EURUSD"])
    assert malformed_symbols["valid"] is False
    assert any("symbol report is malformed" in reason for reason in malformed_symbols["reasons"])
