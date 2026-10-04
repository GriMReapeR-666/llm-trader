"""Pin the OrderGateway risk ledger and exit paths (Phase 2 review R1-R6, X1-X4, D1).

Each test started life as a failing probe against the first gateway version;
docs/audit/REVIEW_PHASE2_2026-10-04.md has the findings.
"""
import inspect
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from core.order_gateway import BaseBrokerAdapter, LiveMT5Adapter, OrderGateway, OrderIntent
from core.trading_safety import TradeProposal, TradingStore, ValidationResult

INTENTS_DDL = """CREATE TABLE IF NOT EXISTS order_intents (intent_key TEXT PRIMARY KEY, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, run_id TEXT, proposal_id TEXT, status TEXT NOT NULL, mt5_ticket INTEGER,
    payload TEXT NOT NULL, result TEXT, error TEXT)"""


def evidence(setup_key="s"):
    return ValidationResult(approved=True, reasons=[], confluence_score=5, setup_key=setup_key)


class RecAdapter(BaseBrokerAdapter):
    def __init__(self, retcode=10009, protection=None):
        self.sent, self.mods, self.rc, self.protection = [], [], retcode, protection

    def send_order(self, req, mt5_client=None):
        self.sent.append(req)
        return {"retcode": self.rc, "order": 777}

    def modify_protection(self, ticket, symbol, sl, tp, mt5_client=None):
        self.mods.append((sl, tp))
        return {"retcode": 10009}

    def cancel_order(self, ticket, mt5_client=None):
        return {"retcode": self.rc}

    def close_position(self, *a, **k):
        return {"retcode": 10009}

    def position_protection(self, ticket, mt5_client=None):
        return self.protection


def intent(key, risk, **kw):
    fields = dict(intent_key=key, symbol="EURUSD", side="BUY", volume=0.1, price=1.1, sl=1.09, tp=1.12,
                  risk_amount=risk, setup_key="s", order_type="buy_limit", validation_result=evidence())
    fields.update(kw)
    return OrderIntent(**fields)


def status(store, key):
    with sqlite3.connect(store.path) as con:
        return con.execute("SELECT status FROM order_intents WHERE intent_key=?", (key,)).fetchone()[0]


@pytest.fixture
def store(tmp_path):
    s = TradingStore(str(tmp_path / "s.db"))
    s.set_state("equity", 10_000.0)
    return s


@pytest.fixture
def gw(store):
    return OrderGateway(store, mode="PAPER", adapter=RecAdapter())


# ---------------------------------------------------------------- R2 equity
def test_cap_uses_the_accounts_equity_on_a_large_account(gw):
    assert gw.submit_entry(intent("a", 1_000.0, account_equity=100_000.0)).success


def test_cap_uses_the_accounts_equity_on_a_small_account(gw):
    assert not gw.submit_entry(intent("a", 250.0, account_equity=500.0)).success


def test_unknown_equity_refuses(tmp_path):
    gw = OrderGateway(TradingStore(str(tmp_path / "s.db")), mode="PAPER", adapter=RecAdapter())
    res = gw.submit_entry(intent("a", 10.0))
    assert not res.success and "equity" in res.error


def test_live_equity_comes_from_the_terminal(store):
    terminal = NS(account_info=lambda: NS(equity=100_000.0))
    gw = OrderGateway(store, mode="LIVE", adapter=RecAdapter())
    assert gw._equity(intent("a", 1.0), mt5_client=terminal) == 100_000.0


# ------------------------------------------------------- R3 one intent, once
def _precreate(store, key, risk, run_id="run1"):
    with sqlite3.connect(store.path) as con:
        con.execute(INTENTS_DDL)
        con.execute("INSERT INTO order_intents VALUES(?,'t','t',?,'p1','CREATED',NULL,?,NULL,NULL)",
                    (key, run_id, json.dumps({"risk_amount": risk, "strategy": "scalp"})))


def test_precreated_intent_is_counted_once(gw, store):
    _precreate(store, "k", 200.0)          # 200 counted once fits the 300 cap; twice would not
    assert gw.submit_entry(intent("k", 200.0)).success


def test_precreated_intent_keeps_its_metadata(gw, store):
    _precreate(store, "k", 10.0)
    gw.submit_entry(intent("k", 10.0))
    with sqlite3.connect(store.path) as con:
        run_id, payload = con.execute("SELECT run_id, payload FROM order_intents WHERE intent_key='k'").fetchone()
    assert run_id == "run1" and "strategy" in payload


def test_resubmitting_a_sent_intent_is_refused(gw):
    assert gw.submit_entry(intent("k", 10.0)).success
    res = gw.submit_entry(intent("k", 10.0))
    assert not res.success and "Duplicate" in res.error
    assert len(gw.adapter.sent) == 1


# ------------------------------------------------- transient retries, abandon
def test_transient_refusal_keeps_the_reservation_for_a_retry(store):
    gw = OrderGateway(store, mode="PAPER", adapter=RecAdapter(retcode=10004))
    res = gw.submit_entry(intent("k", 100.0))
    assert not res.success and "transient" in res.error
    assert status(store, "k") == "CREATED"
    assert store.open_risk() == pytest.approx(100.0)
    gw.adapter.rc = 10009
    assert gw.submit_entry(intent("k", 100.0)).success          # same key retries


def test_abandon_releases_only_created_intents(store, gw):
    gw.adapter.rc = 10004
    gw.submit_entry(intent("k", 100.0))
    assert gw.abandon_entry("k", "gave up") is True
    assert status(store, "k") == "REJECTED" and store.open_risk() == 0.0
    gw.adapter.rc = 10009
    gw.submit_entry(intent("sent", 100.0))
    assert gw.abandon_entry("sent", "should not apply") is False
    assert status(store, "sent") == "FILLED"


def test_partial_fill_is_accepted_and_keeps_reserving(store):
    gw = OrderGateway(store, mode="PAPER", adapter=RecAdapter(retcode=10010))
    assert gw.submit_entry(intent("k", 100.0)).success
    assert status(store, "k") == "PARTIAL"
    assert store.open_risk() == pytest.approx(100.0)


# -------------------------------------------- R1 closed trades release budget
def test_closed_trade_releases_its_reservation(store):
    from agent_operations import AgentOperations
    ops = AgentOperations(store.path)
    proposal = TradeProposal(strategy="s", symbol="EURUSD", side="BUY", entry=1.1, stop_loss=1.09,
                             take_profit=1.12, confidence=.8, features={"setup_id": "x"})
    ops.create_order_intent("k", {"risk_amount": 100.0, "symbol": "EURUSD", "side": "BUY"}, actor="RiskController")
    ops.resolve_order_intent("k", "PENDING", ticket=5)
    ops.resolve_order_intent("k", "FILLED", ticket=5)
    store.record_open_trade(5, proposal, 0.1, 100.0, {})
    assert store.open_risk() == pytest.approx(100.0)                 # counted once, via the trade
    assert store.open_bucket_risk("USD_SHORT") == pytest.approx(100.0)
    store.record_close(5, 1.12, 200.0, "tp")                          # broker SL/TP, not the gateway
    assert store.open_risk() == 0.0
    assert store.open_bucket_risk("USD_SHORT") == 0.0


def test_error_intent_without_a_ticket_reserves_nothing(store):
    with sqlite3.connect(store.path) as con:
        con.execute(INTENTS_DDL)
        con.execute("INSERT INTO order_intents VALUES('e','t','t',NULL,NULL,'ERROR',NULL,?,NULL,'boom')",
                    (json.dumps({"risk_amount": 100.0}),))
        con.execute("INSERT INTO order_intents VALUES('u','t','t',NULL,NULL,'ERROR',9,?,NULL,'boom')",
                    (json.dumps({"risk_amount": 40.0}),))
    assert store.open_risk() == pytest.approx(40.0)    # only the ticketed one: broker state unknown


# --------------------------------------------------------- R4 cancel, R5 FSM
def test_failed_cancel_keeps_the_reservation(store, gw):
    assert gw.submit_entry(intent("a", 100.0)).success
    with sqlite3.connect(store.path) as con:   # a live pending order: PENDING with a ticket
        con.execute("UPDATE order_intents SET status='PENDING' WHERE intent_key='a'")
    gw.adapter.rc = 10013
    gw.cancel_pending(777)
    assert status(store, "a") == "UNRESOLVED"
    assert store.open_risk() == pytest.approx(100.0)
    gw.adapter.rc = 10009
    gw.cancel_pending(777)
    assert status(store, "a") == "CANCELLED" and store.open_risk() == 0.0


def test_gateway_never_makes_an_illegal_transition(store, gw):
    assert gw.submit_entry(intent("a", 10.0)).success                  # FILLED (terminal)
    assert gw._resolve_intent("a", "PENDING") is False
    assert status(store, "a") == "FILLED"


# ------------------------------------------------------------- R6 evidence
@pytest.mark.parametrize("vr,needle", [
    (ValidationResult(True, [], 5), "not bound"),
    (ValidationResult(True, [], 5, setup_key="other"), "mismatch"),
    (ValidationResult(True, [], 5, setup_key="s", created_at="2020-01-01T00:00:00+00:00"), "expired"),
    (ValidationResult(True, [], 5, setup_key="s", created_at="garbage"), "unreadable"),
])
def test_live_evidence_must_be_bound_and_fresh(store, vr, needle):
    gw = OrderGateway(store, mode="LIVE", adapter=RecAdapter())
    res = gw.submit_entry(intent("a", 10.0, validation_result=vr, account_equity=10_000.0))
    assert not res.success and needle in res.error
    assert gw.adapter.sent == []


def test_validation_result_is_timestamped():
    assert ValidationResult(True, [], 5).created_at


# ------------------------------------------------------------ X1 modify
def test_sl_only_modify_keeps_the_tp(gw):
    gw.modify_protection(1, "EURUSD", new_sl=1.095, current_sl=1.09, current_tp=1.12, side="BUY")
    assert gw.adapter.mods[-1] == (1.095, 1.12)


def test_tp_only_modify_keeps_the_sl(gw):
    gw.modify_protection(1, "EURUSD", new_tp=1.2, current_sl=1.09, side="BUY")
    assert gw.adapter.mods[-1] == (1.09, 1.2)


def test_modify_reads_missing_values_from_the_position(gw):
    gw.adapter.protection = (1.08, 1.15)
    gw.modify_protection(1, "EURUSD", new_sl=1.09, side="BUY")
    assert gw.adapter.mods[-1] == (1.09, 1.15)


def test_modify_refuses_when_current_values_are_unknown(gw):
    res = gw.modify_protection(1, "EURUSD", new_tp=1.2, side="BUY")
    assert not res.success and gw.adapter.mods == []


def test_modify_never_removes_the_stop(gw):
    res = gw.modify_protection(1, "EURUSD", new_sl=0.0, new_tp=1.2, side="BUY")
    assert not res.success and gw.adapter.mods == []


# --------------------------------------------------------- X2/X3 live close
class Terminal:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL, TRADE_ACTION_DEAL, ORDER_TIME_GTC = 0, 1, 1, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC = 0, 1

    def __init__(self, positions, kwargs_ok=True):
        self.positions, self.kwargs_ok, self.calls = positions, kwargs_ok, []

    def positions_get(self, **kw):
        if kw and not self.kwargs_ok:
            raise TypeError("no kwargs")
        return [p for p in self.positions if not kw or p.ticket == kw.get("ticket")]

    def symbol_info_tick(self, s):
        return NS(bid=1.1, ask=1.1002)

    def symbol_info(self, s):
        return NS(filling_mode=2)

    def order_send(self, req):
        self.calls.append(req)
        return NS(retcode=10009, order=1)


def test_close_never_falls_back_to_another_position():
    t = Terminal([NS(ticket=99, type=0, volume=1.0, symbol="GBPUSD", magic=1)], kwargs_ok=False)
    res = LiveMT5Adapter(t).close_position(5, "EURUSD")
    assert res["retcode"] == -1 and t.calls == []


def test_close_request_carries_broker_fields():
    t = Terminal([NS(ticket=5, type=0, volume=0.1, symbol="EURUSD", magic=460001)])
    LiveMT5Adapter(t).close_position(5, "EURUSD", volume=0.05)
    req = t.calls[0]
    assert req["type_filling"] == 1 and req["deviation"] == 20 and req["magic"] == 460001
    assert req["position"] == 5 and req["volume"] == 0.05 and req["type"] == 1


def test_close_refuses_more_than_the_position_volume():
    t = Terminal([NS(ticket=5, type=0, volume=0.1, symbol="EURUSD", magic=1)])
    assert LiveMT5Adapter(t).close_position(5, "EURUSD", volume=0.5)["retcode"] == -1
    assert t.calls == []


# ------------------------------------------------------------- X4 signature
def test_place_market_order_accepts_the_evidence_kwargs():
    from core import order_execution
    params = inspect.signature(order_execution.place_market_order).parameters
    assert {"validation_result", "setup_key", "risk_amount", "intent_key"} <= set(params)


# --------------------------------------------- D1 no per-call transport swap
def test_trade_manager_never_switches_the_shared_gateway(store):
    from core import order_gateway
    from trade_manager import TradeManager
    shared = order_gateway.configure_default_gateway(store, mode="PAPER")
    adapter_before = shared.adapter
    manager = TradeManager.__new__(TradeManager)
    manager.store, manager.paper_broker, manager.arbiter = store, None, None
    private = manager._gateway()                                   # live branch
    assert private is not shared and not private.is_paper
    assert shared.adapter is adapter_before and shared.is_paper
    order_gateway.reset_default_gateway()


def test_guardian_flatten_leaves_the_shared_gateway_on_paper(store, monkeypatch):
    from core import order_gateway
    from core.external_guardian import ExternalGuardian
    shared = order_gateway.configure_default_gateway(store, mode="PAPER")
    guardian = ExternalGuardian.__new__(ExternalGuardian)
    t = Terminal([NS(ticket=5, type=0, volume=0.1, symbol="EURUSD", magic=460001)])
    t.orders_get = lambda: []
    monkeypatch.setattr(guardian, "_get_mt5", lambda: t, raising=False)
    guardian._execute_flatten("test")
    assert shared.is_paper and shared.flattening is False
    assert [c["position"] for c in t.calls] == [5]
    order_gateway.reset_default_gateway()
