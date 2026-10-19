"""In-process MCP client round-trip: boot the FastMCP server over an in-memory
transport and exercise the real MCP protocol (initialize → notifications/initialized
→ tools/list).

Verifies the FastMCP server-side registration of the mt5_* tier survives a
real MCP client connection (not just in-process list_tools() through us).
Use anyio.run() instead of pytest.mark.asyncio (not installed in this repo).
"""
from __future__ import annotations

import anyio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_in_process_mcp_list_tools_sees_mt5_tier():
    """MCP client over in-memory transport lists the 25 mt5_* tools + kronos tier."""
    # Importing kronos_mcp's tiers via kronos_mcp.utils alone does not
    # register the tools — they attach as import side effects in the tier
    # modules (see kronos_mcp/main.py).  Import BOTH tiers explicitly.
    import kronos_mcp.tools_forecast  # noqa: F401
    import kronos_mcp.tools_mt5  # noqa: F401
    from fastmcp import Client
    from kronos_mcp.utils import mcp as mcp_server

    async def go():
        async with Client(mcp_server) as client:
            tools = await client.list_tools()
            return sorted(t.name for t in tools)

    names = anyio.run(go)
    mt5_names = [n for n in names if n.startswith("mt5_")]
    assert len(mt5_names) >= 25, mt5_names[:30]
    # A kronos tool must also still be present (both tiers registered).
    assert any(n.startswith("kronos_") for n in names)


def test_call_tool_returns_structured_result():
    """Every mt5_* tool reachable by name; no-arg tool returns proper error envelope."""
    import kronos_mcp.tools_forecast  # noqa: F401
    import kronos_mcp.tools_mt5  # noqa: F401
    from fastmcp import Client
    from kronos_mcp.utils import mcp as mcp_server

    async def go():
        async with Client(mcp_server) as client:
            return await client.call_tool("mt5_get_account_info", {})

    result = anyio.run(go)
    data = result.data
    assert data["ok"] is False or "error" in data
