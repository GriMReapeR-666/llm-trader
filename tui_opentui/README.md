# LLM Trader — Command Deck (OpenTUI)

A terminal command deck for the autonomous multi-agent MT5 trading system,
built with [OpenTUI](https://www.opentui.com) (`@opentui/react` + Bun).

The trading engine stays fully autonomous and headless. This deck is an
attachable observer + intervention console: it subscribes to live events and
can issue authorized commands. If no engine is running, a clearly-labeled
mock transport keeps every screen alive for development.

## Screens

Organized after three standards: Bloomberg Terminal's functional tab taxonomy,
agent-observability span trees (Langfuse/LangSmith/MLflow pattern: cycle →
tools → LLM generations with inline latency/tokens), and QuantConnect/MT5
backtest report sections (overall stats with quality bands, equity+drawdown,
monthly P&L).

**TRADE**

| Key | Screen | Interface |
|-----|--------|-----------|
| `1` | STREAM | **Reply-style autonomous feed** — agents post as replies: tool calls, streaming LLM thinking, glowing decision cards, double-bordered order/fill cards. Prompt bar accepts slash commands. |
| `2` | COCKPIT | Equity sparkline, drawdown/heat gauges, per-symbol micro-charts, session ledger. |
| `3` | POSITIONS | Monospace blotter: ticket/side/vol/SL/TP in bp/R/age + floating P&L. |

**INTEL**

| Key | Screen | Interface |
|-----|--------|-----------|
| `4` | BRAIN | Cycle brief reader: markdown reasoning pane + signed intent→validated diff (what RiskController changed). |
| `5` | CAUSAL | Macro causal DAG drawn with strength-proportional ASCII edges + edge table. |
| `6` | MACRO | Active narratives + alternative nowcast meters. |

**OPS**

| Key | Screen | Interface |
|-----|--------|-----------|
| `7` | TASKS | **Loop pipeline trace explorer** — every main-loop stage as a span (`✔ data.poll ███████ 0.9s TechnicalNet`), selectable; right pane shows that stage's tool calls with args, results, latency and token counts. Loop stats header (cycles, avg duration). |
| `8` | STRATEGIES | **Strategy library** — registry table (stage-colored CANDIDATE/EVALUATION/FULL/QUARANTINE) with quality-banded WIN%/PF/Sharpe; select a row → promotion-gate progress bar, full stat grid, equity sparkline, notes. |
| `9` | ANALYTICS | **Metric visualizations** — overall equity + drawdown curves, R-multiple distribution histogram, session ratios (PF/win rate/net R), monthly-R calendar bars, per-strategy comparison table with inline sparklines. |
| `0` | JOURNAL | 14-rule integrity audit + operational event stream. |
| `m` | MAP | **System explorer** — ASCII diagram of the full loop (feeds → intelligence → decision → execution → memory/research), fast/slow lanes, authority model, screen directory, keybindings, backend→panel data-source map. |

Global: `/` or typing `/…` opens the command palette · `i` focuses prompt ·
`ESC` releases prompt / dismisses alerts · `↑↓` selects items in
Brain/Tasks/Strategies · `x` dismisses top alert.

Intervention ladder (all logged to operational_events): `/pause`, `/flatten`,
`/kill` — each requires an explicit confirm step; kill engages the red HALTED
state until manually resumed (`/resume`).

## Transport

- `TRANSPORT=auto` (default): REST bootstrap from `GET /api/snapshot` +
  live events over `WS /ws/harness-events` on `DECK_API`
  (default `http://127.0.0.1:8000`). Fails closed if the engine is unreachable —
  the deck never shows synthetic data.
- `TRANSPORT=mcp` / `mt5`: direct MT5 terminal bridge via MCP.
- `TRANSPORT=live`: force the REST/WS engine on `:8000`, no fallback (for prod).

Point it at the engine after Phase 1 of the backend bridge is wired:

```powershell
python ui/gui_api.py        # bridge
START_DECK.bat              # this deck
```

## Development

```powershell
cd tui_opentui
bun install          # once
bun run dev          # auto transport (live engine required)
bun run typecheck    # strict TS, zero errors expected
bun run selftest     # offscreen render of all views (CI-safe)
```

Requires Bun ≥ 1.3 (installed at `%USERPROFILE%\.bun`) and Windows Terminal
for full truecolor.

## Architecture notes

- `src/store.ts` — framework-free external store consumed via
  `useSyncExternalStore`; immutable updates keep selectors stable.
- `src/transport/live.ts` — snapshot bootstrap, envelope-typed WS events,
  exponential-backoff reconnect, stale-heartbeat detection, control channel.
- Layout lessons baked in: fixed character widths for rails (percent chains
  reflow between frames), fixed heights for strips, memoized ascii-font brand,
  custom MiniMarkdown/IntentDiff instead of tree-sitter-dependent components.
