# Implementation Plan: Phase 5 (Honest numbers) and Phase 6 (Demo soak)

Companion to [`ROADMAP.md`](ROADMAP.md), [`PLAN_PHASE_1_2.md`](PLAN_PHASE_1_2.md) and [`PLAN_PHASE_3_4.md`](PLAN_PHASE_3_4.md). IDs in backticks refer to the roadmap and audit reports.
Line numbers are from `phase4/live-mode-correctness` at `3e5e832` plus the uncommitted `PH4-N1` work. They will shift; the function names won't.

**Estimated effort:**
- **Phase 5:** about 12 days of focused work, including review. The roadmap said 4-5.
- **Phase 6:** about 3 days of preparation, then at least 14 calendar days of soak, then 1 day of exit review. The roadmap said 2+ weeks.

**Why Phase 5 is bigger than the roadmap says:**
- `LA-B4` was ticked in Phase 2, but only its database half was fixed.
- Planning found 9 new defects (§0.3).
- The fill fix covers five separate backtest engines.

---

## 0. Ground rules

1. **Same rules as Phases 1-4:**
   - tests first;
   - known bugs as strict xfails tagged with their roadmap ID;
   - one PR per work package;
   - reuse before new code;
   - the full suite green and `graphify update .` on every PR.
2. **Delete before repairing.** Where a module has no consumer that needs it, the plan proposes deleting it rather than fixing it (`AGENTS.md` §3). Each deletion is listed in the decisions (§ Decisions) for you to confirm.
3. **Phase 5 is mostly research code, but six changes reach the running engine.** Each one is named in its PR, and each carries the golden replay and characterization tests:
   - alpha values (`rank`/`scale`);
   - ensemble weights;
   - the metrics shown to the LLM;
   - the automated-training context in the prompt;
   - the per-strategy drawdown cap in sizing;
   - `mega_alpha_composite` in the prompt.
4. **I place no orders on any account, demo included.** In Phase 6, **you**:
   - start the engine;
   - switch Algo Trading on and off;
   - issue every flatten, kill switch, restart and kill.

   I read the journal, the broker history (through the bridge) and the logs, all **read-only**, and I write the reports.
5. **The soak tests the plumbing, not the strategy.** Ten trading days hold far too few trades to measure an edge. P&L is reported but is never an exit criterion. That is the Phase 5 rule applied to Phase 6.

### 0.1 Step 0: before Phase 5 starts
| # | Item | Why |
|---|---|---|
| 1 | **Merge `fix/phase3-review` (`e03ee35`) into Phase 4.** It isn't there today. A trial merge conflicts in 4 files:<br>• `intelligence/llm_orchestrator.py`<br>• `mt5_trader.py`<br>• `tests/fakes/fake_mt5.py`<br>• `docs/audit/REVIEW_PHASE3_2026-10-05.md`<br>`BOT_MAGICS` is already identical on both sides. | Phase 6's drills test exactly the review's B1-B3 (the deck's KILL and FLATTEN did nothing) and H4 (a flatten issued mid-cycle expired). Without this merge, the soak drills fail on known bugs. |
| 2 | Commit the `PH4-N1` work in progress: 15 modified files and `tests/safety/test_locked_broker_reads.py`. | |
| 3 | **Phase 4 review**, in the same form as the Phase 2 and 3 reviews.<br>Then the **§4.6 demo session**: its reconciliation report must show zero unexplained differences. | This is Phase 4's gate. It is also the soak's dress rehearsal. |
| 4 | Copy `/private/tmp/la_probe/p1_results.json` into `tests/fixtures/truncation_baseline.json`. It holds the audit's 62-function truncation table. | `/private/tmp` is cleared on reboot. PR 5.1 builds its test table from this file. |

### 0.2 What the code looks like today (measured for this plan)
| Area | Fact | Where |
|---|---|---|
| Alpha primitives | `rank()` ranks over the full series and `scale()` divides by the full-series sum. 46 call sites. The live path calls `combine_alphas` too. | `core/alpha_factors.py:69-83`; `data/market_data_provider.py:1570` |
| Ensemble weights | One full-sample IC mean per alpha, applied to every bar. The forward-return label is `shift(-periods)`. | `core/alpha_ensemble.py:436-440, 704-707, 760-805` |
| Hybrid backtest | **Tier 1:** winners keep their overshoot past TP, losers are capped at exactly −SL.<br>**Tier 2:** runs a fixed EMA 12/26 crossover whatever it's given.<br>Only reached from the CLI `--engine hybrid`. | `research/hybrid_backtest_pipeline.py:52-128`; `mt5_trader.py:3676` |
| Broker backtest engine | • A silent synthetic fallback with a +1 bp/bar drift; the CLI keeps its own copy of it.<br>• Short-side slippage *improves* the SL and TP exits.<br>• SL/TP are tested on closes only, and the exit is booked exactly at the barrier.<br>• Only half the spread is charged.<br>• ×√252 on per-trade returns. | `research/mt5_backtest_engine.py:348-365, 430-457, 508-513`; `mt5_trader.py:3606-3616` |
| Replay | `latency_bars=0` by default. The exit scan starts on the fill bar. Only half the spread is charged. The causal engine and the regime replay call it with entry = signal close. | `research/broker_replay.py:23, 141-169`; `intelligence/autonomous_causal_engine.py:680`; `research/historical_regime_replay.py:149` |
| Full-pipeline backtest | • Fills at `closes[i+1]`, then tests SL/TP on that same bar.<br>• `max_open_positions` and `regime_multiplier` are never read; one position at a time.<br>• The "day" resets every 288 bars whatever the timeframe. | `research/full_pipeline_backtest.py:1014, 445, 328, 686-689` |
| vbt engine | "M15" matches "M1" first. `slippage_pips` is never used. Shorts are accepted and ignored. | `research/vbt_engine.py:184-191, 94-100, 122-123` |
| Metrics shown to the LLM | `compute_summary(backtest_trials=1)`; Sharpe and Sortino default to 252 periods per year. | `core/metrics.py:774, 792, 921-951`; `intelligence/llm_orchestrator.py:4713` |
| Trial counting | Only the overfit gate and CPCV call `record_trial`. **No backtest engine records a trial.** | `core/overfit_gate.py:172`; `core/cpcv_backtester.py:101` |
| CPCV / walk-forward | `embargo_bars` is never used. "PBO" is the share of negative paths. The std is floored at 0.002. ×√252. | `core/cpcv_backtester.py:44-117` |
| Verdicts | • The continuous pipeline ignores CPCV, and a DSR error removes a blocker.<br>• Boot validation keeps only the last strategy (the assignment sits outside the loop).<br>• `auto_promotion` calls `run_walk_forward`, which doesn't exist.<br>• `setup_metrics` never computes `sharpe_ratio`.<br>• The divergence gate is hard-wired to EURUSD and passes when it has no evidence. | `research/continuous_backtest_pipeline.py:662-703`; `core/autonomous_bootstrap.py:302-335`; `research/auto_promotion.py:473, 538`; `research/research_validation.py:352-426`; `research/divergence_monitor.py:80, 309` |
| Lifecycle writes | `auto_promotion._update_strategy_status` writes with a raw `UPDATE strategy_lifecycle`, bypassing `transition()`. | `research/auto_promotion.py:591-640` |
| Registry | 103 entries: 10 `FULL` and 3 `EVALUATION` with hand-written metrics, and 90 `CANDIDATE`. `_EVIDENCE_INVALID` is set, and **no reader checks it**. | `strategy_registry.json`; `operations/strategy_factory.py:81-90` |
| Lifecycle | • A deny-list: only `FROZEN` and `RETIRED` block trading.<br>• Code setups are seeded as `DEPLOYED`.<br>• Any actor string that starts with `operator` counts as a human. | `operations/strategy_lifecycle.py:33, 122-158, 264` |
| Evidence journal | **Trades:** 151 rows: 150 `synthetic_fixture_legacy` and 1 `mt5_backtest`.<br>**Memories:** 386 `APPROVED` memories, all `TECHNICAL_BACKTEST` with an empty `reviewed_by`.<br>**Lifecycle history:** 303 transitions over 59 setups. | `data/evidence/trading_state_evidence.db` (read-only copy of the Windows journal) |
| Daily reconciliation | • Treats MT5 deal times as UTC and uses the UTC day as its window.<br>• Compares closed trades only.<br>• `llm_decisions.trade_ticket` is never written: `update_outcome` has no caller. | `operations/daily_reconciliation.py:42-70`; `llm_orchestrator.py:5498-5500`; `core/llm_decision_logger.py:62` |

### 0.3 New findings while planning
| ID | Sev | Finding | Where | Fixed in |
|---|---|---|---|---|
| `PH5-N1` | 🟠 | **`LA-B4` is only half fixed.** PR 2.4 isolated the database and removed the auto-promotion. Still open:<br>• the silent synthetic fallback with drift (also in the CLI);<br>• short slippage that improves exits;<br>• close-only barriers that truncate gap losses;<br>• half spread;<br>• commission computed but never deducted;<br>• ×√252. | see §0.2 | PR 5.3 |
| `PH5-N2` | 🟠 | **Code setups are seeded straight into `DEPLOYED`** ("seeded from registered code setup"): 7 setups in the evidence journal.<br>• This grants nothing on the trading path, because the gate is a deny-list.<br>• But every reader (deck, promotion package, readiness) sees `DEPLOYED` with no evidence.<br>• Also, `rl_qlearning_v1`, a *model* key, is `PAPER_PROVEN` in production. So `1b-R13` ⚪ did happen. | `operations/strategy_lifecycle.py:122-158` | PR 5.5 |
| `PH5-N3` | 🟠 | **Operator identity is a string prefix.** This is how `operator_antigravity` got in:<br>• `transition()` treats any actor starting with `operator` as human;<br>• the CLI takes `--operator` as free text;<br>• `strategy_factory.process_decisions` prefixes `operator_` to whatever `decidedBy` says. | `strategy_lifecycle.py:264`; `mt5_trader.py:2677-2742`; `operations/strategy_factory.py:274` | PR 5.5 |
| `PH5-N4` | 🟡 | `POST /api/control/strategy_stage` sets any registry stage, `FULL` included, with no actor, confirmation or evidence. | `ui/gui_api.py` `deck_control` → `StrategyFactory.set_stage` | PR 5.5 |
| `PH5-N5` | 🟡 latent | **The sizing drawdown cap reads the registry's hand-written `max_dd`.**<br>• The registry stores percent (for example 3.8); the code compares it with the fractions 0.20 and 0.35.<br>• No live strategy name matches a registry id today, so the cap never fires.<br>• If a name ever matched, any recorded drawdown would halt sizing. | `intelligence/llm_orchestrator.py:3321-3336` | PR 5.6 |
| `PH5-N6` | 🟡 | **No backtest engine records a trial,** so DSR's trial count misses exactly the searches that inflate Sharpe:<br>• the GA miner's ~15k candidates;<br>• alpha harness sweeps;<br>• CLI backtests. | §0.2 | PR 5.4 |
| `PH5-N7` | 🟡 | **Historical research pulls apply one server offset to every bar** (the residual of `LA-A3`; Phase 4 fixed the live half).<br>• Session-hour features are 1 h off on one side of a DST change.<br>• `capstone_validator` applies no offset at all. | `data/market_data_provider.py:236`; `core/capstone_validator.py:174` | PR 5.2 |
| `PH5-N8` | ⚪ | The full-pipeline backtest resets its "day" every 288 bars. On H1 the daily loss limit spans 12 days.<br>**Correction to `TL-T18`:** the backtest holds one position at a time. `max_open_positions` is dead, but the results are not "optimistic from concurrency"; concurrency isn't modelled at all. | `research/full_pipeline_backtest.py:686-689, 445` | PR 5.3 |
| `PH5-N9` | 🟠 | **386 backtest-written memories are `APPROVED` with no reviewer, and they are LLM prompt context.**<br>• PR 2.4 stopped new writes but never cleaned the existing rows.<br>• `playbooks` wasn't in the evidence export; check it on Windows. | `case_study_memories` (`primary_domain = TECHNICAL_BACKTEST`) | 5.9 |
| `PH5-N10` | 🟡 | `fred_backtest_data` also falls back to synthetic releases, silently, whenever FRED returns nothing or fails. | `research/fred_backtest_data.py:166-193` | PR 5.2 |
| `PH6-N1` | 🟠 | **Daily reconciliation can't be clean on a UTC+2/3 server.**<br>• It reads deal times as UTC and uses the UTC day as its window, while Phase 4 moved the journal and the risk day to broker time. Closes near the boundary then show as "missing" on one side.<br>• It reconciles closed trades only: open positions, pending orders and orphan bot-magic orders are never compared. | `operations/daily_reconciliation.py:42-70`; `llm_orchestrator.py:5491-5520` | PR 6.1 |
| `PH6-N2` | 🟡 | **On macOS the news gate depends on ForexFactory.**<br>• The MT5 calendar DLL isn't reachable through the bridge.<br>• If ForexFactory is down, every proposal is blocked as `high_impact_news`. That fail-closed behaviour is correct (Phase 3).<br>• The risk: the soak then shows zero trades and still looks "clean". | `intelligence/llm_orchestrator.py:3068`; `data/market_data_provider.py:774-796` | Day-0 check + soak report |
| `PH6-N3` | 🟠 | **An order can't be traced back to its decision.**<br>• 721 of 744 decisions have no symbol.<br>• `trade_ticket` is never written.<br>• `llm_decisions` has no proposal id, and its database path is relative to the working directory.<br>• This is pulled forward from Phase 7 (`EV`, 721/744), because Phase 6's exit needs the full trace. | `core/llm_decision_logger.py:12, 31-65`; `llm_orchestrator.py:2005` | PR 6.1 |

**Detail for the `EV` lifecycle review.** The evidence journal shows a different picture from the audit's summary:
- **`operator_antigravity`:** 51 transitions. 50 of them are **demotions**:
  - 30 × PAPER_PROVEN→VALIDATING;
  - 13 × READY→VALIDATING;
  - 7 × DEPLOYED→DECAYING.

  The **1 promotion** is `example_momentum` READY_FOR_DEPLOYMENT→DEPLOYED ("operator approved via ap-6c5301", 14 Sep).
- **`auto_promoter`:** wrote the unbacked jumps, which are more than the audit counted:
  - NULL→PAPER_PROVEN 14;
  - NULL→DEMO_PROVEN 14;
  - **NULL→READY_FOR_DEPLOYMENT 28**;
  - 104 no-op READY→READY.
- **End state** of the 59 setups:
  - 43 VALIDATING, 7 DECAYING, 6 CANDIDATE;
  - 2 PAPER_PROVEN (`default`, `rl_qlearning_v1`);
  - 1 DEPLOYED (`momentum_cascade_v1`, seeded).

So the review comes down to three setups whose current state rests on nothing, plus confirming the demotions.

---

## Phase 5: Honest numbers

**Goal:** every number that reaches the LLM, a promotion gate, sizing or the deck meets one of two conditions:
- it comes from a backtest that passes the look-ahead and fill-realism checks; or
- it is hidden, or labelled `UNVERIFIED`.

A gate that can't evaluate fails closed and says why.

**Exit gate:**
- **PR 5.1's harnesses pass with no xfail:**
  - truncation (every function used on history);
  - null strategy (every backtest engine);
  - annualization;
  - "not dead, not open" for every gate.
- **No `sqrt(252` outside the single helper** (AST test).
- **Every engine records trials,** and the metrics engine's DSR reads the ledger's count.
- **No unbacked number in the API or the deck:**
  - a registry row without evidence returns `null` metrics;
  - the deck renders `UNVERIFIED`;
  - nothing renders green by default.
- **The Windows journal is quarantined.** You review the dry-run report, then apply it. The archive hash is recorded in `docs/audit/`.
- **The lifecycle review is recorded,** and every approval needs a human confirmation.
- **Honest baseline:** the backtests for the setups you'll soak are re-run on real MT5 history and published as `docs/audit/BASELINE_HONEST.md`. **Expect worse numbers. That is the deliverable.**

### PR 5.1: The gate harnesses, tests only (1 day)
This PR defines the exit gate. The fix PRs convert its xfails.

| Harness | File | Asserts |
|---|---|---|
| **Truncation** | `tests/research/test_point_in_time.py` | `f(data[:t+1])[t] == f(data)[t]` at 4 points (tolerance 1e-9).<br>• **Rows:** the 62 functions from `truncation_baseline.json`, plus GA expression evaluation and `combine_alphas` with `causal_weights=True`.<br>• **Strict xfails** (tag `LA-B3`): today's 20 alpha and primitive failures, `rolling_ic` and `combine_alphas` ×2.<br>• **By-design rows:** the 2 centered SMC helpers (`swing_points`, `find_order_blocks.mitigated`) become an allow-list, plus a guard test that they're only called on the live endpoint frame. |
| **Null strategy** | `tests/research/test_null_strategy.py` | Fixture: the audit's `synth()` (a driftless GBM on M15, seeds 0-9) with random entries and sides, SL 1 ATR and TP 2 ATR.<br>**Zero costs:**<br>• \|mean R\| ≤ 2 SE;<br>• no exit on a bar before the fill;<br>• no `sl` exit booked at a profit;<br>• no loss smaller than −1R unless it is a gap.<br>**With spread *s*:** mean R ≈ −cost in R, within 2 SE.<br>**One row per engine,** each a strict xfail:<br>• full pipeline `LA-B1`;<br>• replay at the callers' spec `LA-B2`;<br>• `mt5_backtest_engine` `PH5-N1`;<br>• hybrid `LA-B5`;<br>• vbt `LA-B7`.<br>**Alpha harness on 12 noise alphas:** out-of-half composite IC \|t\| < 2 (`LA-B3`; probe P3 got t = 8.1). |
| **Annualization** | `tests/research/test_annualization.py` | One `periods_per_year(timeframe)`: M1 ≠ M15 ≠ M30 (probe P4). Per-trade series are never annualized. An AST scan for `sqrt(252` starts at today's count (about 8 sites) and must reach 0. |
| **Gates are neither dead nor open** | `tests/research/test_gates_alive.py` | For each gate:<br>• a synthetic **good** fixture passes;<br>• a synthetic **bad** fixture fails;<br>• **missing** evidence fails.<br>Gates covered:<br>• the 7 `auto_promotion` checks;<br>• the `research_validation` floors;<br>• the continuous-pipeline verdict;<br>• boot validation;<br>• divergence.<br>Today's dead and open rows are strict xfails (`1b-R3`, `R4`, `R6`, `R9`). |

### PR 5.2: Point-in-time features and macro data (2 days) · `LA-B3`, `LA-B6`, `PH5-N7`, `PH5-N10`
| Change | Where | Detail |
|---|---|---|
| `rank` and `scale` | `core/alpha_factors.py:69-83` | Rolling percentile rank and rolling `sum(abs)` over a fixed window equal to the live window (250 bars). Research and live then compute the same thing: no train/serve skew. |
| Ensemble weights | `core/alpha_ensemble.py` `combine_alphas` | `causal_weights=True` becomes the default.<br>• For each alpha: `ic.shift(horizon).expanding().mean()` gives a weight per bar, so bar *t* only uses IC anchors whose label ended before *t*.<br>• The composite is Σ wₜ·xₜ.<br>• At the live endpoint the weights are essentially unchanged; history becomes honest. |
| Harness | `research/alpha_backtest_harness.py` | Alphas are shifted one bar, as live does (`market_data_provider.py:1446-1451`). The fill goes through `research/fill_model.py` (PR 5.3) at the next open. |
| Vintages | `research/fred_backtest_data.py` | • ALFRED `realtime_start` gives the first-print value and its release date.<br>• `_match_event_to_releases` matches only releases with `release_time <= event_time`.<br>• No vintage → the series is marked `vintage_unknown` and excluded.<br>• The synthetic fallback becomes an explicit `synthetic=True` argument, labelled in every output (`PH5-N10`). |
| News-gate backtest | `research/news_gate_backtest.py` | • Event dates come from the release calendar instead of `2025-01-01 + whole weeks` (every event fell on a Wednesday).<br>• The direction derived from the outcome is removed.<br>• `price_before` is taken from the bar that *closes* before the print.<br>• The legacy path that attaches today's FRED values is deleted. |
| DST (`PH5-N7`) | `market_data_provider.py:236`, `capstone_validator.py:174` | Each historical bar gets the server offset in force on its own date: `broker_time` offset history, with EU DST rules for the server zone. The capstone validator uses the same helper. |

**Live impact (golden replay updated in this PR):** alphas that apply `ts_*` operators to `rank()` will show slightly different endpoint values, because their earlier ranks no longer see the rest of the window.

**Tests:** convert the `LA-B3` truncation rows and the alpha-harness null row; a DST-crossing fixture; a release matched at T−1 minute but not at T+1 minute.

### PR 5.3: One fill model for every engine (2 days) · `LA-B1`, `LA-B2`, `LA-B5`, `LA-B7`, `PH5-N1`, `TL-T18`, `PH5-N8`
**New:** `research/fill_model.py` (about 100 lines). It replaces three diverging copies.
- **Cost specification:** `ReplaySpec` from `broker_replay`.
- **Entries:**
  - a signal on bar *i* fills at `open[i+1]`;
  - bars are bid bars, so a BUY pays `ask = bid + spread`, plus slippage against the side.
- **Exits:**
  - barriers are tested against high and low, with stop-first when both are touched on one bar;
  - a gap through a barrier fills at the bar's open;
  - long exits trigger at the bid, short exits at the ask.
- **Charges:**
  - commission on both legs;
  - signed swap, including the triple day.

| Engine | Change |
|---|---|
| `full_pipeline_backtest` | • Uses `fill_model`. SL and TP are placed relative to the *actual* fill.<br>• The day boundary comes from bar timestamps on the broker day (`PH5-N8`).<br>• The dead `max_open_positions` and `regime_multiplier` are deleted, and the docstring says "one position at a time". |
| `broker_replay` | • `latency_bars` defaults to 1; 0 raises unless `allow_same_bar=True` (tests only).<br>• The exit scan starts after the fill.<br>• Full spread.<br>• `swap_per_volume_day` may be negative.<br>**Callers:** the causal engine and the regime replay submit at the next open. |
| `mt5_backtest_engine` (`PH5-N1`) | • Uses `fill_model`. Commission is deducted.<br>• **No real data → it raises `NoMarketData`.**<br>• The CLI's `--synthetic` is the only way to get synthetic bars. The result is labelled `SYNTHETIC`, is never written to the trial ledger or the lifecycle, and drift is 0.<br>• Same change in `mt5_trader.py:3606-3616`. |
| `hybrid_backtest_pipeline` | **Recommended: delete it, and `--engine hybrid`.** Its verdict feeds nothing.<br>Alternative: tier 1 with symmetric barrier accounting, and tier 2 = `broker_replay` on the same signals. |
| `vbt_engine` | `periods_per_year()` from PR 5.4; spread and slippage through `fill_model`; passing `short_entries`/`short_exits` raises until shorts are implemented. |

**Tests:** convert every null-strategy row.
- Probe P5c: a SHORT that hits a 30-pip TP with 5 pips of slippage books ≤ 25 pips.
- Probe P2: no `sl` exit at a profit.

**Follow-up in 5.9:** re-run the causal-edge replay. Edges whose `REPLAY_PASS` rested on the latency-0 artifact get demoted. That needs the new edges added in PR 5.6.

### PR 5.4: Honest statistics (2 days) · `P1-H5` (DSR, annualization, GA, bandit), `TL-T6`, `PH5-N6`, `LA-B8`, `LA-B9`, `LA-B10`
| Change | Where | Detail |
|---|---|---|
| Annualization | `core/metrics.py` | **One helper:** `periods_per_year(timeframe)`.<br>• Sharpe and Sortino take explicit `periods`, or `per_trade=True`, which means not annualized.<br>• Every `sqrt(252` site goes: `mt5_backtest_engine:508,513`, `cpcv_backtester:58`, `bayesian_walk_forward_sandbox:101`, `auto_promotion:426`, `automated_training_manager:358`.<br>• **LLM context** (`llm_orchestrator.py:4713`): it receives `sharpe_per_trade` with its basis spelled out. |
| DSR | `core/metrics.py:921`, `core/quantitative_metrics_service.py:170` | The trial count comes from `TrialLedger.n_trials_for_class`. The output carries `dsr_trials`. A ledger read error → DSR `null`, never `trials=1`. |
| Trial recording (`PH5-N6`) | `core/trial_ledger.py` | **`record_backtest_trial(engine, strategy_class, sharpe, n_obs, count=1)`** is called by each engine's entry point.<br>• A GA run records `count = candidates evaluated`.<br>• The migration adds `count INTEGER DEFAULT 1`, included in the hashed record; `n_trials_for_class` sums it.<br>• The chain stays valid: old rows count 1. |
| GA miner | `research/alpha_miner/pipeline.py`, `ga_engine.py` | **Chronological split:** train 60 / validation 20 / holdout 20, with an embargo of max look-back + label horizon.<br>• Fitness is computed on train and selection on validation.<br>• **Holdout IC is computed once and stored.**<br>• `TL-T6`: import `evaluate_with_cache` at module level; `validated_as` reflects what actually ran. |
| CPCV (`LA-B8`) | `core/cpcv_backtester.py` | **Say what it is:** path resampling of one return series.<br>• Fields renamed: `share_negative_paths` and `path_sharpe_dispersion`.<br>• **Real PBO** (Bailey CSCV) only when a trial matrix of ≥ 2 configurations × T is supplied. The GA miner and the alpha harness have one.<br>• `is_statistically_robust` is no longer derived from the fake PBO.<br>• The embargo is applied.<br>• Readers of the old key get `null`. |
| Walk-forward (`LA-B8`) | `core/walk_forward_validator.py` | **`validate(fit, evaluate, data)`:** parameters are chosen on each train fold and scored on its test fold. With no `fit`, WFE is `NOT_APPLICABLE`, not ≈ 1 by construction. |
| Labels (`LA-B9`, `LA-B10`) | `core/meta_labeling.py`, `research/baseline_research.py`, `research/supervised_training.py` | • **`LA-B9`:** high/low touches, barrier exits via `fill_model`, and average-uniqueness sample weights (AFML §4.4) returned with the labels.<br>• **`LA-B10`:** evaluation keeps non-decisive exits. Training may still drop them, but the report shows both numbers. |
| Bandit | `research/contextual_bandit.py` | **Recommended:** rename the report to `setup_take_filter`, whose text says "shrunk-mean take/skip filter, not a bandit". Remove the unused import at `llm_orchestrator.py:186`. |

**Tests:**
- The annualization harness.
- **DSR with a seeded ledger:** n = 1 vs n = 10,000 gives a different `dsr`.
- The GA holdout is never touched by selection: a spy on the evaluator.
- WFE on a curve-fit strategy is < 0.5 on a random walk.
- The uniqueness weights for two fully overlapping labels are 0.5 each.

### PR 5.5: Evidence, identity and the lifecycle (1.5 days) · `D2` = `P1-M9` (data side), `PH5-N2`, `PH5-N3`, `PH5-N4`, `EV` tooling
| Change | Where | Detail |
|---|---|---|
| Registry evidence | `operations/strategy_factory.py` `_load`/`all`; `core/autonomous_bootstrap.py:159` | **One helper:** `registry_entry_view(entry, evidence)`.<br>• When `_EVIDENCE_INVALID` is set, or an entry has no journal evidence, its metrics come back as `null` with `evidence: "UNVERIFIED"`.<br>• A one-time migration moves the hand-written metrics into `claimed_metrics`, kept but never displayed as fact, and demotes the 10 `FULL` and 3 `EVALUATION` entries to `CANDIDATE`.<br>• Every reader goes through the helper: API, metrics exporter, bootstrap, orchestrator. |
| Seeding (`PH5-N2`) | `operations/strategy_lifecycle.seed_registry` | Seeds as `CANDIDATE` with the reason "code setup, unproven". No automatic `DEPLOYED`. |
| Model keys | `research/auto_promotion.check_all_strategies` | Iterates lifecycle rows only, so a model key such as `rl_qlearning_v1` never enters the strategy lifecycle (`1b-R13`). |
| Human identity (`PH5-N3`) | `strategy_lifecycle.transition`, the CLI, `strategy_factory.process_decisions` | **`actor_kind`** is one of `human_deck`, `human_cli`, `automation`.<br>• **Operator transitions** need `human_deck` (the deck's typed confirmation, carried through the authenticated session) or `human_cli`, which means:<br>  – an interactive TTY (`sys.stdin.isatty()`);<br>  – typing the setup id;<br>  – the OS user recorded via `getpass.getuser()`.<br>• The `operator` string prefix stops meaning anything.<br>• **`AGENTS.md` gets one rule:** coding agents never run approval, promotion, live-approval or latch-clear commands. |
| Stage endpoint (`PH5-N4`) | `ui/gui_api.py` `strategy_stage` | Takes the actor from the session and requires a typed confirmation. `FULL` needs an evidence reference (the promotion package hash). |
| Quarantine tool | `operations/journal_quarantine.py` (new, about 120 lines) + `mt5_trader.py journal quarantine` | **`--dry-run`** (the default) lists:<br>• trades with `payload.synthetic` or `source` in {`synthetic_fixture_legacy`, `mt5_backtest`}, or magic 888888 in the backtest ticket range;<br>• their dependents: `trade_labels`, `trade_lifecycle_events`, `metrics_trades`, `reward_events`;<br>• `case_study_memories` with `primary_domain = TECHNICAL_BACKTEST` and no reviewer (`PH5-N9`);<br>• `playbooks` rows from backtests.<br>**`--apply --confirm <journal filename>`:**<br>• copies everything into `journal_quarantine_<date>.db` with a SHA-256 manifest;<br>• then deletes it from the live tables in one transaction;<br>• then journals an event. |
| Lifecycle review tool | `mt5_trader.py lifecycle review` | Per setup: the current state, its path, the actor kinds and the evidence references. It proposes "no evidence → `CANDIDATE`" using the existing operator rollback edges. **You apply it with the human confirmation above.** |

**Tests:**
- A registry with `_EVIDENCE_INVALID` → the API returns `null` metrics and bootstrap finds 0 active strategies.
- An actor string `operator_x` with `actor_kind=automation` → refused.
- A non-TTY CLI approval → refused.
- **Quarantine on a copy of the evidence DB:**
  - the dry run counts 151 trades and 386 memories;
  - after apply, the live tables have none and the archive's hash matches.

### PR 5.6: Verdicts that can fail and can pass (1.5 days) · `1b-R3`, `1b-R4`, `1b-R6`, `1b-R8`, `1b-R9`, `1b-R10`, `PH5-N5`
| Change | Where | Detail |
|---|---|---|
| Continuous verdict (`R3`) | `continuous_backtest_pipeline.py:662-703, 341-380, 308` | **Blockers:**<br>• any DSR status other than `completed` and passed;<br>• a missing or failed CPCV;<br>• real PBO above 0.5 when it's available.<br>**Walk-forward input:** filtered on this strategy and symbol, ordered by `closed_at`, with real folds.<br>**Removed:** the scale-only "tuning" grid and the `or 50`. |
| Boot validation (`R6`) | `core/autonomous_bootstrap.py:302-400` | • The assignment moves inside the loop.<br>• Each strategy is tested on its own symbol and timeframe; if those are unknown, it is `NOT_VALIDATED`.<br>• The key becomes `max_drawdown`.<br>• **In LIVE**, a failed backtest, missing returns or `NOT_APPLICABLE` is a blocker. In DEMO and PAPER it is reported but doesn't block.<br>• Only evidence-backed strategies count as active (PR 5.5 helper). |
| Promotion gates (`R4`) | `research/auto_promotion.py`, `research/research_validation.py` | • `setup_metrics` computes a per-trade `sharpe_ratio`.<br>• `_check_walkforward` uses the PR 5.4 API on purged journal folds and scores the out-of-sample folds.<br>• The rubric and OpenQuant gates fail with "not applicable to lifecycle setups".<br>• **Every write goes through `strategy_lifecycle.transition()`.** No raw `UPDATE`, so automation can't write `DEMO_PROVEN` once the gates come alive.<br>• `_log_promotion` runs only after a committed write (`R13`). |
| Divergence (`R9`) | `auto_promotion.py:532-546`, `divergence_monitor.py` | It passes only when the status is `completed` on ≥ N broker-reconciled trades of *this* strategy's symbols. The backtest is matched on strategy and symbol. Rows with NULL `risk_amount` are excluded. |
| Edge lifecycle (`R10`) | `research/edge_lifecycle.py:30-96` | A rejected, demoted or retired `macro_edges.status` overrides history. New edges: `STAT_PASS→STAT_FAIL` and `REPLAY_PASS→REPLAY_FAIL`. |
| Automated training (`R8`) | `core/automated_training_manager.py` | Its "OOS" result becomes `NOT_VALIDATED: trained policies are not evaluated`.<br>• Deleted: the fabricated WFE, the `+50` and the `hash()` seeds (replaced by `zlib.crc32`).<br>• **Removed from the LLM context.**<br>• Real out-of-sample evaluation of the trained policies moves to Phase 8, when training leaves the pulse (`P1-H7`). |
| Drawdown cap (`PH5-N5`) | `llm_orchestrator.py:3321-3336` | Reads realized per-strategy drawdown from the journal (`operations/analytics` `per_strategy`) as a fraction. No journal history → no cap, and it says so. |

**Tests:** convert the `test_gates_alive.py` xfails.
- A divergence check with no trades → fails.
- A `STAT_PASS` edge demoted by status → reports demoted.
- Bootstrap with 3 strategies → 3 results.
- Automation can't reach `DEMO_PROVEN` even with every gate green.

### PR 5.7: Research writers and the LLM context (1 day) · `1b-R5`, `1b-R11`
| Change | Detail |
|---|---|
| One RL writer (`R5`) | **Owner:** the orchestrator's `RLTrainer` is the only one. The periodic retrain in `mt5_trader.py:5314-5404` calls it instead of building a second trainer.<br>• **Applied trades:** a `rl_applied_tickets` table, so each trade is applied once.<br>• **Simulator training:** writes `rl_model_sim.json` and is never registered as broker-trained.<br>• **Registry:** the version is the SHA-256 of the artifact bytes and `training_data_hash` hashes the applied-ticket list. Registry failures are logged at WARNING. |
| Alpha context (`R11`) | **Recommended: drop `mega_alpha_composite` from the prompt** (`llm_orchestrator.py:460, 5189-5194`). It is a mean of in-sample IC, not a signal.<br>• The decay check reads `id` and a stored `ic_history`.<br>• The `strategy_bridge` writes only through `factory.upsert`, with an absolute path and no private `_persist_locked`. |

**Tests:**
- 3 retrains over the same 50 tickets apply them once.
- After an engine restart, registration doesn't hit the UNIQUE constraint.
- The prompt contains no `mega_alpha_composite`.
- Killing the bridge mid-write leaves the registry intact.

### PR 5.8: The deck shows only what is known (1 day) · `D2` (display), `D3`, `D4`
**The rule from the safety strip applies to every screen:** unknown renders `UNKNOWN`/`UNVERIFIED` in amber, and nothing is green by default.

| Finding | Change |
|---|---|
| `D2` | **Positions:** "N ENGINES ACTIVE" counts strategies with evidence that produced a decision in the last hour.<br>**Strategies:** "● live" requires both.<br>**Registry metrics:** `—` with an `UNVERIFIED` badge.<br>**Observatory:** profitability charts show an empty state that says "no broker-reconciled trades". |
| `D3` | `GET /api/security-full` (`ui/gui_api.py:3567-3606`):<br>• `audit()` returns `files_scanned`;<br>• `clean` only when `files_scanned > 0` and there are no findings;<br>• `gitScrubStatus` and `journalScope` are `null` unless they were actually computed (both default to "clean"/"LIVE" today). |
| `D4` | **Alpha Lab:** a missing health status is `UNKNOWN`, not `HEALTHY` (`AlphaScreen.tsx:56`).<br>**Pipeline Graph:** the hard-coded "100% HEALTHY" and latency figures are deleted (`GraphScreen.tsx:1161, 1518`). |

**Tests:** API field tests (zero files → not clean; invalid registry → null), `bun test` snapshots, `tsc`.

### 5.9 Phase 5 gate (1 day, plus your time on Windows)
1. The full suite, all four harnesses green, the AST scan at 0.
2. **You,** on the Windows journal:
   - run `journal quarantine --dry-run` and review the counts;
   - expect 151 trades and 386 memories, plus whatever has been written since 3 Oct;
   - then `--apply`.

   **I** record the archive manifest hash in `docs/audit/`.
3. **You** run `lifecycle review` and apply the demotions with a human confirmation.
4. Re-run the causal-edge replay with the new fill model, and demote the edges that flip.
5. **Honest baseline.** With the terminal up, I fetch history read-only through the bridge, re-run the backtests for the setups you'll soak, and write `docs/audit/BASELINE_HONEST.md`. For each setup it reports:
   - trades;
   - expectancy R after costs, with its SE;
   - per-trade Sharpe;
   - DSR and its trial count;
   - holdout results.

   **Setups with no edge are reported as such.** That gates live money; it doesn't gate the soak.

---

## Phase 6: Demo soak

**Goal:** prove the real loop on a real broker over weeks:
- proposal → decision → order → fill → management → close → reconciliation;
- including restarts, outages and emergency controls;
- with **zero unexplained differences** between the journal and the broker.

**Live money only after this.**

### 6.0 Entry criteria
- [ ] Step 0 done: the Phase 3 review fixes are merged, Phase 4 is reviewed, and the §4.6 session's report is clean.
- [ ] Phase 5 merged (gate 5.9). Exception: the uncounted warm-up (§6.4).
- [ ] PRs 6.1-6.3 merged.
- [ ] The **soak tag** is cut (`soak-YYYYMMDD`). Engine code is frozen to that tag (rule in §6.6).

### PR 6.1: Reconciliation that can say "zero unexplained" (1.5 days) · `PH6-N1`, `PH6-N3`
| Change | Where | Detail |
|---|---|---|
| Broker time | `operations/daily_reconciliation._deal_time`, `llm_orchestrator.reconcile_daily_broker_history` | Deal times go through `broker_epoch_to_utc`. The window is the **broker trading day** (`core.broker_time.broker_trading_day`), the same day the risk limit uses. |
| Full scope | `compare_day` | Adds three comparisons:<br>• **open positions:** owned broker positions vs open journal trades, mapped through the execution ledger;<br>• **pending orders:** owned broker orders vs `PENDING` intents;<br>• **orphans:** bot-magic orders or deals with no intent.<br>Also flags `UNRESOLVED`/`CREATED` intents older than 1 h. |
| Explained ≠ hidden | `reconciliation_annotations` table (day, item key, cause, evidence reference, actor, time) | A discrepancy counts as **explained** only with an annotation that names its cause and evidence. Day status: `CLEAN` \| `EXPLAINED` \| `ATTENTION`. |
| Decision link (`PH6-N3`) | `core/llm_decision_logger.py`, `llm_orchestrator.py:2005` | • `log_decision` records symbol, timeframe, `run_id` and the proposal's `setup_key`.<br>• The decision id goes onto the proposal features and the intent payload.<br>• At fill, `update_outcome(decision_id, outcome, ticket)` is called.<br>• The logger uses the journal path (`LLM_TRADER_JOURNAL_PATH`), not the working directory. |
| Trace | `mt5_trader.py soak trace --day D` | One row per order: proposal → decision → intent → broker order → deal(s) → close, with each hop shown as present or `MISSING`. |

**Tests (`FakeMT5` with UTC+3):**
- a close at 23:30 server time lands on the right day;
- an open position with no journal row → `ATTENTION`;
- an annotated item → `EXPLAINED`;
- an orphan bot-magic order is flagged;
- a full trace on the Phase 4 lifecycle fixture has no `MISSING`.

### PR 6.2: Soak report and drill records (1 day)
**Reuse:** PR 6.1, `/api/safety`, `operations/journal_backup.py` and `operations/live_rollout_gate.py`. **No new UI.**

| Command | Output |
|---|---|
| `soak report --day D` | `artifacts/soak/<tag>/<day>.{json,md}`. It runs as a scheduled job 15 min after the broker day ends (only when `LLM_TRADER_SOAK=1`), and it can be run by hand. It contains:<br>• the reconciliation status and items;<br>• proposals per day and rejections by reason;<br>• **`calendar_source` coverage** (`PH6-N2`);<br>• decisions, `deterministic_fallback` count, LLM error rate and p90 latency;<br>• intents by status;<br>• orders, fills, and slippage and spread compared with the quote at intent;<br>• breaker state and **daily-limit resets** (there should be one, at broker midnight);<br>• latch and kill-switch events;<br>• engine restarts;<br>• guardian heartbeat misses;<br>• RSS and database sizes;<br>• the coverage matrix (§6.5). |
| `soak drill --id D3 --phase before\|after` | A snapshot of positions, orders, intents, controls, the safety strip and the breaker, written to `artifacts/soak/<tag>/drills/`. Each drill has a few expected predicates in a dict; `after` reports PASS or FAIL against them. |
| `soak status` | The day counter, the clean streak, the coverage matrix and the drills done. |
| backup | `journal_backup` of the soak journal, daily, encrypted. |

### PR 6.3: Soak host on the Mac (0.5 day)
`scripts/mac/soak.sh` and `docs/SOAK_RUNBOOK.md`:
- runs the engine under `caffeinate -dimsu`;
- a bridge health check every 60 s (`mt5_wine.sh ensure`) that logs each restart;
- `LLM_TRADER_JOURNAL_PATH` points to a **fresh soak journal**, so every row in it is soak evidence;
- log rotation and a free-disk check;
- the runbook holds the drill steps from §6.3, written for you to follow.

### 6.1 Day 0 readiness (you and me, about 1 h)
- [ ] **Versions:** tag, `pip freeze` hash, MT5 build and bridge version, recorded in the soak folder.
- [ ] **Account:** MetaQuotes-Demo. The engine logs `trade_mode == DEMO` (the Phase 2 token) and the server's UTC offset.
- [ ] **`calendar_source == configured_calendar`:** ForexFactory is reachable (`PH6-N2`). Otherwise every proposal holds.
- [ ] **Controls:** the news latch is clear, the kill switch is off, `LLM_TRADER_LIVE_ENTRIES` is unset, and no field on the safety strip is `UNKNOWN`.
- [ ] **Mac:** on mains power, sleep off, OS updates deferred, at least 20 GB free.
- [ ] **Drill D0 runs first** (below).

### 6.2 Configuration (recommended; decision 12)
| | Week 1 | Week 2 |
|---|---|---|
| Symbols | EURUSD | + XAUUSD (different point, digits and contract size; this exercises sizing and the spread guard) |
| Strategies | swing + scalp (the default), so at least two magics | same |
| Risk | 0.25 % per trade, with real sizing so the sizing path runs | same |
| Limits | max 2 open positions; daily and weekly limits as configured | same |
| Pyramiding | off (Phase 4 decision 7) | off |

### 6.3 Drills
**You run each drill. I capture the evidence.** Each drill is `soak drill --id … --phase before`, then your action, then `--phase after`.

| ID | Drill | When | Pass |
|---|---|---|---|
| D0 | Start with Algo Trading **OFF** | Day 0 | Entries are refused (`AUTOTRADING_DISABLED`), intents end `REJECTED`, there is no retry storm, and no intent stays `UNRESOLVED`. |
| D1 | Deck **FLATTEN** while idle: ≥ 2 positions on ≥ 2 magics, plus 1 pending order | Week 1 | `APPLIED {closed n, cancelled m}` within 5 s. The *broker* snapshot shows 0 remaining. Next day's reconciliation is `CLEAN`. |
| D2 | **FLATTEN mid-cycle**, issued while an LLM call is in flight | Week 1 | Applied within 2 s (control thread, review H4). The in-flight entry is refused by `flattening`. Nothing expires. |
| D3 | **Kill switch** with a pending entry and an open position | Week 1 | `ACKNOWLEDGED` at once.<br>• The pending order is cancelled (`APPLIED`).<br>• The open position keeps its SL/TP and its trailing.<br>• The next proposal is refused with "kill switch".<br>• Turning it off needs the typed confirmation. |
| D4 | **Restart** with an open position and a pending order (stop flag, then start) | Week 1 | No duplicate order. Reservations match the broker. Breakeven and trailing continue from the *initial* R (`1a-F9`). |
| D5 | **Flatten after a restart** (positions opened before it) | Week 1 | As D1. This proves ownership by magic, not by session. |
| D6 | **Restart inside 00:00-05:30 IST,** after a realized loss | Week 2 | Broker midnight is 02:30 IST on a UTC+3 server and 03:30 IST on UTC+2.<br>• Restart at about 01:30 IST: daily P&L and the breaker are unchanged.<br>• Restart about 1 h after broker midnight: exactly one reset, at broker midnight.<br>• Also one restart around 14:00 IST.<br>(The evidence logs had 37 resets in 11 days.) |
| D7 | **LLM outage:** engine restarted with `HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost` for 1 h in an active session | Week 2 | Every decision is `deterministic_fallback` HOLD, 0 intents, the RL bridge is suppressed, and the safety strip shows the LLM circuit open. Recovery after the proxy is removed.<br>The proxy also cuts the news feeds. **The proof is the `deterministic_fallback` decisions**, not the HOLD count. |
| D8 | **Bridge loss:** bridge stopped for 5 min with a position open | Week 2 | Entries hold on a stale tick and the bridge status. No crash loop. The position keeps its broker-side SL/TP. After reconnecting, a close that happened in the gap is reconciled exactly once. |
| D9 | **Crash:** `kill -9` mid-cycle, then start | Week 2 | The engine lock is reacquired. `CREATED` intents are swept against the broker (Phase 2 D4). `ACKNOWLEDGED` controls become `FAILED` (review H4). No duplicate order. |
| D10 | **Weekend crossing** | Weekend 1 | The engine idles without errors while the market is closed. Positions keep SL/TP. Monday's reconciliation is `CLEAN`. |
| D11 | **Offline flatten CLI** with the engine stopped | Week 2 | Refused while the engine holds the lock. Asks for the account login as confirmation. Owned positions only. Next reconciliation is `CLEAN`. |
| D12 | **DST change** (natural event, observed) | **25 Oct** (EU) and **1 Nov** (US), 2026 | The server offset is re-probed, the broker-day boundary moves once, there is no spurious reset, and time stops stay correct. If the counted soak misses both dates, the warm-up (§6.4) catches 25 Oct. |

**What the roadmap listed, and what I added:**
- **From the roadmap:** daily reconciliation (6.1-6.2); flatten and kill switch during a cycle and after a restart (D1-D5, D11); restarts inside and outside the window (D6); LLM outage (D7); the end-to-end trace (6.1).
- **Added:**
  - D0, because a terminal with Algo Trading off is the most likely real failure;
  - D8 and D9, because on a Mac the bridge and Wine are new failure points;
  - D10 and D12, because a 2-week window crosses at least one weekend, and likely a DST change.
- **The news latch** was drilled in CI in Phase 3. In the soak it is observe-only: any real trip is recorded as true or false, which feeds Phase 7 (`PI-P9`).

### 6.4 Calendar
| Stage | What | Counts toward exit |
|---|---|---|
| **Warm-up** (recommended, decision 13) | Starts as soon as Step 0 is done, on the Phase 4 build, and runs while Phase 5 is built. EURUSD only, D0 and D1.<br>It surfaces Mac, Wine, bridge, calendar and reconciliation problems early; those don't depend on Phase 5.<br>It also catches the 25 Oct DST change if the counted soak starts later. | No |
| **Counted week 1** | EURUSD; D1-D5 at different sessions (Asian, London open, New York overlap); D10 at the weekend. | Yes |
| **Counted week 2** | Adds XAUUSD; D6-D9, D11. | Yes |
| **Days 11-14+** | Run only, no drills. | Yes |
| **Exit review** | Once §6.5 holds. | |

**Daily (you, about 10 min):**
- read the report;
- annotate any item, with its cause and evidence;
- glance at the terminal.

**Weekly (me):** a summary, with trends in slippage, LLM latency and rejection reasons.

### 6.5 Exit criteria: the gate for live money
1. **At least 14 calendar days.** The **last 10 consecutive trading days** are `CLEAN` or `EXPLAINED` on the final tag, with no `ATTENTION` day.
2. **Coverage matrix complete,** each item at least once:
   - a pending entry filled;
   - a pending entry expired or cancelled;
   - an SL hit and a TP hit;
   - a breakeven move and a trailing move;
   - a time stop;
   - a trade held across a broker day boundary;
   - a flatten close.

   **And at least 20 closed bot trades.** If the natural rate is too low, **extend the soak**: study the rejection reasons, but never loosen a safety gate to reach the count.
3. **Every drill D0-D11 passed,** with its evidence file present. D12 was observed, or covered by the warm-up.
4. **Trace:** 100 % of orders traced through every hop. No orphan bot-magic order or deal, no duplicate order, and no intent `UNRESOLVED` for more than 1 h at day end.
5. **Daily limit:** no reset inside a broker day, across every restart.
6. **LLM outage:** 0 intents during D7.
7. **Costs calibrated.** Measured spread and slippage per symbol are compared with `ReplaySpec`'s defaults. If the real costs are worse:
   - update the defaults;
   - re-run the honest baseline (5.9 step 5), which closes the loop with Phase 5.
8. **Operations:** no crash loop. Every guardian heartbeat miss is explained. RSS and disk stay within the runbook's bounds.
9. **Evidence package.** `artifacts/soak/<tag>/MANIFEST.json` lists every report, drill record and backup hash. These are the file references you pass to `journal live-approval approve`. That command already:
   - hashes each file;
   - caps tiny-live risk at 0.1 %;
   - expires the approval within 168 h.

   **Live money remains your call,** and it also needs `LLM_TRADER_LIVE_ENTRIES=1` (Phase 4).

**P&L is reported, never gated.** With 20 trades and σ ≈ 1.2R, the standard error of mean R is about 0.27R, far too wide to tell an edge from none.

### 6.6 Freeze rule
- Any change to engine code (anything the engine process imports) resets the clean-day counter to 0 from the day it is deployed.
- Deck, docs and research CLIs that the engine doesn't import can change freely.
- A fix for a defect found in the soak is welcome. The counter restarts after it.

---

## Schedule

| Day | Work |
|---|---|
| 0-2 | Step 0: merge the Phase 3 fixes, commit `PH4-N1`, Phase 4 review, §4.6 demo session (you). **Warm-up soak starts** (you). |
| 3 | PR 5.1 (harnesses) |
| 4-5 | PR 5.2 (point in time) |
| 6-7 | PR 5.3 (fill model) |
| 8-9 | PR 5.4 (statistics) |
| 10 | PR 5.5 (evidence and identity) |
| 11-12 | PR 5.6 (verdicts) + PR 5.7 (writers and LLM context) |
| 13 | PR 5.8 (deck) |
| 14 | 5.9 gate: quarantine and lifecycle review (you, on Windows), honest baseline. **Phase 5 gate.** |
| 12-15 | PRs 6.1-6.3, in parallel with the end of Phase 5; they touch different files. |
| 16 | Day-0 readiness, soak tag. **The counted soak starts.** |
| 16-30+ | Counted soak (≥ 14 calendar days, ≥ 10 clean trading days at the end) |
| exit | Exit review, evidence package. **Phase 6 gate.** |

PRs 5.2, 5.3 and 5.4 touch disjoint files and can run as parallel branches after 5.1. PR 5.6 needs 5.4 (DSR and walk-forward API) and 5.5 (registry helper).

## Risks and how they're handled
| Risk | Mitigation |
|---|---|
| Phase 5 changes alpha values and the LLM context, so paper decisions shift | Six named live-path changes, each with golden replay updates that name the producer. They all land before the counted soak. |
| The deck looks empty once fabricated numbers are hidden | Intended. Empty states say why ("no broker-reconciled trades"). |
| The honest baseline shows no edge for some or all setups | That is the information Phase 5 exists to produce. It gates live money. The soak still runs, because it tests plumbing, and on demo the money is not real. |
| The quarantine deletes journal rows on Windows | It archives to a separate database with a SHA-256 manifest first, the dry run is reviewed by you, and the delete is one transaction. |
| A token can't prove a human on a single-user machine | Typed confirmation in the deck or an interactive terminal, `actor_kind` recorded, and an `AGENTS.md` rule that agents never approve. That raises the bar; it doesn't make it impossible. The audit trail shows who did what. |
| MT5 in Wine isn't stable for 2 weeks | The warm-up finds out early. Fallback: a Windows VM or VPS running the bridge (`docs/MACOS.md` option B). |
| Too few trades for the coverage matrix | Extend the soak. The report shows proposals per day and rejection reasons, so a "clean" soak with no trades is visibly not evidence. |
| An engine fix mid-soak | The freeze rule restarts the 10-day counter. Calendar cost, not a quality cost. |
| The DST change confuses the day boundary | It is drill D12, and the warm-up observes 25 Oct. |
| The proxy-based outage drill also cuts the news feeds | Judge it on `deterministic_fallback` decisions, not on HOLDs. |

## Decisions I need from you (recommended defaults in bold)
1. Hybrid backtest pipeline: **delete it, and `--engine hybrid`**, or repair both tiers.
2. `contextual_bandit`: **rename it honestly**, or delete it.
3. `vbt_engine` shorts: **raise when passed**, or implement them.
4. `mega_alpha_composite` in the prompt: **drop it**, or rename it to `mean_in_sample_ic`.
5. Automated training "OOS": **report `NOT_VALIDATED` and drop it from the LLM context now; real evaluation in Phase 8**, or build it now (+2 days).
6. The 13 registry entries with hand-written metrics: **move the metrics to `claimed_metrics` and demote to `CANDIDATE`**, or delete the metrics.
7. Code setups seed as **`CANDIDATE`**, or keep `DEPLOYED` as a label.
8. Lifecycle cleanup: **every setup whose state rests on no evidence goes to `CANDIDATE`; `rl_qlearning_v1` leaves the strategy lifecycle; you confirm the list.**
9. Journal quarantine: **archive and delete** (151 trades, 386 memories, backtest playbook rows), or keep the rows with an exclusion flag.
10. Approvals: **typed confirmation in the deck or an interactive terminal, plus `actor_kind` and the `AGENTS.md` rule**, or also a separate operator passphrase.
11. Soak host: **the Mac through the bridge**, as the roadmap says, or a Windows VM or VPS.
12. Soak scope: **week 1 EURUSD; week 2 adds XAUUSD; swing + scalp; 0.25 % risk; max 2 positions.**
13. Warm-up soak during Phase 5, not counted: **yes.**
14. Exit trade count: **≥ 20 closed trades plus the full coverage matrix; extend if not reached.**
15. Freeze rule: **any engine-code change resets the 10-clean-day counter.**
16. Who acts on the account: **you run every drill and restart; I read and report.**
