"""Drop-in ``MetaTrader5`` module for macOS / Linux.

The official ``MetaTrader5`` package is a Windows-only wheel.  On other
platforms ``scripts/mac/setup_mac.sh`` puts this directory on ``sys.path``
(via a ``.pth`` file in the venv) so every existing ``import MetaTrader5``
resolves here.  Each call is forwarded to ``mt5_bridge_server.py``, which runs
under Windows Python next to the real terminal (Wine on the same Mac, or a
Windows VM/VPS) and calls the real package.

Semantics mirror the real package so the fail-closed callers stay correct:

* Nothing connects until ``initialize()``.  Before that every API call
  returns ``None`` and ``last_error()`` reports "No IPC connection" — the same
  as the real package with no terminal attached.
* A dead / unreachable bridge looks like a dead terminal: ``None`` results,
  ``initialize()`` returns ``False``.
* The constants below are the documented MT5 values.  On connect they are
  checked against the server's real ones; any mismatch refuses
  ``initialize()`` rather than risk sending a wrong order type.

Configuration (environment):
    MT5_BRIDGE_HOST      default 127.0.0.1
    MT5_BRIDGE_PORT      default 18812
    MT5_BRIDGE_KEY       shared secret (else read from MT5_BRIDGE_KEY_FILE)
    MT5_BRIDGE_KEY_FILE  default ~/.config/llm-trader/mt5_bridge.key
    MT5_BRIDGE_TIMEOUT   seconds per call, default 30
"""

from __future__ import annotations

import collections
import datetime as _dt
import os
import threading
from multiprocessing.connection import Client as _Client
from pathlib import Path

__version__ = "5.0.45"
__author__ = "llm-trader mt5 bridge"
BRIDGE = True  # lets diagnostics tell the shim from the real package

# Real-package error codes for transport failures.
_RES_E_INTERNAL_FAIL_CONNECT = -10004  # "No IPC connection"
_RES_E_INTERNAL_FAIL_TIMEOUT = -10005  # "IPC timeout"
_RES_E_INTERNAL_FAIL = -10001

# --------------------------------------------------------------------------
# Documented constants (verified against the server on every connect).
# --------------------------------------------------------------------------
_CONSTANTS = {
    # timeframes
    "TIMEFRAME_M1": 1, "TIMEFRAME_M2": 2, "TIMEFRAME_M3": 3, "TIMEFRAME_M4": 4,
    "TIMEFRAME_M5": 5, "TIMEFRAME_M6": 6, "TIMEFRAME_M10": 10, "TIMEFRAME_M12": 12,
    "TIMEFRAME_M15": 15, "TIMEFRAME_M20": 20, "TIMEFRAME_M30": 30,
    "TIMEFRAME_H1": 16385, "TIMEFRAME_H2": 16386, "TIMEFRAME_H3": 16387,
    "TIMEFRAME_H4": 16388, "TIMEFRAME_H6": 16390, "TIMEFRAME_H8": 16392,
    "TIMEFRAME_H12": 16396, "TIMEFRAME_D1": 16408, "TIMEFRAME_W1": 32769,
    "TIMEFRAME_MN1": 49153,
    # copy_ticks flags
    "COPY_TICKS_ALL": -1, "COPY_TICKS_INFO": 1, "COPY_TICKS_TRADE": 2,
    # order types
    "ORDER_TYPE_BUY": 0, "ORDER_TYPE_SELL": 1, "ORDER_TYPE_BUY_LIMIT": 2,
    "ORDER_TYPE_SELL_LIMIT": 3, "ORDER_TYPE_BUY_STOP": 4, "ORDER_TYPE_SELL_STOP": 5,
    "ORDER_TYPE_BUY_STOP_LIMIT": 6, "ORDER_TYPE_SELL_STOP_LIMIT": 7,
    "ORDER_TYPE_CLOSE_BY": 8,
    # trade actions
    "TRADE_ACTION_DEAL": 1, "TRADE_ACTION_PENDING": 5, "TRADE_ACTION_SLTP": 6,
    "TRADE_ACTION_MODIFY": 7, "TRADE_ACTION_REMOVE": 8, "TRADE_ACTION_CLOSE_BY": 10,
    # filling / time
    "ORDER_FILLING_FOK": 0, "ORDER_FILLING_IOC": 1, "ORDER_FILLING_RETURN": 2,
    "ORDER_FILLING_BOC": 3,
    "ORDER_TIME_GTC": 0, "ORDER_TIME_DAY": 1, "ORDER_TIME_SPECIFIED": 2,
    "ORDER_TIME_SPECIFIED_DAY": 3,
    # positions / deals / book
    "POSITION_TYPE_BUY": 0, "POSITION_TYPE_SELL": 1,
    "DEAL_TYPE_BUY": 0, "DEAL_TYPE_SELL": 1, "DEAL_TYPE_BALANCE": 2,
    "DEAL_ENTRY_IN": 0, "DEAL_ENTRY_OUT": 1, "DEAL_ENTRY_INOUT": 2, "DEAL_ENTRY_OUT_BY": 3,
    "BOOK_TYPE_SELL": 1, "BOOK_TYPE_BUY": 2, "BOOK_TYPE_SELL_MARKET": 3,
    "BOOK_TYPE_BUY_MARKET": 4,
    # trade server return codes
    "TRADE_RETCODE_REQUOTE": 10004, "TRADE_RETCODE_REJECT": 10006,
    "TRADE_RETCODE_CANCEL": 10007, "TRADE_RETCODE_PLACED": 10008,
    "TRADE_RETCODE_DONE": 10009, "TRADE_RETCODE_DONE_PARTIAL": 10010,
    "TRADE_RETCODE_ERROR": 10011, "TRADE_RETCODE_TIMEOUT": 10012,
    "TRADE_RETCODE_INVALID": 10013, "TRADE_RETCODE_INVALID_VOLUME": 10014,
    "TRADE_RETCODE_INVALID_PRICE": 10015, "TRADE_RETCODE_INVALID_STOPS": 10016,
    "TRADE_RETCODE_TRADE_DISABLED": 10017, "TRADE_RETCODE_MARKET_CLOSED": 10018,
    "TRADE_RETCODE_NO_MONEY": 10019, "TRADE_RETCODE_PRICE_CHANGED": 10020,
    "TRADE_RETCODE_PRICE_OFF": 10021, "TRADE_RETCODE_INVALID_EXPIRATION": 10022,
    "TRADE_RETCODE_ORDER_CHANGED": 10023, "TRADE_RETCODE_TOO_MANY_REQUESTS": 10024,
    "TRADE_RETCODE_NO_CHANGES": 10025, "TRADE_RETCODE_SERVER_DISABLES_AT": 10026,
    "TRADE_RETCODE_CLIENT_DISABLES_AT": 10027, "TRADE_RETCODE_LOCKED": 10028,
    "TRADE_RETCODE_FROZEN": 10029, "TRADE_RETCODE_INVALID_FILL": 10030,
    "TRADE_RETCODE_CONNECTION": 10031, "TRADE_RETCODE_ONLY_REAL": 10032,
    "TRADE_RETCODE_LIMIT_ORDERS": 10033, "TRADE_RETCODE_LIMIT_VOLUME": 10034,
    "TRADE_RETCODE_INVALID_ORDER": 10035, "TRADE_RETCODE_POSITION_CLOSED": 10036,
    "TRADE_RETCODE_INVALID_CLOSE_VOLUME": 10038, "TRADE_RETCODE_CLOSE_ORDER_EXIST": 10039,
    "TRADE_RETCODE_LIMIT_POSITIONS": 10040, "TRADE_RETCODE_REJECT_CANCEL": 10041,
    "TRADE_RETCODE_LONG_ONLY": 10042, "TRADE_RETCODE_SHORT_ONLY": 10043,
    "TRADE_RETCODE_CLOSE_ONLY": 10044, "TRADE_RETCODE_FIFO_CLOSE": 10045,
}
globals().update(_CONSTANTS)

# The public API of the real package.  Anything else the server exposes is
# reachable through module ``__getattr__`` once connected.
_API = (
    "login", "version", "account_info", "terminal_info",
    "symbols_total", "symbols_get", "symbol_info", "symbol_info_tick", "symbol_select",
    "market_book_add", "market_book_get", "market_book_release",
    "copy_rates_from", "copy_rates_from_pos", "copy_rates_range",
    "copy_ticks_from", "copy_ticks_range",
    "orders_total", "orders_get", "order_calc_margin", "order_calc_profit",
    "order_check", "order_send",
    "positions_total", "positions_get",
    "history_orders_total", "history_orders_get",
    "history_deals_total", "history_deals_get",
)

_lock = threading.RLock()
_conn = None
_server_functions: frozenset = frozenset()
_last_error = (_RES_E_INTERNAL_FAIL_CONNECT, "No IPC connection")
_nt_types: dict = {}


def _settings():
    host = os.environ.get("MT5_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("MT5_BRIDGE_PORT", "18812"))
    timeout = float(os.environ.get("MT5_BRIDGE_TIMEOUT", "30"))
    key = os.environ.get("MT5_BRIDGE_KEY", "")
    if not key:
        key_file = Path(os.environ.get(
            "MT5_BRIDGE_KEY_FILE", "~/.config/llm-trader/mt5_bridge.key")).expanduser()
        try:
            key = key_file.read_text(encoding="utf-8").strip()
        except OSError:
            key = ""
    return host, port, timeout, key.encode("utf-8")


# ----------------------------- wire encoding ------------------------------
# Results are tagged so neither side needs the other's MetaTrader5 / numpy
# pickle internals (numpy 1.x cannot unpickle numpy 2.x arrays).

def _encode(value):
    if isinstance(value, _dt.datetime) and value.tzinfo is not None:
        return value.astimezone(_dt.timezone.utc)  # pytz-free, pickles anywhere
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)) and not hasattr(value, "_fields"):
        return type(value)(_encode(v) for v in value)
    return value


def _decode(value):
    # The server sends every tuple tagged, so a bare tuple is always a tag.
    if isinstance(value, tuple):
        tag = value[0]
        if tag == "__nt__":
            _, name, fields, items = value
            cls = _nt_types.get((name, fields))
            if cls is None:
                cls = collections.namedtuple(name, fields, rename=True)
                cls.__module__ = __name__
                _nt_types[(name, fields)] = cls
            return cls(*(_decode(v) for v in items))
        if tag == "__nd__":
            import numpy as np
            _, descr, shape, raw = value
            dtype = np.dtype([tuple(f) for f in descr]) if isinstance(descr, list) else np.dtype(descr)
            return np.frombuffer(raw, dtype=dtype).reshape(shape).copy()
        if tag == "__tuple__":
            return tuple(_decode(v) for v in value[1])
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if isinstance(value, dict):
        return {k: _decode(v) for k, v in value.items()}
    return value


# ------------------------------- transport --------------------------------

def _drop(error):
    global _conn, _last_error
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None
    _last_error = error


def _rpc(op, *payload, timeout=None):
    """Send one request; return (ok, value).  Caller holds ``_lock``."""
    global _last_error
    if _conn is None:
        _last_error = (_RES_E_INTERNAL_FAIL_CONNECT, "No IPC connection")
        return False, None
    timeout = max(timeout or 0, _settings()[2])
    try:
        _conn.send((op,) + payload)
        if not _conn.poll(timeout):
            _drop((_RES_E_INTERNAL_FAIL_TIMEOUT, f"IPC timeout (mt5 bridge, {timeout:g}s)"))
            return False, None
        status, value, err = _conn.recv()
    except (EOFError, OSError) as exc:
        _drop((_RES_E_INTERNAL_FAIL_CONNECT, f"No IPC connection (mt5 bridge lost: {exc})"))
        return False, None
    if err is not None:
        _last_error = tuple(err)
    if status != "ok":
        _last_error = (_RES_E_INTERNAL_FAIL, f"mt5 bridge: {value}")
        return False, None
    return True, _decode(value)


def _connect():
    """Open the socket and verify constants.  Caller holds ``_lock``."""
    global _conn, _server_functions, _last_error
    if _conn is not None:
        return True
    host, port, timeout, key = _settings()
    if not key:
        _last_error = (_RES_E_INTERNAL_FAIL_CONNECT,
                       "No IPC connection (mt5 bridge key missing: set MT5_BRIDGE_KEY or run scripts/mac/setup_mac.sh)")
        return False
    try:
        _conn = _Client((host, port), family="AF_INET", authkey=key)
    except Exception as exc:  # refused, auth failure, unreachable
        _conn = None
        _last_error = (_RES_E_INTERNAL_FAIL_CONNECT,
                       f"No IPC connection (mt5 bridge at {host}:{port}: {exc})")
        return False
    ok, hello = _rpc("hello")
    if not ok:
        return False
    remote_constants = hello.get("constants", {})
    mismatched = sorted(k for k, v in _CONSTANTS.items()
                        if k in remote_constants and remote_constants[k] != v)
    if mismatched:
        _drop((_RES_E_INTERNAL_FAIL,
               "mt5 bridge constant mismatch, refusing to trade: " + ", ".join(mismatched)))
        return False
    globals().update({k: v for k, v in remote_constants.items() if k not in _CONSTANTS})
    _server_functions = frozenset(hello.get("functions", ()))
    return True


def _call(name, args, kwargs):
    with _lock:
        ok, value = _rpc("call", name, _encode(args), _encode(kwargs))
        return value if ok else None


# ------------------------------ public API --------------------------------

def initialize(*args, **kwargs):
    """Connect to the bridge, then attach the remote terminal."""
    with _lock:
        if not _connect():
            return False
        # The terminal may take its own ``timeout`` (ms) to attach; wait past it.
        wait = float(kwargs.get("timeout") or 0) / 1000 + 10
        ok, value = _rpc("call", "initialize", _encode(args), _encode(kwargs), timeout=wait)
        return bool(value) if ok else False


def shutdown():
    with _lock:
        if _conn is None:
            return None
        _rpc("call", "shutdown", (), {})
        _drop((_RES_E_INTERNAL_FAIL_CONNECT, "No IPC connection"))
        return None


def last_error():
    return _last_error


def _make(name):
    def fn(*args, **kwargs):
        return _call(name, args, kwargs)
    fn.__name__ = fn.__qualname__ = name
    fn.__doc__ = f"Forwarded to MetaTrader5.{name} through the mt5 bridge."
    return fn


for _name in _API:
    globals()[_name] = _make(_name)
del _name


def __getattr__(name):
    if name in _server_functions:
        fn = _make(name)
        globals()[name] = fn
        return fn
    raise AttributeError(f"module 'MetaTrader5' has no attribute {name!r}")
