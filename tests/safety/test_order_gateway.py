"""Unit and concurrency tests for OrderGateway (Phase 2 PR 2.2).

Pins:
- Precondition refusals: kill switch, flattening, invalid SL (PH1-N3)
- Evidence requirement: valid setup_key, approved, not expired
- Risk reservation under atomic BEGIN IMMEDIATE
- Concurrency probe: 4 threads attempting to exceed risk cap (CC-F1 invariant)
- Exits allowed under kill switch
- Protective stops enforce monotonic only-tighten
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import os
import sqlite3
import pytest

from core.order_gateway import OrderGateway, OrderIntent, PaperAdapter
from core.trading_safety import TradingStore, PortfolioRiskArbiter


@dataclass
class DummyValidationResult:
    approved: bool = True
    setup_key: str = "EURUSD_H1"
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def store(tmp_path):
    db_file = str(tmp_path / "test_gateway.db")
    return TradingStore(db_file, mode="PAPER")


@pytest.fixture
def arbiter(store):
    return PortfolioRiskArbiter(store, max_open_risk_fraction=0.03)


@pytest.fixture
def gateway(store, arbiter):
    return OrderGateway(store, mode="PAPER", arbiter=arbiter)


def test_gateway_refuses_when_kill_switch_active(gateway, store):
    """Entry must be refused when kill switch is active."""
    store.set_kill_switch(True, "testing kill switch")
    intent = OrderIntent(
        intent_key="test-1",
        symbol="EURUSD",
        side="BUY",
        volume=0.01,
        price=1.10,
        sl=1.09,
        tp=1.12,
        risk_amount=50.0,
        setup_key="EURUSD_H1",
        validation_result=DummyValidationResult(),
    )
    res = gateway.submit_entry(intent)
    assert res.success is False
    assert "kill switch" in res.error.lower()


def test_gateway_refuses_when_flattening_active(gateway):
    """Entry must be refused while flattening is in progress (CC-F10)."""
    gateway.flattening = True
    intent = OrderIntent(
        intent_key="test-flat",
        symbol="EURUSD",
        side="BUY",
        volume=0.01,
        price=1.10,
        sl=1.09,
        risk_amount=50.0,
        setup_key="EURUSD_H1",
        validation_result=DummyValidationResult(),
    )
    res = gateway.submit_entry(intent)
    assert res.success is False
    assert "flattening" in res.error.lower()


def test_gateway_refuses_missing_stop_loss(gateway):
    """PH1-N3: Entries with sl=None or 0 must fail closed."""
    for bad_sl in [None, 0.0, -1.0]:
        intent = OrderIntent(
            intent_key=f"test-sl-{bad_sl}",
            symbol="EURUSD",
            side="BUY",
            volume=0.01,
            price=1.10,
            sl=bad_sl,
            risk_amount=50.0,
            setup_key="EURUSD_H1",
            validation_result=DummyValidationResult(),
        )
        res = gateway.submit_entry(intent)
        assert res.success is False
        assert "stop-loss" in res.error.lower() or "sl" in res.error.lower()


def test_gateway_requires_fresh_validation_evidence(gateway):
    """Validation evidence must be present, approved, and unexpired."""
    # 1. Missing evidence
    intent_no_ev = OrderIntent(
        intent_key="test-no-ev",
        symbol="EURUSD",
        side="BUY",
        volume=0.01,
        price=1.10,
        sl=1.09,
        risk_amount=50.0,
        setup_key="EURUSD_H1",
        validation_result=None,
    )
    assert gateway.submit_entry(intent_no_ev).success is False

    # 2. Unapproved evidence
    intent_unapp = OrderIntent(
        intent_key="test-unapp",
        symbol="EURUSD",
        side="BUY",
        volume=0.01,
        price=1.10,
        sl=1.09,
        risk_amount=50.0,
        setup_key="EURUSD_H1",
        validation_result=DummyValidationResult(approved=False),
    )
    assert gateway.submit_entry(intent_unapp).success is False

    # 3. Expired evidence
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    intent_expired = OrderIntent(
        intent_key="test-exp",
        symbol="EURUSD",
        side="BUY",
        volume=0.01,
        price=1.10,
        sl=1.09,
        risk_amount=50.0,
        setup_key="EURUSD_H1",
        validation_result=DummyValidationResult(created_at=old_ts),
    )
    res_exp = gateway.submit_entry(intent_expired)
    assert res_exp.success is False
    assert "expired" in res_exp.error.lower()


def test_exit_allowed_under_kill_switch(gateway, store):
    """Risk-reducing exits are permitted even if kill switch is active."""
    # Create an initial paper position
    intent = OrderIntent(
        intent_key="entry-for-exit",
        symbol="EURUSD",
        side="BUY",
        volume=0.01,
        price=1.10,
        sl=1.09,
        risk_amount=50.0,
        setup_key="EURUSD_H1",
        validation_result=DummyValidationResult(),
    )
    res_entry = gateway.submit_entry(intent)
    assert res_entry.success is True
    ticket = res_entry.ticket
    assert ticket is not None

    # Trip kill switch
    store.set_kill_switch(True, "emergency shutdown")

    # Exit must still be permitted!
    res_exit = gateway.submit_exit(ticket, "EURUSD", comment="protective_exit")
    assert res_exit.success is True


def test_modify_protection_enforces_only_tighten(gateway):
    """Monotonic stop rule: widening a protective stop is rejected."""
    # BUY: widening means lowering SL
    res_widen = gateway.modify_protection(
        ticket=100, symbol="EURUSD", new_sl=1.0850, current_sl=1.0900, side="BUY"
    )
    assert res_widen.success is False
    assert "cannot be widened" in res_widen.error

    # SELL: widening means raising SL
    res_widen_sell = gateway.modify_protection(
        ticket=101, symbol="EURUSD", new_sl=1.1050, current_sl=1.1000, side="SELL"
    )
    assert res_widen_sell.success is False
    assert "cannot be widened" in res_widen_sell.error


def test_concurrency_race_never_exceeds_risk_cap(tmp_path):
    """Port of probe P1/P2 from 06_concurrency.md.

    Scenario: 4 threads simultaneously attempt to place orders with risk 100 each.
    Equity = 10,000, max_open_risk_fraction = 0.03 -> Total cap = 300.
    INVARIANT: Total admitted risk <= 300 in 100% of trials!
    """
    for trial in range(5):
        db_file = str(tmp_path / f"conc_trial_{trial}.db")
        store = TradingStore(db_file, mode="PAPER")
        store.set_state("equity", 10000.0)
        arbiter = PortfolioRiskArbiter(store, max_open_risk_fraction=0.03)
        gateway = OrderGateway(store, mode="PAPER", arbiter=arbiter)

        def attempt_order(idx: int):
            intent = OrderIntent(
                intent_key=f"trial-{trial}-intent-{idx}",
                symbol="EURUSD",
                side="BUY",
                volume=0.01,
                price=1.10,
                sl=1.09,
                risk_amount=100.0,
                setup_key="EURUSD_H1",
                validation_result=DummyValidationResult(),
            )
            return gateway.submit_entry(intent)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(attempt_order, i) for i in range(4)]
            results = [f.result() for f in futures]

        accepted = [r for r in results if r.success]
        # At most 3 orders of 100.0 can be accepted under a 300.0 cap
        assert len(accepted) <= 3, f"Trial {trial}: {len(accepted)} orders accepted (exceeded cap of 3)"

        # Check total reserved open risk in store
        open_risk = store.open_risk(include_pending_intents=True)
        assert open_risk <= 300.0 + 1e-6, f"Trial {trial}: open risk {open_risk} exceeded 300.0"
