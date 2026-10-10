"""MT5 bridge: macOS shim <-> Windows-side server, against a fake terminal.

Loads the shim under a private module name so it never shadows (or is
shadowed by) a real ``MetaTrader5`` install on Windows.
"""
import collections
import datetime as dt
import importlib.util
import socket
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

BRIDGE_DIR = Path(__file__).resolve().parents[1] / "compat" / "mt5_bridge"
sys.path.insert(0, str(BRIDGE_DIR))
import mt5_bridge_server as server  # noqa: E402

KEY = "test-bridge-key"

AccountInfo = collections.namedtuple("AccountInfo", "login balance equity currency")
TerminalInfo = collections.namedtuple("TerminalInfo", "connected company")
SymbolInfo = collections.namedtuple("SymbolInfo", "name digits point")
TradeRequest = collections.namedtuple("TradeRequest", "action symbol volume type price")
OrderSendResult = collections.namedtuple("OrderSendResult", "retcode order comment request")
RATES_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
                        ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"),
                        ("real_volume", "<u8")])


def make_fake_mt5(**constant_overrides):
    m = types.ModuleType("FakeMetaTrader5")
    shim_constants = load_shim("_shim_constants")._CONSTANTS
    for k, v in {**shim_constants, **constant_overrides, "SYMBOL_FILLING_FOK": 1}.items():
        setattr(m, k, v)
    m.calls = []
    m.shutdowns = 0
    m.initialize = lambda *a, **kw: (m.calls.append(("initialize", a, kw)) or True)

    def shutdown():
        m.shutdowns += 1
    m.shutdown = shutdown
    m.last_error = lambda: (1, "Success")
    m.terminal_info = lambda: TerminalInfo(True, "Demo Broker")
    m.account_info = lambda: AccountInfo(123456, 10000.0, 10012.5, "USD")
    m.symbols_get = lambda: (SymbolInfo("EURUSD", 5, 1e-5), SymbolInfo("XAUUSD", 2, 0.01))

    def copy_rates_from(symbol, timeframe, date_from, count):
        m.calls.append(("copy_rates_from", (symbol, timeframe, date_from, count), {}))
        arr = np.zeros(count, dtype=RATES_DTYPE)
        arr["time"] = np.arange(count) + 1_700_000_000
        arr["close"] = np.linspace(1.1, 1.2, count)
        return arr
    m.copy_rates_from = copy_rates_from

    def order_send(request):
        req = TradeRequest(request["action"], request["symbol"], request["volume"],
                           request["type"], request["price"])
        return OrderSendResult(10009, 42, "done", req)
    m.order_send = order_send
    m.positions_get = lambda **kw: None
    return m


def load_shim(name):
    spec = importlib.util.spec_from_file_location(name, BRIDGE_DIR / "MetaTrader5" / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def bridge(monkeypatch):
    def start(fake, key=KEY):
        port = free_port()
        ready = threading.Event()
        threading.Thread(target=server.serve,
                         args=(fake, "127.0.0.1", port, key.encode()),
                         kwargs={"ready": ready}, daemon=True).start()
        assert ready.wait(5)
        monkeypatch.setenv("MT5_BRIDGE_PORT", str(port))
        monkeypatch.setenv("MT5_BRIDGE_KEY", KEY)
        monkeypatch.setenv("MT5_BRIDGE_TIMEOUT", "5")
        return port
    return start


def test_before_initialize_behaves_like_detached_terminal(bridge):
    bridge(make_fake_mt5())
    mt5 = load_shim("_shim_detached")
    assert mt5.terminal_info() is None
    assert mt5.account_info() is None
    assert mt5.last_error()[0] == -10004
    assert mt5.ORDER_TYPE_BUY == 0 and mt5.TIMEFRAME_H1 == 16385


def test_round_trip_results(bridge):
    fake = make_fake_mt5()
    bridge(fake)
    mt5 = load_shim("_shim_roundtrip")
    assert mt5.initialize(login=1, password="x", server="Demo") is True
    assert fake.calls[0] == ("initialize", (), {"login": 1, "password": "x", "server": "Demo"})

    acct = mt5.account_info()
    assert acct.login == 123456 and acct.equity == 10012.5
    assert acct._asdict()["currency"] == "USD"

    syms = mt5.symbols_get()
    assert isinstance(syms, tuple) and [s.name for s in syms] == ["EURUSD", "XAUUSD"]

    when = dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    rates = mt5.copy_rates_from("EURUSD", mt5.TIMEFRAME_M15, when, 10)
    assert rates.dtype.names == RATES_DTYPE.names and len(rates) == 10
    assert rates["close"][-1] == pytest.approx(1.2)
    sent_when = fake.calls[-1][1][2]
    assert sent_when == when and sent_when.utcoffset() == dt.timedelta(0)

    res = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": "EURUSD",
                          "volume": 0.1, "type": mt5.ORDER_TYPE_BUY, "price": 1.1})
    assert res.retcode == mt5.TRADE_RETCODE_DONE and res.request.volume == 0.1

    assert mt5.positions_get(symbol="EURUSD") is None
    assert mt5.SYMBOL_FILLING_FOK == 1  # extra server constants appear on connect
    mt5.shutdown()
    assert fake.shutdowns == 1
    assert mt5.terminal_info() is None


class StructSeqAccountInfo(tuple):
    """Shape of the real package's results: tuple + _asdict(), no _fields."""
    _names = ("login", "balance", "currency")

    def _asdict(self):
        return dict(zip(self._names, self))


def test_struct_sequence_results_keep_field_names(bridge):
    fake = make_fake_mt5()
    fake.account_info = lambda: StructSeqAccountInfo((42, 5000.0, "USD"))
    bridge(fake)
    mt5 = load_shim("_shim_structseq")
    assert mt5.initialize()
    acct = mt5.account_info()
    assert acct.login == 42 and acct.balance == 5000.0 and acct.currency == "USD"


def test_constant_mismatch_refuses_initialize(bridge):
    bridge(make_fake_mt5(ORDER_TYPE_SELL=7))
    mt5 = load_shim("_shim_mismatch")
    assert mt5.initialize() is False
    assert "ORDER_TYPE_SELL" in mt5.last_error()[1]


def test_wrong_key_and_missing_bridge_fail_closed(bridge, monkeypatch):
    bridge(make_fake_mt5(), key="other-key")
    mt5 = load_shim("_shim_badkey")
    assert mt5.initialize() is False
    assert mt5.last_error()[0] == -10004

    monkeypatch.setenv("MT5_BRIDGE_PORT", str(free_port()))
    assert mt5.initialize() is False


def test_unknown_function_is_rejected():
    b = server.Bridge(make_fake_mt5())
    status, _, _ = b.call(1, "__class__", (), {})
    assert status == "error"


def test_terminal_shuts_down_only_after_last_client():
    fake = make_fake_mt5()
    b = server.Bridge(fake)
    b.call(1, "initialize", (), {})
    b.call(2, "initialize", (), {})
    b.detach(1)
    assert fake.shutdowns == 0
    b.detach(2)
    assert fake.shutdowns == 1
