#!/usr/bin/env python3
"""Start the vendored TradingView MCP server with the right sys.path.

Usage:
    python scripts/start_tv_mcp.py [--log-level INFO]

Equivalent to:
    python -c "import sys; sys.path.insert(0,'vendor/tradingview-mcp/src'); \
               import tradingview_mcp.server; tradingview_mcp.server.main()"
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TV_SRC = REPO_ROOT / "vendor" / "tradingview-mcp" / "src"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    args, _rest = parser.parse_known_args()
    if not TV_SRC.is_dir():
        sys.exit("vendored TradingView MCP missing: vendor/tradingview-mcp/src")
    sys.path.insert(0, str(TV_SRC))
    os.environ.setdefault("LOG_LEVEL", args.log_level)
    from tradingview_mcp.server import main as tv_main
    tv_main()


if __name__ == "__main__":
    main()
