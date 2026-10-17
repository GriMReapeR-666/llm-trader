from __future__ import annotations

"""High-level MT5 order/position layer (ariadng/metatrader-mcp-server taxonomy).

Module name note (P1-C1 single-import-root): a BARE ``import trading_tools``
inside pytest resolves to ``core.trading_tools`` instead (the canonicaliser
walks repo roots, not compat).  Consumers must therefore use the
package-qualified spelling ``from compat.mt5_bridge import trading_tools`` or
load it via importlib into a private name (kronos_mcp/tools_mt5.py does the
latter).

This module builds the named commercial-tool surface (place_market_order,
place_pending_order, modify_position, ..., close_all_positions, get_deals,
get_orders, get_candles_latest, get_candles_by_date, get_symbols,
get_symbol_price, get_account_info, get_all_positions, get_positions_by_symbol,
positions_get_by_id, close_position, close_all_positions_by_symbol,
close_all_profitable_positions, close_all_losing_positions,
get_all_pending_orders, get_pending_orders_by_symbol, cancel_pending_order,
cancel_all_pending_orders, cancel_pending_orders_by_symbol) *as a thin
facade* over the raw MetaTrader5-API calls that `mt5_bridge` already exposes.
It never bypasses the bridge transport (HMAC auth, serialized lock, constant
verification) and never adds any new trade/execution approval semantics of
its own: every write path is a straight `order_send` with an explicit
`TRADE_ACTION_*` request dict — exactly what `MetaTrader5.order_send` accepts.

All functions accept the same kwargs exposed via MetaTrader5 MSL calls, so
they are *transport-agnostic* (works against the real Windows Python package,
the bridge shim, or a bridge-running fake in tests).  Lifecycle handling
(initialize/shutdown/last_error) is provided by the underlying MetaTrader5
package itself — this module just calls it; it never manages the terminal.
"""

import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Timeframe and helper maps (mirrors ariadng's `mt5_timeframe` mapping).
# ---------------------------------------------------------------------------

_TIMEFRAMES: Dict[str, int] = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1,
}

# ariadng's tiers for close_all_profitable/losing_positions.
_PROFIT_TIER = "PROFIT_TIER"


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _py_time(value: Any) -> _dt.datetime:
    """Convert int/unix-timestamp/str to datetime for copy_rates_* calls."""
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, (int, float)):
        return _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc)
    if isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid date {value!r}: {exc}") from exc
    raise TypeError(f"unsupported date type {type(value).__name__}")


def _rates_to_dicts(rates: Any) -> List[Dict[str, Any]]:
    """Normalize copy_rates_* numpy-struct arrays to plain per-bar dicts."""
    if rates is None:
        return []
    if isinstance(rates, dict):
        return [rates]
    out: List[Dict[str, Any]] = []
    for rec in rates:
        out.append(
            {
                "time": int(rec["time"]),
                "open": float(rec["open"]),
                "high": float(rec["high"]),
                "low": float(rec["low"]),
                "close": float(rec["close"]),
                "tick_volume": int(rec["tick_volume"]),
                "spread": int(rec["spread"]),
                "real_volume": int(rec["real_volume"]),
            }
        )
    return out


def _rate_time(rates: Any, index: int) -> _dt.datetime:
    return _py_time(int(rates[index]["time"]))


def _dedupe(seq: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for row in seq:
        key = (row.get("ticket"), row.get("time"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


class MT5Tools:
    """Named tool surface on top of the MetaTrader5-API via the bridge."""

    def __init__(self, mt5_module=None):
        self._mt5 = mt5_module if mt5_module is not None else mt5

    # -- Registration surface for MCP mode (tools_mt5.py) -------------------

    def tool_specs(self) -> List[Dict[str, Any]]:
        """Names + descriptions + JSON-schema summary of every tool here."""
        return _TOOL_SPECS

    # ============================= Account ===============================

    def get_account_info(self) -> Dict[str, Any]:
        info = self._mt5.account_info()
        if info is None:
            return {"ok": False, "error": self._mt5.last_error(), "account": None}
        return {
            "ok": True,
            "account": {
                "login": int(info.login), "balance": float(info.balance),
                "equity": float(info.equity),
                "margin": float(getattr(info, "margin", 0.0)),
                "free_margin": float(getattr(info, "margin_free", 0.0)),
                "margin_level": float(getattr(info, "margin_level", 0.0)),
                "leverage": int(getattr(info, "leverage", 0)),
                "currency": str(getattr(info, "currency", "")),
                "name": str(getattr(info, "name", "")),
            },
        }

    # ============================ Market data ============================

    def get_symbols(self) -> Dict[str, Any]:
        syms = self._mt5.symbols_get() or ()
        return {
            "ok": True,
            "symbols": [
                {"name": s.name, "digits": int(s.digits),
                 "point": float(s.point), "currency": getattr(s, "currency", "")}
                for s in syms
            ],
        }

    def get_symbol_price(self, symbol: str) -> Dict[str, Any]:
        sym = _normalize_symbol(symbol)
        tick = self._mt5.symbol_info_tick(sym)
        if tick is None:
            return {"ok": False, "error": self._mt5.last_error(), "symbol": sym}
        return {
            "ok": True, "symbol": sym,
            "time": int(tick.time), "bid": float(tick.bid),
            "ask": float(tick.ask), "last": float(getattr(tick, "last", 0.0) or 0.0),
            "volume": int(getattr(tick, "volume", 0)),
        }

    def get_candles_latest(self, symbol: str, timeframe: str = "H1", count: int = 100) -> Dict[str, Any]:
        sym = _normalize_symbol(symbol)
        tf = _TIMEFRAMES.get((timeframe or "H1").upper())
        if tf is None:
            return {"ok": False,
                    "error": f"unsupported timeframe {timeframe!r}; use one of {sorted(_TIMEFRAMES)}",
                    "symbol": sym}
        count = max(1, min(int(count), 1000))
        rates = self._mt5.copy_rates_from_pos(sym, tf, 0, count)
        if rates is None:
            return {"ok": False, "error": self._mt5.last_error(), "symbol": sym}
        return {"ok": True, "symbol": sym, "timeframe": (timeframe or "H1").upper(),
                "count": len(rates), "candles": _rates_to_dicts(rates), }

    def get_candles_by_date(self, symbol: str, timeframe: str, date_from: Any, date_to: Any) -> Dict[str, Any]:
        sym = _normalize_symbol(symbol)
        tf = _TIMEFRAMES.get((timeframe or "H1").upper())
        if tf is None:
            return {"ok": False,
                    "error": f"unsupported timeframe {timeframe!r}; use one of {sorted(_TIMEFRAMES)}",
                    "symbol": sym}
        try:
            from_d, to_d = _py_time(date_from), _py_time(date_to)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc), "symbol": sym}
        rates = self._mt5.copy_rates_range(sym, tf, from_d, to_d)
        if rates is None:
            return {"ok": False, "error": self._mt5.last_error(), "symbol": sym}
        return {"ok": True, "symbol": sym, "timeframe": (timeframe or "H1").upper(),
                "date_from": from_d.isoformat(), "date_to": to_d.isoformat(),
                "count": len(rates), "candles": _rates_to_dicts(rates)}

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        sym = _normalize_symbol(symbol)
        info = self._mt5.symbol_info(sym)
        if info is None:
            return {"ok": False, "error": self._mt5.last_error(), "symbol": sym}
        data_keys = ("name", "digits", "point", "spread", "currency", "description",
                     "trade_mode", "filling_mode", "volume_min", "volume_max",
                     "volume_step", "swap_long", "swap_short")
        data = {}
        for k in data_keys:
            data[k] = getattr(info, k, None)
        data["spread"] = int(data["spread"] or 0)
        data["digits"] = int(data["digits"] or 0)
        return {"ok": True, "symbol": sym, "info": data}

    # ========================== Order execution ==========================

    def place_market_order(self, symbol: str, order_type: str, volume: float,
                           sl: Optional[float] = None, tp: Optional[float] = None,
                           deviation: int = 20, comment: str = "") -> Dict[str, Any]:
        sym = _normalize_symbol(symbol)
        t = (order_type or "").upper()
        if t in ("BUY", "SELL"):
            action = self._mt5.TRADE_ACTION_DEAL
            ot = self._mt5.ORDER_TYPE_BUY if t == "BUY" else self._mt5.ORDER_TYPE_SELL
        else:
            return {"ok": False, "error": f"order_type must be BUY or SELL, got {order_type!r}"}
        request = {
            "action": action, "symbol": sym, "volume": float(volume), "type": ot,
            "deviation": int(deviation), "magic": 0, "comment": comment or "",
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }
        if sl is not None:
            request["sl"] = float(sl)
        if tp is not None:
            request["tp"] = float(tp)
        res = self._mt5.order_send(request)
        if res is None:
            return {"ok": False, "error": self._mt5.last_error(), "request_sent": request}
        retcode = int(res.retcode)
        return {
            "ok": retcode == self._mt5.TRADE_RETCODE_DONE or retcode == self._mt5.TRADE_RETCODE_PLACED,
            "retcode": retcode, "comment": getattr(res, "comment", ""),
            "order": int(getattr(res, "order", 0)), "deal": int(getattr(res, "deal", 0)),
            "price": float(getattr(res, "price", 0.0) or 0.0),
            "volume": float(getattr(res, "volume", 0.0) or 0.0),
            "request_sent": request,
        }

    def place_pending_order(self, symbol: str, order_type: str, volume: float, price: float,
                            sl: Optional[float] = None, tp: Optional[float] = None,
                            comment: str = "") -> Dict[str, Any]:
        sym = _normalize_symbol(symbol)
        t = (order_type or "").upper()
        order_type_map = {
            "BUY_LIMIT": self._mt5.ORDER_TYPE_BUY_LIMIT,
            "SELL_LIMIT": self._mt5.ORDER_TYPE_SELL_LIMIT,
            "BUY_STOP": self._mt5.ORDER_TYPE_BUY_STOP,
            "SELL_STOP": self._mt5.ORDER_TYPE_SELL_STOP,
        }
        if t not in order_type_map:
            return {"ok": False,
                    "error": f"order_type must be one of {sorted(order_type_map)}, got {order_type!r}"}
        request = {
            "action": self._mt5.TRADE_ACTION_PENDING, "symbol": sym,
            "volume": float(volume), "type": order_type_map[t], "price": float(price),
            "magic": 0, "comment": comment or "",
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_RETURN,
        }
        if sl is not None:
            request["sl"] = float(sl)
        if tp is not None:
            request["tp"] = float(tp)
        res = self._mt5.order_send(request)
        if res is None:
            return {"ok": False, "error": self._mt5.last_error(), "request_sent": request}
        retcode = int(res.retcode)
        return {
            "ok": retcode in (self._mt5.TRADE_RETCODE_DONE, self._mt5.TRADE_RETCODE_PLACED),
            "retcode": retcode, "comment": getattr(res, "comment", ""),
            "order": int(getattr(res, "order", 0)),
            "request_sent": request,
        }

    def modify_position(self, position_ticket: int, sl: Optional[float] = None,
                        tp: Optional[float] = None) -> Dict[str, Any]:
        request = {"action": self._mt5.TRADE_ACTION_SLTP, "position": int(position_ticket)}
        if sl is not None:
            request["sl"] = float(sl)
        if tp is not None:
            request["tp"] = float(tp)
        if "sl" not in request and "tp" not in request:
            return {"ok": False, "error": "modify_position requires at least sl or tp"}
        res = self._mt5.order_send(request)
        if res is None:
            return {"ok": False, "error": self._mt5.last_error(), "request_sent": request}
        retcode = int(res.retcode)
        return {
            "ok": retcode in (self._mt5.TRADE_RETCODE_DONE, self._mt5.TRADE_RETCODE_PLACED,
                              self._mt5.TRADE_RETCODE_NO_CHANGES),
            "retcode": retcode, "comment": getattr(res, "comment", ""),
            "request_sent": request,
        }

    def modify_pending_order(self, ticket: int, price: Optional[float] = None,
                             sl: Optional[float] = None, tp: Optional[float] = None) -> Dict[str, Any]:
        # TRADE_ACTION_MODIFY requires the existing order to be re-fetched for
        # type/time; the officialtablet docs rely on the caller supplying these.
        order = self._mt5.orders_get(ticket=ticket)
        if not order:
            return {"ok": False, "error": f"pending order {ticket} not found", "ticket": int(ticket)}
        o = order[0]
        request = {
            "action": self._mt5.TRADE_ACTION_MODIFY, "order": int(ticket),
            "price": float(price if price is not None else o.price_open),
            "type_time": getattr(o, "type_time", self._mt5.ORDER_TIME_GTC),
        }
        if sl is not None:
            request["sl"] = float(sl)
        if tp is not None:
            request["tp"] = float(tp)
        res = self._mt5.order_send(request)
        if res is None:
            return {"ok": False, "error": self._mt5.last_error(), "request_sent": request}
        retcode = int(res.retcode)
        return {
            "ok": retcode in (self._mt5.TRADE_RETCODE_DONE, self._mt5.TRADE_RETCODE_PLACED,
                              self._mt5.TRADE_RETCODE_NO_CHANGES),
            "retcode": retcode, "comment": getattr(res, "comment", ""),
            "request_sent": request,
        }

    # ======================== Position management ========================

    def get_all_positions(self) -> Dict[str, Any]:
        positions = self._mt5.positions_get() or ()
        return {"ok": True, "count": len(positions), "positions": [self._pos_dict(p) for p in positions]}

    def get_positions_by_symbol(self, symbol: str) -> Dict[str, Any]:
        sym = _normalize_symbol(symbol)
        positions = self._mt5.positions_get(symbol=sym)
        return {"ok": positions is not None, "symbol": sym,
                "count": len(positions or ()),
                "positions": [self._pos_dict(p) for p in (positions or ())]}

    def get_positions_by_id(self, ticket: int) -> Dict[str, Any]:
        positions = self._mt5.positions_get(ticket=int(ticket))
        return {"ok": bool(positions), "ticket": int(ticket), "count": len(positions or ()),
                "positions": [self._pos_dict(p) for p in (positions or ())]}

    def close_position(self, ticket: int, volume: Optional[float] = None,
                       deviation: int = 20, comment: str = "") -> Dict[str, Any]:
        positions = self._mt5.positions_get(ticket=int(ticket))
        if not positions:
            return {"ok": False, "error": f"position {ticket} not found", "ticket": int(ticket)}
        p = positions[0]
        is_buy = p.type == self._mt5.ORDER_TYPE_BUY
        tick = self._mt5.symbol_info_tick(p.symbol)
        if tick is None:
            return {"ok": False, "error": self._mt5.last_error(), "ticket": int(ticket)}
        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "position": int(ticket), "symbol": p.symbol,
            "volume": float(volume if volume is not None else p.volume),
            "type": self._mt5.ORDER_TYPE_SELL if is_buy else self._mt5.ORDER_TYPE_BUY,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": int(deviation), "magic": 0,
            "comment": comment or "close via MCP",
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }
        res = self._mt5.order_send(request)
        if res is None:
            return {"ok": False, "error": self._mt5.last_error(), "request_sent": request}
        retcode = int(res.retcode)
        return {
            "ok": retcode in (self._mt5.TRADE_RETCODE_DONE, self._mt5.TRADE_RETCODE_PLACED),
            "retcode": retcode, "ticket": int(ticket),
            "comment": getattr(res, "comment", ""), "request_sent": request,
        }

    def close_all_positions(self) -> Dict[str, Any]:
        return self._close_many(None)

    def close_all_positions_by_symbol(self, symbol: str) -> Dict[str, Any]:
        return self._close_many(_normalize_symbol(symbol))

    def close_all_profitable_positions(self) -> Dict[str, Any]:
        return self._close_many(None, only_profitable=True)

    def close_all_losing_positions(self) -> Dict[str, Any]:
        return self._close_many(None, only_unprofitable=True)

    def _close_many(self, symbol_filter: Optional[str] = None,
                    only_profitable: bool = False, only_unprofitable: bool = False) -> Dict[str, Any]:
        if symbol_filter:
            positions = self._mt5.positions_get(symbol=symbol_filter)
        else:
            positions = self._mt5.positions_get()
        if positions is None:
            return {"ok": False, "error": self._mt5.last_error(),
                    "closed": [], "failed": [], "skipped": 0}
        attempts = []
        for p in positions:
            profitable = p.profit >= 0
            if only_profitable and not profitable:
                continue
            if only_unprofitable and profitable:
                continue
            attempts.append(self.close_position(p.ticket))
        return {
            "ok": True,
            "closed": [a for a in attempts if a["ok"]],
            "failed": [a for a in attempts if not a["ok"]],
            "skipped": len(positions) - len(attempts),
            "symbol_filter": symbol_filter or "",
        }

    # ========================= Pending orders ===========================

    def get_all_pending_orders(self) -> Dict[str, Any]:
        orders = self._mt5.orders_get() or ()
        return {"ok": True, "count": len(orders), "orders": [self._order_dict(o) for o in orders]}

    def get_pending_orders_by_symbol(self, symbol: str) -> Dict[str, Any]:
        sym = _normalize_symbol(symbol)
        orders = self._mt5.orders_get(symbol=sym)
        return {"ok": orders is not None, "symbol": sym, "count": len(orders or ()),
                "orders": [self._order_dict(o) for o in (orders or ())]}

    def cancel_pending_order(self, ticket: int) -> Dict[str, Any]:
        request = {"action": self._mt5.TRADE_ACTION_REMOVE, "order": int(ticket)}
        res = self._mt5.order_send(request)
        if res is None:
            return {"ok": False, "error": self._mt5.last_error(), "request_sent": request}
        retcode = int(res.retcode)
        return {
            "ok": retcode in (self._mt5.TRADE_RETCODE_DONE, self._mt5.TRADE_RETCODE_PLACED),
            "retcode": retcode, "ticket": int(ticket), "request_sent": request,
        }

    def cancel_all_pending_orders(self) -> Dict[str, Any]:
        return self._cancel_many(None)

    def cancel_pending_orders_by_symbol(self, symbol: str) -> Dict[str, Any]:
        return self._cancel_many(_normalize_symbol(symbol))

    def _cancel_many(self, symbol_filter: Optional[str] = None) -> Dict[str, Any]:
        if symbol_filter:
            orders = self._mt5.orders_get(symbol=symbol_filter)
        else:
            orders = self._mt5.orders_get()
        if orders is None:
            return {"ok": False, "error": self._mt5.last_error(),
                    "cancelled": [], "failed": [], "count": 0}
        attempts = [self.cancel_pending_order(o.ticket) for o in orders]
        return {
            "ok": True,
            "cancelled": [a for a in attempts if a["ok"]],
            "failed": [a for a in attempts if not a["ok"]],
            "count": len(orders),
            "symbol_filter": symbol_filter or "",
        }

    # =========================== History ================================

    def get_deals(self, symbol: Optional[str] = None, date_from: Any = None,
                  date_to: Any = None) -> Dict[str, Any]:
        """Deal history.  No args → last 30 days; symbol/date range also supported."""
        sym = _normalize_symbol(symbol) if symbol else None
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        if date_from is None and date_to is None:
            date_from = now - _dt.timedelta(days=30)
            date_to = now
        try:
            from_d, to_d = _py_time(date_from), _py_time(date_to)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        if sym:
            deals = self._mt5.history_deals_get(sym, from_d, to_d)
        else:
            deals = self._mt5.history_deals_get(from_d, to_d)
        if deals is None:
            return {"ok": False, "error": self._mt5.last_error()}
        return {"ok": True, "deals": [self._deal_dict(d) for d in deals], "count": len(deals)}

    def get_orders(self, symbol: Optional[str] = None, date_from: Any = None,
                   date_to: Any = None) -> Dict[str, Any]:
        """Order history.  No args → last 30 days; symbol/date range also supported."""
        sym = _normalize_symbol(symbol) if symbol else None
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        if date_from is None and date_to is None:
            date_from = now - _dt.timedelta(days=30)
            date_to = now
        try:
            from_d, to_d = _py_time(date_from), _py_time(date_to)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        if sym:
            orders = self._mt5.history_orders_get(sym, from_d, to_d)
        else:
            orders = self._mt5.history_orders_get(from_d, to_d)
        if orders is None:
            return {"ok": False, "error": self._mt5.last_error()}
        return {"ok": True, "orders": [self._order_dict(o) for o in orders], "count": len(orders)}

    # ============================ helpers ================================

    @staticmethod
    def _pos_dict(p: Any) -> Dict[str, Any]:
        return {
            "ticket": int(p.ticket), "symbol": str(p.symbol),
            "type": "BUY" if p.type == 0 else "SELL",
            "volume": float(p.volume), "price_open": float(p.price_open),
            "price_current": float(getattr(p, "price_current", 0.0)),
            "sl": float(p.sl), "tp": float(p.tp),
            "profit": float(p.profit), "swap": float(p.swap),
            "time": int(p.time), "comment": str(getattr(p, "comment", "")),
        }

    @staticmethod
    def _order_dict(o: Any) -> Dict[str, Any]:
        return {
            "ticket": int(o.ticket), "symbol": str(o.symbol),
            "type": int(o.type), "volume": float(o.volume_initial or o.volume_current or 0),
            "price_open": float(o.price_open), "sl": float(o.price_sl),
            "tp": float(o.price_tp),
            "state": int(getattr(o, "state", -1)),
            "time_setup": int(getattr(o, "time_setup", 0)),
            "comment": str(getattr(o, "comment", "")),
        }

    @staticmethod
    def _deal_dict(d: Any) -> Dict[str, Any]:
        return {
            "ticket": int(d.ticket), "order": int(getattr(d, "order", 0)),
            "symbol": str(d.symbol), "type": int(d.type),
            "entry": int(getattr(d, "entry", -1)),
            "volume": float(d.volume), "price": float(d.price),
            "profit": float(d.profit), "commission": float(d.commission),
            "swap": float(d.swap),
            "time": int(getattr(d, "time", 0)),
            "comment": str(getattr(d, "comment", "")),
        }


# ---------------------------------------------------------------------------
# Tool spec catalog (ariadng naming): helps the MCP layer in kronos_mcp build
# its own @mcp.tool registrations without re-deriving parameter shapes.
# ---------------------------------------------------------------------------

_TIMEFRAME_HELP = "M1|M5|M15|M30|H1|H4|D1|W1|MN1"

_TOOL_SPECS: List[Dict[str, Any]] = [
    {"name": "mt5_get_account_info", "group": "account",
     "description": "Get balance, equity, margin level, leverage, currency",
     "args": []},
    {"name": "mt5_get_symbols", "group": "market_data",
     "description": "List all available trading symbols",
     "args": []},
    {"name": "mt5_get_symbol_price", "group": "market_data",
     "description": "Get current bid/ask price for a symbol",
     "args": [{"name": "symbol", "type": "string", "required": True}]},
    {"name": "mt5_get_symbol_info", "group": "market_data",
     "description": "Get detailed symbol information",
     "args": [{"name": "symbol", "type": "string", "required": True}]},
    {"name": "mt5_get_candles_latest", "group": "market_data",
     "description": "Get recent price candles (OHLCV data)",
     "args": [{"name": "symbol", "type": "string", "required": True},
              {"name": "timeframe", "type": "string", "required": False},
              {"name": "count", "type": "integer", "required": False}]},
    {"name": "mt5_get_candles_by_date", "group": "market_data",
     "description": "Get historical candles for a date range",
     "args": [{"name": "symbol", "type": "string", "required": True},
              {"name": "timeframe", "type": "string", "required": True},
              {"name": "date_from", "type": "string", "required": True},
              {"name": "date_to", "type": "string", "required": True}]},
    {"name": "mt5_place_market_order", "group": "order_execution",
     "description": "Execute instant BUY/SELL orders",
     "args": [{"name": "symbol", "type": "string", "required": True},
              {"name": "order_type", "type": "string", "required": True},
              {"name": "volume", "type": "number", "required": True},
              {"name": "sl", "type": "number", "required": False},
              {"name": "tp", "type": "number", "required": False},
              {"name": "deviation", "type": "integer", "required": False},
              {"name": "comment", "type": "string", "required": False}]},
    {"name": "mt5_place_pending_order", "group": "order_execution",
     "description": "Place limit/stop orders for future execution",
     "args": [{"name": "symbol", "type": "string", "required": True},
              {"name": "order_type", "type": "string", "required": True},
              {"name": "volume", "type": "number", "required": True},
              {"name": "price", "type": "number", "required": True},
              {"name": "sl", "type": "number", "required": False},
              {"name": "tp", "type": "number", "required": False},
              {"name": "comment", "type": "string", "required": False}]},
    {"name": "mt5_modify_position", "group": "order_execution",
     "description": "Update stop loss or take profit on an open position",
     "args": [{"name": "position_ticket", "type": "integer", "required": True},
              {"name": "sl", "type": "number", "required": False},
              {"name": "tp", "type": "number", "required": False}]},
    {"name": "mt5_modify_pending_order", "group": "order_execution",
     "description": "Modify pending order parameters",
     "args": [{"name": "ticket", "type": "integer", "required": True},
              {"name": "price", "type": "number", "required": False},
              {"name": "sl", "type": "number", "required": False},
              {"name": "tp", "type": "number", "required": False}]},
    {"name": "mt5_get_all_positions", "group": "positions",
     "description": "View all open positions",
     "args": []},
    {"name": "mt5_get_positions_by_symbol", "group": "positions",
     "description": "Filter open positions by trading pair",
     "args": [{"name": "symbol", "type": "string", "required": True}]},
    {"name": "mt5_get_positions_by_id", "group": "positions",
     "description": "Get one position by ticket",
     "args": [{"name": "ticket", "type": "integer", "required": True}]},
    {"name": "mt5_close_position", "group": "positions",
     "description": "Close a specific position by ticket (optionally partial volume)",
     "args": [{"name": "ticket", "type": "integer", "required": True},
              {"name": "volume", "type": "number", "required": False},
              {"name": "deviation", "type": "integer", "required": False}]},
    {"name": "mt5_close_all_positions", "group": "positions",
     "description": "Close all open positions",
     "args": []},
    {"name": "mt5_close_all_positions_by_symbol", "group": "positions",
     "description": "Close all positions for a symbol",
     "args": [{"name": "symbol", "type": "string", "required": True}]},
    {"name": "mt5_close_all_profitable_positions", "group": "positions",
     "description": "Close only winning trades",
     "args": []},
    {"name": "mt5_close_all_losing_positions", "group": "positions",
     "description": "Close only losing trades",
     "args": []},
    {"name": "mt5_get_all_pending_orders", "group": "pending",
     "description": "List all pending orders",
     "args": []},
    {"name": "mt5_get_pending_orders_by_symbol", "group": "pending",
     "description": "Filter pending orders by symbol",
     "args": [{"name": "symbol", "type": "string", "required": True}]},
    {"name": "mt5_cancel_pending_order", "group": "pending",
     "description": "Cancel a specific pending order",
     "args": [{"name": "ticket", "type": "integer", "required": True}]},
    {"name": "mt5_cancel_all_pending_orders", "group": "pending",
     "description": "Cancel all pending orders",
     "args": []},
    {"name": "mt5_cancel_pending_orders_by_symbol", "group": "pending",
     "description": "Cancel pending orders for a symbol",
     "args": [{"name": "symbol", "type": "string", "required": True}]},
    {"name": "mt5_get_deals", "group": "history",
     "description": "Get historical completed trades (last 30d by default)",
     "args": [{"name": "symbol", "type": "string", "required": False},
              {"name": "date_from", "type": "string", "required": False},
              {"name": "date_to", "type": "string", "required": False}]},
    {"name": "mt5_get_orders", "group": "history",
     "description": "Get historical order records (last 30d by default)",
     "args": [{"name": "symbol", "type": "string", "required": False},
              {"name": "date_from", "type": "string", "required": False},
              {"name": "date_to", "type": "string", "required": False}]},
]
