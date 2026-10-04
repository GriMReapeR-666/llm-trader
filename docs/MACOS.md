# Running on macOS

The official `MetaTrader5` Python package only ships Windows wheels. On macOS
the rest of the system runs natively, and MT5 calls go through a small bridge:

```
 macOS Python (.venv)                         Windows Python (Wine or Windows box)
 ┌───────────────────────────┐   TCP 18812    ┌──────────────────────────────┐
 │ mt5_trader.py, gui_api,   │  HMAC-authed   │ compat/mt5_bridge/           │
 │ deck MCP server ...       │ ─────────────▶ │   mt5_bridge_server.py       │
 │ import MetaTrader5  ──────┼─ shim ──────── │ import MetaTrader5 (real)    │
 │ (compat/mt5_bridge/...)   │                │        │                     │
 └───────────────────────────┘                │   MT5 terminal64.exe         │
                                              └──────────────────────────────┘
```

No trading code changed. `import MetaTrader5` resolves to the shim through a
`.pth` file in the venv. The shim behaves like the real package with no
terminal attached until `initialize()` connects. If the bridge is down, every
call fails closed exactly as it would with a dead terminal. When it connects,
the shim checks its order and timeframe constants against the real package and
refuses to connect if any differ.

## 1. Set up

```bash
scripts/mac/setup_mac.sh          # add --full for torch/Kronos/Laya and other extras
```

This creates `.venv`, installs `requirements.txt`, installs the shim and writes
a bridge key to `~/.config/llm-trader/mt5_bridge.key` (mode 600). If `bun` is
installed, it also installs the deck's dependencies (`brew install oven-sh/bun/bun`).

## 2. Run MT5 and the bridge

**Option A: on this Mac (Wine).**

1. Install *MetaTrader 5 for Mac* from metatrader5.com. It bundles Wine.
   Open it once and log in to your broker account.
2. Install Windows Python plus `MetaTrader5` and `numpy` inside MT5's Wine prefix:
   ```bash
   scripts/mac/mt5_wine.sh install
   ```
3. Start the bridge. The launchers in step 3 also do this for you:
   ```bash
   scripts/mac/mt5_wine.sh serve      # foreground; or `ensure` for background
   scripts/mac/mt5_wine.sh info       # what was found, and whether the bridge is up
   ```

The script finds Wine inside `/Applications/MetaTrader 5.app` and uses the
prefix at `~/Library/Application Support/net.metaquotes.wine.metatrader5`.
To use other locations, set `WINE`, `WINEPREFIX` or `MT5_TERMINAL_PATH` (a
Windows path).

**Option B: a Windows VM or VPS.** Copy `compat/mt5_bridge/mt5_bridge_server.py`
and your key file to the Windows machine, then run:

```bat
pip install MetaTrader5 numpy
python mt5_bridge_server.py --key-file mt5_bridge.key
```

The server listens on 127.0.0.1 only. Reach it from the Mac through an SSH
tunnel (`ssh -N -L 18812:127.0.0.1:18812 user@winbox`) rather than opening the
port, because the pickled traffic is authenticated but not encrypted.

## 3. Launch

| Windows | macOS |
|---|---|
| `START_TRADING.bat [args]` | `./start_trading.sh [args]` |
| `START_DECK.bat [--fast\|--full]` | `./start_deck.sh [--fast\|--full]` |
| `python mt5_trader.py ...` | `source .venv/bin/activate && python mt5_trader.py ...` |

Both launchers run `mt5_wine.sh ensure` first. Set `MT5_BRIDGE_AUTOSTART=0`
when the bridge runs elsewhere. Check everything with
`python mt5_trader.py doctor`.

## Bridge settings

| Env | Default | |
|---|---|---|
| `MT5_BRIDGE_HOST` / `MT5_BRIDGE_PORT` | `127.0.0.1` / `18812` | both sides |
| `MT5_BRIDGE_KEY_FILE` | `~/.config/llm-trader/mt5_bridge.key` | or `MT5_BRIDGE_KEY` inline |
| `MT5_BRIDGE_TIMEOUT` | `30` s | per call; `initialize()` also waits out its own `timeout` |

## Differences from Windows

- Every process shares the bridge's single terminal session. The terminal shuts
  down only after the last attached client calls `shutdown()`.
- `data/mt5_calendar_wrapper.py` loads `terminal64.dll` directly and is not
  available through the bridge. Callers already treat that as unavailable.
- The OneDrive and `C:\...` paths in older docs are Windows-specific. Use the
  repo-relative paths instead.
