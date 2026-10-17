"""Collect repo-relative paths a maintainer might need for MCP wiring.

Used by tui_opentui deck-side mcp.ts? No - deck spawn is TS-side and knows
MT5_MCP_ARGS literally.  This file is used by scripts/ tests and dev helpers
that need to boot the vendored TradingView MCP or the MT5 tier without
guessing sys.path.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_TV_SRC = REPO_ROOT / "vendor" / "tradingview-mcp" / "src"
MT5_BRIDGE_DIR = REPO_ROOT / "compat" / "mt5_bridge"


def ensure_tradingview_src_on_path() -> None:
    """Make `python -m tradingview_mcp.server` work from any cwd."""
    if str(VENDOR_TV_SRC) not in sys.path and VENDOR_TV_SRC.is_dir():
        sys.path.insert(0, str(VENDOR_TV_SRC))


def ensure_mt5_bridge_on_path() -> None:
    """Make `from trading_tools import MT5Tools` work in bridge-layer tests."""
    if str(MT5_BRIDGE_DIR) not in sys.path and MT5_BRIDGE_DIR.is_dir():
        sys.path.insert(0, str(MT5_BRIDGE_DIR))
