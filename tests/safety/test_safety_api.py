"""Tests for GET /api/safety endpoint (PR 3.7 / Part 1 §7.2)."""
import os
import tempfile
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from ui.gui_api import app
from core.file_lock import FileLock
from core.trading_safety import TradingStore


@pytest.fixture
def auth_token(monkeypatch):
    token = "test-safety-token-xyz"
    monkeypatch.setenv("GUI_API_TOKEN", token)
    return token


@pytest.fixture
def client():
    return TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    db_file = str(tmp_path / "safety_test.db")
    lock_file = str(tmp_path / "engine.lock")
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", db_file)
    monkeypatch.setenv("LLM_TRADER_LOCK_PATH", lock_file)
    monkeypatch.setenv("LLM_TRADER_MODE", "PAPER")
    return {"db_file": db_file, "lock_file": lock_file}


def test_safety_endpoint_unauthorized(client):
    """GET /api/safety without auth token returns 401."""
    resp = client.get("/api/safety")
    assert resp.status_code == 401


def test_safety_endpoint_engine_down(client, auth_token, temp_env):
    """When engine lock is not held, engine.is_running is False and all other fields are None."""
    headers = {"Authorization": f"Bearer {auth_token}"}
    resp = client.get("/api/safety", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["engine"]["is_running"] is False
    assert data["engine"]["lock_held"] is False
    assert data["engine"]["pid"] is None
    assert "as_of" in data["engine"]

    # All other fields must be None when engine is down
    assert data["mode"] is None
    assert data["autonomy"] is None
    assert data["kill_switch"] is None
    assert data["news_latch"] is None
    assert data["daily_pnl"] is None
    assert data["tick_age"] is None
    assert data["bridge"] is None
    assert data["llm_circuit"] is None
    assert data["open_controls"] is None


def test_safety_endpoint_engine_up(client, auth_token, temp_env):
    """When engine lock is held, all safety telemetry is populated with timestamps."""
    lock = FileLock(temp_env["lock_file"])
    assert lock.acquire(non_blocking=True) is True
    lock.write_pid()

    try:
        store = TradingStore(path=temp_env["db_file"])
        # H2: journal scope is the honest mode source — seed PAPER scope so
        # the strip reads it back from the journal (not from env).
        store = TradingStore(path=temp_env["db_file"], mode="PAPER")
        # H2: seed persisted risk state; pnl/limit stay null without a broker.
        store.update_state("risk_manager_state", lambda _c: {
            "start_equity": 10000.0, "trading_day": "2026-10-05",
            "day_basis": "broker", "flatten_reason": "",
        })
        from operations.agent_operations import AgentOperations
        ops = AgentOperations(db_path=temp_env["db_file"])
        run_id = ops.start_run("test_agent")

        headers = {"Authorization": f"Bearer {auth_token}"}
        # Enter lifespan so the real startup path attaches the journal store
        # (B1 wiring — autonomy/latch snapshots are journal-backed, not guesses).
        with client:
            resp = client.get("/api/safety", headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["engine"]["is_running"] is True
        assert data["engine"]["lock_held"] is True
        assert data["engine"]["pid"] == os.getpid()
        assert data["engine"]["heartbeat_age_s"] is not None
        assert data["engine"]["heartbeat_age_s"] >= 0.0

        assert data["mode"]["value"] == "PAPER"
        assert "as_of" in data["mode"]

        assert data["autonomy"] is not None
        assert "level" in data["autonomy"]

        assert data["kill_switch"] is not None
        assert data["kill_switch"]["active"] is False

        assert data["news_latch"] is not None
        assert data["news_latch"]["latched"] is False

        assert data["daily_pnl"] is not None
        assert "pnl" in data["daily_pnl"]
        assert "breaker_tripped" in data["daily_pnl"]

        assert data["tick_age"] is not None
        assert "symbols" in data["tick_age"]

        assert data["bridge"] is not None
        assert "connected" in data["bridge"]

        assert data["llm_circuit"] is not None
        assert "state" in data["llm_circuit"]

        assert data["open_controls"] is not None
        assert data["open_controls"]["count"] == 0
        assert data["open_controls"]["requests"] == []
    finally:
        lock.release()


def test_safety_endpoint_active_latches_and_controls(client, auth_token, temp_env):
    """When kill switch, news latch, or operator controls are active, API reflects them."""
    lock = FileLock(temp_env["lock_file"])
    assert lock.acquire(non_blocking=True) is True
    lock.write_pid()

    try:
        store = TradingStore(path=temp_env["db_file"])
        store.set_kill_switch(True, "Manual emergency stop")
        store.create_operator_control(
            action="flatten",
            actor="operator:alice",
            source="deck_ui",
            idempotency_key="flatten-req-1",
        )

        from data.kill_switch_latch import get_kill_switch_latch
        latch = get_kill_switch_latch()
        # Attach-once (H5): bind this test's journal explicitly, like
        # production boot does — entries_blocked and the endpoint never
        # re-point a live backend.
        latch.attach_store(store)
        latch.apply_market_data(halt_flag=True)

        headers = {"Authorization": f"Bearer {auth_token}"}
        # Enter lifespan so the real startup path attaches the journal store
        # (B1 wiring — autonomy/latch snapshots are journal-backed, not guesses).
        with client:
            resp = client.get("/api/safety", headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["engine"]["is_running"] is True
        assert data["kill_switch"]["active"] is True
        assert "Manual emergency stop" in data["kill_switch"]["reason"]

        assert data["news_latch"]["latched"] is True
        assert data["news_latch"]["since"] is not None

        assert data["open_controls"]["count"] == 1
        assert len(data["open_controls"]["requests"]) == 1
        assert data["open_controls"]["requests"][0]["action"] == "flatten"
        assert data["open_controls"]["requests"][0]["status"] == "REQUESTED"
    finally:
        # Reset latch
        latch.clear(reason="test_cleanup", actor="test")
        lock.release()

