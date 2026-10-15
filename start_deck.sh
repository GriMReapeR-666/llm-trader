#!/usr/bin/env bash
# macOS/Linux counterpart of START_DECK.bat: API server + paper trader + OpenTUI deck.
#   ./start_deck.sh [--fast | --full]
# Env: DECK_TRIAGE_MODE=off|auto, DECK_TUI_ARGS, SMOKE_PREFLIGHT=1,
#      LLM_TRADER_COMPACT_CONTEXT=1, MT5_BRIDGE_AUTOSTART=0
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export DELIBERATIVE_GRAPH_SHADOW=1
export PYTHONUTF8=1
PY="$ROOT/.venv/bin/python"
# The deck spawns `python -m kronos_mcp.main  # the repo's MT5 FastMCP entrypoint (kronos_mcp + compat/mt5_bridge)`; make that the venv's python.
export PATH="$ROOT/.venv/bin:$PATH"
BUN="$(command -v bun || echo "$HOME/.bun/bin/bun")"

cat <<'EOF'

  +====================================================+
  |   ##      ########  ########  ##   ##  ########    |
  |   ##      ##    ##  ##    ##   ## ##   ##          |
  |   ##      ########  ########    ###    ######      |
  |   ##      ##    ##  ##    ##   ## ##   ##          |
  |   ######  ########  ########  ##   ##  ########    |
  |   autonomous multi-agent trading                   |
  +====================================================+

EOF

# -- Pre-flight -------------------------------------------------------------
[ -x "$BUN" ] || { echo " [X] Bun not found. Install: brew install oven-sh/bun/bun"; exit 1; }
[ -x "$PY" ] || { echo " [X] .venv missing - run scripts/mac/setup_mac.sh"; exit 1; }
[ -f tui_opentui/package.json ] || { echo " [X] Project not found. Run from repo root."; exit 1; }

if [ "${SMOKE_PREFLIGHT:-0}" = "1" ]; then
  echo " [PREFLIGHT] QKF smoke probes running..."
  (cd quant_knowledge_framework && "$PY" smoke_test.py --fast) \
    || { echo " [X] PREFLIGHT: QKF smoke RED - aborting launch. Report: artifacts/qkf_smoke_report.txt"; exit 1; }
  echo " [OK] PREFLIGHT: QKF smoke GREEN."
fi
if [ "${LLM_TRADER_COMPACT_CONTEXT:-0}" = "1" ]; then
  "$PY" artifacts/ctx_probe.py \
    || { echo " [X] PREFLIGHT: compact-context probe RED - aborting launch. See artifacts/ctx_probe.txt"; exit 1; }
  echo " [OK] PREFLIGHT: compact-context probe GREEN."
fi

if [ "$(uname)" = "Darwin" ] && [ "${MT5_BRIDGE_AUTOSTART:-1}" = "1" ]; then
  scripts/mac/mt5_wine.sh ensure || echo " [!] MT5 bridge not running - market data will fail closed (see docs/MACOS.md)"
fi

# -- Boot mode: fast boot skips the ~25-min Laya python -m kronos_mcp.main  # the repo's MT5 FastMCP entrypoint (kronos_mcp + compat/mt5_bridge)s triage ----------------
TRIAGE_MODE=auto
[ "${DECK_TRIAGE_MODE:-}" = "off" ] && TRIAGE_MODE=off
[ "${DECK_TRIAGE_MODE:-}" = "auto" ] && TRIAGE_MODE=auto_no_prompt
[ "${1:-}" = "--fast" ] && TRIAGE_MODE=off
[ "${1:-}" = "--full" ] && TRIAGE_MODE=full
if [ "$TRIAGE_MODE" = "auto" ]; then
  echo
  read -r -t 5 -n 1 -p "Fast boot? Skip ~25min news triage this launch [Y/N, N in 5s]: " ans || ans=N
  echo
  case "$ans" in [Yy]) TRIAGE_MODE=off ;; *) TRIAGE_MODE=full ;; esac
fi
case "$TRIAGE_MODE" in
  off)  export NEWS_TRIAGE_MODE=off;  echo " [FAST BOOT] NEWS_TRIAGE_MODE=off -- deterministic news triage, Laya inference skipped" ;;
  full) export NEWS_TRIAGE_MODE=auto; echo " [FULL BOOT] NEWS_TRIAGE_MODE=auto -- full Laya news triage runs at engine start" ;;
  *)    unset NEWS_TRIAGE_MODE ;;
esac

# -- API server on :8000 ----------------------------------------------------
kill_port() { lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null | xargs kill -9 2>/dev/null || true; }
kill_port
echo " Starting API Server on :8000..."
"$PY" -u ui/gui_api.py > api.log 2>&1 &
API_PID=$!
trap 'echo; echo " Shutting down API server..."; kill $API_PID 2>/dev/null; kill_port; echo " Done."' EXIT

echo " Waiting for API..."
for i in $(seq 1 30); do
  sleep 1
  "$PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" >/dev/null 2>&1 && break
  if [ "$i" = 30 ]; then
    echo " [X] API server failed to start"
    echo; echo " --- Last API log output: ---"; tail -n 15 api.log; echo " ----------------------------"
    exit 1
  fi
done
echo " [OK] API server ready"
echo

# -- Auto-start trader in PAPER mode ----------------------------------------
echo " Starting trader (paper mode)..."
"$PY" - <<'PYEOF' || echo " [!] Trader auto-start failed (connection error) -- start manually from Command Deck [5]"
import json, os, urllib.request
body = {"mode": "paper", "symbol": "EURUSD", "timeframe": "AUTO", "risk": 1.0, "continuous": True,
        "news_triage_mode": os.environ.get("NEWS_TRIAGE_MODE", "") or None}
req = urllib.request.Request("http://127.0.0.1:8000/api/control/start", data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
b = json.load(urllib.request.urlopen(req, timeout=10))
print(" [OK] Trader started (paper mode)" if b.get("status") == "success"
      else " [!] Trader start refused: " + str(b.get("status")) + " -- start manually from Command Deck [5]")
PYEOF
echo

# -- Deck --------------------------------------------------------------------
if [ ! -d tui_opentui/node_modules ]; then
  echo " Installing TUI dependencies..."
  (cd tui_opentui && "$BUN" install) || { echo " [X] bun install failed"; exit 1; }
fi
echo "  Keys: 1-6 sections  [ ] screens  / palette  i prompt  x dismiss"
echo "  Exit: Ctrl+C"
echo
cd tui_opentui
# shellcheck disable=SC2086
"$BUN" src/index.tsx ${DECK_TUI_ARGS:-}
