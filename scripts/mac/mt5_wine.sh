#!/usr/bin/env bash
# Run the MT5 bridge server inside the MetaTrader 5 for Mac Wine prefix.
#
#   mt5_wine.sh info      show the Wine binary / prefix / terminal found
#   mt5_wine.sh install   install Windows Python + MetaTrader5 + numpy into the prefix
#   mt5_wine.sh serve     run the bridge in the foreground (Ctrl+C to stop)
#   mt5_wine.sh ensure    start the bridge in the background unless already up
#   mt5_wine.sh stop      stop a background bridge
#
# Overrides: WINE, WINEPREFIX, MT5_TERMINAL_PATH (Windows path), MT5_BRIDGE_PORT,
#            MT5_BRIDGE_KEY_FILE, WIN_PYTHON_VERSION.  See docs/MACOS.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT="${MT5_BRIDGE_PORT:-18812}"
KEY_FILE="${MT5_BRIDGE_KEY_FILE:-$HOME/.config/llm-trader/mt5_bridge.key}"
WIN_PY_VER="${WIN_PYTHON_VERSION:-3.11.9}"
WIN_PY_DIR='C:\Python311'
LOG="$ROOT/artifacts/mt5_bridge.log"
MT5_APP="/Applications/MetaTrader 5.app"

die() { echo "mt5_wine: $*" >&2; exit 1; }

find_wine() {
  if [ -n "${WINE:-}" ]; then echo "$WINE"; return; fi
  if [ -d "$MT5_APP" ]; then
    local w
    w="$(find "$MT5_APP/Contents" -type f \( -name wine64 -o -name wine \) -perm -u+x 2>/dev/null | sort | head -1)"
    [ -n "$w" ] && { echo "$w"; return; }
  fi
  command -v wine64 || command -v wine || true
}

find_prefix() {
  if [ -n "${WINEPREFIX:-}" ]; then echo "$WINEPREFIX"; return; fi
  local p="$HOME/Library/Application Support/net.metaquotes.wine.metatrader5"
  [ -d "$p" ] && { echo "$p"; return; }
  echo "$HOME/.wine"
}

WINE_BIN="$(find_wine)"
export WINEPREFIX="$(find_prefix)"
export WINEDEBUG="${WINEDEBUG:--all}"

# Unix path -> Wine Z: drive path
winpath() { local p="$1"; echo "Z:${p//\//\\}"; }

terminal_path() {
  if [ -n "${MT5_TERMINAL_PATH:-}" ]; then echo "$MT5_TERMINAL_PATH"; return; fi
  for d in "Program Files/MetaTrader 5" "Program Files (x86)/MetaTrader 5"; do
    if [ -f "$WINEPREFIX/drive_c/$d/terminal64.exe" ]; then
      echo "C:\\${d//\//\\}\\terminal64.exe"; return
    fi
  done
}

need_wine() {
  [ -n "$WINE_BIN" ] || die "no Wine found. Install MetaTrader 5 for Mac from metatrader5.com (it bundles Wine), or set WINE=/path/to/wine64."
  [ -d "$WINEPREFIX" ] || die "Wine prefix not found at $WINEPREFIX. Open MetaTrader 5 once so it creates it, or set WINEPREFIX."
}

win_python() { "$WINE_BIN" "$WIN_PY_DIR\\python.exe" "$@"; }

bridge_up() { nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; }

cmd_info() {
  echo "wine:      ${WINE_BIN:-<not found>}"
  echo "prefix:    $WINEPREFIX"
  echo "terminal:  $(terminal_path || true)"
  echo "python:    $( [ -f "$WINEPREFIX/drive_c/Python311/python.exe" ] && echo "$WIN_PY_DIR\\python.exe" || echo '<not installed: run install>')"
  echo "key file:  $KEY_FILE $( [ -s "$KEY_FILE" ] || echo '(missing: run scripts/mac/setup_mac.sh)')"
  echo "bridge:    127.0.0.1:$PORT $(bridge_up && echo UP || echo down)"
}

cmd_install() {
  need_wine
  if [ ! -f "$WINEPREFIX/drive_c/Python311/python.exe" ]; then
    local cache="$HOME/Library/Caches/llm-trader"
    # The python.org .exe installer's bootstrapper is 32-bit, which MetaTrader's
    # 64-bit-only Wine cannot run. The official NuGet package is a plain zip of
    # the same 64-bit build, so unpack it instead.
    local pkg="$cache/python-$WIN_PY_VER.nupkg"
    local tmp; tmp="$(mktemp -d)"
    mkdir -p "$cache"
    if [ ! -s "$pkg" ]; then
      echo "Downloading Windows Python $WIN_PY_VER (official NuGet package) ..."
      curl -fL --progress-bar -o "$pkg" "https://www.nuget.org/api/v2/package/python/$WIN_PY_VER"
    fi
    echo "Unpacking Windows Python into the MT5 Wine prefix ..."
    unzip -q "$pkg" -d "$tmp"
    mkdir -p "$WINEPREFIX/drive_c/Python311"
    cp -R "$tmp/tools/." "$WINEPREFIX/drive_c/Python311/"
    rm -rf "$tmp"
    win_python -m ensurepip >/dev/null
  fi
  echo "Installing MetaTrader5 + numpy for Windows Python ..."
  win_python -m pip install --disable-pip-version-check -q --upgrade pip MetaTrader5 numpy
  win_python -c "import MetaTrader5 as m; print('MetaTrader5', m.__version__, 'OK')"
}

cmd_serve() {
  need_wine
  [ -s "$KEY_FILE" ] || die "missing $KEY_FILE - run scripts/mac/setup_mac.sh first"
  [ -f "$WINEPREFIX/drive_c/Python311/python.exe" ] || die "Windows Python not installed - run: $0 install"
  local args=(--port "$PORT" --key-file "$(winpath "$KEY_FILE")")
  local term; term="$(terminal_path || true)"
  [ -n "$term" ] && args+=(--terminal "$term")
  exec "$WINE_BIN" "$WIN_PY_DIR\\python.exe" -u \
    "$(winpath "$ROOT/compat/mt5_bridge/mt5_bridge_server.py")" "${args[@]}"
}

cmd_ensure() {
  if bridge_up; then echo "mt5 bridge already up on :$PORT"; return 0; fi
  mkdir -p "$(dirname "$LOG")"
  echo "Starting mt5 bridge in the background (log: $LOG) ..."
  nohup "$0" serve >>"$LOG" 2>&1 &
  for _ in $(seq 1 60); do
    bridge_up && { echo "mt5 bridge up on :$PORT"; return 0; }
    sleep 1
  done
  echo "mt5 bridge did not come up within 60s; last log lines:" >&2
  tail -n 15 "$LOG" >&2 || true
  return 1
}

cmd_stop() {
  pkill -f "mt5_bridge_server.py" && echo "mt5 bridge stopped" || echo "mt5 bridge was not running"
}

case "${1:-info}" in
  info) cmd_info ;;
  install) cmd_install ;;
  serve) cmd_serve ;;
  ensure) cmd_ensure ;;
  stop) cmd_stop ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
