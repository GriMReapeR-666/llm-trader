# LLM Trader: Deep Audit (Part 2)

**Date:** 2026-10-04 · **Commit:** `3e7baf1` + the macOS port (uncommitted)
**Extends:** [`AUDIT_2026-10-04.md`](AUDIT_2026-10-04.md) (Part 1). Where the two disagree, this document wins; corrections are listed in §10.
**Detail reports:** [`work/`](work/). Each has file:line evidence, probes and a "verified clean" section.

| Report | Scope | 🔴 | 🟠 | 🟡 | ⚪ |
|---|---|---|---|---|---|
| [01a hot path](work/01a_hot_path_deep_read.md) | Read line by line: `execute_cycle` middle, `trade_manager`, `market_data_provider`, data quality, schedules | 3 | 5 | 9 | 2 |
| [01b research](work/01b_research_deep_read.md) | `research/`: promotion, training, model loading, backtest engines | 0 | 5 | 7 | 1 |
| [02 prompt injection](work/02_prompt_injection.md) | Every untrusted-text path into an LLM prompt, with a working PoC | 0* | 3 | 7 | 2 |
| [03 look-ahead / backtests](work/03_lookahead_backtest.md) | Point-in-time features, fill models, labels; a 62-function truncation test | 2 | 4 | 6 | 2 |
| [04 test strength](work/04_test_strength.md) | Mutation testing (1,357 mutants, 6 safety modules); 7 property tests | score 48 % | 2 | 2 | 1 |
| [05 tool sweep](work/05_tool_sweep.md) | ruff, pyright, bandit, semgrep, pip-audit, npm audit, vulture, radon, secrets, layering | 1 | 6 | 9 | 4 |
| [06 concurrency](work/06_concurrency.md) | Threads, locks, SQLite, signals; 9 stress probes | 3 | 7 | 3 | 1 |
| §7 here | **The deck run in a real terminal**, plus the MT5 bridge on your demo account | | | | |

\* P1 is 🟠 today and 🔴 the moment tool calls are wired through the router (§2.3).

**How this was checked:** seven parallel reviews, each told to verify against the callers and to mark anything unconfirmed. I then re-checked the highest-impact claims against the code myself (marked ✔ below). Probes ran offline against temp databases or fake MT5 modules. The one live check was a **read-only** session against your MetaQuotes **demo** account.

**Not yet covered:** your Windows working folder and runtime databases (you'll send these). Until then, every claim here is about the code at `3e7baf1`, not about what has actually happened in your trading history.

---

## 1. The verdict, revised

Part 1 said the safety design is better than its wiring. The deep pass confirms it and goes further: **live mode is not ready for real money, and paper mode isn't safe to run against a real-money terminal.**

1. **Live mode can't trade at the moment, and that is what keeps it safe.** Every live/demo cycle is rejected by a data-quality gate whose inputs are only filled in later (01a F13 ✔). Every live pending order is rejected by a reference to a missing enum value (05 T1 ✔). Behind those two accidental locks:
   - live positions are invisible to position management, the position limit and the heat cap (01a F1 ✔);
   - every open live trade gets booked as closed at 0 P&L (01a F2);
   - risk per trade compounds without any approval (01a F3);
   - the spread and margin re-checks are dead (01a F5, F19; 05 T2).

   **Fixing either lock first would turn a live engine that doesn't trade into one that trades unmanaged.**
2. **There are at least six ways to place an order that skip the validator**, so "every order passes the deterministic validator" is false:

   | Path | Reaches the broker? | Ref |
   |---|---|---|
   | Deck MT5 palette `mt5_order_send` | Yes, on Enter, no confirmation | Part 1 C3 |
   | **Paper-mode pyramiding** calls the real `mt5.order_send` | **Yes**, on whatever account the terminal is logged into | 01a F8 |
   | RL ε-exploration turns an LLM HOLD into BUY/SELL | Through the validator, but the trade wasn't the LLM's decision | 01a F4 / 01b R1 |
   | LLM outage → institutional workflow promoted at confidence 0.85 | Through the validator | 01a F6 / 02 P12 |
   | Decision-LLM tool `execute_dynamic_metric_analysis` → `exec()` with full builtins | Arbitrary code on the trading host (latent) | 02 P1 / 05 S1 ✔ |
   | Research backtest engine writes simulated trades into the live `trades` table | Changes the weekly loss limit | 01b R2 / 03 B4 |

3. **The emergency stack is broken at every layer**, and I demonstrated this end to end in the deck:
   - the kill-switch latch is split across two module copies (Part 1 C1), and those copies tear the shared state file, 3,904 of 17,730 reads in a probe (06 F5);
   - `flatten`/`killswitch` are no-ops (C4);
   - **the deck still shows "FLATTEN executed by operator"** (§7, D1 ✔ observed);
   - cross-process controls are lost, 1,826 of 2,000 in a probe (06 F7);
   - SIGTERM can deadlock the engine or be swallowed (06 F4).
4. **The risk limits can be exceeded:**
   - the open-risk cap is check-then-act and doesn't see pending orders; 20 of 20 race trials went over the cap (06 F1 ✔);
   - parallel reconciliation counts closes more than once, so 200 closes became 800 "wins" and position size grew toward the 1.8× cap (06 F3, 01a F7);
   - the copula heat budget latches at its crisis value (01a F16).
5. **The evidence that drives promotion can't be trusted:**
   - ensemble weights use the full sample (03 B3 ✔): noise alphas scored t = 8.1;
   - the hybrid tier-1 backtest makes random signals look profitable (03 B5);
   - fills and exits are read from the same bar (03 B1, B2);
   - backtests write into the live journal (01b R2);
   - boot validation keeps only the last strategy's result (01b R6);
   - **every automated promotion path is dead anyway** (01b R4);
   - the deck shows the registry's unbacked figures as live engines (§7, D2).

---

## 2. New critical and high findings (deduplicated)

Severity follows Part 1. ✔ = re-verified by me against the code.

### 2.1 Live-mode correctness (fix as one unit, after the order gateway exists)
| ID | Finding | Ref |
|---|---|---|
| 🔴 L1 ✔ | `PaperBroker` is constructed and attached in **every** mode. In live, `TradeManager._get_positions` returns the empty paper book, so breakeven, trailing and scale-out never run, the daily-loss flatten clears itself without closing anything, and the max-positions check and heat cap read 0. | `llm_orchestrator.py:809-817, 839-841`; `trade_manager.py:124`; `risk_manager.py:703, 777` (01a F1) |
| 🔴 L2 | Live reconciliation books every open live trade as CLOSED at 0 P&L. The "do not fabricate" guard sits on the wrong `if`, and the method then crashes with an `IndexError`. | `llm_orchestrator.py:5119-5190, 5300` (01a F2) |
| 🔴 L3 | **Paper-mode pyramiding sends a real order** (`mt5.order_send`, 0.1 lot, stale SL) to whatever account the terminal is logged into. It skips the validator, journal, guardian and margin checks. | `trade_manager.py:739-820` (01a F8) |
| 🟠 L4 ✔ | Every live pending order is rejected: `ConstraintVerdict.HOLD` doesn't exist, so the check raises and the order is refused. | `order_execution.py:756`; `trading_constraints.py:73-77` (05 T1) |
| 🟠 L5 | Every live/demo cycle returns HOLD, because the data-quality gate needs `clock_health`/`provider_time`, which are only set after the LLM call. Broker server time is also treated as UTC, so a fix would still reject on UTC+2/+3 servers. | `llm_orchestrator.py:7483, 2842-2851` (01a F13) |
| 🟠 L6 | `risk_per_trade` is multiplied in place on every cycle by the crash detector, without the research-influence gate (1% → 2% in 4 cycles). It is one object shared by all strategy threads. | `llm_orchestrator.py:4759-4767` (01a F3) |
| 🟠 L7 | The spread-explosion guard compares the spread with 0.8× itself, so it can never fire. On a tick error it passes with spread 0. `_symbol_tick` is also undefined in that module. | `market_data_provider.py:993-1008` (01a F5, 05 T2) |
| 🟡 L8 | Send-time margin re-check: wrong call signature, `TypeError` swallowed. Distributed trade lock: `acquire(blocking=True)` raises `TypeError`, logged at debug. | `order_execution.py:314, 768-778` (01a F19, 06 F8) |
| 🟡 L9 | Time stops and durations compare MT5 server epochs with UTC `time.time()`, so on a UTC+3 server a 4 h stop fires at about 7 h. | `trade_manager.py:455` (01a F10, 03 A3) |

### 2.2 Concurrency
| ID | Finding | Ref |
|---|---|---|
| 🔴 K1 ✔ | The open-risk cap is check-then-act, and `open_risk()` only sums filled journal rows, so pending orders and concurrent strategy threads are invisible. **20 of 20 trials exceeded the cap; with pending orders, 6 were admitted where the cap allows 3.** | `trading_safety.py:828-831, 1361` (06 F1) |
| 🔴 K2 | MetaTrader5 is called from at least 4 threads with no lock, and `last_error()` is process-global. (Whether native calls actually interleave is unverified; no Windows terminal here.) | 06 F2 |
| 🔴 K3 | Concurrent `reconcile_open_positions` runs record each close several times. An unlocked `consecutive_wins += 1` pushes size toward the 1.8× cap; in the probe, 200 closes were counted as 800 wins. | `pipeline_v2.py:304`; `risk_manager.py:352-363` (06 F3, 01a F7) |
| 🟠 K4 | A SIGTERM arriving while `GraphStatePersistence._lock` is held deadlocks the process (stack captured). Otherwise SIGTERM is swallowed and the engine keeps running. | 06 F4 |
| 🟠 K5 | Operator controls written from the API process are lost: 1,826 of 2,000 `killswitch` records in the probe. | 06 F7 |
| 🟠 K6 | The LLM budget reservation is check-then-insert: `max_concurrent=1` granted up to 3 slots. The single-instance engine guard races the same way. | 06 F6, F9 |
| 🟠 K7 | The guardian's flatten is a single pass, and an entry can slip in between its trip and the flatten. | 06 F10 |

### 2.3 LLM trust boundary
| ID | Finding | Ref |
|---|---|---|
| 🟠→🔴 I1 ✔ | The decision LLM is sent 39 tool schemas, including `execute_dynamic_metric_analysis`, which runs `exec`/`eval` with full builtins, no review and no timeout (`__import__('os')` works). It is inert today **only** because `LLMRouter.generate` turns tool-call messages into a string. A one-line router change would let a crafted headline run code on the host that holds your broker credentials. | `trading_tools.py:650-662`; `code_interpreter.py:150-186`; `llm_orchestrator.py:1785-1793`; `llm_router.py:730-745` (02 P1, 05 S1) |
| 🟠 I2 | Raw headlines go into the decision prompt with no data/instruction boundary. **PoC:** the real `get_decision` with the LLM stubbed out turned an injected headline into BUY 1.085 / SL 1.084 / TP 1.089 at confidence 0.95. The injection regex misses it and isn't applied on this path anyway. | `api_manager.py:356` → `market_data_provider.py:1843` → `llm_orchestrator.py:1593` (02 P2, P4) |
| 🟠 I3 | The source allow-list matches on publisher *name*, so "Reuters Insider Blog" at attacker.example is allowed into the macro graph and claim extraction. | `macro_source_policy.py:120-141` (02 P3) |
| 🟡 | Lenient "repair" parser (prose around the JSON, `Infinity`); escapable alpha-DSL sandbox; injected text replayed for about 5 cycles via the context buffer; D9 tripwire counts non-allow-listed sources (denial of service). | 02 P5-P10 |

**Controls that hold** (02): the symbol never comes from LLM output; volume comes from the RiskManager with lot caps, and LLM confidence can only shrink it; entry must be within an ATR tolerance of the live price; the bounded-autonomy LLM can only veto.

### 2.4 Research and statistical validity
| ID | Finding | Ref |
|---|---|---|
| 🔴 E1 ✔ | Ensemble weights are full-sample IC statistics applied to every bar. On 12 pure-noise alphas, the second-half IC was +0.048 (t = 8.1) against +0.0004 out of sample. 17 of 23 Alpha101 factors use full-series `rank()`/`scale()`. | `alpha_ensemble.py:356, 433-438`; `alpha_factors.py:69-83` (03 B3) |
| 🔴 E2 | Hybrid tier 1 caps losers but keeps winners' overshoot: random signals with zero costs score +0.36 R per trade. Tier 2 ignores the candidate and runs EMA 12/26. | `hybrid_backtest_pipeline.py:52-130` (03 B5) |
| 🟠 E3 | Fills and exits are read from the same bar: entry at `closes[i+1]`, SL/TP checked against that bar's high/low, so 75 trades booked SL exits *at a profit*. `broker_replay` at latency 0 does the same and charges half the spread; the causal-edge replay gate uses that setup. | `full_pipeline_backtest.py:1014`; `broker_replay.py:141-190`; `autonomous_causal_engine.py:797` (03 B1, B2) |
| 🟠 E4 | `mt5_backtest_engine` silently falls back to synthetic data with a positive drift, auto-promotes to PAPER_PROVEN from one in-sample run (even when its own DSR check fails), inserts simulated trades into the live `trades` table (they count toward the weekly loss limit), and writes APPROVED memories and playbook rules. Shorts get slippage added to profit. | `mt5_backtest_engine.py:684-937` (01b R2, 03 B4) |
| 🟠 E5 | Validation verdicts don't hold: continuous backtests ignore CPCV/PBO, a DSR error *removes* a blocker, boot keeps only the last strategy's result, and a failed backtest blocks nothing. | `continuous_backtest_pipeline.py:662-703`; `autonomous_bootstrap.py:323` (01b R3, R6) |
| 🟠 E6 | Two writers race on `rl_model.json`; the same trades are re-applied every 50 cycles, and simulator-trained Q-values are registered as if they came from broker trades. | 01b R5 |
| 🟠 E7 | FRED observation dates are used as release dates, on latest-vintage (revised) values. | `fred_backtest_data.py:170-187` (03 B6) |
| 🟡 | Every automated promotion gate is dead (missing method / metric that is never computed); `vbt_engine` annualizes M15 as M1 (Sharpe about 3.9× high); triple-barrier labels overlap without uniqueness weights; Kronos/Laya weights load without integrity checks; the "OOS validation" never tests the models, and its WFE is a constant 0.833. | 01b R4, R7, R8, R12; 03 B7-B9 |

**Truncation test** (03): 62 feature/indicator functions, **40 pass, 22 fail**. Core indicators (EMA, RSI, MACD, ATR, ADX), `feature_builder.build_snapshot` and `smc.detect_structure` pass. The failures are research-side: Alpha101 rank/scale, rolling-IC labels, the ensemble composite, and centred SMC pivots.

### 2.5 Crashes and dead code found by the tools
| ID | Finding | Ref |
|---|---|---|
| 🟠 | `llm-trade --symbol AUTO` or a comma-separated symbol list **crashes at startup** (`fetch_ohlc` becomes a function-local and is unbound). | `mt5_trader.py:4151` vs `:4533` (05 T3) |
| 🟠 | Live `reconciliation-run` always fails: `_get_mt5` is undefined. | `mt5_trader.py:2747` (05 T4) |
| 🟠 | Logger setup pasted *inside module docstrings* in 9 files, so `logger` is undefined: the keyring fallback in `secure_store` crashes instead of falling back, and so do the memory search fallbacks. | `core/secure_store.py:1-5` + 8 files (05 T5) |
| 🟠 | The alpha-miner walk-forward tests a momentum baseline but labels it `discovered_alpha`. | `alpha_miner/pipeline.py:957` (05 T6) |
| 🟡 | Writes to frozen dataclasses and `sqlite3.Row` in the size-reduction, Kronos-veto and reconciliation steps crash, and the errors are swallowed. | 05 T7-T9 |

**Tool totals** (05): ruff 15,456 (174 undefined names) · pyright 1,460 errors · bandit 104 medium+ · radon: 227 functions at complexity rank D-F · vulture: 71 unused items · **pip-audit: 0 known vulnerabilities · npm audit: 0** · secrets: only the known `ROUTESME_KEY`, which **is also in git history**, so it must be rotated.
**Layering:** almost every top-level package imports every other (`core` → `intelligence` 51 imports, `core` → `research` 52). `research` can reach the order path through `market_data_provider`. `pipeline_v2` imports `ui.gui_api.ui_state`, a second UI-state singleton.

---

## 6. Test strength (mutation testing)

Full detail in [work/04_test_strength.md](work/04_test_strength.md). 1,357 mutants across 6 safety modules; raw results in `work/mutation/`.

| Module | Coverage | **Mutation score** |
|---|---|---|
| `trading_safety.py` | 83 % | **49 %** |
| `margin_safety.py` | 80 % | **44 %** |
| `order_execution.py` | 40 % | **38 %** |
| `risk_manager.py` | 72 % | **43 %** |
| `kill_switch_latch.py` | 94 % | **69 %** |
| `autonomy.py` | 91 % | **68 %** |
| **All** | | **48 %** |

**The suite catches fewer than half of the bugs that could be planted in the code that protects money.** The worst gaps:
- **Every individual validator gate** (market closed, symbol not tradable, spread, stop distance, reward/risk, confluence…) can be deleted and the suite still passes. Tests only fail several gates at once.
- **Order geometry** (`validate_order_params`, 25 %): inverting the BUY/SELL SL/TP side checks or the 5 %/0.5 ATR bounds goes unnoticed. Part 1 relied on this gate.
- **Risk limits:** the drawdown ladder (14 %), streak multiplier (16 %), weekly breaker (20 %), max-positions cap and `check_drawdown` (0 %), and the arbiter's daily, weekly and open-risk comparisons all accept flipped or sign-swapped logic.
- **Margin:** every threshold constant can be doubled.

**Property tests: 5 of 7 failed, and all five are real bugs** (verified by me):
- 🟠 NaN, zero or negative equity gives the drawdown multiplier **1.0 (full size)** (`risk_manager.py:454`);
- 🟠 a corrupt latch checkpoint after a crash comes back **unlatched** (`kill_switch_latch.py:89-98`);
- 🟡 a NaN changepoint probability is **approved** (`trading_safety.py:1116`);
- lot rounding exceeds the 1 % cap by up to 12.5 %;
- NaN prices pass `validate_order_params`.

**Correction:** `test_p0_gates.py::test_overfit_critical_blocks_trade` is not flaky. Its one failure came from a mutation worker copy that still held an aborted mutant.

**Highest-value fix:** a table-driven "break exactly one gate" test for the validator, arbiter, geometry and margin. It's cheap to write and would kill about 150 of the 626 survivors.

---

## 7. The deck in a real terminal, and the MT5 bridge on your demo account

### 7.1 What was run
- **MT5 bridge, live:** MetaTrader 5 for Mac, with Windows Python 3.11.9 inside its Wine, then the bridge server, then the Mac `.venv`. Read-only session on **MetaQuotes-Demo** ($100,000, leverage 1:100, 12,335 symbols):
  - `initialize()` 0.1 s; quote round-trip **0.5 ms**; 500 × M15 bars 1.7 s on the first fetch.
  - The constant check against the real package passed.
  - Terminal "Algo trading" is off (`trade_allowed False`).
  - Account history afterwards: one deal, the $100,000 opening deposit. **No orders were placed.**
- **Bridge fixes found by testing for real:** the server imported the Mac shim instead of the real package (same folder); real results are C struct sequences with `_asdict()` but no `_fields`; the stock Python installer is 32-bit and won't run in MT5's 64-bit-only Wine (switched to the official NuGet build); a client disconnect crashed a server thread. All fixed; tests updated (7 pass).
- **Deck:** `bun src/index.tsx` with `TRANSPORT=auto` against the API server, driven in a real pseudo-terminal. I visited all 40 screens, the help, the palette and a full **Flatten** confirm flow. Screens were captured as text.

### 7.2 Findings from running the deck
| ID | Sev | Observed | Where |
|---|---|---|---|
| D1 ✔ | 🔴 | **"FLATTEN executed by operator"** appears after confirming Flatten, with **no engine running**. The API wrote `{"action":"flatten","consumed":false}` to `control_plane.jsonl` and returned 200; nothing was flattened (and per C4 it wouldn't be even with an engine). The code also shows "executed" when the deck isn't connected at all (`!live \|\| ok`), having contacted nothing. | `components/CommandPalette.tsx:378-413`; `ui/gui_api.py` deck_control |
| D2 | 🟠 | Unbacked registry figures shown as live: Positions "**103 ENGINES ACTIVE**"; Strategies "Kronos-Neural H1 · FULL · **● live** · gate 100 % · Sharpe 2.14"; Observatory "**+1.84R** profitability" charts. The registry's own `_EVIDENCE_INVALID` marker says these are hand-written. | Positions, Strategies, Observatory |
| D3 | 🟠 | Security screen: "**✓ clean** · Scanned **0 files**. Zero exposed credentials." Green after scanning nothing, while a real key is in the repo (C5). | Security |
| D4 | 🟡 | Statuses default to healthy without data: every alpha is "HEALTHY" with IC 0.000; the Pipeline Graph shows a **hard-coded** latency spec (+0.8 ms … "[RUNNING]") under "● LIVE" with no engine. | Alpha Lab, Pipeline Graph |
| D5 | 🟡 | Contradictory state: header "CONNECTING" while the feed says LIVE; Cockpit balance $0.00 while Positions shows $100,000; Analytics "now $100,000 · peak $0.00"; MT5 screen "● MCP LIVE" next to "terminal: DISCONNECTED"; autonomy `L0_SHADOW` vs `L0_DISABLED`. | several |
| D6 | 🟡 | Navigation: `[`/`]` switch **sections** and `Tab` switches **screens**, but the status bar only says "1-6 sections · / palette", so `Tab` is undiscoverable. 40 screens. | `App.tsx:190-209` |
| D7 | 🟡 | Design-law notes rendered as operator text: "(Von Restorff #7)", "(Hick #1: one purpose per screen)", "Tesler #19", "Sensible defaults #13", "Goal-Gradient #20". | Cockpit, Orchestration, Scanner |
| D10 | 🟠 | **The deck's direct-MT5 mode (`TRANSPORT=mcp`) is broken against the published `metatrader5-mcp` 1.0.0.** Every tool takes a single `params` model (`tools_market.py:72`, `tools_positions.py:32`), but the deck sends flat arguments, so ticks, positions, orders and rates fail with `missing_argument`/`unexpected_keyword_argument` (only argument-less tools like account info and health check work). The MCP server's log output also prints over the TUI, because `StdioClientTransport` is started without capturing stderr. The Python package isn't pinned anywhere. Tested live through the bridge on the demo account. | `tui_opentui/src/transport/mcp.ts:94-533` |
| D8 | ⚪ | A missing timestamp is shown as `05:30:00` (Unix time 0 in IST). | Workflows |
| D9 | ⚪ (confirm by eye) | Text overlap in the Macro calendar table and the confirm dialog (`FLATTENRallNpositionsNat market`, `ru│ning`). This **may be an artifact of my terminal emulator**; please check the Macro screen in a real terminal. | Macro, Tasks, confirm dialog |
| ✓ | | Works: boot and transport recovery; fails closed with no ticks ("no market ticks yet"); Flatten asks for confirmation with **CANCEL as the default**; Approvals inbox empty state; help and palette open and close. | |

**Deck findings outside the deck code:** the agents' probes and my test run wrote into the local runtime `trading_state.db` (the Backtest screen showed 3 probe runs). That file is gitignored, and I'll reset it. The Backtest screen also showed a real run of `meanrev_bollinger` (EURUSD H1, synthetic-data fallback) at **PF 0.36, Sharpe −6.89**, while the registry lists it as FULL with PF 1.95 / Sharpe 1.92. That's not proof either way, given E4, but it is the kind of gap D2 hides.

---

## 8. Before any real money: Gate 0

None of these is optional. They are ordered so that no step exposes a hidden problem before it's fixed.

1. **Rotate the RoutesMe key** (it's in git history) and add a secret-scan pre-commit hook.
2. **Pin the safety logic before changing it:** add the "break exactly one gate" tests for the validator, arbiter, geometry, margin and risk ladders (§6). Without them, the refactors below can silently break a gate.
3. **Close the bypasses:**
   - remove `mt5_order_send` from the deck;
   - add a paper branch to the pyramid order (L3);
   - stop the RL bridge from changing the action (01a F4);
   - remove the outage→workflow promotion (01a F6);
   - remove `execute_dynamic_metric_analysis` from the decision LLM's tools (I1);
   - make the backtest engine write to its own database (E4).
4. **Build the single order gateway** (Part 1 §9.2): `validate → reserve risk (atomic, counting pending orders) → journal → send`, holding one MT5 lock. This fixes K1, K2 and L8.
5. **Make the emergency stack real:**
   - one import root (C1);
   - implement flatten and killswitch;
   - controls through the journal with compare-and-set, not a JSONL file (K5);
   - signal handlers that only set a flag (K4);
   - the deck shows *applied* outcomes, never "executed" on 200 (D1).
6. **Fix live correctness together:** L1, L2, L6, L7, L9 and the server-time offset. Only then L5 and L4, which re-enable live trading.
7. **Gate the evidence:** fix E1-E5, fail closed on missing evidence, and hide the registry figures from the deck until they're regenerated (D2).
8. **Demo-soak on your MetaQuotes demo** through the bridge for 2+ weeks. Reconcile journal against broker every day and drill flatten and killswitch with real demo positions. Only then consider live.

---

## 9. What the Windows folder and runtime data will settle

| Question | Data needed |
|---|---|
| Has L2 (fake closes) or K3 (double-counted wins) already corrupted your journal and risk state? | `trading_state.db` `trades`, `risk_state.json` |
| Have backtest rows (tickets 999,000,000+) landed in the live `trades` table (E4)? | `trading_state.db` |
| Did the split latch (C1) ever hide a news-triggered halt? | latch checkpoint, `operational_events`, logs |
| How often does the LLM return unparseable output, and how often does HOLD become BUY via RL (01a F4)? | `llm_decisions.db`, `operational_events` |
| Were any orders placed outside the engine (deck palette, pyramids in paper)? | broker history vs journal (`daily_reconciliation`) |
| Is `strategy_trial_ledger.py` among the untracked files (H3)? | the Windows working folder |

---

## 9b. Smaller repo findings from this pass
- **The deck has no lockfile because `.gitignore`'s `*.lock` pattern (meant for SQLite lock files) also matches `bun.lock`.** Add `!tui_opentui/bun.lock` and commit one (Part 1 M8).
- **Tests write runtime state into the repo:** a full test run creates `artifacts/` (about 30 files, including `trial_ledger.db` and `llm_decisions.db`), `trading_state.db` and `compliance_audit.db` at the root, plus stray `--db`, `..tmp` and `:memory:` files. Point all test stores at `tmp_path`.
- **Tests overwrite the committed runtime evidence.** Now that `artifacts/` is tracked (merged from `origin/main`), one full test run modified 17 committed files, including the evidence ledgers `artifacts/trial_ledger.db` and `artifacts/strategy_trial_ledger.db`, and pruned the scheduler reports. Move evidence to a read-only location (e.g. `data/evidence/`) and point every test store at `tmp_path`.
- **Parallel test collection races a schema migration:** `intelligence/active_knowledge_agent.py:692` builds a module-level `ActiveKnowledgeAgent()` at import time against the repo-root `trading_state.db`, and `trading_memory.py:143` migrates it check-then-`ALTER`. On a fresh checkout with `-n 6`, two workers add the same column (`duplicate column name: label_rule`) and xdist aborts. Make the singleton lazy and the migration idempotent under a lock or `BEGIN IMMEDIATE`.
- **Tests attach to a live terminal when one is available.** With the MT5 bridge up, the suite's `initialize()` calls connected to the real demo terminal (bridge log, clients 6-13). The same happens on Windows with MT5 running. Tests must never reach a terminal unless an explicit opt-in flag is set.

## 10. Runtime evidence (pushed to `origin/main`, commits `ea5f984`…`f218887`)

Checked against `data/evidence/trading_state_evidence.db` (18 tables), `data/logs/guardian_audit.jsonl` (51,283 rows), `data/state/risk_state.json` and `artifacts/` (531 entries), extracted read-only from `origin/main`.

**Privacy check:** no API keys, passwords or MT5 account numbers in any of it. The repo is now private (anonymous API returns 404); it was public earlier today.

### 10.1 What the data shows
| Finding | Evidence | Audit link |
|---|---|---|
| **The journal holds no real broker trades.** 150 of 151 rows have `synthetic: true`, `source: synthetic_fixture_legacy` and `fixture_note: "Not a broker fill…"`; all share entry 1.1 / SL 1.0 / TP 1.3 and score exactly +1.5R or −1.0R. **Any win rate, profit factor or equity curve derived from this journal is fabricated.** | `trades` | D2, H5 |
| **A backtest trade leaked into the live journal:** ticket 1,003,479,400, `source: mt5_backtest`, magic 888888, SHORT 1.0 lot, P&L 50.5 on risk 100 (0.505R) while its own payload says `r_multiple: 2.02` (pips stored as money). | `trades` | **E4/R2 confirmed in production** |
| **No LLM proposal has ever become a trade.** 744 decisions over 10 days (23 Sep-2 Oct): BUY 379, SELL 184, HOLD 168, no action 13. `trade_ticket` is empty on every row. All 61 journalled proposals were REJECTED. | `llm_decisions`, `trade_lifecycle_events` | L4/L5 |
| **Rejection reasons:** setup registry no-match 23; DSR overfit gate 17; **bar/feature timestamp missing, malformed or "later than capture time" 20**; regime/changepoint/news/event gates 13; other 4. "Later than capture time" is what treating broker server time as UTC produces. | `trade_lifecycle_events` | L5, L9, 03 A3 |
| **LLM providers are unreliable:** 90 of 137 requests ERROR (66 %). Successful latency: median 24.5 s, p90 70 s, max 182 s. 721 of 744 decisions have no symbol or timeframe recorded. | `llm_requests`, `llm_decisions` | §2.2 deadlines |
| **Promotions without evidence:** 14 strategies set straight to `DEMO_PROVEN` and 14 to `PAPER_PROVEN` from no prior state; 104 no-op READY→READY transitions; `mt5_backtest_engine` made 7 lifecycle transitions; **51 transitions by `operator_antigravity`**, apparently an AI coding agent acting under the operator identity. | `strategy_lifecycle_history` | Prior #4, E4, D2 |
| **The news kill switch has been latched since 24 Sep 18:31 UTC, on false alarms,** tripped by headlines such as "Ellison Pledges $9.2 Billion More in Oracle Shares" and "Jev, an AI Model That Can't Chat…". The project's own report counts 33 benign false trips against 13 real ones. **It has no effect on trading:** the latch is read only by the deliberative graph, which runs in shadow mode (`DELIBERATIVE_GRAPH_SHADOW=1` default, "never touches the live path"), and no rejection in the next 8 days cites a kill switch. | `artifacts/d9_kill_switch_latch.json`, `d9_false_positive_report.json`, `mt5_trader.py:4681-4692` | **New, and worse than C1** |
| **The daily-loss limit reset 37 times in 11 days** (up to 8 times on 30 Sep). Four of those resets were boots at 00:46-04:18 IST, inside the window where H1 predicts a reset on every restart. Several resets come in pairs milliseconds apart, consistent with two RiskManager instances (C1). Logs are in IST (`session_…T0057Z.log` starts at 06:27). | `artifacts/*session*.log`, `live_run_*.log` | H1, C1 |
| `risk_state.json`: `start_equity` 100,000 vs `initial_balance` 10,000. | `data/state/risk_state.json` | Prior #8 |
| Guardian: 51,275 OK polls, 7 heartbeat timeouts (Γ silent ~120-122 s against a 120 s limit), 1 `account_info() returned None`. | `guardian_audit.jsonl` | §2.2 deadlines |

### 10.2 The delivery summary's answers, checked
| Claim | Verdict |
|---|---|
| "All 151 trades … preserving complete empirical auditability" | **Misleading.** 150 are synthetic fixtures and 1 is a leaked backtest trade; there are no broker fills. `operational_events`, `state`, `order_intents` and `broker_execution_reports` were not exported. |
| Q1 "The split latch never missed a halt" | **Not supported.** One checkpoint file can't show what two in-process copies did. The data instead shows the latch stuck on false alarms since 24 Sep and **not enforced anywhere on the trading path**. |
| Q2 "LLM parse failure 0.8 %" | **Confirmed** (738/744 valid JSON). Not the risk that matters, though: the 66 % provider error rate is. |
| Q3 "The daily limit does not reset on restart" | **Contradicted.** The reasoning covers same-date restarts only and ignores the local-vs-UTC comparison. Logs show 37 resets in 11 days, including boots inside the predicted window. |
| Q4 cycle times (234 s boot, 180 s cadence) | Plausible, not re-measured. The more decision-relevant number is LLM latency (p90 70 s, max 182 s) against a 180 s cycle. |
| "76/76 tests passing" for the restored ledger | The four new or changed test files pass (49 tests) on `origin/main`. |

### 10.3 New fix commits
- `776ff47` (readiness: fail closed when the ledger module can't be imported): **correct.** Resolves H3, together with `ea5f984` restoring the module.
- `15b4a1a` (warn on micro-lot truncation to 0.0): fine; it logs instead of failing silently.
- `f218887` (API token auth): **doesn't close C2 as shipped.** It is opt-in (`if not token: return True`); no launcher sets `GUI_API_TOKEN`; it isn't in `.env.example`; **the deck and the web GUI never send a token**, so enabling it breaks every control button. CORS is still `["*"]`, there's no Host check, read routes stay open, and a `?token=` in the URL ends up in logs. To finish it: generate the token at install (like `mt5_bridge.key`), send it from both clients, make it mandatory, and restrict CORS to the deck/GUI origin.

## 11. Corrections to Part 1
- Part 1 §4 said "deterministic fallbacks can never escalate to a trade". That's true inside `get_decision`, but **01a F6 re-escalates after it returns**.
- Part 1 §7.2 described the deck's digit keys as mode-overloaded. Precisely: digits pick a *section*, `[`/`]` cycle sections, `Tab` cycles screens.
- Part 1 C4 said the flatten and killswitch controls "report success but do nothing". The deck run confirms this, and it is worse: the deck also reports success **when it isn't connected to anything** (D1).
- Part 1 §6 coverage figures stand, but coverage overstates protection: see §6 (mutation testing).
- Part 1 H5 said the Deflated Sharpe "never deflates". That's true of the metrics engine (the figures shown to the LLM), but the **overfit gate does deflate** (it used n_trials = 16,743 in real rejections, §11).
- Part 1 H3 (missing trial ledger, always-valid stub) is **resolved** by `ea5f984` + `776ff47`.
