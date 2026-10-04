import pytest
import os
import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock

from security_audit import audit as run_security_audit
from broker_preflight import has_contract_changed, fingerprint, validate_envelope
from execution_policy import load as load_policy, candidate_template, validate as validate_policy
from trade_manager import translate_retcode, TradeManager, RETCODE_MESSAGES
from readiness_report import build as generate_readiness_report
from trading_safety import TradingStore


def test_security_audit_scans_all_providers_and_routesme_tokens(tmp_path):
    # Test assignment scanning for TokenRouter and RoutesMe keys
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TOKENROUTER_KEY=sk-prod12345678901234567890\n"  # secret-scan: allow
        "ROUTESME_KEY=rm-prod12345678901234567890\n",  # secret-scan: allow
        encoding="utf-8"
    )
    result = run_security_audit(root=tmp_path)
    assert result["status"] == "REVIEW_REQUIRED"
    all_findings = result.get("credential_findings", []) + result.get("non_production_findings", [])
    assert len(all_findings) >= 1
    found_types = [t for item in all_findings for t in item.get("types", [])]
    assert "TOKENROUTER_KEY" in found_types or "ROUTESME_KEY" in found_types


def test_broker_preflight_contract_changed_detection():
    pf1 = {
        "fingerprint": "abc123hash",
        "report": {
            "account": {"login": 12345, "server": "Demo"},
            "symbols": {"EURUSD": {"digits": 5, "point": 0.00001}}
        }
    }
    pf2 = {
        "fingerprint": "abc123hash",
        "report": {
            "account": {"login": 12345, "server": "Demo"},
            "symbols": {"EURUSD": {"digits": 5, "point": 0.00001}}
        }
    }
    pf3 = {
        "fingerprint": "def456changedhash",
        "report": {
            "account": {"login": 12345, "server": "Demo"},
            "symbols": {"EURUSD": {"digits": 5, "point": 0.00001, "trade_stops_level": 20}}
        }
    }
    assert has_contract_changed(pf1, pf2) is False
    assert has_contract_changed(pf1, pf3) is True


def test_execution_policy_rejects_changed_broker_contract(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    
    # Store preflight with changed = True
    preflight = {
        "fingerprint": "fp_current",
        "changed": True,
        "validation": {"valid": True},
        "report": {"account": {}, "symbols": {}}
    }
    store.set_state("broker_preflight", preflight)
    
    policy = {
        "version": "v1", "symbol": "EURUSD", "broker_fingerprint": "fp_current",
        "max_spread_price": 0.0002, "min_stop_distance_price": 0.0001,
        "entry_tolerance_min_price": 0.0001, "entry_tolerance_atr_fraction": 0.1,
        "expected_slippage_price": 0.0001, "commission_per_volume": 7.0,
        "expected_swap_money": 1.0, "expires_seconds": 900, "max_tick_age_ms": 5000,
        "status": "APPROVED", "reviewed_at": "2026-08-08T00:00:00+00:00"
    }
    store.set_state("execution_policies", {"EURUSD": policy})
    
    # Load policy must fail closed because contract changed
    with pytest.raises(ValueError, match="broker contract changed"):
        load_policy(store, "EURUSD")


def test_trade_manager_retcode_translation_and_logging(tmp_path):
    assert translate_retcode(10009) == "DONE"
    assert translate_retcode(10004) == "REQUOTE"
    assert translate_retcode(10006) == "REJECT"
    assert translate_retcode(10013) == "INVALID_PRICE"
    assert translate_retcode(10014) == "INVALID_STOPS"
    assert translate_retcode(99999) == "RETCODE_99999"
    assert translate_retcode(None) == "UNKNOWN"

    store = TradingStore(str(tmp_path / "state.db"))
    manager = TradeManager(client=None, risk_manager=None, store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    
    # Log management event with retcode
    manager._record_management(12345, "SL_MODIFY_ACCEPTED", {"old_sl": 1.09, "new_sl": 1.095}, retcode=10009)
    
    with store._connect() as con:
        rows = con.execute("SELECT * FROM broker_execution_reports WHERE position_ticket=12345").fetchall()
    assert len(rows) == 1
    assert rows[0]["retcode"] == 10009
    payload = json.loads(rows[0]["payload"])
    assert payload["retcode_label"] == "DONE"
    assert payload["manager"] == "TradeManager"


def test_readiness_report_aggregation(tmp_path):
    rep = generate_readiness_report(str(tmp_path / "readiness.db"))
    assert "status" in rep
    assert "blockers" in rep
    assert "gates" in rep
    assert rep["execution_authority"] is False
