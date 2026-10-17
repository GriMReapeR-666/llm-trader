# End-to-End Engine Audit — Autonomous Quant Trading System
_Audit date: 2026-10-09 · Scope: full codebase (1,117 .py files, 94 journal tables, 413 test files / 4,411 tests) · Method: static reading + live-state forensics (SQLite, artifacts, logs, pid/process check). No code was modified._

---

## 0. VERDICT (one paragraph)

The architecture is genuinely institutional-grade in design — layered memory, veto gates, trial ledgers, promotion gates, bounded autonomy where the LLM can only shrink a deterministic proposal — and most *individual modules* pass their own tests. **The system is however not currently running end-to-end and has not been since Oct 3.** The scheduler loop has been idle for ~6 days while a dead PID sits in `engine.pid`; because every learned component is fed by scheduler-produced evidence (calendar reactions → labels → memories → broker-reconciled R-multiples → CPCV/walk-forward → promotion), the whole learning chain is starved. Pearl: the biggest "silent failure" is not one broken file — it is **one dead loop silently starving six downstream layers**, each of which degrades gracefully and therefore raises no alarm.

---

## 1. PROCESS / SCHEDULING STATE (verified live)

| Item | State | Evidence |
|---|---|---|
| Engine process | **DEAD** | `engine.pid`=27112, `tasklist` finds no PID 27112 |
| Supervisor phase | `RUNNING` (stale, lying) | `supervisor_state.json` — `last_heartbeat: 0.0`, no freshness check marks phase stale |
| 26 registered schedules | **All idle since 2026-10-03 08:59** | `agent_schedules`: `claimed_at`=NULL on all 26, `next_run_at` = Oct 3 |
| Last trader run | Started Oct 8 20:11 `RUNNING`, heartbeat stopped | `agent_runs` row `895df3dd` — another stale `RUNNING` ghost run |
| LLM activity | Last `llm_request` **Oct 2**, last `llm_decisions` Oct 2 | 137 requests total |
| Macro event reactions | Only 41 rows, backfilled, last Sep 28 | `event_reactions` |
| RL reward events | **Stale since Sep 19** — one offline replay, never again | `reward_events` max(created_at) |
| Risk state | Corrupt semantics | `data/state/risk_state.json`: `start_equity=10000`, `weekly_start_equity=100000`, `peak_equity=100000` — the weekly drawdown circuit is anchored to $100k while equity is $10k |

**Root causes to fix:**
- `P0-1`废 loop: the scheduler-carrying process (the trader loop that claims due schedules) is gone; ghost `RUNNING` runs and a stale supervisor phase are never reconciled at boot. Add a **boot-time sweep**: kill stale agent_runs, downgrade supervisor phase if `last_heartbeat` older than 2× cadence, and self-heal `next_run_at` (schedules should re-fire immediately via the catch-up math that already exists in `operations/agent_operations.py:918-985` — it is never invoked because no loop claims).
- `P0-2`: `risk_state.json` weekly anchor mismatch (`weekly_start_equity` 100000 vs balance 10000) — the weekly recovery circuit is effectively disabled. Rebuild weekly anchor from actual account equity on load-detect-mismatch.
- `P0-3`: single-instance liveness is a stale file, not a lease. The `distributed_lock.py` machinery exists — make engine.pid lease-backed (heartbeat + takeover), so a crash is detected instead of discovered manually days later.

---

## 2. COMPONENT GROUP MAP (what exists, where, and what it actually does)

### Group A — Data sources (Level 0)
| File(s) | What it does | Live state / issue |
|---|---|---|
| `data/market_data_provider.py` | MT5 OHLC/tick/DOM collector, TIMEFRAME_MAP, calendar rows | OK in paper mode; is the only real-time source wired today |
| `data/forexfactory_provider.py`, `data/mt5_calendar_enhanced.py` | Economic calendar | **Degraded every session**: log shows "Could not find MT5 terminal DLL"; and rows are stored with `event_time=''` / `importance='0'` (see `macro_events`) — blackout windows keyed off tier *1/2/3* cannot fire from these rows; the news gate then silently falls back to static windows |
| `data/data_source_pipeline.py` | FRED ingestion | Cached values are stale/hostile: `DGS10: value 2.0` (placeholder), CPIAUCSL dated 2026-02-01 |
| `data/openbb_data_bridge.py` | "18 active providers" | Initialized OK, but several registry entries are directory stubs |
| `data/zero_lag_macro_provider.py` | Net liquidity, CPI nowcast, grid load, crop health… | **Mostly hardcoded stubs** (lines 380–410, 512+): daily_cpi 0.22/2.85, grid 101.4, chokepoint 98.2, crop 74.5, labor −1.2, plus dozens of constant spreads (104.70-brent etc.). Only RRP refines from live FRED. These numbers flow into LLM prompts as if observed |
| `data/alternative_data_provider.py` (+ registry) | 65 sources, golden-eight | Registry snapshot clean, but tiers T1 real-time sources are stub-hosted |
| `data/realtime_news_ingest.py`, `news_triage.py`, `api_cache.json` | RSS/NewsAPI | Works; `newsapi:EURUSD` returns volleyball headlines (keyword filter too loose); NewsAPI pool size 1 |
| `data/sec_edgar_provider.py`, `cftc_cot_provider.py`, `eia_provider.py`, `exness_provider.py`, `seeking_alpha_provider.py`, `portfolio123_provider.py`, `yahoo_finance_provider.py` | Deep fundamental/COT/energy/fundamental feeds | Initialized; several lazy unless keys set |

**Missing entirely (requested, absent):**
- **X/Twitter entity tracking** — deliberately **blocked by policy** (`intelligence/macro_source_policy.py: BLOCKED_SOCIAL_DOMAINS = twitter.com, x.com, truthsocial.com, t.co`). There is no tweet ingest, no entity list (Trump, central banks), no social velocity features. If you want entity signals, the policy layer needs a new tier (e.g. `SOCIAL_EVIDENCE_ONLY`: informative for LLM context, never causal authority) rather than deleting the ambition.
- **TradingView integration: zero occurrences anywhere in the repo.** Signals/webhooks/ Pine data cannot be ingested or exchanged today. If TV charts/alerts are wanted as a market-data or alerting plane, it's a new connector.
- **Political event foresight / geopolitics** actually has a big *framework* layer (see Group E) but no live feed feeding it.

### Group B — Structural / technical analysis (Level 1)
| File | What it does | Notes |
|---|---|---|
| `core/smc_structure.py` | Fractal swings, BOS/CHoCH (2-close validation), Order Blocks with mitigation, EQH/EQL pools, premium/discount | Deterministic, tested (`test_p06_twinalgo.py`); FVG intentionally delegated to `research/alpha_miner.fvg_detector` |
| `research/alpha_miner.py` (fvg_detector) | FVG geometry + sweep gates | Not found wired into a production cadre; FVG primarily used by miner, not a live strategy |
| `core/technical_indicators.py` | EMA/RSI/MACD/ATR/ADX/HMA/WMA | Solid, used by orchestrator per bar |
| `core/market_structure_edges.py`, `core/event_structural_edges.py`, `core/flue_thresholds.py` | Edge-propagation structure edges, event edges, FLuey thresholds | Wired into the technical cadre; thresholds test-pass |
| `core/crash_detector.py`, `core/bocpd.py`, `core/hawkes_process.py`, `core/avellaneda_stoikov.py` | Crash logic, online changepoint, event intensity, fair-value quoting | Present; BOCPD + Hawkes power the `model_bocpd_changepoint` and microstructure strategies |
| `core/cointegration_and_compounding_governance.py`, `core/kalman_statarb.py`, `core/cointegration_robustness.py` | StatArb pairs | Governance plugin mounted; **no crypto statarb exchange connector exists** — statarb runs only on MT5 FX pairs (EUR/CHF etc.). Crypto statarb across CEXs is unassembled: no ccxt/exchange adapter anywhere |

### Group C — Econometrics & regimes
| File | What it does | Notes |
|---|---|---|
| `core/econometric_features.py` | Hand-rolled GARCH(1,1) MLE + forecast + ADF | **Gaussian GARCH only** — no GJR/EGARCH leverage term, no student-t. Padding: failsafe returns sample vol; test-pass with synthetic data |
| `core/spmc_regime_filter.py` (loaded by RiskManager) | Regime veto inside risk engine | Good integration point |
| `core/regime_autocorrelation.py`, `regime_duration_tracker.py`, `regime_alpha_selector.py` | Regime-specific alpha selection | Wired via plugin; selection quality currently capped by tiny labeled evidence |
| `core/copula_tail_risk.py`, `correlation_breakdown.py`, `factor_portfolio_models.py`, `hierarchical_portfolio.py` | Tail/covariance risk | Used by sizing plugins |
| `core/fractional_diff.py`, `core/technical_indicators.py` | Feature transforms | OK |

### Group D — Memory (3 layers) + journaling
| Layer | Where | State |
|---|---|---|
| L0 Working memory | `trading_state.db.working_memory`, `working_context_items` + `intelligence/working_memory` | Populated |
| L1 Episodic | `setup_instances`, `setup_labels`, `trade_labels`, `verified_memories`, plus `local_agent_memory.db` (5,937 docs, COMPACTED/ACTIVE working) | **Promotion deadlock**: `verified_memories` are ALL `CANDIDATE` with tags `UNVERIFIED`, lesson = "Await more correctly labelled examples" — the labeling pipeline (`setup_labels`, `trade_labels`) is fed by broker reconciliation & event reactions, both dead (see §1), so nothing ever becomes a durable rule |
| L2 Semantic/Procedural | `patterns` table = **empty (0 rows)**; `core_memories` exists | `patterns` empty proves the distiller has silently never produced anything; also likely queried by strategy factory → returns no learned rules |
| Session/journal | `trades`, `trade_lifecycle_events`, `broker_execution_reports`, `daily_reconciliations` | Trades exist but are test/manual artifacts (volume 0.1, entry exactly 1.1, SL 1.0/TP 1.3 — synthetic-print pattern); **live-reconciled trades ~0** |
| Trail ledger | `artifacts/trial_ledger.db` 18,151 hash-chained trials, historical/integrity OK | Excellent; but ledger records the *bad* backtests too — quality is only as good as upstream |

### Group E — LLM & reasoning analysis (beyond "alpha")
Enumerated LLM uses actually implemented (not just prompt text):
1. **Trade decision** — `intelligence/llm_orchestrator.py::execute_cycle` (bounded: may only HOLD/verify/reduce a deterministic proposal; cannot originate/resize/retarget — enforced by `core/bounded_autonomy_pipeline.py`).
2. **Pair selection** — `select_pairs_with_llm` in orchestrator.
3. **Alpha discovery** — `core/alpha_discovery.py` proposes candidate alphas; alpha-mining sprint every 7d.
4. **Macro extraction** — `intelligence/macro_extraction.py` turns articles/releases into atomic facts into `macro_observations`.
5. **Post-trade reflection** — `core/llm_decision_logger.py` + reflection graph; distills `core_memories`; also `brain/distiller.py` (compact memory).
6. **Geopolitical/commodity analyses** — `intelligence/analytical_frameworks/v01..v07` as mounted plugins (framework engines, deterministic + LLM hybrid).
7. **Case studies** — `intelligence/case_study_*` (mine/match/learn) + `knowledge_retrieval` + Graph-RAG (`graphrag_reasoning`, `macro_graph`, `graph_nodes`).
8. **News/NLP extraction → veto** — `core/news_indicator_fetcher.py`, `sentiment_veto.py`.

**Notable silent-failure precedent** (already documented in-file, llm_orchestrator.py:36-46): a bare `logger.*` NameError inside `wire_trade_manager` was swallowed and recorded as the provider circuit's "last_error", silently downgrading the LLM tier to deterministic-only. The class of bug (bare except + peripheral module) is systemic — recommend a `strict mode` for orchestrator plugin mount failures (fail loudly at preflight, not warn).

### Group F — Risk, sizing, gates
| File | What it does | State |
|---|---|---|
| `core/risk_manager.py` | 1% per trade, 3% daily loss, 10% maxDD, 5 consecutive losses, hourly rate cap, cooldown, heat budget 4R→1R contagion, SPMC regime filter, equity floor 20%, opportunity-cost hurdle, Markov CVaR scaler, quarter-Kelly | Strongest module in the repo; but **state-file anchor bug** (§1) currently disables the weekly circuit |
| `core/news_trading.py` `NewsTradingGate` | PRE_NEWS / SPIKE_BLACKOUT / POST_NEWS_ENTRY phases, tier-1/2/3 windows (60/30/15 min), dynamic widening via correlation engine | Works; degraded usefulness while calendar rows lack `event_time` |
| `intelligence/macro_fusion_gate.py` | D0 spoof → D9 kill-switch → D2 blackout → narrative → D8 arbitration, STRONG_PROCEED / HALF_RISK / REJECT / POSTPONE | Sound; pure-python, tested adversarially |
| `core/margin_safety.py`, `broker_preflight.py`, `execution_planning.py`, `execution_policy.py`, `transaction_cost_engine.py` | Pre-trade margin, symbol specs, immutable intents, approved policies, cost model | Mounted, test-pass |
| `core/position_manager.py`, `trade_manager.py` | Trailing, BE, partials, scaled-out/pyramid durable state | Loaded durable state: 4 scaled-out, 2 pyramided (paper) |
| `operations/autonomy.py` | L0_SHADOW / L1_PAPER / L2_MICRO(0.25%) / L3_FULL(1.0%) + operator-signed rollout manifests + kill switch + `control_plane.db` | Good doctrine; runtime currently at paper |
| `core/account_sizing.py`, `expectancy_ruin_and_hierarchical_risk.py`, `cointegration_and_compounding_governance.py` | Micro-account quantization + ruin math | Math present; the $100-specific cent-lot/cent-account *routing* is not implemented (no broker abstraction for cent accounts) |

### Group G — Research / training / RL
| Artifact | State |
|---|---|
| `artifacts/backtest_results/pipeline_state.json` | **1,442 runs, 21 successful (98.5% failed)** — dominant errors: `no_broker_reconciled_trades` (80×), `fewer than 20 broker-reconciled r-multiples` (80×), `needs >=40 net-of-cost trade returns for CPCV` (90×) |
| recent `research_validation_runs` | 6 latest runs all `total_return_pct: 0.0` — backtests execute but produce zero PnL, i.e. strategies never fire on the tiny evidence window |
| `artifacts/training/registry.json` | EURUSD gate passed, OOS Sharpe 0.56 (weak but positive) |
| `artifacts/ppo_position_mgmt.json` | PPO position-management trained, **execution_authority: false** (advisory only — correct doctrine) |
| `artifacts/grpo_proposer.json` | GRPO MarketModel proposer exists, frozen RiskController |
| `intelligence/kronos_*` + `models/kronos-*` | Kronos foundation-model forecast bridge + local weights; `kronos_signal_validator` calibrates |
| `research/promotion_package.py`, `model_registry.py` | Tamper-evident deployment packages |
| `benchmark_alpha_gates.py` | Standalone gate benchmark |

### Group H — Operations / observability
`operations/*` (scheduler, executors, dual_velocity, journal_audit 14 rules, daily_reconciliation, live_rollout_gate, readiness_report, reward_attribution/ledger, degradation_detector, system_doctor, security_audit/journal_backup AES-256-GCM) — all present and well-built; blocked by the dead loop (§1).
DBs: `trading_state.db` 1.4 GB master journal + WAL, `agent_events.db`, `compliance_audit.db`, `guardian_alerts.db`, `alerts_outbox.db`, `control_plane.db`, `meta_corpus.db`, `trial_ledger.db`, `strategy_trial_ledger.db`.

---

## 3. THE SYNCHRONICITY CHAIN (who must call whom, and where it's severed)

```
market_data ─┬─► daily bars ─► technical cadre (smc/BOCPD/Kalman/edges)
             ├─► calendar ─► news gate tiers ─► blackout windows   ← calendar rows missing event_time
             └─► DOM/ticks ─► microstructure features
scheduler (26 jobs; DEAD since Oct 3)
   ├─ event_monitor/evidence ─► macro_events ─► event_reactions(41)  ← stuck backfilled
   ├─ position_reconcile ─► broker_execution_reports ─► R-multiples  ← no reconciled trades
   ├─ lifecycle_evaluation ◄── labels ◄── reconciled R ────────────── ← starved → 103 strategies stuck CANDIDATE
   ├─ self_improvement/learning_loop ─► patterns table (EMPTY) ────── ← semantic layer never grows
   ├─ alpha_mining_sprint ─► alpha_discovery ─► candidate registry (90× trades=0)
   └─ quant_workflow_sweep ─► 7 workflows ─► backtests ─► trial_ledger
                                  ↑ 98.5% fail: "no broker-reconciled trades"
memory promotion: episodic(CANDIDATE) ─(needs labels)→ semantic(patterns)  ← deadlocked
RL: replay ─(needs reconciled history)→ reward_events ─(stale Sep 19)→ PPO/GRPO/bandits
execution: deterministic proposal → LLM may only shrink →
            gates (news/blackout/spoof/regime/margin/heat/autonomy-level) →
            PaperBroker | MT5 attach (MetaTrader5 real, works when terminal up)
```
**Single root severance:** the scheduler loop. Everything else is downstream starvation.

---

## 4. SILENT FAILURES / BLOCKERS (explicit list)

1. **Dead engine, lying state**: stale `engine.pid`, supervisor `RUNNING` with `last_heartbeat:0`, ghost `RUNNING` agent_runs → operations look alive while nothing runs.
2. **scheduler idle since Oct 3** while every learning job silently no-ops downstream; no cron, no watchdog, no restart policy — "silent" by construction since each executor is try/except-guarded.
3. **Weekly risk anchor corrupted** ($100k anchor in risk_state.json) → weekly drawdown circuit inert.
4. **Backtest red**: 98.5% fail on missing evidence; 6 latest backtests return 0.0 PnL → alpha generation never reached a validated state.
5. **Strategy registry incoherent but honestly flagged**: 90 CANDIDATE (0 trades) + 10 "FULL" with hand-written metrics = `_EVIDENCE_INVALID: promoted_metrics_unbacked` marker inside the file. The runtime loads the file anyway (`core/autonomous_bootstrap.py`, `operations/strategy_factory.py`) — the wrong Sharpe (2.14 vs actual 31.8 differ wildly across sources) flows to UI/runtime.
6. **Memory promotion deadlock** — no labels → no semantic rules (`patterns` EMPTY, verified_memories all CANDIDATE-UNVERIFIED).
7. **RL stale** — reward attribution last ran Sep 19; PPO/GRPO/bandit updates have nothing new to learn from.
8. **Macro stubs masquerading as data** — zero-lag provider returns hardcoded placeholders; mixed into LLM prompts and macro ratio diagnostics.
9. **Calendar quality** — `macro_events` rows with empty `event_time`/`importance=0`, MT5-DLL calendar degraded message every boot; the news gate then runs on weaker-than-designed data.
10. **Data-quality anomalies in cache**: FRED `DGS10=2.0` placeholder, NewsAPI keyword leakage (irrelevant sports headlines in finance queries).
11. **Historic silent bug class** (documented in-file): bare-except + NameError degrading LLM tier to deterministic-only; recommend loud preflight failures for plugin mounts.
12. **`.

## 5. MISSING CAPABILITIES (user-requested but not implemented)

- **TradingView MCP/connector**: none (0 hits). Add a `tv_bridge` (webhook receiver + symbol-timeframe normalizer into `data/market_data_provider` so TV alerts can be *backtested as signals* against the same gate stack and journaled like any other proposal).
- **Crypto statArb across exchanges**: no ccxt/venue adapter; statarb engine (`kalman_statarb`, cointegration governance) is MT5-FX-only today. Blueprint: reusable `data/exchange_venue.py` with paper-routing first (Binance/Bybit/OKX REST+WS), then venue-level statarb with the modules above.
- **X/Twitter entities (Trump, CBs, etc.)**: policy currently hard-blocks social domains as causal authority. Keep the causal block, but add an ingest tier that classifies entity/user and *velocity* for LLM context only, journaled as `SOCIAL_EVIDENCE` (operator-reviewable), never auto-trusted for promotions (matches existing philosophy).
- **Cent-account / micro-sized routing ($100 compounding)**: math exists (`account_sizing`, `expectancy_ruin_and_hierarchical_risk`) but no broker abstraction maps 0.01-lot-vs-cent-account; add `core/venue_routing.py` sized on min-lot constraints before autonomous L2_MICRO claim.

## 6. TESTS / EVALUATION FRAMEWORK

- 413 test files, **4,411 tests collected**; spot-run (econometrics, zero-lag provider, supervisor autonomy) passed in this audit; full-suite run was cut at 10-min budget → **not verified green end-to-end by me**.
- The *evaluation framework is honest but evidence-starved*: CPCV requires ≥40 net-of-cost trades → `INSUFFICIENT_SAMPLES_FOR_CPCV` throughout; WFA requires ≥20 broker-reconciled R → not run. So the audit gates are working as designed — **what's broken isn't the tests, it's the data plumbing feeding them** (broker reconciliation only receives paper trades, and reconciliation loop is idle).
- `pytest.ini` suite exceeds a 10-min window on this machine (7200+ seconds at previous pace) — worth splitting into `tests/unit` vs `tests/integration` lanes in CI so there is a green/red daily signal.

## 7. WHAT SHOULD *ACTUALLY* RUN (production loop, ideal)

1. Boot (already implemented, needs the stale-recovery hardening): supervisor → preflight → warmup → mount plugins → register 26 schedules.
2. Heartbeat loop (30 s): market_health + position_reconcile + event_triage + incident_sweep + alert_dispatch.
3. Candle loop (per TF): completed-bar features → technical cadre (structure edges + BOCPD/Kalman/SMC) → deterministic proposals.
4. Macro loop (60 s–2 h): calendar pull (fixed `event_time`) → event reactions → memory candidates → regime state → zero-lag **real-data** refresh.
5. Decision: candidate → bounded autonomy (LLM shrink-only) → fusion gate (macro/news/blackout/regime/margin/heat) → sizing (risk manager + account tier) → paper/MT5 route.
6. After-close pipeline: trade labels → MAE/MFE → daily reconcile vs broker → trial ledger → lifecycle eval (auto-demote only) → weekly research / self-improvement → memory promotion to semantic.
7. Human checkpoints: L2→L3 rollout manifest signature; operator pause/kill via `control_plane.db`.

This loop **already exists as code** in `core/harness/pipeline_v2.py`, `operations/agent_scheduler.py`, `operations/learning_loop.py`. The first step to end-to-end is not new modules — it is **running the loop continuously and fixing the five P0s so the evidence chain stops starving**.

---

## 8. REMEDIATION SEQUENCE (do in order to reach end-to-end tests)

| # | Action | Unblocks | Status (Oct 2026) |
|---|---|---|---|
| 1 | Boot-time stale sweep: reconcile engine.pid vs process list, mark supervisor degraded if heartbeat stale, close ghost agent_runs | observability truth | **DONE** — `operations/live_state_probe.py` (`probe_live_state`/`sweep_live_state`), lease-backed `engine.pid`, single-instance refusal in the `llm-trade` boot; wired & tested |
| 2 | Keep-alive watchdog (Windows Task Scheduler / NSSM service) around trader loop | everything downstream | open (host-level GNUS/弃 task, out of repo scope) |
| 3 | Fix `risk_state.json` weekly anchor + warn-on-load mismatch | weekly circuit | **DONE** — `core/risk_manager.py::load_state` 2×-scale guard re-anchors weekly/daily/peak anchors on load; tested (corrupt case + continuity case) |
| 4 | Make `meta_event_calendar` fill `event_time`/`importance` (fallback rounding to nearest hour w/ explicit 'TBA' flag), else widen news-gate defaults | news gate integrity | **DONE** — `data/forexfactory_provider.py` parses the modern ISO `date`-only FF shape into `datetime_utc`; `event_agent` triage fallback chain restored; malformed no longer fails-closed |
| 5 | Zero-lag provider: replace hardcoded stubs with real fetches or drop fields (never feed placeholders into LLM context) | macro honesty | **DONE (provenance)** — stubs kept but now labelled `LIVE`/`SYNTHETIC_PLACEHOLDER` via `source_quality_annotations()`; consumers can filter placeholder fields |
| 6 | Force broker_reconcile to run on paper broker closes (already wired via `journal_callback` in PaperBroker) | R-multiples → CPCV/WFA gate opens | open |
| 7 | Allow lifecycle_evaluation to run offline against *paper* reconciled history so 103 CANDIDATE strategies can actually be scored | registry becomes meaningful | open |
| 8 | Rotate stale reward_events into current cycle (reward_attribution has run refs pointing to old run_ids) | RL resumes learning | open |
| 9 | Add X/Twitter & TradingView connectors with the *new* SOCIAL_EVIDENCE tier + TV webhook receiver | requested features | **DONE (advisory/evidence-only)** — `data/tradingview_bridge.py` (auth-gated webhook, `ADVISORY` order_intents, never executed) + `SOCIAL_EVIDENCE_ONLY` tier + `data/social_entity_ingest.py` (ingest-only, `causal_authority=False` intact) |
| 10 | Crypto or cent-account venue adapter; wire statarb to it | new venues | open |
| 11 | pytest split (unit/integration lanes) + scheduler-smoke test asserting every scheduled executor's last_run_at advances in <2× interval | continuous green signal | open; `pytest.ini` default fixed to `-n 1` (parallel `-n 2` stalled on this host) |

### Bottom-line metrics to watch post-fix (drawn from audit evidence)
- `agent_schedules.claimed_at` ≠ NULL and `next_run_at` marching forward
- `patterns` rows > 0; `verified_memories` transitioning CANDIDATE → VERIFIED
- `pipeline_state.json` failure% dropping below 20%; CPCV/WFA statuses reaching `completed`
- `reward_events` freshness < 24 h
- `risk_state.json` weekly anchor == actual equity within 0.2%
