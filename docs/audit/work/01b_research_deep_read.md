# 01b: Deep read of `research/` (live-influencing paths first)

**Date:** 2026-10-04 · **Commit:** `3e7baf1` + working tree · **Builds on:** `docs/audit/AUDIT_2026-10-04.md`, `docs/CODEBASE_DEEP_DIVE.md` Part 3.
**Severity scale:** same as the main audit (🔴 / 🟠 / 🟡 / ⚪).

## Method

1. Listed every `research` module imported by `intelligence/llm_orchestrator.py`, `mt5_trader.py`, `core/autonomous_bootstrap.py`, `core/automated_training_manager.py` (`operations/strategy_factory.py` and `operations/strategy_lifecycle.py` import nothing from `research/`).
2. Read those modules in full, then followed each one to the code that consumes its output.
3. Probes ran with `.venv/bin/python` (`FAST_TEST=1 LLM_TRADER_OPENBB_OFFLINE=1`). Scratch scripts and DBs are under `/private/tmp/auditprobe/`.

**Side effect to disclose:** probe `p2.py` called `BacktestDSRIntegrator` through `MT5BrokerBacktestEngine.process_strategy_improvement`. That integrator always appends one trial (strategy `crossover`, Sharpe 1.2) to the **gitignored runtime ledger `artifacts/trial_ledger.db`**. It is append-only, so I left it. No tracked file was modified.

Findings already in the main audit or the deep dive are not repeated. Their status is listed in §3.

---

## 0. Summary

**Counts:** 🔴 0 · 🟠 5 (R1, R2, R3, R5, R6) · 🟡 9 (R4, R7-R12, plus two in R13) · ⚪ 5 (R13).

1. **R1 🟠** The RL bridge overrides LLM HOLDs with ε-greedy Q-table actions (88% BUY on cold states). Its "live" guard checks attributes that don't exist (`rl_action_bridge.py:230-232`, `llm_orchestrator.py:8389-8391`).
2. **R2 🟠** `MT5BrokerBacktestEngine` writes backtest trades (pips stored as money) into the production `trades` table, where they count toward the weekly loss limit. It also writes APPROVED LLM memories and the playbook, and auto-promotes setups to PAPER_PROVEN from one in-sample backtest, ignoring its own DSR reject (`mt5_backtest_engine.py:684-694,797-813,903-937`).
3. **R3/R6 🟠** The continuous-backtest verdict ignores CPCV/PBO and fails open when DSR errors. At boot, only the last strategy's backtest is kept, and a failed backtest is skipped instead of blocking (`continuous_backtest_pipeline.py:662-703`, `autonomous_bootstrap.py:323`).
4. **R4 🟡** Every automated promotion and research-influence gate is permanently dead: a missing method and a never-computed `sharpe_ratio`. The operator's research-influence approval can never take effect.

## 1. Findings

### R1 🟠 The RL bridge turns an LLM `HOLD` into a BUY/SELL, and the "live" guard in front of it can never fire

**Where:** `research/rl_action_bridge.py:229-235`, wired in at `intelligence/llm_orchestrator.py:8388-8396` and `:8570-8575`.

```python
# rl_action_bridge.py:230-232
current_action = str(out.get("action") or "HOLD").upper()
if current_action in {"", "HOLD", "NONE", "NULL"}:
    out["action"] = bridged.action
# llm_orchestrator.py:8389-8391
has_paper_broker = getattr(self.data_provider, "paper", None) is not None
has_execution_authority = getattr(self.data_provider, "execution_authority", False)
non_live = has_paper_broker or not has_execution_authority
```

**What goes wrong**

- `merge_bridge_into_decision(..., fill_missing_only=True)` is documented as "fill gaps". In practice it **replaces the action** whenever the LLM said HOLD. It also fills entry, SL and TP, so the overridden decision passes RR≥2 geometry.
- The proposed action comes from `RLTrainer.select_action(state, explore=True)`. That is ε-greedy (ε=0.2) over a Q-table keyed on a fine-grained state. For an unvisited state, `max({"BUY":0,"SELL":0,"HOLD":0})` returns `BUY` (prior audit, rl_trainer cold-state bug).
- The guard meant to keep this out of live trading is dead. `MarketDataProvider` (`llm_orchestrator.py:530`) defines neither `paper` nor `execution_authority`; `git grep` finds no assignment anywhere. So `non_live` is **always True**.

**Probe** (`/private/tmp/auditprobe/p1.py`):
- 1,000 cold states through a fresh `RLTrainer` gave BUY 884 · SELL 53 · HOLD 63.
- `merge_bridge_into_decision({"action":"HOLD","confidence":0.9,"reasoning":"no setup, stand aside"}, BUY-bridge)` returned `action='BUY', entry=1.1002, SL=1.0987, TP=1.1032, confidence=0.9`. The HOLD's confidence and reasoning are carried into the trade.
- `non_live` for an object without those attributes is `True`.

**Who is affected**
- **Paper mode (default):** about 88% of LLM HOLDs on unseen states become paper BUYs. That pollutes paper evidence and the learning loops fed from it.
- **`--demo` / `--live` (`paper_mode=False`, `mt5_trader.py:4257,4495`):** the override is active whenever `TRADING_RL_ENABLE_UPDATES=true`. That flag's name says "enable learning updates", not "let RL originate orders". The research-influence approval path would also activate it.
- Only `--bounded-autonomy` (off by default) or an LLM outage suppresses it.

The deterministic validator and risk manager still bind. But the decision to take risk no longer comes from the model that is supposed to make it.

**Fix**
- The bridge may only fill numeric fields on an existing BUY/SELL. Never change the action: delete lines 230-232.
- Replace the `non_live` heuristic with an explicit `paper_mode`/account-mode check.
- Never use `explore=True` outside an offline simulator.

### R2 🟠 The broker backtest engine writes simulated trades, APPROVED memories, playbook rules and lifecycle promotions into the production journal

**Where:** `research/mt5_backtest_engine.py:595-937`.

**How it is reached**
- `ContinuousBacktestPipeline` stages 5-7 (`continuous_backtest_pipeline.py:398-450`), which run at boot (`core/autonomous_bootstrap.py:311`) and every 500 cycles in the live loop (`mt5_trader.py:5402`).
- The CLI `mt5_trader.py backtest --engine mt5` (`:3497`). That path uses the default `db_path="trading_state.db"` relative to the working directory, which is the production journal.
- The two paths write different things:
  - The pipeline builds `BacktestMetrics` without `trades` (`continuous_backtest_pipeline.py:412-420`), so on that path `process_journaling` logs **0 trades**. The lifecycle promotion (item 3) and the APPROVED memory and playbook writes (item 4) still happen, once at boot and then every 500 cycles, and they accumulate without bound.
  - The CLI path writes all of items 1-5.

```python
# :797-813  process_journaling
base_ticket = 999000000 + int(time.time() % 100000) * 100
con.execute("INSERT OR REPLACE INTO trades (...) VALUES (...)", (ticket_id, 888888, strategy_name, symbol, tr["side"], 1.0,
    tr["entry_price"], 0.0, 0.0, 100.0, tr["entry_time"], tr["exit_time"], tr["exit_price"], tr["pnl_pips"], ...
# :903-908  process_reflection
INSERT INTO case_study_memories (...) VALUES (?, ?, ?, ?, ?, ?, 'APPROVED')
# :684-694  process_strategy_improvement
transition(..., "VALIDATING", actor="system", allow_auto=True); transition(..., "PAPER_PROVEN", actor="system", ...)
```

**What goes wrong**

1. **Simulated trades land in `trades`.** Their `pnl` is in **pips**, not money, and `risk_amount=100`. `TradingStore.realized_loss` (`core/trading_safety.py:842-845`) sums `pnl<0` over all rows with no source filter. `PortfolioRiskArbiter` (`:1356-1360`) compares that sum with the **weekly loss limit**.
   - The CLI path logs up to 100 trades, so a few dozen losing backtest trades can block live trading for the rest of the week with "weekly loss limit".
   - Same-day rows escape the *daily* check only by accident: `str(pd.Timestamp)` uses a space and the cutoff uses `T`, so the string comparison excludes them.
   - Probe `p2.py`: three backtest losses of −30 pips, closed two days ago, made `realized_loss(week_start)` = **90.0**.
2. **`INSERT OR REPLACE` with tickets 999,000,000-1,008,999,999** can overwrite a real broker ticket in that range. The overwritten row keeps its `order_intents` record, so `broker_reconciled_tickets` then treats a simulated trade as broker evidence for `auto_promotion._admissible_trades`. Ticket ranges vary by broker, so this collision is **UNVERIFIED** in practice.
3. **Lifecycle promotion.** One in-sample crossover backtest moves the setup CANDIDATE → VALIDATING → **PAPER_PROVEN**, with reason "Validated … with full broker friction".
   - No paper trade exists.
   - The DSR and CPCV results computed a few lines earlier are not part of `passes_promotion` (`:675-681`). Probe: DSR said "REJECT (DSR 0.66 < 0.95, n_trials=414)" and the row still became `PAPER_PROVEN`.
   - `AUTO_TRANSITIONS` permits these two edges, so the lifecycle module accepts them. The resulting PAPER_PROVEN is the evidence an operator then promotes from.
4. **APPROVED LLM memory without review.** Backtest "lessons" are written to `case_study_memories` with status **APPROVED**, bypassing the CANDIDATE review that the memory layer otherwise enforces. They are also appended to today's `playbooks` row ("Robust performance on EURUSD: Sharpe 1.20 …"). Both are LLM prompt context, so a backtest of a generic EMA crossover becomes approved, trusted context for the live decision model.
5. **Fabricated OOS Sharpe** (`:879-883`): `test_sharpe=max(metrics.sharpe_ratio * 0.85, 0.1)` is passed to the meta-reflector's health audit as if it were measured out-of-sample.

**Fix**
- The research engine must never open `trading_state.db` for writing. Give it its own `research.db`, or pass `db_path=None` and journal nothing.
- Backtest results must never enter `trades`, `case_study_memories` or `playbooks`.
- Remove `VALIDATING→PAPER_PROVEN` from `AUTO_TRANSITIONS`, or require paper-journal evidence for it.

### R3 🟠 `ContinuousBacktestPipeline`'s verdict ignores CPCV/PBO and fails open on DSR errors; its walk-forward stage mixes strategies

**Where:** `research/continuous_backtest_pipeline.py:662-703` and `:341-380`.

```python
dsr = stages.get("dsr", {})
if dsr.get("status") == "completed" and not dsr.get("passed", False):   # failed/errored DSR => no blocker
...
"SELECT r_multiple FROM trades WHERE closed_at IS NOT NULL AND r_multiple IS NOT NULL AND ticket IN (...) LIMIT 100"
"folds_tested": 3,
```

**What goes wrong**
- `_compute_verdict` never reads `stages["cpcv"]`. PBO = 0.9 or `is_statistically_robust=False` still yields `PASS`.
- If DSR raises (import error, ledger lock), its status is `failed`, and that is not a blocker. A failing safety stage therefore *removes* a blocker.
- The walk-forward stage reads r-multiples from **all strategies and symbols**, unordered (no `ORDER BY`), capped at 100, and reports them as `strategy_name`'s walk-forward. `folds_tested: 3` is a constant.
- The sandbox it calls annualises each 15-trade fold Sharpe with `sqrt(252/15)` (`bayesian_walk_forward_sandbox.py:101`).
- The "tuning" grid only scales returns by a constant (`rr_multiplier`). Sharpe, win rate and payoff ratio are all scale-invariant, so the three candidates are statistically identical and the ranking is noise.
- DSR uses `n_observations = total_trades or 50` (`:308`; same at `mt5_backtest_engine.py:619`), which fabricates 50 observations when there are none.

**Fix**
- Make the verdict require `cpcv.status=="COMPLETED"` and PBO below a threshold, and treat any non-`completed` DSR as a blocker.
- Filter walk-forward input by strategy and symbol and order it by `closed_at`.
- Drop the scale-only grid.

### R4 🟡 Every automated promotion path is dead, so the research→lifecycle machinery runs every 100 cycles and can never promote

These gates fail closed, which is the safe direction, but the feature does nothing and nothing reports that. Verified by reading, then by probe: `hasattr(BayesianWalkForwardSandbox(), 'run_walk_forward')` is `False`, and `_setup_floor_reasons` on an excellent row shaped like `setup_metrics` output (500 trades, expectancy 0.5R, PF 3.0, win rate 0.6) returns `['sharpe_ratio<0.5']`.

- **`auto_promotion._check_walkforward`** calls `BayesianWalkForwardSandbox().run_walk_forward(...)` (`auto_promotion.py:473`). That method does not exist; the class has only `generate_splits`, `evaluate_candidate` and `tune`. The `AttributeError` becomes `walkforward_unavailable` on every call.
- **`auto_promotion._check_judge_rubric` / `_check_openquant_admissibility`** need `metrics.formula` / OpenQuant keys. `_get_strategy_status` returns `metrics: {}` for every `strategy_lifecycle` row (`:225-231`), so both gates always fail for lifecycle strategies.
- **`research_validation._setup_floor_reasons`** requires `metric["sharpe_ratio"] >= 0.5` (`:421-426`). `setup_metrics()` never computes `sharpe_ratio` (`:368-376`), so every setup fails with `sharpe_ratio<0.5` and `promotion_gate()["eligible_setups"]` is always empty. Consequently `operations/research_influence_gate.py:74` reports `research_ready=False` forever. **An operator's research-influence approval can never take effect**, and the CLI's "purged/OOS research promotion gate is not ready" message is misleading.
- **`purged_walk_forward_folds` is a fold count, not a test** (`research_validation.py:374,394`). The floor "require_purged_walk_forward" only means "at least 125 rows". No out-of-sample fold is ever scored.

**Fix:** compute `sharpe_ratio` in `setup_metrics` (per-trade, not annualised). Either implement `run_walk_forward` or call `evaluate_candidate`. Make each gate *evaluate* its OOS folds. Add a test that a synthetic good setup can pass each gate, so a dead gate is caught.

### R5 🟠 Two writers race on `rl_model.json`; synthetic and duplicated updates get persisted and registered as if trained on broker evidence

**Where:** `mt5_trader.py:5089-5200`, `intelligence/llm_orchestrator.py:7013-7022`, `research/rl_trainer.py:22-56,326-353`.

**What goes wrong**
- The live orchestrator holds one `RLTrainer` (cwd-relative `rl_model.json`, loaded at init). It is updated online. With `TRADING_KRONOS_RL_OFFLINE=true` it is also trained in-memory on the **heuristic Kronos simulator** (`save=False`). It is saved on exit (`mt5_trader.py:5695`).
- Every `RL_TRAIN_INTERVAL` (50) cycles the main loop builds a **second** `RLTrainer(BASE_DIR/rl_model.json)`. It loads the file, re-applies the **same last 50 broker trades** (no "already applied" marker, so each retrain re-counts old trades as new experience), and overwrites the file.
- Last writer wins: the exit-time save discards the periodic retrains, and the retrains discard the in-memory online and simulator updates.
- The simulator-trained Q-values end up in the same file that `model_registry` records as `rl_qlearning_v1`, whose provenance says broker trades.
- The retrain sets `next_state = state` and `done=True`, so the "Q-learning" is a running average of reward per (state, side).

**Registry consequences** (`research/model_registry.py:29-42`; `core/trading_safety.py:276` has `UNIQUE(model_key, version)`)
- `version=f"auto_{cycle_count}"` restarts at 0 on every engine restart, so after the first restart every `register()` hits the UNIQUE constraint. The failure is swallowed at **debug** (`mt5_trader.py:5190`).
- `training_data_hash` is `md5(get_stats())`, not a hash of data. `activation_status`'s dataset-hash corroboration (`model_registry.py:185-213`) therefore compares two unrelated hashes.
- Every version points to the same mutable file, with no artifact hash.

**Fix**
- One trainer instance and one writer.
- Record applied tickets and update each one once.
- Keep simulator training in a separate artifact.
- Hash the artifact bytes into the registry row, version as `<content-hash>` instead of a cycle counter, and log registry write failures at WARNING.

### R6 🟠 The boot VALIDATE step keeps only the last strategy's backtest, and a failing backtest blocks nothing (consumer of the research pipeline)

**Where:** `core/autonomous_bootstrap.py:311-336` (the consumer of `ContinuousBacktestPipeline`).

```python
            for strat in active_strategies:
                ...
                result = _safe(pipeline.run_validation_cycle, ..., symbol="EURUSD", strategy_name=strat_id, timeframe="H1", bars=500)
            backtest_results[strat_id] = {      # <- dedented: runs once, after the loop
```

**What goes wrong**
- The assignment sits outside the `for` loop. With N active (`FULL`/`EVALUATION`) registry strategies, only the **last** one reaches `backtest_results`. The walk-forward-efficiency gate (1e) and the overfit gate (1f) are the two steps that can add a *live-mode boot blocker*, and they iterate `backtest_results`, so the other N−1 strategies are never gated.
- For the one strategy that is recorded, `if not bt_data.get("passed"): … continue` treats a **failed backtest as "skip"**, not as a blocker. The gates fail open on the worst result.
- Every strategy is backtested on **EURUSD H1** whatever it actually trades.
- `"max_dd"` reads `max_drawdown_pct`, but the stage key is `max_drawdown` (`continuous_backtest_pipeline.py:252`), so it is always 0.
- Each `run_validation_cycle` also triggers R2's lifecycle and memory writes for every registry strategy.

**Fix**
- Indent the assignment into the loop.
- In live mode, treat `passed=False` or a missing `returns` series as a blocker.
- Backtest each strategy on its own symbol and timeframe.

### R7 🟡 Kronos weights load with no integrity check; the checksum manifest is trust-on-first-use and never consulted at load

**Where:** `research/kronos_bridge.py:319-401`, `research/kronos_downloader.py:98-130`.

**What goes wrong**
- `try_load_neural_bundle` loads `model.safetensors` and `config.json` from `KRONOS_MODELS_DIR` or the legacy directory. It never calls `KronosDownloader.verify_checksums`. A repo-wide `git grep verify_checksums` finds only the definition.
- `verify_checksums` is trust-on-first-use: if no manifest exists, it **writes one from whatever files are present** and returns `ok: True` (`:111-115`).
- `load_state_dict(..., strict=False)` only *warns* on unexpected keys (`:384-392`).
- The config JSON is splatted straight into the constructors (`KronosTokenizer(**tok_cfg)`, `Kronos(**model_cfg)`).
- The bridge prepends `vendor/kronos_official` to `sys.path` and imports the generic top-level package name `model`. That shadows any other `model` module for the rest of the process.

**Impact.** Safetensors cannot execute code, so this is not RCE. But a swapped or partially written weights file silently becomes the "NEURAL" forecast that `kronos_advisory` puts into every LLM prompt (`llm_orchestrator.py:7805`). The workflow selector reads it at `:5863`: Kronos confidence above 0.75 selects `neural_kronos`.

**Fix**
- Call `verify_checksums(model_key, require_manifest=True)` before load and fall back to heuristic on mismatch.
- Commit the manifest (hashes only).
- Use `strict=True`.
- Import the vendor code under a namespaced package.

### R8 🟡 `AutomatedTrainingManager`'s "OOS validation" never touches the trained models, and its WFE is a constant

**Where:** `core/automated_training_manager.py` (research consumer), `:106-158`, `:186-200`, `:330-372`, `:229`. It is run at boot (`mt5_trader.py:4381-4392`) and as an LLM auto-tool every ~1,008 pulses (`core/auto_quant_tools.py:96,866-868`), whose result goes into the decision context as `status: PASS/FAIL`.

```python
is_sharpe = max(abs(sharpe) * 1.2, 0.1)  # IS typically better, floor at 0.1
raw_wfe = sharpe / is_sharpe
...
total_trades = sum(r.scenarios_passed + 50 for r in results.values())
```

**What goes wrong**
1. **The "OOS" data is synthetic.** `KronosPredictor.generate_synthetic_regime` is a seeded Gaussian random walk. Its own docstring says regimes "remain heuristic even when neural weights are loaded".
2. **The "OOS strategy" is not the trained model.** It is a hard-coded 20-bar momentum rule that always goes **long**, entering on `abs(momentum)`. None of the Q-learning, PPO, meta-RL or GRPO models trained in phases 2-3 is evaluated.
3. **WFE is fabricated.** For any |Sharpe| ≥ 0.083, `wfe = sharpe / (1.2·|sharpe|) = ±0.833`. The `MIN_WFE = 0.30` gate therefore reduces to "Sharpe > 0".
4. **Sharpe is annualised ×√252** on per-trade R values (the same class as H5).
5. **`run_full_cycle` invents a trade count**: `scenarios_passed + 50` per symbol.
6. **Seeds are not reproducible.** `seed=hash((symbol, regime)) % 10000` and `ScenarioGenerator(seed=hash(symbol) % 10000)` use Python's per-process randomised `hash()`, so the "OOS" set differs on every run.

**Impact.** The LLM is told `automated_training: PASS, wfe 0.833` when an always-long momentum rule makes money on a random walk with positive drift (the `bull` regime has drift +0.15%/bar). This is fabricated validation evidence (class e). The cycle also runs PPO, meta-RL and GRPO training **inside a decision pulse**.

**Fix**
- Evaluate the trained policies themselves on held-out **real** bars.
- Compute WFE from real in-sample and out-of-sample folds.
- Drop the `+50`.
- Use `zlib.crc32` or explicit integer seeds.
- Move the training cycle out of the pulse.

### R9 🟡 `auto_promotion`'s divergence gate fails open and is hard-wired to EURUSD

**Where:** `research/auto_promotion.py:532-546`, `research/divergence_monitor.py:75-128,202-228`.

```python
result = monitor.check_divergence("EURUSD")
return {"passed": not result.get("alert", False), ...}
```

**What goes wrong**
- `check_divergence` returns `alert: False` for every non-evaluation: `no_trading_db`, `no_broker_reconciled_trades`, `insufficient_trades`, `no_backtest_data`. The `no_divergence` gate therefore **passes on missing evidence**.
- It checks EURUSD for every strategy, whatever the strategy trades.
- It compares *all strategies'* live EURUSD trades with the latest pipeline backtest for EURUSD of *any* strategy label, often the generic ruleset (`_get_backtest_expectations` matches on symbol only).
- `live_r = gross_pnl / max(risk_amount or 1, 0.01)` turns a NULL `risk_amount` into an R-multiple measured in account currency.

R4 currently masks all of this, because walk-forward always fails. Fixing R4 without fixing this gate would let PAPER_PROVEN → DEMO_PROVEN through on no divergence evidence at all.

**Fix:** return `passed: False` unless the status is `completed` on ≥ N trades for **this strategy's** symbols, and match the backtest on strategy *and* symbol.

### R10 🟡 `current_lifecycle` lets stale history override a demotion; STAT_PASS → STAT_FAIL is not a legal transition

**Where:** `research/edge_lifecycle.py:30-41,84-96`; consumed by `intelligence/llm_orchestrator.py:8047-8064` → `intelligence/opportunity_bus.py:132,203-209`.

**What goes wrong**
- `current_lifecycle` returns the newest `macro_edge_history` payload that carries a `lifecycle` key, **before** it looks at the live `macro_edges.status`.
- `ALLOWED_TRANSITIONS["STAT_PASS"]` has no `STAT_FAIL` edge, so a passed edge that later fails statistics cannot be recorded as failed; `advance_lifecycle` refuses it.
- Any status-only demotion path writes no lifecycle payload. That includes `macro_graph` demotions and `REJECTED_*` updates.
- In both cases the edge keeps reporting `STAT_PASS`. `opportunity_from_stat_pass_edge` admits an edge when `stage in _STAT_PASS_OR_BETTER` **or** its status is approved, so a stale `STAT_PASS` still publishes a demoted edge to the opportunity bus as advisory LLM context.

The mechanism is verified by reading. **UNVERIFIED:** whether demoted edges can still appear in `toon["macro_causal_context_regime"]` (that depends on the macro-context query filter, which I did not trace).

**Fix:** when `macro_edges.status` is a rejected, demoted or retired status, it must win over history. Add `STAT_PASS→STAT_FAIL` and `REPLAY_PASS→REPLAY_FAIL` edges.

### R11 🟡 Alpha-miner context fed to the LLM is mislabelled, and the bridge rewrites the production registry non-atomically

**Where:** `intelligence/llm_orchestrator.py:4972-5040` (consumer), `research/alpha_miner/strategy_bridge.py:63,278-285,322-350`.

**What goes wrong**
- `"mega_alpha_composite"` is described to the LLM as a "MegaAlpha composite signal". It is actually `mean(ic_mean)` over registry rows: an average of **in-sample** information coefficients from the GA (see H5: no holdout). It is not a current signal value.
- "Active" includes `CANDIDATE`, because only QUARANTINE and RETIRED are excluded.
- The decay check reads `alpha.get("strategy_id")` and `alpha.get("ic_history")`, but `AlphaStrategy.to_dict()` emits `id` and has no `ic_history`. Every decay alert is therefore dead: `alpha_decay_alerts` is always `[]`.
- `_save_alpha_strategies` does an unlocked read-modify-write with `open(..., "w")` + `json.dump` on the **cwd-relative** `strategy_registry.json`. `StrategyFactory` writes the absolute `ROOT/strategy_registry.json` atomically under a lock.
  - If the cwd is not the repo root, the two layers diverge.
  - If the process dies mid-dump, the file is truncated. `StrategyFactory._load` then falls back to `{"strategies": []}` and the next persist **wipes the registry**.
- `update_strategy_metrics` mutates a factory record in place and calls the private `factory._persist_locked()` without taking `factory._lock`.

**Fix**
- Rename the field to `mean_in_sample_ic` or drop it.
- Read `id`, and store `ic_history`.
- Have the bridge go through `factory.upsert` only, with no file writes of its own.

### R12 🟡 Laya decision-model weights load with no completeness check; the MacroAnalyst veto it feeds is never enforced

**Where:** `research/decision_models/laya_model.py:60-117`; consumer `core/harness/pipeline_v2.py:625-766`.

**What goes wrong**
- `_load_agent` builds the encoder with `AutoModel.from_config`, which gives **random initialisation**. It then copies only the tensors present in `model.safetensors`. Missing parameters stay random, there is no count or `strict` check, and there is no hash.
  - A truncated, renamed or wrong checkpoint therefore produces confident random answers, with no error.
  - The news-triage path (`data/news_triage.py:577`) uses the same loader.
- The pipeline stage feeds the model invented defaults when snapshot fields are missing: `vix` 16.5, `tier` "HIGH", `minutes_to_release` 999 (`pipeline_v2.py:652-656,676`).
- When the bundle vetoes, the stage sets `ctx.flags["macro_hold_only"] = True` (`:760`). **No code reads `macro_hold_only`**, and nothing downstream reads `ctx.shared["macro_bundle"]` either; only `ui_state` does. The veto is decorative. This is the same class as H2's `hold_only`, but a separate flag.

**Fix**
- Assert that the loaded key set equals `named_parameters() ∪ named_buffers()`, and verify a manifest hash.
- Pass `None` instead of fabricated defaults, and let the deterministic labeler abstain.
- Read `macro_hold_only` in the execution gate, or remove the claim.

### R13 Smaller verified items

| Sev | Where | Finding | Fix |
|---|---|---|---|
| 🟡 | `research/hybrid_backtest_pipeline.py:100-131,149` | Tier 2 ("realistic MT5 replay") **ignores the `entries`/`exits` it is given** and re-runs a fixed EMA 12/26 crossover. The Tier-1 vs Tier-2 "Sharpe decay" verdict compares two different strategies. Reached from `mt5_trader.py backtest --engine hybrid` (`:3529`). | Replay the supplied signal arrays. |
| 🟡 | `research/auto_promotion.py:166-172` | `_log_promotion` runs for the operator-required branch too. `promotion_history` and the log line "Strategy X promoted: DEMO_PROVEN → READY_FOR_DEPLOYMENT" record a promotion that did **not** happen. `promoted=True` is also returned when `_update_strategy_status` swallowed a DB error (`:672-675`). | Log only on a committed write, and have `_update_strategy_status` return success. |
| ⚪ | `research/auto_promotion.py:714-718,659-665` | `check_all_strategies` treats every `model_registry.model_key` (`rl_qlearning_v1`, `ppo_position_mgmt_v1`) as a strategy. A promotion would `INSERT OR REPLACE` a `kind="strategy", version="auto"` row under a *model* key. Unreachable today because of R4. | Iterate lifecycle rows only. |
| ⚪ | `mt5_trader.py:5213-5225` | The in-loop PPO "training" uses `seed=7` on the heuristic Kronos simulator, so every 200 cycles it produces an identical artifact, overwrites `artifacts/ppo_position_mgmt.json` and tries to register a new version. That costs CPU and RAM in the trading process for no new information (adds to H7). | Run it off-process; skip when the inputs are unchanged. |
| ⚪ | `research/kronos_bridge.py:319` + callers | No weight cache. Each `KronosPredictor()` reloads torch weights: the orchestrator integrator at init (`TRADING_KRONOS_ADVISORY` defaults to true), `KronosSignalFusion`, and `AutomatedTrainingManager._generate_kronos_oos` at boot. Small for kronos-mini, but it adds to H7's boot-RAM problem. | Use a module-level cached predictor. |
| ⚪ | `research/promotion_package.py:63-86` | `verify()` recomputes an **unkeyed** SHA-256 over the package, so it proves internal consistency only, not who produced it. Nothing treats it as authorisation today, which is correct; the docstring should say so. | Use an HMAC with the install key if it is ever used as approval evidence. |
| ⚪ | `research/research_validation.py:21-24` + bare imports | A bare-vs-package import probe shows `adaptive_models` and `research.adaptive_models` both loaded (C1 class). The module has no module-level state, so this is harmless today. | Covered by the C1 fix. |

## 2. Verified clean (read in full; no new defect found)

- **`research/research_validation.py` `labelled_dataset` / `broker_reconciled_tickets`.** Requires `order_intents.mt5_ticket` or `broker_execution_reports` evidence, excludes synthetic markers, checks the feature-schema hash and values hash, and checks `completed_bar_time ≤ opened_at`. Fails closed when tables are missing. (Caveat for R2: a simulated row that overwrites a real ticket inherits that ticket's evidence.)
- **`purged_embargoed_splits` / `purged_walk_forward_splits`.** Purge and embargo logic is correct for interval labels. (Their *use* as a fold count is R4.)
- **`research/sample_gate.py`.** Per-(strategy, symbol, setup) sample and regime floors; unknown regime fails.
- **`research/supervised_training.py`.** Train-only standardisation, purged walk-forward folds, no write unless `artifact_path` is set. The prior 🟡 (last-fold artifact vs pooled metrics) still stands. Its `out_of_sample_status` value `EVALUATED_PURGED_WALK_FORWARD` is not in `model_registry.activation_status`'s accepted set (`PASS/PASSED/VALIDATED/OK`), so a supervised run can never corroborate a model. That is fail-closed but inconsistent.
- **`research/demo_sample_generator.py`.** Refuses to inject into a journal that contains non-synthetic rows. Every row carries three independent synthetic markers that `labelled_dataset` excludes. (The default `path` is still the production DB name; recommend requiring an explicit path.)
- **`research/model_registry.py` `review()`.** Approval requires the five evidence flags and an `artifact_uri`. `load_approved_model` / `list_approved_models` have **no callers outside the module**, so the prior Path E is latent, not live.
- **`research/auto_promotion.py` gate handlers.** All exception paths now fail closed (`_gate_unavailable`). DEPLOYED and READY_FOR_DEPLOYMENT are never written by automation, and `_update_strategy_status` raises on live-authority states for non-operator actors.
- **`research/rl_action_bridge.py` `bridge_rl_action`.** SL/TP geometry is directionally correct, non-positive levels force HOLD, and `execution_authority` is always False. (The defect is the *merge*, R1.)
- **`research/kronos_bridge.py` loader.** Uses safetensors only, so there is no pickle or `torch.load`. A repo-wide grep found **no `pickle.load` / `torch.load` / `joblib.load` in `research/`**. The RL Q-table is JSON.
- **`research/broker_replay.py`.** Unchanged from the prior audit's ✅.
- **Import cost.** Importing all 24 research modules that `mt5_trader` imports at top level took about 1 s and an 88 MB max RSS, with **no torch and no pandas** loaded (`/private/tmp/auditprobe/p3.py`). Torch enters only through the Kronos and Laya runtime paths.

## 3. Status of prior research findings

| Prior item | Status at this tree |
|---|---|
| Deep dive 3.1 Path A (auto_promotion raw SQL, two-step to DEPLOYED, gates fail open) | ✅ Two-step and fail-open fixed. Raw SQL still bypasses `strategy_lifecycle.transition()` (`auto_promotion.py:630-667`), but it can only write VALIDATING/PAPER_PROVEN/DEMO_PROVEN, and R4 makes it unreachable. |
| 3.1 Path D (strategy_bridge writes the registry) | ❌ Still open; new evidence in R11 (non-atomic, cwd-relative, private `_persist_locked` without the lock). |
| 3.1 Path E (model_registry) | ⚠️ `ImportError: pass` around the OpenQuant check is still there (`model_registry.py:104-105`), and there is still no status precondition on `review()`. The loaders have no callers, so this is latent. |
| 3.2 rl_trainer cold-state BUY | ❌ Still open, and now shown to drive R1 (88% BUY on cold states). |
| 3.2 meta_rl latent leak, PPO numpy gradient | Not re-checked (training-only, no live consumer found beyond the R8 log-only path). |
| 3.4 GA in-sample / overlapping IC / fake SHAP | ❌ Still open. R11 adds that the in-sample IC is fed to the LLM as a "composite signal". |
| H5 DSR trials / ×√252 | ❌ Still open. New sites: `auto_promotion.py:426`, `bayesian_walk_forward_sandbox.py:101` (`sqrt(252/n_test)`), `automated_training_manager.py:358`. |

## 4. Probes

All under `/private/tmp/auditprobe/`:
- `p1.py`: RL cold-state action distribution, HOLD→BUY merge, and the `non_live` evaluation (R1).
- `p2.py`: `MT5BrokerBacktestEngine` journaling, lifecycle and reflection against a scratch `j.db` (R2). Side effect: one trial appended to the gitignored `artifacts/trial_ledger.db`.
- `p3.py`: import timing, RSS and double-load check.
- An inline probe checked `run_walk_forward` existence and the `sharpe_ratio` floor (R4).
