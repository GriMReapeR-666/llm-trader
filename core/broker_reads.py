"""Locked broker read facade (PR 4.3 / PH4-N1).

Routes broker read calls (positions, orders, history, account, tick, symbol_info)
through the process-wide MT5 lock to ensure thread-safe execution and atomic
last_error attribution without racing concurrent order submission.

Return contract: every helper returns ``(result, error)`` where ``error`` is
an ``Exception`` instance when the call itself raised, and ``None`` otherwise.
``last_error()`` is captured atomically under the lock for diagnostics, but a
non-None ``last_error`` value is NOT a failure signal (MT5 reports the previous
error code even after successful calls, and mocks never return None). Callers
must test ``isinstance(error, Exception)`` — never truthiness — to detect
failure, and ``result is None`` to detect absence.
"""
import sys
from typing import Any, Optional, Tuple
from core.order_gateway import get_mt5_lock


_UNSET: Any = object()


_READ_ATTRS = (
    "positions_get",
    "orders_get",
    "history_deals_get",
    "history_orders_get",
    "account_info",
    "symbol_info_tick",
    "symbol_info",
    "copy_rates_from_pos",
    "copy_rates_range",
)


def _is_connection_wrapper(client: Any) -> bool:
    """Return True if client is MT5Client or similar connection wrapper, not a raw broker handle.

    MT5Client exposes login, account_info, get_account_info, and shutdown, but
    does not expose positions_get, orders_get, history_deals_get, or symbol_info_tick.
    """
    if client is None:
        return False
    # If it has connection management attributes but lacks core broker reading methods
    has_conn_surface = any(hasattr(client, attr) for attr in ("reconnect_if_needed", "ensure_import", "login", "shutdown"))
    has_broker_surface = callable(getattr(client, "positions_get", None)) and callable(getattr(client, "orders_get", None))
    return has_conn_surface and not has_broker_surface


def _looks_like_broker(client: Any, required_method: Optional[str] = None) -> bool:
    """True when the handle exposes the required broker read surface.

    Connection wrappers (MT5Client, with login, account_info, get_account_info,
    shutdown) are NOT broker handles for reads: routing reads through them yields
    AttributeError-shaped silence instead of broker data.
    """
    if client is None:
        return False
    if _is_connection_wrapper(client):
        return False
    if required_method:
        return callable(getattr(client, required_method, None))
    core_methods = (
        "positions_get",
        "orders_get",
        "history_deals_get",
        "history_orders_get",
        "symbol_info_tick",
        "symbol_info",
        "copy_rates_from_pos",
        "copy_rates_range",
    )
    return any(callable(getattr(client, name, None)) for name in core_methods)


def _resolve_client(mt5_inst: Any = _UNSET, required_method: Optional[str] = None) -> Any:
    """Resolve the broker client, distinguishing absent from unavailable.

    - Explicit ``None`` means "no broker" (paper/offline callers, tests that
      neutralise the module global) and resolves to None — it must NOT
      trigger a hunt that would silently reattach a detached broker.
    - A non-None handle without the required read surface (e.g. the MT5Client
      connection wrapper) falls back to the process module handle, so live
      reads never silently yield empty against a wrapper.
    - Only an omitted argument hunts unconditionally.
    """
    if mt5_inst is None:
        return None
    if mt5_inst is not _UNSET and _looks_like_broker(mt5_inst, required_method):
        return mt5_inst

    # Fallback to module handle
    fallback = None
    try:
        from core.order_execution import _get_mt5
        fallback = _get_mt5()
    except Exception:
        fallback = sys.modules.get("MetaTrader5")

    if fallback is not None and _looks_like_broker(fallback, required_method):
        return fallback

    # If fallback also doesn't satisfy required_method, but mt5_inst was provided and has it
    if mt5_inst is not _UNSET and callable(getattr(mt5_inst, required_method or "", None)):
        return mt5_inst
    if fallback is not None and callable(getattr(fallback, required_method or "", None)):
        return fallback

    return None if mt5_inst is not _UNSET and not _looks_like_broker(fallback, required_method) else fallback


def broker_positions_get(*args: Any, mt5_inst: Any = _UNSET, **kwargs: Any) -> Tuple[Any, Any]:
    """Retrieve open broker positions under MT5 lock. Returns (positions, error-or-None)."""
    client = _resolve_client(mt5_inst, "positions_get")
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.positions_get(*args, **kwargs)
            return res, None
        except Exception as exc:
            return None, exc


def broker_orders_get(*args: Any, mt5_inst: Any = _UNSET, **kwargs: Any) -> Tuple[Any, Any]:
    """Retrieve pending broker orders under MT5 lock. Returns (orders, error-or-None)."""
    client = _resolve_client(mt5_inst, "orders_get")
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.orders_get(*args, **kwargs)
            return res, None
        except Exception as exc:
            return None, exc


def broker_history_orders_get(*args: Any, mt5_inst: Any = _UNSET, **kwargs: Any) -> Tuple[Any, Any]:
    """Retrieve historical broker orders under MT5 lock. Returns (orders, error-or-None)."""
    client = _resolve_client(mt5_inst, "history_orders_get")
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.history_orders_get(*args, **kwargs)
            return res, None
        except Exception as exc:
            return None, exc


def broker_history_deals_get(*args: Any, mt5_inst: Any = _UNSET, **kwargs: Any) -> Tuple[Any, Any]:
    """Retrieve historical broker deals under MT5 lock. Returns (deals, error-or-None)."""
    client = _resolve_client(mt5_inst, "history_deals_get")
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.history_deals_get(*args, **kwargs)
            return res, None
        except Exception as exc:
            return None, exc


def broker_account_info(mt5_inst: Any = _UNSET) -> Tuple[Any, Any]:
    """Retrieve broker account info under MT5 lock. Returns (account_info, error-or-None)."""
    client = _resolve_client(mt5_inst, "account_info")
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.account_info()
            return res, None
        except Exception as exc:
            return None, exc


def broker_symbol_info(symbol: str, mt5_inst: Any = _UNSET) -> Tuple[Any, Any]:
    """Retrieve symbol info under MT5 lock. Returns (symbol_info, error-or-None)."""
    client = _resolve_client(mt5_inst, "symbol_info")
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.symbol_info(symbol)
            return res, None
        except Exception as exc:
            return None, exc


def broker_symbol_info_tick(symbol: str, mt5_inst: Any = _UNSET) -> Tuple[Any, Any]:
    """Retrieve latest tick under MT5 lock. Returns (tick, error-or-None)."""
    client = _resolve_client(mt5_inst, "symbol_info_tick")
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.symbol_info_tick(symbol)
            return res, None
        except Exception as exc:
            return None, exc


def broker_copy_rates_from_pos(
    symbol: str, timeframe: Any, start_pos: int, count: int, mt5_inst: Any = _UNSET
) -> Tuple[Any, Any]:
    """Retrieve OHLC rates from position under MT5 lock. Returns (rates, error-or-None)."""
    client = _resolve_client(mt5_inst, "copy_rates_from_pos")
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.copy_rates_from_pos(symbol, timeframe, start_pos, count)
            return res, None
        except Exception as exc:
            return None, exc


def broker_copy_rates_range(
    symbol: str, timeframe: Any, date_from: Any, date_to: Any, mt5_inst: Any = _UNSET
) -> Tuple[Any, Any]:
    """Retrieve OHLC rates range under MT5 lock. Returns (rates, error-or-None)."""
    client = _resolve_client(mt5_inst, "copy_rates_range")
    if client is None:
        return None, "client_unavailable"
    with get_mt5_lock():
        try:
            res = client.copy_rates_range(symbol, timeframe, date_from, date_to)
            return res, None
        except Exception as exc:
            return None, exc
