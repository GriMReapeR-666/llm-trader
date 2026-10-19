# Vendored: atilaahmettaner/tradingview-mcp

- Source: https://github.com/atilaahmettaner/tradingview-mcp (atilaahmettaner/tradingview-mcp)
- Pinned commit: main snapshot, imported 2026-10-11 (see commit hash embedded in CHANGELOG.md on upstream)
- License: MIT (Copyright (c) 2025 Ahmet Taner Atila) — see LICENSE
- Vendored at: ~3.9MB (source + tests + docs; help the tree stay lossless)
- No modifications were made to the vendored source code itself.
- Refpin: tradingview-mcp-server pyproject.toml declares mcp[cli]>=1.14.0,<2 — compatible with
  fastmcp 3.x / mcp sdk already installed in this repo's environment (fastmcp 3.2.4, mcp 1.x).
- Running:
    python -m tradingview_mcp.server   # requires vendor/tradingview-mcp/src on sys.path
    # OR: cd vendor/tradingview-mcp && python -m src.tradingview_mcp.server
