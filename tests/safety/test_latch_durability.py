"""Durable D9 kill-switch latch and unified kill-switch authority tests.

Covers PR 3.2 requirements:
- Two-process stress test (zero torn reads / corruption)
- Corrupt database / checkpoint record fails closed to latched
- Missing VIX does not clear; calm VIX clears with cooldown
- Operator clear requires reason + confirmation
- DeterministicValidator rejects entries with 'news kill-switch latched'
- OrderGateway refuses entries and allows exits under latch
- API endpoints /api/latch/status and /api/latch/clear
"""
import concurrent.futures
import json
import math
import os
import tempfile
import time
from unittest.mock import MagicMock

import pytest

from core.kill_switch import entries_blocked
from core.order_gateway import GatewayResult, OrderGateway
from core.trading_safety import DeterministicValidator, TradeProposal, TradingStore
from data.kill_switch_latch import KillSwitchLatch, get_kill_switch_latch


def test_missing_vix_does_not_clear(tmp_path):
    path = str(tmp_path / "latch.json")
    latch = KillSwitchLatch(path=path)
    latch.apply_market_data(halt_flag=True)
    assert latch.snapshot()["latched"] is True

    # Missing / None VIX
    snap = latch.apply_market_data(vix=None, halt_flag=False)
    assert snap["latched"] is True

    # NaN VIX
    snap = latch.apply_market_data(vix=float("nan"), halt_flag=False)
    assert snap["latched"] is True

    # High VIX
    snap = latch.apply_market_data(vix=40.0, halt_flag=False)
    assert snap["latched"] is True

    # Calm VIX clears
    snap = latch.apply_market_data(vix=20.0, halt_flag=False)
    assert snap["latched"] is False
    assert snap["cooldown_active"] is True
    assert "market_data_calm" in snap["clear_reason"]


def test_operator_clear(tmp_path):
    path = str(tmp_path / "latch.json")
    latch = KillSwitchLatch(path=path)
    latch.apply_market_data(halt_flag=True)
    assert latch.snapshot()["latched"] is True

    snap = latch.clear(reason="manual investigation complete", actor="chief_risk_officer")
    assert snap["latched"] is False
    assert snap["cooldown_active"] is True
    assert snap["clear_reason"] == "operator_clear:chief_risk_officer:manual investigation complete"


def test_corrupt_store_record_fails_closed_to_latched(tmp_path):
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(db_path)
    # Write a corrupt non-dict or invalid structure into state
    with store._connect() as con:
        con.execute(
            "INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?)",
            ("d9_kill_switch_latch", '"corrupt_string_not_dict"', "2026-10-05T00:00:00Z"),
        )
    latch = KillSwitchLatch(path=str(tmp_path / "latch.json"), store=store)
    snap = latch.snapshot()
    assert snap["latched"] is True
    assert snap["trigger_source"] == "checkpoint_unreadable"


def test_unreadable_store_fails_closed_to_latched(tmp_path):
    store = MagicMock()
    store.get_state.side_effect = RuntimeError("disk I/O error")
    latch = KillSwitchLatch(path=str(tmp_path / "latch.json"), store=store)
    snap = latch.snapshot()
    assert snap["latched"] is True
    assert snap["trigger_source"] == "checkpoint_unreadable"


def _worker_observe(args):
    db_path, latch_file, worker_id = args
    store = TradingStore(db_path)
    latch = KillSwitchLatch(path=latch_file, store=store)
    for i in range(25):
        latch.observe(1, [f"Worker {worker_id} headline {i}"])
        snap = latch.snapshot()
        assert isinstance(snap, dict)
        assert "latched" in snap
    return True


def test_multi_process_stress_no_corruption(tmp_path):
    """Port of probe P5: concurrent processes reading/writing the latch produce no torn reads."""
    db_path = str(tmp_path / "concurrent_state.db")
    latch_file = str(tmp_path / "concurrent_latch.json")
    TradingStore(db_path)  # initialize schema

    tasks = [(db_path, latch_file, i) for i in range(4)]
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_worker_observe, tasks))
    assert all(results)

    # Check that final JSON file is valid JSON
    assert os.path.exists(latch_file)
    with open(latch_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert data.get("latched") is True


def test_entries_blocked_unified_authority(tmp_path):
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(db_path)
    latch = KillSwitchLatch(path=str(tmp_path / "latch.json"), store=store)

    # 1. Initially unlatched and no kill switch
    assert latch.snapshot()["latched"] is False
    blocked, reason = entries_blocked(store)
    assert not blocked
    assert reason == ""

    # 2. Manual kill switch active
    store.set_kill_switch(True, "manual test")
    blocked, reason = entries_blocked(store)
    assert blocked
    assert reason == "kill switch active"

    # Turn off manual kill switch
    store.set_kill_switch(False, "clear test")
    blocked, reason = entries_blocked(store)
    assert not blocked

    # 3. Latch active
    latch.apply_market_data(halt_flag=True)
    assert latch.snapshot()["latched"] is True
    blocked, reason = entries_blocked(store)
    assert blocked
    assert reason == "news kill-switch latched"

    # 4. Operator clear latch
    latch.clear("operator clear", "tester")
    blocked, reason = entries_blocked(store)
    assert not blocked


def test_validator_rejects_with_news_kill_switch_latched(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(db_path)
    latch_path = str(tmp_path / "latch.json")
    latch = KillSwitchLatch(path=latch_path, store=store)
    monkeypatch.setattr("core.kill_switch.get_kill_switch_latch", lambda: latch)

    validator = DeterministicValidator(store=store, min_confluence=0.0, min_rr=1.0)
    proposal = TradeProposal(
        strategy="macro",
        symbol="EURUSD",
        side="BUY",
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        confidence=0.8,
        features={"score": 10},
    )
    market = {
        "market_open": True,
        "symbol_tradable": True,
        "spread": 1.0,
        "max_spread": 2.0,
        "tick_age_ms": 100,
        "max_tick_age_ms": 1000,
        "clock_ok": True,
        "completed_candle": True,
        "breakout_event": False,
        "required_data_ok": True,
        "stop_distance": 50,
        "min_stop_distance": 10,
        "high_impact_news": False,
        "session_allowed": True,
        "liquidity_ok": True,
        "microstructure_ok": True,
        "setup_cooldown_seconds": 900.0,
    }

    # Unlatched -> approved
    res = validator.validate(proposal, market)
    assert res.approved is True

    # Engage latch
    latch.apply_market_data(halt_flag=True)
    res = validator.validate(proposal, market)
    assert res.approved is False
    assert "news kill-switch latched" in res.reasons


def test_gateway_refuses_entry_and_allows_exit(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(db_path)
    latch_path = str(tmp_path / "latch.json")
    latch = KillSwitchLatch(path=latch_path, store=store)
    monkeypatch.setattr("core.kill_switch.get_kill_switch_latch", lambda: latch)

    adapter = MagicMock()
    adapter.close_position.return_value = {"retcode": 10009}
    gw = OrderGateway(store=store, mode="PAPER", adapter=adapter)

    intent = MagicMock()
    intent.side = "BUY"
    intent.sl = 1.0900
    intent.volume = 0.1
    intent.intent_id = "test-intent"
    intent.validation_result = MagicMock(approved=True, setup_key="test_setup")

    # Engage latch
    latch.apply_market_data(halt_flag=True)

    # Submit entry -> refused by latch
    res = gw.submit_entry(intent)
    assert res.success is False
    assert "news kill-switch latched" in res.error

    # Submit exit -> allowed under latch
    res_exit = gw.submit_exit(
        ticket=999,
        symbol="EURUSD",
        volume=0.1,
        comment="risk_reduction",
    )
    assert res_exit.success is True


def test_api_latch_status_and_clear(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from ui.gui_api import app

    token = "secret-test-token-123"
    monkeypatch.setenv("GUI_API_TOKEN", token)

    latch_path = str(tmp_path / "latch.json")
    latch = KillSwitchLatch(path=latch_path)
    latch.apply_market_data(halt_flag=True)
    monkeypatch.setattr("data.kill_switch_latch.get_kill_switch_latch", lambda: latch)

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    headers = {"Authorization": f"Bearer {token}"}

    # GET /api/latch/status
    res = client.get("/api/latch/status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["latched"] is True

    # POST /api/latch/clear with missing confirmation -> 400
    res_fail1 = client.post("/api/latch/clear", json={"reason": "test"}, headers=headers)
    assert res_fail1.status_code == 400

    # POST /api/latch/clear with missing reason -> 400
    res_fail2 = client.post("/api/latch/clear", json={"confirm": "CLEAR_LATCH"}, headers=headers)
    assert res_fail2.status_code == 400

    # POST /api/latch/clear with valid payload -> 200
    res_ok = client.post(
        "/api/latch/clear",
        json={"confirm": "CLEAR_LATCH", "reason": "operator investigated"},
        headers=headers,
    )
    assert res_ok.status_code == 200
    body = res_ok.json()
    assert body["ok"] is True
    assert body["result"]["latched"] is False
    assert "operator_clear" in body["result"]["clear_reason"]
