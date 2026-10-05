"""Locked broker read facade (PR 4.3 / PH4-N1).

Routes broker read calls (positions, orders, history, account, tick, symbol_info)
through the process-wide MT5 lock to ensure thread-safe execution and atomic
last_error attribution without racing concurrent order submission.
"""
import sys
from typing import Any, Optional, Tuple
from core.order_gateway import get_mt5_lock


def _resolve_client(mt5_inst: Any = None) -> Any:
    if mt5_inst is not None:
        return mt5_inst
    try:
        from core.order_execution import _get_mt5
        return _get_mt5()
    except Exception:
        return sys.modules.get("MetaTrader5")


def broker_positions_get(*args: Any, mt5_inst: Any = None, **kwargs: Any) -> Tuple[Any, Any]:
    """Retrieve open broker positions under MT5 lock. Returns (positions, last_error)."""
    client = _resolve_client(mt5_inst)
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.positions_get(*args, **kwargs)
            err = client.last_error() if hasattr(client, "last_error") else None
            return res, err
        except Exception as exc:
            return None, exc


def broker_orders_get(*args: Any, mt5_inst: Any = None, **kwargs: Any) -> Tuple[Any, Any]:
    """Retrieve pending broker orders under MT5 lock. Returns (orders, last_error)."""
    client = _resolve_client(mt5_inst)
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.orders_get(*args, **kwargs)
            err = client.last_error() if hasattr(client, "last_error") else None
            return res, err
        except Exception as exc:
            return None, exc


def broker_history_orders_get(*args: Any, mt5_inst: Any = None, **kwargs: Any) -> Tuple[Any, Any]:
    """Retrieve historical broker orders under MT5 lock. Returns (orders, last_error)."""
    client = _resolve_client(mt5_inst)
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.history_orders_get(*args, **kwargs)
            err = client.last_error() if hasattr(client, "last_error") else None
            return res, err
        except Exception as exc:
            return None, exc


def broker_history_deals_get(*args: Any, mt5_inst: Any = None, **kwargs: Any) -> Tuple[Any, Any]:
    """Retrieve historical broker deals under MT5 lock. Returns (deals, last_error)."""
    client = _resolve_client(mt5_inst)
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.history_deals_get(*args, **kwargs)
            err = client.last_error() if hasattr(client, "last_error") else None
            return res, err
        except Exception as exc:
            return None, exc


def broker_account_info(mt5_inst: Any = None) -> Tuple[Any, Any]:
    """Retrieve broker account info under MT5 lock. Returns (account_info, last_error)."""
    client = _resolve_client(mt5_inst)
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.account_info()
            err = client.last_error() if hasattr(client, "last_error") else None
            return res, err
        except Exception as exc:
            return None, exc


def broker_symbol_info(symbol: str, mt5_inst: Any = None) -> Tuple[Any, Any]:
    """Retrieve symbol info under MT5 lock. Returns (symbol_info, last_error)."""
    client = _resolve_client(mt5_inst)
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.symbol_info(symbol)
            err = client.last_error() if hasattr(client, "last_error") else None
            return res, err
        except Exception as exc:
            return None, exc


def broker_symbol_info_tick(symbol: str, mt5_inst: Any = None) -> Tuple[Any, Any]:
    """Retrieve latest tick under MT5 lock. Returns (tick, last_error)."""
    client = _resolve_client(mt5_inst)
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.symbol_info_tick(symbol)
            err = client.last_error() if hasattr(client, "last_error") else None
            return res, err
        except Exception as exc:
            return None, exc


def broker_copy_rates_from_pos(
    symbol: str, timeframe: Any, start_pos: int, count: int, mt5_inst: Any = None
) -> Tuple[Any, Any]:
    """Retrieve OHLC rates from position under MT5 lock. Returns (rates, last_error)."""
    client = _resolve_client(mt5_inst)
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.copy_rates_from_pos(symbol, timeframe, start_pos, count)
            err = client.last_error() if hasattr(client, "last_error") else None
            return res, err
        except Exception as exc:
            return None, exc


def broker_copy_rates_range(
    symbol: str, timeframe: Any, date_from: Any, date_to: Any, mt5_inst: Any = None
) -> Tuple[Any, Any]:
    """Retrieve OHLC rates range under MT5 lock. Returns (rates, last_error)."""
    client = _resolve_client(mt5_inst)
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.copy_rates_range(symbol, timeframe, date_from, date_to)
            err = client.last_error() if hasattr(client, "last_error") else None
            return res, err
        except Exception as exc:
            return None, exc
