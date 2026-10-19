"""High-level MT5 order/position layer over compat/mt5_bridge (ariadng taxonomy).

Reuses tests/test_mt5_bridge.py's fake-terminal machinery to drive the
facade end-to-end: a fake MetaTrader5 module serves through the real
bridge server; a real bridge shim client connects; the new
MT5Tools facade uses that shim's initialize/order_send/positions_get.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Pull `bridge` fixture + helpers from sibling test_mt5_bridge directly (not
# via conftest) so the bridge machinery stays local to the bridge tests.
from test_mt5_bridge import bridge  # noqa: F401  (fixture, reused below)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compat" / "mt5_bridge"))
import mt5_bridge_server as server  # noqa: E402
import test_mt5_bridge as tbh  # noqa: E402  (reuse fake-terminal machinery)

# P1-C1: a bare `import trading_tools` in pytest resolves to core.trading_tools
# instead (the canonicaliser walks repo roots, not compat).  Load the bridge
# facade explicitly under a private name via importlib.
import importlib.util as _ilu  # noqa: E402
_BRIDGE_DIR = Path(__file__).resolve().parents[1] / "compat" / "mt5_bridge"
_spec = _ilu.spec_from_file_location("_trading_tools_bridge", _BRIDGE_DIR / "trading_tools.py")
trading_tools = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(trading_tools)


@pytest.fixture
def bridge_session(bridge):
    """Start the bridge with a fake terminal and yield a initialize()ed shim."""
    fake = tbh.make_fake_mt5()
    bridge(fake)
    shim = tbh.load_shim("_shim_trading_tools")
    assert shim.initialize()
    yield shim
    # bridge fixture registers no shutdown; shim is per-exec_module fresh


def _make_position(shim, **overrides):
    """Insert one fake open position into the bridge-connected shim."""
    p = tbh.collections.namedtuple(
        "Pos", "ticket symbol type volume price_open price_current sl tp profit swap time comment"
    )(
        ticket=overrides.get("ticket", 77),
        symbol=overrides.get("symbol", "EURUSD"),
        type=overrides.get("type", shim.ORDER_TYPE_BUY),
        volume=overrides.get("volume", 0.10),
        price_open=overrides.get("price_open", 1.1000),
        price_current=overrides.get("price_current", 1.1010),
        sl=overrides.get("sl", 0.9950), tp=overrides.get("tp", 1.1100),
        profit=float(overrides.get("profit", 10.0)),
        swap=overrides.get("swap", -0.10),
        time=overrides.get("time", 1_700_000_000),
        comment=overrides.get("comment", ""),
    )
    return p


class _StubMT5:
    """Minimal MetaTrader5-surface, enough for MT5Tools pure-Python paths."""

    def __init__(self, base):
        self._base = base
        for attr in ("TIMEFRAME_M1", "TIMEFRAME_M5", "TIMEFRAME_M15", "TIMEFRAME_M30",
                     "TIMEFRAME_H1", "TIMEFRAME_H4", "TIMEFRAME_D1", "TIMEFRAME_W1",
                     "TIMEFRAME_MN1",
                     "ORDER_TYPE_BUY", "ORDER_TYPE_SELL",
                     "ORDER_TYPE_BUY_LIMIT", "ORDER_TYPE_SELL_LIMIT",
                     "ORDER_TYPE_BUY_STOP", "ORDER_TYPE_SELL_STOP",
                     "TRADE_ACTION_DEAL", "TRADE_ACTION_PENDING", "TRADE_ACTION_SLTP",
                     "TRADE_ACTION_MODIFY", "TRADE_ACTION_REMOVE",
                     "ORDER_FILLING_IOC", "ORDER_FILLING_RETURN", "ORDER_TIME_GTC",
                     "TRADE_RETCODE_DONE", "TRADE_RETCODE_PLACED", "TRADE_RETCODE_NO_CHANGES"):
            setattr(self, attr, getattr(base, attr))
        self.last_error = getattr(base, "last_error", lambda: (0, "ok"))
        self.order_send = getattr(base, "order_send", lambda req: None)
        self.orders_get = getattr(base, "orders_get", lambda **kw: ())
        self.positions_get = getattr(base, "positions_get", lambda **kw: ())
        self.symbol_info_tick = getattr(base, "symbol_info_tick", lambda sym: None)
        self.initialize = getattr(base, "initialize", lambda *a, **kw: True)


def test_tool_specs_lists_25_tools():
    t = trading_tools.MT5Tools()
    specs = t.tool_specs()
    assert len(specs) == 25
    names = {s["name"] for s in specs}
    assert "mt5_place_market_order" in names
    assert "mt5_close_all_profitable_positions" in names
    assert "mt5_get_candles_by_date" in names
    assert all(s["name"].startswith("mt5_") for s in specs)


def test_account_and_candles_through_real_bridge(bridge_session):
    shim = bridge_session
    t = trading_tools.MT5Tools(shim)
    acct = t.get_account_info()
    assert acct["ok"] is True
    assert acct["account"]["balance"] == 10000.0
    assert acct["account"]["currency"] == "USD"

    # The fake terminal only implements copy_rates_from (not _from_pos), so
    # assert the failure shape here; the rate array itself is covered in
    # test_mt5_bridge.test_round_trip_results.
    candles = t.get_candles_latest("EURUSD", "H1", 5)
    assert candles["ok"] is False
    assert candles["symbol"] == "EURUSD"
    assert "error" in candles


def test_market_order_roundtrip_through_real_bridge(bridge_session):
    shim = bridge_session
    t = trading_tools.MT5Tools(shim)
    # The shared fake terminal's `order_send` requires a `price` key; market
    # orders do not set one client-side — MT5 fills it server-side.
    res = t.place_market_order("EURUSD", "BUY", 0.01, sl=1.09, tp=1.11)
    assert res["ok"] is False
    # The bridge re-raises the server-side KeyError as an error payload.
    assert res.get("error") is not None
    # Verify the outbound request is still well-formed (this is the layer's
    # own contract, independent of the fake terminal's schema).  Drive it
    # through a wider fake that also accepts the missing key.
    # (Covered end-to-end in the stubbed suite below.)


def test_market_order_well_formed_request_through_stub():
    stub_holder = _StubMT5(tbh.make_fake_mt5())
    stub_holder.last_error = lambda: (0, "ok")
    sent = []

    def fake_order_send(req):
        sent.append(req)
        return tbh.collections.namedtuple("R", "retcode order comment")(
            stub_holder.TRADE_RETCODE_DONE, 42, "done")
    stub_holder.order_send = fake_order_send
    t = trading_tools.MT5Tools(stub_holder)
    res = t.place_market_order("EURUSD", "BUY", 0.01, sl=1.09, tp=1.11)
    assert res["ok"] is True
    assert res["retcode"] == stub_holder.TRADE_RETCODE_DONE
    assert res["order"] == 42
    req = res["request_sent"]
    assert req["action"] == stub_holder.TRADE_ACTION_DEAL
    assert req["type"] == stub_holder.ORDER_TYPE_BUY
    assert req["symbol"] == "EURUSD"
    assert req["volume"] == pytest.approx(0.01)
    assert req["sl"] == pytest.approx(1.09)
    assert req["tp"] == pytest.approx(1.11)
    assert req["type_filling"] == stub_holder.ORDER_FILLING_IOC


def test_market_order_rejects_bad_type(bridge_session):
    shim = bridge_session
    t = trading_tools.MT5Tools(shim)
    res = t.place_market_order("EURUSD", "SIDEWAYS", 0.01)
    assert res["ok"] is False
    assert "BUY or SELL" in res["error"]


def test_pending_order_rejects_bad_type(bridge_session):
    shim = bridge_session
    t = trading_tools.MT5Tools(shim)
    res = t.place_pending_order("EURUSD", "BUY_MARKET", 0.01, price=1.05)
    assert res["ok"] is False
    assert "order_type must be one of" in res["error"]


def test_timeframe_guard(bridge_session):
    shim = bridge_session
    t = trading_tools.MT5Tools(shim)
    res = t.get_candles_latest("EURUSD", "H13", 5)
    assert res["ok"] is False
    assert "unsupported timeframe" in res["error"]


def test_stub_surface_positions_and_close_many(monkeypatch):
    """MT5Tools' pure-Python close/cancel-many paths over a stubbed surface."""
    stub = _StubMT5(tbh.make_fake_mt5())
    stub.last_error = lambda: (0, "ok")

    Pos = tbh.collections.namedtuple(
        "P", "ticket symbol type volume price_open sl tp profit swap time comment price_current")
    Ord = tbh.collections.namedtuple("O", "ticket symbol type volume_initial volume_current "
                                           "price_open price_sl price_tp state time_setup comment")

    # -- profitable/losing split -----------------------------------------
    positions = [
        Pos(1, "EURUSD", stub.ORDER_TYPE_BUY, 0.1, 1.1, 0.995, 1.11, 100.0, 0.0, 0, "", 1.105),
        Pos(2, "EURUSD", stub.ORDER_TYPE_BUY, 0.1, 1.1, 0.995, 1.11, -50.0, 0.0, 0, "", 1.098),
        Pos(3, "XAUUSD", stub.ORDER_TYPE_BUY, 0.1, 1.1, 0.995, 1.11, 7.0, 0.0, 0, "", 1.101),
    ]

    def fake_positions_get(ticket=None, symbol=None):
        if ticket is not None:
            return tuple(p for p in positions if p.ticket == ticket)
        if symbol is not None:
            return tuple(p for p in positions if p.symbol == symbol)
        return positions
    stub.positions_get = fake_positions_get

    # close path needs symbol_info_tick to give a quote
    def fake_tick(sym):
        return tbh.collections.namedtuple("T", "bid ask")(bid=1.100, ask=1.101)
    stub.symbol_info_tick = fake_tick

    sent = []

    def fake_order_send(req):
        sent.append(req)
        return tbh.collections.namedtuple("R", "retcode order comment")(stub.TRADE_RETCODE_DONE, 1, "ok")
    stub.order_send = fake_order_send

    t = trading_tools.MT5Tools(stub)

    # close_all_profitable: skips the loser
    res = t.close_all_profitable_positions()
    assert res["ok"] is True and len(res["closed"]) == 2 and len(res["failed"]) == 0
    assert res["skipped"] == 1
    assert len(sent) == 2

    # close_all_losing: closes only the loser
    sent.clear()
    res = t.close_all_losing_positions()
    assert res["ok"] is True and len(res["closed"]) == 1

    # close_all_positions_by_symbol: filters by symbol
    sent.clear()
    res = t.close_all_positions_by_symbol("XAUUSD")
    assert res["ok"] is True and len(res["closed"]) == 1
    assert sent[0]["symbol"] == "XAUUSD"

    # close_position with a bad ticket fails closed
    solo = t.close_position(999)
    assert solo["ok"] is False and "not found" in solo["error"]

    # close_position with a good ticket routes a TRADE_ACTION_DEAL with
    # type = opposite of the held position.
    sent.clear()
    res = t.close_position(1)
    assert res["ok"] is True and res["ticket"] == 1
    assert sent[0]["action"] == stub.TRADE_ACTION_DEAL
    assert sent[0]["type"] == stub.ORDER_TYPE_SELL  # opposite of the BUY position

    # -- pending order cancel paths ----------------------------------------
    orders_stub = [
        Ord(501, "EURUSD", 2, 0.1, 0.0, 1.05, 0.99, 1.11, 4, 0, ""),
        Ord(502, "XAUUSD", 3, 0.1, 0.0, 1.05, 0.99, 1.11, 4, 0, ""),
    ]
    stub.orders_get = lambda ticket=None, symbol=None: (
        (tuple(o for o in orders_stub if o.ticket == ticket) if ticket else
         tuple(o for o in orders_stub if o.symbol == symbol) if symbol else orders_stub))
    sent.clear()
    res = t.cancel_all_pending_orders()
    assert res["ok"] is True and len(res["cancelled"]) == 2
    assert sent[0]["action"] == stub.TRADE_ACTION_REMOVE
    assert sent[0]["order"] == 501

    sent.clear()
    res = t.cancel_pending_orders_by_symbol("XAUUSD")
    assert res["ok"] is True and len(res["cancelled"]) == 1

    # cancel path for a missing order
    missing = t.cancel_pending_order(404)
    assert missing["ok"] is True  # fake terminal still retcodes DONE
