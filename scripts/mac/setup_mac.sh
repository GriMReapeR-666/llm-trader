#!/usr/bin/env bash
# One-time macOS setup: Python venv, deps, MT5 bridge shim, bridge key, deck deps.
#   scripts/mac/setup_mac.sh          core dependencies
#   scripts/mac/setup_mac.sh --full   + requirements-optional.txt (torch, models, scrapers)
# Safe to re-run.  See docs/MACOS.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY_VERSION="${LLM_TRADER_PYTHON:-3.12}"
KEY_FILE="${MT5_BRIDGE_KEY_FILE:-$HOME/.config/llm-trader/mt5_bridge.key}"
cd "$ROOT"

say() { printf '  %s\n' "$*"; }

echo "== llm-trader macOS setup =="

# 1) Python venv ---------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  [ -x .venv/bin/python ] || uv venv --python "$PY_VERSION" .venv
  PIP=(uv pip install --python .venv/bin/python)
else
  PY="$(command -v "python$PY_VERSION" || command -v python3)"
  say "uv not found; using $PY (install uv for faster setup: brew install uv)"
  [ -x .venv/bin/python ] || "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  PIP=(.venv/bin/python -m pip install -q)
fi
say "venv: $ROOT/.venv ($(.venv/bin/python --version))"

# 2) Dependencies (MetaTrader5 is skipped on macOS by its platform marker) ------
"${PIP[@]}" -r requirements.txt pytest
say "python dependencies installed"
# The deck's MT5 panel runs the repo MT5 FastMCP server (kronos_mcp + compat/mt5_bridge);
# is Windows-only, so install it without deps (the bridge shim stands in).
"${PIP[@]}" fastmcp "mcp>=0.9.0" "pydantic>=2.5.0"
"${PIP[@]}" --no-deps kronos-mcp  # repo package that provides the MT5 FastMCP entrypoint
if [ "${1:-}" = "--full" ]; then
  "${PIP[@]}" -r requirements-optional.txt
  say "optional dependencies installed"
fi

# 3) MT5 bridge shim on sys.path ------------------------------------------------
SITE="$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
printf '%s\n' "$ROOT/compat/mt5_bridge" > "$SITE/llm_trader_mt5_bridge.pth"
.venv/bin/python -c 'import MetaTrader5 as m; assert m.BRIDGE' \
  && say "import MetaTrader5 -> bridge shim (compat/mt5_bridge)"

# 4) Shared bridge key (never committed; *.key is gitignored anyway) ------------
if [ ! -s "$KEY_FILE" ]; then
  mkdir -p "$(dirname "$KEY_FILE")"
  ( umask 077; .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))' > "$KEY_FILE" )
  say "bridge key created: $KEY_FILE"
else
  say "bridge key exists: $KEY_FILE"
fi

# 4a) Shared API token (never committed; mode 0600) ------------------------------
TOKEN_FILE="${GUI_API_TOKEN_FILE:-$HOME/.config/llm-trader/api.token}"
if [ ! -s "$TOKEN_FILE" ]; then
  mkdir -p "$(dirname "$TOKEN_FILE")"
  ( umask 077; .venv/bin/python -c 'import secrets; print(secrets.token_hex(32))' > "$TOKEN_FILE" )
  say "api token created: $TOKEN_FILE"
else
  say "api token exists: $TOKEN_FILE"
fi

# 4b) Secret-scan pre-commit hook -------------------------------------------------
git config core.hooksPath .githooks && say "pre-commit secret scan enabled (.githooks)"

# 5) Command deck (Bun + OpenTUI) ----------------------------------------------
BUN="$(command -v bun || true)"
[ -z "$BUN" ] && [ -x "$HOME/.bun/bin/bun" ] && BUN="$HOME/.bun/bin/bun"
if [ -n "$BUN" ]; then
  (cd tui_opentui && "$BUN" install)
  say "deck dependencies installed"
else
  say "bun not found - the deck needs it:  brew install oven-sh/bun/bun"
fi

cat <<EOF

Done.  Next:
  1. Install MetaTrader 5 for Mac (metatrader5.com), open it, log in to your account.
  2. scripts/mac/mt5_wine.sh install     # Windows Python + MetaTrader5 inside MT5's Wine
  3. scripts/mac/mt5_wine.sh serve       # start the bridge (or let the launchers do it)
  4. source .venv/bin/activate && python mt5_trader.py doctor
EOF
