# Independent Audit — 2026-10-20

**Auditor stance:** adversarial. Assume every claim is false until an artifact proves it.
**Scope:** the whole repo — execution, risk, data, research, alpha, macro, tests, docs.
**Method:** read code, read the artifacts the system itself produced, run the gates, reproduce the numbers.

**Success criteria for the project live in [`docs/PROJECT_SUCCESS_CONTRACT.md`](PROJECT_SUCCESS_CONTRACT.md)** — the critical elements with their acceptance tests, the ranked risk register, and the phased plan with objective exit criteria. This audit is its evidence base.

---

## 0. Verdict up front

| Question | Answer |
|---|---|
| Is this a serious engineering effort? | **Yes.** Top-decile for a solo project. The safety architecture is real and enforced by tests, not prose. |
| Does it have a demonstrated trading edge? | **No.** Every measured artifact says no. |
| Has the "$100 → millions" thesis been demonstrated? | **No — the opposite.** The system's own gates reject its own only strategy. |
| Is it safe to point at a live account? | **Not yet.** `execution_authority=False`, zero broker-reconciled live trades, 201 non-real rows in the authoritative journal. |
| Overall grade | **B+ engineering / D alpha.** A genuinely good verification machine attached to an unproven hypothesis. |

**Caveat that must not be lost:** the verification machine is itself not green. A full run ends
**5687 passed / 12 failed** (H5). The architecture is sound; the reconciliation between code and tests
is not. An auditor who reports “the gates work” without running the suite would have missed this.

**The honest one-liner:** this repository is an *institutional-grade refusal engine*. Its most valuable
feature is that it says "no" to its own strategy. That is genuinely rare and genuinely valuable — but it
is not a trading system with an edge, and no artifact in the repo supports the return claims the README
implies.

---

## 1. The evidence that decides the verdict

From `artifacts/backtest_results/pipeline_results.json`, the last full gauntlet run (EURUSD H1, 4000 bars,
`meanrev_bollinger`, 2026-10-19T15:52):

```
verdict.passed = false
verdict.status = "FAILED"
blockers = [ sharpe_below_1,
             profit_factor_below_1.2,
             dsr_gate_failed,
             cpcv_overfitting_risk_detected,
             walkforward_skipped ]
```

Stage detail:

| Stage | Result | Reading |
|---|---|---|
| backtest | Sharpe **0.70**, PF **1.10**, win rate **36.11%**, expectancy **+0.0628R**, 72 trades | Below every promotion bar. |
| dsr | deflated Sharpe **0.0006**, `passed=false` | `0.0006` is the DSR **confidence** (= 1 − p, the ledger's `dsr` field). Under the null the observed Sharpe 0.7 sits far below the 2811-trial luck bar, so confidence collapses to ~0. Reproduced numerically and calibrated by Monte Carlo (§7); the rejection is real. |
| cpcv | mean CPCV Sharpe **0.039**, PBO **0.333**, `is_statistically_robust=false`, grade **F** | Overfitting risk detected. |
| walkforward | `samples: 0` — needs ≥20 broker-reconciled R-multiples, found **0** | Never validated out-of-sample on real fills. |
| live_comparison | `count: 0`; excluded `synthetic_row_excluded: 50`, `unbrokered_trade: 151` | Zero verified live trades after 50 broker tickets. |
| live_validation | `overfit: REJECT / CRITICAL / F`, `walk_forward: NOT_VALIDATED` | The gate works. |

`artifacts/training/registry.json` contains exactly one entry: `EURUSD.oos_sharpe = 0.85`, `gate_passed = true`.
`artifacts/bootstrap_state.json`: `execution_authority = false`.

So the alpha story is: **one symbol, one strategy, Sharpe 0.7 in-sample, DSR 0.0006, PBO 0.33, zero live
validation.** The compounding arithmetic is not the problem — with 0.0628R expectancy at 1% risk you need
roughly 150,000 trades to move $100 to $1M. At 72 trades per 4000 H1 bars that is ~8,300 bar-years of
flawless execution. There is no path to the claim from the measured numbers.

---

## 2. Critical findings — must be fixed before any live capital

### C1. `query_zero_lag_macro` laundered hardcoded constants as "LIVE" and "tradable" — **FIXED**

The provider is scrupulously honest: `data/zero_lag_macro_provider.py` marks
`daily_cpi_mom_nowcast`, `industrial_grid_load_index`, `net_liquidity_usd_billions` and five other
inputs as `SYNTHETIC_PLACEHOLDER` (lines 161–169), and `_annotations()` stamps constants last so a live
quote can never upgrade them (lines 276–285). That is excellent work.

The tool wrapper destroyed it. `core/trading_tools.py:query_zero_lag_macro` computed `zero_lag_score`
from **only** those three SYNTHETIC fields, then returned:

```python
"source": "LIVE_ZERO_LAG_PROVIDER",
"is_tradable": True,
```

unconditionally. The docstring even claimed it was *"honest about staleness"*. This tool is exposed to the
LLM via `llm_orchestrator.get_tool_schemas()`, so the model was handed a fixed, constants-derived macro
bias every cycle, labelled live and tradable. A safety field that structurally cannot say `false` is worse
than no field at all.

**Fix applied:** `source` and `is_tradable` are now derived from the provider's own `source_quality` map.
Verified output on this machine today:

```
source = SYNTHETIC_CONSTANTS
is_tradable = False
zero_lag_score = 0.14
score_input_provenance = {'daily_cpi_mom_nowcast': 'SYNTHETIC_PLACEHOLDER',
                          'industrial_grid_load_index': 'SYNTHETIC_PLACEHOLDER',
                          'net_liquidity_usd_billions': 'SYNTHETIC_PLACEHOLDER'}
warning = zero_lag_score is computed from provider fields annotated ... -- no LIVE input.
          Treat as a constant prior, not an observation.
```

Regression test: `tests/test_trading_tools.py::test_query_zero_lag_macro_never_labels_constants_live`.

### C2. Every return and drawdown metric was inflated 100× — **FIXED**

`research/mt5_backtest_engine.py` built its equity curve from **pips** (`np.cumsum(pnls)`), then
converted to "percent" by dividing by the stop-loss size and multiplying by 100:

```python
total_return_pct      = total_pnl / sl_pips * 100     # R * 100
max_drawdown_pct      = max_dd    / sl_pips * 100     # R * 100
```

`total_pnl / sl_pips` is an R-multiple. R×100 is not a percentage of equity. Consequences:

* A +4.5R run was reported as **"452%"**.
* A 10.1R drawdown was reported as **"1010%"** — which is exactly the `1010.0` in the artifact.
* The 20%-drawdown promotion gate therefore failed *every* strategy, on a units error, not on risk.
* Calmar was fed a value 100× too large and then divided by 100 (`continuous_backtest_pipeline.py:391`
  passes `_bt["max_drawdown"]` — a percentage — into a key named `max_drawdown_pct`, so the wrongness
  round-tripped undetected).

**Fix applied:** R is converted to a percentage of equity through the system's own mandated 1% risk per
trade, and the drawdown percentage can no longer exceed 100.

Verified by running the engine end-to-end on synthetic EURUSD H1 bars:

```
trades 13   expectancy_r 0.3007
total_return_pct 3.91      (was 391.0)
max_drawdown_pct 4.07      (was 407.0)
```

13 × 0.3007R = 3.91R = 3.91% of equity at 1% risk. The numbers now agree with each other.

Regression test: `tests/test_mt5_backtest_pipeline_integration.py::test_mt5_backtest_metrics_calculation`
now asserts `0 <= max_drawdown_pct <= 100` and
`total_return_pct ≈ expectancy_r * total_trades`.

### C3. The promotion gate consumed a currency value as a percentage (same root, now moot)

`research/continuous_backtest_pipeline.py:391` maps the *drawdown* field into `metrics["max_drawdown_pct"]`
and line 550 reads it straight back out. Once C2 is fixed the value is genuinely a percentage, so the
mapping is correct-by-accident; it is nonetheless a fragile key contract that let a 500× unit error survive
five stages. **Recommended:** carry an explicit `_units` tag on every metric, or rename the fields
`total_return_r` / `max_drawdown_r` and convert once at the presentation boundary.

---

## 3. High findings

### H1. `data_source: "unknown"` on the artifact that decides promotion — **FIXED**

The backtest stage records `bars: 4000` but `data_source: "unknown"`. The repository ships
`generate_correlated_synthetic_market_data` as an LLM-callable tool. The single document that authorises a
strategy for live capital does not say whether its 4000 bars were broker ticks, a CSV, or generated noise.
**Fix:** make `data_source` a required, enum-constrained field (`BROKER / CSV / SYNTHETIC / REPLAY`) and
refuse promotion on `SYNTHETIC`. — **Implemented in §8**: the field is validated at every backtest write
site, the declaration travels on the result object, and both the pipeline verdict and the promoter refuse
an undeclared or synthetic source.

### H2. The authoritative journal contains 201 non-real rows — **FIXED**

`live_comparison` excluded `synthetic_row_excluded: 50` and `unbrokered_trade: 151`, yielding `count: 0`
usable live trades from 50 broker tickets. The *filter* is exemplary — fail-closed, explicitly named, and
it refuses to compute statistics on garbage. The *upstream* is the problem: synthetic rows are being
written into the database that the README calls "authoritative". A journal you have to filter before you
can trust it is not authoritative.
**Fix:** reject synthetic provenance at write time, not read time. A `provenance` column with a CHECK
constraint beats a downstream filter. — **Implemented in §8**: `trades.provenance` is enforced by an
INSERT trigger (the allowlist is generated from the same Python constant the writer validates against),
and the promotion gate refuses a strategy whose admissible rows include a synthetic one.

### H3. 28% of pipeline runs fail, silently

`pipeline_state.json`: `total_runs: 2351`, `successful: 1690`, `failed: 661`, and
`degradation_alerts: []`. 661 failures produced zero alerts. This is a silent-failure farm: the system
absorbs a quarter of its research as errors and reports "healthy".
**Fix:** any run that ends `FAILED` must emit a counted, categorised alert; an empty `degradation_alerts`
with a non-zero failure rate should itself be an alert.

### H4. `pytest -q` did not pass — and it failed for *two independent* reasons — **FIXED**

**(a) Stale safety characterization test.**

`tests/safety/test_order_path_characterization.py::test_live_pending_order_with_token_is_refused_by_missing_enum_today`
expected a `TradingConstraintEngine` message; the guard now raises the *stronger*
`"live pending order requires validation evidence (single-door rule)"`. The code got better and the test
did not follow, so the whole suite was red and the README's "run `pytest -q`" instruction was broken.
**Fix applied:** renamed to `..._is_refused_without_validation_evidence` and matched on `single-door rule`,
with a docstring explaining the migration. `tests/safety`: **375 passed, 2 skipped, 5 xfailed, exit 0**.

**(b) The evidence-integrity architecture lint was red in `HEAD`, independent of any audit edit.**

`tests/test_from_trades_lint.py` — the guard that exists to stop modules reading the `trades` table
directly and bypassing broker reconciliation — failed on two counts, both pre-existing
(`git show HEAD:research/continuous_backtest_pipeline.py | grep -c "FROM trades"` returns **0**, so the
failure predates this audit):

1. `_SKIP_DIRS = {..., "scratch"}` — the directory is actually named **`_scratch`**, so the skip never
   matched and throwaway restore scripts (`_scratch/HEAD_pipeline_restore.py`) were scanned and rejected
   as unclassified evidence readers.
2. `INVENTORY["research/continuous_backtest_pipeline.py"] = ("gate", 1)` while the file contains **0**
   raw readers. The test's own documented tombstone semantics require `0` for a removed reader.

This is the most ironic finding in the audit: the guard against unverified evidence reads was itself
wrong on the one file the whole promotion pipeline runs through. **Fix applied:** `"_scratch"` added to
`_SKIP_DIRS`, the stale entry corrected to `("gate", 0)`, file kept in `GATE_FILES` so the routing-marker
requirement still binds. `tests/test_from_trades_lint.py`: **4 passed**.

---

### H5. The suite ships **12 failing tests** on `HEAD` — 5687 passed / 12 failed / 17 skipped / 7 xfailed

A complete run of `tests/` (5723 outcomes) ends **red**. None of the 12 involves a file edited in this
audit; they are pre-existing drift between implementation and tests:

```
tests/test_ambient_state_sandbox.py::test_no_unregistered_runtime_state_files_at_repo_root
tests/test_bounded_autonomy_e2e.py::test_bounded_autonomy_monotone_exposure_cap
tests/test_gui_llm_providers_endpoint.py::test_agent_model_is_flagged_default
tests/test_news_gate_integration.py::test_news_gate_ablated_is_false
tests/test_news_gate_integration.py::test_news_execution_plan_present_and_clear
tests/test_news_gate_integration.py::test_macro_routed_verdicts_emitted_and_observational
tests/test_news_gate_integration.py::test_news_gate_wiring_round_trip
tests/test_overfit_gate.py::TestOverfitGate::test_validate_healthy_passes
tests/test_overfit_gate.py::TestValidateProposalOverfitGate::test_validated_passing_strategy_allows
tests/test_single_import_root.py::test_no_repository_module_loads_twice
tests/test_statistical_validation.py::TestDeflatedSharpe::test_high_sharpe_few_trials
tests/test_trial_ledger.py::TestStrategyClassScoping::test_same_sharpe_easier_bar_when_scoped
```

Re-audited standalone on 2026-10-20: **4 are now fixed** (the DSR cluster, H5c), **5 pass in isolation**
and only fail inside the full run (H5b + `test_single_import_root`) — i.e. cross-test state leakage — and
**3 are genuine, reproducible failures** (`test_ambient_state_sandbox`, `test_bounded_autonomy_e2e`,
`test_gui_llm_providers_endpoint`). Three clusters, three different verdicts:

**(a) Environment pollution (1).** `test_ambient_state_sandbox` fails because an unregistered runtime
artefact, `engine_watchdog.json`, now sits in the repo root beside `engine.pid`, `agent_events.db` and
`compliance_audit.db`. The guard is doing its job: a new ambient state file appeared that neither
`core.state_persistence.STATE_REGISTRY` nor the test's allowlist knows about. The fix is a registration
and an owning-process comment, not an allowlist entry that swallows it.

**(b) News-gate wiring (4) — order-dependent, not broken.** Re-run standalone, all four
`test_news_gate_integration` tests **pass** (verified 2026-10-20). They only fail inside the full run,
which means the suite leaks state between tests. That is worse than a plain failure: the news gate is a
*safety* gate, and a real regression in it would look exactly like this noise — “it passes when I run it
alone”. `test_single_import_root` behaves the same way (passes alone, fails in-suite): double-imported
modules from a polluted `sys.path`.

**(c) DSR / overfit-gate units contract (4).** — **RESOLVED and verified standalone, see §7.**

The DSR *maths* was correct (the artifact's `0.0006` is the confidence, not the p-value); what was broken
was the **units contract between callers and the gate**. `overfit_gate` computed `mean/std` — the raw
per-period estimator — and handed it to a ledger that assumed an annualized daily Sharpe, which
de-annualized an already-per-period value and understated z by √252 ≈ 15.9×. That single mismatch is why
a healthy 500-observation strategy was graded `REJECT / D`. The tests disagreed with each other too: one
class documented per-bar units, its neighbour assumed annualized. §7 records the decision, the full call
site alignment, and the Monte-Carlo calibration.

### H6. `deflated_sharpe_test` reports the multiple-testing bar ~4x too high — **FIXED**

`core/overfit_detector.py:123-130` computes

```python
max_expected = sqrt(2 ln N) - (ln π + ln ln N) / (2 sqrt(2 ln N))
```

which is `E[max of N standard normals]` in **σ units**, and returns it as `max_expected_sharpe`.
`core/metrics.py:190-215` already documents this exact confusion class and fixes it by scaling with
`se_null`; the detector never got the same treatment. Measured:

| case | reported `max_expected_sharpe` | correct (annualized, scaled by `se_null`) |
|---|---|---|
| SR 2.0, N=5, T=500 | **1.3425** | ≈ 0.95 |
| SR 0.7, N=2811, T=4000 | **3.5817** | ≈ 0.93 |

At N=2811 the operator is told the luck-bar is 3.58 Sharpe when it is 0.93. That is the difference between
“no strategy can ever pass” and “this one nearly does”, and it is printed into the evidence. The `p_value`
path is unaffected (it calls the corrected `core/metrics.py`), so promotion decisions are safe; the
*reported reason* is not.

**Fix applied:** one shared `core.metrics.expected_max_sharpe()` now supplies the bar to `deflated_sharpe_test`
and both trial ledgers, in the caller's own units. Measured after the fix on a synthetic EURUSD H1 run:
`raw_sharpe 3.31` vs `expected_max_sharpe 16.17` (N=2818, 13 trades) — the failure is now *visible*instead of looking almost achievable at 3.58.

The same pass replaced the closed-form Gumbel approximation with the **exact order-statistic expectation**
(∫₀^∞(1−Φ(x)^k)dx − ∫₀^∞Φ(−x)^k dx). The asymptotic form is biased high: **+50% at k=2, +15% at k=5,
+3.5% at k=100** — worst exactly where a new strategy is judged.

---

## 4. Medium findings

| # | Finding | Evidence |
|---|---|---|
| M9 | **FIXED (twice — first attempt was wrong).** The `dsr` stage paired the engine's per-**trade** annualized Sharpe with the **bar** count (72 trades reported as 4000 observations), shrinking the standard error ~7x and overstating the evidence. The stage now declares `sharpe_basis="per_trade"`, uses `sample_size=trades`, `periods_per_year=252` and reports `dsr_sample_size`. *Correction:* an earlier audit edit set the constant to 8760 on the assumption the Sharpe was per-bar; that assumption was wrong and the edit was reverted. | `continuous_backtest_pipeline.py:_stage_dsr` |
| M1 | **FIXED.** `dsr` stage labelled its trial count `n_trades` (2811) while the backtest stage said 72 trades. The *number* is right and conservative (deflating by the global ledger count is exactly right); the *label* would corrupt any per-trade statistic computed downstream. The stage now returns `n_trades = <this run's trades>` and `n_trials = <family size>`. | `continuous_backtest_pipeline.py:645`; `pipeline_results.json` → `stages.dsr` |
| M2 | Calmar is `total_R / dd_R` — a ratio of two totals with no annualisation. Dimensions cancel so it is not nonsense, but it is not Calmar. | `mt5_backtest_engine.py:618` |
| M3 | `annualized_return_pct` linearly extrapolates total return by `252 / n_days`. At 4000 H1 bars the factor is ≈1.0 so it is benign today; at 600 bars it amplifies noise ~5×. | `mt5_backtest_engine.py:657` |
| M4 | README drift: it advertises "154 test suites (730+ tests)" and "148 test files, 700+ tests". Reality: **515 test files, 5301 test functions (5723 outcomes once parametrised)**. Understating by 7× is harmless; that it drifted at all means the operator documentation is not generated from state. | `README.md` vs filesystem |
| M5 | `walkforward` stage records an absolute Windows path (`C:\Users\User\OneDrive\...\trading_state.db`) into a research artifact. Not portable, and it leaks the operator's directory layout into evidence. | `pipeline_results.json` |
| M6 | Docs-to-evidence ratio is inverted: **96,730 lines** of markdown in `docs/` plus **39,229** in `tui_opentui/`, against one strategy tested and rejected. Documentation is not a substitute for an out-of-sample track record, and at this ratio it becomes a liability — it asserts capabilities the artifacts contradict. | `find` counts |
| M7 | 5 `xfail` and 2 `skip` in `tests/safety` alone. Acceptable, but each one is an unverified safety path; they should be enumerated in the readiness report rather than absorbed. | `pytest tests/safety` |
| M8 | **The suite is too slow to be the gate the README says it is.** The README tells operators to `pytest -q` before trading. Measured end-to-end: **~95 minutes** for 5723 outcomes, with a dead zone around the 13–17% mark. A verification step that takes 1.5 hours on a developer machine is a verification step that does not get run — which is exactly how H4(b) and H5 stayed red unnoticed. Split the suite into a `<5 min` `-m fast` gate (safety + trading tools + pipeline contracts) that operators actually run, and put the rest behind CI. | measured: full run to 100% |

---

## 5. What is genuinely excellent (do not "clean up" these)

1. **Single-door order routing, proven by AST.** `tests/safety/test_single_order_door.py::test_ast_proves_order_send_only_in_order_gateway`
   parses the source to prove `order_send` appears only in `order_gateway`. That is a genuinely elite
   technique — it makes the invariant a *compile-time* property instead of a code-review promise.
2. **`market-order` is disabled at the CLI.** Direct market entry was removed rather than discouraged.
3. **`DeterministicValidator` is fail-closed**, with mandatory stop-loss and a 1% risk cap (default 0.01,
   `risk_manager.py:46`), and the pending path now demands a validation verdict + setup key + risk amount
   ("single-door rule") before it will route.
4. **DSR/CPCV/PBO gates actually reject.** The gauntlet produced five blockers and `passed=false`. Most
   retail "quant" repos ship a backtest that always passes. This one refuses.
5. **`execution_authority = False`** in bootstrap state — live authority is not granted by accident.
6. **Provenance vocabulary (P0-5)** in the macro provider, including the explicit
   `pillar_inputs_are_hardcoded_constants: True` flag and the "constants applied last" rule that stops a
   live quote laundering a hardcoded anchor.
7. **Cost realism:** `calculate_dynamic_slippage_deviation` widens deviation for IOC takers and tightens
   for makers (`order_execution.py:343`), and the backtest carries spread + slippage + commission + swap
   through a `tce_per_fill` cost model.
8. **Test depth:** 5301 test functions, with the safety suite alone at 375 passing.

---

## 6. Verification performed in this audit

| Check | Result |
|---|---|
| `python mt5_trader.py --help` | exit 0, 38 subcommands, `market-order` disabled |
| `pytest tests/safety` | **375 passed, 2 skipped, 5 xfailed**, exit 0 |
| `pytest tests/test_trading_tools.py tests/test_mt5_backtest_pipeline_integration.py` | 21 passed |
| `pytest tests/test_backtest_cost_model_cpcv.py test_edge_gates.py test_evidence_artifacts.py test_quantitative_metrics_service.py test_full_pipeline_backtest.py test_vbt_engine.py` | 73 passed, 4 skipped |
| Engine run on 1500 synthetic H1 bars | metrics now dimensionally consistent (3.91R → 3.91%) |
| `query_zero_lag_macro` | now returns `SYNTHETIC_CONSTANTS`, `is_tradable=False`, warning |
| `pytest tests/test_from_trades_lint.py` | **4 passed** (was 2 failed pre-existing) |
| `pytest test_backtest_cost_model_cpcv test_harness_full_modes` | 30 passed |
| Sweep of every test file that imports the three changed modules (21 files) | 196 passed, 1 skipped after the lint fix (was 2 failed) |
| DSR contract surface (10 files: both ledgers, overfit gate/detector, CPCV, pillars, promotion gates) | **233 passed** |
| Downstream pipeline surface (12 files: bootstrap gates, P0 gates, twin-algo, strategy registry, prompt wiring) | **158 passed** |
| `tests/test_dsr_calibration.py` (size, power, exact order statistics, unit equivalence) | **11 passed** |

**Not verified (limitations, stated as limitations):**
* The full suite **was** run to completion once: **5687 passed / 12 failed / 17 skipped / 7 xfailed**
  in ~95 min (H5). After the DSR contract work those 12 were re-run **standalone**: 4 now pass (H5c fixed),
  5 pass only outside the full run (state leakage, H5b), 3 still fail (listed in H5a/H5). The full suite
  was **not** re-run end-to-end after the fixes — that is a ~95 min job, so the post-fix stopwatch number
  is unverified; the per-test standalone evidence is what is proven here.
* No MT5 terminal is attached in this environment, so live order routing, fill quality and broker
  reconciliation were read, not executed.
* No live or demo account history exists in the repo, so no real-fill performance could be measured.

---

## 7. DSR units contract — resolved, and the gate proven calibrated

### 7.1 The decision

> **Sharpe values crossing a DSR boundary are ANNUALIZED by default.**
> `estimated_sharpe` and `benchmark_sharpe` are annualized unless the caller passes
> `annualized=False`, in which case they are the raw per-period estimator (`mean / std`).
> `periods_per_year` must be the constant that actually produced them, and is never inferred.
> `sample_size` is the observation count *behind that Sharpe, at that frequency* — trades for a
> per-trade Sharpe, bars for a per-bar one. The two are never mixed.

Rationale: every operator-facing report, every tool and the whole promotion path speak annualized
Sharpe; the raw estimator appears only where code computes `mean/std` inline. Making the default
annualized keeps the promotion path correct and forces the raw-estimator sites to say so out loud —
which is exactly what was missing.

### 7.2 Every call site, and what it now declares

| Site | Produces | Declared |
|---|---|---|
| `core/overfit_gate.py` (×2) | `mean/std` of the return series | `annualized=False` ← **was the bug** |
| `core/overfit_detector.detect_overfitting` (×2) | `_compute_sharpe(returns)` (annualization=1.0) | `annualized=False` |
| `core/overfit_detector.minimum_backtest_length` | per-observation target | `annualized=False` |
| `core/cpcv_backtester` | mean CPCV path Sharpe | `annualized=False` (already explicit) |
| `core/metrics.QuantitativeMetricsEngine` | `calculate_sharpe_ratio` (×√252) | annualized (default) |
| `research/mt5_backtest_engine` → integrator | per-trade Sharpe × √252 | annualized, `periods_per_year=252` |
| `research/continuous_backtest_pipeline._run_dsr_gate` | per-trade Sharpe × √252 | annualized, `ppy=252`, `sample_size=trades` |
| `TrialLedger` / `StrategyTrialLedger.evaluate_dsr` | caller's value | `annualized` / `periods_per_year` params added |
| `research/backtest_dsr_integrator.validate_backtest` | caller's value | `sharpe_annualized` / `periods_per_year` params added |

The contract is written at the top of `core/metrics.py` and repeated in every docstring on the path,
so a reviewer can check a call site without re-deriving the stats.

**Residual (documented, not silently patched):** `auto_quant_tools` seeds `BaselineSnapshot.sharpe_rolling`
from CPCV's *per-period* `sharpe` in one fallback branch and from an annualized Sharpe in another. Only a
soft 0–1 score consumes it, so it is not a gate input, but it is the last place where the unit is
branch-dependent.

### 7.3 Calibration — measured, not asserted

Executable as `tests/test_dsr_calibration.py` (11 tests, ~6 s). Method: draw `k` independent null strategies
of `n` iid normal per-period returns, take the **best**, annualize it, and ask the gate. That is exactly the
selection process the deflation is supposed to undo.

**Size — a gate that manufactures edges is worse than none (nominal α = 0.05):**

| trials k | 1 | 5 | 20 | 100 | 500 |
|---|---|---|---|---|---|
| false-positive rate | **0.053** | 0.010 | 0.003 | 0.003 | 0.000 |

The gate is nominal at k=1 and **conservative** for k>1 (never anti-conservative). This is inherent to the
published DSR, which scales a Gumbel-distributed maximum by the Sharpe estimator's SE: the maximum's true
dispersion shrinks with k, so the test over-covers. Conservative is the safe direction for a promotion gate.

**Power — and its price (n=500, α=0.05, one real edge of per-period Sharpe s among k trials):**

| true edge | k=1 | k=5 | k=20 | k=100 |
|---|---|---|---|---|
| 0.00 | 0.04 | 0.02 | 0.00 | 0.00 |
| 0.05 (≈0.79 annualized) | 0.27 | 0.07 | 0.02 | 0.00 |
| 0.08 (≈1.27 annualized) | 0.55 | 0.17 | 0.05 | 0.03 |
| 0.12 (≈1.90 annualized) | **0.89** | 0.45 | 0.21 | 0.04 |

Both ends are now quantified. The promotion path carries **2811–2818 global trials**, so it sits far right
in that table: an annualized Sharpe near 1.9 is detected ~4% of the time. That is not a bug in the gate —
it is the honest cost of searching 2800 hypotheses. **The lever that restores power is scoping**, and the
ledger already supports it: `strategy_class` deflates within a hypothesis class instead of the global pool.
Persisting 2811 trials in one undifferentiated family makes the gate correct and useless at the same time.

---

## 8. Data provenance — write-time constraint, implemented

H1 and H2 asked for the same thing from two directions: a declaration that cannot be omitted and cannot
be a fixture. Both are now enforced at the write site, not by a filter that reads later.

### 8.1 One vocabulary (`core/provenance.py`)

`BACKTEST_SOURCES` (`mt5_history`, `mt5_broker`, `paper_broker`, `csv_archive`, `kronos_simulated`) and
`JOURNAL_SOURCES` (`mt5_broker`, `paper_broker`, `manual`, `legacy_import`) each include
`SYNTHETIC_SOURCES`. `validate_data_source(value, kind=…)` raises on a missing, blank, or unknown value and
returns the canonical token; `is_synthetic` is the single predicate every gate uses. Honest synonyms are
resolved per vocabulary (`mt5` → `mt5_broker` in a journal, `mt5_history` in a backtest);
`synthetic(seed=7)` normalises to `synthetic`, so the seeded-generator form that the old equality check
missed is now caught.

### 8.2 Backtests cannot be constructed without a declaration

- `BacktestResult.__post_init__` (alpha harness) validates `data_source`; it is emitted by `to_dict`.
- `run_backtest`, `run_alpha_backtest_harness`, `VectorizedBacktestEngine.run_backtest` /
  `run_crossover_strategy` / `run_grid_sweep` all refuse empty or unknown declarations — `""` is not a
  default, it is a failure.
- `MT5BrokerBacktestEngine.run_crossover_strategy` / `run_strategy_backtest` /
  `run_full_autonomous_backtest` require one too; `process_journaling` validates before writing a research
  evidence row. The `research_validation_runs` payload no longer records `"unknown"`.
- The hybrid funnel stamps the source on `HybridVerdict` and adds `synthetic_data_not_authoritative` to
  its rejection reasons.
- `NewsGateBacktest` validates `BacktestResult.data_source` (its `run` labels any self-generated trade or
  event sample `synthetic` and requires a declaration for caller-supplied history; the MT5-history path
  declares `mt5_history`).
- The DSR path stamps `data_source` into the trial ledger metadata, so the backtest→ledger link carries
  provenance into promotion.

### 8.3 Journal rows declare provenance at insert

`trades` gains a `provenance` column (idempotent `ALTER` for existing journals; pre-constraint rows stay
`NULL` and are honestly *undeclared*, not silently labelled synthetic). An INSERT trigger refuses `NULL`,
blank, or unknown tokens. `TradingStore.record_open_trade(..., provenance=…)` is a required keyword that
validates first — the live/paper orchestrator passes `mt5_broker` / `paper_broker`; the demo generator
writes `synthetic_demo_generator`; the legacy importer writes `legacy_import`. `labelled_dataset` now
checks the column before the payload heuristics, so a fixture cannot enter research through a row whose
payload happens to look clean.

### 8.4 Promotion refuses synthetic evidence

`AutoPromoter._run_validation_gates` adds a `provenance` gate. It refuses when any broker-reconciled closed
row for the strategy carries a synthetic source, when a row has no declaration, and when the strategy's
most recent trial in `artifacts/trial_ledger.db` declares a synthetic `data_source`. A legacy trial with no
declaration is reported as undeclared rather than invented as a pass or a failure. Separately,
`ContinuousBacktestPipeline._compute_verdict` now blocks on `data_source_undeclared` and on the canonical
`is_synthetic` check, closing the hole where a missing key — and the `synthetic(seed=…)` string — passed the
old `== "synthetic"` test.

### 8.5 Verified

`tests/test_provenance_write_constraints.py` (17 tests) pins the contract: missing/unknown backtest
sources raise; the result and metadata carry the declaration; `record_open_trade` requires and validates
provenance; raw SQL without it is aborted by the trigger; a synthetic journal row and a synthetic trial
both refuse promotion; the pipeline verdict blocks undeclared and seeded runs. Across the 23 affected test
files — provenance, safety, journal/reconciliation, promotion, pipeline, both backtest engines, news gate,
skills and tools — the suite exits green: **140** tests in the provenance/engine/promotion batch, **101** in
the news/tools/skills/safety batch, and `test_news_gate_integration.py` separately at **4/4** (exit 0 each). The new required keyword is threaded
through every caller, proven by **40** journal-API caller tests (reconciliation, execution metrics, phase5,
portfolio netting, setup lifecycle, trading safety) and **31** memory/demo-orchestrator and e2e paper-path
tests. Exit status captured directly (`PYTEST_EXIT=0`, zero `FAILED`/`ERROR` lines), not through a pipe.

One limitation observed while verifying, and **not** caused by this work: `tests/test_ablation_harness.py`
leaks orchestrator state into `tests/test_news_gate_integration.py` when both run in one process (4 failures
in a combined run, all green standalone, and `test_ablation_harness.py` is untouched by this change). That
is a pre-existing test-isolation defect of the same family as H5b, reported rather than papered over.

**Residual, deliberate:** existing journals keep `NULL` provenance on rows written before the constraint;
those rows are excluded from promotion by the undeclared-row rule rather than retro-labelled. Two
dead/adjacent paths are reported rather than "fixed" with invented provenance:
`core/trading_tools.run_vectorized_backtest` calls a `run_fast_ema_cross_sweep` method that does not exist
anywhere in the tree, and `research/training_orchestrator.run_backtest` imports a `run_backtest` from
`research/full_pipeline_backtest.py` that module never defines (that script generates synthetic bars
inline). Both already fail before any provenance check can matter.

---

## 9. The three things that actually matter next

1. **Stop adding capability; start collecting evidence.** The repo needs 200+ broker-reconciled live
   (demo) trades on one symbol with a frozen strategy. Everything else — 5723 test outcomes, 96k lines of docs,
   134 economic dynamics — is unfalsifiable until that exists.
2. ~~**Make provenance a write-time constraint, not a read-time filter.**~~ Done — see §8. The remaining
   work is to stop *legacy* journals from being counted: backfill or quarantine their `NULL` rows so the
   undeclared-row refusal stops competing with real evidence.
3. **Treat the rejection as the product.** The gauntlet correctly killed `meanrev_bollinger`. Freeze those
   thresholds, publish them as the contract, and let the research loop search for something that survives
   them — rather than relaxing the thresholds until the current strategy passes.

---

*Audit produced from source, artifacts and executed tests on 2026-10-20. Nine defects were fixed and
re-tested during the audit (C1, C2, H4a, H4b, H6, M1, M9, the DSR units contract across 9 call sites, plus
regression and calibration tests); the rest are reported with evidence and recommended fixes.*
