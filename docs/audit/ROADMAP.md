# Remediation Roadmap

**Source:** [`AUDIT_2026-10-04.md`](AUDIT_2026-10-04.md) (Part 1), [`AUDIT_2026-10-04_DEEP.md`](AUDIT_2026-10-04_DEEP.md) (Part 2) and [`work/`](work/).
**Rule:** every audit finding appears exactly once below. Duplicates found by several reports are listed together (`=`).

**ID prefixes:**

| Prefix | Source |
|---|---|
| `P1-` | Part 1 |
| `D-` | Deck run (Part 2 §7) |
| `EV-` | Runtime evidence (Part 2 §10) |
| `1a-` | [01a hot path](work/01a_hot_path_deep_read.md) |
| `1b-` | [01b research](work/01b_research_deep_read.md) |
| `PI-` | [02 prompt injection](work/02_prompt_injection.md) |
| `LA-` | [03 look-ahead](work/03_lookahead_backtest.md) |
| `TS-` | [04 test strength](work/04_test_strength.md) |
| `TL-` | [05 tool sweep](work/05_tool_sweep.md) |
| `CC-` | [06 concurrency](work/06_concurrency.md) |

**Severity:** 🔴 critical · 🟠 high · 🟡 medium · ⚪ low.

| Phase | Goal | Estimate | Gate to next phase |
|---|---|---|---|
| 1 | Safety net and test hygiene | 3-4 days | Single-gate tests green; tests never touch real data or a terminal |
| 2 | One door to the broker | 5-7 days | No order path bypasses the gateway (grep + test) |
| 3 | Emergency stack that works | 4-5 days | Flatten and kill switch drilled end to end in paper |
| 4 | Live-mode correctness | 5-7 days | Live engine on demo manages a position through its full lifecycle |
| 5 | Honest numbers | 4-5 days | Backtests pass the look-ahead and fill-realism checks; unproven stats hidden |
| 6 | Demo soak | 2+ weeks | Clean daily reconciliation, drills pass. **Live money only after this.** |
| 7 | LLM hardening and reliability | ~1 week | |
| 8 | Architecture and code health | 2-3 weeks | |
| 9 | UI/UX overhaul | 1-2 weeks | |
| 10 | Repo and documentation | 3-5 days | |

Each phase ships as its own branch and PR: tests first, then the fix, then the full suite, then `graphify update .`

---

## Done
- [x] macOS support: MT5 bridge, setup and launchers (`feat/macos-support-and-audit`)
- [x] `P1-H3` Missing trial-ledger module with an always-valid stub: restored (`ea5f984`) and made fail-closed (`776ff47`)
- [x] `TL-T17`-adjacent micro-lot truncation now warns (`15b4a1a`)

---

## Phase 1: Safety net and test hygiene
- [ ] 🟠 `P1-C5` **Rotate the RoutesMe key** (it's in public git history). Replace the fixture with an obvious fake. *Owner: you.*
- [x] 🟡 Add a secret-scan pre-commit hook (CI step: PR 1.4) (detect-secrets/gitleaks).
- [x] 🟠 `TS-1` Validator "break exactly one gate" table tests (every reason string, plus an all-pass fixture).
- [x] 🟠 `TS-2` Arbiter boundary tests: daily, weekly and open-risk at, below and above the limit.
- [x] 🟠 `TS-3` Order-geometry table tests (BUY/SELL × SL/TP sides, 5 % and 0.5 ATR bounds, NaN/inf).
- [x] 🟠 `TS-4` Margin threshold tests, one per constant and one per malformed-input reason.
- [x] 🟠 `TS-5` Risk-ladder tests: drawdown and streak multipliers, including NaN, 0 and negative equity.
- [x] 🟠 `TS-6` Latch persistence tests (corrupt file → stays latched; write from copy A, read from copy B).
- [x] 🟡 `TS-8` Mutation-score ratchet in CI on the six safety modules (weekly, `--min-score 50`). Measured at the end of Phase 1: **52.2 %**, short of the 75 % target. See `work/04_test_strength.md` § Phase 1 re-measure.
- [ ] 🟡 `TS-9` Raise the safety mutation score to ≥ 75 %. Most of the remaining survivors are in `order_execution` (place_pending_order and emergency_market_exit, which Phase 2 rewrites) and `calc_position_size` (Phase 4). Re-measure after each of those phases and raise the ratchet.
- [x] 🟠 `P1-H6` Raise order-path coverage (`order_execution` 40 %): an end-to-end proposal → `order_send` test on the bridge's fake broker.
- [x] 🟠 Part 2 §9b: tests overwrite committed `artifacts/` and `data/` evidence. Move evidence to a read-only location, and point every test store at `tmp_path`.
- [x] 🟡 Part 2 §9b: parallel collection races the `trade_labels` migration (make `ACTIVE_KNOWLEDGE_AGENT` lazy; idempotent migration under `BEGIN IMMEDIATE`).
- [x] 🟠 Part 2 §9b: tests must never reach an MT5 terminal without an explicit opt-in flag.
- [x] 🟡 Part 1 M7: stray files (`--db`, `..tmp`, `:memory:`) at the repo root; Windows-only tests marked `skipif`.
- [x] ~~Flaky `test_p0_gates.py::test_overfit_critical_blocks_trade`~~: not flaky (0/8 failures); the earlier failure was a mutation-harness artifact.
- [x] 🟡 `P1-M8` Commit `tui_opentui/bun.lock` (un-ignore it from the `*.lock` pattern).
- [x] 🟡 Part 1 §6: CI workflow (pytest, both `tsc` checks, the double-import guard, secret scan, graphify update).

## Phase 2: One door to the broker
- [x] 🔴 Build the **OrderGateway**: validate → atomic risk reservation (counting pending orders) → journal write-ahead → send → reconcile. One MT5 lock inside it. Fixes `CC-F1` (risk cap check-then-act), `CC-F2` (MT5 from 4 threads, no lock), `CC-F8` (trade lock never acquired).
- [x] 🔴 `P1-C3` Remove `mt5_order_send` from the deck palette (`mt5_order_check` only); manual orders go through the gateway with typed confirmation.
- [x] 🔴 `1a-F8` Pyramiding in paper mode sends a real order: add a paper branch, then route through the gateway. Also fix the stale-SL use.
- [x] 🟠 `1a-F4` = `1b-R1` The RL bridge must never change `action`; replace the dead "live" check with the real journal mode.
- [x] 🟠 `1a-F6` = `PI-P12` Remove the outage → institutional-workflow promotion at hard-coded confidence 0.85.
- [x] 🔴 `PI-P1` = `TL-S1` Remove `execute_dynamic_metric_analysis` (unsandboxed `exec`) from the decision LLM's tools.
- [x] 🟡 `PI-P10` `dynamic_code_runner`'s ζ review fails open; `PI-P6` the alpha-DSL AST check is escapable.
- [x] 🟠 `1b-R2` = `LA-B4` The backtest engine writes trades, memories, playbook rules and promotions into the production journal. Give it its own DB.
- [x] 🔴 `P1-C2` Finish API auth (started in `f218887`): token generated at install, sent by the deck and web GUI, mandatory, CORS allowlist, Host check, no `?token=` in URLs.
- [x] 🟡 `1a-F19` The send-time margin re-check has the wrong signature and never runs.
- [x] 🟠 `PH1-N3` (found by the Phase 1 pinning tests) `validate_order_params` skips **every** check for an order with no stop-loss (`sl=None` or `0`) or an unknown side (`"long"`). The gateway must require both. Pinned in `tests/safety/test_order_geometry.py`.
- [x] 🟠 `CC-F10` Guardian flatten vs concurrent entries (TOCTOU): block entries via the gateway while flattening.
- [x] 🟡 `TL-S2` The Prometheus exporter binds `0.0.0.0` by default.
- [x] 🔴 Phase 2 review `R1`-`R6`, `X1`-`X4`, `D1` (see `REVIEW_PHASE2_2026-10-04.md`): phantom reservations after broker-side closes, fake $10k equity, double-counted intents, failed-cancel release, state-machine bypass, unbound evidence, SL/TP cleared by modify, wrong-position close, exit requests missing broker fields, `place_market_order` TypeError, per-call paper/live switching. Pinned in `tests/safety/test_order_gateway_ledger.py`.
- [x] 🟠 Review `D2`: paper entries with validation evidence now go through the gateway (reservation, intent lifecycle). The reconciler journals paper fills from the paper book; before, paper market entries stayed UNRESOLVED and reserved risk forever. Also fixed: every fill crashed the reconciler on `TradeProposal(**payload)` (extra `validation_trace` key, present since the first commit).
- [x] 🟡 Review `D3` (analysed): daily/weekly loss and drawdown depend on realized P&L and equity, which entries don't change, so a stale read can't be raced past. The bucket cap uses the same fraction as the open-risk cap, so the atomic open-risk check already bounds it.
- [ ] 🟡 `D3b` The 2 % rolling-correlation cap and the FX-netting limits read open trades only (pending intents are invisible to them) and are not atomic. Move them into the Phase 4 risk engine.
- [x] 🟡 Review `D4`: `OrderGateway.sweep_stale_created` runs each reconcile. Paper → REJECTED. Live → matched against broker orders, positions and history (PENDING / UNRESOLVED + CRITICAL review / REJECTED). A send that raises is UNRESOLVED on live.
- [x] 🟠 Review `D6`: the web GUI has a sign-in screen. `gui/api.ts` `apiFetch` sends the HttpOnly cookie, `/` serves the built GUI, the static bundle is public, every `/api` call is authenticated, and `/api/auth/logout` exists.
- [x] 🟠 `PH2-N1` (found in review) Live planned entries never passed the harness ORDER_WRITE token, so every live pending entry raised `PermissionError`. Now `mt5_trader --live` hands its gated profile sandbox to the orchestrator, which mints a 2-minute broker-scoped token per entry (LIVE journal only). The live urgency path gets the same gate. `--demo` gets order authority only when the broker reports a demo account (`trade_mode == ACCOUNT_TRADE_MODE_DEMO`), re-checked on every order. Paper gets no token.

## Phase 3: Emergency stack that works
Plan: [`PLAN_PHASE_3_4.md`](PLAN_PHASE_3_4.md).
- [x] 🔴 `P1-C1` = `P1-M1` One import root: `pyproject.toml`, remove 88 `sys.path` inserts, add a double-import guard test. Also fixes `CC-F13` (lazy singletons without locks).
- [ ] 🟠 `P1-C4` Implement `flatten` (through the gateway) and `killswitch` (store + cancel pending); emit `CONTROL_EXECUTED` only on success. Make the autonomy gate fail closed.
- [ ] 🔴 `D1` The deck shows outcomes (requested → acknowledged → applied), never "executed" on HTTP 200, and never when disconnected.
- [x] 🔴 `EV` **The news kill-switch latch isn't enforced on the trading path** (only the shadow graph reads it). Wire it into the validator and gateway.
- [x] 🟠 `EV` + `PI-P9` The latch trips on false alarms (33 benign vs 13 real; latched since 24 Sep). Fix the tripwire precision and count only allow-listed sources.
- [x] 🟠 `PH3-N1` (found while planning) A missing VIX clears the news latch: `graph_nodes.py:444` defaults VIX to 18 (calm).
- [x] 🟠 `PH3-N2` (found while planning) Nothing can clear the news latch in live: the only caller of `apply_market_data` is the shadow graph. Add an operator clear and a market-data clear on the main path **before** enforcing the latch.
- [x] 🟠 `CC-F5` The latch copies tear the state file. `TS-P6`: a corrupt checkpoint must load as **latched**.
- [ ] 🟠 `PH3-N3` (found while planning) The API emits `CONTROL_EXECUTED` for a merely queued control and takes `actor` from the request body (`gui_api.py:1872-1877`).
- [ ] 🟠 `CC-F7` Cross-process operator controls are lost (1,826/2,000). Move controls into the journal with compare-and-set.
- [ ] 🟠 `CC-F4` The SIGTERM handler deadlocks or is swallowed. Handlers should only set a flag.
- [ ] 🟠 `CC-F9` The single-instance engine guard is check-then-act (use an OS file lock).
- [ ] ⚪ `CC-F14` Shutdown ordering (daemon threads killed mid-write).
- [ ] 🟠 `P1-H4` Validator approves on missing evidence: a typed `MarketEvidence` with no pass-defaults. Includes `TS-P3` (NaN changepoint approved).
- [ ] 🟠 `TS-P2` NaN, zero or negative equity gives drawdown multiplier 1.0 → fail closed (0.0).
- [ ] 🟠 `PH1-N1` (found by the Phase 1 pinning tests) An explicit `high_impact_news=None` (news feed returned nothing) is treated as **safe**; a missing key is treated as unsafe. Pinned as a strict xfail in `tests/safety/test_validator_single_gate.py`.
- [ ] 🟠 `PH1-N2` (found by the Phase 1 pinning tests) `RiskManager.check_daily_drawdown` allows trading when `daily_start_equity` is 0 or negative, or when equity is NaN. Pinned in `tests/safety/test_risk_ladders.py`.
- [ ] 🟡 `1a-F15` Pre-trade gates that fail open on exception (data quality, news gate, pending-order guardian) → HOLD.
- [ ] 🟠 `P1-H2` Enforce stage deadlines (`run_stage_with_deadline`) and honour `hold_only`.
- [ ] 🟡 Part 1 §7.2 **Persistent safety status bar** in the deck (PAPER/LIVE, autonomy, kill switch and latch, daily P&L vs limit, feed age, bridge, LLM circuit).

## Phase 4: Live-mode correctness (fix together; only then remove the two accidental blocks)
Plan: [`PLAN_PHASE_3_4.md`](PLAN_PHASE_3_4.md).
- [ ] 🔴 `1a-F1` The PaperBroker is attached in every mode, so live positions are invisible to management, the position limit and heat.
- [ ] 🔴 `1a-F2` Live reconciliation books open trades as closed at 0 P&L, plus an `IndexError` (`TL-T8` sqlite Row `.get` is in the same method).
- [ ] 🔴 `CC-F3` = `1a-F7` Concurrent reconciliation double-counts closes and the streak breaker (per-ticket CAS).
- [ ] 🟠 `1a-F3` `risk_per_trade` compounds every cycle, ungated, and is shared across strategies.
- [ ] 🟠 `1a-F5` = `TL-T2` The spread-explosion guard can never fire; `_symbol_tick` is undefined.
- [ ] 🟠 `P1-H1` The daily limit resets on restart (local vs UTC; **EV: 37 resets in 11 days**). Use the broker server day.
- [ ] 🟡 `1a-F10` = `LA-A3` Server time vs UTC across positions, deals and ticks (**EV: "bar later than capture time" rejections**).
- [ ] 🟡 `1a-F9` Pyramid and time-stop R basis use the current SL.
- [ ] 🟡 `1a-F11` Paper SL updates hit every position with the same symbol and magic; no monotonic guard.
- [ ] 🟡 `1a-F16` The copula heat budget latches at its crisis value.
- [ ] 🟡 `1a-F18` A second position-management engine with a dead partial close (pick one engine).
- [ ] 🟡 `1a-F14` `captured_at` is stamped after slow work, so the freshness check measures nothing.
- [ ] 🟠 `CC-F6` LLM budget reservation is check-then-insert.
- [ ] 🟡 `PH4-N1` (found while planning) 60 broker read calls bypass the gateway's MT5 lock (`CC-F2`, read half); route them through one locked read facade.
- [ ] 🟡 `CC-F11` Unguarded orchestrator state; `CC-F12` SQLite configuration inconsistencies.
- [ ] 🟡 `TS-P1` = prior F-RM-1 Lot rounding exceeds the 1 % cap (refuse at broker minimum lot).
- [ ] ⚪ `TS-P5` NaN prices pass `validate_order_params`.
- [ ] 🟡 `TL-T7` Writes to the frozen `TradeProposal` in size reduction.
- [ ] 🟡 `LA-A1` / `LA-A2` Live SMC and context fields read the forming bar.
- [ ] ⚪ `1a-F12` Smaller trade_manager defects (filling mode, failed-close flagging, per-position try, journal growth, initial-R cache).
- [ ] 🟠 `TL-T4` Live `reconciliation-run` always fails (`_get_mt5` undefined).
- [ ] 🟠 **Last:** `1a-F13` (data-quality fields set too late; server time treated as UTC) and `P1-H3`/`TL-T1` (`ConstraintVerdict.HOLD` missing). These two currently block all live trading.

## Phase 5: Honest numbers
- [ ] 🔴 `LA-B3` Ensemble weights use full-sample IC; 17 of 23 Alpha101 factors use full-series rank/scale.
- [ ] 🔴 `LA-B5` Hybrid tier 1 caps losers and keeps winners' overshoot; tier 2 ignores the candidate.
- [ ] 🟠 `LA-B1` / `LA-B2` Same-bar fill and exit; half-spread costing; `latency_bars=0` in the causal replay gate.
- [ ] 🟠 `LA-B6` FRED observation dates used as release dates; latest-vintage data.
- [ ] 🟡 `LA-B7` `vbt_engine` annualizes M15 as M1; no slippage; shorts ignored.
- [ ] 🟡 `LA-B8` CPCV and walk-forward don't do what they say; `LA-B9` triple-barrier labels overlap without uniqueness weights; ⚪ `LA-B10` label selection excludes non-decisive exits.
- [ ] 🟠 `P1-H5` DSR with 1 trial and ×√252 on per-pulse returns in the metrics engine. (The overfit gate itself deflates correctly.)
- [ ] 🟠 `P1-H5` GA alpha miner has no OOS split; `TL-T6` its walk-forward tests a momentum baseline labelled "discovered_alpha".
- [ ] 🟠 `P1-H5` `contextual_bandit` isn't a bandit.
- [ ] 🟠 `1b-R3` Continuous-backtest verdict ignores CPCV/PBO and fails open on DSR errors.
- [ ] 🟠 `1b-R6` Boot validation keeps only the last strategy's result; failures block nothing.
- [ ] 🟠 `1b-R5` Two writers race on `rl_model.json`; simulator Q-values registered as broker-trained.
- [ ] 🟡 `1b-R4` Every automated promotion path is dead; `1b-R9` divergence gate fails open and is hard-wired to EURUSD; `1b-R10` illegal lifecycle transitions.
- [ ] 🟡 `1b-R8` "OOS validation" never tests the models; WFE is a constant 0.833.
- [ ] 🟡 `1b-R11` Alpha context mislabelled; the registry is rewritten non-atomically.
- [ ] 🟠 `D2` = `P1-M9` Hide registry stats without evidence (deck: "103 engines active", "● live", Observatory charts); readers must honour `_EVIDENCE_INVALID`.
- [ ] 🟠 `EV` Purge the 150 synthetic fixture trades and the leaked backtest trade (ticket 1,003,479,400) from the production journal, keeping an archived copy.
- [ ] 🟠 `EV` Review the 51 `operator_antigravity` transitions and the 28 promotions straight to *_PROVEN; require human operator identity for approvals.
- [ ] 🟡 `D3` The Security screen says "clean" after scanning 0 files; `D4` statuses default to healthy without data.
- [ ] 🟡 `TL-T18` `max_open_positions` is ignored by the full-pipeline backtest.

## Phase 6: Demo soak (2+ weeks on MetaQuotes demo via the bridge)
- [ ] Daily journal ↔ broker reconciliation, with zero unexplained differences.
- [ ] Flatten and kill-switch drills with real demo positions (including during a cycle and after a restart).
- [ ] Restart drills inside and outside the 00:00-05:30 IST window (daily limit must persist).
- [ ] LLM outage drill (provider down → HOLD, no escalation).
- [ ] Review the evidence log: every proposal → decision → order → fill traced end to end.

## Phase 7: LLM hardening and reliability
- [ ] 🟠 `PI-P2` Data/instruction boundary for headlines and scraped text (delimited, labelled, truncated).
- [ ] 🟠 `PI-P3` Source allow-list keyed on domain, not publisher name.
- [ ] 🟡 `PI-P4` The injection filter is a 5-phrase regex on one path only.
- [ ] 🟡 `PI-P5` Strict response parser (no prose wrapping, no `Infinity`). `P1-M5`: missing confidence → 0, not 0.5.
- [ ] 🟡 `PI-P7` Injected text replays through the context buffer for about 5 cycles.
- [ ] 🟡 `PI-P8` Pair selection sends raw `hot_topics`. ⚪ `PI-P11` approved macro edges carry source text back into the prompt.
- [ ] 🟠 `EV` Provider reliability: 66 % of requests error, p90 latency 70 s (max 182 s) against a 180 s cycle. Add failover tuning, per-call deadlines and health-based routing.
- [ ] 🟡 `EV` 721 of 744 decisions are logged without symbol or timeframe.
- [ ] 🟡 `1b-R7` Kronos weights load without an integrity check; `1b-R12` Laya weights load without a completeness check and its MacroAnalyst veto is never enforced; `TL-S3` the Hugging Face download is unpinned.
- [ ] 🟡 `TL-T10` The `evaluate_portfolio_risk_gate` tool has the wrong signature; `TL-T13` NameErrors in graph_nodes.

## Phase 8: Architecture and code health
- [ ] 🟠 `P1-H7` Process split: execution kernel / research and training worker / news worker (removes the RAM boot bar and torch crash class).
- [ ] 🟠 `P1-H7` Decompose `main()` (4,362 lines) and `execute_cycle()` (2,039 lines) into `pipeline_v2` stages; move training out of boot.
- [ ] 🟡 Part 1 §4 Move cheap vetoes (kill switch, spread, session, stale tick) to the top of the cycle.
- [ ] 🟡 `P1-M2` Persistence layer: one journal DB, numbered migrations, no raw `sqlite3.connect` (154 sites).
- [ ] 🟡 `P1-M3` Typed settings for 117 env flags (81 undocumented), validated at boot.
- [ ] 🟡 `TL` Layering: no package boundaries (`core` → `intelligence` / `research`). Add `import-linter` contracts; `pipeline_v2` must not import `ui.gui_api`.
- [ ] 🟡 `P1-M4` Swallowed exceptions (1,581 broad handlers, ~340 `pass`).
- [ ] 🟠 `TL-T3` `--symbol AUTO` crashes at startup.
- [ ] 🟠 `TL-T5` Logger setup pasted inside module docstrings in 9 files (`secure_store` fallback crashes).
- [ ] 🟡 `TL-T9` Frozen `DecisionBundle` writes; `TL-T11` `np` UnboundLocal; `TL-T12` wrong constructor kwargs; `TL-T14` `run_cycle` signature; `TL-T15` `requests` undefined in EIA.
- [ ] ⚪ `TL-T16` `df` never assigned in main; `TL-T17` misc NameErrors; `TL-T19` unused `window_minutes`; `TL-T20` `/api/market` fallback TypeError; `TL-S4` Windows `netstat` shell call.
- [ ] ⚪ `1a-F17` Smaller hot-path items (ADV tracker, hard-coded correlation DB path, `data_drift_hold_bias` unread, event-time parsing, 100k default balance, PF 99.9, PerformanceMonitor column names, loss-count disagreement).
- [ ] ⚪ `P1-M6` Delete the `kill_switch_active()` trap method.
- [ ] 🟡 Static backlog: pyright 1,460 errors, ruff 174 undefined names, vulture 71 unused items (ratchet down in CI).

## Phase 9: UI/UX overhaul
- [ ] 🟡 Part 1 §7.1 One operator console (the deck); the web GUI becomes a read-only viewer or is retired; delete the 6 retired TUI stubs and the Tkinter/pywebview UIs.
- [ ] 🟡 Part 1 §7.2 Consolidate 40 views into about 12; command palette first; digits for sections only.
- [ ] 🟡 `D6` Document `Tab` (screens) vs `[ ]` (sections) in the status bar.
- [ ] 🟡 Part 1 §7.3 Typed confirmations for live start, flatten, kill switch, manual orders and promotion to FULL.
- [ ] 🟠 `D10` The direct-MT5 (MCP) mode is broken against `metatrader5-mcp` 1.0.0 (args must be wrapped in `params`); capture its stderr; pin the package.
- [ ] 🟡 `D5` Contradictory state across screens (header vs feed, Cockpit vs Positions balance, `L0_SHADOW` vs `L0_DISABLED`).
- [ ] 🟡 `D7` Remove design-law notes from operator text.
- [ ] ⚪ `D8` Epoch-0 timestamps shown as 05:30. `D9` Check text overlap in the Macro table and confirm dialog in a real terminal.
- [ ] 🟡 `P1-M10` `SettingsView` hard-codes `localhost:8000`; `P1-M11` HistoryView renders mock data.
- [ ] ⚪ Part 1 §7.4 Split the 618 kB web bundle; empty states when the engine is down.

## Phase 10: Repo and documentation
- [ ] 🟡 Part 1 §10 Consolidate 177 docs (5+ "source of truth" files) into ARCHITECTURE, OPERATOR_RUNBOOK, SECURITY and `adr/`; archive the rest.
- [ ] 🟡 Part 1 §10 Move about 29 MB of third-party transcripts/subtitles and PDFs out of the repo (licensing).
- [ ] ⚪ The README links to a Windows `file:///C:/` path and quotes stale test counts.
- [ ] ⚪ Commit per change from now on (history is 17 bulk commits).
- [ ] ⚪ `LA-A4` `zero_lag_macro_provider` hard-codes "nowcasts" (label as estimates).
