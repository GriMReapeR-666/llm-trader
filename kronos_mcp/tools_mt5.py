"""MT5 execution tools — retrieve & modify positions/orders via FastMCP.

This is the kronos_mcp FastMCP layer that registers the compat/mt5_bridge
trading-tools taxonomy as real MCP tools.  Every write (order_send) runs
under the bridge's serialized lock, so concurrent MCP clients cannot
corrupt each other.  Positions, orders and history are all routed through
one entry point (``compat/mt5_bridge/trading_tools.py``).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .utils import mcp

logger = logging.getLogger(__name__)


def _get_tools():
    """Lazy-load the bridge-tied trading-tools layer.

    Import lives inside the function so building the tool catalog and
    running an unrelated tool list stays possible without a Windows
    MetaTrader5 package installed (single-line no-op for CI).
    """
    try:
        from compat.mt5_bridge.trading_tools import MT5Tools
    except ImportError:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "compat" / "mt5_bridge"))
        from trading_tools import MT5Tools
    return MT5Tools()


def _err(exc: Exception) -> Dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ============================= Account ===============================

@mcp.tool
def mt5_get_account_info() -> Dict[str, Any]:
    """Balance, equity, margin level, leverage, currency for the connected account."""
    try:
        return _get_tools().get_account_info()
    except Exception as exc:
        logger.exception("mt5_get_account_info failed")
        return _err(exc)


# ============================ Market data ============================

@mcp.tool
def mt5_get_symbols() -> Dict[str, Any]:
    """List all available trading symbols."""
    try:
        return _get_tools().get_symbols()
    except Exception as exc:
        logger.exception("mt5_get_symbols failed")
        return _err(exc)


@mcp.tool
def mt5_get_symbol_price(symbol: str) -> Dict[str, Any]:
    """Current bid/ask price for a symbol."""
    try:
        return _get_tools().get_symbol_price(symbol=symbol)
    except Exception as exc:
        logger.exception("mt5_get_symbol_price failed")
        return _err(exc)


@mcp.tool
def mt5_get_symbol_info(symbol: str) -> Dict[str, Any]:
    """Detailed symbol information (digits, point, spread, order constraints)."""
    try:
        return _get_tools().get_symbol_info(symbol=symbol)
    except Exception as exc:
        logger.exception("mt5_get_symbol_info failed")
        return _err(exc)


@mcp.tool
def mt5_get_candles_latest(symbol: str, timeframe: str = "H1", count: int = 100) -> Dict[str, Any]:
    """Recent price candles (OHLCV data)."""
    try:
        return _get_tools().get_candles_latest(symbol=symbol, timeframe=timeframe, count=count)
    except Exception as exc:
        logger.exception("mt5_get_candles_latest failed")
        return _err(exc)


@mcp.tool
def mt5_get_candles_by_date(symbol: str, timeframe: str, date_from: str, date_to: str) -> Dict[str, Any]:
    """Historical candles for a date range (ISO dates)."""
    try:
        return _get_tools().get_candles_by_date(symbol=symbol, timeframe=timeframe,
                                                date_from=date_from, date_to=date_to)
    except Exception as exc:
        logger.exception("mt5_get_candles_by_date failed")
        return _err(exc)


# ========================== Order execution ==========================

@mcp.tool
def mt5_place_market_order(symbol: str, order_type: str, volume: float,
                           sl: Optional[float] = None, tp: Optional[float] = None,
                           deviation: int = 20, comment: str = "") -> Dict[str, Any]:
    """Execute instant BUY/SELL order. order_type='BUY'|'SELL'."""
    try:
        return _get_tools().place_market_order(symbol=symbol, order_type=order_type, volume=volume,
                                               sl=sl, tp=tp, deviation=deviation, comment=comment)
    except Exception as exc:
        logger.exception("mt5_place_market_order failed")
        return _err(exc)


@mcp.tool
def mt5_place_pending_order(symbol: str, order_type: str, volume: float, price: float,
                            sl: Optional[float] = None, tp: Optional[float] = None,
                            comment: str = "") -> Dict[str, Any]:
    """Place limit/stop orders for future execution. order_type='BUY_LIMIT'|'SELL_LIMIT'|'BUY_STOP'|'SELL_STOP'."""
    try:
        return _get_tools().place_pending_order(symbol=symbol, order_type=order_type, volume=volume,
                                                price=price, sl=sl, tp=tp, comment=comment)
    except Exception as exc:
        logger.exception("mt5_place_pending_order failed")
        return _err(exc)


@mcp.tool
def mt5_modify_position(position_ticket: int, sl: Optional[float] = None, tp: Optional[float] = None) -> Dict[str, Any]:
    """Update stop loss or take profit on an open position."""
    try:
        return _get_tools().modify_position(position_ticket=position_ticket, sl=sl, tp=tp)
    except Exception as exc:
        logger.exception("mt5_modify_position failed")
        return _err(exc)


@mcp.tool
def mt5_modify_pending_order(ticket: int, price: Optional[float] = None,
                             sl: Optional[float] = None, tp: Optional[float] = None) -> Dict[str, Any]:
    """Modify pending order parameters (price/sl/tp)."""
    try:
        return _get_tools().modify_pending_order(ticket=ticket, price=price, sl=sl, tp=tp)
    except Exception as exc:
        logger.exception("mt5_modify_pending_order failed")
        return _err(exc)


# ======================== Position management ========================

@mcp.tool
def mt5_get_all_positions() -> Dict[str, Any]:
    """View all open positions."""
    try:
        return _get_tools().get_all_positions()
    except Exception as exc:
        logger.exception("mt5_get_all_positions failed")
        return _err(exc)


@mcp.tool
def mt5_get_positions_by_symbol(symbol: str) -> Dict[str, Any]:
    """Filter open positions by trading pair."""
    try:
        return _get_tools().get_positions_by_symbol(symbol=symbol)
    except Exception as exc:
        logger.exception("mt5_get_positions_by_symbol failed")
        return _err(exc)


@mcp.tool
def mt5_get_positions_by_id(ticket: int) -> Dict[str, Any]:
    """Get one open position by ticket."""
    try:
        return _get_tools().get_positions_by_id(ticket=ticket)
    except Exception as exc:
        logger.exception("mt5_get_positions_by_id failed")
        return _err(exc)


@mcp.tool
def mt5_close_position(ticket: int, volume: Optional[float] = None, deviation: int = 20,
                       comment: str = "") -> Dict[str, Any]:
    """Close a specific position by ticket (optionally partial volume)."""
    try:
        return _get_tools().close_position(ticket=ticket, volume=volume,
                                           deviation=deviation, comment=comment)
    except Exception as exc:
        logger.exception("mt5_close_position failed")
        return _err(exc)


@mcp.tool
def mt5_close_all_positions() -> Dict[str, Any]:
    """Close all open positions."""
    try:
        return _get_tools().close_all_positions()
    except Exception as exc:
        logger.exception("mt5_close_all_positions failed")
        return _err(exc)


@mcp.tool
def mt5_close_all_positions_by_symbol(symbol: str) -> Dict[str, Any]:
    """Close all positions for a symbol."""
    try:
        return _get_tools().close_all_positions_by_symbol(symbol=symbol)
    except Exception as exc:
        logger.exception("mt5_close_all_positions_by_symbol failed")
        return _err(exc)


@mcp.tool
def mt5_close_all_profitable_positions() -> Dict[str, Any]:
    """Close only winning trades."""
    try:
        return _get_tools().close_all_profitable_positions()
    except Exception as exc:
        logger.exception("mt5_close_all_profitable_positions failed")
        return _err(exc)


@mcp.tool
def mt5_close_all_losing_positions() -> Dict[str, Any]:
    """Close only losing trades."""
    try:
        return _get_tools().close_all_losing_positions()
    except Exception as exc:
        logger.exception("mt5_close_all_losing_positions failed")
        return _err(exc)


# ========================= Pending orders ===========================

@mcp.tool
def mt5_get_all_pending_orders() -> Dict[str, Any]:
    """List all pending orders."""
    try:
        return _get_tools().get_all_pending_orders()
    except Exception as exc:
        logger.exception("mt5_get_all_pending_orders failed")
        return _err(exc)


@mcp.tool
def mt5_get_pending_orders_by_symbol(symbol: str) -> Dict[str, Any]:
    """Filter pending orders by symbol."""
    try:
        return _get_tools().get_pending_orders_by_symbol(symbol=symbol)
    except Exception as exc:
        logger.exception("mt5_get_pending_orders_by_symbol failed")
        return _err(exc)


@mcp.tool
def mt5_cancel_pending_order(ticket: int) -> Dict[str, Any]:
    """Cancel a specific pending order."""
    try:
        return _get_tools().cancel_pending_order(ticket=ticket)
    except Exception as exc:
        logger.exception("mt5_cancel_pending_order failed")
        return _err(exc)


@mcp.tool
def mt5_cancel_all_pending_orders() -> Dict[str, Any]:
    """Cancel all pending orders."""
    try:
        return _get_tools().cancel_all_pending_orders()
    except Exception as exc:
        logger.exception("mt5_cancel_all_pending_orders failed")
        return _err(exc)


@mcp.tool
def mt5_cancel_pending_orders_by_symbol(symbol: str) -> Dict[str, Any]:
    """Cancel pending orders for a symbol."""
    try:
        return _get_tools().cancel_pending_orders_by_symbol(symbol=symbol)
    except Exception as exc:
        logger.exception("mt5_cancel_pending_orders_by_symbol failed")
        return _err(exc)


# =========================== History ================================

@mcp.tool
def mt5_get_deals(symbol: Optional[str] = None, date_from: Optional[str] = None,
                  date_to: Optional[str] = None) -> Dict[str, Any]:
    """Historical completed trades (last 30d by default)."""
    try:
        return _get_tools().get_deals(symbol=symbol, date_from=date_from, date_to=date_to)
    except Exception as exc:
        logger.exception("mt5_get_deals failed")
        return _err(exc)


@mcp.tool
def mt5_get_orders(symbol: Optional[str] = None, date_from: Optional[str] = None,
                   date_to: Optional[str] = None) -> Dict[str, Any]:
    """Historical order records (last 30d by default)."""
    try:
        return _get_tools().get_orders(symbol=symbol, date_from=date_from, date_to=date_to)
    except Exception as exc:
        logger.exception("mt5_get_orders failed")
        return _err(exc)
