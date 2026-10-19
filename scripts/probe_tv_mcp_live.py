#!/usr/bin/env python3
"""Final probe: boot the TradingView MCP server over stdio, list tools,
spot-check two real tools, shut down. Diagnostic helper only."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from fastmcp.client.transports.stdio import PythonStdioTransport

ROOT = Path(".").resolve()
TV_SRC = str(ROOT / "vendor" / "tradingview-mcp" / "src")
TV_ERR_LOG = ROOT / "scripts" / "probe_tv_mcp_live.err"
TV_ERR_LOG.unlink(missing_ok=True)

transport = PythonStdioTransport(
    script_path=str(ROOT / "scripts" / "start_tv_mcp.py"),
    args=[],
    env={
        "PYTHONPATH": TV_SRC + ":" + os.environ.get("PYTHONPATH", ""),
        "LOG_LEVEL": "WARNING",
    },
    cwd=str(ROOT),
    keep_alive=False,
    log_file=TV_ERR_LOG,
)


async def main() -> None:
    async with Client(transport) as client:  # type: ignore[name-defined]
        tools = await client.list_tools()
        names = [t.name for t in tools]
        print("tools listed:", len(names))

        results: dict[str, dict[str, object]] = {}
        for name, args in (("yahoo_price", {"symbol": "AAPL"}), ("market_snapshot", {})):
            try:
                res = await asyncio.wait_for(client.call_tool(name, args), timeout=20.0)
                txt = None
                for block in getattr(res, "content", []) or []:
                    if getattr(block, "type", None) == "text":
                        txt = getattr(block, "text", None)
                        break
                results[name] = {"ok": txt is not None and bool(txt), "text": txt}
                if txt:
                    print(f"call {name}: OK, {len(txt)} chars")
            except Exception as exc:
                results[name] = {"ok": False, "error": repr(exc)}
                print(f"call {name}: FAILED", repr(exc))

        print("final results:", results)


asyncio.run(main())
