# 06: Concurrency and shared-state review

Date: 2026-10-04. Scope: production Python (excludes `tests/`, `vendor/`, `.venv/`, `quant_knowledge_framework/`, `graphify-out/`, front-ends). No tracked file was modified. Probes live in `/private/tmp/conc_probe/` and use temp DBs only. They make no MT5, LLM or network calls.

Severity: 🔴 can breach a money or safety limit · 🟠 safety or availability defect · 🟡 correctness or telemetry defect · ⚪ hygiene.

## 0. Probe results (empirical)

| Probe | Script | Result |
|---|---|---|
| P1: concurrent arbiter permits (4 strategy threads, 10k equity, 3% open-risk cap = 300, each trade risks 100) | `probe_races.py arbiter` | **20/20 trials over cap**: all 4 admitted, open risk 400 |
| P2: sequential permits with orders still pending (the production record point is the fill) | `probe_races.py seq` | **6/6 admitted** against a cap that allows 3 |
| P3: `AgentOperations.reserve_llm(max_concurrent=1)` from 8 threads | `probe_races.py llm` | **4/10 trials granted more than 1**; worst case granted 3 |
| P4: two strategy threads reconcile the same closed winning ticket | `probe_races.py streak` | 200 closes counted as **800 wins**. Streak multiplier reached the **1.8× cap** |
| P5: the two `KillSwitchLatch` copies (C1) save to one file through a shared `.tmp` | `probe_races.py latch` | 17,730 reads: **3,904 torn or empty JSON**, 5,157 unlatched. A fresh instance after the run reports `latched=False` |
| P6: SIGTERM arrives while the main thread holds `GraphStatePersistence._lock` | `probe_signal.py` (deadlock) | **Process hangs.** The stack shows `begin_checkpoint`:264 called from `shutdown`:286, called from `_sigterm_handler`:130, called from inside `begin_checkpoint`:266 |
| P7: SIGTERM with no lock held | `probe_signal.py` (swallow) | **Process keeps running** (rc=0). The handler swallows SIGTERM |
| P8: double-loaded modules after importing `mt5_trader` and `ui.gui_api` | `dupe_locks.py` | 25 files are loaded twice. Module-level primitives that are duplicated: `kill_switch_latch._LATCH_LOCK` + `_LATCH`, and `llm_router._router` |

Re-run the probes with:

```
cd /private/tmp/conc_probe
"/Users/dankmagician/Documents/New project/llm-trader/.venv/bin/python" probe_races.py      # P1-P5
"/Users/dankmagician/Documents/New project/llm-trader/.venv/bin/python" probe_signal.py     # P6-P7
"/Users/dankmagician/Documents/New project/llm-trader/.venv/bin/python" probe_controls.py   # P9
"/Users/dankmagician/Documents/New project/llm-trader/.venv/bin/python" dupe_locks.py mt5_trader ui.gui_api   # P8
```

P9 (`probe_controls.py`) runs two `AutonomyManager` instances on one control file, standing in for the API process and the engine process. Result: of **2,000 issued `killswitch` controls, 1,826 were never drained**. The lines were appended to an inode that `drain_controls` had already replaced.

P4 caveat: the probe reproduces the interleaving of `reconcile_open_positions` (read the open set, call the broker, close, record the outcome) on top of the real `RiskManager.record_trade_outcome`. It does not drive the whole orchestrator.

## 1. Inventory

### 1.1 Counts (production code)

| Primitive | Sites |
|---|---|
| `threading.Thread(...)` | 13 (gui_api tailer, external guardian, sched-bounded, web dashboard adapter, prometheus, readiness prefetch, boot fetch watchdog, openbb bridge, api_manager bounded, run_bounded, mt5 bridge server, ui/main_window, ui/gui_launcher) |
| `ThreadPoolExecutor` | 4 (+1 inline in `mt5_trader.py:4756`): strategy pool, dual-velocity fast/slow, trading_memory retrieval, mt5_calendar_enhanced |
| multiprocessing | `multiprocessing.connection` only, in the untracked `compat/mt5_bridge` |
| asyncio | FastAPI loop in `ui/gui_api.py`, plus `asyncio.to_thread` and `call_soon_threadsafe`. `asyncio.run` appears only in a calendar example |
| `subprocess.Popen` (process model) | gui_api engine spawn (`:557/560`), watchdog (`ops/watchdog/trading_watchdog.py:90`), `mt5_trader.py:2818` |
| `Lock` / `RLock` | 38 (list in 1.3) |
| `Event` | 2 real sites (`crash_recovery.py:112`, `llm_orchestrator.py:5614`) |
| `Condition` | 0 |
| `threading.local` | 7 (TradingStore, GraphStatePersistence, agent_event_bus, pipeline_v2 `_tls`, orchestrator `_cycle_local`) |
| File locks | `fcntl`/`msvcrt` only in `core/trial_ledger.py:714-751`. `core/distributed_lock.py` uses an O_EXCL lock file (see F8) |
| `sqlite3.connect` | 154 |
| `busy_timeout` | 5 s: TradingStore, GraphStatePersistence, trial_ledger, agent_event_bus. 10 s: AgentOperations, trading_memory, macro_graph, graph_checkpointer |
| `BEGIN IMMEDIATE` | 15 sites (trading_safety, agent_operations claim/lease, strategy_lifecycle, alert_outbox, meta_loop, trial_ledger) |
| `check_same_thread=False` | 1 (`intelligence/graph_checkpointer.py:46`). Safe: one connection per `_con()` call, closed in `finally` |
| `signal.signal` | 2 (`core/crash_recovery.py:135,147`) |
| `atexit.register` | 2 (`crash_recovery.py:122`, `mt5_trader.py:3750`) |
| `global _x` singleton getters | 44 |

### 1.2 Thread and executor map (engine process unless noted)

| Thread / executor | Started at | Runs | Shared objects touched |
|---|---|---|---|
| Main thread | `mt5_trader.py main()` | boot, main loop, task_queue (`:5006`), crash-recovery checkpoints (`:4675`), signal handlers | everything at boot; `GraphStatePersistence._lock`; `TaskQueue` (main thread only) |
| `strat_*` pool (N = number of strategies) | `mt5_trader.py:4756` (`with _TPE(max_workers=len(strategy_cycles))`) | `orch.execute_cycle[_v2]`, one per strategy, **against one shared orchestrator** | `orch.*`, `RiskManager`, `PortfolioRiskArbiter` + `TradingStore`, `AgentOperations` (llm budget, intents, tasks), `DualVelocityExecutor.poll`, `AutonomyManager.drain_controls`, `display`/`shared_state.ui_state`, MetaTrader5 module, kill-switch latch, pipeline_v2 maintenance stages (reconcile pending/open, position tick) |
| `sched-bounded` (daemon) | `llm_orchestrator.py:5636` | `_process_due_schedules` (research, news ingest via plugins, macro) | AgentOperations schedules, news ingest → `kill_switch_latch` copy A, MetaTrader5 (calendar/rates) |
| `trading-fast` / `trading-slow` | `operations/dual_velocity.py:37-38` | `process_pending_memory`, other slow work | trading_memory DB, AgentOperations memory outbox |
| trading_memory retrieval pool (up to 10 workers) | `intelligence/trading_memory.py:1140` | per-layer retrieval reads | trading_memory DB (one connection per call, busy_timeout 10 s) |
| `Ψ-ExternalGuardian` (daemon) | `core/external_guardian.py:611`, started `mt5_trader.py:4405` | polls `mt5.account_info`/`positions_get`; on trip: `orders_get`/`order_cancel`/`order_send` flatten | MetaTrader5 module (no lock), guardian state (`_lock` around the trip latch only) |
| CrashRecovery signal handlers | `core/crash_recovery.py:135,147` | `shutdown()` → persistence writes, session hook | `GraphStatePersistence._lock` (non-reentrant) |
| WebDashboardAdapter worker (daemon) | `ui/web_dashboard_adapter.py:19` | HTTP posts from a bounded queue | `queue.Queue` (safe) |
| prometheus HTTP (daemon) | `operations/prometheus_exporter.py:274` | metric scrape | `_cache_lock` |
| **API process**: uvicorn loop + threadpool | `ui/gui_api.py` | sync `def` endpoints in the AnyIO threadpool; WebSockets | `active_ws_clients` list, `trading_state.db`, autonomy control file, `engine.pid`, MetaTrader5 (`/api/mtf-trends` initialize/shutdown) |
| API `harness-event-tailer` (daemon) | `ui/gui_api.py:815` | polls `harness_events`, fans out via `call_soon_threadsafe` | WS clients |
| **Watchdog process** | `ops/watchdog/trading_watchdog.py:90` | spawns and monitors the engine | `engine.pid` |
| **mt5 bridge server** (untracked, Windows host) | `compat/mt5_bridge/mt5_bridge_server.py:162` | one thread per client; every call under `Bridge.lock` | real MetaTrader5 |

### 1.3 Locks (production)

`core`: distributed_lock `_lock_mutex`; trial_ledger `_lock` + `_ledger_lock` + flock; rate_limiter; compliance_audit `_lock` + `_AUDIT_LOCK`; agent_event_bus `_bus_lock`; meta_loop `_append_lock`; graph_state_persistence `_lock` + `_init_lock`; external_guardian `_lock` + `_GUARDIAN_LOCK`; crash_recovery `_recovery_lock`.
`intelligence`: orchestrator `_schedule_thread_lock` and `_cycle_state_lock`; boot_datasheet RLock + `_ds_lock`.
`operations`: supervisor RLock, strategy_factory RLock, autonomy RLock, incident_agent ×2, event_agent RLock + `_manager_lock`, dual_velocity, event_bridge RLock, prometheus `_cache_lock`.
`data`: kill_switch_latch `_lock` + `_LATCH_LOCK`, yahoo `_NET_FAIL_LOCK`, mt5_calendar_wrapper RLock.
`research`: kronos `_KRONOS_PREDICT_LOCK`, replay_buffer ×2.
Also `state_manager._lock`, plus `compat` bridge client RLock and server Lock.

**No lock exists in `core/risk_manager.py`, `core/trade_manager.py`, `core/order_execution.py`, `data/market_data_provider.py`, `core/trading_safety.py` (beyond thread-local connections), `core/shared_state.py` or `core/task_queue.py`.**

## 2. Findings

### F1 🔴 The portfolio open-risk cap is check-then-act, and pending orders are invisible to it (P1, P2)

- `core/trading_safety.py:1361`: `if self.store.open_risk() + new_risk > equity * self.max_open_risk_fraction: return False, "portfolio open-risk limit"`
- `core/trading_safety.py:828-831`: `open_risk()` sums `trades WHERE closed_at IS NULL`.
- A row reaches `trades` only through `record_open_trade` (`trading_safety.py:556`). The only production caller is the pending-order reconciler, after a broker fill is seen (`llm_orchestrator.py:5422`). Nothing reserves risk at permit time. The same applies to `open_bucket_risk` (`:833`), to `correlated_open_risk` (`intelligence/correlation_risk.py:167`, which reads `store.open_trade_rows()`), to `RiskManager.open_portfolio_heat_r` (`core/risk_manager.py:775-824`, which reads `mt5.positions_get()` only, never `orders_get`), and to the paper heat provider.
- **Interleaving:** strategy threads A and B (the `mt5_trader.py:4756` pool) both reach `self.arbiter.permit(...)` (`llm_orchestrator.py:9020`) with open risk at 200. Both see 200 + 100 ≤ 300 and both are approved. Both build plans, create *different* intents (the key is `setup_key:magic`, so there is no cross-strategy guard at `:9113`) and call `place_pending_order` (`:9203`). Combined exposure is 400 against a 300 cap.
- **No threads needed either:** each permit runs before any pending order fills, so a series of cycles while orders rest admits without limit. P2 admitted 6 of 6 against a cap of 3.
- **Fix:** (1) Make reservation part of the permit. Inside one `BEGIN IMMEDIATE` transaction, read open risk plus reserved risk from `order_intents` in `CREATED`/`PENDING`/`PARTIAL`, then insert the intent with its `risk_amount`. Release on `REJECTED`/`CANCELLED`/`EXPIRED`. (2) Include pending orders (`mt5.orders_get`) in the heat calculation. (3) As defence in depth, serialize the section from sizing to order_send with a process-wide `threading.Lock`; the cross-process story is F8.

### F2 🔴 MetaTrader5 is called from at least 4 threads, with no lock (native Windows path)

- No `mt5_lock` exists anywhere. `core/trade_manager.py` (21 call sites), `core/order_execution.py`, `data/market_data_provider.py`, `core/risk_manager.py`, the orchestrator reconcilers and `core/external_guardian.py:467-504` all call the global `MetaTrader5` module directly.
- Concurrent callers: the N strategy threads, `sched-bounded`, the slow lane, and the guardian thread.
- The MetaTrader5 package keeps one IPC channel per process, and `mt5.last_error()` is process-global. Thread-safety is not documented, so treat it as unsafe; whether native calls actually interleave on the wire is **UNVERIFIED** (no Windows terminal here).
- **What is certain:** error attribution is racy. `order_send` returns `None` in thread A, a successful call in thread B resets `last_error`, and A logs and branches on B's code.
- The untracked macOS bridge client (`compat/mt5_bridge/MetaTrader5/__init__.py:119,250`) *does* serialize RPCs under `_lock`, but `last_error()` (`:278`) is read outside it. That is the same attribution race.
- Also on the bridge client: an IPC timeout on `order_send` drops the connection and returns `None` (`:203-205`), while the order may already have executed on the bridge host. That is an "unknown outcome" reported as a failure.
- **Fix:** wrap every call, including `last_error`, in a single module (`core/broker_gateway.py`) holding a process-wide `RLock`. Return `(result, last_error)` atomically. Treat a timeout on `order_send` as `UNRESOLVED` and reconcile by magic and comment, never as a rejection.

### F3 🔴 Concurrent `reconcile_open_positions` double-counts closes in the RiskManager streak breaker (P4)

- `core/harness/pipeline_v2.py:304,312`: every strategy thread runs the maintenance stages `reconcile.pending` and `reconcile.open` on the same orchestrator. The legacy path does the same at `llm_orchestrator.py:7062-7064` and `:7205-7207`.
- `llm_orchestrator.py:5128` reads `durable = {... self.store.open_trade_rows()}`. Then `:5179-5189` runs `self.store.record_close(...)` followed by `self.risk.record_trade_outcome(total_pnl)`.
- `record_close` (`trading_safety.py:591`) is an unconditional `UPDATE ... WHERE ticket=?`, with no `AND closed_at IS NULL`. So both threads "succeed", and `record_trade_outcome` (`risk_manager.py:352-363`) does `self.consecutive_wins += 1` without a lock, once per thread.
- **Effect:** wins are double- or quadruple-counted. `get_streak_multiplier` (`:474-475`) scales size by up to **1.8×**. Losses are also double-counted, which trips the halt early, so the error runs in both directions. `trade_r_history`, the lifecycle events and `perf.close_trade` are duplicated too, which skews the Kelly and statistics inputs. The same pattern applies to `reconcile_pending_orders` → `record_open_trade` (UPSERT, so the row is idempotent but lifecycle events and setup transitions are duplicated).
- P4: 200 closes counted as 800 wins, multiplier 1.8.
- **Fix:** run reconciliation once per pulse on the main thread, before the strategy pool fans out, or under a dedicated `_reconcile_lock`. Make `record_close` conditional (`WHERE ticket=? AND closed_at IS NULL`) and gate `record_trade_outcome` on `rowcount == 1`.

### F4 🟠 SIGTERM/SIGINT handler deadlocks on a non-reentrant lock and swallows SIGTERM (P6, P7)

- `core/crash_recovery.py:128-135`: `_sigterm_handler` → `self.shutdown()` → `self.persistence.begin_checkpoint(...)` (`:286`). That takes `GraphStatePersistence._lock`, a non-reentrant `threading.Lock` (`core/graph_state_persistence.py:162,264`).
- The main thread takes the same lock every cycle (`mt5_trader.py:4675` → `begin_cycle_checkpoint`). A signal delivered while it is held runs the handler on the same thread, and the handler blocks forever. P6 reproduced the hang with exactly that stack.
- **Separately:** `original_sigterm` is `SIG_DFL`, which is not callable, so after `shutdown()` the handler returns and **the process keeps running** (P7). The `_shutdown_event` it sets is read nowhere (grep shows only `:112` and `:297`).
- Consequence on macOS and Linux: the deck's Stop button (`ui/gui_api.py:1581`, `psutil.terminate()`) never stops the engine gracefully. It either deadlocks or keeps trading, and is hard-killed with `kill()` after 10 s. The shutdown snapshot also marks the in-flight `RUNNING` checkpoint `FAILED`, which inflates the crash counter.
- Windows `terminate()` is `TerminateProcess`, so no handler runs at all.
- **Fix:** keep handlers minimal: set a flag or Event, and have the main loop check it the same way as `_stop_file_requested()` (`mt5_trader.py:4422`). Do all I/O in the `finally` block. If I/O in the handler is unavoidable, use `RLock` plus a non-blocking acquire with a timeout.

### F5 🟠 C1's two kill-switch latch copies tear the shared state file (P5)

- `data/kill_switch_latch.py:100-106`: `_save()` writes `self._path + ".tmp"` and then `os.replace`. Copy A (bare import, news ingest on `sched-bounded`) and copy B (`data.` import, decision graph on strategy threads) have **different `_lock` and `_LATCH_LOCK` objects** (P8: `same_lock_object: False`) and share one tmp filename.
- **Interleaving:** A opens tmp ("w", truncate) and writes. B opens the same tmp, truncating it. A runs `os.replace(tmp → path)`, moving the half-written file into place. B's `os.replace` raises `FileNotFoundError`, which the `except OSError: pass` swallows.
- `_load()` (`:89-98`) treats unparseable JSON as blank, which means **unlatched**. P5 saw 22% torn reads, and a restart reported `latched=False`.
- **Fix:** C1's single import root removes the duplicate. Also use a unique tmp name (`tempfile.mkstemp(dir=...)`), `fsync`, and an inter-process `fcntl`/`msvcrt` lock (the pattern already in `core/trial_ledger.py:714`). Treat a corrupt latch file as **latched** (fail closed).

### F6 🟠 The LLM budget reservation is check-then-insert (P3)

- `operations/agent_operations.py:1150-1172` counts `RESERVED`/hourly/daily/tokens/cost with plain `SELECT`s, which autocommit under Python's legacy isolation, then runs `INSERT`. There is no `BEGIN IMMEDIATE`.
- N strategy threads, plus any other process sharing `trading_state.db`, can all pass `max_concurrent=1`. P3: 4/10 trials over the limit, up to 3 granted.
- The trigger_key dedupe at `:1151` has the same race: two threads with the same key both pass the check.
- **Fix:** `con.execute("BEGIN IMMEDIATE")` before the first `SELECT`, as `claim_task` (`:813`) already does. Add a partial `UNIQUE` index on `trigger_key WHERE status IN ('RESERVED','SUCCESS')`.

### F7 🟠 Operator controls written cross-process are lost (P9)

- `operations/autonomy.py:237`: the API process appends to `control_file` with `open(..., "a")`.
- `:254-270`: the engine's `drain_controls` (called from every strategy cycle, `llm_orchestrator.py:7362`) reads the file, then `safe_atomic_write_lines` → `os.replace`.
- The `RLock` is per process. Any line appended between the read and the replace, or appended through a handle already open on the old inode, is gone. P9 lost 1,826 of 2,000.
- **Separately**, `_state` is in-memory and authoritative, and the API process's `_persist_locked` overwrites the state the engine persisted (and vice versa): last writer wins on `halted`/`paused`/`level`. That is the same shape as C1, but across processes.
- **Fix:** move controls into an SQLite table (`INSERT` from the API; `UPDATE ... SET consumed=1 WHERE id IN (...)` under `BEGIN IMMEDIATE` in the engine). Re-read halted/paused from the store on every check.

### F8 🟠 The distributed trade lock is never acquired

- `core/order_execution.py:313-318` calls `_lock.acquire(blocking=True)`, but `_FileLock.acquire(self)` (`core/distributed_lock.py:22`) takes no arguments. The call raises `TypeError`, which `except Exception` turns into `log.debug("DistributedLock skipped ...")` and `_lock = None`. Verified: `acquire(blocking=True) -> TypeError`.
- So `place_market_order` has never been serialized, and `place_pending_order` (`:640`) has no lock at all. `mt5_trader.py:200` imports `get_trade_lock` and never uses it.
- Even if called correctly, the design is unsafe:
  - Stale-lock breaking (`:36-39`) lets two processes both `remove` and re-create.
  - `TradeLock.__exit__` releases even when `acquire` timed out, deleting another holder's lock.
  - The path uses `$TEMP` or `/tmp`, which differs between processes with different environments.
- **Fix:** drop it in favour of the F1 SQLite reservation (cross-process and atomic). If a mutex is still wanted, use `fcntl.flock`/`msvcrt.locking` on a fixed repo-local path.

### F9 🟠 The single-instance engine guard is check-then-act

- `ui/gui_api.py:518-537` checks `engine.pid` and then spawns (`:557/560`). The engine writes `engine.pid` only after its heavy imports, at the start of the `llm-trade` branch (`mt5_trader.py:3737-3741`), with an unconditional overwrite and no check of its own.
- Two Start clicks inside that window (the endpoint is sync and runs in the threadpool, so the clicks can be concurrent), or Start plus `START_TRADING.bat`/`start_trading.sh` or the watchdog, produce two engines. Each runs its own strategy pool against one `trading_state.db` and one MT5 account, which compounds F1 across processes.
- `os.remove(_pid_file)` on stop (`:1587`) can also delete the *new* engine's pidfile.
- PID reuse: start checks only that the process is "python"; stop checks nothing.
- **Fix:** have the engine take an exclusive OS lock (`fcntl.flock` / `msvcrt.locking` on `engine.lock`) as its first action and exit if it fails. Keep that file descriptor open for the process lifetime. The API should test the same lock instead of the PID.

### F10 🟠 Guardian flatten versus concurrent entries (TOCTOU)

- `data/market_data_provider.py:957-958` checks `get_guardian().is_tripped` and then sends. The guardian's `_execute_flatten` (`core/external_guardian.py:467-504`) takes a single snapshot of `orders_get`/`positions_get`.
- A strategy thread that passed the check just before `_trip` sets `TRIPPED` (`:405-409`) sends after the guardian's snapshot. Its order survives the "flatten everything" and there is no re-sweep.
- RED trips don't call `halt_entries_fn` (only ORANGE does, `:442`), so in the TRIPPED state only the `is_tripped` gate protects entries.
- **Fix:** have the guardian sweep until two consecutive empty snapshots (bounded). Make order submission take a shared read lock that `_trip` takes exclusively, or re-check `is_tripped` after `order_send` and cancel or close immediately.

### F11 🟡 Duplicate velocity results and other unguarded orchestrator state

- `operations/dual_velocity.py:96-115`: `poll()` snapshots the jobs under the lock and pops each job only after processing, so two strategy threads polling at once both report the same completed job. Effect: duplicate `dual_velocity_job_finished` events (`llm_orchestrator.py:934-949`), telemetry only. Fix: pop under the lock before reading `result()`.
- `llm_orchestrator.py:9157-9164`: `self.rl_state_history.append(...)` and then `self.rl_state_history = self.rl_state_history[-1000:]` from N threads. An append can be lost when it races the reassignment (🟡, RL telemetry).
- `:7515/7573/7589`: lazy `self._news_corr_engine = ...`, `self._live_indicator_fetcher = ...` and `self._news_exec_engine = ...` initialization can construct twice (⚪).
- `core/shared_state.py:85-88`: `add_log` does append then `pop(0)` with no lock. The `display` header and cycle fields interleave across strategies (⚪, cosmetic).
- `operations/agent_operations.py:1239-1252`: `resolve_order_intent` runs SELECT, validates, then UPDATE without `BEGIN IMMEDIATE`. Two reconcilers can both validate from the same state. The terminal-state guard can be bypassed only in that window (🟡). Fix: `BEGIN IMMEDIATE`, or `UPDATE ... WHERE intent_key=? AND status=?` and check `rowcount`.

### F12 🟡 SQLite configuration inconsistencies

- `TradingStore._connect` (`core/trading_safety.py:116-122`) passes `timeout=10.0` and then `PRAGMA busy_timeout=5000`. The PRAGMA wins, so the effective wait is 5 s, not 10. `GraphStatePersistence` does the same.
- `AgentOperations`, `trading_memory` and `macro_graph` use 10 s and open **a fresh connection per call** (`agent_operations.py:100-107`, `trading_memory.py:30-37`). WAL is set only at init. That is fine because WAL persists in the file.
- Connections from `with sqlite3.connect() as con` are never closed explicitly (the context manager commits but does not close); CPython refcounting closes them. ⚪
- Two processes write `trading_state.db`: the engine (N threads) and the API (control and `set_state` endpoints). The watchdog (`ops/watchdog/trading_watchdog.py`) does not open the DB; it only uses `engine.pid` and `os.kill(pid, 0)`. Verified by grep.
- `update_state`, `claim_task` and `transition_setup_instance` correctly use `BEGIN IMMEDIATE`.
- No long-held write transactions around network or MT5 calls were found in TradingStore or AgentOperations: each method opens, writes and commits.
- The "~70 s stall" (`docs/CODEBASE_DEEP_DIVE.md:674`, F-MEM-2) is still present: `intelligence/trading_memory.py:1140-1145` calls `future.result()` with **no timeout**, on top of `busy_timeout=10000` per layer. Fix: `wait(futures, timeout=...)`, and cap `busy_timeout` at 1-2 s for read paths.

### F13 🟡 Lazy singletons without locks

- `operations/supervisor.py:257-260` and `operations/autonomy.py:283-286` (`if _x is None: _x = X()`) have no lock. Both are first created on the main thread at boot (`mt5_trader.py:4173`) before any pool starts, so this is ⚪ in practice. It becomes real if the first touch moves into a worker; `get_autonomy()` is also called from API threadpool handlers in the API process.
- `data/llm_router._router` is duplicated by C1 (P8): two routers mean two sets of provider circuit and rate state.
- `get_kill_switch_latch` is locked but duplicated (F5).

### F14 ⚪ Shutdown ordering

- `mt5_trader.py:5605-5655` `finally` order: CrashRecovery snapshot → guardian stop (join 5 s) → supervisor shutdown → `orch.shutdown()` → `velocity.shutdown(wait=False, cancel_futures=True)`.
- A running slow-lane job keeps running. `concurrent.futures` joins non-daemon workers at interpreter exit, so it completes rather than being killed mid-write.
- `sched-bounded`, the guardian (if its join times out), the web adapter, the prometheus server and `harness-event-tailer` are **daemon** threads and die at interpreter teardown. `sched-bounded` can be inside an SQLite write. WAL makes the transaction atomic, so the result is a lost schedule completion (the lease will expire and reclaim), not corruption.
- The guardian may be mid-flatten when killed. Fix: join `sched-bounded` with a bound before the `finally` returns, and don't stop the guardian before the strategy pool has exited (it already has, since the `with` block joins).
- The strategy `ThreadPoolExecutor` is a `with` block, so it is joined every pulse ✅.
- `atexit` order is LIFO: `_cleanup_pid` (registered later) runs before CrashRecovery's `_atexit_shutdown`. That is harmless.

## 3. Verified safe

- **Per-strategy LLM context:** `llm_orchestrator.py:7262-7315`. `memory` and `_last_llm_trace` are keyed by `threading.local` strategy, and the dicts are mutated under `_cycle_state_lock`. The lock is never held while calling out.
- **Schedule single-flight:** `llm_orchestrator.py:5593-5642`. The flag is guarded by `_schedule_thread_lock`, and the worker releases it in `finally`. The lock is never held across work.
- **`DualVelocityExecutor` submit, dedupe and backpressure:** `operations/dual_velocity.py:43-72`, all under `_lock`. Only the duplicate-poll issue (F11) remains.
- **Candle dedupe:** `claim_task` (`agent_operations.py:804-830`) uses `BEGIN IMMEDIATE`, so it is atomic across threads and processes.
- **Same-intent dedupe:** `create_order_intent` uses `INSERT OR IGNORE` on the `intent_key` PK (`agent_operations.py:1218`), which is atomic. It is not a cross-strategy exposure guard (F1).
- **`TradingStore`:** thread-local connections (`trading_safety.py:111-129`), and `update_state` read-modify-write under `BEGIN IMMEDIATE` (`:336-358`). For example, `equity_peak` in the arbiter is safe.
- **`GraphStatePersistence`:** writes are serialized by `_lock` and connections are thread-local. The only hazard is signal re-entry (F4).
- **`graph_checkpointer` `check_same_thread=False`:** a connection per call, closed in `finally`.
- **Guardian trip latch:** idempotent under `_lock` (`external_guardian.py:405-409`). `start_guardian`/`get_guardian` use `_GUARDIAN_LOCK`. `external_guardian` is imported only as `core.external_guardian`, so it is not duplicated by C1 (P8).
- **`crash_recovery.get_recovery`:** double-checked under `_recovery_lock`.
- **WebDashboardAdapter:** a bounded `queue.Queue` and one worker. Calls from the trading loop never block.
- **gui_api tailer → WebSocket:** `loop.call_soon_threadsafe(asyncio.ensure_future, ...)` (`ui/gui_api.py:756`) is the correct cross-thread hand-off. `broadcast_harness_event_to_gui` (`:119-125`, `asyncio.create_task` from any thread) has no callers.
- **TaskQueue (`core/task_queue.py`):** touched only from the main loop (`mt5_trader.py:5006-5026`). The `auto_quant_tools` instance is separate.
- **compat mt5 bridge server:** every terminal call is under `Bridge.lock`, and detach and shutdown are reference-counted.
- **Strategy pool lifetime:** `with ThreadPoolExecutor` joins every pulse, and `_fut.result()` propagates exceptions.
- **`trial_ledger` append:** thread `_lock` plus `fcntl.flock`/`msvcrt.locking` plus `BEGIN IMMEDIATE`. This is the one correct cross-process file lock in the repo.

## 4. Effects of double module loading on locks (C1, §2.1)

P8 enumerated the 25 duplicated files. Module-level synchronization objects that are duplicated:

| Module | Duplicated object | Shared resource | Consequence |
|---|---|---|---|
| `data/kill_switch_latch.py` | `_LATCH_LOCK`, `_LATCH`, each instance's `_lock` | `kill_switch_latch.json` + its `.tmp` | split latch (C1) **and** torn file → fail-open on restart (F5, P5) |
| `data/llm_router.py` | `_router` | provider circuits and rate limits | two independent budgets and circuit breakers per process |
| `core/trading_safety.py` | `TradingStore` and `PortfolioRiskArbiter` classes | `trading_state.db` | no module lock; DB-level `BEGIN IMMEDIATE` still serializes, so it is safe for `update_state`. `isinstance` checks across the copies fail |
| `core/risk_manager.py` | `RiskManager` class | risk state file (`save_state`) | instances created from different copies hold separate counters (UNVERIFIED that two live instances exist at runtime) |
| `operations/agent_operations.py`, `operations/strategy_lifecycle.py` | classes only | DB | DB transactions are the real lock, so safe |

No other duplicated module holds a module-level `Lock`. The classes with instance locks (autonomy, supervisor, external_guardian, graph_state_persistence) are not among the 25.

## 5. Priority fixes

1. F1: atomic risk reservation (`BEGIN IMMEDIATE` + pending-intent risk) and pending orders in heat. **Prerequisite for running more than one strategy live.**
2. F3: reconcile once per pulse, outside the strategy pool. Conditional `record_close`, and outcome only on `rowcount == 1`.
3. F2: a single broker gateway with a process lock. `order_send` timeout → `UNRESOLVED`.
4. F4: a flag-only signal handler that the main loop honours.
5. F5 / C1: a single import root. Unique tmp names, fsync, fail-closed load.
6. F6, F7, F9: move cross-process state (LLM budget, controls, engine singleton) to `BEGIN IMMEDIATE` SQLite plus an OS file lock.
7. F8: delete `core/distributed_lock.py` or fix it; today it gives a false sense of safety.
