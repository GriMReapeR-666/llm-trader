# Loop & Trading Process Audit — Faults and Missing Pieces

Scope: `mt5_trader.py` main loop → `intelligence/llm_orchestrator.py::execute_cycle` → risk/execution/reconciliation modules.
Severity legend: 🔴 high (can halt or corrupt the trading process), 🟠 medium (degrades reliability/observability), 🟡 low (polish/hygiene).

> **STATUS UPDATE:** Fixes 1–6 have been implemented and verified (`py_compile` clean,
> full pytest suite green). See "Applied fixes" at the bottom of this document.

---

## 🔴 HIGH — faults that can stop or corrupt the loop

### 1. One transient exception kills the entire continuous run
`mt5_trader.py` main loop calls `orch.execute_cycle(...)` with **no per-cycle try/except**.
Inside `execute_cycle`, several failure paths deliberately re-raise:
- `fetch_data` failure → `self.operations.fail_task(cycle_key, ...); raise` (~line 3428)
- LLM decision failure → `fail_task(...); raise` (~line 4129)
- Unguarded calls such as `microstructure.observe`, `trade_memory.record_events`,
  `evaluate_event_policy`, `build_feature_snapshot` also propagate.

Any single network hiccup (OpenRouter/Cerebras timeout, MT5 disconnect, bad tick data)
propagates out of the `for strategy_name ...` loop into the outer handler, which sets
`run_status = "FAILED"` and **exits the process**. A "24/7 continuous" trader dies on the
first transient error instead of skipping the cycle and retrying.

**Fix:** wrap each `execute_cycle` call (and ideally each strategy iteration) in
try/except, log + journal the failure, sleep, and continue the loop.

### 2. Reconnection logic exists but is never invoked
`MT5Client.reconnect_if_needed()` is defined (`mt5_trader.py` ~line 663) but **never called**
anywhere in the codebase. If the terminal disconnects mid-run:
- `fetch_ohlc` / `account_info` / `positions_get` start failing,
- combined with fault #1, the process exits instead of reconnecting.

**Fix:** call `client.reconnect_if_needed()` at the top of every pulse (and/or inside the
per-cycle exception handler from fix #1).

### 3. Unguarded `import MetaTrader5` breaks non-broker/paper installs
- `core/risk_manager.py` line 1: `import MetaTrader5 as mt5` (unguarded)
- `core/trade_manager.py` line 6: `import MetaTrader5 as mt5` (unguarded)

`mt5_trader.py` carefully wraps its own import in try/except so paper mode works without
the package, but the very next import (`from risk_manager import RiskManager`) raises
`ImportError` anyway. Paper/research mode cannot run on any machine without the MT5
Python package (e.g., Linux CI, clean venv). `core/order_execution.py` already shows the
correct guarded pattern — apply it here too.

### 4. Silent drop of executable decisions (no journal record)
In `execute_cycle`, after validation approves a BUY/SELL:
- If `stop_loss` or `entry` is missing → `if sl and entry:` fails → **nothing happens**:
  no rejection logged, no lifecycle event, decision still says BUY.
- If `calc_position_size` returns `0.0` (daily cap, min-lot refusal, confidence < 0.6,
  circuit breakers) → `if safe_vol > 0:` silently skips, again with no journal trace.

The trade is invisible to the audit trail and to the operator; the LLM/UI believes an
order was intended. Every rejected/skipped intent should get a
`log_lifecycle_event(..., "REJECTED", ...)` / ops event with the reason.

---

## 🟠 MEDIUM — reliability / correctness gaps

### 5. API-key gate blocks non-OpenRouter setups
`mt5_trader.py` ~line 2300: hard-exits unless `OPENROUTER_API_KEY` is set, even when the
user selected Cerebras/NVIDIA-only routing via `--llm-providers`. Startup fails although
the configured providers are fully usable.

### 6. Dead dashboard-update code inside the `if not display:` branch
Lines ~4286–4302 of `llm_orchestrator.py` sit inside the `if not display:` CLI-printing
block yet contain `if display: display.update_rl_stats(...)`, a second
`display.update_decision(...)`, and narrative updates. They are **unreachable** — TUI
RL-stats/narrative panels never receive these updates. Move them out of the branch.

### 7. Paper mode has no protective position management
`TradeManager.monitor_positions()` returns immediately when `self.client` is None
(paper). Consequences in paper runs:
- No trailing stop / breakeven / partial scale-out / time-stop.
- `enforce_protective_flatten()` (daily-loss flatten) never executes.
Paper fills happen through `reconcile_pending_orders` + `paper_broker`, but everything
after entry is unmanaged. If paper results are used as promotion evidence for live
gates, they measure a different system than the one that will trade live.

### 8. `check_active_trade_limit` counts the wrong book in paper mode
`RiskManager.check_active_trade_limit()` uses `mt5.positions_total()` — the *live
terminal* position count — even in paper mode. Paper concurrency is unlimited while
live/demo is capped at 3, so paper risk-of-run estimates diverge from live behavior.

### 9. Hardcoded `balance=10000.0` for RiskManager
`mt5_trader.py` constructs `RiskManager(balance=10000.0, ...)` regardless of mode. Live
equity is re-read from MT5 inside most checks, but `self.balance` remains the fallback
whenever `account_info()` fails (exactly the degraded moments when correct sizing matters
most). Seed it from the authenticated account snapshot at startup.

### 10. Holiday-skipped candles are permanently consumed
When a holiday is detected after `claim_task(cycle_key)`, the task is completed with
`{"action": "HOLD", "reason": "Market Holiday"}`. Because the durable claim is the
dedup checkpoint, that specific completed candle is never analysed after the holiday
ends. Consider releasing the lease (or recording a retryable status) instead of
completing it.

### 11. Unbounded in-memory growth in continuous mode
- `self.rl_state_history` (appended per executed trade, never trimmed)
- `self._rl_updated_tickets` (set, grows forever)
- `perf.history` (kept fully in memory)

Over weeks of 24/7 operation these inflate RAM and slow `perf.save_history()`.
Add bounded deques / periodic pruning.

### 12. Dual persistence: legacy JSON vs SQLite journal
`PerformanceMonitor("trading_rewards.json")` coexists with the authoritative
`trading_state.db`. Fills/closes are recorded in both places by different code paths;
a crash between them leaves divergent histories (the metrics panel reads the JSON).
Long-term, PerformanceMonitor should read from the store, or writes should be
transactionally paired.

### 13. KeyboardInterrupt mid-LLM-call leaves RESERVED telemetry
If Ctrl-C lands between `reserve_llm()` and `finish_llm()`, the request stays RESERVED
until lease expiry. Wrap the decision section so `finish_llm(success=False)` /
circuit update also runs on `KeyboardInterrupt`.

### 14. Duplicate per-pulse work when `--strategies both`
Each pulse runs `execute_cycle` twice (swing+scalp): two full data fetches, two
`_monitor_owned_positions_tick` passes, duplicated calendar ingestion and macro-graph
writes for the same symbol. Idempotency keys mostly protect correctness, but latency
and provider load double. Consider hoisting shared data collection per pulse.

---

## 🟡 LOW — hygiene / polish

15. `ModelSelector` import block assigns `ModelSelector = None` twice (duplicated line ~275).
16. `run_status` starts as `"STOPPED"`; a normal end-of-loops exit and a pre-orchestrator crash are indistinguishable in `finish_run`.
17. `preflighted_symbols` session cache means broker spec changes mid-session are not recaptured (acknowledged trade-off, but worth a TTL).
18. `decision["metrics"]["rl_feedback"] = "PENDING"` is never flipped to a final value on this path.
19. `_run_simple_menu` non-interactive defaults silently pick `mock_llm=True`; acceptable for CI but should log loudly.
20. Progress-bar math `cycle_num * (10 // max(1, total_cycles))` overflows the bar for large `total_cycles` (cosmetic).

---

## What is already solid (verified, no action needed)
- Candle gating: `_completed_candle_candidate` + durable `claim_task` idempotency prevents double-analysis and crash-skip.
- Pending-order lifecycle: expiry cancellation, deal-history fill reconciliation, partial-fill handling, UNRESOLVED→review escalation are all implemented and fail-closed.
- Risk stack ordering: validator → cooldown/rate/active-limit/consecutive-loss → sizing → arbiter → margin gate → cost gate → order intent → submit, with journal events at each stage.
- Kill switch, provider circuit breaker, LLM budget reservation, and scope-bound journals (PAPER/DEMO/LIVE fingerprints) are wired correctly.
- `expires_at` is tz-aware UTC, so the reconciler comparison is safe.

## Recommended priority order
1. Per-cycle exception isolation + heartbeat-safe continue (fix #1) — biggest availability win.
2. Wire `reconnect_if_needed()` into the pulse (fix #2).
3. Guard MT5 imports for paper portability (fix #3).
4. Journal every rejected/skipped intent (fix #4).
5. Items 5–14 in order of how much you rely on paper evidence and long uninterrupted runs.

---

## Applied fixes (implemented & test-verified)

| # | Fix | Files changed |
|---|-----|---------------|
| 1 | Per-cycle exception isolation: each `execute_cycle` call is wrapped in try/except (KeyboardInterrupt re-raised); a failed candle/LLM call logs, journals, surfaces on the TUI, and the loop continues on the next pulse instead of exiting. | `mt5_trader.py` |
| 2 | `client.reconnect_if_needed()` is now invoked every pulse (guarded) so a dropped terminal degrades into a retry, not a crash. | `mt5_trader.py` |
| 3 | Guarded `import MetaTrader5` in both modules; added `_account_equity()` / `_symbol_spec()` None-safe helpers used by all circuit-breaker/sizing paths; `check_active_trade_limit` tolerates an unavailable MT5 module. | `core/risk_manager.py`, `core/trade_manager.py` |
| 4 | Every previously silent skip is now journalled: cooldown/rate-limit/active-limit/consecutive-loss skips emit ops events; zero-volume sizing results log a `REJECTED/risk_sizing` lifecycle event + ops event and flip the decision to HOLD with a reason; approved BUY/SELL missing SL/entry logs `REJECTED/execution_gate` + ops event and flips to HOLD. | `intelligence/llm_orchestrator.py` |
| 5 | OpenRouter API key is only required when OpenRouter is actually part of the selected provider set (`--llm-providers`); Cerebras/NVIDIA-only deployments can start. | `mt5_trader.py` |
| 6 | TUI dashboard updates (`update_rl_stats`, second `update_decision`, narrative) moved out of the unreachable `if not display:` branch so they now run when a display instance exists. | `intelligence/llm_orchestrator.py` |
| — | Removed duplicated `ModelSelector = None` assignment. | `mt5_trader.py` |

Remaining open items from this audit (not yet fixed): #7 paper-mode position management,
#8 paper active-trade limit counting, #9 hardcoded balance seed, #10 holiday-consumed candles,
#11 unbounded memory growth, #12 dual persistence, #13 KeyboardInterrupt mid-LLM RESERVED telemetry,
#14 duplicate per-pulse work under `--strategies both`.
