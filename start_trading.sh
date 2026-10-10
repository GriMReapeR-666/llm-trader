#!/usr/bin/env bash
# macOS/Linux counterpart of START_TRADING.bat.
#   ./start_trading.sh               paper, EURUSD AUTO, continuous
#   ./start_trading.sh --opt ...     same, with extra llm-trade options
#   ./start_trading.sh <command> ... any mt5_trader.py command
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export DELIBERATIVE_GRAPH_SHADOW=1
export PYTHONUTF8=1
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "[X] .venv missing - run scripts/mac/setup_mac.sh"; exit 1; }
MODEL="meta/llama-3.2-11b-vision-instruct"

echo
echo "===================================================="
echo "  LLM TRADER - AUTONOMOUS HEADLESS TRADING ENGINE"
echo "===================================================="
echo

# Market data (paper included) comes from the MT5 terminal via the bridge.
if [ "$(uname)" = "Darwin" ] && [ "${MT5_BRIDGE_AUTOSTART:-1}" = "1" ]; then
  scripts/mac/mt5_wine.sh ensure || echo "[!] MT5 bridge not running - MT5-backed commands will fail closed (see docs/MACOS.md)"
fi

preflight() {
  if [ "${SMOKE_PREFLIGHT:-0}" = "1" ]; then
    echo "[PREFLIGHT] QKF smoke probes running (set SMOKE_PREFLIGHT=0 to skip)..."
    (cd quant_knowledge_framework && "$PY" smoke_test.py --fast) \
      || { echo "[PREFLIGHT] QKF smoke RED - aborting launch. Report: artifacts/qkf_smoke_report.txt"; exit 1; }
    echo "[PREFLIGHT] QKF smoke GREEN."
  fi
  if [ "${LLM_TRADER_COMPACT_CONTEXT:-0}" = "1" ]; then
    echo "[PREFLIGHT] Compact-context probe running (LLM_TRADER_COMPACT_CONTEXT=1)..."
    "$PY" artifacts/ctx_probe.py \
      || { echo "[PREFLIGHT] Compact-context probe RED - aborting launch. See artifacts/ctx_probe.txt"; exit 1; }
    echo "[PREFLIGHT] Compact-context probe GREEN."
  fi
}

if [ $# -eq 0 ]; then
  echo "[HEADLESS AUTO-MODE] Launching EURUSD AUTO (Paper Mode, Continuous)"
  preflight
  exec "$PY" -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe AUTO \
    --model "$MODEL" --risk-per-trade 1.0 --continuous
elif [[ "$1" == --* ]]; then
  echo "[HEADLESS AUTO-MODE] Launching EURUSD AUTO with options: $*"
  exec "$PY" -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe AUTO \
    --model "$MODEL" --risk-per-trade 1.0 "$@"
else
  echo "[HEADLESS CUSTOM-COMMAND] Launching: $*"
  exec "$PY" -u mt5_trader.py "$@"
fi
