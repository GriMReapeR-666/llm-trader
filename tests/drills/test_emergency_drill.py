"""Phase 3 Emergency Stack Exit Gate Drill (tests/drills/test_emergency_drill.py).

Drill requirements (from docs/audit/PLAN_PHASE_3_4.md §3.8):
1. Run engine loop in paper with 3 open positions and 1 pending order.
2. Flatten through API -> APPLIED {closed: 3, cancelled: 1} within 5 s; journal and paper book agree.
3. Kill switch -> next validated proposal is refused by gateway with 'kill switch'; exit order still permitted.
4. Two confirmed tripwire batches set news latch -> validator rejects with 'news kill-switch latched'.
   Operator clears latch -> entries allowed again.
5. Corrupt latch record restart -> fails closed to latched.
6. Single-instance lock -> second engine launch refused with returncode 3; clean shutdown.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.file_lock import FileLock
from core.kill_switch import entries_blocked
from core.order_execution import PaperBroker
from core.order_gateway import OrderGateway, OrderIntent, PaperAdapter
from core.trading_safety import DeterministicValidator, TradeProposal, TradingStore, ValidationResult
from data.kill_switch_latch import KillSwitchLatch
from mt5_trader import _drain_operator_controls
from ui.gui_api import app

REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)


@pytest.fixture
def auth_token(monkeypatch):
    token = "drill-secret-token-789"
    monkeypatch.setenv("GUI_API_TOKEN", token)
    return token


@pytest.fixture
def client():
    return TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.fixture
def baseline_proposal():
    return TradeProposal(
        strategy="macro",
        symbol="EURUSD",
        side="BUY",
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        confidence=0.85,
        features={"score": 10, "ema_alignment": 1, "adx": 30},
    )


@pytest.fixture
def baseline_market():
    return {
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
        "setup_cooldown_seconds": 0.0,
    }


# ---------------------------------------------------------------------------
# Drill 1 & 2: Paper Flatten through the API within 5s
# ---------------------------------------------------------------------------
@pytest.mark.drill
def test_drill_1_and_2_operator_flatten_paper_via_api(tmp_path, client, auth_token, monkeypatch):
    """Drill 1 & 2: 3 open positions + 1 pending order flattened via API within 5s.

    Journal (operator_controls) and paper book agree: closed 3, cancelled 1, remaining 0.
    """
    db_path = str(tmp_path / "drill_trading_state.db")
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", db_path)

    store = TradingStore(path=db_path)
    paper = PaperBroker()

    # 1. Populate paper broker with 3 open positions (one per bot magic) + 1 pending order
    t1 = paper.market_order("EURUSD", "BUY", 0.01, 1.0500, sl=1.0400, tp=1.0600, comment="drill_pos_1", magic=460001)
    t2 = paper.market_order("GBPUSD", "SELL", 0.02, 1.2500, sl=1.2600, tp=1.2400, comment="drill_pos_2", magic=460002)
    t3 = paper.market_order("USDJPY", "BUY", 0.01, 150.00, sl=149.00, tp=151.00, comment="drill_pos_3", magic=460003)
    t_pend = paper.pending_order("EURUSD", "buy_limit", 1.0450, 0.01, sl=1.0400, tp=1.0600, comment="drill_pend_1", magic=460002)

    assert len(paper.positions) == 3
    assert len(paper.orders) == 1

    gateway = OrderGateway(store=store, mode="PAPER", adapter=PaperAdapter(paper))

    # Mock engine orchestrator that drains controls. NOTE: no hand-wired
    # autonomy store here — the API attaches its own journal store at startup
    # (B1), so this drill exercises the real API wiring end to end.
    from core.trading_safety import BOT_MAGICS
    orch = SimpleNamespace(
        store=store,
        client=paper,
        paper_broker=paper,
        order_gateway=gateway,
        owned_magics=set(BOT_MAGICS),
    )

    # NOTE: the API attaches its own journal store at startup from
    # LLM_TRADER_JOURNAL_PATH (B1). No hand-wiring here on purpose: the
    # drill must exercise the real API wiring, including the 503 path when
    # the journal is unavailable.

    # 2. Start background engine pulse loop draining operator controls
    stop_event = threading.Event()

    def engine_pulse():
        while not stop_event.is_set():
            _drain_operator_controls(orch)
            time.sleep(0.05)

    worker = threading.Thread(target=engine_pulse, daemon=True)
    worker.start()

    start_time = time.monotonic()
    try:
        # Request flatten through API inside the lifespan context so the
        # real startup path attaches the journal store (B1 wiring, no mocks).
        with client:
            # Request flatten through API
            headers = {"Authorization": f"Bearer {auth_token}"}
            post_resp = client.post(
                "/api/control/flatten",
                headers=headers,
                json={"confirm": "FLATTEN", "idempotency_key": "drill-flatten-idemp-1"},
            )
            assert post_resp.status_code == 200, post_resp.text
            resp_data = post_resp.json()
            assert resp_data["ok"] is True
            cid = resp_data.get("control_id")
            assert cid is not None, f"flatten issued without control_id: {resp_data}"

            # Poll status until APPLIED with timeout SLA of 5.0 seconds
            applied = False
            final_ctrl: dict[str, Any] = {}
            while time.monotonic() - start_time < 5.0:
                get_resp = client.get(f"/api/control/{cid}", headers=headers)
                assert get_resp.status_code == 200
                ctrl_data = get_resp.json()
                if ctrl_data.get("status") == "APPLIED":
                    applied = True
                    final_ctrl = ctrl_data
                    break
                time.sleep(0.05)

        elapsed = time.monotonic() - start_time
        assert applied is True, f"Flatten did not reach APPLIED within 5.0s (elapsed={elapsed:.2f}s)"
        assert elapsed < 5.0, f"Flatten exceeded 5s SLA: {elapsed:.2f}s"

        # Verify outcome details
        res = final_ctrl.get("result", {})
        assert res.get("closed") == 3
        assert res.get("cancelled") == 1
        assert res.get("remaining") == 0

        # Verify journal and paper book agree
        assert len(paper.positions) == 0
        assert len(paper.orders) == 0

    finally:
        stop_event.set()
        worker.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Drill 2b: Live Flatten on FakeMT5 across all bot magics (B2/B3/M4)
# ---------------------------------------------------------------------------
@pytest.mark.drill
def test_drill_2b_live_flatten_fake_mt5_all_magics(tmp_path, monkeypatch):
    """Drill 2b: live flatten on FakeMT5 closes owned 460001/2/3, spares manual.

    Uses the real drain path (_drain_operator_controls) with a listable
    broker client (B2) and the central BOT_MAGICS set (B3). A manual-magic
    position must survive an owned-only flatten.
    """
    from types import SimpleNamespace as NS
    from tests.fakes.fake_mt5 import FakeMT5

    db_path = str(tmp_path / "drill_live_state.db")
    store = TradingStore(path=db_path)
    fake = FakeMT5()
    fake.positions = [
        NS(ticket=101, symbol="EURUSD", volume=0.10, type=0, magic=460001),
        NS(ticket=102, symbol="GBPUSD", volume=0.10, type=1, magic=460002),
        NS(ticket=103, symbol="USDJPY", volume=0.10, type=0, magic=460003),
        NS(ticket=104, symbol="EURUSD", volume=0.10, type=0, magic=999999),
    ]
    fake.orders = [
        NS(ticket=201, symbol="EURUSD", magic=460002),
        NS(ticket=202, symbol="EURUSD", magic=999999),
    ]

    from core.order_gateway import LiveMT5Adapter, OrderGateway
    from core.trading_safety import BOT_MAGICS
    gateway = OrderGateway(store=store, mode="LIVE", adapter=LiveMT5Adapter(fake))

    cid = store.create_operator_control(action="flatten", actor="operator", source="drill-2b")
    orch = SimpleNamespace(
        store=store,
        client=fake,
        paper_broker=None,
        order_gateway=gateway,
        owned_magics=set(BOT_MAGICS),
    )
    _drain_operator_controls(orch)

    ctrl = store.get_operator_control(cid)
    assert ctrl is not None
    assert ctrl["status"] == "APPLIED", f"flatten not APPLIED: {ctrl}"
    result = ctrl.get("result", {})
    assert result.get("closed") == 3, result
    assert result.get("cancelled") == 1, result
    assert result.get("remaining") == 0, result

    # Manual positions/orders survive an owned-only flatten.
    remaining_tickets = {getattr(p, "ticket", None) for p in fake.positions}
    assert remaining_tickets == {104}, remaining_tickets
    remaining_orders = {getattr(o, "ticket", None) for o in fake.orders}
    assert remaining_orders == {202}, remaining_orders


# ---------------------------------------------------------------------------
# Drill 3: Kill Switch Blocks Entries, Permits Exits
# ---------------------------------------------------------------------------
@pytest.mark.drill
def test_drill_3_kill_switch_blocks_entries_permits_exits(tmp_path):
    """Drill 3: Kill switch active -> entries refused with 'kill switch'; exits succeed."""
    db_path = str(tmp_path / "drill_ks.db")
    store = TradingStore(path=db_path)
    paper = PaperBroker()
    gateway = OrderGateway(store=store, mode="PAPER", adapter=PaperAdapter(paper))

    # Engage manual kill switch
    store.set_kill_switch(True, "Drill 3 emergency kill")
    assert store.is_kill_switch_active() is True

    # Validate that entries are blocked by unified authority
    blocked, reason = entries_blocked(store)
    assert blocked is True
    assert "kill switch" in reason.lower()

    # Entry submission must be refused by gateway
    vr = ValidationResult(
        approved=True,
        reasons=[],
        confluence_score=0.9,
        setup_key="setup_drill_3",
        created_at="2026-10-05T00:00:00Z",
    )
    intent = OrderIntent(
        intent_key="intent_drill_entry",
        symbol="EURUSD",
        side="BUY",
        volume=0.01,
        price=1.0500,
        sl=1.0400,
        tp=1.0600,
        risk_amount=10.0,
        setup_key="setup_drill_3",
        validation_result=vr,
        account_equity=1000.0,
    )
    entry_res = gateway.submit_entry(intent)
    assert entry_res.success is False
    assert "kill switch is active" in str(entry_res.error)

    # Risk-reducing exit must still succeed
    order_res = paper.market_order("EURUSD", "BUY", 0.01, 1.0500, sl=1.0400, tp=1.0600, comment="drill_exit_pos", magic=460001)
    ticket = order_res["order"]
    assert ticket is not None
    assert len(paper.positions) == 1

    exit_res = gateway.submit_exit(ticket, "EURUSD", comment="drill_exit")
    assert exit_res.success is True
    assert len(paper.positions) == 0


# ---------------------------------------------------------------------------
# Drill 4: News Latch Confirmation Ladder, Validator Rejection, Operator Clear
# ---------------------------------------------------------------------------
@pytest.mark.drill
def test_drill_4_news_latch_ladder_validator_rejection_and_operator_clear(
    tmp_path, baseline_proposal, baseline_market, monkeypatch
):
    """Drill 4: Two confirmed tripwire batches set latch -> validator rejects.

    Operator clears latch -> entries allowed again.
    """
    db_path = str(tmp_path / "drill_latch.db")
    latch_path = str(tmp_path / "drill_latch.json")
    store = TradingStore(path=db_path)

    latch = KillSwitchLatch(
        path=latch_path,
        store=store,
        confirmation_threshold=2,
        confirm_window_s=300.0,
    )
    monkeypatch.setattr("core.kill_switch.get_kill_switch_latch", lambda: latch)

    validator = DeterministicValidator(store=store, min_confluence=0.0, min_rr=1.0)

    # Initial baseline check: entries allowed
    init_res = validator.validate(baseline_proposal, baseline_market)
    assert init_res.approved is True

    # Batch 1: first tripwire batch (threshold is 2) -> unlatched
    latch.observe(1, ["Geopolitical conflict escalation detected"])
    assert latch.snapshot()["latched"] is False
    assert validator.validate(baseline_proposal, baseline_market).approved is True

    # Batch 2: second tripwire batch -> latch engages!
    latch.observe(2, ["Major currency peg broken emergency announcement"])
    assert latch.snapshot()["latched"] is True

    # Validator must reject with "news kill-switch latched"
    latched_res = validator.validate(baseline_proposal, baseline_market)
    assert latched_res.approved is False
    assert "news kill-switch latched" in latched_res.reasons

    # Operator clears the latch
    clear_snap = latch.clear(reason="threat assessment resolved", actor="operator:sec_ops")
    assert clear_snap["latched"] is False
    assert latch.snapshot()["latched"] is False

    # Validator allows entries again
    cleared_res = validator.validate(baseline_proposal, baseline_market)
    assert cleared_res.approved is True


# ---------------------------------------------------------------------------
# Drill 5: Corrupt Latch Record Fails Closed on Restart
# ---------------------------------------------------------------------------
@pytest.mark.drill
def test_drill_5_corrupt_latch_record_restart_fails_closed(
    tmp_path, baseline_proposal, baseline_market, monkeypatch
):
    """Drill 5: Restart with corrupt latch record fails closed to latched."""
    db_path = str(tmp_path / "drill_corrupt.db")
    latch_path = str(tmp_path / "drill_corrupt.json")
    store = TradingStore(path=db_path)

    # Write corrupt malformed JSON into the latch file
    with open(latch_path, "w", encoding="utf-8") as f:
        f.write("{{MALFORMED_TRUNCATED_JSON: True,")

    # Restart latch instance
    restarted_latch = KillSwitchLatch(path=latch_path, store=store)
    snap = restarted_latch.snapshot()

    assert snap["latched"] is True
    assert snap["trigger_source"] == "checkpoint_unreadable"

    monkeypatch.setattr("core.kill_switch.get_kill_switch_latch", lambda: restarted_latch)

    # Unified authority blocks entries
    blocked, reason = entries_blocked(store)
    assert blocked is True
    assert reason == "news kill-switch latched"

    # Validator fails closed
    validator = DeterministicValidator(store=store, min_confluence=0.0, min_rr=1.0)
    res = validator.validate(baseline_proposal, baseline_market)
    assert res.approved is False
    assert "news kill-switch latched" in res.reasons


# ---------------------------------------------------------------------------
# Drill 6: Single-Instance Lock, rc 3 Rejection, and Clean Shutdown Order
# ---------------------------------------------------------------------------
@pytest.mark.drill
def test_drill_6_single_instance_guard_and_clean_exit(tmp_path):
    """Drill 6: Single-instance lock prevents duplicate process (rc 3) and exits cleanly."""
    lock_file = str(tmp_path / "engine.lock")

    lock = FileLock(lock_file)
    acquired = lock.acquire(non_blocking=True)
    assert acquired is True, f"Failed to acquire engine lock at {lock_file}"
    lock.write_pid(os.getpid())

    try:
        child_env = os.environ.copy()
        child_env["LLM_TRADER_LOCK_PATH"] = lock_file
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONPATH"] = REPO_ROOT

        # Spawn second engine process attempting to start on the same lock
        cmd = [
            sys.executable,
            os.path.join(REPO_ROOT, "mt5_trader.py"),
            "llm-trade",
            "--symbol",
            "EURUSD",
            "--paper",
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        stdout, stderr = proc.communicate(timeout=25)
        assert proc.returncode == 3
        combined = stdout + stderr
        assert "engine.lock held by another process" in combined
    finally:
        # Clean shutdown and lock release within 5 seconds
        start_release = time.monotonic()
        lock.release()
        elapsed = time.monotonic() - start_release
        assert elapsed < 5.0
        assert lock.is_locked is False
