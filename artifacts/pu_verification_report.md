# Full-System Verification Report (PU-0158)

Scope: verify every autonomous component of the repo end-to-end — full test
suite, discipline/lint harness, SQLite state stores, and the autonomous
boot-watcher chassis (sessions 9/10) — then fix what was broken and re-arm
what was stuck. Marker doctrine applies: outputs are trusted only when
footed with their END-MARKER id.

Date: 2026-10-02 (runs spanned 2026-10-01 → 2026-10-02 UTC).

---

## 1. Full test suite — PASS (after 2 fixes)

392 test files, executed serially in 5 alphabetical chunks under the
project's xdist config (`-o addopts=""` for hang isolation where needed).
Evidence: [pu_fullsuite_verify.log](pu_fullsuite_verify.log).

Every chunk reached exit 0. Two failures were found and fixed:

### Fix 1 — lint harness INVENTORY gap (tests/test_from_trades_lint.py)

Foreign file `tests/test_mt5_backtest_pipeline_integration.py` (another
workstream) was unclassified in the lint harness's `_site_counts()` reader
inventory → 1 file over budget. Added a `("test", 1)` inventory entry.
Result: 4/4 green. This is a harness registration, not a behavior change.

### Fix 2 — E3 golden replay hung serially (tests/test_pipeline_v2_e3.py)

Serial run hung indefinitely on the BUY scenario. Root-caused with
`faulthandler_timeout` stack dumps + `cProfile` (evidence:
[pu_buy.prof](pu_buy.prof), [pu_buy_hang.log](pu_buy_hang.log),
[pu_buy_slow.log](pu_buy_slow.log), [pu_buy_slow2.log](pu_buy_slow2.log),
[pu_e3_hang.log](pu_e3_hang.log), [pu_e3_final.log](pu_e3_final.log)).
The fixture was building a REAL `LLMOrchestrator`, which triggered, in
order:

1. **Real Kronos torch inference** — `_kronos_advisory_snapshot`
   (intelligence/llm_orchestrator.py:7699) ran the actual model on the BUY
   scenario (the HOLD scenario passed only because it short-circuits).
2. **Real broker round-trips** — `_capture_intermarket_proxy_returns`
   (intelligence/llm_orchestrator.py:7578) calls module-level `fetch_ohlc`
   → ~5s of live network per call.
3. **Cold ML stack import** — `_apply_alpha_mining_enrichment`
   (intelligence/llm_orchestrator.py:8333) imported the sklearn/scipy
   alpha_miner stack cold (~70s).
4. **82 live HTTPS calls** — cProfile: 124.8s of the 132.7s total was
   `urlopen` from `process_cycle_data_sources` (data/data_source_pipeline.py:490)
   → `ingest_alternative_nowcast` (:405) → `fetch_nowcast_snapshot` →
   `zero_lag_macro_provider.compute_net_liquidity`. This fires **even in
   paper mode** — see §3.

Fixture hardening applied in [tests/test_pipeline_v2_e3.py](../tests/test_pipeline_v2_e3.py):
stub `orch.kronos_integrator` (canned backend_status + advisory_bundle),
stub `orch._capture_intermarket_proxy_returns`, stub
`orch._apply_alpha_mining_enrichment`, and an autouse
`_stub_external_data_pipeline` monkeypatch of
`intelligence.llm_orchestrator.process_cycle_data_sources`. All marked
`STUBBED_FOR_GOLDEN_REPLAY` so a future drift back to real I/O is visible.

Result: **18/18 pass, exit 0** under the project default xdist config
(evidence: [pu_e3_xdist.log](pu_e3_xdist.log), `XDIST-EXIT=0`) and
serially. BUY-scenario time: unbounded hang → 146s (profiled) → 80s → 74s →
**47s** after each stub layer. The post-suite exit hang also disappeared
(it was torch native threads from the real inference).

## 2. State stores — PASS

All four SQLite databases pass `PRAGMA quick_check` = `ok`, opened
read-only (`file:NAME?mode=ro`):

| DB | verdict | objects |
|---|---|---|
| trading_state.db | ok | 216 |
| agent_events.db | ok | 56 |
| agent_operations.db | ok | 95 |
| alerts_outbox.db | ok | 55 |

E3 test rows appearing in agent_events.db are expected (tests exercise the
real event emitter) and harmless.

## 3. Production finding (documented, intentionally not changed here)

`process_cycle_data_sources` runs the alternative-nowcast ingestion
unconditionally — the `include_optional_providers=(not paper_mode)` flag at
the paper-mode call site (intelligence/llm_orchestrator.py:7779) does **not**
gate `ingest_alternative_nowcast`. Paper-mode cycles therefore make real
network calls (~82 HTTPS requests/cycle observed). This is a latency/noise
problem for live paper trading and an I/O hazard for tests. Flagged for a
follow-up knob; deliberately NOT changed during this verification pass to
avoid altering runtime behavior mid-verification.

## 4. Autonomous chassis — PASS after patch; session-10 re-armed

### Session-9: fail-safe worked as designed, but produced no run

Evidence: [pu_session9_boot_stdout.txt](pu_session9_boot_stdout.txt).
Gate opened 2026-10-02T00:05Z; preflight looped ~50 polls of
"RAM pass but NO fresh M15 candle - waiting" until 03:07Z, then exited
**without booting**. Corroborated on disk: no `pu_session9_meta.json`
(meta is only written at boot), no "boot spawned" line in stdout, and the
waiter process is gone. Consequence: no session-9 report will ever land.

### Session-10: found stuck on an impossible gate (now fixed + re-armed)

`_session9_done()` in [pu_session10_boot.py](pu_session10_boot.py) required
the s9 report to exist — which §4 above proves never will. A second latent
bug: `_s9_waiter_running()` excluded `CHASSIS_TOKENS`, which contains the
token being searched for, so the scan could never return True.

Patch applied (honest gate): the slot is free when the s9 waiter AND boot
watcher are both gone AND either the s9 report exists or s9 provably wound
down without booting (no meta file + no "boot spawned" line in its stdout).
Selftest after patch: `s9_done=True`, `trader_running=False`.

Re-arm: stale watcher/waiter pids 28536/29344 killed; fresh pair armed
2026-10-02T07:32Z as **watcher 25644 / waiter 26496**. Boot log confirms
the patched gate fell through honestly —
`session-9 slot free (waiter exited; report NOT present) - preflight loop` —
and is now in the preflight loop (boot window 16h → expires ~2026-10-02T23:32Z;
run cap 5h; `PU_ABLATION=no_llm`, loops=24; report via the PU-0150 engine
with the ablation section).

Preflight bars at arm time: free RAM 0.98GB (bar 1.50GB — the loop silently
waits below the bar by design), and **no fresh M15 candle** (see §5).

## 5. Blockers and honest caveats

- **MT5 terminal is not running** (`terminal64` process count: 0). Without
  a fresh M15 candle feed the s10 preflight will refuse to boot and will
  fail-safe exactly like s9 when its 16h window expires (~23:32Z). The
  watcher is armed and harmless either way. **User action required:** start
  the MT5 terminal (and let RAM clear ≥1.5GB) for session-10 to fire.
- **Ablation pairing**: because s9 never ran, session-10's `no_llm` ablation
  has no same-condition with-LLM baseline; the PU-0150 ablation section may
  report an insufficient window. The ablation should be treated as
  exploratory until a paired run exists.
- Cerebras 402 (billing) and revoked TOKENROUTER_KEY are not load-bearing
  for the current paper chassis.
- Foreign workstream files (news triage / kill-switch / decision-models)
  carry modifications from other threads; untouched here except the lint
  INVENTORY registration their test file required.

## 6. Verdict summary

| Component | Verdict |
|---|---|
| Test suite (392 files, 5 chunks) | PASS — 2 fixes applied (lint INVENTORY, E3 stubs) |
| Discipline/lint harness | PASS (after INVENTORY fix) |
| E3 golden replay | PASS (18/18, exit 0, no real I/O) |
| SQLite state stores (4) | PASS (quick_check ok) |
| Boot-watcher chassis | PASS (s9 fail-safe correct; s10 gate patched + re-armed 07:32Z) |
| Trader slot management | PASS (no slot collisions; honest wind-down detection) |
| MT5 connectivity | **BLOCKED — terminal down, external action needed** |
| Ablation run (session-10) | ARMED — pending RAM + candle bars |
| Strategy validation stack | Previously reported: see [pu_system_benchmark_report.md](pu_system_benchmark_report.md) §8.1 (19/19 FAIL, gauntlet REJECTED) |

END-MARKER-PU-0158
