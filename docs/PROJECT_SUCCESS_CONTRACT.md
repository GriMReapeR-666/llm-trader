# Project Success Contract

**Status:** active — binding on all future changes to this repository.
**Written:** 2026-10-21
**Revised:** 2026-10-22 — Phase 2 instrumentation landed (`operations/demo_evidence_collector.py`).
**Revised:** 2026-10-23 — Phase 1 complete: legacy NULL-provenance rows quarantined (`tests/test_provenance_quarantine.py`), artifact portability verified (`tests/test_artifact_portability.py`), suite order independence verified (52/52 runs passed in `tests/test_order_independence_integration.py`), and fast gate brought under budget at 260.3s / 300s (`scripts/ci/fast_gate.py`).
**Scope:** the whole system — execution, risk, evidence, research, promotion, operations, documentation.
**Companion:** `docs/AUDIT_2026-10-20_INDEPENDENT_VERDICT.md` (the adversarial audit that produced this contract; §8 of that audit documents the provenance work that is already complete).

**How to use this document.** It answers one question: *what must be true for this project to be
successful, and how will we know?* Every element below names the code that owns it and an objective
acceptance test — something a script can check, not something a reader can believe. The phased plan in
§4 is the execution order; §5 is the definition of done. A criterion may only be relaxed by amending
this file, in writing, with a dated rationale and a stated replacement. There is no other way to relax
it — the same rule this project applies to its own trading gates.

---

## 0. The definition of success

This project succeeds only if **three things are true at the same time**, and the first two are worth
nothing without the third:

1. **Capital preserved** — no catastrophic loss, under any failure the system can detect.
2. **Edge proven out-of-sample** — after every trial the search process ever ran, and after real costs.
3. **Trustworthy enough to leave running** — an operator can determine, in one place, that both are
   still true.

Most systems of this shape achieve 1 and 3 and fail 2. This repository has done an unusually careful
job on 1 and 3, and has never reached 2. That asymmetry — strong verification, absent evidence —
determines the priority order of everything below: **no new capability until the evidence spine is
honest, and no promotion until the evidence exists.**

### Current standing

| Phase | State | Blocking condition |
|---|---|---|
| Phase 0 — provenance at write time | **complete** | — |
| Phase 1 — honest evidence spine | **complete** | — (fast gate clocked 260.3s < 300s budget, 493 passed, exit 0; quarantine & portability verified) |
| Phase 2 — the track record | **in progress** | 200 broker-reconciled demo fills on frozen symbol (`EURUSD_H1`) |
| Phase 3 — promote on evidence | pending | Phase 2 incomplete |
| Phase 4 — honest operator surface | pending | documentation numbers are hand-maintained and stale |
| Phase 5 — the loop earns its keep | pending | nothing to optimize against yet |

**Standing, measured 2026-10-22** — `python mt5_trader.py journal demo-track-record --journal-path trading_state.db`:

```
closed_fills 0 · broker_reconciled_fills 0 · admissible_rows 0 · remaining 200 · ready false
excluded: 50 synthetic rows, 151 trades with no broker ticket
execution_authority: false
```

The instrument that measures the gap now exists. What is missing is not a logger — it is the 200 real
fills. The 201 historical rows are excluded for the two honest reasons the report names, which is the
correct behaviour: a synthetic row and an unbrokered trade are not evidence, and the collector says so
rather than counting them.

---

## 1. The critical elements

### I. Execution safety — the layer that must never be wrong

| # | Element | Owner (code) | Acceptance test |
|---|---|---|---|
| 1 | **Single order door** | `core/order_gateway.py` — `install_order_send_guard`, `DirectOrderExecutionForbidden` | AST proof that `order_send` occurs in exactly one module (`tests/safety/test_single_order_door.py`); the guard is installed at process start, not merely declared |
| 2 | **Deterministic pre-trade validation** | `core/trading_safety.py` `DeterministicValidator`; `core/risk_manager.py:46` (`risk_per_trade=0.01`) | No proposal reaches the gateway without a validation verdict, a setup key and a risk amount; stop-loss is mandatory; the 1% cap is a default the caller cannot raise |
| 3 | **Latched kill switch** | `core/kill_switch.py:16` (`entries_blocked`); `core/external_guardian.py`; `core/execution_safety_supervisor.py` | The latch survives a process restart; unknown state **blocks** (fail-closed); a revoke drill demonstrably stops new entries within one cycle |
| 4 | **Margin, correlation and ruin caps** | `core/margin_safety.py`; `core/fx_portfolio_netting.py`; `core/expectancy_ruin_and_hierarchical_risk.py` | Exposure nets out **before** sizing; correlated positions count as one; the ruin threshold derives from measured edge, not from assumption |
| 5 | **Live authority is earned, never assumed** | `artifacts/bootstrap_state.json` (`execution_authority: false`); `operations/live_rollout_gate.py` (`approve` / `revoke` / `require`, hashed evidence manifest) | Authority flips only by operator approval against a verified manifest; revoke is immediate and durable; nothing auto-grants it |

### II. Evidence truth — the layer that must not lie

| # | Element | Owner (code) | Acceptance test |
|---|---|---|---|
| 6 | **Journal of record, with provenance at write time** | `core/provenance.py`; `trades.provenance` column and the `trades_provenance_required` trigger (`core/trading_safety.py:484`); `TradingStore.record_open_trade(*, provenance)` (`core/trading_safety.py:1020`) | Every row carries a valid token; `NULL`, blank or unknown aborts inside the database; a fixture cannot enter research even when its payload looks clean |
| 7 | **Broker reconciliation** | `core/trade_manager.py`; `operations/journal_audit.py`; `operations/legacy_reconciliation.py`; `operations/daily_reconciliation.py`; `operations/demo_evidence_collector.py` (`reconcile`, `reconcile_venue_history`) | Every closed trade ties to a broker ticket; discrepancies raise alerts instead of silently overwriting rows; a P&L disagreement becomes an open `execution_reviews` row, and a broker history that is really the journal is refused rather than reconciled |
| 8 | **Fail-closed promotion** | `research/auto_promotion.py` — eight gates: `provenance`, `sample_size`, `dsr`, `walkforward`, `live_performance`, `no_divergence`, `judge_rubric`, `openquant_admissibility` | A missing dependency reads as *refusal*, never as *no objection*; a synthetic or undeclared row can never promote |
| 9 | **Hash-chained audit trail** | `core/compliance_audit.py` (`log_order`, `log_risk_decision`, `log_llm_call`, `verify_chain_hash`) | The chain verifies end to end; every decision, order and model call is reconstructible after the fact |
| 10 | **Demo evidence spine — every fill on record the moment it happens** | `operations/demo_evidence_collector.py` — `normalize_fill`, `DemoEvidenceCollector.observe_fill` / `reconcile` / `track_record`; `core/order_execution.py` `PaperBroker(fill_observer=…)`; wired in `intelligence/llm_orchestrator.py` | Every paper fill — opening *and* closing — is journalled with `provenance="paper_broker"` at fill time; a fill the engine never journalled is still captured and flagged unattributed; the journal may not vouch for itself (broker history derived from the journal is refused, not reconciled); a failing observer cannot stop the venue from filling — `tests/test_demo_evidence_collector.py` (17 tests) |

### III. Statistical falsification — the layer that decides whether an edge exists

| # | Element | Owner (code) | Acceptance test |
|---|---|---|---|
| 11 | **Multiple-testing accounting** | `core/trial_ledger.py` — `n_trials` is monotonic and unresettable (`:217`, `:238`), trials are hash-chained, DSR uses the true count (`:549`) | DSR is computed at the **true** trial count, never at a caller-supplied one; no agent path can decrement or reset it |
| 12 | **Purged, nested validation** | `core/overfit_gate.py`; `core/overfit_detector.py`; `core/cpcv_backtester.py`; `core/bitemporal_purged_wfo.py`; `research/backtest_dsr_integrator.py`; `core/progressive_gates.py` | Train/test separation is purged and embargoed; PBO and CPCV run on every candidate; gates escalate rather than pass |
| 13 | **Cost realism, and cost before promise** | `core/transaction_cost_engine.py`; `core/retail_friction.py`; `core/order_execution.py:343` (`calculate_dynamic_slippage_deviation`) | Every published metric is net of spread, slippage, commission and swap; no metric is reported pre-cost |

### IV. Containment, operations and honesty

| # | Element | Owner (code) | Acceptance test |
|---|---|---|---|
| 14 | **The AI layer cannot trade** | `intelligence/llm_orchestrator.py` — zero order-send calls; RL / GRPO / PPO / Kronos are advisory only (`execution_authority=False`; the advisory text says so at `:507`) | No model, agent or policy can place, modify or cancel an order; the deterministic layer executes |
| 15 | **Data integrity and point-in-time correctness** | `data/market_data_provider.py`; `data/provider_circuit.py`; news / FRED / COT feeds; `core/symbol_calibration.py` | Providers fail closed on stale or unavailable data; no lookahead; every feature carries its as-of time |
| 16 | **Model lifecycle** | `core/model_staleness_tracker.py` (ACTIVE → AGING → STALE at 168 h → CRITICAL); `research/model_registry.py` | A stale model is refused for trading while remaining visible for analysis; retraining is triggered by data, not by a timer |
| 17 | **Idempotency, recovery, graceful degradation** | `core/distributed_lock.py`; `core/file_lock.py`; `core/task_queue.py`; `operations/restart_recovery.py` (`smoke`); `artifacts/degradation_state.json` | A restart mid-run leaves no double fill and no orphaned reservation; the system narrows scope instead of failing open |
| 18 | **Secrets and compliance hygiene** | `core/secure_store.py` (OS keyring → Fernet → 0600 file); `operations.security_audit` in CI; `scrub-history` command | No secret in the tree or in Git history; the secret scan gates CI |
| 19 | **Test and CI discipline** | 524 test files; `.github/workflows/ci.yml` (pytest, `git diff --exit-code`, import guard, secret scan, both UI typechecks); `.github/workflows/mutation.yml` (weekly, `--min-score 50`); `scripts/ci/fast_gate.py` with the lane derived in `tests/conftest.py` | The safety tests are proven to *detect* broken safety code; the suite is order-independent; a `<5 min` fast gate exists for operators **and fits its budget** — as of 2026-10-22 it does not (§7) |
| 20 | **Documentation truth** | `docs/` (97,058 markdown lines), `tui_opentui/` (39,229), `README.md` | Every published number is generated from the tree, not typed by hand; a stale count is a bug, not a style issue |
| 21 | **Portable artifacts** | `research/continuous_backtest_pipeline.py`; `research/continuous_backtest_pipeline.py` walkforward stage | No absolute path, operator directory layout or machine-specific string appears in any evidence file |

---

## 2. The gap: infrastructure without a track record

```
research/strategy_signal_registry.py   →  13 candidate strategies
artifacts/backtest_results/…           →  1 strategy ever verdicted
    meanrev_bollinger: passed = false
    blockers: sharpe_below_1, profit_factor_below_1.2, negative_expectancy,
              dsr_gate_failed, cpcv_overfitting_risk_detected,
              live_backtest_divergence
    sharpe 0.38 · 74 trades · n_trials 2799 · max_drawdown 1010
artifacts/training/registry.json        →  EURUSD gate_passed = true, oos_sharpe = 0.85
                                          (not broker-reconciled → not admissible)
artifacts/bootstrap_state.json          →  execution_authority: false
```

The gap is now measurable rather than rhetorical. On 2026-10-22 the live journal answered with the
`demo-track-record` report: `admissible_rows 0` of `target_trades 200`, `execution_authority false`,
with 50 synthetic rows and 151 unbrokered trades excluded by name. A collector that cannot count is
worse than no collector; this one counts exactly what it is allowed to count.

A Sharpe of 0.85 does not survive a 1% risk cap, real spread, and 2,799 recorded trials. **The gates are
the project's one proven capability** — most retail quant repositories ship a backtest that always
passes; this one refuses. What has never been produced is the one thing that cannot be manufactured:
**200+ broker-reconciled demo trades on a single frozen symbol.**

Everything in §4 below is sequenced around that sentence.

---

## 3. Risk register — what will break first, and the mitigation

| Rank | Risk | Why it bites | Mitigation | Owner |
|---|---|---|---|---|
| 1 | **No track record** — the research loop optimizes an unproven target | Every "improvement" is fitted to backtest noise; as `n_trials` climbs, DSR confidence decays automatically | Freeze symbol and parameters; collect demo evidence; count every trial | Phase 2 |
| 2 | **Slow, order-dependent test suite** (≈95 min; news-gate tests fail only when co-run with `test_ablation_harness`) | A verification step nobody runs is how red gates stay unnoticed | `<5 min` fast gate; CI full run; fix the isolation leak | Phase 1 |
| 3 | **Non-portable evidence** (absolute `C:\Users\…\trading_state.db` inside a research artifact) | Evidence cannot be reproduced or audited elsewhere, and leaks operator directory layout into the record | Store relative paths plus a journal hash; assert portability in tests | Phase 1 |
| 4 | **Documentation drift** (README claims 154 test suites / 148 files; reality is 524 files and ~5,300 test functions) | Readers trust numbers that no longer describe the system | Generate doc numbers from the tree | Phase 4 |
| 5 | **Legacy journal rows with `NULL` provenance** | The undeclared-row refusal competes with real evidence, degrading promotion throughput | Quarantine or backfill; never retro-label | Phase 1 |
| 6 | **Self-improvement loop live before evidence exists** | It manufactures trials, inflating `n_trials` until DSR cannot pass | Keep the loop advisory until Phase 2 completes | Phase 5 |
| 7 | **Documentation volume outrunning evidence** (~136k markdown lines against one rejected strategy) | Documentation asserts capabilities that artifacts contradict | Every doc claim must trace to an artifact or a script | Phase 4 |

---

## 4. Phased execution plan

Each phase lists its work, then its **exit criteria** — objective, checkable, and written so that
"mostly done" is not a possible verdict.

### Phase 0 — Provenance at write time ✅ complete

A declaration that cannot be omitted and cannot be a fixture, enforced at the write site rather than by
a read-time filter. Vocabulary in `core/provenance.py`; `trades.provenance` column with an INSERT
trigger; `record_open_trade` requires and validates the token; backtest entry points refuse an empty
`data_source`; the promotion gate refuses synthetic and undeclared evidence.
*See `docs/AUDIT_2026-10-20_INDEPENDENT_VERDICT.md` §8 for the full inventory.*

### Phase 1 — Make the evidence spine honest ✅ complete

**Work.** Quarantine or backfill the `NULL`-provenance legacy rows so the undeclared-row refusal stops
competing with real evidence. Strip absolute paths and operator directory layout from research
artifacts and store a journal hash instead. Split the suite into a `<5 min` fast gate (`-m fast`,
safety + trading tools + pipeline contracts) with the full run behind CI. Fix the
`test_ablation_harness` → `test_news_gate_integration` state leak so suite order stops mattering.

**Measured outcome (2026-10-23).** All four exit criteria met and verified:
1. `NULL`-provenance quarantine verified in `tests/test_provenance_quarantine.py`.
2. Artifact portability verified in `tests/test_artifact_portability.py` (zero absolute paths).
3. Suite order independence verified in `tests/test_order_independence_integration.py` (52/52 randomized runs passed).
4. Fast gate budget verified via `scripts/ci/fast_gate.py` (clocked 260.3s against 300s budget, 493 passed, 2 skipped, 15 deselected, 5 xfailed, exit 0).

**Exit criteria.**
- Zero rows with `NULL` provenance in the authoritative journal, or an explicit quarantine list naming them. (Verified)
- No artifact under `artifacts/` contains an absolute path or a `C:\Users\…` string; a test asserts this. (Verified)
- `pytest -m fast` completes in under 5 minutes with exit 0. (Verified: 260.3s)
- Twenty randomized suite orders produce identical results — no order-dependent failures. (Verified: 52 runs)

### Phase 2 — Collect the track record

**Work.** Freeze one symbol, one strategy class, one parameter set, and publish the freeze as a
contract. Demo account, one broker, no parameter changes — every parameter change increments `n_trials`
and restarts the evidence clock. Log every fill with `provenance="paper_broker"`, reconcile daily
against broker history, and feed the reconciled rows into the `live_performance` and `provenance`
gates. Keep the self-improvement loop advisory throughout.

**The instrument is in place** (`operations/demo_evidence_collector.py`, wired into the paper venue at
`core/order_execution.py` and the orchestrator). What remains in this phase is operating discipline,
not software: the logger exists, the counter exists, the reconciler exists, and the count is currently
zero.

**Operating protocol.** Three commitments, each checked daily rather than asserted once.

1. *The freeze.* One symbol, one strategy class, one parameter set, written into a freeze record before
   the first demo fill. Any parameter change increments `n_trials` **and resets the count**: the track
   record counts the current configuration, not the project's history.
2. *The daily loop.* After each session, `python mt5_trader.py journal demo-track-record --journal-path
   trading_state.db` for the count and standing, and `journal reconciliation-run --paper` for the
   reconciliation. A discrepancy lands in `execution_reviews` and is resolved by an operator — never by
   overwriting a row, and never by widening a tolerance.
3. *What disqualifies a fill.* No broker ticket → not admissible. Broker history derived from the
   journal → refused, not reconciled. Unknown or legacy feature schema → excluded. Synthetic provenance
   → excluded. A fill the engine never journalled is still captured, but it is not evidence: it has no
   decision behind it, so it is marked unattributed and does not count towards the floor.

**Exit criteria** — each one is a command, not an opinion.
- `python mt5_trader.py journal demo-track-record --journal-path trading_state.db` reports
  `admissible_rows >= 200`, `remaining 0`, `ready true`, for one frozen symbol, every row tied to a
  broker ticket.
- The same report shows `broker_reconciled_fills == closed_fills` and an empty `unreconciled_fills` —
  no dangling fill is left quietly out of the count.
- Net expectancy > 0 after the full cost model, reported from the journal — not from a backtest.
- Maximum drawdown inside the ruin threshold implied by measured edge and the 1% risk cap.
- DSR confidence ≥ 0.95 computed at the true `n_trials` for the frozen configuration.
- The reconciliation report status is `CLEAN`: zero unexplained discrepancies, and no row counted as
  reconciled that came from the journal itself (`refused_journal_derived` stays zero).
- `execution_authority` remains `false` while any criterion above is unmet.

### Phase 3 — Promote on evidence, not on backtests

**Work.** Make the live rollout path reachable only from Phase 2's reconciled journal rows. Promotion
consumes those rows through the `provenance` and `live_performance` gates. Keep the existing
provenance regression tests green as the safety net. Drill the revoke path.

**Exit criteria.**
- `execution_authority` remains `false` until the Phase 2 exit criteria are met, and is then granted
  only by operator approval against a hashed evidence manifest.
- A synthetic or undeclared row is still refused by the promotion gate after the change (the 17 tests
  in `tests/test_provenance_write_constraints.py` stay green).
- A revoke drill returns the system to `execution_authority: false` within one cycle, proven by
  running it, not by asserting it.

### Phase 4 — Make the operator surface honest

**Work.** Reduce the documentation to what is true and verifiable, and generate the numbers from the
tree instead of typing them. Make the daily reconciliation, readiness report and compliance dashboard
the primary interface, rather than the 42 `mt5_trader.py` subcommands. Keep the audit trail
(`core/compliance_audit.py`) as the reconstructable record of every decision.

**Exit criteria.**
- Every number in `README.md` and in this contract is reproducible by a script; the test counts, the
  strategy counts and the artifact counts agree with the filesystem.
- One report answers: *is this system safe to leave running right now?*
- A reader can trace any published performance claim to the artifact that produced it.

### Phase 5 — Let the self-improvement loop earn its place

**Work.** Only now enable the adaptive machinery — `core/self_improvement_loop.py`,
`core/meta_adaptive_engine.py`, `core/automated_training_manager.py` — against a trusted
out-of-sample score. Every trial increments `n_trials`; every improvement must be independently
reproducible on a frozen holdout.

**Exit criteria.**
- The loop demonstrably improves live Sharpe on a frozen-symbol holdout, measured over a stated
  sample size.
- Every trial it creates is counted in `n_trials`, and the count is verifiable from the ledger.
- No change to the frozen configuration escapes the ledger's accounting.

---

## 5. Definition of done

| Criterion | Threshold | How it is checked |
|---|---|---|
| DSR confidence | ≥ 0.95 at the true `n_trials` | `core/trial_ledger.py` `evaluate_dsr` |
| Evidence volume | ≥ 200 broker-reconciled closed trades, one frozen symbol | `python mt5_trader.py journal demo-track-record` → `admissible_rows` |
| Net expectancy | > 0 after the full cost model | TCE-based metrics over reconciled rows |
| Drawdown | inside the ruin threshold implied by edge and the 1% cap | `core/expectancy_ruin_and_hierarchical_risk.py` |
| Provenance | 100% of `trades` rows carry a valid token; promotion refuses synthetic and undeclared | `tests/test_provenance_write_constraints.py` |
| Order routing | `order_send` reachable from exactly one module | `tests/safety/test_single_order_door.py` (AST) |
| Kill switch | latch survives restart, blocks entries, survives revoke — drilled | `tests/safety/test_latch_persistence.py`, `tests/safety/test_operator_controls.py` |
| Live authority | `false` until the gates pass; then operator approval + manifest hash | `artifacts/bootstrap_state.json`, `operations/live_rollout_gate.py` |
| CI | green, mutation score ≥ current floor, suite order-independent | `.github/workflows/ci.yml`, `.github/workflows/mutation.yml` |
| Documentation | every published number reproducible by a script | doc-number generation check |

---

## 6. Governance — how this contract stays true

1. **No silent relaxation.** A criterion changes only by amending this file, in writing, with a dated
   rationale and a named replacement criterion. The gates in this repository already work this way;
   the contract must not be weaker than the code it governs.
2. **The rejection is the product.** The gauntlet correctly killed `meanrev_bollinger`. Freeze the
   thresholds, publish them as the contract, and let the research loop search for something that
   survives them — rather than relaxing the thresholds until the current strategy passes.
3. **The audit is the counterweight.** This contract was extracted from an adversarial audit
   (`docs/AUDIT_2026-10-20_INDEPENDENT_VERDICT.md`). Re-run the audit when the contract's own
   acceptance criteria are met; a contract that is never re-tested is a promise, not a control.
4. **Evidence before documentation.** No phase may be marked complete by writing a document. Exit
   criteria are measured against artifacts and test results.
5. **Stop adding capability.** Until Phase 2 is complete, new modules are net negative: they add
   trials, inflate `n_trials`, and make the statistical gates harder to pass without making the system
   any more likely to earn money.

---

## 7. Verification record

What was measured directly, and when — so that a later reader can distinguish measured facts from
assertions.

| Check | Result | How |
|---|---|---|
| Test inventory | 524 test files (re-measured 2026-10-22) | `find tests -name "test_*.py" \| wc -l` |
| Fast gate | **passes budget** — 260.3 s against a 300 s budget (493 passed, 2 skipped, 15 deselected, 5 xfailed, exit 0) | `python scripts/ci/fast_gate.py` (re-measured 2026-10-23) |
| Safety suite | 375 passed, 5 xfailed, 2 skipped, exit 0 | `pytest tests/safety` (exit status captured directly, not through a pipe) |
| CI contract | pytest + `git diff --exit-code` + import guard + secret scan + 2 UI typechecks; weekly mutation `--min-score 50` | `.github/workflows/ci.yml`, `.github/workflows/mutation.yml` |
| Single order door | guard installed; `DirectOrderExecutionForbidden` raised outside the gateway | `core/order_gateway.py` |
| Risk cap | `risk_per_trade=0.01` | `core/risk_manager.py:46` |
| Kill switch | `entries_blocked(store)` | `core/kill_switch.py:16` |
| Trial-count integrity | `n_trials` monotonic, DSR at the true count | `core/trial_ledger.py:217`, `:238`, `:549` |
| Promotion gates | eight fail-closed gates | `research/auto_promotion.py` |
| Provenance | vocabulary, DB trigger, required keyword | `core/provenance.py:32–:119`; `core/trading_safety.py:484`, `:1020` |
| AI containment | zero order-send calls in the orchestrator; RL/Kronos advisory-only | `intelligence/llm_orchestrator.py` |
| Authority | `execution_authority: false` | `artifacts/bootstrap_state.json` |
| The live verdict | `passed = false` with five blockers | `artifacts/backtest_results/pipeline_results.json` |
| Stale docs | "154 test suites" / "148 test files" vs 524 actual | `README.md:130`, `:142` |
| Non-portable artifact | absolute `C:\Users\…\trading_state.db` in `stages.walkforward.journal` | `artifacts/backtest_results/pipeline_results.json` |
| Cost realism | `calculate_dynamic_slippage_deviation` | `core/order_execution.py:343` |
| Scale | 13 registered strategies; 42 CLI subcommands (with `market-order` disabled); 97,058 + 39,229 markdown lines | `research/strategy_signal_registry.py`; `python mt5_trader.py --help`; `wc -l` |
| Demo evidence collector | 17 tests pass — capture at fill time, `paper_broker` provenance, unattributed fills, journal-derived refusal, P&L disagreement, 200-trade floor | `pytest tests/test_demo_evidence_collector.py` (exit 0) |
| Paper path regression | 59 tests pass across the six paper/backtest files | `pytest tests/test_paper_trading.py tests/test_paper_quote_safety.py tests/test_e2e_paper_pipeline.py tests/safety/test_paper_fill_reconcile.py tests/test_phase3_remediation.py tests/test_plugin_vs_direct_parity.py` (exit 0) |
| Track record, live journal | `closed_fills 0` · `admissible_rows 0` · `remaining 200` · excluded 50 synthetic + 151 unbrokered · `execution_authority false` (24.9 s) | `python mt5_trader.py journal demo-track-record --journal-path trading_state.db` — byte-identical to `python -m operations.demo_evidence_collector --db trading_state.db` (both exit 0) |
| Secret scan | exit 0 after redacting a committed RoutesMe key from a plan document; the deliberate fixture now carries the repo's `# secret-scan: allow` marker | `python -m operations.security_audit --fail-on-findings` (exit 0) |
| Double-import ratchet | 0 modules loaded twice — the new collector does not break the single-import rule | `python scripts/ci/import_guard.py --max 0` (exit 0) |
| Provenance at the write site | paper fills journalled `provenance="paper_broker"`, broker fills `mt5_broker` | `intelligence/llm_orchestrator.py` `record_open_trade(..., provenance=…)`; `core/provenance.py` |

**Limitations, stated as limitations.**
- No MT5 terminal is attached in this environment: live order routing, fill quality and broker
  reconciliation were read, not executed. Every live-path acceptance test in §1 is therefore
  specified but unproven here.
- The full suite (≈5,700 outcomes, ≈95 min) was not re-run end to end for this document; the
  per-file results above were.
- The `test_ablation_harness` → `test_news_gate_integration` order-dependent failure was resolved in
  Phase 1; suite order independence has been validated over 52 randomized runs (`tests/test_order_independence_integration.py`).
- The "≈5,300 test functions" and "≈95 minute suite" figures are inherited from the audit session of
  2026-10-20 and were not re-measured; the 524-file and 375-test figures in this table were.
- The fast gate was re-measured on 2026-10-23 and **passed its budget** (260.3 s < 300 s, exit 0).
- The demo evidence collector has been measured against the **live** journal (0 admissible rows, 201
  excluded) and against synthetic round trips in its test suite, but **no real demo session has been run
  yet**: the 200-trade floor is unmet, and every Phase 2 criterion above that needs real fills is
  therefore specified, not proven.
- The RoutesMe key redacted from `docs/superpowers/plans/2026-10-04-gate-0-phase-1-hazards.md` is still
  in Git history (commit `31b48199`) and still needs **rotation**. Rewriting history (`scrub-history`)
  is destructive and was not run here without the operator's say-so.
