import concurrent.futures
import json
import os
import threading
import time
from typing import Any, Dict
import pytest

from core.order_execution import PaperBroker
from core.order_gateway import OrderGateway, OrderIntent, PaperAdapter, configure_default_gateway, get_default_gateway
from core.trading_safety import TradingStore, ValidationResult
from operations.autonomy import AutonomyManager


def test_probe_p9_2000_controls_drained_exactly_once(tmp_path):
    """Port of probe P9: 2,000 controls are drained cross-thread/process exactly once each."""
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(path=db_path)

    # Insert 2,000 controls
    total = 2000
    for i in range(total):
        store.create_operator_control(
            action="pause" if i % 2 == 0 else "resume",
            actor=f"operator_{i}",
            source="probe_p9",
            idempotency_key=f"ctrl_{i}",
        )

    # Concurrently drain with multiple workers using CAS claim_pending_operator_controls
    drained_ids = []
    lock = threading.Lock()

    def worker():
        local_store = TradingStore(path=db_path)
        while True:
            batch = local_store.claim_pending_operator_controls(limit=50)
            if not batch:
                break
            with lock:
                drained_ids.extend([c["id"] for c in batch])
            time.sleep(0.001)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly 2,000 drained, no duplicates, no missing
    assert len(drained_ids) == total
    assert len(set(drained_ids)) == total


def test_cas_claim_two_drainers_one_acknowledgement(tmp_path):
    """Compare-and-set claim: two concurrent drainers produce exactly one acknowledgment."""
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(path=db_path)
    cid = store.create_operator_control(action="flatten", actor="op", source="test")

    drainer1_got = []
    drainer2_got = []
    barrier = threading.Barrier(2)

    def drainer(collector):
        s = TradingStore(path=db_path)
        barrier.wait()
        claimed = s.claim_pending_operator_controls(limit=10)
        collector.extend(claimed)

    t1 = threading.Thread(target=drainer, args=(drainer1_got,))
    t2 = threading.Thread(target=drainer, args=(drainer2_got,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    total_claimed = len(drainer1_got) + len(drainer2_got)
    assert total_claimed == 1
    claimed_item = (drainer1_got or drainer2_got)[0]
    assert claimed_item["id"] == cid
    assert claimed_item["status"] == "ACKNOWLEDGED"


def test_paper_flatten_positions_and_orders(tmp_path):
    """Paper flatten: 3 open positions + 1 pending order -> closed 3, cancelled 1."""
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(path=db_path)
    paper = PaperBroker()

    # Create 3 paper positions
    paper.market_order("EURUSD", "BUY", 0.01, 1.0500, sl=1.0400, tp=1.0600, comment="test1", magic=460001)
    paper.market_order("GBPUSD", "SELL", 0.02, 1.2500, sl=1.2600, tp=1.2400, comment="test2", magic=460001)
    paper.market_order("USDJPY", "BUY", 0.01, 150.00, sl=149.00, tp=151.00, comment="test3", magic=460001)
    assert len(paper.positions) == 3

    # Create 1 paper pending order
    paper.pending_order("EURUSD", "buy_limit", 1.0450, 0.01, sl=1.0400, tp=1.0600, comment="test_pending", magic=460001)
    assert len(paper.orders) == 1

    gateway = OrderGateway(store=store, mode="PAPER", adapter=PaperAdapter(paper))

    outcome = gateway.execute_flatten(owned_magics={460001})
    assert outcome.closed == 3
    assert outcome.cancelled == 1
    assert outcome.failed == 0
    assert outcome.remaining == 0
    assert len(paper.positions) == 0
    assert len(paper.orders) == 0

    outcome_dict = outcome.to_dict()
    assert outcome_dict["closed"] == 3
    assert outcome_dict["cancelled"] == 1
    assert outcome_dict["remaining"] == 0


def test_flatten_vs_concurrent_entry_submit(tmp_path):
    """While gateway is flattening, concurrent entry is refused with 'flattening in progress'."""
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(path=db_path)
    paper = PaperBroker()
    gateway = OrderGateway(store=store, mode="PAPER", adapter=PaperAdapter(paper))

    gateway.flattening = True
    vr = ValidationResult(approved=True, reasons=[], confluence_score=0.9, setup_key="setup_k", created_at="2026-10-05T00:00:00Z")
    intent = OrderIntent(
        intent_key="intent_1", symbol="EURUSD", side="BUY", volume=0.01, price=1.0500,
        sl=1.0400, tp=1.0600, risk_amount=10.0, setup_key="setup_k", validation_result=vr, account_equity=1000.0
    )

    res = gateway.submit_entry(intent)
    assert not res.success
    assert "flattening in progress" in str(res.error)


def test_autonomy_gate_exception_fails_closed_to_hold(tmp_path, monkeypatch):
    """An exception inside trading_allowed causes execute_cycle to fail closed to HOLD."""
    from intelligence.llm_orchestrator import LLMOrchestrator
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(path=db_path)

    # Monkeypatch AutonomyManager to raise
    def broken_trading_allowed(*args, **kwargs):
        raise RuntimeError("simulated autonomy circuit database corruption")

    monkeypatch.setattr(AutonomyManager, "trading_allowed", broken_trading_allowed)

    orch = object.__new__(LLMOrchestrator)
    orch._cycle_local = threading.local()
    orch.store = store

    decision, confidence = orch.execute_cycle(symbol="EURUSD", timeframe="M15", cycle_num=1, total_cycles=1)
    assert decision["action"] == "HOLD"
    assert "autonomy gate error" in decision.get("reasoning", "")
    assert confidence == 0.0


def test_api_session_actor_and_typed_confirmation(tmp_path, monkeypatch):
    """API derives actor from session (PH3-N3), requires typed confirmation, and emits CONTROL_REQUESTED."""
    from fastapi.testclient import TestClient
    from ui.gui_api import app

    token = "secret-token-pr33"
    monkeypatch.setenv("GUI_API_TOKEN", token)

    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(path=db_path)
    from operations.autonomy import get_autonomy
    get_autonomy().attach_store(store)
    monkeypatch.setattr("core.trading_safety.TradingStore", lambda *a, **k: store)

    captured_events = []
    monkeypatch.setattr("operations.event_bridge.emit", lambda evt, payload: captured_events.append((evt, payload)))

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. POST /api/control/flatten without confirmation -> 400
    res_no_conf = client.post("/api/control/flatten", json={"actor": "malicious_spoof"}, headers=headers)
    assert res_no_conf.status_code == 400
    assert "pass confirm='FLATTEN'" in res_no_conf.json()["detail"]

    # 2. POST /api/control/killswitch without confirmation -> 400
    res_no_kill = client.post("/api/control/killswitch", json={"confirm": "WRONG"}, headers=headers)
    assert res_no_kill.status_code == 400
    assert "pass confirm='KILL'" in res_no_kill.json()["detail"]

    # 3. POST /api/control/flatten with confirm='FLATTEN'
    res_flatten = client.post("/api/control/flatten", json={"confirm": "FLATTEN", "actor": "spoofed_actor"}, headers=headers)
    assert res_flatten.status_code == 200
    flat_data = res_flatten.json()
    assert flat_data["ok"] is True
    assert flat_data["status"] == "REQUESTED"
    cid = flat_data["control_id"]
    # Actor must come from authenticated session, not payload spoof
    assert flat_data["actor"] == "authenticated_operator"

    # Event emissions: CONTROL_REQUESTED emitted, CONTROL_EXECUTED NOT emitted
    event_names = [e[0] for e in captured_events]
    assert "CONTROL_REQUESTED" in event_names
    assert "CONTROL_EXECUTED" not in event_names

    # 4. GET /api/control/{control_id} returns REQUESTED
    res_get = client.get(f"/api/control/{cid}", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["status"] == "REQUESTED"

    # 5. POST /api/control/killswitch with confirm='KILL' -> returns ACKNOWLEDGED
    res_ks = client.post("/api/control/killswitch", json={"confirm": "KILL"}, headers=headers)
    assert res_ks.status_code == 200
    ks_data = res_ks.json()
    assert ks_data["status"] == "ACKNOWLEDGED"
    assert store.is_kill_switch_active() is True
