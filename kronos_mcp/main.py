#!/usr/bin/env python3
"""Entrypoint for Kronos FastMCP server - autonomous-agent K-line forecasting & volatility.

Mirrors metatrader5_mcp.main pattern: stdio FastMCP with CLI args + .env loading.
"""
from __future__ import annotations
import argparse
import os
import sys
from typing import Optional

# Ensure heavy deps lazy; register tools via import side-effects
from . import tools_forecast  # noqa: F401
from . import tools_mt5  # noqa: F401  # MT5 execution tier (bridge-backed)
from .utils import mcp

try:
    from .utils import mcp as _mcp
    from metatrader5_mcp.logger import configure_logging, logger  # reuse logger if available
except Exception:
    import logging
    logger = logging.getLogger("kronos-mcp")
    def configure_logging(level: str = "INFO"):
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))

def load_env_file() -> None:
    env_file = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_file):
        return
    try:
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip(); v = v.strip()
                    if k not in os.environ:
                        os.environ[k] = v
    except Exception as e:
        logger.warning("Failed to load .env: %s", e)

def parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kronos MCP Server - multi-dim OHLCVA forecasting & advanced volatility for autonomous agents (arXiv:2508.02739)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables:
  KRONOS_MODELS_DIR   path to local models folder (default ./models)
  KRONOS_FORCE_HEURISTIC  1/true to force heuristic backend (no torch)
  KRONOS_MODEL        default model_key (kronos-mini | kronos-small | kronos-base)
  LOG_LEVEL           DEBUG/INFO/WARNING/ERROR

Example:
  python -m kronos_mcp.main --log-level INFO
  KRONOS_MODELS_DIR=./models python -m kronos_mcp.main
        """,
    )
    parser.add_argument("--log-level", type=str, default=os.getenv("LOG_LEVEL", "INFO"), choices=["DEBUG","INFO","WARNING","ERROR","CRITICAL"], help="Logging level")
    parser.add_argument("--models-dir", type=str, default=os.getenv("KRONOS_MODELS_DIR", ""), help="Models directory")
    parser.add_argument("--force-heuristic", action="store_true", help="Force heuristic backend even if neural weights present")
    return parser.parse_args(args)

def main(args: Optional[list[str]] = None) -> None:
    load_env_file()
    parsed = parse_args(args)
    if parsed.models_dir:
        os.environ["KRONOS_MODELS_DIR"] = parsed.models_dir
    if parsed.force_heuristic:
        os.environ["KRONOS_FORCE_HEURISTIC"] = "1"
    configure_logging(parsed.log_level)
    try:
        from importlib.metadata import version as pkg_version
        _v = pkg_version("kronos-mcp")  # type: ignore
    except Exception:
        _v = "0.1.0-local"
    logger.info("Kronos MCP Server v%s", _v)
    logger.info("KRONOS_MODELS_DIR=%s KRONOS_FORCE_HEURISTIC=%s", os.getenv("KRONOS_MODELS_DIR","./models"), os.getenv("KRONOS_FORCE_HEURISTIC","0"))
    # probe bridge without heavy load
    try:
        from pathlib import Path as P
        import sys
        # Single import root (P1-C1/M3): repo root only, never subpackage folders.
        _root = str(P(__file__).resolve().parent.parent)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from research.kronos_bridge import KronosPredictor
        probe = KronosPredictor(model_name=os.getenv("KRONOS_MODEL","kronos-mini"))
        logger.info("Kronos probe backend=%s weights=%s reason=%s", probe.backend, probe.weights_loaded, probe.load_reason)
    except Exception as e:
        logger.warning("Kronos probe failed (will fallback heuristic per-call): %s", e)
    logger.info("Server ready - waiting for MCP client connections (stdio)")
    try:
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error("Server error: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
