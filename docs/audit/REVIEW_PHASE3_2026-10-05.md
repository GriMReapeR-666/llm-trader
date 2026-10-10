# Review: Phase 3 (Emergency stack) implementation

**Branch:** `phase3/emergency-stack` (local, not pushed), 9 commits `1c5446b..58c3dc9` on top of `c47f4a9`. 377 files, +6,894 / −1,830 lines.
**Reviewed against:** [`PLAN_PHASE_3_4.md`](PLAN_PHASE_3_4.md).
**Date:** 2026-10-05.

**How:**
- **Test runs:** a separate worktree of the branch. I ran the full suite, then bisected every failure across the 9 commits.
- **Reading:** every diff, read against the plan.
- **Probes:** small offline probes for each suspected defect, using a temporary database, the real FastAPI app via `TestClient`, and the bridge's `FakeMT5`. No terminal, no network, no tracked file changed.

## Verdict

**Not mergeable yet.** The plumbing is mostly in place, and several parts are good: the import root, the engine lock, the flag-only signal handlers, the autonomy gate, and the fail-closed validator changes.

**The core of the phase doesn't work end to end.** Phase 3 exists so that "stop" really stops and the operator sees the truth. Through the real wiring:
- the deck's **KILL** sets nothing and reports `ACKNOWLEDGED`;
- the deck's **FLATTEN** queues nothing;
- the engine's live **flatten** closes nothing, and even with the right broker client it skips the default strategy's positions, while reporting `APPLIED, remaining 0`.

The drill test passes only because it wires these pieces by hand. Separately:
- 23 tests fail on the branch head;
- one local import breaks the `kill-switch`, `latch` and `journal reconciliation` CLIs;
- the safety strip always says **PAPER**.

One blocker (B4) and two highs (H3, H4) **come from my plan**, not from the implementer; see §5.

| Severity | Count |
|---|---|
| 🔴 Blocker | 5 |
| 🟠 High | 6 |
| 🟡 Medium | 5 |
| ⚪ Low | 3 |

---

## 1. Blockers

### B1 🔴 The deck's kill switch and flatten are no-ops that report success
- **Cause:** the API process's `get_autonomy()` never gets a store. Only the engine attaches one (`intelligence/llm_orchestrator.py:7446`). `issue_control` therefore inserts no `operator_controls` row and never calls `set_kill_switch`. It also swallows store errors. Either way it returns `ok: True`.
- **API response** (`ui/gui_api.py` `deck_control`): `killswitch` → `ACKNOWLEDGED`, and `flatten` → `REQUESTED`, with no `control_id`.
- **Deck:** shows "KILL ACKNOWLEDGED" and sets `halted: true` locally (`CommandPalette.tsx:461-465`). It can't poll because there is no id.
- **Probe** (real app, fresh store, authenticated):
  ```
  killswitch 200 {'ok': True, 'status': 'ACKNOWLEDGED', ...}
  flatten    200 {'ok': True, 'status': 'REQUESTED', ...}      # no control_id
  kill switch active in store: False
  operator_controls rows: []
  ```
- **Why tests didn't catch it:**
  - The drill sets `autonomy.store = store` by hand (`tests/drills/test_emergency_drill.py`).
  - Two existing tests do catch it and **fail on the branch**: `test_supervisor_autonomy.py::test_pause_blocks_but_resume_clears` and `::test_control_plane_roundtrip_rejects_unknown`. A store-less manager issues controls that go nowhere.
- **Fix:**
  1. Attach the journal store (`LLM_TRADER_JOURNAL_PATH`) to `get_autonomy()` when the API starts.
  2. `issue_control` returns `ok: False` when there is no store, the insert fails, or `set_kill_switch` fails.
  3. The API returns 503 without a `control_id`.
  4. The deck treats a missing id as FAILED.
  5. The drill uses the real API wiring.

### B2 🔴 Live and demo flatten closes nothing and reports APPLIED
- **Cause:** `_drain_operator_controls` (`mt5_trader.py:1385-1412`) passes `orch.client` as the broker client. That object is an `MT5Client` wrapper with no `positions_get` or `orders_get`. `execute_flatten` (`core/order_gateway.py:1038-1054`) skips the reads when those methods are missing, sees an empty account twice, and returns.
- **Kill switch too:** `cancel_all_owned_pending` takes the same path, so it cancels nothing.
- **Probe** (`FakeMT5` with open positions, LIVE gateway bound to it):
  ```
  [orch.client = MT5Client]        status=APPLIED result={'closed': 0, 'remaining': 0, 'sweeps': 2}  close requests sent: []
  ```
- **Fix:**
  - Don't pass a client: the gateway already has its bound live client.
  - Fail (FAILED) if positions can't be listed.
  - Compute `remaining` from a final broker snapshot, not from the gateway's own "closed" bookkeeping. Today a close whose result says success is never re-checked against the broker.

### B3 🔴 Flatten and kill switch skip the default strategy's positions
- **Mismatch:** `TradeProposal.magic` (`core/trading_safety.py:57-61`) gives 460001 to swing, **460002 to scalp (the default strategy)** and 460003 to macro/neocortex/causal. Operator flatten and kill switch use `owned_magics = {460001}` (`llm_orchestrator.py:844`, `mt5_trader.py:1386, 1410, 2273`).
- **Probe** (with the real module as client): only ticket 101 (460001) was closed. 102 (scalp) and 103 (macro) stayed open, and the result still said `remaining: 0`.
- **Same root, pre-existing elsewhere:**
  - `TradeManager.managed_magics` and `RiskManager.managed_magics` are `{460001, 460002}`;
  - `daily_reconciliation.BOT_MAGICS` omits 460003.
- **Fix:** one `BOT_MAGICS = {SWING_MAGIC, SCALP_MAGIC, NEOCORTEX_MAGIC}` in `core/trading_safety.py`, used everywhere above.

### B4 🔴 An abandoned analysis stage can still place an order after the runner gave up
- **How the staged pipeline works:** in `PIPELINE_V2` (on by default), **every analysis stage re-runs the whole `execute_cycle` body**, including validation, sizing and the gateway submit (`_run_staged_pass`, `core/harness/pipeline_v2.py:448-474`).
- **What PR 3.6 changed:** it made `analysis.context/data/causal/llm` abandonable. `analysis.context` gets a **30 s** deadline, while LLM latency is a 24.5 s median and 70 s p90.
- **What happens on timeout:**
  - The runner reports the stage failed and moves on, but the worker thread keeps running to the order.
  - The worker also loses the pipeline's thread-local state (`_tls.v2_active` is False), so seam attribution breaks.
  - After two live abandoned threads, the leak bound **pauses all trading globally** (`abandon_leak:<stage>`). In the test suite this pause leaks into later tests (3 order-dependent failures).
- **Probe:**
  ```
  runner thread v2_active: True | stage status returned: failed | side effects at return: [('v2_active_in_stage_thread', False)]
  side effects 0.8 s later: [..., 'ORDER SUBMITTED']
  ```
- **Fix:**
  - Remove `abandonable` from the `analysis.*` stages.
  - Bound the LLM where abandoning is safe: a per-call deadline around the provider request inside `get_decision`, where a timeout means HOLD.
  - Keep stage abandonment only for stages proven free of side effects.

### B5 🔴 23 tests fail on the branch head
- **Result:** 4,686 passed, 23 failed, 11 xfailed, 1 xpassed. Baseline `c47f4a9`: all 23 pass.
- **Bisect:**

| Introduced by | Failures | Why |
|---|---|---|
| PR 3.3 `97e6667` | 4 | 2 autonomy tests (B1); 2 CLI reconciliation tests (H1). |
| PR 3.5 `d2f34c3` | 16 | Older tests build market dicts without the new required fields (M6). The code is right; the tests weren't updated. |
| PR 3.6 `69b2ab4` | 3, full run only | The global `abandon_leak` pause from `test_stage_deadlines.py` leaks into `test_ablation_harness.py` (×2) and `test_pipeline_v2_e3.py::test_golden_replay_llm_called_once_per_mode` (B4). |

---

## 2. High

### H1 🟠 One local import breaks the operator CLIs
- **Cause:** `from core.trading_safety import TradingStore` inside `main()` (`mt5_trader.py:2264`, the `flatten` branch) makes `TradingStore` a local name for the whole function. All 16 other uses in `main()` raise `UnboundLocalError`.
- **Broken commands:** `kill-switch`, **`latch`** (the operator clear added in PR 3.2), `journal reconciliation` and others.
- **In `llm-trade`:** the two uses (`:3987`, `:4174`) sit inside `try/except`. The engine starts, but silently skips restoring the persisted balance.
- **Ironic detail:** the comment at `:3984` warns against exactly this import.
- **Fix:** delete the local import.

### H2 🟠 The safety strip shows invented values
`GET /api/safety` (`ui/gui_api.py:1949+`) violates the plan's rule that "unknown is null":
- **mode** comes from `LLM_TRADER_MODE`, which nothing sets, so it is always **PAPER**, even for a live engine.
- **daily breaker** comes from a `daily_breaker` state key that nothing writes, so it is always "not tripped", with a made-up limit of −500.
- **autonomy and latch** come from the API process's own singletons, which have no store (B1), so they can be stale.

**Fix:** read mode from the journal scope and the breaker from the persisted RiskManager state; return null when a value is unavailable.

### H3 🟠 The tripwire guard suppresses real crises, and the source filter filters almost nothing
- **Recall loss:** the guard (`data/news_triage.py`) is the research probe's measured guard plus two phrases. Its "loses 0 of 13 real trips" figure was measured on **13 short strings harvested from tests**. On 15 plausible crisis headlines it suppresses 5:
  - "Trading halted on NYSE after technical glitch; exchange says it **may** reopen later"
  - "Stock exchange closed after earthquake as officials **review** damage"
  - "Exchange **halts trading in** all stocks after crash"
  - "Credit Suisse bank run: clients pull $10 billion, **no** end in sight"
  - "Treasury flash crash: …, Fed **may** intervene"
- **Weak source filter:** it accepts tier `RUNTIME_FEED`, which includes Yahoo Finance, Seeking Alpha, bare NewsAPI, and **any URL whose publisher string says "reuters"**.
- **Fix:**
  - Narrow the guard to explicit negation, retrospective and forecast markers. Drop `may`, `could`, `if`, `when`, `how`, `review`, a bare `no`, and the `halts trading in` single-name rule.
  - Add a realistic must-trip corpus.
  - Count only PRIMARY sources and wire **domains**, keyed by URL host.

### H4 🟠 A flatten issued during a long cycle expires instead of running
- **When the engine drains controls:** only at the top of the main loop and in the sleep loops (`mt5_trader.py:4571, 5790, 5799`). The main thread is blocked while the strategy pool runs a cycle.
- **What expires them:** `claim_pending_operator_controls` marks REQUESTED rows older than **60 s** as EXPIRED. Cycles routinely run 70-180 s, so a flatten requested mid-cycle is dropped as "engine offline".
- **Other gaps:**
  - ACKNOWLEDGED rows are never recovered after a crash.
  - Expiry runs only when the engine claims, so with the engine down the deck never sees EXPIRED.
  - The deck stops polling after 10 s (`CommandPalette.tsx:469-470`).
- **Fix:**
  - A dedicated control thread drains every second, independent of cycles; gateway calls are already lock-protected.
  - Expire based on engine heartbeat age, not request age.
  - On startup, mark stale ACKNOWLEDGED rows FAILED.
  - The deck polls until a terminal state or the expiry.

### H5 🟠 Latch durability gaps
`data/kill_switch_latch.py`:
- **Lost updates between processes:** the read-modify-write isn't atomic across processes. `_load()` runs outside the transaction, and `_save()` writes through `update_state(lambda _curr: st_copy)`, which ignores the current value. The news worker and an operator clear can overwrite each other.
- **A fresh latch can be lost (fails open):** a failed store write is swallowed (`except: pass`) while the file mirror is still updated. The next `_load()` prefers the old store record.
- **Dead market-data clear:** `orch.macro_provider` doesn't exist, and `macro_observations_ingest` is a list, so the pulse clear in `mt5_trader.py` (Step 5b) never runs. The error is swallowed at debug level. Only the API clear works (the CLI is broken by H1), and the deck has no clear action.
- **Re-pointed singleton:** `entries_blocked(store)` re-attaches the process-wide latch to whatever store the caller passes.
- **Mocks in production code:** `_load()` special-cases `MagicMock`/`Mock`.
- **Fix:**
  - Do the whole transition inside `update_state` (`lambda cur: transition(cur)`).
  - On a store write failure, stay latched and log an ERROR.
  - Attach the journal store once at boot.
  - Delete the dead pulse block, or feed it a real, timestamped VIX.

### H6 🟠 The offline flatten CLI can race the engine
`mt5_trader.py flatten` (`:2259-2272`):
- **No engine lock:** it doesn't take the engine lock, as the plan required.
- **Weak confirmation:** `--confirm` accepts any string.
- **Default scope:** without `--owned` it closes manual positions too.
- **Possibly sees nothing** (*by reading, not run*): it configures a LIVE gateway without binding an MT5 client, so it may see an empty account (same shape as B2).

---

## 3. Medium

| ID | Finding | Where |
|---|---|---|
| M1 | `CC-F14` scheduler join is dead code: `orch.bounded_pipeline` is not the `sched-bounded` thread. That thread is started anonymously and has no reference to join. | `mt5_trader.py` finally block; `llm_orchestrator.py:5727` |
| M2 | *By reading, Windows only:* `msvcrt.locking` locks byte 0, so another process's `read_pid()` can't read the pid. The API's stop and `/api/safety` would show no pid on Windows. | `core/file_lock.py` |
| M3 | Import-root guard gaps:<br>• The AST check misses inserts made in a loop, so 5 production files still add subpackage folders: `research/mt5_backtest_engine.py:24-28` (imported by the `mt5_trader` backtest path), `full_pipeline_backtest.py`, `ensemble_backtest.py`, `ui/main_window.py`, `kronos_mcp/tools_forecast.py`.<br>• 81 test-file inserts remain, some at import time (`tests/test_live_order_lockout.py:26-27`).<br>• CI still runs `import_guard.py --max 25`.<br>• `test_every_import_path_shares_one_latch` now compares the module to itself.<br>• A `sys._d9_kill_switch_latch_registry` workaround hides duplicates instead of failing on them. | various |
| M4 | The drill doesn't test the real wiring. It sets the autonomy store by hand, uses magic 460001 only, and passes the paper book as `client`, which hides B1, B2 and B3. There is no `FakeMT5` live-flatten drill, although the plan asked for "paper and `FakeMT5`". | `tests/drills/test_emergency_drill.py` |
| M5 | Control rows can be overwritten: `complete_operator_control` has no status compare-and-set, so a late APPLIED can overwrite EXPIRED or FAILED. | `core/trading_safety.py` |
| M6 | `MarketEvidence` requires fields that aren't evidence: `setup_cooldown_seconds` is configuration and `breakout_event` is an optional trigger. This caused most of the 16 PR 3.5 failures. The "producer supplies every field" test is a grep of the source text. | `core/trading_safety.py` `MarketEvidence` |

## 4. Low
- ⚪ The SIGTERM/SIGINT handlers log; this is usually safe because logging uses re-entrant locks, but a pure flag is cleaner.
- ⚪ `_stop_file_path()` now silently prefers `stop.flag` over `trader_stop.flag` when it exists.
- ⚪ `issue_control` swallows `set_autonomy_paused` failures and still reports APPLIED.

---

## 5. Where my plan was wrong
| Finding | Plan error |
|---|---|
| B4 | I listed `analysis.context/data/causal/llm` as abandonable, assuming they were read-only. In the staged pipeline each of them runs the full cycle, including the order. The correct place for a deadline is the LLM provider call. |
| H3 | I recommended porting the research guard on the strength of its "31 of 33 suppressed, 0 of 13 lost" measurement, without checking that the 13 "real" trips were short test strings. |
| H4 | I specified draining "once per pulse and in the sleep loop" plus a 60 s expiry, which can't both hold when a cycle takes longer than 60 s. A separate control thread is needed. |

`PLAN_PHASE_3_4.md` (PR 3.2 step 3, PR 3.3 draining, PR 3.6 "Deadline" row) should be corrected to match.

## 6. What is verified good
- **Import root:**
  - 0 double-loaded modules;
  - production bare imports down from 637 to 2;
  - all 17 scheduler handlers still resolve;
  - `pyproject.toml` and `pythonpath = .` are fine.
- **Engine lock:** `fcntl` lock, rc 3 for a second engine; the API's start check and its stop (stop flag, a 15 s wait, then terminate) are right in design.
- **Signal handlers** only set a flag, and the main loop honours it.
- **Autonomy gate:** taken out of the telemetry `try`; it fails closed and attaches the store in the engine.
- **`entries_blocked`:** wired into the validator and gateway entries only; exits stay allowed.
- **PR 3.5 code:**
  - NaN and missing-key handling;
  - `high_impact_news=None` counts as unsafe;
  - `TS-P2` and `PH1-N2`;
  - the fail-open data-quality, news and Ψ checks now HOLD and release the task;
  - a lock on the data-quality fingerprints.
- **`operator_controls`:** the table and its compare-and-set claim are correct in themselves (port of probe P9).
- **Deck:**
  - typed confirmations;
  - no "executed" on HTTP 200;
  - `NOT SENT: deck offline`;
  - the safety strip renders UNKNOWN for null or stale fields;
  - `tsc` clean, 16 `bun` tests pass.
- **`PH3-N1`** (a missing VIX no longer clears the latch) is fixed.

## 7. Recommended fix order
1. **H1:** delete one line. That restores the CLIs and 2 tests.
2. **B1 + B2 + B3:**
   - attach the store in the API;
   - fail when there is no `control_id`;
   - flatten uses the gateway's bound client;
   - `remaining` comes from the broker;
   - `BOT_MAGICS` everywhere.

   Then rewrite the drill to use the real API wiring, plus a `FakeMT5` live flatten across all three magics.
3. **B4:** revert abandonment for the `analysis.*` stages and add a per-call LLM deadline. That fixes the 3 polluted tests.
4. **H4:**
   - a control thread;
   - heartbeat-based expiry;
   - ACKNOWLEDGED recovery;
   - deck polling until a terminal state.
5. **B5 (rest):** update the 16 tests for `MarketEvidence` and drop the two non-evidence fields (M6).
6. **H2** (strip fields from the journal), **H5** (latch), **H6** (offline flatten).
7. **H3:** narrower guard, domain-only sources, realistic corpus.
8. Medium and low items. Then the full suite, the drill and a mutation re-measure, and only then tick the roadmap.

**Phase 4 note:** `phase4/live-mode-correctness` (2 commits) is built on this branch. The fixes should land on `phase3/emergency-stack` first, then be rebased or merged into Phase 4. The roadmap on the branch ticks every Phase 3 item; B1-B4, H1-H6 and M1-M6 should be unticked until they're fixed.

---

## 8. Fix status (branch `fix/phase3-review`)

**What changed:**
- All findings in §1-§4 are fixed in this branch.
- The plan corrections from §5 are applied to `PLAN_PHASE_3_4.md`.

**Re-verified with the original probes:**
- **API controls:** the API refuses (503) when it has no journal. With a journal it returns real control ids, writes the rows and the kill switch to the journal the engine drains, and `GET /api/control/{id}` reads the same journal from any working directory.
- **Live flatten:** with the production `MT5Client` shape, it closes positions on `FakeMT5` across all three magics.
- **Stage deadlines:** only `velocity.poll` and `memory.dispatch` stay abandonable. The LLM deadline wraps the provider call only.
- **CLIs:** `latch`, `kill-switch` and `journal reconciliation` work again.

**Three more test-isolation problems fixed during verification:**
1. **The news latch leaked between tests.** It was shared by every test in an xdist worker, so one test that tripped it made later validator and gateway tests fail ("news kill-switch latched"). Now each test gets a fresh latch (`isolated_kill_switch_latch` in `tests/conftest.py`).
2. **Tests read the real autonomy state.** The autonomy singleton loaded the repo-root `autonomy_state.json` before the fixture redirected its paths, so a paused real engine made tests fail. Each test now starts from clean in-memory state. Proven with a paused canary file, which the tests leave untouched.
3. **A stray file in the repo root.** `tests/test_quant_models_wiring.py` passed `state_file=":memory:"`, which wrote a JSON file named `:memory:` there. It now uses `tmp_path`.

**Also:** the Phase 3 API routes (control status, safety, latch) resolve the journal the same way the control writer does: `LLM_TRADER_JOURNAL_PATH`, else the repo-root `trading_state.db`.

**Gate:**
- 2 consecutive full runs: **4,714 passed, 0 failed**, no stray files;
- import guard 0;
- deck `tsc` clean, `bun test` 16/16;
- web GUI `tsc` clean.

**Still open (not blocking the merge):**
- the mutation re-measure (`TS-9`);
- older API routes still default to a working-directory-relative `trading_state.db`, as before Phase 3.
