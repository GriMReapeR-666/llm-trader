# quantpipe - MT5 + TradingView MCP research-to-execution pipeline

```
 LLM (Claude, via MCP)
   |  create/edit strategy SPEC (JSON DSL, never free code)
   v
 validate -> backtest (broker profile x market scenario) -> diagnostics flags
   -> stress matrix (all brokers x all scenarios) -> optimize -> walk-forward
   -> holdout (budgeted) -> deploy gate
   v
 live_runner (closed-bar signals) --+            TradingView alerts --> /tv webhook (HMAC-style secret)
                                    v                                        v
                          mt5_exec.place()  <--- RiskGuard (kill switch, daily loss, DD, whitelist, SL required)
                                    v
                       PaperClient (default)  |  MT5Client (demo/live, explicit opt-in)
```

## Economic news, event replay & macro walk-forward (new)
```
MT5 ExportCalendar.mq5 (mql5/) or any calendar CSV -> import_events (server_tz=ny_close) -> point-in-time surprise z-scores
  -> event_study (does it even move price?) -> strategies using ev_* fields / event_blackout / flatten_before_event_min
  -> backtest/stress/optimize/walk_forward_test (all accept events=<set>) -> event_replay (per-release attribution)
```
Spec fields: `ev_surprise_net` (+ = base currency bullish, decays over `events.decay_hours`), `ev_mins_since`, `ev_mins_to_next`,
`ev_in_bar`; filter `event_blackout`; risk `flatten_before_event_min`. The engine also widens spread/slippage 3x on bars that
contain a scheduled high-impact release, and tags every trade with `ev_ctx` (pre/post/quiet) for diagnostics.

**Alignment rules enforced in code (and covered by tests):** UTC everywhere (`server_to_utc` handles GMT+2/+3 DST brokers);
a bar sees an event only if release <= bar close; surprises standardised with an expanding std of PRIOR releases only (first 4
releases of any series get no signal); future *scheduled* times are usable, future *values* never; a spec that needs events
refuses to run without an events set instead of silently ignoring fundamentals.
**Your responsibility (cannot be automated away):** use FIRST-RELEASE actuals (revised data leaks the future), verify your
broker's real server offset, and prefer one calendar source for the whole history. "Perfect" alignment is not achievable;
these rules remove the known lookahead and timezone failure modes.

## MQL5 export + real MT5 Strategy Tester (new, Windows)
`export_mql5` -> `mt5_compile` (headless MetaEditor, returns errors for auto-fix loops) -> `mt5_run_tester` (command-line
`terminal64.exe /config:`, models ohlc_1m / every_tick / real_ticks) -> parity report vs the Python run. Uses MetaQuotes' own CLI,
so no DLL bridge/EA is needed. Set `MT5_TERMINAL_EXE`, `MT5_DATA_DIR`; use a dedicated `/portable` terminal for testing.
The MQL5 calendar API does not work inside the tester, so the generated EA reads `qp_events.csv` (same z-scores as Python)
from the Common\\Files folder. Exportable indicators: sma ema rsi atr highest lowest macd adx (others stay Python-only).
Live runner: event-dependent specs need `QP_LIVE_EVENTS=<set>` and a calendar you refresh on a schedule.

## Install
```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt          # MetaTrader5 installs on Windows only
copy .env.example .env                   # then export those vars
python -m pytest tests -q                # sanity check
```
Without MT5 (Mac/Linux/CI) everything except real MT5 data/execution works: synthetic/CSV data + paper mode.

## Connect to Claude Desktop (claude_desktop_config.json)
```json
{"mcpServers": {"quantpipe": {
  "command": "C:\\path\\to\\quantpipe\\.venv\\Scripts\\python.exe",
  "args": ["-m", "quantpipe.mcp_server"],
  "cwd": "C:\\path\\to\\quantpipe",
  "env": {"QP_MODE": "paper", "TV_WEBHOOK_SECRET": "change-me"}}}}
```
Claude Code: `claude mcp add quantpipe -- python -m quantpipe.mcp_server`

## Run the three processes
1. MCP server (started by your client).
2. TradingView receiver: `uvicorn quantpipe.tv_webhook:app --port 8787` + public HTTPS tunnel (cloudflared/ngrok). TradingView alert -> Webhook URL `https://<host>/tv`, message from tool `pine_alert_template`.
3. Autonomous loop: `python -m quantpipe.live_runner` (runs strategies activated by `deploy_strategy`).

## How "autonomous improvement" works
Ask Claude to use the MCP prompt `improve_strategy`. It loops: backtest -> read `diagnostics.flags`
(COST_DRAG, REGIME_WEAKNESS, SIDE_ASYMMETRY, STOP_HEAVY, CONCENTRATION, LOW_SAMPLE...) -> change ONE thing
-> `save_strategy` (versioned, with rationale) -> `compare_runs`. Guardrails built in:
- iterate only on the TRAIN segment (first 70%); HOLDOUT (last 30%) allows 2 uses per strategy+dataset
- `walk_forward_test` verdict PASS/FAIL after any parameter search
- `stress_test` across 4 broker profiles x 6 market scenarios must be `robust`
- `deploy_strategy` refuses unless a robust stress run AND a profitable holdout run exist for that exact version

## Broker & market constraints modelled
Spread, commission (round-turn), slippage, swap/day, leverage + margin check + stop-out, min/step/max lot,
stops-level minimum SL/TP distance, USD-base pairs (USDJPY), per-symbol overrides (XAUUSD).
Add your real broker with `add_broker_profile` (copy values from MT5 Market Watch -> Specification).
Scenarios: baseline, high_vol, low_vol, wide_spread, news_gaps, crisis (vol rescale, spread/slippage multipliers, jump gaps).

## Live safety model
Default PAPER. Live needs `QP_MODE=live` + `QP_LIVE_ENABLED=1`; a non-demo account additionally needs `QP_ALLOW_REAL=1`.
Every path (MCP tool, TV webhook, runner) goes through `mt5_exec.place()` -> RiskGuard: symbol whitelist, SL required,
risk/trade cap, max positions/lots, max spread, daily-loss and drawdown limits that auto-trip the kill switch
(`set_kill_switch`). Edit limits in `config/risk.json` (or `qp_data/risk.json`). Audit trail: `recent_events`.

## Known limitations - read before trusting results
- NOT verified here: MQL5 compilation, MetaEditor/tester command-line behaviour and report parsing (no Windows/MT5 in my sandbox;
  parsers were only unit-tested on samples I wrote). Expect to fix small compile errors - `mt5_compile` is built for that loop.
- Python vs MT5 results will differ somewhat (indicator seeding, tick model, real spreads); the parity report quantifies it.
- MT5 execution code is written to the official API but was NOT run against a live terminal here (MT5 is Windows-only).
  Validate on a DEMO account first: check fills, filling modes, stops-level rejections, symbol suffixes (`EURUSD.m`).
- Backtests use bar data: no tick-level path, SL/TP inside one bar resolve SL-first; fixed (not floating) spread; account currency assumed USD
  (non-USD-quote crosses like EURGBP need a conversion factor - not implemented).
- Synthetic data only proves the plumbing works. Real conclusions need real broker history (`download_mt5_dataset`).
- TradingView has no official data/read API. Signals flow in via webhooks only (needs a paid TV plan for webhook alerts).
- Optimisation and LLM iteration both overfit. Treat PASS gates as necessary, not sufficient. Forward-test on demo for weeks.
- This is software, not financial advice; you are responsible for any trading you enable.
