"""kronos_mcp.tools_mt5: FastMCP registration of the 25 mt5_* bookkeeping tools.

Bridges the compat/mt5_bridge trading_tools facade to a real FastMCP server.
Every tool delegates to MT5Tools, which itself routes through the bridge
(HMAC + serialized lock).  End-to-end MCP client behavior (stdio) is
exercised in test_mcp_stdio_roundtrip.py covering the actual stdio
JSON-RPC path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kronos_mcp.tools_mt5 as t5  # noqa: E402


def test_tools_mt5_registers_25_tools():
    names = {fn.__name__ for fn in t5._get_tools_mcp_fns() if fn.startswith("mt5_")} \
        if False else None
    # Instead: the module's tool set is governed by the FastMCP shared registry
    # in kronos_mcp.utils.  Contract: tool count == 25.
    from kronos_mcp.utils import mcp
    import asyncio
    tools = asyncio.run(mcp.list_tools())
    mt5_names = sorted(t.name for t in tools if t.name.startswith("mt5_"))
    assert len(mt5_names) == 25, mt5_names


def test_tool_names_match_ariadng_taxonomy():
    from kronos_mcp.utils import mcp
    import asyncio
    tools = {t.name for t in asyncio.run(mcp.list_tools()) if t.name.startswith("mt5_")}
    expected = {
        "mt5_get_account_info", "mt5_get_symbols", "mt5_get_symbol_price",
        "mt5_get_symbol_info", "mt5_get_candles_latest", "mt5_get_candles_by_date",
        "mt5_place_market_order", "mt5_place_pending_order",
        "mt5_modify_position", "mt5_modify_pending_order",
        "mt5_get_all_positions", "mt5_get_positions_by_symbol", "mt5_get_positions_by_id",
        "mt5_close_position", "mt5_close_all_positions",
        "mt5_close_all_positions_by_symbol", "mt5_close_all_profitable_positions",
        "mt5_close_all_losing_positions",
        "mt5_get_all_pending_orders", "mt5_get_pending_orders_by_symbol",
        "mt5_cancel_pending_order", "mt5_cancel_all_pending_orders",
        "mt5_cancel_pending_orders_by_symbol",
        "mt5_get_deals", "mt5_get_orders",
    }
    assert tools == expected


@pytest.mark.asyncio
async def test_mt5_tools_fail_closed_without_terminal():
    """Without any real MT5/bridge, tools report ok=False, never raise raw."""
    from kronos_mcp.utils import mcp

    # No bridge configured → _get_tools() still imports fine on Windows (real
    # package installed) but every call hits no terminal.
    res = await mcp.call_tool("mt5_get_account_info", {})
    data = res[0] if isinstance(res, tuple) else res
    # FastMCP 3.x wraps results in ToolResult with structured_content.
    payload = getattr(data, "structured_content", None)
    if payload is None:
        payload = data if isinstance(data, dict) else None
    assert isinstance(payload, dict), type(data)
    # Either "ok": False or "error" — never a raw exception.
    assert payload.get("ok") is False or "error" in payload


def test_no_mt5_import_leaks_into_kronos_tools_forecast():
    """The kronos (forecast) tier stays independent of the MT5 tier."""
    import kronos_mcp.tools_forecast as f
    assert not any(k.startswith("mt5_") for k in vars(f))
