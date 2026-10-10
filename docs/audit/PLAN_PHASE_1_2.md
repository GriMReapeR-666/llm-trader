# Implementation Plan: Phase 1 (Safety net) and Phase 2 (One door to the broker)

Companion to [`ROADMAP.md`](ROADMAP.md). IDs in backticks refer to the roadmap and audit reports.
Estimated effort: **Phase 1 about 4 days, Phase 2 about 7 days** (focused work, including review).

---

## 0. Ground rules for both phases

1. **Tests before changes.** Every behavioural change lands after a test that pins the current behaviour (characterization) or the desired behaviour.
2. **Known bugs become strict expected failures.** A test for a known bug is marked `@pytest.mark.xfail(strict=True, reason="<roadmap ID>")`. When someone fixes the bug, the test starts passing, `strict=True` turns that into a failure, and the fixer must remove the marker and tick the roadmap box. That keeps the roadmap and the code honest.
3. **Small PRs.** One PR per numbered work package below, each with its own tests, the full suite green, `graphify update .`, and a roadmap checkbox ticked.
4. **Reuse first** (the repo's own `AGENTS.md` rule). The gateway assembles pieces that already exist: arbiter, order intents, execution reports, capability tokens, geometry checks. No new third-party runtime dependency.
5. **Live trading stays blocked throughout.** The two accidental blocks (`1a-F13`, `TL-T1`) are **not** touched until Phase 4.

### 0.1 Before Phase 1 starts (half a day)
| Step | Who |
|---|---|
| Merge `feat/macos-support-and-audit` into `main` (PR) so phase branches start from it | you approve, I open the PR |
| **Rotate the RoutesMe API key** in your RoutesMe account; put the new key only in `.env` | **you** |
| **On the Windows machine, before pulling anything from Phase 1:** copy `artifacts/` and `data/` somewhere outside the repo (§1.2 explains why) | **you** |

---

## Phase 1: Safety net and test hygiene

**Goal:** a test suite that (a) catches a broken safety rule on its own, (b) can't touch real data or a real terminal, and (c) runs automatically on every PR.

**Exit gate:**
- the single-gate test pack is green, with known bugs as strict xfails;
- two consecutive full runs from a fresh clone (`-n auto`) leave `git status` clean and create no files in the repo root;
- the bridge log shows zero test connections;
- the mutation score on the six safety modules is **≥ 75 %** (today 48 %);
- CI is green on the PR.

### PR 1.1: Secrets (0.5 day) · `P1-C5`
| Change | Where | Detail |
|---|---|---|
| Replace the leaked-looking fixture value | `tests/test_section_f_hardening.py:20,33` | Use an obviously fake `rm-TESTFIXTURE…` value of the same shape, so the redaction test still means something. |
| **Fix the scanner that scans zero files** | `operations/security_audit.py:144` | `paths = _git_tracked(root) if include_untracked else []` is inverted. Tracked files must always be scanned; `include_untracked` only *adds* the walk. This is why the deck shows "clean · 0 files". |
| Report test-file secrets as findings | `operations/security_audit.py:201` | `tests/` is classified "non-production" and effectively ignored. Keep the category, but make CI fail on any real-looking key anywhere. |
| Pre-commit hook | `.githooks/pre-commit` + `git config core.hooksPath .githooks` (one line in `setup_mac.sh` and `build.bat`) | Runs the fixed scanner on staged files. No new dependency. |
| CLI entry | `python -m operations.security_audit --tracked --fail-on-findings` | Used by the hook and by CI. |

**Tests:** a scanner test that writes a fake `rm-…` key into a temp git repo and expects a finding, and one that expects `files_scanned > 0`.
**Note:** the old key stays in git history. Rotation makes it harmless; rewriting history is optional (`mt5_trader.py scrub-history` exists) and not planned.

### PR 1.2: Test isolation (1 day) · Part 2 §9b, `TS` flaky, `P1-M7`
| Change | Where | Detail |
|---|---|---|
| **Stop tracking runtime `artifacts/`; keep the evidence** | `git mv artifacts data/evidence/artifacts_2026-10-03`, then add `artifacts/` back to `.gitignore` | The evidence snapshot stays in git under a read-only name that no code writes to. Runtime keeps writing to `artifacts/`, which is untracked again. **⚠ Windows machine:** pulling this commit deletes the tracked files from `artifacts/` in the working folder. Back up `artifacts/` first and copy it back after the pull (§0.1). |
| Session guard: tests must not modify tracked files | `tests/conftest.py` (session fixture) | Record `git status --porcelain --untracked-files=no` at session start; at the end, fail the session and list any tracked file that changed. This catches the next writer automatically, which matters because 63 production modules write paths under `artifacts/`. |
| Block real terminals in tests | `tests/conftest.py` (session, autouse) | Patch `sys.modules["MetaTrader5"].initialize` to return `False` with `last_error=(-10004, "tests may not attach a terminal")`, and clear `MT5_BRIDGE_KEY`, unless `LLM_TRADER_TEST_ALLOW_TERMINAL=1`. Patching the module object covers both import names (C1) and both the real Windows package and the Mac shim. |
| Fix the collection race | `intelligence/active_knowledge_agent.py:692` | Make `ACTIVE_KNOWLEDGE_AGENT` lazy (module `__getattr__`), so importing creates no DB. In `intelligence/trading_memory.py:143`, run the `ALTER TABLE` inside `BEGIN IMMEDIATE` and treat "duplicate column" as success. |
| Stray repo-root files | find the tests that create `--db`, `..tmp`, `:memory:` (a grep for `"--db"` and `":memory:"` paths written via `open()`) | Point them at `tmp_path`. |
| Windows-only tests | `tests/test_calendar_wrapper.py` | `@pytest.mark.skipif(sys.platform != "win32")` |
| Flaky test | `tests/test_p0_gates.py::test_overfit_critical_blocks_trade` | Find its ambient input (trial ledger or overfit cache under `artifacts/`) and give it a `tmp_path` fixture. Verify with `-p no:randomly -n 6` × 10 runs. |

**Acceptance:** from a fresh clone, run `pytest -n auto` twice in a row; both runs green apart from the known failures, `git status` clean, and `ls` of the repo root unchanged.

### PR 1.3: Safety pinning tests (1.5 days) · `TS-1`…`TS-7`, `P1-H6` (part)
New folder `tests/safety/`. Each file is a **table-driven** test built on the existing all-pass fixtures (`tests/test_trading_safety.py:_proposal/_market`; the perturbation table in `docs/audit/work/mutation/props/test_props.py`).

| File | What it pins | Rows (approx.) | Known-bug xfails |
|---|---|---|---|
| `test_validator_single_gate.py` | Baseline is approved; then break **exactly one** input per row, and assert `approved is False` **and** that the specific reason string is present. Covers market closed, stale tick, clock, tradable, spread, session, liquidity, microstructure, required data, entry tolerance, completed candle, stop distance, RR, event window, confluence, cooldown, changepoint, lag, walk-forward, overfit, VPIN, net-EV. | ~30 | `TS-P3` NaN changepoint · `P1-H4` missing-key defaults (one row per defaulted key: `clock_ok`, `session_allowed`, `liquidity_ok`, `microstructure_ok`, `required_data_ok`, `min_stop_distance`) |
| `test_arbiter_boundaries.py` | `PortfolioRiskArbiter.permit`: daily, weekly, drawdown, open-risk and bucket limits **at**, **ε below** and **ε above** each limit. | ~20 | `CC-F1` (pending orders not counted), unxfailed in PR 2.2 |
| `test_order_geometry.py` | `validate_order_params`: {buy, sell} × SL {above, below, equal} × TP {above, below}; the 5 % max-SL and 0.5-ATR min-SL boundaries; RR 2.0 boundary. `validate_pending_entry_geometry`: each `otype` against bid/ask. | ~40 | `TS-P5` NaN/inf accepted |
| `test_margin_thresholds.py` | `MarginSafetyGate.assess`: each constant (`margin_safety.py:21-27`) at its boundary; one row per malformed-input reason (`:124-139`). | ~16 | none |
| `test_risk_ladders.py` | `get_drawdown_multiplier` (dd 0, 4.99, 5, 9.99, 10, … 20 %), `get_streak_multiplier`, `check_weekly_circuit_breaker`, `check_daily_drawdown`, `check_active_trade_limit`, `check_drawdown`, `record_trade_outcome`. | ~35 | `TS-P2` (NaN/0/negative equity → full size) · `TS-P1` (lot rounding over cap) · `P1-H1` (local-vs-UTC reset; freeze the clock at 02:00 IST) |
| `test_latch_persistence.py` | Latch → restart → still latched; cooldown; clears only on calm market data. | ~8 | `TS-P6` (corrupt checkpoint → unlatched) · `P1-C1` (copy A writes, copy B reads) |
| `tests/property/` | Port the 7 hypothesis properties from `docs/audit/work/mutation/props/`, with failing ones as strict xfails. | 7 | P1, P2, P3, P5, P6 |
| `test_order_path_characterization.py` | **Characterization** of today's entry path on a `FakeMT5` (`tests/fakes/fake_mt5.py`, extended from the bridge test's fake): a validated proposal records exactly which `order_send` request goes out (paper and live-with-token). Phase 2 compares against this. | 4-6 | `1a-F8` (paper pyramid reaches the real `order_send`) |

**Acceptance:** rerun the mutator (`scripts/mutation/`, moved from `docs/audit/work/mutation/`) in focused mode on the six modules; the score must be ≥ 75 %. Remaining survivors get listed in the PR description as accepted or follow-up.

### PR 1.4: Tooling and CI (1 day) · Part 1 §6, `P1-M8`, `TS-8`
| Change | Detail |
|---|---|
| `requirements-dev.txt` | `pytest`, `pytest-xdist`, `pytest-cov`, `hypothesis` (test-only). |
| Deck lockfile | Add `!tui_opentui/bun.lock` to `.gitignore` (the `*.lock` pattern catches it) and commit the lockfile. |
| `.github/workflows/ci.yml` | **python** (ubuntu, Python 3.12): install deps; `PYTHONPATH=compat/mt5_bridge` for parity with the Mac shim; `pytest -n auto`; then the "tracked files unchanged" check. **frontend**: `bun install --frozen-lockfile` + `tsc --noEmit` (deck); `npm ci` + `tsc` (web GUI). **security**: the fixed scanner with `--fail-on-findings`. **import-guard**: import `mt5_trader` and `ui.gui_api`, count files loaded twice; *ratchet*: fail if the count goes **above 25** (it drops to 0 in Phase 3). |
| `.github/workflows/nightly.yml` | Focused mutation run on the six safety modules (about 1 h); fail if the score drops below the last recorded value. Too slow for every PR. |
| PR template | Checklist: tests first, suite green, `graphify update .`, roadmap box ticked. |

**Cost note:** the repo is private, so GitHub Actions minutes count against the free 2,000 min/month. One PR run is about 10-12 min and the nightly run about 60 min, so roughly 1,800 min/month at daily nightlies. Run the nightly **weekly** instead to stay well inside the quota.

---

## Phase 2: One door to the broker

**Goal:** every order of any kind (entry, close, partial close, SL/TP change, cancel, emergency exit, guardian flatten) goes through a single `OrderGateway`. No other code can reach `order_send`.

**Exit gate:**
- an AST test proves `order_send` appears only inside the gateway's adapters;
- the concurrency probe (4 threads, pending orders) never exceeds the risk cap;
- the Phase 1 characterization tests show the same broker requests on the happy path;
- the deck can't send orders;
- the API rejects unauthenticated, cross-origin and wrong-Host requests;
- all Phase 1 tests are green, and the `CC-F1` xfail is converted to a pass.

### 2.0 Design: `core/order_gateway.py` (target ≤ 300 lines)

```
                      ┌───────────────────────── OrderGateway ─────────────────────────┐
 validated proposal ─▶│ 1 preconditions  2 evidence  3 reserve (one txn)  4 margin     │
 (from validator)     │ 5 send (MT5 lock) 6 journal + audit  7 resolve intent          │
 exits / modify ─────▶│ ownership → journal → send (lock) → resolve  (no reservation)  │
                      └──────────────┬──────────────────────────────┬──────────────────┘
                                     ▼                              ▼
                           LiveMT5Adapter                     PaperAdapter
                     (wraps order_execution.*)              (wraps PaperBroker)
                     chosen ONCE at boot from the journal mode, never per call
```

**Entry pipeline** (`submit_entry(intent)`). Every step fails closed and is journalled:
1. **Preconditions:** journal mode (PAPER/DEMO/LIVE; never inferred from attributes like `data_provider.paper`); `store.is_kill_switch_active()`; autonomy level allows entries; the gateway's **`flattening` flag** is not set (`CC-F10`).
2. **Evidence:** the intent carries the validator's `ValidationResult` for **this** `setup_key`, approved and younger than N seconds. An intent without one is refused.
3. **Atomic risk reservation:** one `BEGIN IMMEDIATE` transaction on `trading_state.db` (the store and `order_intents` already share the file, via `AgentOperations(self.store.path)`). Re-check daily/weekly loss and drawdown, then **open risk = filled open trades + Σ risk of intents in `CREATED/PENDING/PARTIAL/UNRESOLVED/ERROR`**, plus the bucket risk the same way. If it's within the cap, insert the intent as `CREATED` and commit. This replaces today's separate `arbiter.permit` (`llm_orchestrator.py:9020`) and `create_order_intent` (`:9113`), which is the race in `CC-F1`. Terminal intent states release the reservation automatically.
4. **Margin preflight:** the existing `MarginSafetyGate`, called correctly (`1a-F19`: keyword arguments, test `.approved`, fail closed).
5. **Send under the MT5 lock:** one process-wide `RLock` stored **on the `MetaTrader5` module object** (`setattr(mt5, "_llm_trader_lock", …)` once). Because the module object is unique, duplicate imports of our own code (C1, fixed in Phase 3) still share it. Includes a timeout. This fixes `CC-F2` for orders; read calls take the same lock in Phase 4.
6. **Journal:** `broker_execution_reports` (already append-only), the raw trace and the compliance audit, consolidated from `llm_orchestrator.py:9134-9240`.
7. **Resolve the intent** (`PENDING`/`REJECTED`/`ERROR`, or `UNRESOLVED` on a timeout) through the existing `resolve_order_intent` state machine.

**Exit, modify and cancel pipeline** (`submit_exit`, `modify_protection`, `cancel`): ownership by magic allowlist; **allowed under the kill switch** (risk-reducing); no reservation; journal; MT5 lock. `modify_protection` enforces the *protective side + broker stop/freeze distance + only-tighten* rule, moved from `trade_manager._modify_sl:1017-1040`, which the audit verified as correct.

**Enforcement:**
- `tests/safety/test_single_order_door.py` AST-scans production code: `.order_send(` may appear only in `core/order_gateway.py`.
- A runtime guard installed at boot wraps `mt5.order_send` and raises unless called inside the gateway's context variable. That catches dynamic and unexpected paths.

### 2.1 Migration map (the 12 `order_send` sites plus the deck)
| Today | File:line | Becomes |
|---|---|---|
| `place_pending_order` (orchestrator entry) | `order_execution.py:640` ← `llm_orchestrator.py:9203` | `gateway.submit_entry` → LiveMT5Adapter / PaperAdapter |
| `place_urgency_routed_order` | `order_execution.py:398` ← `llm_orchestrator.py:9193` | `submit_entry(order_type=MARKET)` |
| `place_market_order` | `order_execution.py:287` ← `market_data_provider.py:947`, `code_safety_agent` | `submit_entry` |
| `emergency_market_exit` | `order_execution.py:568` | `submit_exit` |
| `modify_order_sltp`, `cancel_pending_order` | `order_execution.py:864, 891` ← `llm_orchestrator.py:5372` | `modify_protection`, `cancel` |
| `_execute_partial_close`, `execute_partial_close`, `_close_position` | `trade_manager.py:485, 908, 1193` | `submit_exit` |
| `_modify_sl` | `trade_manager.py:1006` | `modify_protection` |
| **`_execute_pyramid_order`** | `trade_manager.py:763` | **Disabled** behind `PYRAMIDING_ENABLED=0` (default off) until Phase 4 fixes its R basis (`1a-F9`) and gives it a validated proposal. This closes `1a-F8` immediately. |
| Guardian `_execute_flatten` | `external_guardian.py:455` | Set the gateway's `flattening` flag, then `submit_exit` for each owned position (`CC-F10`). |
| Deck MCP `mt5_order_send` | `MT5Screen.tsx:510`, `transport/mcp.ts:94` | **Removed.** `callTool` gets a client-side allowlist of read-only tools; `mt5_order_check` stays. |
| CLI `pending-order`, `modify-order`, `cancel-order` | `mt5_trader.py` | Through the gateway; `market-order` stays disabled. |

### 2.2 Work packages

**PR 2.1: Close the bypasses that don't need the gateway (1-1.5 days)**
| ID | Change | Where |
|---|---|---|
| `P1-C3` | Remove `mt5_order_send` from the palette, plus a read-only allowlist in `callTool`. | `tui_opentui/src/screens/MT5Screen.tsx:510`, `transport/mcp.ts:94` |
| `1a-F4` = `1b-R1` | `merge_bridge_into_decision` may fill numeric fields only and **never** changes `action`; replace the dead `non_live` heuristic with the journal mode. | `research/rl_action_bridge.py:230-232`, `llm_orchestrator.py:8389-8391` |
| `1a-F6` = `PI-P12` | Delete the outage → institutional-workflow promotion. | `llm_orchestrator.py:8547-8564` |
| `PI-P1` = `TL-S1` | A **read-only decision toolkit**: pass an explicit allowlist to `get_tool_schemas()` at `llm_orchestrator.py:1785`, and have `execute_tool` refuse any name outside the active toolkit. Remove `execute_dynamic_metric_analysis` from the registry (`trading_tools.py:2693`). Memory-writing tools (`ingest_quant_and_market_memory`, `sync_working_memory_state`) and `invoke_agent_skill` are also excluded from the decision toolkit. | `core/trading_tools.py:650, 2680-2760` |
| `PI-P10` | ζ review fails **closed**. | `core/skill_registry.py` (dynamic code runner) |
| `PI-P6` | The alpha-DSL check becomes an **allowlist** of AST node types and names (no attribute access, no dunders, no calls outside the operator set). | `core/alpha_discovery.py:96-101` |
| `TL-S2` | The Prometheus exporter binds `127.0.0.1` by default. | `operations/prometheus_exporter.py:271, 285` |

Tests: deck palette snapshot (no order tools); RL merge table (HOLD stays HOLD); a toolkit test (the schema list has no write/exec tools, and `execute_tool("execute_dynamic_metric_analysis")` is refused); AST-escape cases from `02_prompt_injection.md` P6 are rejected.

**PR 2.2: Gateway core (2 days)**
- `core/order_gateway.py` with the adapters, the reservation transaction and the MT5 lock.
- Unit tests, one per precondition refusal; each reservation limit at its boundary; intent state transitions.
- **Concurrency tests:** port probes P1/P2 from `work/06_concurrency.md` (4 threads × entries, pending orders present). **Invariant:** reserved + open ≤ cap in 100 % of trials. Convert the `CC-F1` xfail to a pass.
- Fix the trade lock never being acquired (`CC-F8`): replace `order_execution.py:314` with the gateway lock, then delete the dead `DistributedLock` use.

**PR 2.3: Migrate the call sites (1.5-2 days)**
- Rewire every row of §2.1.
- Add the AST "single door" test and the runtime guard.
- Pyramiding flag off by default.
- End-to-end test on `FakeMT5` through the bridge server: validated proposal → gateway → `order_send` → intent `PENDING` → fill → `FILLED`.
- Compare against the Phase 1 characterization tests; any difference must be intended and called out in the PR.

**PR 2.4: Isolate the backtest engine (0.5 day) · `1b-R2` = `LA-B4`**
- `mt5_backtest_engine` takes a required `journal_path` and refuses `trading_state.db` (and any path whose `TradingStore` mode is LIVE or DEMO).
- Remove its writes of APPROVED memories, playbook rules and lifecycle promotions. Promotion goes through the lifecycle with an operator.
- Deleting the already-leaked ticket 1,003,479,400 is a data migration in Phase 5.

**PR 2.5: Finish API authentication (1-1.5 days) · `P1-C2`**
| Piece | Design |
|---|---|
| Token | Generated at install, like `mt5_bridge.key`: `setup_mac.sh` → `~/.config/llm-trader/api.token`; Windows: `START_DECK.bat` creates `%APPDATA%\llm-trader\api.token` via `python -c "import secrets…"`. `gui_api` reads `GUI_API_TOKEN` or that file. **If there's no token, control routes refuse to start**, instead of today's "no token = open". |
| Deck | One `apiFetch()` wrapper in `transport/live.ts` adds `Authorization: Bearer`; the 35 `fetch(` sites move to it (mechanical). The WebSocket connects with the header (Bun supports `headers`). |
| Web GUI | Served by `gui_api` itself, so it uses a **`SameSite=Strict; HttpOnly` session cookie** set when the index page is served. Pages on other sites can't send or read it. 13 fetch sites, no code to read tokens. |
| Server checks | `hmac.compare_digest`; **Host** header must be `127.0.0.1:<port>` or `localhost:<port>` (blocks DNS rebinding); CORS allowlist = same origin (+ the Vite dev origin when `GUI_DEV=1`); **remove `?token=`** query auth; read routes are authenticated too (`GUI_STRICT_AUTH` defaults to on); `/api/internal/update` requires a separate **engine token** passed to the engine subprocess via its environment. |
| Approval actor | `/api/control/approve` ignores `actor` from the request body; the actor comes from the authenticated session. That closes the `operator` forgery and prevents more `operator_antigravity`-style approvals. |

Tests: extend `tests/test_gui_api_auth.py`. No token → 401 on every route, including reads and WebSockets; wrong Host → 400; cross-origin preflight denied; cookie works same-origin only; the engine token is required for internal updates; the actor can't be spoofed.

### 2.3 Phase 2 demo smoke check (optional, 1 hour, at the end)
On the Mac, with your MetaQuotes demo and **"Algo Trading" OFF** in the terminal:
1. Run paper mode and check that orders go to the PaperAdapter only. The bridge log shows **no** `order_send`.
2. Run the deck with `TRANSPORT=mcp`; the palette shows no order tools.
3. Use `curl` without a token, with a wrong Host and from another origin; all are refused.

---

## Schedule

| Day | Work |
|---|---|
| 0 | §0.1: merge PR, rotate key, back up Windows `artifacts/` |
| 1 | PR 1.1 (secrets) + start PR 1.2 (isolation) |
| 2 | Finish PR 1.2; start PR 1.3 (pinning tests) |
| 3 | PR 1.3 |
| 4 | PR 1.4 (CI) + mutation re-measure → **Phase 1 gate** |
| 5 | PR 2.1 (bypasses without the gateway) |
| 6-7 | PR 2.2 (gateway core + concurrency) |
| 8-9 | PR 2.3 (migrate the 12 sites + guard + e2e) |
| 10 | PR 2.4 (backtest isolation) + start PR 2.5 |
| 11 | Finish PR 2.5 (auth) + demo smoke → **Phase 2 gate** |

## Risks and how they're handled
| Risk | Mitigation |
|---|---|
| The refactor changes trading behaviour unintentionally | Characterization tests (PR 1.3) compared in PR 2.3; mutation ratchet; strict xfails. |
| **Pulling PR 1.2 on the Windows machine deletes the live `artifacts/`** | Back it up first (§0.1); the PR description repeats this warning. |
| Double-loaded modules (C1) aren't fixed until Phase 3 | The MT5 lock lives on the `MetaTrader5` module object; the import-guard ratchet stops new duplicates. |
| Removing LLM tools weakens decisions | Tool calls never execute today (the router turns them into text), so no capability is lost. |
| Auth breaks the deck or GUI | Both clients are updated in the same PR; the demo smoke check covers it. |
| GitHub Actions quota on a private repo | Weekly mutation run; PR runs about 12 min. |

## Decisions I need from you (recommended defaults in bold)
1. Stop tracking `artifacts/` and keep the snapshot under `data/evidence/`: **yes**.
2. Disable pyramiding until Phase 4: **yes**.
3. `execute_dynamic_metric_analysis`: **delete** (vs keep for research CLI only).
4. Web GUI auth: **same-origin cookie** (vs retiring the web GUI now).
5. CI on GitHub Actions with a **weekly** mutation run: **yes**.
