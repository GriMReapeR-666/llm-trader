#!/usr/bin/env python3
"""Prove the TradingView MCP is startable from this repo end-to-end.

Starts the server exactly as a user would from repo root
(`python scripts/start_tv_mcp.py`, stdio), connects with a real
fastmcp stdio client, lists tools (assert 39), spot-checks two tools
in the same connected session, then shuts the server down cleanly.

Time-boxed: if the server does not answer within the budget, the test
fails with a clear message instead of hanging the run. A single tool
call that stalls is failed without killing the whole test.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from fastmcp.client.transports.stdio import PythonStdioTransport

REPO_ROOT = Path(__file__).resolve().parents[1]
TV_SRC = REPO_ROOT / "vendor" / "tradingview-mcp" / "src"
START_SCRIPT = REPO_ROOT / "scripts" / "start_tv_mcp.py"

SERVER_START_BUDGET_S = 20.0
TOOL_CALL_BUDGET_S = 30.0


def _make_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(
        p for p in (str(TV_SRC), env.get("PYTHONPATH", ""), str(REPO_ROOT)) if p
    ).rstrip(":")
    env.setdefault("LOG_LEVEL", "WARNING")
    return env


def _kill_leftover(transport: PythonStdioTransport) -> None:
    proc = getattr(transport, "_process", None)
    if proc is None or proc.poll() is not None:
        return
    try:
        os.kill(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


async def _extract_text(result: fastmcp.client.client.CallToolResult) -> str | None:
    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", None) or ""
    return None


@pytest.mark.asyncio
async def test_tv_mcp_startable_from_repo_root() -> None:
    assert TV_SRC.is_dir(), f"missing vendored TV MCP source: {TV_SRC}"
    assert START_SCRIPT.exists(), f"missing start script: {START_SCRIPT}"

    transport = PythonStdioTransport(
        script_path=str(START_SCRIPT),
        args=[],
        env=_make_env(),
        cwd=str(REPO_ROOT),
        keep_alive=False,
    )

    from fastmcp import Client

    tool_results: dict[str, object] = {}

    try:
        async with Client(transport) as client:
            ts = await asyncio.wait_for(
                client.list_tools(), timeout=SERVER_START_BUDGET_S
            )
            tools = list(ts)
            tool_names = [t.name for t in tools]
            assert len(tool_names) == 39, (
                f"expected 39 TradingView MCP tools, got {len(tool_names)}: {tool_names}"
            )

            for name, args in (("yahoo_price", {"symbol": "AAPL"}), ("market_snapshot", {})):
                try:
                    res = await asyncio.wait_for(
                        client.call_tool(name, args), timeout=TOOL_CALL_BUDGET_S
                    )
                    text = await _extract_text(res)
                    assert text is not None, f"{name} returned no text content"
                    assert text.strip(), f"{name} returned empty text"
                    tool_results[name] = {"ok": True, "text_len": len(text)}
                except Exception as exc:
                    tool_results[name] = {"ok": False, "error": repr(exc)}
    except Exception as exc:
        raise AssertionError(
            f"Could not boot + list tools within {SERVER_START_BUDGET_S}s: {exc}"
        ) from exc
    finally:
        # Disconnect/teardown the stdio child. keep_alive=False already asks
        # for teardown; disconnect() is the official path.
        try:
            disconnect = getattr(transport, "disconnect", None)
            if disconnect is not None:
                if asyncio.iscoroutinefunction(disconnect):
                    await disconnect()
                else:
                    disconnect()  # type: ignore[misc]
        except Exception:
            pass
        _kill_leftover(transport)

    successes = [k for k, v in tool_results.items() if v.get("ok")]
    assert len(successes) >= 2, (
        f"server started and listed 39 tools, but fewer than 2 spot-check tools "
        f"returned non-empty text. Results: {tool_results}"
    )
