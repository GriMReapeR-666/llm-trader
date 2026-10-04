# Implementation Plan: Phase 3 (Emergency stack) and Phase 4 (Live-mode correctness)

Companion to [`ROADMAP.md`](ROADMAP.md) and [`PLAN_PHASE_1_2.md`](PLAN_PHASE_1_2.md). IDs in backticks refer to the roadmap and audit reports.
Line numbers are from `phase2/order-gateway` at `c47f4a9`. They will shift; the function names won't.
Estimated effort: **Phase 3 about 8 days, Phase 4 about 8 days** (focused work, including review). The roadmap said 4-5 and 5-7. Most of the difference is the import-root migration, which is about 1,100 import rewrites rather than 88, and the reconciler rework.

---

## 0. Ground rules

1. **Same rules as Phases 1-2:**
   - tests first;
   - known bugs as strict xfails tagged with their roadmap ID;
   - one PR per work package;
   - reuse before new code;
   - the full suite green and `graphify update .` on every PR.
2. **Start from `main` after PR #3 (Phase 2) merges.** Phase 3 depends on the gateway (`execute_flatten`, `flattening`, `cancel_pending`).
3. **Live entries stay blocked until PR 4.5.** The two accidental blocks (`1a-F13`, `TL-T1`) are removed in the last PR of Phase 4, after everything they would expose has a passing test.
4. **I place no orders on any account, demo included.** Drills run in paper and on the bridge's `FakeMT5`. The one demo session at the end of Phase 4 is yours to run (§4.6).

### 0.1 What the code looks like today (measured for this plan)
| Area | Fact | Where |
|---|---|---|
| Import root | **173** `sys.path` inserts in 101 production files, plus 81 in tests. **637** bare imports in production and 487 in tests, across 343 files and 190 modules. No module name exists in two packages, so a mechanical rewrite is safe. 25 modules load twice. | repo-wide; `scripts/ci/import_guard.py` |
| Controls | The API appends to `control_plane.jsonl`. The engine drains it once per *strategy* cycle and emits `CONTROL_EXECUTED` without doing anything. The API also emits `CONTROL_EXECUTED` itself, and takes `actor` from the request body. | `operations/autonomy.py:230-275`, `intelligence/llm_orchestrator.py:7450`, `ui/gui_api.py:1866-1877` |
| Deck | Shows "FLATTEN executed by operator" when `!live \|\| ok`. | `tui_opentui/src/components/CommandPalette.tsx:378-413` |
| News latch | **Writer:** news ingest. **Only reader:** the deliberative graph, which runs only in shadow mode, opt-in, with a paper profile. **The validator and the gateway** read only the manual store kill switch. | `data/realtime_news_ingest.py:478`; `intelligence/graph_nodes.py:442`, `mt5_trader.py:4708`; `core/trading_safety.py:1043`, `core/order_gateway.py:498` |
| Latch file | Shared `.tmp` name, no fsync, no inter-process lock; an unreadable file loads as **unlatched**. | `data/kill_switch_latch.py:89-106` |
| Signals | The handler calls `shutdown()`, which takes a non-reentrant lock. `SIG_DFL` isn't callable, so SIGTERM is swallowed. | `core/crash_recovery.py:121-147` |
| Engine singleton | The API checks `engine.pid` and then spawns. The engine writes `engine.pid` late and overwrites it unconditionally. | `ui/gui_api.py:776-790`, `mt5_trader.py:3739-3751` |
| Validator defaults | `clock_ok`, `session_allowed`, `liquidity_ok`, `microstructure_ok`, `required_data_ok` default True; `min_stop_distance` defaults to 0; `high_impact_news=None` counts as safe. | `core/trading_safety.py:1059-1092` |
| Fail-open gates | Data-quality gate, news gate, Ψ check on pending orders. | `llm_orchestrator.py:7591, 7718`; `data/market_data_provider.py:1072` |
| Stages | `run_stage` never reads `timeout_s`. `hold_only` is set and only reported. `PIPELINE_V2` is on by default. | `core/harness/pipeline_v2.py:157, 215, 893`; `mt5_trader.py:4685` |
| Live wiring | PaperBroker and paper risk providers are installed in every mode. | `llm_orchestrator.py:812-818, 863-865` |
| Reconcile | Branches on `self.paper_broker`, not mode. Fabricates 0-P&L closes. `record_close` is unconditional. Called from 3 sites plus the scheduler. | `llm_orchestrator.py:5172-5260, 6406, 7152, 7295`; `core/trading_safety.py:604` |
| Risk day | `__init__` uses UTC; `reset_daily_limits` and `set_store` use local dates. | `core/risk_manager.py:97-118, 219` |
| Risk mutation | `risk_per_trade` is written back each cycle in 3 places. | `llm_orchestrator.py:4676, 4707, 4824` |
| Spread guard | Baseline derived from the current spread; `_symbol_tick` undefined in the provider. | `core/order_execution.py:781`; `data/market_data_provider.py:995` |
| Paper SL | A modify hits every position with the same symbol and magic. | `core/order_execution.py:161`, `core/order_gateway.py:395` |
| Blocks | `ConstraintVerdict.HOLD` doesn't exist; `_get_mt5` is undefined in the CLI. | `core/order_execution.py:804`; `mt5_trader.py:2748` |
| Broker reads | **60** direct MT5 read calls (positions, orders, history, account, tick) outside the gateway's lock. | core, data, intelligence, operations, `mt5_trader.py` |

### 0.2 New findings while planning
| ID | Sev | Finding | Where | Fixed in |
|---|---|---|---|---|
| `PH3-N1` | 🟠 | **A missing VIX clears the news latch.** `snapshot.get("vix", features.get("vix", 18.0))` passes 18 ("calm") to `apply_market_data`. That breaks the module's own rule that missing data never clears. | `intelligence/graph_nodes.py:444` | PR 3.2 |
| `PH3-N2` | 🟠 | **Nothing can clear the news latch in live.** The only caller of `apply_market_data` is the shadow graph. Enforcing the latch as it is would halt live entries permanently after the first trip. | `graph_nodes.py:442`, `mt5_trader.py:4708` | PR 3.2 |
| `PH3-N3` | 🟠 | The API emits `CONTROL_EXECUTED` when a control is merely queued, and takes `actor` from the request body. Phase 2 fixed `actor` for `/approve` only. | `ui/gui_api.py:1872-1877` | PR 3.3 |
| `PH4-N1` | 🟡 | 60 broker read calls bypass the gateway's MT5 lock, so `last_error` attribution is still racy (`CC-F2`, read half). The Phase 1/2 plan deferred this to Phase 4, but it wasn't on the roadmap. | see §0.1 | PR 4.3 |
| `PH4-N2` | ⚪ | `core/order_gateway.py` is 1,043 lines against a 300-line target. | | Phase 8 |

---

## Phase 3: Emergency stack that works

**Goal:** when the operator, the news tripwire or the guardian says stop, the system stops. That has to hold across processes and restarts, and the operator sees what actually happened.

**Exit gate:**
- The import guard reports **0** duplicates, enforced as a test.
- The paper drill (§3.8) passes in CI: flatten, kill switch, news latch, SIGTERM, second instance.
- Every Phase 3 strict xfail is converted:
  - validator fail-open rows ×8;
  - `TS-P2` ×3 plus the property test;
  - daily breaker ×3;
  - `TS-P6` ×2;
  - `P1-C1` ×1;
  - `TS-P3` property.
- The deck never shows "executed". Every control shows `REQUESTED → ACKNOWLEDGED → APPLIED | FAILED | EXPIRED`, with counts.
- Mutation re-measure on `trading_safety` and `risk_manager`; raise the ratchet if the score went up (`TS-9`).

### PR 3.1: One import root (1.5-2 days) · `P1-C1` = `P1-M1`, `CC-F13`
| Change | Where | Detail |
|---|---|---|
| `pyproject.toml` | repo root | Minimal setuptools metadata; the packages are the existing folders with `__init__.py` (`core`, `data`, `intelligence`, `operations`, `research`, `ui`, …). No new runtime dependency. `pip install -e .` is optional: `python mt5_trader.py` from the repo root keeps working, because the script folder is `sys.path[0]`. |
| Codemod | `scripts/codemods/package_imports.py` (stdlib `ast` plus line rewrite; kept so other branches can re-run it) | `import trading_safety` → `from core import trading_safety`; `from trading_safety import X` → `from core.trading_safety import X`; aliases preserved. Also rewrites string targets: 3 `mock.patch`/`monkeypatch` strings and any `importlib.import_module("x")`. |
| Remove the inserts | 173 production + 81 test sites, including `mt5_trader.py:49-55, 5115, 5230` | **Rule:** only the repo root may be added to `sys.path`, and only by scripts run by path (prefer `python -m`). Subpackage folders never. |
| pytest | `pytest.ini`, `tests/conftest.py` | `pythonpath = .`; conftest drops its subpackage inserts. CI keeps `PYTHONPATH=compat/mt5_bridge`: the MetaTrader5 shim is a separate top-level package on purpose. |
| Guard | CI + `tests/safety/test_single_import_root.py` | Import guard `--max 0`. The test imports `mt5_trader` and `ui.gui_api` in a subprocess and asserts 0 duplicates. An AST check fails on any production `sys.path.insert` of a subpackage folder. |
| `CC-F13` | `operations/autonomy.py:283`, `operations/supervisor.py:257` | Lazy singletons use double-checked locking (the `crash_recovery.get_recovery` pattern). |

**Tests:**
- Unxfail `tests/safety/test_latch_persistence.py:99` (`P1-C1`).
- Full suite.
- Import guard at 0.
- `graphify update .` (expect many new real import edges).
- Mac smoke: `mt5_trader.py --help`, API start, deck connects.

**No behaviour change is intended.** Land this first and on its own: it touches about 350 files and will conflict with any parallel branch.

### PR 3.2: News latch that is durable, precise, clearable and enforced (1.5 days) · `EV`, `PI-P9`, `CC-F5`, `TS-P6`, `PH3-N1`, `PH3-N2`
**Order inside the PR matters.** Precision and clearing must exist before enforcement. Otherwise the latch that has been set since 24 Sep on the Windows machine blocks entries forever.

| Step | Change | Where |
|---|---|---|
| 1 Durable | Keep latch state in `trading_state.db` through `TradingStore.update_state` (`BEGIN IMMEDIATE`), like the autonomy state.<br>• Import the existing JSON once at first start.<br>• Every read goes to the store, so the API, engine and news worker agree.<br>• An unreadable or invalid record loads as **latched** (`trigger_source="checkpoint_unreadable"`); no record means unlatched (first boot). | `data/kill_switch_latch.py:89-106` |
| 2 Missing VIX | Pass `vix=None` when no VIX is present; missing data never clears (`PH3-N1`). | `intelligence/graph_nodes.py:444` |
| 3 Precision | Port the guard already measured in `research/d9_false_positive_probe.py:80-93` into `_deterministic_d9`. It suppresses negation, retrospective, forecast/debate, single-name and reversal headlines. On the project's own corpus it **suppresses 31 of 33 benign trips and loses 0 of 13 real ones**. Also close the 2 recall holes listed in the report: passive "peg abandoned", and "halts nickel trading". | `data/news_triage.py:85-95, 651-665` |
| 4 Sources | Only articles whose URL passes `assess_source(url)` (PRIMARY tier or an allow-listed wire) count toward the latch. The check is keyed on domain. The full allow-list rework (`PI-P3`) stays in Phase 7. | `data/realtime_news_ingest.py:466-480` |
| 5 Clearing (`PH3-N2`) | **(a) Operator clear:** `POST /api/latch/clear` with a reason and a typed confirmation; the actor comes from the session; journalled. Same in the CLI: `mt5_trader.py latch --clear --reason …`.<br>**(b) Market-data clear on the main path:** each engine pulse applies a *fresh* VIX when the macro feed has one, using today's rule (VIX < 35, no halt flag, 15-minute re-arm cooldown). | new route; engine pulse |
| 6 Enforce | New `core/kill_switch.py`: `entries_blocked(store) -> (bool, reason)` is true when the manual kill switch is on **or** the latch is set; a read error counts as blocked.<br>• Called by `DeterministicValidator.validate` (replacing `trading_safety.py:1043`) and by the gateway's entry preconditions (`order_gateway.py:498`).<br>• Exits, modifies and cancels stay allowed. | |

**Tests:**
- **Corpus regression:** `tests/fixtures/d9_corpus.json`, built from `d9_false_positive_report.json`. 13 must trip; of the 33 benign false trips, only the 2 measured residuals may still trip; the 14 benign passes keep passing.
- **Two-process stress test** (port of audit probe P5): zero torn reads.
- **Corrupt record loads as latched:** unxfail `TS-P6` ×2.
- **Missing VIX** doesn't clear.
- **Validator** rejects with "news kill-switch latched".
- **Gateway** refuses entries and allows exits.

⚠ **Windows:** after the pull, the existing latch blocks entries until you review and clear it (step 5a). That is intended.

### PR 3.3: Controls through the journal, real flatten and kill switch, honest outcomes (2 days) · `P1-C4`, `CC-F7`, `D1`, `PH3-N3`, `CC-F10` re-sweep

```
deck / API                        trading_state.db                   engine main loop (every ≤ 1 s)
POST /api/control/flatten ──▶ INSERT operator_controls ──────▶ UPDATE … SET status='ACKNOWLEDGED'
     returns control_id          status='REQUESTED'                WHERE id=? AND status='REQUESTED'   (compare-and-set)
                                                                   kill switch ON, gateway.flattening = True
                                                                   execute_flatten(owned magics) + cancel owned pending
                                                                   re-sweep until 2 empty snapshots (max 5)
GET /api/control/{id} ◀──────── APPLIED {closed, cancelled, failed, remaining}  |  FAILED {error}  |  EXPIRED
```

**Table:** `operator_controls` (id, action, actor, source, issued_at, status, acked_at, applied_at, result_json, idempotency_key UNIQUE). Status has a CHECK constraint.

| Action | Applied by | APPLIED means |
|---|---|---|
| pause / resume | API, at once, in the store | The engine re-reads paused/halted from the store on every check. Fixes the second half of `CC-F7`, where the last writer wins between processes. |
| killswitch | API sets the store flag at once. That gives **ACKNOWLEDGED**: entries are blocked everywhere. The engine then cancels owned pending entry orders, which gives **APPLIED** with a count. | Entries blocked and pending entries cancelled. Open positions keep their SL/TP and management. |
| killswitch off | API, with a typed confirmation | |
| flatten | engine only | Every owned position closed and every owned pending order cancelled. Implies kill switch ON. |

- **Draining:** moves from the per-strategy `execute_cycle` (`llm_orchestrator.py:7450`) to the main loop. It runs once per pulse and also inside the sleep loop next to `_stop_file_requested()`, so a flatten lands within about 1 s.
- **Expiry:** a REQUESTED control with no engine acknowledgement after 60 s becomes **EXPIRED**. The deck then says "engine offline: not applied" and shows the offline flatten command.
- **Offline flatten:** `mt5_trader.py flatten --owned --confirm <account login>`. It takes the engine lock (PR 3.4) and runs the same function, so it can't race a running engine.
- **Autonomy gate:** taken out of the telemetry `try` (`llm_orchestrator.py:7437-7476`). It fails closed: an exception means HOLD with a reason. Same in `pipeline_v2`.
- **API:** stops emitting `CONTROL_EXECUTED` itself (`gui_api.py:1877`). The actor comes from the authenticated session (`PH3-N3`).
- **`control_plane.jsonl` is retired.** Unconsumed lines are imported once as EXPIRED and never replayed: a stale flatten must not fire days later.
- **Deck:**
  - `sendControl` returns the `control_id`, and `applyIntervention` polls `GET /api/control/{id}`.
  - It shows `REQUESTED → ACKNOWLEDGED → APPLIED (3 closed, 1 cancelled)`, or `FAILED (reason)`, or `EXPIRED`.
  - When the deck isn't connected it shows `NOT SENT: deck offline`, and the `!live || ok` branch goes.
  - Flatten and kill switch require typing FLATTEN or KILL; CANCEL stays the default.

**Tests:**
- Port of probe P9: 2,000 controls from a second process are drained exactly once each.
- Compare-and-set: two drainers produce one acknowledgement.
- Paper flatten: 3 positions and 1 pending order give `closed 3, cancelled 1`.
- Flatten while a strategy thread submits: the entry is refused by `flattening`, and the re-sweep closes anything that slipped through.
- Autonomy-gate exception → HOLD.
- Deck: `bun test` on outcome rendering.
- API: actor comes from the session; no `CONTROL_EXECUTED` on issue.

### PR 3.4: Process lifecycle (1 day) · `CC-F4`, `CC-F9`, `CC-F14`
| Change | Where | Detail |
|---|---|---|
| Flag-only signal handlers | `core/crash_recovery.py:121-147` | The handler sets `_shutdown_event` and returns. A second SIGINT raises `KeyboardInterrupt`. The main loop checks `shutdown_requested()` wherever it checks `_stop_file_requested()`: the top of the loop and both sleep loops (`mt5_trader.py:5615, 5623`). |
| Engine lock | `mt5_trader.py:3739` | First action of `llm-trade`:<br>• Open `engine.lock` with a non-blocking `fcntl.flock` / `msvcrt.locking`. Extract the helper from `core/trial_ledger.py:714-751` into `core/file_lock.py` and reuse it.<br>• Write the pid and keep the fd open for the life of the process.<br>• Exit code 3 if the lock is held.<br>`engine.pid` stays, for display only. |
| API start and stop | `ui/gui_api.py:776-790, 1837-1860` | **Start:** try the same lock; return 409 if it's held.<br>**Stop:** write the stop flag, wait up to 15 s, then terminate the pid recorded in the lock file. This works on Windows too, where `TerminateProcess` runs no handler. Never delete a pidfile the API didn't create. |
| Shutdown order | `mt5_trader.py:5632-5690` | Stop new cycles → join `sched-bounded` (10 s bound) → velocity shutdown → orchestrator shutdown → **guardian stop last** → CrashRecovery snapshot. |

**Tests** (POSIX subprocess):
- SIGTERM while the checkpoint lock is held → exits within 5 s with rc 0 (probe P6).
- SIGTERM while idle → exits (probe P7).
- A second engine → rc 3.
- API start while the lock is held → 409.

### PR 3.5: Fail closed on missing evidence (1-1.5 days) · `P1-H4`, `TS-P3`, `TS-P2`, `PH1-N1`, `PH1-N2`, `1a-F15`
| Change | Where | Detail |
|---|---|---|
| `MarketEvidence` | `core/trading_safety.py` (new frozen dataclass beside `TradeProposal`) | Every gate input is a required field with no default. `MarketEvidence.parse(market)` returns the evidence or a list of problems; a missing key, `None` or a non-finite number is a problem. `validate()` parses first and rejects with "missing evidence: <field>". Callers keep passing a dict (smallest diff); a test asserts that the producer `_validate_proposal` supplies every field. |
| `PH1-N1` | `trading_safety.py:1086, 1092` | `high_impact_news=None` means unknown, which counts as unsafe. |
| `TS-P3` | `trading_safety.py:1155-1166` | A non-finite changepoint probability is rejected. |
| `TS-P2` | `risk_manager.get_drawdown_multiplier` (`:447`) | Non-finite, zero or negative equity gives 0.0. |
| `PH1-N2` | `risk_manager.check_daily_drawdown` (`:273`) | A baseline that is zero, negative or non-finite, or equity that is non-finite: return False (blocks entries) and raise an alert. Bad data never triggers a protective flatten. |
| `1a-F15` | `llm_orchestrator.py:7591` (data-quality gate), `:7718` (news gate), `market_data_provider.py:1072` (Ψ check), the `_corr_engine` NameError | An exception means HOLD: release the candle task (don't complete it) and log at WARNING. The data-quality gate's fingerprint dict gets a lock. |

**Tests:**
- Unxfail: validator fail-open rows (8), `TS-P2` (3 plus the property test), daily breaker (3), `TS-P3` property.
- One test per fail-open site: make the gate raise, expect HOLD and a released task.

**Expect paper to reject more where a producer drops a key.** That is the point. The golden replay (`tests/test_pipeline_v2.py`) and the characterization tests name the producer, and the fix goes there.

### PR 3.6: Stage deadlines and hold-only (0.5-1 day) · `P1-H2`
| Change | Detail |
|---|---|
| Deadline | `run_stage` (`pipeline_v2.py:157`) runs `spec.fn` under `run_stage_with_deadline` (`intelligence/boot_fetch_watchdog.py:71`) for stages marked `abandonable=True`: `velocity.poll`, `memory.dispatch`, `analysis.context/data/causal/llm`. A timeout emits `STAGE_TIMEOUT` and applies the stage's fallback. The abandoned thread's result is discarded; the LLM stage releases its budget reservation. |
| Never abandoned | `reconcile.*`, `position.tick`, `analysis.validate/size/execute`: they hold the broker lock or write the journal. Their bound comes from the gateway (a send timeout → UNRESOLVED) and the broker-lock timeout. Overruns are reported as SLA misses. |
| LLM fallback | `analysis.llm` changes from `continue` to `degrade`: a timed-out LLM means HOLD. |
| `hold_only` | `_stage_cycle_execute` returns HOLD when `ctx.flags["hold_only"]` or the autonomy circuit's `hold_only` flag is set. The legacy body checks the same flag before calling the gateway. |
| Leak bound | At most 2 abandoned threads per stage key. A third timeout trips the autonomy circuit (pause) instead of starting another thread. |

**Tests:**
- A stage that sleeps past its deadline returns within the timeout plus 0.5 s.
- `degrade` → no order is submitted.
- `hold_only` → HOLD event.
- A non-abandonable stage is never abandoned.

### PR 3.7: Safety strip in the deck (0.5-1 day) · Part 1 §7.2
- **API:** `GET /api/safety`, a single authenticated call. Each field carries `as_of`; anything unknown is `null`. Fields:
  - journal mode;
  - autonomy level, paused/halted;
  - manual kill switch;
  - news latch (latched, since, source, headlines);
  - daily P&L against the limit, and breaker state;
  - last tick age per symbol;
  - MT5 bridge status;
  - LLM provider circuit;
  - engine lock and heartbeat;
  - open control requests.
- **Deck:** `SafetyStrip.tsx`, mounted in `layout.tsx` on every screen (reuse `LiveModeBanner` colours). A `null` value, or one older than 10 s, renders as **UNKNOWN** in amber. Nothing is green by default; that is also the rule for `D4`.
- **Tests:**
  - API field test, including all-null when the engine is down;
  - deck selftest snapshot;
  - `tsc`.

### 3.8 Phase 3 drill (the gate) (0.5 day)
`tests/drills/test_emergency_drill.py`, marked `drill`, runs in CI on paper and on the bridge's `FakeMT5`:
1. Run the engine loop in paper with 3 open positions and 1 pending order.
2. Flatten through the API → `APPLIED {closed: 3, cancelled: 1}` within 5 s; the journal and the paper book agree.
3. Kill switch → the next validated proposal is refused by the gateway with "kill switch"; an exit still works.
4. Two confirmed tripwire batches from allow-listed sources set the latch, and the validator rejects. The operator clears it, and entries are allowed again.
5. Restart with a corrupt latch record → latched.
6. SIGTERM mid-cycle → clean exit within 5 s. A second engine is refused (rc 3).

Optional, 30 minutes on the Mac: the same drill on the paper engine with the deck attached, to see the outcome states.

---

## Phase 4: Live-mode correctness

**Goal:** in LIVE and DEMO, the engine sees, sizes, manages and reconciles real positions correctly. Only then are the two accidental blocks removed.

**Exit gate:**
- A full position lifecycle passes on `FakeMT5` in CI:
  - pending entry → fill → reconcile;
  - breakeven → trail → partial close → close;
  - reconcile → exactly one risk outcome.
- The audit's probes are passing tests: 01a probe1 and probe2, concurrency P4.
- All Phase 4 xfails are converted:
  - `P1-H1`;
  - `TS-P1`;
  - `TS-P5` ×2 plus the 3 geometry rows;
  - the `TL-T1` characterization test.
- **You run one demo session (§4.6).** Its reconciliation report shows zero unexplained differences.
- Mutation re-measure, including `calc_position_size`; raise the ratchet (`TS-9`).

### PR 4.1: Mode-correct wiring and one reconciler (2 days) · `1a-F1`, `1a-F2`, `TL-T8`, `CC-F3` = `1a-F7`, `TL-T4`
| Change | Where | Detail |
|---|---|---|
| Pass the journal mode in | `LLMOrchestrator.__init__` (`:523`), `:812-818`, `wire_trade_manager` (`:852-865`) | Create PaperBroker and the paper providers only when the mode is PAPER. A boot assertion in `mt5_trader` for LIVE/DEMO: `trade_manager.paper_broker is None` and the risk providers are the live ones. |
| Live providers | `risk_manager.check_active_trade_limit` (`:695`), `open_portfolio_heat_r` (`:775`) | **Position count:** owned broker positions (magic allowlist) plus reserving intents.<br>**Heat:** owned positions plus owned pending orders (`CC-F1` item 2). |
| Reconcile by mode | `llm_orchestrator.py:5172-5260` | • Branch on mode.<br>• Convert durable rows to dicts (`TL-T8`).<br>• No broker history → keep the trade open and count misses; `flag_execution_review` after 3.<br>• Guard `closing_deals`.<br>• Never fabricate a 0-P&L close. |
| Close once | `TradingStore.record_close` (`core/trading_safety.py:604`) | `UPDATE … WHERE ticket=? AND closed_at IS NULL`, returning a bool. The side effects (`perf.close_trade`, `risk.record_trade_outcome`, lifecycle, labels, reflection) run only when it returns True. |
| One reconciler | `pipeline_v2` maintenance stages; legacy `:6406, 7152, 7295`; scheduled `position_reconcile` | Reconcile once per pulse on the main thread, before the strategy pool fans out. The scheduled handler takes the same `_reconcile_lock` without blocking, and skips if it's held. |
| `TL-T4` | `mt5_trader.py:2748` | Import `_get_mt5`; test the live daily reconciliation on `FakeMT5`. |

**Tests:**
- An orchestrator in LIVE mode has no PaperBroker (probe1).
- A live reconcile leaves an open broker position open (probe2).
- 4 threads reconciling one closed ticket produce one close and one outcome (P4).
- Missing history keeps the trade open.

### PR 4.2: Broker time and the trading day (1.5 days) · `P1-H1`, `1a-F10`, `1a-F14`, `LA-A3` (part)
| Change | Detail |
|---|---|
| `core/broker_time.py` | Move `_server_utc_offset_seconds` here from `market_data_provider.py:131-184`. Add `broker_epoch_to_utc(epoch)` and `broker_trading_day(now_utc)`. The day boundary is server midnight, as on MT5 daily bars. One cache per process (there is only one copy after PR 3.1). |
| Risk day | Every date comparison in `risk_manager.py:97-118, 219` uses `broker_trading_day()`. The state stores `trading_day` and `day_basis: "broker"`. Existing `risk_state.json` dates are converted once. A restart on the same broker day keeps the breaker. |
| Every MT5 epoch | The trade manager's time stop and duration (`1a-F10`), reconcile close times, the gateway sweep's history window, and the position context shown to the LLM. |
| `captured_at` | Stamped at the quote read. The DOM's own `captured_at` feeds the freshness check (`1a-F14`). |

**Tests:**
- Unxfail `P1-H1`.
- Freeze the clock at 02:00 IST with UTC+2 and UTC+3 servers.
- The time stop fires at 4 h of server-corrected time.
- A close at 22:00 UTC lands on the right trading day.

### PR 4.3: Sizing and portfolio limits (2 days) · `1a-F3`, `1a-F16`, `TS-P1`, `TL-T7`, `D3b`, `CC-F6`, `PH4-N1`, `CC-F11`, `CC-F12`
| Change | Where | Detail |
|---|---|---|
| Fixed base risk | `llm_orchestrator.py:4676, 4707, 4824`; `risk_manager` | Set `base_risk_per_trade` once from config. Each cycle computes `effective = base × product of this cycle's multipliers`, clamps it and passes it per proposal, never writing it back. Increases (multiplier > 1) need research-influence approval in every mode; decreases don't. |
| Heat budget | `market_data_provider.py:1583`, `llm_orchestrator.py:3062` | Computed per proposal from the base budget and the current copula regime. No sticky writes (`1a-F16`). |
| Lot floor | `risk_manager.calc_position_size` (`:861`) | If the broker's minimum lot risks more than the cap, the size is 0 with a reason (`TS-P1`). Never round up past the cap. |
| Frozen writes | `llm_orchestrator.py:2569, 2693, 2709, 2887` | Size reductions return a scale on the sizing result, applied once at sizing (`TL-T7`). |
| `D3b` | Gateway reservation; `correlation_risk.correlated_open_risk`; `trading_safety.py:1412` netting | Both limits move inside the reservation transaction and count reserving intents. Symbol, side and risk are already on the intent payload. |
| LLM budget | `agent_operations.reserve_llm` (`:1129`) | `BEGIN IMMEDIATE`, plus a partial UNIQUE index on `trigger_key` (`CC-F6`). |
| Broker reads (`PH4-N1`) | 60 call sites | `core/broker_reads.py`: positions, orders, history, account, tick and symbol_info under the gateway's module lock. Each call returns `(result, last_error)` together. An AST ratchet test like the single-door test starts at today's count and goes to 0. |
| Small concurrency fixes | `CC-F11`, `CC-F12` | • `dual_velocity.poll` pops jobs under its lock.<br>• `rl_state_history` updates under `_cycle_state_lock`.<br>• Lazy inits are locked.<br>• `busy_timeout` is 10 s everywhere.<br>• `trading_memory` futures get a timeout. |

**Tests:**
- 10 cycles at ×1.2: effective risk stays at 1.2 % (probe3).
- A crisis reading then normal: the heat budget goes back to 4.0.
- Minimum lot over the cap → 0 lots (unxfail `TS-P1`).
- Port of P1 with correlated symbols on 4 threads: never over the 2 % correlation cap.
- Port of P3: `max_concurrent` is respected.

### PR 4.4: Position management (1.5 days) · `1a-F18`, `1a-F9`, `1a-F11`, `1a-F12`, `1a-F5` = `TL-T2`, `TS-P5`, `LA-A1`/`LA-A2`
| Change | Where | Detail |
|---|---|---|
| One engine | `llm_orchestrator.py:599-602, 4083-4100` | TradeManager executes; PositionManager stops issuing actions. Its partial close is dead and its trail fights TradeManager's. |
| Initial risk | `trade_manager._initial_risk_for` (`:429`), `_check_time_stop` (`:479`), `_check_pyramid_entry` (`:658`) | Read the open price, initial SL and risk amount from the trades row written at fill, never from the first SL seen.<br>• The pyramid and time-stop checks use it (`1a-F9`).<br>• Pyramid children are tagged and can't pyramid.<br>• An add's risk is (entry − SL) × volume × tick value.<br>• Pyramiding stays off (`PYRAMIDING_ENABLED=0`). |
| Paper SL | `order_execution.py:161`, `order_gateway.py:395` | Modifies are scoped to the ticket. Paper goes through the gateway's only-tighten and protective-side checks. Breakeven and trail are computed once and the tighter one wins (`1a-F11`). |
| `1a-F12` | `trade_manager` | • `type_filling` on every close.<br>• `closed_any` only on success.<br>• A `try` per position.<br>• `UNOWNED_POSITION_OBSERVED` once per ticket per session.<br>• History is trimmed. |
| Spread guard | `order_execution.py:781`, `market_data_provider.py:995` | The baseline is a rolling per-symbol spread average from `MicrostructureObserver`. No tick on live → reject. Units are points (`1a-F5`, `TL-T2`). |
| Geometry | `trading_safety.validate_order_params` | Reject non-finite prices, an unknown side and a missing SL. That converts `TS-P5` and the `PLAN-1.3` / `F-TS-6` rows. The gateway already refuses these orders; this fixes the function itself. |
| Forming bar | `market_data_provider.py:1159-1167, 1332`, SMC block | Trend, multi-timeframe and SMC read the last *completed* bar (`LA-A1`/`LA-A2`). |

**Tests:**
- The full ladder on `FakeMT5` (breakeven at +1R, trail at 1.5R, scale-out, time stop), using the initial R after a restart.
- A paper modify of ticket A leaves ticket B untouched.
- A spread at 3× the average → rejected.
- Unxfail the geometry rows.

### PR 4.5: Remove the two accidental blocks (0.5-1 day) · `1a-F13`, `TL-T1` · **last**
| Change | Detail |
|---|---|
| `1a-F13` | `fetch_data` collects the tick and the clock observation, with the server offset subtracted, and sets `clock_health` and `provider_time`. The pre-analysis data-quality gate and the validator then read broker time correctly. |
| `TL-T1` | `order_execution.py:804` checks for `ConstraintVerdict.REJECT` only, because the enum has no HOLD. The constraint check then actually runs; a test proves that a REJECT verdict blocks. |
| Characterization | The `test_order_path_characterization.py:52` xfail converts: a live pending entry with a token reaches `order_send` on `FakeMT5` with the expected request. |
| Real-money switch | LIVE needs `LLM_TRADER_LIVE_ENTRIES=1` in addition to `--live`. DEMO works with the verified-demo token from Phase 2. |

**This PR merges only after 4.1-4.4.** Its description lists the `1a-F1`, `F2`, `F3`, `F5` and `F8` tests that must already be green.

### 4.6 Phase 4 demo session (the gate): you run it
Run this on the Mac through the bridge, on MetaQuotes-Demo, with the minimum lot, one symbol and one strategy.
1. **You** turn "Algo Trading" ON in the terminal and start `python mt5_trader.py llm-trade --demo …`. I don't start it or place orders.
2. Let it run until one position completes its lifecycle.
3. I read the journal (intents, execution reports, trades), the broker history through the bridge, and the logs, all **read-only**. Then I write the reconciliation report: every proposal → intent → order → fill → management → close traced, with zero unexplained differences.
4. You drill flatten and the kill switch once with the demo position open.
5. You turn Algo Trading back OFF.

This is also the dress rehearsal for Phase 6.

---

## Schedule

| Day | Work |
|---|---|
| 0 | Merge PR #3 (Phase 2); branch from `main` |
| 1-2 | PR 3.1 (import root) |
| 3 | PR 3.2 (news latch) |
| 4-5 | PR 3.3 (controls, flatten, kill switch, deck outcomes) |
| 6 | PR 3.4 (lifecycle) + PR 3.5 (fail closed) |
| 7 | PR 3.6 (deadlines) + PR 3.7 (safety strip) |
| 8 | Drill + mutation re-measure → **Phase 3 gate** |
| 9-10 | PR 4.1 (mode wiring, reconciler) |
| 11 | PR 4.2 (broker time) |
| 12-13 | PR 4.3 (sizing, limits, broker reads) |
| 14 | PR 4.4 (position management) |
| 15 | PR 4.5 (unblock) + mutation re-measure |
| 16 | Demo session (you) + reconciliation report → **Phase 4 gate** |

## Risks and how they're handled
| Risk | Mitigation |
|---|---|
| PR 3.1 touches about 350 files and conflicts with parallel work | Land it first and alone, with no behaviour change. The codemod script can be re-run on other branches. |
| Enforcing the latch halts entries on the Windows machine, which has been latched since 24 Sep | Precision and the operator clear land before enforcement, in the same PR. The PR description says to review and clear. |
| `MarketEvidence` makes paper reject more | Intended. The golden replay names the producer that drops a field. |
| Abandoned threads after a deadline | Only read-only stages can be abandoned, at most 2 threads per stage; after that the circuit pauses. |
| Removing the blocks exposes something not yet fixed | PR 4.5 is last, every exposed finding has a passing test first, and real-money LIVE needs an extra switch. |
| Flatten closes your manual positions | Operator flatten is owned-only. The guardian's RED trip still flattens everything, as it does today (decision 3). |
| CI minutes | The drill runs on paper and `FakeMT5` in seconds; no new nightly job. |

## Decisions I need from you (recommended defaults in bold)
1. Where the news latch lives: **in `trading_state.db`**, or keep the JSON file with an OS lock.
2. What the kill switch does: **block new entries and cancel pending entries; open positions keep their SL/TP and management**, or also flatten.
3. Flatten scope:
   - the operator flatten closes **owned positions only**;
   - the guardian's RED trip **keeps flattening everything**, as it does today.
4. Flatten when the engine is down: **a CLI command that takes the engine lock**, or the API does it.
5. After PR 3.2, the Windows latch (set since 24 Sep) blocks entries until you **review and clear it**: OK?
6. Position management: **TradeManager only**; PositionManager stops executing.
7. Pyramiding: **stays off** after Phase 4; revisit after the demo soak.
8. Trading day: **the broker server day**, or the UTC day.
9. Real-money LIVE after Phase 4: **needs an extra env switch**, or `--live` alone.
10. The Phase 4 demo session: **you** turn Algo Trading on and start it; I only read.
