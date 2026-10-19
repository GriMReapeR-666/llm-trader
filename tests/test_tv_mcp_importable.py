#!/usr/bin/env python3
"""Prove the vendored TradingView MCP is importable + tool-listed from repo root.

Runs from repo root (cwd). Does NOT call mcp.run() / stdio — only import +
list_tools(), so it is a safe CI check and does not touch network or block.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TV_SRC = REPO_ROOT / "vendor" / "tradingview-mcp" / "src"

def _ensure_tv_src_on_path() -> None:
    if str(TV_SRC) not in sys.path:
        sys.path.insert(0, str(TV_SRC))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

def test_tv_mcp_importable_and_lists_tools() -> None:
    _ensure_tv_src_on_path()
    import tradingview_mcp.server as ts
    assert hasattr(ts, "main"), "tradingview_mcp.server missing main() entrypoint"
    assert getattr(ts, "mcp", None) is not None, "tradingview_mcp.server missing mcp FastMCP"

    async def go() -> list[str]:
        tools = await ts.mcp.list_tools()
        return [t.name for t in tools]

    names = asyncio.run(go())
    assert len(names) >= 30, f"expected >=30 TV tools, got {len(names)}: {names[:20]}"
    # Spot-check a few canonical tools from the vendored server.
    for needle in ("top_gainers", "coin_analysis", "bollinger_scan",
                    "yahoo_price", "market_snapshot", "futures_market_overview"):
        assert needle in names, f"missing TV tool {needle!r}; available={names[:10]}"
    return names


if __name__ == "__main__":
    names = test_tv_mcp_importable_and_lists_tools()
    print("TV MCP importable from repo root: OK")
    print(f"tools listed: {len(names)} -> {', '.join(names[:12])} ...")
