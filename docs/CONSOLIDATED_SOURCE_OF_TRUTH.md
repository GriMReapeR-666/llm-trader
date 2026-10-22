# CONSOLIDATED SOURCE OF TRUTH
## Autonomous LLM Trader — Architecture, Components, PRD, Contracts, Knowledge & Implementation Gap Matrix

**Document date:** 2026-09-26 (v2 — full-coverage rebuild; v1 covered 66 of 141 content files, v2 covers 187 of 187)
**Method:** exhaustive read of all 187 files in `docs/` (72 top-level entries) in 9 parallel extraction passes + line-level verification against the live tree, plus a second full recovery pass for content lost to output truncation in v1.
**Notation:** **[CODE]** = verified in code with `file:line`. Unqualified claims are doc-sourced with the doc named.

**Authority order** (from the PRD reading contract, footer):
> *"Repository is the single source of truth: if this document and the code disagree, treat the code as authoritative and file the drift as a documentation defect."*

Applied order: **code > this document > PRD > architecture docs > audits > knowledge corpus > raw research .txt**

**Relationship to existing docs:** this document supersedes `SOURCE_OF_TRUTH.md`, `ARCHITECTURE.md`, `AUTONOMOUS_TRADING_SYSTEM_MASTER_ARCHITECTURE.md` and `UAQP_STATE_TENSOR.md` **for navigation**. It does not supersede `SYSTEM_SOURCE_OF_TRUTH_PRD.html` (still the code-anchored PRD) or `TRADING_MEMORY_SPEC.md` (normative memory spec).

**Companion — `CODEBASE_DEEP_DIVE.md`:** this document covers **what `docs/` claims**. `CODEBASE_DEEP_DIVE.md` covers **what the code, runtime state, databases, UI and logs actually do** — Parts 0-8, 32 headline findings, a 16-item CRITICAL / 30-item HIGH bug ledger, per-module `file:line` logic audits, the 28-database runtime census, the frontend rules-of-hooks crash, the performance reality check, a real pytest baseline (3,816 passing / 10 order-dependent flakes out of 3,836, after ten fixes — including the discovery that `pytest.ini` shipped `-n auto` on a 7.4 GB host, which was the single root cause of every MemoryError, pagefile error and native access violation), and the five highest-value fixes. **Where the two disagree, the deep dive is the later and more specific reading; treat any doc claim it contradicts as a drift item for Part 13.**

**How to read this document:**

| Part | Content |
|---|---|
| 0 | Executive summary + the eight questions that matter |
| 1-2 | System identity, six doctrines, formal model `Γ_deployed=(Γ,Ψ)`, agent roster, three graphs, layer map with verified line counts |
| 3 | Runtime control flow — boot sequence, pulse loop, the 26-stage cycle |
| 4-6 | The 13 authority gates, the 23 validator rules, the 19-factor sizing ladder, the AI decision contract, execution & trade management |
| 7-9 | Data fabric, memory/KG/state tensor, autonomy & operations |
| 10-11 | The knowledge corpus (six-layer architecture, algorithm catalogue, formulas) and the alpha factory |
| 12 | Operator surfaces — 64 API routes, 40 TUI screens, the knowledge-graph studio |
| **13** | **The implementation gap matrix — 8 P0 + 7 P1 + 15 P2 + the wiring-gap table + 40 doc-drift items** |
| 14-16 | Operations/security/runbooks, the 72-entry doc inventory, the five highest-value actions |
| **17-18** | **PRD §07-§21 subsystem reference, recovered — data quality, news triage, LLM router, causal intelligence, memory architecture, orchestration, reflection, alpha mining, validation gates, ML/RL, promotion, persistence, autonomy, operations** |
| 19 | The 12-agent registry + the 163-item task list with the GOAL scorecard |
| 20 | The 5 architecture diagrams with per-diagram code reality checks |
| 21-24 | Portfolio-optimization series (8 papers), institutional-vs-retail + dispersion bias, the quant-KB module ABI, the raw research corpus |
| **25** | **Coverage audit of this document + the three findings that emerged only from the deep pass** |

---

# PART 0 — EXECUTIVE SUMMARY

| Question | Answer |
|---|---|
| What is it? | An MT5-connected, LLM-proposed, deterministically-gated autonomous FX/commodity trading system in Python, with a Bun/OpenTUI operator deck. |
| Does it run? | **Yes.** 5,395-line `mt5_trader.py` façade; 913 Python files / 220,576 lines; 3,686 collected tests; 64 FastAPI routes; 40 TUI screens. |
| Is the design implemented? | **Structurally yes, functionally ~70 %.** All 13 authority gates, all 26 cycle stages, all 23 validator rules and the full 19-factor sizing ladder exist as code. **8–9 of 19 sizing factors and 4 of 23 validator rules are unreachable at runtime** because the production call site omits the `market` argument. |
| Is live execution functional? | **No — one blocking bug.** `core/order_execution.py:704` calls `validate_pre_trade_safety(symbol)` with 1 of 5 required args and reads the wrong result key ⇒ every live pending order raises `RuntimeError`. Undocumented anywhere in `docs/`. |
| Are the docs current? | **No.** 12 audit items marked OPEN are actually fixed. 7 of 21 spot-checked TODO `[~]` markers are stale. The PRD's source-file count is understated 1.26×. Two documented constants do not exist in code. The "134 economic dynamics" figure is wrong (actual: 83 edges / 96 entities). |
| Biggest structural risk | **KG decay** — `macro_graph.py:307` demotes approved edges to CANDIDATE. The PRD shows re-validation *is* wired, but in the **caller** (`causal_validator.validate_candidate_edge`, `llm_orchestrator.py:3277-3313`), so the residual risk is whether `causal_edge_ids` is ever attached to proposals. |
| Biggest architectural debt | 18 import sites reference **11 modules that were never created**; all are swallowed by `try/except ImportError`, producing quiet capability loss — e.g. the 1,224-line deterministic validator has **no live-health monitor**. |
| 43 strategies registered | **0 implementations.** `strategy_registry.json` has 43 entries, none with a code pointer; `strategies/loader.py:78-83` skips every file present ⇒ `discover()` returns `{}`. |
| 🔴 Worst undocumented defect | **The non-monotonic risk ladder.** `L1_PAPER max_risk 1.00 %` > `L2_MICRO max_risk 0.25 %` (`operations/autonomy.py:38-43`). An automatic `DEMOTE` on drawdown (L2→L1) therefore **raises** permitted risk 4× while lowering authority. Flagged in no doc and no audit. See §25.3. |

---

# PART 1 — SYSTEM IDENTITY & DOCTRINE

## 1.1 The nine identity dimensions (PRD §01.1)

| Dimension | Implementation | Anchor |
|---|---|---|
| Broker/venue | MT5 terminal via official Python API; primary EURUSD, XAUUSD + scan watchlist | `mt5_trader.py` `MT5Client:875` |
| Decision maker | Multi-model LLM (OpenRouter / TokenRouter / RoutesMe / Cerebras / NVIDIA NIM) prompted with a compact JSON "TOON" payload | `intelligence/llm_orchestrator.py:1150` `get_decision` |
| Permission layer | Deterministic validator + risk manager + portfolio arbiter + margin safety + execution policy — **no LLM in this path** | `core/trading_safety.py`, `core/risk_manager.py` |
| Execution | Paper broker (in-process) or real MT5 pending orders with dynamic slippage and urgency routing. **Direct market entries disabled by design** | `core/order_execution.py:290` |
| Position management | Break-even, ATR trailing, partial scale-outs, time stops, pyramiding — all authority-checked | `core/trade_manager.py:163` |
| Memory | L0 episodic → L1 atomic facts → L2 scenario playbooks → L3 strategy knowledge + semantic + case-study retrieval | `intelligence/trading_memory.py` |
| Research/ML | GA alpha mining, factor library, Kronos, PPO/meta-RL, contextual bandits, supervised models, CPCV/DSR/overfit/promotion gates | `research/`, `core/alpha_*.py` |
| Autonomy | Supervisor kernel, levels L0–L3, circuits, scheduler, dual-velocity lanes, watchdog, crash/restart recovery | `operations/autonomy.py`, `operations/supervisor.py` |
| Operator surfaces | FastAPI + WebSocket, React/OpenTUI command deck, Tkinter terminal, static HTML dashboards, standalone visualizer | `ui/gui_api.py`, `tui_opentui/`, `visualizer/` |

## 1.2 The one-sentence contract (PRD §01.1, verbatim)

> "The LLM proposes; `DeterministicValidator` + `RiskManager` + `PortfolioRiskArbiter` + margin/broker gates grant or deny permission; the journal (`trading_state.db`) is the source of truth; missing or stale data means HOLD."

## 1.3 The six non-negotiable doctrines (PRD §01.3)

1. **Stateless agents, stateful graph** — each stage reads a point-in-time slice, computes, writes an immutable event, ends. Continuity lives in the state tensor, never process memory.
2. **The LLM is a proposer, never an executor** — may only emit a `TradeProposal`-shaped decision. *"There is no LLM path that reaches `place_market_order` directly."*
3. **Fail-closed** — missing ticks, stale marks, unknown spread, unmeasurable heat, absent broker spec, provider failure, unapproved policy → HOLD. *"Unknown" is never "safe."*
4. **The journal is the source of truth** — every proposal (approved *and* rejected), lifecycle transition, fill and management action persisted with its reason. *"Surfaces render the journal; they never fabricate values."*
5. **Research is advisory until promoted** — Kelly/RL/Kronos/HMM cannot mutate live risk parameters or size without the research-influence gate + promotion funnel. *"The factory may demote its own work, but can never self-approve live authority."*
6. **Autonomy is bounded by levels** — L0 shadow → L1 paper → L2 micro-live (0.25 % risk/trade) → L3 full live (1 %).

> **⚠ Doctrine 3 is violated in 6 places** — see §12.2.
> **⚠ Doctrine 5 is enforced only by convention.** `execution_authority: False` is *set* at `llm_orchestrator.py:599,879` but nothing *enforces* that an agent tool cannot mutate risk state; `core/trading_tools.py:1336` exposes `clayton_copula_tail_risk` as a callable tool.

## 1.4 Measured scale — REAL vs PRD-claimed

| Scope | PRD claim | **REAL (verified)** | Drift |
|---|---:|---:|---|
| Source layers (`core/ intelligence/ data/ operations/ research/ strategies/ scripts/ ui/`) | 442 files / 152,883 ln | **557 files / 190,501 ln** | PRD **understates 1.26×** |
| Test suite | 360 files / 51,401 ln | **366 files / 62,574 ln** | PRD accurate |
| All `*.py` in tree | 908 | **913 files / 220,576 ln** | PRD accurate |
| Collected tests | — | **3,686 items / 351 files** | — |
| `mt5_trader.py` | ~7,000 ln | **5,395 ln** | PRD **overstates 1.30×** |
| `intelligence/llm_orchestrator.py` | ~10,000 ln | **7,700 ln** | PRD **overstates 1.30×** |
| API server path | `gui/gui_api.py` | **`ui/gui_api.py`** (3,337 ln) | PRD path **wrong** |
| `SWING_MAGIC`/`SCALP_MAGIC` | in `mt5_trader.py` | **`core/trading_safety.py:34-35`** | PRD path **wrong** |

`audit/TURN_0_BASELINE.md` claims *"99.7 % of 2,993-test suite"* green (2026-09-09). Actual collected = **3,686 (+23 %)**. The 99.7 % figure is **unverifiable as stated** because `tests/test_fallback2.py` aborts collection with `FileNotFoundError: 'test_si_state4.json'`.

---

# PART 2 — FORMAL ARCHITECTURE

## 2.1 The formal model (PRD §02.1, verbatim)

```
Γ_deployed = ( Γ , Ψ )

  Γ = ( A , E , S , Φ , Θ , T )        # the trading graph
      A = autonomous agents (stateless stages)
      E = relational edges (data / permission / feedback)
      S = authoritative state tensor  (SQLite journal + keyed state store + JSON)
      Φ = state-transformation functions (deterministic; replayable)
      Θ = orchestrator scheduling priority
      T = trigger mechanisms (candle close, event, schedule, circuit)

  Ψ = External Guardian — out-of-process, fail-closed kill authority
      independent of Γ so that a defect inside Γ cannot silence it
```

Two implementation facts:
- **(a)** `start_guardian()` launches **after** bootstrap and **only when not paper mode** (`mt5_trader.py:4251-4257`). Thresholds hardcoded in `core/external_guardian.py` (`HEARTBEAT_TIMEOUT_S: float = 120.0` at `:85`), **not** read from the LLM's configuration.
- **(b)** Φ is deterministic and LLM-free — everything in `core/` that validates/sizes/arbitrates/routes is replayable without an API key.

## 2.2 The agent roster (PRD §02.2)

Greek glyph = identity; bus stage strings = the machine contract.

| Glyph | Agent | Bus stages in code | Implementation | Verified |
|---|---|---|---|---|
| δ | DataCollector | `DATA_COLLECT` (fetching/fetched/error), `DATA_QUALITY` (checked/rejected) | `data/market_data_provider.py`, `data/data_quality.py` | ✅ `llm_orchestrator.py:5863-5870` |
| ω | MarketModel | `NEWS_GATE` (checked/blocked), `LLM_DECISION` (proposal_received) | `intelligence/llm_orchestrator.py:1150` | ✅ |
| σ | TechnicalNet / signal builder | `SIGNAL_BUILD` (feature_snapshot) | `intelligence/feature_builder.py`, `core/technical_indicators.py` | ✅ `:6064-6074` |
| μ | RegimeDetector | `REGIME_DETECT` (regime_classified) | `intelligence/regime_classifier.py`, `intermarket_regime.py` | ✅ `:6153-6171` |
| ρ | RiskController | `RISK_CHECK` (validated), `POSITION_SIZE` (sized) | `core/trading_safety.py`, `core/risk_manager.py` | ✅ `:2655`, `:7357` |
| ε | Execution | `ORDER_SUBMIT` (order_placed) | `core/order_execution.py` | ✅ `:7509-7520` |
| κ | Audit / journal | `AUDIT_LOG_ERROR` (audit_log_failed) | `core/compliance_audit.py`, `operations/journal_audit.py` | ✅ |
| α | AlphaMiner advisor | alpha/edge lifecycle; alpha-mining enrichment of payload | `core/alpha_discovery.py`, `research/alpha_miner/` | ✅ `:6783` |
| β | PortfolioAgent | allocation caps, exposure buckets, zero-volume rejections | `core/hierarchical_portfolio.py`, `operations/agents/portfolio_agent.py` | ✅ `:7338` |
| ζ | Self-improvement / meta agent | code versions (`ζ_code_versions.json`), improvement pipeline | `core/self_improvement_loop.py`, `operations/improvement_pipeline.py` | ✅ |

## 2.3 The three graphs (PRD §02.3)

- **Trade Graph (authoritative):** `mt5_trader.py` pulse loop → `LLMOrchestrator.execute_cycle` → gates → order. Authority and journaling live here.
- **Deliberative Graph (shadow):** LangGraph pipeline, **observation-only** copy of the reasoning chain each pulse. Writes observations, never orders. The orchestrator calls the compiled graph and **discards the result** (`mt5_trader.py:~4466`: *"Deliberative Graph shadow cycle (observations only)"*).
- **Reflection Graph (post-trade):** on closure — equity state update → coach → scenario consolidation → macro feedback. Updates beliefs, not positions.

**Deliberative node sequence** (declared `intelligence/deliberative_graph.py:65-95`; classes `intelligence/graph_nodes.py`):
`START` → `technical_net`(65) → `macro_analyst`(129) → `memory_retriever`(534) → `market_model`(568) → `signal_arbitrator`(683) → `debate_protocol`(773) → `risk_controller`(887) *gate* → branch → `order_execution`(1105) **or** `rejection_sink`(1172) → `END`
Reflection: `equity_state_update`(`reflection_graph.py:20`) → `quantitative_coach`(`graph_nodes.py:1247`) → `scenario_consolidator`(`reflection_graph.py:59`) → `macro_feedback`(`reflection_graph.py:81`)

**Deliberative-graph node thresholds** (from `AUTONOMOUS_TRADING_SYSTEM_MASTER_ARCHITECTURE.md` §3.2, DESIGN-ONLY where marked):
`technical_net` freshness ≤ 5000 ms, spread ≤ 2.5 pips, zero-volume reject, EMA(12,26,50,200)/RSI(14)/MACD(12,26,9)/ATR(14)/ADX(14)/HMA/Hurst · `macro_analyst` 134-node causal graph + Laya ModernBERT-421M · `market_model` provider order Cerebras → OpenRouter → NVIDIA NIM → Local · `debate_protocol` agreement **> 0.70** required · `risk_controller` 1 % risk/trade, heat cap **6.0R** (⚠ disagrees with the main loop's 4.0R) · `rejection_sink` codes `DATA_QUALITY_FAIL`, `EXPOSURE_LIMIT_EXCEEDED`, `SPREAD_TOO_WIDE`.

> **⚠ M-13 half-open:** `graph_nodes.py:261` now calls `find_causal_paths(...)` ✅, but `:281` `cesi_score = 0.85 if edge_count > 0 else 1.0` is **still a hardcoded literal**.

## 2.4 Layer map **[CODE — verified line counts]**

| Layer | Role | Authority | Files | Lines | Representative modules |
|---|---|---|---:|---:|---|
| `mt5_trader.py` | CLI façade, config, MT5 client, boot, pulse loop | process owner | 1 | 5,395 | `Config:618`, `MT5Client:875`, boot `:4018-4257` |
| `core/` | Deterministic trading primitives | **hard authority** | 128 | 51,184 | trading_safety (1,224), risk_manager (1,138), order_execution (851), trade_manager (1,212), margin_safety (292) |
| `core/harness/` | Microkernel: context, disposables, sandbox, pipeline v2 | composition | 13 | 3,734 | kernel (163), disposable (86), sandbox (88), stage_contracts (43), pipeline_v2 (812), workflows (1,051) |
| `data/` | Ingestion, providers, quality, calendars, news, LLM router | supply | 30 | 12,282 | market_data_provider, data_quality (288), llm_router (623) |
| `intelligence/` | Causal graph, macro, memory, prompts, decision loop, reflection | **proposes/advises** | 74 | 31,530 | llm_orchestrator (7,378), trading_memory (1,630), macro_graph (710) |
| `intelligence/analytical_frameworks/` | 7 discipline engines (v01–v07) | advisory | 9 | 1,866 | genuinely wired via `core/trading_tools.py:2033-2169` |
| `research/` | Backtests, validation, ML/RL, promotion, registry | **zero live authority** | 125 | 36,143 | alpha_miner (31 files), ppo_trainer, kronos_*, broker_replay |
| `operations/` | Autonomy, scheduling, audits, reconciliation, alerts | governance | 56 | 11,925 | autonomy (251), journal_audit (872), skill_registry (821) |
| `strategies/` | Strategy base + loader + registry glue | packaged logic | 5 | 715 | **0 strategy implementations** |
| `ui/` | FastAPI (3,337 ln, 64 routes), Tkinter, dashboards | read + control | 20 | 7,301 | gui_api.py |
| `gui/` | React/Vite/TS app + built `dist/` bundle (618 KB JS) | read + control | 0 py | — | App.tsx, 8 components |
| `tui_opentui/` | Canonical operator deck (Bun + OpenTUI, 40 screens) | read + control | 0 py | — | App.tsx, transport/live.ts (72 KB), GraphScreen.tsx (179 KB) |
| `visualizer/` | Standalone knowledge-graph studio (498 KB single file) | reference | 0 py | — | **0 code references it** |
| `tests/` | Verification incl. component matrix | proof | 365 | 52,342 | 3,686 items |
| `graphify-out/` | AST knowledge graph | reference | 1,566 | — | `graph.json` 28.9 MB |
| `quant_knowledge_framework/` | Stale fork of `trading_knowledge_base/` | **orphaned** | 41 | 2,459 | 1,117 files, **0 importers** |

**Import-graph closure (AST-verified):** `mt5_trader.py` → `execute_cycle` closure = **368 modules live**; any-entrypoint closure = **445**; **131 modules have zero importers**. The project uses flattened `sys.path` imports (`from risk_manager import …` resolves to `core/risk_manager.py`).

---

# PART 3 — RUNTIME CONTROL FLOW

## 3.1 Boot sequence (PRD §03.4 vs **[CODE]**)

| # | Step | Anchor | **Verified status** |
|---|---|---|---|
| 1 | `Config.from_sources()` + logging (`TUILogHandler`) | `mt5_trader.py:618` | ✅ but resolution is env→AccountManager→config.ini; **no env override for risk/cooldown/log_level** |
| 2 | Mode selection + journal mode string; account fingerprint hashed (credentials never persisted) | `:3896-3900` | ✅ |
| 3 | Harness profile mount → `trading_store` plugin; fallback inline services + JSON migration | `:3840-3849` | ✅ |
| 4 | `RiskManager` seeded with **positive equity** | `:3661-3697` | ⚠ **PARTIAL** — 4-rung ladder ending in hardcoded `10000.0` at `:3696`; forced write-back only on the fallback path (`:3892-3896`) |
| 5 | Live sandbox capability gate (`PermissionLevel.ORDER_WRITE`) | `:3905-3922` | ⚠ **SCOPE GAP** — gate only runs `if harness_ctx is not None and has("sandbox")`; the inline-fallback path never evaluates it |
| 6–8 | **MISSING FROM THE PRD TABLE** | — | ❌ PRD defect |
| 9 | Supervisor boot checks (`config_loaded, orchestrator_ready, journal_reachable, autonomy_state, secrets_hygiene_warnonly`) | `:4018-4046` | ⚠ **not fail-closed** — `except` at `:4045-4046` only logs |
| 10 | Task queue `migrate_from_intervals(DEFAULT_INTERVALS)` | `:4052-4054` | ⚠ warn-only |
| 11 | κ compliance audit (hash-chained append-only) | `:4064-4069` | ⚠ warn-only |
| 12 | Crash recovery: unclean-shutdown scan, snapshot restore, atexit/SIGTERM | `:4074-4096` | ⚠ warn-only |
| 13 | **`AutonomousBootstrap.run()`** → `ready/phase/duration/quality_score`; not ready ⇒ print blockers + `exit(1)` | `:4101-4141` | ⚠ **only blocks if it completes** — `:4142-4143` swallows *any* exception as "Autonomous bootstrap skipped" |
| 14 | Startup training pipeline | `:4144-4246` | ⚠ **PARTIAL** — walk-forward **init only** (`:4155` sets `"initialized"`, no validation run); CPCV logs "available" but never invokes the runner (`:4163`); **GA alpha mine DOES run** (`:4184` `population_size=15, generations=1, min_ic_threshold=0.005`) |
| 15 | **Θ External Guardian starts** (after bootstrap, non-paper only) | `:4251-4257` | ✅ `HEARTBEAT_TIMEOUT_S = 120.0` at `core/external_guardian.py:85` |
| 16 | Enter pulse loop | `:4259-4261` | ✅ |

> *"Why this ordering is a safety feature"* (PRD): sizing needs positive equity (4 precedes any cycle); the guardian must not watch a loop that does not exist yet (15 follows 13); bootstrap may hard-stop an incapable process (13).

## 3.2 The outer pulse loop — 10 steps (PRD §04.1, all ✅ verified)

| # | Step | Anchor |
|---|---|---|
| 1 | Holiday check → `pulse_maintenance` (**does not exit**; analysis skipped) | `:4268-4274`, impl `:5544` |
| 2 | Autonomous research tick every 10th cycle via `operations.autonomous_pipeline.run_pipeline` — **before** the holiday gate so research runs 24/7 | `:4285-4292`, impl `operations/autonomous_pipeline.py:68` |
| 3 | Symbol selection `MarketScanner.scan(timeframe, limit_to_watchlist)` → atomic `scanner_state.json` | `:4325-4350` |
| 4 | Pre-pulse macro ingest hoisted **once** before the strategy thread pool | `:4487-4492` |
| 5 | Strategy fan-out via `resolve_strategy_cycles` (swing/scalp/both) | `:4416`, def `:1216-1230` (⚠ duplicated dead copy at `llm_orch:7684-7698`) |
| 6 | Per-strategy isolation: `try/except` per worker, `KeyboardInterrupt` re-raised | `:4451-4485` |
| 7 | Reconnect `client.reconnect_if_needed()` | `:4410-4414` |
| 8 | Pipeline selection — `PIPELINE_V2` default ON → `execute_cycle_v2`; `--no-pipeline-v2` / `PIPELINE_V2=0` forces legacy | `:4419-4423` |
| 9 | Circuit evaluation `evaluate_circuits(_risk_snapshot)` | `:4676-4699`, impl `operations/autonomy.py:202`, `CIRCUITS` table `:189-199` |
| 10 | Cadence — `--poll-interval` **default 5.0 s** | `:1514` |

> **⚠ B9 defect:** the circuit snapshot hardcodes `llm_outage_minutes: 0.0` (`:4682`) and `data_stale_seconds: 0.0` (`:4683`), so **2 of 5 circuits can never trip** from this call site.

> **⚠ The holiday gate is a 6-line hardcode** — `data/api_manager.py:660-673` checks only `weekday >= 5` or `mm-dd in {01-01, 12-25}`. `data/mt5_calendar_enhanced.py` (506 ln) has **no holiday/session/blackout logic at all** and is never called from the cycle path. Separately, `conditions.allowed` from `evaluate_market_conditions` is reused for **both** `session_allowed` and `liquidity_ok` (`llm_orch:2631-2632`) — the same boolean drives two different rules.

**Cadence doctrine (PRD §04.1):** *"The pulse is not the decision rate. Most pulses end in the maintenance prefix plus an early HOLD because no new completed candle exists. Decision rate is driven by candle closes and events; pulse rate drives reconciliation, protective management and telemetry."*

## 3.3 The 26-stage `execute_cycle` chain (PRD §04.2)

`execute_cycle` def `intelligence/llm_orchestrator.py:5751`, ends `:7680`.

| # | Stage | Line | Verified |
|---:|---|---:|---|
| 1 | Supervisor heartbeat + Ψ guardian progress + drain operator control queue | 5762 | ✅ |
| 2 | Autonomy gate `autonomy.trading_allowed()` → `CYCLE_SKIPPED_AUTONOMY` | 5780 | ✅ |
| 3 | Metrics export `write_metrics()` + `CYCLE_STARTED` | 5787 | ✅ |
| 4 | Maintenance prefix `_run_maintenance_prefix` — velocity poll, reconcile pending, reconcile open (**live only**), memory dispatch to slow lane, protective position tick, worker heartbeat | 5804 (def 5677) | ✅ |
| 5 | Working-memory refresh (20 items / 6000 chars) under `working_memory:<strategy>:<symbol>` | 5811-5816 | ✅ |
| 6 | Bounded schedule runner `_run_schedules_bounded` | 5824 | ✅ |
| 7 | Trigger gate: completed-candle lease | 5827 | ✅ |
| 8 | Duplicate-trigger suppression via cycle key `{strategy}:{symbol}:{timeframe}:{closed_bar}` | 5831-5833 | ✅ |
| 9 | Holiday guard — **lease RELEASED, not consumed** | 5848-5854 (`release_task(..., reason="market_holiday")` at 5951) | ✅ |
| 10 | `fetch_data` + `DATA_COLLECT` fetching/fetched/error | 5863-5870 | ✅ |
| 11 | Data-quality gate (`require_live_quote = not paper_mode`) | 5877-5896 | ⚠ **fail-OPEN on exception** (`:5897-5898`) |
| 12 | News trading gate + regime-correlation blackout windows | 5903-6002 | ⚠ **fail-OPEN on exception** (`:6001-6002`) |
| 13 | Feature snapshot, indicators, SMC (CHOCH, order blocks, liquidity pools, premium/discount), regime classification, opportunity-bus publication | 6047-6171 | ✅ |
| 14 | Auto-tools, alpha-mining enrichment, causal advisory, intermarket proxy, quant-survival audit | 6056-6783 | ✅ |
| 15 | **LLM decision** `get_decision(toon)` under budget lease | 6789-6910 | ✅ |
| 16 | **Execution shield / decision gate** | — | ❌ **NOT FOUND** — no such module/symbol; only a string inside a bounded-autonomy reasoning string at `:6871` |
| 17 | Decision journaling: `LLM_DECISION` bus event, proposal artifact, `complete_task` | 6952-7011 | ✅ |
| 18 | `_validate_proposal` → `DeterministicValidator` | 7171 (def 1963) | ✅ |
| 19 | Risk pre-checks: cooldown, rate limit, active-trade limit, consecutive-loss breaker | 7215-7235 | ✅ |
| 20 | **Sizing** `risk.calc_position_size(...)` | 7357-7364 | ⚠ **no `market=` arg — 8–9 of 19 ladder factors dead** |
| 21 | **Arbiter** `self.arbiter.permit(...)` | 7326-7329 | ⚠ **no `symbol=`/`side=` — netting/beta limits never evaluated** |
| 22 | P8 allocation cap `apply_portfolio_allocation_cap` (reduce only; cap 0 → `zero_volume_rejected`) | 7338-7357 | ✅ (fails open by design `:2825-2831`) |
| 23 | Immutable execution plan + margin preflight | 7367 (def 2833) | ✅ `ValueError` → HOLD + `REJECTED` |
| 24 | Margin safety gate (`assess_from_mt5` live / `assess` paper) + cost-aware net-RR gate | 7374-7417 | ✅ |
| 25 | Order submission, rejection recording, lifecycle events, telemetry | 7420-7635 | ⚠ live path blocked — see §6.3 |
| 26 | Display/narrative update | 7145-7164 | ⚠ `CYCLE_COMPLETE` is emitted by the **caller** at `mt5_trader.py:4468-4472`, not inside `execute_cycle` |

## 3.4 What a HOLD does — and never does (PRD §04.3)

| It **does** | It **never does** |
|---|---|
| Journal the reason with symbol/strategy/timeframe context via `operations` events and the `proposals` table | Place, modify or cancel an order |
| Complete or fail the leased task so the same candle is not retried forever | Write a `trades` row — **only executed fills create trades** |
| Emit a bus/telemetry event so the deck can display *why* nothing traded | Mutate risk parameters |
| Leave the lease re-evaluable | Silently swallow provider budget — the attempt records success/failure so `RESERVED` cannot stick |

---

# PART 4 — THE AUTHORITY LADDER (13 GATES)

> *"Read this as the security model. The LLM sits between stage 5 and stage 6. It can only add a proposal; every stage after it is deterministic Python that must affirmatively permit the trade."* — PRD §02.5

| # | Gate | Module | Call site | Typical veto | Status |
|---:|---|---|---|---|---|
| 1 | Autonomy gate (halt/pause/circuit) | `operations/autonomy.py:180` | `llm_orch:5780` | `CYCLE_SKIPPED_AUTONOMY` | ✅ |
| 2 | Trigger gate (completed candle, dedup) | `llm_orch:2959` | `:5827-5833` | "No new completed candle" | ✅ |
| 3 | Holiday / session / blackout | `data/api_manager.py:660` | `:5848` | Market Holiday | ⚠ **6-line hardcode**, not a calendar |
| 4 | Data quality + freshness | `data/data_quality.py:37` | `:5878` | stale / NaN / zero-volume | ⚠ **fail-open on exception** |
| 5 | News gate | `core/news_trading.py:116`, `news_regime_correlation.py:204` | `:5914-5938` | news phase block | ⚠ **fail-open on exception** |
| 6 | **Deterministic validator** (23 rules) | `core/trading_safety.py:875` | `:2655` | proposal rejected → HOLD + `REJECTED` | ⚠ **19/23 live** |
| 7 | Cooldown / rate limit / active-trade limit / consecutive-loss breaker | `core/risk_manager.py:641,668,695,411` | `:7215-7235` | skip → HOLD | ✅ |
| 8 | **Position sizing** (19-factor reduction-only ladder) | `core/risk_manager.py:861` | `:7357` | size 0 → REJECT | ⚠ **10/19 live** |
| 9 | **Portfolio risk arbiter** | `core/trading_safety.py:1223` | `:7326` | `permit()` false → HOLD | ⚠ **netting/beta limits dead** |
| 10 | P8 allocation cap | `llm_orch:2688` | `:7338` | cap 0 → `zero_volume_rejected` | ✅ |
| 11 | Execution plan + margin safety | `core/execution_planning.py:73,127`, `core/margin_safety.py:94,239` | `:7367-7417` | plan `ValueError` / margin refused | ✅ |
| 12 | Broker preflight & execution policy | `core/broker_preflight.py:59`, `core/execution_policy.py:83,162` | `:2620,2860` | no approved policy → no order | ⚠ `has_contract_changed` **never called** |
| 13 | Live execution safety supervisor | `core/execution_safety_supervisor.py:114` | `order_execution.py:704` | circuit breaker trip | 🔴 **BROKEN — blocks all live orders** |

## 4.1 Gate 6 — the DeterministicValidator, all rules

Docstring: *"Hard rule gate. All unavailable critical data is rejected, never assumed safe."*

**Defaults** (`core/trading_safety.py:876-883`): `min_rr=2.0` · `min_confluence=3` · `min_net_ev_r=0.0` · `min_ev_samples=30` · `default_friction_r=0.05` · `max_changepoint_probability=0.45` · `min_walk_forward_efficiency=0.0`. Overridable per proposal through the `market` dict.

| # | Rule | Reason string | Line | **LIVE?** |
|---:|---|---|---:|---|
| 1 | Kill-switch latch | `kill switch active` | 961 | ✅ |
| 2 | Session / market hours | `market not open` | 962 | ✅ |
| 3 | Tick freshness (`max_tick_age_ms`=5000) | `stale tick` | 963 | ✅ |
| 4 | Broker/local clock health | `broker/local clock health rejected tick` | 964 | ✅ |
| 5 | Symbol tradability | `symbol not tradable` | 965 | ✅ |
| 6 | Spread ceiling | `spread too wide` | 966 | ✅ |
| 7 | Session / rollover filter | `session/rollover no-trade filter` | 967 | ✅ |
| 8 | Liquidity filter | `liquidity filter rejected entry` | 968 | ⚠ **same bool as #2** (`llm_orch:2631-2632`) |
| 9 | Microstructure filter | `microstructure filter rejected entry` | 969 | ✅ |
| 10 | Planned-entry drift tolerance | `price moved beyond planned-entry tolerance` | 978 | ✅ |
| 11 | Trigger presence | `no completed-candle/breakout trigger` | 980 | 🔴 **NEUTRALISED** — caller hardcodes `"completed_candle": True` (`llm_orch:2618`) |
| 12 | Minimum stop distance | `stop too close` | 987 | ✅ |
| 13 | Reward/risk vs `setup_min_rr` | `reward/risk below setup minimum` | 990 | ✅ |
| 14 | High-impact event window | `setup excludes high-impact event window` | 993 | ✅ |
| 15 | Deterministic confluence ≥ `min_confluence` | `insufficient deterministic confluence` | 996 | ✅ |
| 16 | News safety | `news data missing or high-impact event` | 998 | ✅ |
| 17 | Setup cooldown (`setup_cooldown_seconds`=900) | `setup recently considered` | 1001 | ✅ |
| 18 | Lifecycle gate on `setup_id` | lifecycle block reason | 1005-1016 | ✅ (fail-closed on error) |
| 19 | Causal lag confidence (floor 0.35) | `lag confidence decayed below threshold` | 1029 | ✅ |
| 20 | Net EV after friction (**only when `sample_count ≥ 30`**) | `rolling net expectancy … below floor` | 1045 | ✅ |
| 21 | Regime changepoint probability ≤ 0.45 | `regime changepoint probability above veto threshold` | 1064 | ✅ |
| 22 | Walk-forward decay (DEAD / DECAYING) | `walk-forward decay DEAD: alpha is harmful, all entries blocked` | 1074 | 🔴 **DEAD** — `market["walk_forward_status"]` never set |
| 23 | Walk-forward efficiency floor (≥2 folds) | `walk-forward efficiency REJECT: …` | 1096-1103 | 🔴 **DEAD** — key never set **and** `min_walk_forward_efficiency`=0.0 never overridden |
| 24 | Overfit gate | `overfit gate REJECT: risk=…` | 1115 | 🔴 **DEAD** — `market["overfit_risk_level"]` never set |
| 25 | Data-drift gate | `data-drift gate REJECT: … (PSI)` | 1128 | 🔴 **DEAD** — `block_on_data_drift`=False **and** key never set |
| 26 | Selective activation gate | `selective_activation_gate: …` | 1143-1151 | ⚠ `choppiness_index` in neither `market` nor `features` → always 0.0 |
| 27 | Live health (critical alerts / health score) | `live health critical: …` | 1214-1216 | ⚠ **`core.live_health_monitor` does not exist** — guarded import at `trading_safety.py:29` silently yields `None`; 7 of 9 `market` keys never supplied |

**`rule_trace`** — ✅ VERIFIED. Built `:1153-1188` (28 keys) + `live_health_score` `:1207-1212`, returned `:1220`, persisted to SQLite at `:349,356,363`. *"A rejection is therefore explainable after the fact, not merely a boolean."*

**`selective_activation_gate` (impl `:830`)** works via `features["regime"]` / `features["adx"]` (`llm_orch:2007,1994`).

## 4.2 Gate 8 — the 19-factor reduction-only sizing ladder

**Formula (verbatim):**
```
volume ← f( equity × risk_per_trade , stop_distance , tick_value )

hard rejects first (return 0.0):
  permanent_halt | equity_floor | daily_drawdown | consecutive_loss_breaker | max_drawdown
  portfolio heat:  heat_now + 1R  >  max_portfolio_heat_r   → 0        # default 4.0R, :73
  entry == sl                                                 → 0

multiplicative ladder (reduction only — can never inflate):
  size_mult = f(confidence)   <0.60 → 0 ; <0.75 → 0.5 ; else 1.0      :928-929
  size_mult *= circuit_multiplier   weekly circuit step-down             :933
  size_mult *= dd_multiplier       drawdown ladder (0 at ≥20% DD)       :913-916
  size_mult *= streak_multiplier   anti-martingale loss streak           :917-923
  size_mult *= kelly_multiplier    quarter-Kelly, reduction-only         :924
  size_mult *= spmc_multiplier     SPMC regime: 1.0 good / 0.25 bad      :936-941
  size_mult *= opp_multiplier      opportunity-cost hurdle; 0 → reject  :944-947

retail-quant overlay (core/retail_quant_models.py):
  CUSUM breach (k=0.5, h=4.0, ≥20 R-samples)    → ×0.5                 :961-966
  Bayesian win-rate shrinkage (last 50)          → × p_shrink ≤ 1        :968-978
  Correlation-breakdown heat (window 60, k=2.5) → × mult ≤ 1             :981-989

volatility & model overlays (ALL require market=):
  ATR targeting:  × clamp(atr_target / atr, 0.5, 2.0)                    :997-1003
  Alpha sizing:   × compute_alpha_sizing(composite, direction, IC, conf)  :1008-1025
  Alpha risk:     × alpha_risk_profile.sizing_multiplier                 :1029-1053
  Kronos:         × kronos_risk_adjustment_factor (0–1)                  :1055-1064

research health gates (ALL require market=):
  walk_forward_status = DEAD → 0 ; DECAYING → ×0.5                      :1066-1077
  WFE (≥2 folds): mean_test_sharpe ≤ 0 → 0 ; WFE < 0.5 → × max(0.5,WFE) :1079-1101
  overfit_risk_level = CRITICAL → 0 ; HIGH → ×0.25                      :1103-1115
  data_drift_status  = DRIFT → ×0.5                                     :1117-1121
  CVaR95 > 2R (≥15 samples) → × clamp(2 / CVaR_r, 0.20, 1.0)             :1125-1138
  meta_health_multiplier                                                        :1140-1159

  risk_amt = equity × risk_per_trade × size_multiplier                   :1165
```

> *"Every model-derived input (Kronos, alpha composite, walk-forward state, overfit level, drift) can only shrink a position. A hallucinated or over-fit model cannot inflate size: the LLM's own `confidence` field is capped at 1.0, and any zero is a hard reject."*

### 🔴 ROOT-CAUSE DEFECT — one missing argument kills 9 factors

The single production call site is `llm_orchestrator.py:7357-7364`:
```python
vol = risk.calc_position_size(symbol, entry, sl, confidence=..., alpha_composite=..., ...)
# NO market=, NO atr=, NO atr_target=, NO meta_health_multiplier=
```
`core/risk_manager.py:1034` reads `market.get("alpha_risk_profile") if market else None` ⇒ always `None` ⇒ early return `:1035`.

**Dead in production (9 of 19):** ATR vol-target · Kronos · walk-forward · WFE · overfit · data-drift · CVaR95 · meta-health · alpha-risk profile.
The `alpha_risk` wiring at `core/risk_manager.py:1030-1053` is **real code that never executes**.

`intelligence/win_probability_model.py` exists and `_sizing_confidence` (`llm_orchestrator.py:3935-3936`) correctly caps `min(blended, base)` ✅ — but the blended confidence only matters if a caller passes it.

## 4.3 Gate 9 — the PortfolioRiskArbiter

`core/trading_safety.py:1223-1305`. Constructor limits: `max_drawdown_fraction=0.10` `:1229`, `max_open_risk_fraction=0.03` `:1230`, `max_correlated_risk_fraction=0.02` `:1231` (clamped `:1238`), `max_net_usd_beta_fraction=0.025` `:1232`, `max_single_currency_fraction=0.03` `:1233`.

`permit()` check order (verbatim, 6 steps): 1 max drawdown → kill switch `:1277`; 2 daily loss 3 % `:1281`; 3 weekly loss 6 % `:1282`; 4 portfolio open-risk 3 % `:1283`; 5 correlated bucket 2 % `:1301,1304`; 6 rolling correlated exposure `:1302`. Netting/beta block `:1287-1297`. Hedged path allows 1.5× open-risk `:1304`.

`exposure_buckets(symbol, side)` — side-specific `:1317-1325`:
`("EURUSD","BUY") → {"EUR_LONG","USD_SHORT"}` · `("EURUSD","SELL") → {"EUR_SHORT","USD_LONG"}` · `("XAUUSD","BUY") → {"GOLD_LONG","USD_SHORT"}` · `("GBPJPY","SELL") → {"GBP_SHORT","JPY_LONG"}`

> **🔴 DEFECT:** `permit()` is called at `llm_orchestrator.py:7326-7329` **without `symbol=` or `side=`**, so the netting block `:1287-1297` is skipped entirely — **net-USD-beta and single-currency limits are never evaluated in the live cycle.** `bucket=` receives a single sorted bucket, not the full side-specific set.

`safe_risk_fraction(percent_or_fraction, cap=0.01)` — `1.0 → 0.01`; `2.0 → 0.01`; `0.005 → 0.005`; `50 → 0.01`.

## 4.4 Sizing model as a portfolio answer (retail quant master architecture 03)

`Volume = (Equity × RiskPct) / (|Entry − SL| × TickValue)` with **1 % risk/trade** and heat cap **6.0R** (LangGraph path) / **4.0R** (main loop, `risk_manager.py:73`). **The two paths disagree.**

---

# PART 5 — THE AI DECISION PATH

## 5.1 The 10 payload blocks the model receives

`get_decision(toon)` def `llm_orchestrator.py:1150`; `toon_lite` built `:1193-1328`; Live Advisory Hub `:1329-1367`; budget `:1369`; prompt `:1370`.

| Block | Producer | Line | Verified |
|---|---|---:|---|
| Market snapshot (OHLC, tick, spread, indicators, DOM, MTF, account) | `data/market_data_provider.py` + `core/technical_indicators.py` | 1194-1300 | ✅ |
| `feature_snapshot` (values + completed bar time) | `intelligence/feature_builder.py` / `feature_schema.py` | 1236 | ✅ |
| **`smc_structure`** (CHOCH/BOS, order blocks, liquidity pools, premium/discount) | `core/smc_structure.py` | — | ❌ **NEVER SENT** — computed `:6102-6110`, consumed by the TwinAlgo validator gate `:2292-2323`, but **absent from `toon_lite`** |
| Regime + intermarket state | `regime_classifier.py`, `intermarket_regime.py` | 1202-1203, 1252-1262, 1313 | ✅ |
| Macro / causal advisory (dominant driver, path id, strength, decay) | `intelligence/macro_graph.py`, `_run_causal_advisory:6442` | 1247-1299 | ✅ |
| News + calendar | `data/realtime_news_ingest.py`, `mt5_calendar_enhanced.py` | 1240-1246 | ✅ |
| Knowledge-base context + lessons learned | `_get_knowledge_base_context:1047`, `_format_lessons_learned:1035` | 1234, 1304, 1169-1172 | ✅ |
| Session + symbol keywords | `_get_market_session:1060` | 1327 | ⚠ **PARTIAL** — `_get_symbol_keywords:1015` exists but is never referenced inside `get_decision` |
| Alpha-mining enrichment | `_apply_alpha_mining_enrichment:4408` | 1232, 1199-1211 | ✅ |
| RL / Kronos advisory | `_kronos_advisory_snapshot`, `_build_rl_state` | 1225-1229, 1350-1364 | ⚠ **PARTIAL** — `toon["kronos_advisory"]` (`:6173`) and `toon["rl_bridged_params"]` (`:6763`) are **never injected into `toon_lite`** |

**Budget guards:** `_round_floats` (`:1077-1088`, precision 5, NaN/Inf→0.0) and `_sanitize_toon_lite_budget(cls, toon_lite, max_total_chars=60000)` (`:1090-1148`, prunes 18 keys) — *"so a token-budget failure cannot become a mid-cycle exception."*

## 5.2 What the model must return

| Key | Meaning / downstream use | Verified |
|---|---|---|
| `action` (10 uses) | **decisive** — `BUY`/`SELL`/`HOLD`. Anything else skips the permission path and is recorded as HOLD | ✅ `:7017` |
| `confidence` (10) | 0–1; **<0.6 → size 0, <0.75 → ×0.5, else ×1.0** | ✅ `:6974,7157,7261` |
| `entry`, `stop_loss`, `take_profit` | plan construction, validated vs symbol stops/freeze + R:R | ✅ `:7022,7249-7250` |
| `reasoning` (3) | operator text — always stored | ✅ `:6977,7024,7059` |
| `waiting_for` | display only | ✅ `:7108,7163` |
| `conditional_scenarios` (2) | **advisory only** — logged, never a close authority (`core/trade_manager.py:190`) | ✅ `:7111`, recorder `:3616` |
| `position_size` | LLM-proposed size hint | ✅ `:7278,7301` |

**Provider-fabric failure:** `data/llm_router.py:609-631` `deterministic_hold_fallback(agent, errors)` records a HOLD **with the provider errors** rather than guessing. Combined with `LLM_TRADER_ALLOW_PROVIDER_FAILOVER=false`: *"a provider outage degrades the system to 'no new trades', never to 'uncontrolled trades'."* ✅

## 5.3 From decision to `TradeProposal`

`_validate_proposal` (`:1963`) turns decision + market context into the immutable `TradeProposal` (`core/trading_safety.py:40`) and immediately calls `validator.validate(proposal, market)`. `reward_risk` = `abs(tp-entry)/abs(entry-sl)`; `setup_key()` = SHA-256 of strategy+setup+symbol+side+entry+regime.

**Magic numbers:** `SWING_MAGIC = 460001` (`:34`), `SCALP_MAGIC = 460002` (`:35`), `NEOCORTEX_MAGIC = 460003` (`:36`). Resolved by `TradeProposal.magic` (`:53-56`). `core/trade_manager.py:76` `managed_magics = {460001, 460002}`.

> **⚠ PRD defect:** the PRD documents **5 lifecycle states**. The code has **10** (`core/trading_safety.py:382-390`): `CANDIDATE, VALIDATED, ACCEPTED, FILLED, PARTIAL, MANAGED, REJECTED, MISSED, INVALIDATED, UNRESOLVED, CLOSED` with `terminal = {"REJECTED","MISSED","INVALIDATED","UNRESOLVED","CLOSED"}`. The cycle additionally emits `PROPOSED` `:343`, `SUBMITTED` `:7444`, `PENDING` `:7590`, `CANCELLED/EXPIRED` `:4812-4813`, `ERROR` `:7618`.

---

# PART 6 — EXECUTION, BROKER ROUTING & TRADE MANAGEMENT

## 6.1 Two venues, one contract

| Path | Module | Contract | Verified |
|---|---|---|---|
| Paper (simulation) | `PaperBroker` `core/order_execution.py:40-158` | In-memory orders/positions, **monotonic tickets from 100000** (`:52,55-58`). Refuses to invent positions: non-finite/non-positive quote or volume → **`retcode 10013`** (`:63-65,78-80,119-124`). Closes journaled through a callback into SQLite so research gates and reconciliation consume paper trades exactly like live ones | ✅ **all 6 claims verified** |
| Live/demo MT5 | `place_pending_order`, `place_urgency_routed_order`, `modify_order_sltp`, `cancel_pending_order`, `emergency_market_exit` | Validated pending plans and emergency exits only | ⚠ blocked — §6.3 |

**Paper-broker safety rules (all fail-closed, all verified):**
- `pending_order` accepts only `buy_limit, sell_limit, buy_stop, sell_stop`; anything else → `10013 invalid paper pending order type` (`:76-77`)
- Expired pending rejected: `paper pending order is already expired` (`:81-82`)
- `close_position` requires a matching non-zero `magic` else `paper position identity/magic ownership mismatch` (`:121-122`); refuses to close more than the position holds
- History trimmed at 2000 keeping newest 1000 (`:146-147`) — ⚠ **close-path only**; opens are never appended to `history`

## 6.2 Direct market entries are disabled by design

`core/order_execution.py:290-294`:
```
RuntimeError("direct market entries are disabled; use a validated pending plan
               or emergency_market_exit for an owned position")
```
> *"The only market-order path that reaches a live broker is the emergency exit of a position the system already owns."*

> **🔴 CONSEQUENCE:** because the guard returns for `paper is not None`, lines `:304-395` are **unreachable** — and that block contains **the only live `order_send` with a distributed lock and a non-DONE retcode raise.** The distributed lock `order:<symbol>` therefore exists only in dead code.

## 6.3 🔴 BLOCKING BUG — live execution is non-functional

`core/order_execution.py:704`:
```python
sup_check = sup.validate_pre_trade_safety(symbol) if hasattr(sup, "validate_pre_trade_safety") else {"allowed": True}
```
Two independent defects:
1. **Wrong arity** — the real signature is `validate_pre_trade_safety(self, symbol, requested_volume, current_spread_pips, historical_avg_spread_pips, roundtrip_latency_ms)` (`core/execution_safety_supervisor.py:114-121`). Calling with 1 arg raises `TypeError`, caught at `:709` and re-raised as `RuntimeError(f"LiveExecutionSafetySupervisor failed closed: {exc}")` at `:711`.
2. **Wrong result key** — reads `.get("allowed")` but the real key is `is_approved` (`:125,148`).

The block sits **after** the paper early-return at `:686-688`, so **every live pending order aborts.** Not documented anywhere in `docs/`.

Supervisor thresholds: `max_acceptable_latency_ms=250.0` `:28`, `max_slippage_multiplier=2.5` `:29`, `HEARTBEAT` docstring `:7`, `trip_circuit_breaker:153-158`, `get_execution_safety_supervisor:198-203`.

## 6.4 The live submit path, step by step

| Step | Verified | Note |
|---|:--:|---|
| `validate_order_params(side, price, sl, tp, atr)` `:192-221` | ✅ | called `:307,483,685` |
| `validate_pending_entry_geometry(otype, entry, bid, ask)` `:224-244` | ✅ | reorders a limit that would already be marketable |
| Distributed lock `order:<symbol>` → `{"error":"lock_timeout"}` `:313-315` | 🔴 **DEAD** | only in the unreachable `place_market_order` block. `place_pending_order` `:640-826` and `place_urgency_routed_order` `:398-560` have **no lock** — *"This is what makes the parallel strategy pool safe"* is no longer true |
| `calculate_dynamic_slippage_deviation` `:247-284` | ⚠ | spread branch ✅ (≥0.70→1.5× spread, ≤0.30→0.8×, clamp [5,150]). **Fallback branch uses 0.7× not 0.8×** (`:281`). `place_pending_order` **never calls it** — hardcodes `dev = 30 if point >= 0.01 else 20` `:756`. `place_market_order:322` hardcodes `urgency_score=0.5` |
| `_broker_market_filling_mode` `:174-189` | ✅ | ⚠ `place_pending_order:770` uses an `ORDER_FILLING_RETURN` default and does **not** consult the broker's `filling_mode` |
| κ audit (`ORDER_FILLED`/`ORDER_REJECTED` + retcode, ticket, SL/TP) | ✅ | `:800-815` |
| Non-DONE retcode raises `RuntimeError("Market order failed: retcode=…")` | ✅ | `:825-826`; `RETRYABLE = {10004,10006,10012,10015,10018}` `:775`; `ACCEPTED = {DONE,PLACED,DONE_PARTIAL}` `:776`. *"There is no retry-on-reject loop that could double-fill."* |
| Post-execution advisory — `UrgencySmartOrderRouter` returns `execution_routing` for telemetry (**not** a second order) | ✅ | `:367-386` |

## 6.5 Position management — the ladder

`TradeManager.monitor_positions` (`core/trade_manager.py:163`) runs every pulse for live/demo **and paper**. `_get_positions` `:122-145` returns `PaperPosition` from `paper_broker.positions` at `:124-132`; early return only if **both** client and paper broker absent `:168-169`. Called `llm_orchestrator.py:7654`. **Doc L-7 "paper-mode has no position management" is STALE — it is fixed.**

Order: (1) protective flatten → (2) own positions (foreign magic → `manual_activity`) → (3) `_manage_position` → (4) conditions → (5) synthetic triggers → (6) scale-out / time-stop / pyramid.

| Mechanism | Trigger (real constants) | Action | Line |
|---|---|---|---|
| Protective flatten | `risk.pending_flatten_reason()` set by daily-drawdown check | Closes **all** owned positions after `_authorize_management_action("FULL_CLOSE", …)` | 217-267 |
| Breakeven | profit > `breakeven_r_mult`(1.0) × initial R | SL → entry ∓ **ATR×0.1** buffer, only ever moving protectively | 594-608 |
| ATR trailing | current ± `trailing_stop_r_mult`(1.5) × initial R, beyond open price | Monotonic tightening, **never loosening** | 613-624 |
| Scale-out stage 1 | R ≥ `scale_out_r_mult`(2.0) **and** profit > 2 × ATR | Close **50 %** | 443-445 |
| Scale-out stage 2 | R ≥ `scale_out_two_r_mult`(3.0) | Close **half of the remainder** | 448-450 |
| Time stop | elapsed > `time_stop_seconds`(4 h) and R < **0.20** | Exit early, `FULL_CLOSE` + `extra={"time_stop_exit":True}` | 460-479 |
| Pyramiding | profit > **1.5R** and **ADX > 25** and profit > 1.5 × ATR | Add volume, normalized to broker limits | 643-656 |
| Programmatic conditions | Operator-registered price conditions (`trade_conditions.json`) | Evaluate + record; LLM `conditional_scenarios` observation-only | 192, 1097-1168 |

**The R basis is captured once per ticket** by `_initial_risk_for` (`:402-418`, caches in `self.initial_risk[ticket]` `:415`) from the *initial* stop, before any breakeven/trailing move mutates it — *"otherwise trailing would shrink the R reference each tick."* Falls back to ATR if the initial stop is missing. Used at `:433` (scale-out) and `:589` (BE/trail).

**Ownership:** `_owns()` `:666-667` checks `magic in self.managed_magics`. Foreign positions recorded as `UNOWNED_POSITION_OBSERVED` with `action: "observe_only"` (`:180-188`) — *"the system never touches a position it cannot prove it owns."* Refusal records: `REFUSED_PARTIAL_CLOSE` `:490,898`, `REFUSED_PYRAMID` `:744`, `REFUSED_SL_UPDATE` `:879,989`, `REFUSED_FULL_CLOSE` `:965`.

## 6.6 Post-fill & close reconciliation

After any fill/close the orchestrator records broker execution reports (fill price, slippage, spread at submit and fill, implementation shortfall, requote count, execution latency — columns added by migration `core/trading_safety.py:284-290`), position observations with `mark_quality`, and lifecycle events. `reconcile_pending_orders` (`:4766`), `reconcile_open_positions` (`:4538`), `reconcile_daily_broker_history` (`:4734`) compare broker truth against the journal in every maintenance prefix — *"so a restart or a dropped connection cannot leave the system believing in a position the broker no longer holds."*

## 6.7 Autonomy levels, live gating, broker contract

| Level | Risk/trade | Requirements |
|---|---|---|
| L0 shadow | — | observation only |
| L1 paper | — | `PaperBroker`; market-data session ensured at boot (`_ensure_paper_market_data_session:987`) |
| L2 micro-live | **0.25 %** | signature-gated; auto-demote / HALT on circuit |
| L3 full live | **1 %** | operator-signed rollout manifest id |

`LIVE` additionally requires `LLM_TRADER_ALLOW_LIVE=true` (`:959-961`) **AND** (`--yes` or `confirm_live()` `:956`) **AND** a mounted harness sandbox reaching `PermissionLevel.ORDER_WRITE` (`:3905-3922`, fail-closed on gate exception) — ⚠ but only when `harness_ctx is not None and has("sandbox")`.

**Broker contract (gate 12):** `core/broker_preflight.py` captures symbol and account specs (`SYMBOL_FIELDS` ~16, `ACCOUNT_FIELDS` ~24), fingerprints them (`fingerprint:59-61`), `validate_envelope:89-177`. **⚠ `has_contract_changed:64-72` is DEFINED BUT NEVER CALLED** — `persist_preflight:180-212` does its own inline fingerprint diff at `:208`. Called only from CLI (`mt5_trader.py:1075`), `operations/readiness_report.py:243`, `operations/system_doctor.py:50-54` — **not from the cycle path**. `core/execution_policy.py` `load():83-106` **is** called from the cycle (`:2480,2860`) and refuses on fingerprint change `:100-101`; `approve_for_store:162` is CLI-only (`:2252`).

**Current live-readiness verdict** (`python mt5_trader.py journal readiness-status`, run 2026-09-26):
```
"status": "BLOCKED",  execution_authority: false
blockers: 1) tiny-live rollout approval absent/expired/evidence-invalid
          2) credential/trace security review PENDING_BACKGROUND
          3) learned/adaptive research influence not approved
sub-gates: authorization.status = AUTHORIZED (READY_SHAPE)
           broker_preflight.status = READY (fingerprint f7ce4a80…, XAUUSD, execution_authority: false)
```

---

# PART 7 — DATA FABRIC

## 7.1 Doctrine — no synthetic fallback, no unattributed data

> *"Market data is either authenticated broker data, or the cycle does not happen. There is no 'synthetic candle' path anywhere in `data/market_data_provider.py`."*

| Condition | Code response | Line |
|---|---|---|
| MetaTrader5 package missing | `RuntimeError("MetaTrader5 package is unavailable; refusing external or synthetic market-data fallback")` | `market_data_provider.py:185-188` |
| Symbol not on broker | `RuntimeError` listing similar symbols + BTC/ETH alternatives + AUTO-mode tip | `:194-213` |
| Zero bars returned | `RuntimeError("MT5 returned no OHLC bars" … "refusing external or synthetic fallback")` | `:227-232` |
| Paper mode, no attached terminal | `RuntimeError("paper market data is unavailable; refusing synthetic candles/DOM/news; provide an explicit broker-replay or test fixture")` | `:1117-1132` |
| Enhanced calendar DLL unavailable | `self.calendar = None` — *"no calendar fallback is trusted"* | `:737-739` |
| Exness live feed fails | Returns `[]` — *"No fallback insights — empty is honest"* | `exness_provider.py:137-151` |

> **⚠ Stale-fixture hazard (self-documented in the PRD):** `forexfactory_provider.py:41-86` ships hard-coded `FALLBACK_EVENTS` dated **2026-08-15…18**; `exness_provider.py:40-74` ships `FALLBACK_INSIGHTS` dated **2026-08-14**. The Exness list is dead code; **the Forex Factory list IS live-reachable when both network and cache fail.** Consumers must treat a past-dated Forex Factory row as a stale fixture, not a live release. **As of 2026-09-26 both are ~6 weeks stale.**

## 7.2 Provider roster (all 21 modules exist and are wired)

MT5 terminal · NewsAPI · FRED · SEC EDGAR · Yahoo Finance · OpenBB · Seeking Alpha · Portfolio123 · EIA · CFTC COT · Exness · Forex Factory · RSS (Reuters/Fed) · MT5 economic calendar · zero-lag macro proxies · alternative data (satellite / freight / supply-chain).

Per-provider circuit breaker: `data/provider_circuit.py:110` (CLI `provider-circuit status|reset`). Rate limiting + blocking cap in `data/llm_router.py` `RateLimiter` (RPM / RPD / concurrent / tokens / cost). Provider classes: `OpenRouterProvider`, `CerebrasProvider`, `NVIDIAProvider` with per-provider RPM 60 / 30 / 60.

`data/data_quality.py` (288 ln) `DataQualityGate.validate:72` — ~30 distinct rejection reasons including live-source auth `:84-99`, clock skew `:113-126`, candle revision `:222`, future-data `:234`, DOM staleness `:279`.

## 7.3 Config resolution order (PRD §03.3 vs **[CODE]**)

1. **`AccountManager` active account** (`accounts.json`) — default credentials source
2. **`config.ini`** — `[risk] risk_per_trade, max_drawdown`; `[limits] cooldown_seconds, max_trades_per_hour`; `[general] log_level`; `[trading] default_volume`; `[account] login/server/password_env`
3. **Env overrides credentials**: `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` win (`:655-658`)
4. **Hard defaults**: `risk_per_trade=0.01`, `max_drawdown=0.10`, `default_volume=0.1`, `cooldown=300 s`, `max_trades_per_hour=5`, `log_level=INFO`

**⚠ Gap:** there is **no env override for `risk` / `cooldown` / `max_trades_per_hour` / `log_level` / `default_volume`** — config.ini is the only source for those.

**Other env vars:** provider fabric/budgets (`OPENROUTER_*`, `TOKENROUTER_*`, `ROUTESME_*`, `CEREBRAS_*`, `NVIDIA_*`); per-agent routing (`TRADER_LLM_PROVIDER|MODEL|FALLBACK`, `ANALYST_*`, `RISK_*`); `LLM_TRADER_ALLOW_PROVIDER_FAILOVER=false`; `LLM_ROUTER_TIMEOUT=30`; `LLM_ROUTER_MAX_RATE_WAIT=15`; `LLM_TRADER_ALLOW_LIVE=false`; `MT5_COMMISSION_PER_VOLUME`, `MT5_EXPECTED_SLIPPAGE_PRICE`, `MT5_EXPECTED_SWAP_MONEY` (unset ⇒ **NO_TRADE** in the net-R:R gate); `PIPELINE_V2`; `LOG_LEVEL`; `MT5_INIT_TIMEOUT_MS=5000`; `REMOTE_MIRROR_*`, `LOCAL_MEMORY_*` (never grant execution authority).

## 7.4 CLI surface — 40 actual subcommands **[CODE]**

`data`, `market-order`*, `pending-order`, `modify-order`, `cancel-order`, `account-info`, `broker-preflight`, `diagnose`, `positions`, `orders`, `history`, `benchmark`, `news-gate-backtest`, `backtest`, `web-research`, `llm-trade`, `demo-run`, `accounts`, `deck`, `settings`, `kill-switch`, `provider-circuit`, `journal`, `memory-core`, `memory-review`, `test-live`, `benchmark-llm`, `doctor`, `monitor`, `nexus`, `profiles`, `demo-samples`, `scrub-history`, `macro`, `research-sim`, `meta-audit`, `harness`, `workflow`, `export-correlation`, `pipeline-status`

`*market-order` prints *"Disabled direct market entry; use pending-order or emergency exit"* — a deliberate doctrine change the PRD §03.1 does not reflect.

**`llm-trade` flags (verbatim):** `--symbol --timeframe --risk-per-trade --paper|--demo|--live --yes --loops --continuous --strategies --model --tui --api-sync --poll-interval --show-metrics --show-rl-metrics --no-dashboard --no-pipeline-v2 --bounded-autonomy --validate-every --debug --llm-providers`

**`journal` sub-subcommands (~40):** `integrity, metrics, dashboard-status, operations, memory-status, research-status, policy-status, auth-status, migrate, security-status, supervised-status, broker-preflight-status, policy-candidate, policy-approve, sample-gate-status, readiness-status, restart-status, dataset-status, baseline-status, validation-runs, forward-test-status, stress-status, promotion-package, promotion-verify, model-family-status, bandit-status, weekly-memory, monthly-memory, memory-maintenance, audit-export, audit-retention-status, scope-init, scope-status, alerts, alerts-enqueue, alerts-status, alerts-claim, alerts-ack, alerts-recover, alerts-deliver, backpressure`

**⚠ `requirements.txt` is 354 B (~15 packages) vs ≥20 undeclared runtime imports:** `hmmlearn, p123api, openbb, fredapi, newsapi, yfinance, webview, customtkinter, pdfplumber, faster_whisper, yt_dlp, sentence_transformers, huggingface_hub, langchain_core, peft, pymysql, psutil, pytz, colorama, shap, fastmcp, safetensors`.

## 7.5 Root entrypoints

| file | bytes | target |
|---|---:|---|
| `START_TRADING.bat` | 1,067 | `python -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe AUTO --model mistralai/mistral-7b-instruct-v0.2 --risk-per-trade 1.0 --continuous` |
| `START_DECK.bat` | 5,259 | `python -u ui\gui_api.py` (:8000) → POST `/api/control/start` → `bun src/index.tsx` |
| `start_gui.bat` | 77 | `python ui/main_window.py` (pywebview; needs `webview` — **not in requirements**) |
| `launch_observatory.bat` | 408 | `bun run demo:observatory` in `tui_opentui/` — **hardcodes `C:\Users\User\.bun\bin\bun.exe`** |
| `build.bat` | 512 | `python scripts/build.py` → `ponytail_audit` → `rtk bun run typecheck` + `rtk bun run selftest` → `rtk graphify update .` |
| `ops/watchdog/install_nssm.bat` | — | NSSM Windows service; restart 15 s, log rotation, delayed auto-start. *"Install manually when ready"* |

`launch_trading.py` — **ABSENT** (referenced as a fallback in `LAUNCH_INSTRUCTIONS.md`).

---

# PART 8 — MEMORY, KNOWLEDGE GRAPH & STATE TENSOR

## 8.1 The six memory layers (`TRADING_MEMORY_SPEC.md` §1)

| Layer | Lifetime | Contents | Allowed consumer |
|---|---|---|---|
| Tick/candle cache | minutes–days | Raw OHLCV, ticks, spread, DOM | Feature builder |
| Cycle snapshot | one proposal | All features + data-quality flags | LLM and validator |
| Trade journal | permanent | Proposal, execution, management, close, costs | Audit and research |
| Event memory | permanent | Macro event, surprise, reaction windows | Regime/event analysis |
| Situation memory | permanent | Similar historical setup summaries | LLM context |
| Core playbook | versioned | Stable, repeatedly validated rules | **Proposal context only** |

**Invariant (verbatim):** *"Semantic memory must never be the only copy of an order, P&L, risk, or broker event. Every memory item must include a source journal ID and timestamp."*

## 8.2 L0–L3

- **L0 — Raw execution trace** (immutable, append-only): operator instructions; LLM prompt+response; tool calls + returned market data; MT5 requests/responses/retcodes/broker errors; validator decisions + rejection reasons; order-management actions + overrides; macro-news payloads + retrieval timestamps. **L0 is not injected wholesale into prompts.**
- **L1 — Atomic memory.** Fields: `fact_id, type, content, confidence, valid_from, valid_to, source_ids, symbol/currency, strategy, regime, created_at, supersedes`. Must be marked **observed | inferred | proposed**; *"Only observed facts may be used as evidence."*
- **L2 — Scenario memory.** One doc per symbol/setup/regime/session at `scenarios/<SYMBOL>/<name>.md`. Must include situation definition + eligibility; representative winning/losing/rejected/unresolved examples; R-multiple + cost distribution; indicator ranges / vol / spread / event context; invalidation; last validation date + sample size; links to L1/L0; version history + contradictory evidence. *"must not be treated as proof of future profitability."*
- **L3 — Core memory / personality** (reviewed, versioned only). Four sub-personalities: **Trading** (conservative, evidence-first, uncertainty-aware, no chasing/revenge), **Risk** (capital preservation primary; deterministic limits outrank model confidence; hard caps per-trade/portfolio/daily/weekly/drawdown; **no Kelly/HMM/GARCH/bandit/RL before promotion gates**; no size increase to recover loss), **Research** (records rejected signals, seeks disconfirming examples, compares baselines, retires contradicted memories), **Communication** (thesis/invalidation/evidence/uncertainty, always data age + missing flags, never present memory as live fact without timestamp). ✅ `core_memories` table + `supersedes` FK exist at `intelligence/trading_memory.py:93,99`; `memory-core` CLI ships.

## 8.3 Short-term working memory

current objective + cycle ID · latest completed candle IDs · current market snapshot + data age · active playbook + event windows · proposals in the cooldown window · open positions + pending orders owned by this agent · current exposure + kill-switch status · last few tool results + unresolved errors. *"Never silently drop a risk decision or unresolved error."*

## 8.4 Task graph / execution canvas

```
session_start → fetch_market_data → fetch_calendar → build_features
  → retrieve_similar_situations → propose_setup → deterministic_validation
  → portfolio_permission → execution → position_management
  → broker_reconciliation → label_trade → write_situation_memory
```
Each node records status, start/end time, inputs, outputs, retry count, error.

## 8.5 Persona / preference / policy separation — different authority

**Persona** (behaviour) · **Preferences** (user-selected) · **Policies** (hard safety/compliance) · **Objectives** (current work) · **Permissions** (mode, allowed symbols, allowed magic numbers). *"Policies and permissions must be stored in signed/versioned configuration and checked by code. They cannot be changed by an LLM-generated memory."*

**Five reusable memory assets:** Chat Memory (store only cleaned originals; never re-save recalled memory inside a new message) · Skill Memory (versioned: `calculate_position_size_v3`, `validate_london_sweep_v2`, `reconcile_mt5_history_v1`, `label_closed_trade_v1` — each needs inputs, deterministic outputs, tests, owner, version, promotion status) · Trading Wiki (broker specs, event definitions, strategy docs, indicator lookbacks, known failure modes — versioned + cited) · Code Graph (*"must never be used as a substitute for runtime tests"*) · Team/agent loadout & access control (separate scopes for swing / scalp / research / monitoring / operator agents; **execution agents read-only on core risk policies**; only the review process promotes scenarios; private account data not shared between accounts).

## 8.6 Deterministic label enum (10)

`WIN_TARGET`, `WIN_VALIDATED` (net P&L > 0 **and** ≥ +1R), `WIN_SMALL` (positive < +1R), `LOSS_STOP`, `LOSS_TARGET`, `LOSS_OTHER`, `BREAKEVEN`, `CANCELLED`, `REJECTED_VALIDATOR`, `UNRESOLVED` (+ optional research label `MISSED_SIGNAL` — *"never a realized loss"*).

**Minimum gate (verbatim):** *"100 correctly labelled closed trades per setup class and regime coverage before estimating Kelly or allowing learned influence."* Runtime enforcement: `operations/research_influence_gate.py` — demo/live influence requires qualifying purged/OOS evidence, matching evidence digest, expiring operator approval; paper mode = research-only.

**Situation retrieval — 5 ordered filters:** same symbol/currency driver → same strategy+timeframe → same session+regime → similar vol/spread/event state → vector/keyword similarity. *"Return a small balanced sample: winning, losing, rejected, and unresolved examples. Returning only winners creates confirmation bias."*

**Metric qualification rule (verbatim):** *"Every metric must carry its sample size, time range, cost model, strategy version, and whether it is in-sample, validation, out-of-sample, demo, or live. Never display a Sharpe ratio or win rate without those qualifiers."*

**Retention/eviction:** journal = permanent/authoritative; *"Delete or supersede stale memories; preserve the original evidence"*; reject records missing timestamp/symbol/side/source ID; hash raw payloads so later edits are detectable; keep UTC + original broker timestamp; separate paper/demo/live/backtest; redact credentials before cloud upload; *"Never let retrieved memory directly issue an order."*

## 8.7 The 10-step learning loop (verbatim order)

1. Validate journal integrity & reconcile broker history. 2. Deterministically label closed trades. 3. Build point-in-time feature snapshots; prohibit future fields. 4. Chronological split with walk-forward and purged/embargoed folds. 5. Compare vs random and simple EMA/trend baselines after costs. 6. Train supervised probability models first. 7. Contextual bandits only after adequate labelled samples. 8. **Reserve RL for position management, not unrestricted entry.** 9. Promoted model may adjust confidence or size only within hard caps. 10. Version every model; monitor live-vs-backtest drift.

## 8.8 🔴 KG DECAY — the sleeper risk

`intelligence/macro_graph.py:307` — `apply_trade_outcome_feedback` demotes edges to `CANDIDATE`. Grep for `revalidat` across `macro_graph.py` = **0 hits**. Sole caller `llm_orchestrator.py:3301`. **Nothing re-approves them.**

> Consequence: approved causal context shrinks monotonically and never recovers. This is audit item **M-12**, still open, and the highest-impact unfixed defect in the system because it silently degrades the macro advisory input to the LLM over months of operation. **Fix:** a scheduled re-validation sweep over `CANDIDATE` edges after demotion (audit §6 watch-list #1).

## 8.9 The macro causal KG schema (`macro_causal_kg_schema.md`)

**Node types:** economic indicator · central bank · policy regime · sovereign · sector · commodity · currency · financial instrument · geopolitical actor · sanction regime.

**Edge types defined:** `TRIGGERS`, `INTERVENES_IN`, `CASCADES_TO`, `SHOCKS`, `FUELS`, `ALTERS_BEHAVIOR` (with `direction`, `lag_days`, `strength`, `confidence`, `evidence_count`, `last_validated`).

> **⚠ Doc defect:** the ontology diagram uses relations `initiates / reacts / affected by / triggers cascade`; **`reacts` has no edge type**, yet the temporal-unrolling example uses `(reacts)`. The DAG diagram is also unrendered — a `chartPlaceholder(chartDescription=...)` + `results=[]` LLM artifact sits at §3.

**Economic dynamics catalog:** `intelligence/economic_dynamics_catalog.py` — **134 dynamics definitions**; `CAUSAL_EDGES`, `CORRELATION_REGIMES`, `EVENT_TEMPLATES`, `EVENT_CLASS`, `GEOPOLITICAL`, `FREE_DATA_BINDINGS`, `DECADE_SHOCK_TEMPLATES`; `validate_catalog()`, `catalog_summary()`; **version 1.0.0**. `ECONOMIC_DYNAMICS_SOURCE_OF_TRUTH.md`: *"The chat dump is not executable truth. The catalog is."* Edge lifecycle: `CANDIDATE → STAT_PASS → REPLAY_PASS` (`CausalEdgeBacktestOptimizer.apply_replay_floors`).

**Macro source policy** (`intelligence/macro_source_policy.py`): PRIMARY / RUNTIME_FEED; Twitter / Truth Social = `UNVERIFIED`. *"The LLM as a data source is lower trust than the broker feed."*

## 8.10 The state tensor — UAQP `Γ = (A, E, S, Φ, Θ, T)`

Core principle (verbatim): *"Every agent is stateless. The graph is stateful. An agent wakes, reads S, transforms a subset, writes back, and terminates."*

| Slice | Type | Populated by | Persistence |
|---|---|---|---|
| **S.H** Hypothesis Space | `Set[AlphaCandidate]` | α | `alpha_discovery_state.json`; lifecycle `PENDING→VALIDATED→REJECTED` |
| **S.D** Data Corpus | `Dict[str, DataFrame]` | δ | `trading_state.db:market_ticks` + API caches |
| **S.F** Signal Manifest | `Dict[str, float]` | σ | `ensemble_weights.json` |
| **S.B** Backtest Results | `Dict[str, BacktestMetrics]` | β | `walk_forward_state.json`, `backtest_report.json`, `artifacts/` |
| **S.R** Risk State | `RiskState` | ρ | `trading_state.db:state`; `risk_manager_state.json` |
| **S.P** Portfolio State | `Dict[str, Any]` | ε | `trading_state.db: trades, position_observations, trade_lifecycle_events` |
| **S.M** Market Regime | `RegimeState` | μ | in-memory, recomputed each cycle |
| **S.Ω** System Health | `Dict[str, Any]` | μ | `alpha_decay_state.json`, `auto_quant_tools_state.json` |
| **S.Q** Orchestrator Queue | `TaskQueue` | ω | in-memory, rebuilt from `DEFAULT_INTERVALS` |
| **S.Ξ** Trial Ledger | `(n_trials, trial_sharpe_distribution)` | β | **append-only, never resettable** |

**Regime labels (4):** `LOW_VOL_TREND`, `LOW_VOL_RANGE`, `HIGH_VOL_TREND`, `HIGH_VOL_CRISIS`.

**`AlphaCandidate` schema:** `expression, universe, expected_edge, holding_horizon, rationale, source_confidence (0-1), ic=0.0, ic_ir=0.0, category, status="PENDING"`.

**`toon` transport dict:** `symbol, timeframe, ohlc[], technical_analysis{rsi_14,macd_line,macd_signal,macd_hist,hma_21,adx_14,atr_14,ema_alignment}, bocpd{changepoints,hazard}, fractional_diff{stationary,adf_t_stat}, kalman{beta_t,spread_z,ou_halflife}, alpha_ensemble{composite_score,weights}, regime_allocation, copula_advisory, zero_lag_macro, meta_loop, quant_audit, alpha_mining_results` — "50+ fields".

**The four internal graphs:** Trade (authoritative) · Deliberative (shadow, LangGraph) · Reflection (post-trade) · plus the **Knowledge Graph Studio** visualizer that renders a 5th projection (12 domains, 40 screens, 7-stage lifecycle tour).

## 8.11 SQLite schemas (verbatim DDL)

```sql
CREATE TABLE IF NOT EXISTS market_ticks (symbol TEXT, timeframe TEXT, tick_json TEXT, received_at TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS trades (ticket INTEGER PRIMARY KEY, symbol TEXT, side TEXT, volume REAL,
  entry_price REAL, stop_loss REAL, take_profit REAL, magic INTEGER, strategy TEXT,
  opened_at TEXT, closed_at TEXT, exit_price REAL, pnl REAL, risk_amount REAL);
CREATE TABLE IF NOT EXISTS position_observations (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket INTEGER,
  unrealized_pnl REAL, current_price REAL, observed_at TEXT);
CREATE TABLE IF NOT EXISTS trade_lifecycle_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket INTEGER,
  event_type TEXT, event_json TEXT, recorded_at TEXT);
```
Also: `proposals, setup_instances, setup_labels, manual_activity, execution_reviews, broker_execution_reports, research_validation_runs, strategy_lifecycle, core_memories, memory_outbox, alerts_outbox, harness_events, correlation_returns`. Plus `compliance_audit.db:corpus` (hash-chained) and `meta_corpus.db:{corpus,patterns,proposals}`.

**Operational hardening verified:** every `_con()` sets `busy_timeout=10000` + `synchronous=NORMAL`; `TradingMemory` enables WAL (`intelligence/trading_memory.py:37-38,47`; `operations/agent_operations.py:97-98`; `intelligence/graph_checkpointer.py:49`). ✅

**Memory retrieval concurrency:** all five read-only layers run on a `ThreadPoolExecutor` (`trading_memory.py:1124`, `max_workers=len(jobs)`); a failing layer degrades to `[]` plus a `retrieval_errors` entry (`:1140`). ✅

**⚠ Retention not actually wired:** `prune_retrievals(keep_days=90, keep_linked=True)` (`trading_memory.py:1145`) and `prune_checkpoints(keep_per_thread=200)` (`graph_checkpointer.py:274`) both exist, but `monthly_memory_maintenance` (`:680`) references **neither**. The doc claim is false.

**⚠ Recency-window bias (M-10):** token-overlap retrievers scan only the newest **200–300 rows** (`trading_memory.py:465` `LIMIT 200`, `:730` and `:913` `LIMIT 300`). Older approved memories silently vanish once >N newer rows exist.

---

# PART 9 — AUTONOMY, OPERATIONS & GOVERNANCE

| Component | Responsibility | Verified |
|---|---|---|
| `operations/autonomy.py` (251 ln) | L0–L3, 5 circuits `:189-199`, control plane, `trading_allowed:180`, `evaluate_circuits:202`, signature-gated L3, auto-demote/HALT | ✅ wired `:5780`, `:4688` |
| `operations/supervisor.py` | FSM boot→run→shutdown, `RECOVER_FROM_*` | ✅ `:4034-4044` — ⚠ warn-only on exception |
| `operations/agent_operations.py` | Task leases, heartbeats, events, LLM budget | ✅ `claim_task(lease_seconds=900, max_attempts=3)` `:787-788`; retryable/exhausted `:928-929` |
| `operations/agent_contracts.py` | Least-privilege write scopes, 9 principals, `PrincipalSpec`, `require_scope`, `ContractViolation` | ✅ |
| `operations/agent_scheduler.py` (38 ln) | Schedules: `market_health` @30 s `:10`, `session_review`, `daily_review`, `self_improvement`, `weekly_research`, `candidate_sprint`, `causal_edge_validate`, `catalog_measurement_pass`, `alpha_mining_sprint` @604800 s `:41` | ✅ thin shim |
| `operations/dual_velocity.py` | Fast tick lane vs slow research lane | ✅ `DualVelocityExecutor(max_pending_fast=16, max_pending_slow=4)` `llm_orch:606`; slow lane used `:4723,5655,5803` |
| `operations/executors.py` | String-keyed handler registry + `__import__` resolution `:72` | ✅ 7 of 8 handlers registered — `learning_agent.handle_self_improvement` is an **orphan** |
| `operations/journal_audit.py` (872 ln) | **14-rule** journal integrity verification; CLI `integrity/readiness-status/export` | ✅ |
| `operations/journal_backup.py` | AES-256-GCM encrypted DB backups | ✅ |
| `operations/journal_scope.py` | DEMO/LIVE/PAPER scope isolation via `login|server` fingerprint | ✅ |
| `operations/daily_reconciliation.py` | Broker deals vs journal | ✅ |
| `operations/security_audit.py` (291 ln) | Credential scanning + secret sanitizer | ✅ (disk-cached, async-prefetched) |
| `operations/secrets_guard.py` | Scans `accounts.json`/`config.ini`; warn-only `secrets_hygiene_warnonly` | ✅ |
| `operations/alert_outbox.py` (217 ln) | Durable leased operator-alert handoff: `enqueue/claim/deliver/acknowledge/recover_expired` | ✅ |
| `operations/health_alerts.py` | Health-based alerts enriched with `docs/INCIDENT_RUNBOOKS.md` sections | ✅ |
| `operations/live_rollout_gate.py` | Operator-signed live manifests | ✅ |
| `operations/readiness_report.py` | Aggregates all gates → READY/BLOCKED | ✅ returns BLOCKED today |
| `operations/strategy_factory.py` / `strategy_lifecycle.py` / `strategy_evaluation.py` | Registry FSM, `PROMOTION`/`ROLLOUT_L3`/`CONFIG` round-trip | ✅ |
| `operations/restart_recovery.py`, `core/crash_recovery.py`, `core/graph_state_persistence.py` (760 ln) | Crash recovery, state snapshots, atexit/SIGTERM | ✅ |
| `operations/research_influence_gate.py` | Gates learned influence on live/demo; 100+ labelled samples; SHA-256 digest; expiring approval; paper = research-only | ✅ |
| `operations/metrics_exporter.py`, `operations/prometheus_exporter.py` | Prometheus textfile `artifacts/metrics/trader.prom` | ✅ ⚠ `prometheus_exporter.py:266` `log_message` is an unoverridden stub |
| `operations/incident_drills.py` | Incident drills against runbooks | ✅ |
| `operations/learning_loop.py` (129 ln) | `run()` for `daily_review`/`self_improvement`/`weekly_research` | ⚠ dead statically, **wired dynamically** via `executors.py:72` `__import__` |
| `operations/delegation.py` (75 ln) | Task-graph delegation, child/fanout, parent auto-materialize | 🔴 **DEAD** — 0 non-test importers |
| `ops/watchdog/trading_watchdog.py` (128 ln) | NSSM watchdog | 🔴 **DEAD** — misplaced (not in `operations/`), 0 importers |
| `operations/autonomous_pipeline.py`, `operations/improvement_pipeline.py` | Research tick (every 10th pulse), improvement orchestration | ✅ |

## 9.1 The Guardian Process Ψ (`autonomous_quant_graph_remediation.md` §3)

Reference architecture from SEC Rule 15c3-5, Knight Capital ($440 M / 45 min, 2012), LTCM (1998). Hardcoded constants: **Governance Kill Switch Level 4 (external), latency `<500 ms`**, heartbeat 120 s, *"Never auto-clear the switch"*, *"Require a written operator reset with evidence."* `guardian_response` enum on every knowledge-studio node: `ALERT | THROTTLE | SHUTDOWN`. `core/external_guardian.py` is 578 ln.

**The trial-count argument that reframed the whole research layer:**
| Trials attempted | Observed Sharpe | DSR (confidence true Sharpe > 0) |
|---:|---:|---:|
| 1 | 1.20 | ~100 % |
| 100 | 1.20 | ~100 % |
| 1,000 | 1.20 | 84.8 % |
| **18,250** (50 variants/night × 365) | **1.20** | **0.03 %** |

> *"This is not a minor gap — it inverts the meaning of every number the graph produces."* Fix: a **Trial Ledger** — a persistent, never-resettable counter. *"No agent — including the 'self-improving' Meta-Agent — gets to reset it."* ✅ `core/trial_ledger.py` (751 ln), wired from `core/cpcv_backtester.py`.

**The lever that restores power — hypothesis-class scoping (audit 2026-10-20 §7):**
The 2,818-trial global family deflates a new hypothesis against E[max|null] = 3.53 SE: a real annualized Sharpe of 1.9 is detected ~1% of the time — correct, and useless. But the 2,818 trials are not one family. `core/hypothesis_class.py` owns the taxonomy; the ledger deflates a hypothesis within the family it was selected from.

| Family (live ledger) | Trials (k) | Luck bar (SE) | Required SR | Power @ SR 2.50 |
|---|---:|---:|---:|---:|
| `global` (one undifferentiated family) | 2,818 | 3.53 | 3.69 | 4% |
| `model_based` | 482 | 3.03 | 3.33 | 10% |
| `full_pipeline` | 391 | 2.96 | 3.29 | 13% |
| `alpha101_short` | 355 | 2.93 | 3.26 | 17% |
| `mean_reversion` | 348 | 2.92 | 3.26 | 16% |
| `momentum_trend` | 163 | 2.68 | 3.08 | 22% |

> Three rules make the scope honest, and each one is executable. **Canonical** — the family is derived from the hypothesis identity by ONE taxonomy, so a caller cannot under-report its family by forgetting a tag, and two gates inside one call cannot disagree about the family they deflate within. **Complete** — a class pool contains *every* trial of that family, including the rows recorded before the taxonomy existed (a stored `general` tag is re-derived from the strategy id at read time); a bar computed against a fraction of its family is a bar that invents significance. **Fail-closed** — an identity that cannot be attributed to a family keeps the *global* bar; `general` is an observability bucket, never a scope. ✅ `core/hypothesis_class.py` + `core/trial_ledger.py` / `core/strategy_trial_ledger.py`; table regenerated by `python -m research.trial_class_calibration` → `artifacts/trial_ledger/calibration_by_class.json`.

## 9.2 The 12 v1 gaps and their status

| # | v1 component | Gap | Remediation | **Status** |
|---:|---|---|---|---|
| V-1 | Idea Agent | no trial accounting; crawled text trusted (prompt-injection surface) | Research Governance Agent | ✅ `core/trial_ledger.py` + `core/code_safety_agent.py` (719 ln) |
| V-2 | Data Agent | no survivorship-bias-free universe, no PIT fundamentals | Data Integrity Agent | ⚠ partial |
| V-3 | Signal Agent | no leakage control | Purged & embargoed CV mandatory | ✅ `core/cpcv_validation.py` |
| V-4 | Backtest Agent | `Sharpe < 0.8` trial-count-blind | Statistical Validation Agent (DSR + PBO) | ✅ `core/overfit_detector.py` (604 ln) |
| V-5 | Risk Agent | VaR near-normal assumption | CVaR/ES, stress library, fractional Kelly, hard caps | ⚠ CVaR overlay exists but **dead at call site**; no stress library |
| V-6 | — (missing) | no allocation across concurrent live strategies | Capital Allocation Agent | ⚠ `PortfolioRiskArbiter` buckets cover it; **netting/beta limits dead** |
| V-7 | Execution Agent | no slippage/impact model, no idempotency, no reconciliation | sqrt impact law; idempotent IDs + reconciliation | ✅ `core/transaction_cost_engine.py` (336 ln), `core/optimal_execution.py` |
| V-8 | — (missing) | **"The graph is the only thing that can stop the graph"** | Guardian Process, deliberately not an LLM agent | ✅ `core/external_guardian.py` (578 ln) |
| V-9 | — (missing) | LLM code goes straight into strategy | Code Safety Agent; order-placement code needs human sign-off at every level | ✅ `core/code_safety_agent.py` |
| V-10 | — (missing) | no audit trail, no regulatory awareness | Compliance & Audit Agent, hash-chained append-only | ✅ `core/compliance_audit.py` (731 ln), `operations/journal_audit.py` |
| V-11 | Monitor Agent | decay detection with no defined test | CUSUM / Page-Hinkley with a real significance test | ✅ `core/cusum_monitor.py` (201 ln) |
| V-12 | Meta-Agent | untested patterns baked into future search | Meta-Agent discoveries clear the **same** validation gate | ✅ `core/meta_loop.py` (877 ln) + `operations/research_influence_gate.py` |

**11 of 12 remediated.** V-5 and V-6 partially open.

---

# PART 10 — THE KNOWLEDGE CORPUS (the "wisdom" layer)

This is a **knowledge base, not a feature list.** Everything in Part 10 is KNOWLEDGE-ONLY (theory, no code) or DESIGN-ONLY (formula specified, no implementation). **None of it is an unimplemented feature unless explicitly marked so.**

## 10.1 The Six-Layer Autonomous Architecture (retail quant master architecture 03)

| # | Layer | Responsibilities | Sub-components |
|---:|---|---|---|
| **L1** | **DATA INGESTION ENGINE** | MT5 bridge / CCXT websockets / FRED; tick & bar feeds (1m–1d) + stationarity transformation (**d = 0.40**) | microstructure (bid-ask width, tick volume, micro-price); fractional differentiation |
| **L2** | **REGIME DETECTION ENGINE** | Hurst (H) \| rolling ATR percentile \| ADX; outputs `P_t = [P_Trend, P_Range, P_Crisis]` | recompute every 15 min or each bar close |
| **L3** | **DUAL ALPHA ENGINES (dynamic routing)** | 3A Trend (momentum breakouts, Donchian, EMA) · 3B Mean-Reversion (Kalman cointegration, OU) | weights `w_i` routed from L2 |
| **L4** | **CENTRAL SIZING & PORTFOLIO RISK** | Universal 1R budgeting (0.5–1.0 % equity); inverse vol parity + correlation filter (**r < 0.30, heat ≤ 3.0R**) | sub-models emit direction only (+1/−1) + invalidation price; **L4 owns lot size** |
| **L5** | **SMART EXECUTION ENGINE** | Micro-price passive limit posting (`P_micro`); broker reconciliation + order-lifetime guards | cancel if unfilled `N` s and price drifts > 0.20R |
| **L6** | **TELEMETRY & CIRCUIT BREAKER** | rolling EV/SQN/Sharpe + CUSUM drift; intraday kill switch (−3.0R), weekly step-down (−6.0R), portfolio gate (−15 %) | **has veto power back into L4** |

**Handshake contracts:** `L1→L2` data · `L2→L3` capital weights · `L3→L4` raw signals · `L4→L5` lot + stops · `L5→L6` fills · `L6→L4` **veto (the only feedback edge)**.

**Stated subsystem latencies:** L1 2.4 ms + L2 4.1 + L3 8.2 + L4 1.8 + L5 11.2 + L6 11.2 = **38.9 ms** (arithmetic verified ✓).

**Regime allocation matrix (verbatim):**

| Regime | Conditions | Allocation | Capital multiplier |
|---|---|---|---|
| Low-Vol Trend | `H>0.55, ADX>25, low ATR` | Equity momentum, breakout | 1.0–1.2× |
| Low-Vol Range | `H<0.45, ADX<20, low ATR` | Bollinger/RSI mean reversion, stat-arb | 1.0× |
| High-Vol Trend | `H>0.55, VIX/ATR > 85th pct` | multi-asset CTA trend, short S&P | 0.5–0.75× |
| High-Vol Range / Crisis | `H≈0.50, extreme ATR spikes` | long vol, cash | **0.0–0.25×** |

**Operational reference table (verbatim):**

| Stage | Governing rule | Benchmark | Action if violated |
|---|---|---|---|
| Ingestion / stationarity | fractional diff | `d ∈ [0.35, 0.50]` | re-estimate binomial expansion order |
| Regime filter | Hurst & ADX | `H>0.55` trend / `H<0.45` range | shift sub-model weights |
| Trade sizing | fixed fractional | exactly `1.0 %` (1R) | reject if size > 1R budget |
| Portfolio risk | aggregate book heat | `Σ Open Risk_i ≤ 3.0R` | block new entries |
| Execution | spread capture | maker limit at `P_micro` | cancel if unfilled after timeout |
| Intraday safety | hard circuit breaker | max daily DD ≤ −3.0R | liquidate open book, halt 24 h |
| Model health | System Quality Number | `SQN = √N × μ_R/σ_R ≥ 2.5` | decommission if < 1.5 |

**Regime-lag failure audit (the doc's own critique):** `H`, `ADX₁₄`, `ATR₂₀` are backward-looking; structure breaks in ms, metrics need 14–50 bars — *"by the time H>0.55 and ADX>25 register, the trend is exhausted, prompting the system to buy the top."* This is precisely why BOCPD (1–2 ticks) exists as an upgrade.

> **Status:** the six-layer *naming* maps onto the real system (L1↔`data/`, L2↔`intelligence/regime_classifier.py` + `core/bocpd.py`, L3↔`strategies/` + alpha engines, L4↔`core/risk_manager.py`, L5↔`core/order_execution.py`, L6↔`core/cusum_monitor.py` + `operations/autonomy.py`). **But the concrete L3 dual engines do not exist** — `strategies/` has 0 strategy implementations and 43 registry entries carry no code pointer.

## 10.2 The Seven-Tier Analysis Pyramid (knowledge taxonomy — a different axis)

`Tier 1 Philosophy/Epistemology` → `Tier 2 Formal Sciences` → `Tier 3 Domain Sciences` → `Tier 4 Macro-Fundamental Analysis` → `Tier 5 Analytical Approaches (Top-Down / Bottom-Up / Quantamental)` → `Tier 6 Causal Discovery & BMA Pool` → `Tier 7 Execution & Risk Management`

**Three Pillars of Macro:** Real Activity · Inflation & Purchasing Power · Monetary Policy & Liquidity.
**Three-Tools Framework:** Real Economic Activity × Financial Conditions × Market Fragility & Sentiment → 3 regime axes: Growth (Expansion/Contraction), Inflation (Reflation/Disinflation), Liquidity (Easy/Tight-Crisis).

**Master-analyst 3-phase method:**
- **Phase 1 Triage (4 questions):** Complicated vs Complex? Structured math vs Unstructured semantics? Time horizon? Cooperative vs Adversarial?
- **Phase 2 Filter Stack:** Telescope (Describe) → Magnifying Glass (Diagnose anomaly) → Scalpel (Causal transmission) → Compass (Prescribe: risk parity / convexity) → Falsifier (Popperian disconfirmation).
- **Phase 3 Zen:** remove all filters; recognize analysis = model reduction.

## 10.3 Algorithm / model catalogue with implementation status

### Causal discovery
| Method | Mechanism | Status |
|---|---|---|
| **Granger causality F-test** | `F = ((RSS_R−RSS_U)/q) / (RSS_U/(T−p−q−1))` | ✅ **IMPLEMENTED** `core/econometric_features.py` (235 ln, ⚠ 0 non-test importers), `core/causal_feature_filter.py` (505 ln, wired) |
| PC / FCI | skeleton → v-structures → Meek rules | ❌ **ASPIRATIONAL** — absent |
| GES / FGES | `BIC(G;D) = ln P(D|θ̂_G,G) − (k_G/2) ln n` | ❌ ASPIRATIONAL |
| NOTEARS / GOLEM | `min_W ½n‖X−XW‖²_F + λ‖W‖₁` s.t. `h(W)=tr(e^{W⊙W})−d=0` | ❌ ASPIRATIONAL |
| PCMCI / PCMCI+ | MCI test conditioning on both parents' histories | ❌ ASPIRATIONAL |
| LiNGAM | `x_i = Σ c_ij x_j + e_i`, `e_i` non-Gaussian | ❌ ASPIRATIONAL |
| Transfer Entropy | information-theoretic | ❌ ASPIRATIONAL |

**Consensus Edge Filter rules:** (1) no spurious inclusion — an indicator is admitted only if a directed causal path to the target exists in the consensus DAG; (2) an edge is confirmed by **≥ 2 distinct algorithmic families**; (3) temporal precedence `Δt > 0`. Pipeline: 200+ raw features → stationarity/outlier preprocessing → multi-algorithm engine → consensus filter (≥2) → validated DAG → causally-informed feature filter. Dark-node discovery: an unrecognised semantic triple spawns `U_UNKNOWN_SHOCK` / `U_CHOKEPOINT_SURCHARGE` candidates for online discovery.

**CESI (Causal Edge Stability Index):** `CESI = (1/|E_target|) Σ_e ( (1/W) Σ_{τ=1..W} 𝕀(e ∈ G_τ) )`, `W = 60` trading days. `CESI < 0.70` → halve position sizes + schedule DAG rediscovery. *(DESIGN-ONLY — metric not in code; `cesi_score` is a hardcoded literal at `graph_nodes.py:281`.)*

**Seven Confluence Gates:** causal path `p < 0.01`, hit rate `≥ 65 %`, live validated edges = 14 at `p = 0.0002` (doc claim, DESIGN-ONLY).

### Regime detection
| Method | States | Status |
|---|---|---|
| HMM-2 (Expansion/Contraction) | 2 | DESIGN-ONLY (BMA pool) |
| HMM-3 (Bull/Sideways/Bear) | 3 | DESIGN-ONLY (BMA pool) |
| Hurst exponent (R/S), min 50 points | continuous | ✅ `core/metrics.py:668-669` (returns 0.50 if n<50), `hierarchical_portfolio.py` |
| ADX₁₄, ATR percentile | — | ✅ |
| BOCPD (Adams–MacKay) | run-length | ✅ `core/bocpd.py` (95 ln) — ⚠ sliding-Gaussian predictive, not conjugate NIG; **not consumed by `detect_regime()`** |
| CUSUM | — | ✅ `core/cusum_monitor.py` (201 ln) |
| Markov Switching AR | — | ❌ ASPIRATIONAL (P1) |
| GEPA / Proto-MAML few-shot | — | ❌ **NO-GO** per applicability matrix |

Threshold set: `H > 0.55` trending · `H < 0.45` mean-reverting · `H ≈ 0.50` GBM (un-tradeable) · `ADX > 25` confirms momentum · ATR pct > 90th = crisis.

### Validation / sizing / survival — all ✅ implemented
| Name | Formula | Threshold |
|---|---|---|
| **Purged K-Fold CV** | purge training obs whose label horizon spans test; embargo 1–5 % | ✅ `core/cpcv_validation.py` |
| **CPCV** | 16 purged splits → N train/test paths for PBO | ✅ `core/cpcv_backtester.py` |
| **DSR** | `Φ( (SR̂−SR₀)√(T−1) / √(1 − γ₃SR̂ + ((γ₄−1)/4)SR̂²) )` | ✅ 6 files. ⚠ E[maxSR] uses `√(2 ln k) − (ln π + ln ln k)/(2√(2 ln k))`; canonical is `ln 4π` — direction is conservative |
| **Triple barrier** | `y_t = +1/−1/0`; barriers `±k·ATR` | ✅ `core/meta_labeling.py` |
| **Meta-labeling** | primary recall-optimized + secondary precision-optimized | ✅ ⚠ advisory only — never reaches sizing |
| **Fixed fractional** | `q = (Capital × Risk%) / |Entry − Stop|` | ✅ risk ≤ 1.0 % |
| **Kelly** (3 equivalent forms) | `f* = [p(b+1)−1]/b`; `K* = [P_win·R − (1−P_win)]/R`; `f* = (μ−r_f)/σ²` | ✅ `core/account_sizing.py`; quarter-Kelly `0.25K*`, half-Kelly `0.5K*` |
| **Quarter-Kelly worked example** | `K* = (2.41×0.714 − 0.286)/2.41 = 0.595 ⟹ 0.25K* = 0.14 NAV (0.50 % trade cap)` | doc example |
| **Vol targeting** | `Scaled = Base × σ_target/σ_realized(t)`; `w_i = (1/ATR_20,i)/Σ_j(1/ATR_20,j)` | ✅ ⚠ overlay dead at call site |
| **RoR** | `((1−A)/(1+A))^U` | ✅ target 0.00 % |
| **R-multiple** | `R = (P_exit − P_entry)/(P_entry − P_stop)` | ✅ |
| **Sharpe / Sortino / CAGR / Calmar / MaxDD / SQN / WFE / IC** | standard | ✅ `core/metrics.py` (1,198 ln) |
| **Run theory** | `P(k) = P_loss^k`; `L_max ≈ ln N / ln(1/P_loss)` — 200 trades @50 % ⇒ `L_max = 7.64` | ✅ |
| **Monte Carlo** | 10,000 permutations; AR(1) ensembles; Markov regime-switching MC | ✅ `research/synthetic_path_simulator.py` |
| **FDR / Bonferroni** | `P_(i) ≤ (i/m)·α` | ❌ ASPIRATIONAL |
| **White's Reality Check / SPA bootstrap** | — | ❌ MISSING (named gap) |
| **Online Ledoit-Wolf shrinkage covariance** | — | ❌ MISSING (named gap) |

**Recovery asymmetry table:** −10 %→+11.1 % · −30 %→+42.9 % · −50 %→+100 % · −80 %→+400 % · −90 %→+900 %.

**The four fatal sins:** Overfitting (→WFA + BMA) · Lookahead (→PIT + DAG precedence) · Survivorship (→include delistings) · Txn-cost neglect (→Almgren-Chriss sqrt).

**CAGR fix applied:** `core/metrics.py` `calculate_cagr` now returns **−100 %** when `end_equity <= 0` instead of 0 % — *"total ruin was previously rendered as 'flat', hiding the worst outcome from Calmar/CAGR consumers."* Sharpe denominator now uses dispersion of **excess** returns.

### Fractional differentiation
`(1−B)^d X_t = Σ_k (−1)^k C(d,k) X_{t−k} = X_t − dX_{t−1} + [d(d−1)/2!]X_{t−2} − …`
`d = 0.40` canonical. ✅ `core/fractional_diff.py` (178 ln). ⚠ **computed by background tools only — never consumed by the cycle.**

### Transaction cost engine (Almgren–Chriss + Perold implementation shortfall)
```
base_spread_price = arrival_price × (base_half_spread_bps / 10000)          # 1.5 bps
vol_mult  = 0.75 × max(0, (current_atr/baseline_atr) − 1.0)
liq_mult  = 0.50 × max(0, (avg_bar_volume/max(1,current_volume)) − 1.0)
event_mult = 2.0 if is_high_impact_event else 0.0
dynamic_spread = base_spread_price × (1 + vol_mult + liq_mult + event_mult)
  LIMIT → return −0.25 × dynamic_spread   (maker rebate: capture 25 % of the spread)
i_perm = γ σ p₀ (q / v_adv)                                  # γ = 0.314
i_temp = η σ p₀ (v_rate / max(1,v_bar))^α                    # η = 0.142, α = 0.5
effective_impact = 0.5·i_perm + i_temp
exec_price = arrival_price + direction·(half_spread + effective_impact)
total_dollar_cost = |exec_price − arrival_price| × order_size + explicit_fees
is_bps = (total_dollar_cost / (arrival_price × order_size)) × 10000
explicit: comm = q×0.0035; exchange = q×0.0015 (MARKET) or −q×0.0010 (LIMIT);
          SELL only: SEC 0.00278 % of notional; FINRA TAF min(8.30, q×0.000166)
```
**Price impact:** `I_total = Y σ_daily √(Q/V_ADV)`, `Y ≈ 0.5–0.7`. **Realized avg exec price:** `P_exec = P_0 + side·(S_t/2 + ½·I_perm + I_temp)`. **Net expectancy:** `EV_net = EV_gross − (Total Implementation Shortfall / 1R Dollar Budget)`. **Rule of thumb:** round-trip friction > 10 % of the 1R budget ⇒ severe live alpha degradation. ✅ 336 ln, wired `core/auto_quant_tools.py`. ⚠ `asset_class` is declared but **never used** — equity/futures/FX/crypto branches are not implemented.

**Dynamic spread simulation (backtest):** `S_t = S_base × [1 + α(σ_t/σ̄) + β(V̄/V_t) + γ·𝕀_event] × M_session(t)` where `M_session` = **1.0×** London/NY overlap, **3.5×** at 17:00 EST FX rollover.

### Microstructure
| Model | Formula | Status |
|---|---|---|
| **VPIN** | `Σ_τ |V_τ^B − V_τ^S| / (N·V)` | ✅ `intelligence/macro_alpha_signals_and_microstructure.py` |
| **Micro-price** | `P_micro = (Q_bid·P_ask + Q_ask·P_bid)/(Q_bid+Q_ask)` | ✅ |
| Effective spread | `S_eff = 2q(P_exec − M_t)` | ✅ |
| Realized spread | `S_realized = 2q(P_exec − M_{t+τ})` | ✅ |
| Price impact | `PI = 2q(M_{t+τ} − M_t) = S_eff − S_realized` | ✅ |
| BVC | `V_τ^B = V·Φ(ΔP_τ/σ_ΔP)` | ❌ ASPIRATIONAL |
| Hawkes | `λ(t) = μ + Σ α e^{−β(t−t_i)}` | ❌ ASPIRATIONAL |
| Kyle's λ, OFI | price-impact slope / `ΔQ_bid − ΔQ_ask` | DESIGN-ONLY |

### BMA / decision fusion — three different pools documented (a doc inconsistency)
**Bayesian Model Averaging:** `Pr(y|D) = Σ_{i=1..K} w_i Pr(y|M_i, D)`. Log-score with memory decay: `LL_i^(t) = λ·LL_i^(t−1) + ln p(y_t|y_<t, A_i)`; numerically-stable softmax: `w_i = exp(LL_i − max_j LL_j)/Σ_k exp(LL_k − max_j LL_j)`. Fused prediction + between-model variance: `ŷ = Σ w_i ŷ_i`; `Var(ŷ) = Σ w_i[σ_i² + (ŷ_i − ŷ)²]`. Weight entropy `H(w) = −Σ w_i ln w_i`; `N_eff = 1/Σ w_i²`; dominance alert if `max w_i > 0.90`; ambiguity when `H → ln K`.

| Pool | Members | Weights | Source |
|---|---|---|---|
| **A₁–A₆** (macro regime) | A₁ rules 0.08 · A₂ GMM 0.14 · A₃ HMM-2 0.18 · A₄ HMM-3 0.22 · A₅ ensemble 0.16 · A₆ causal-graph 0.22 | Σ=1.00 | BMA 01 / SSOT |
| **A₁–A₅** (geopolitical) | Liberal Order 0.20 · Multipolar 0.25 · Bipolar 0.35 · Fragmentation 0.15 · Hegemonic 0.05 | Σ=1.00 | geopol 02 |
| **A–E** (sanctions) | Economic Coercion 0.20 · Financial Weaponization 0.25 · Tech Chokepoints 0.30 · Multilateral Legitimacy 0.15 · Domestic Regime 0.10 | Σ=1.00 | sanctions 04 §9 |

> **All three pools are DESIGN-ONLY — no BMA pool code exists.** `λ` is stated as 0.98 in the SSOT but 0.99 in BMA-07 and geopol-02 — an unresolved doc conflict.

### Synthetic spreads (physical stoichiometry) — 24 formulas, all DESIGN-ONLY
Haber–Bosch ammonia `33.5·P_HH + 65` · corn/soy `P_Soy/P_Corn` (>2.50 soy-favored, <2.20 corn-favored) · 3:2:1 crack (>��30 tight, $18–30 healthy, <$12 run cuts) · soy crush (>$2.00/bu) · spark spread (>$25/MWh) · clean dark spread `P_Elec − 0.38·P_Coal − 0.85·CarbonCost` · Brent–WTI (>$6) · Brent–Dubai EFS (>$3.50) · gasoil crack (>$30) · BTU parity `P_WTI/(6·P_HH)` (>5.0 switch to gas) · copper/gold (>2.20 reflation) · copper/oil (>6.0) · lumber/gold (>0.35) · XLI/XLU (>1.80) · MOVE/VIX (>7.5) · gold/silver (>85) · platinum/gold · HRC mill spread `P_HRC − 1.6·P_IronOre − 0.77·P_CokingCoal` · aluminum smelter · cattle crush · hog/corn · wheat/corn (<$0.60/bu substitution) · HY/IG OAS ratio (>3.5 contagion) · **US Net Liquidity `WALCL − TGA − ON_RRP`** (0.90 correlation to equity/crypto liquidity).

**τ_substitution spectrum (the deep structural idea):** crude→ethanol hours–days · corn→soy acres 1 growing season (6–12 mo) · API synthesis 2–4 yr · sub-2nm fab 5–7 yr · Cu/Li greenfield 7–12 yr. Downstream ripple weights scale by τ.

**Four data-as-of-frequency tiers:** T1 MACRO REGIME (FRED 10Y-2Y, EFFR, HY spreads; monthly/weekly; `S_macro ≥ 0.70` = 100 % risk, `< 0.30` = 25 % or cash) → T2 FACTOR SELECTION (SEC EDGAR, CFTC COT; weekly/quarterly) → T3 TACTICAL & EVENT GATING (ForexFactory red folder; blackout −15 min before / +30 min after; sentiment veto if > 85 % one-sided) → T4 EXECUTION & SIZING (MT5/WebSocket; tick/sub-second).

### Predictive / chaos mathematics — KNOWLEDGE-ONLY
Linear · ballistic (`t_impact = (v₀ + √(v₀²+2gy₀))/g`) · exponential/logistic · state-space (discrete) · 4-component decomposition `Y=T+S+C+ε` · **random-walk MMSE** (⇒ *"any perceived geometric chart pattern is a statistical artifact"*) · Jacobian/sensitivity · Lyapunov divergence `‖δx(t)‖ ≈ ‖δx₀‖·exp(λ_max·t)` · **predictability horizon** `t ≈ (1/λ_max)·ln(ε_max/ε_initial)` — macro regimes 3–18 mo, weather 10–14 d, order book ms, planetary orbits ~100 My · GPR index (Caldara–Iacoviello) · 5-tier prediction hierarchy with explicit confidence bands (L1 physical laws 99.9 % → L5 strategic scenarios unquantifiable) — *"The Parachute Principle"* · 5-phase strategic foresight (horizon scanning → trajectory velocity → scenario matrix → EWS triggering → asymmetric positioning) · 2×2 scenario matrices · COMPASS 7 bearings · SIPRI 8-domain EWS with the Russia–Ukraine tripwire ladder (3 mo SAR troop massing → 2 mo diplomatic ultimatum → 1 mo cyber pre-emption → 3 days DPR/LPR recognition → Event Zero) · Bullwhip `Order_t = Demand_t + k(Target−Actual)·exp(Uncertainty)` · 7 structural meta-lenses (prompt spreads, shadow fleets, JWC war-risk premiums, L/C trade-finance freezes, battery black mass, sovereign production concentration, τ_substitution).

### ML / NLP / alt-data
**FinBERT** (12 layers, 768 hidden, domain-trained on 10-K/10-Q/FOMC/earnings): `SentimentScore(s) = P(Pos|s) − P(Neg|s)`; `ΔS = (1/N)Σ SentimentScore − E[S]`; trigger `ΔS > +2σ_hist` ⇒ PEAD long. ❌ **`intelligence/llm_finance.py` does not exist** — only a 41-line dead module in `trading_knowledge_base/modules/`.
**PPO / A2C / DDPG / Meta-RL** ✅ `research/ppo_trainer.py`, `meta_rl_trainer.py` (PEARL latent encoder + RL²-PPO). **EWC** `L(θ) = L_new + Σ (λ/2)F_i(θ_i − θ_old)²` ✅ 346 ln. **22-pillar zero-lag nowcasting stack** (5–90 day leads vs 15–90 day government lag; 0-day/sub-second for SAR, VIIRS, TROPOMI, SCADA, AIS) — DESIGN-ONLY. Ridge/Lasso, XGBoost/LightGBM, Isolation Forest/LOF/Mahalanobis/Autoencoder, t-SNE/UMAP — DESIGN-ONLY.

## 10.4 Institutional-vs-retail applicability verdict

`institutional-vs-retail-applicability/` is an **adversarial triage** of the rest of the corpus. Key verdicts:
- **NO-GO:** GEPA / Proto-MAML few-shot regime classification; RMT-3 of Richard-Stein (no `HierarchicalRiskParity` symbol exists).
- **PARTIAL:** CRL-ABSO and CLEAR graded **4/5 Hybrid** — *"Recommend piloting on top of the existing causal DAG."* The full CRL-ABSO stack is P3/do-not-build — *"20 % effort for 80 % gain" from the lite version.*
- **The central retail insight (the single most actionable finding in the corpus):** triple-barrier labeling + meta-classifier + calibrated slippage + EV machinery all exist, but the chain breaks at two links: **(a)** the meta-classifier is never trained/served on rolling labelled data; **(b)** its output never replaces LLM confidence in `calc_position_size`. *"Closing this loop converts the system from 'LLM guess + fixed fraction' to 'calibrated edge + fractional Kelly'."*

## 10.5 Portfolio optimization series (8 papers — KNOWLEDGE-ONLY, explicitly deferred)

Risk metrics (0103107) · noisy covariance I (0111503) · noisy covariance II (0205119) · estimated correlation (0305475) · exponential weighting + RMT (0402573) · random-matrix filtering (0509235) · noise sensitivity across risk measures (0611027) · synthesis & trading implications · book chapter on absolute deviation.

## 10.6 The raw research .txt corpus (KNOWLEDGE-ONLY reading material)

`casual model deep domain knowledge .txt` (4,948 ln) · `quan types and model.txt` (1,774) · `retail quant model.txt` (1,511) · `news trading model.txt` (1,031) · `quant papers autonomous quant trading systems .txt` (905) · `how to find edge.txt` (548) · `retail_quant_models_part1-3.txt` · `complete_trading_models_part1.txt` · `swing_scalp_math_models_extracted.txt` · `swing_scalp_math_models_supplementary.txt` · `strategy discover agenttic ai.txt` (592) · `model optimization and standard` (no extension) · `knowledge/types of analysis…txt` (569) · `knowledge/extracted_knowledge_base.md` (**43,651 ln**).

**These are reading material, not specifications.** They must not be read as unimplemented features.

## 10.7 Cross-discipline knowledge volumes (100 % KNOWLEDGE-ONLY)

`analytical_frameworks_and_epistemology/` Vol 01–07: geopolitical/macro-commodity · quantitative-statistical-chaos · economic systems & input-output (Leontief `(I−A)⁻¹d`) · strategic intelligence & adversarial (Clausewitz CoG, Kill Chain, link analysis, PESTLE/SWOT, RPN = S×O×D, 5 Whys) · behavioral-cognitive-psychological (FACS `AU6+AU12`, availability & attribution biases, memetic `R₀ = β·c·d`) · epistemology-ontology-philosophy (Goodhart/Campbell, observer effect, Constructal Law, Shannon entropy, 3-phase triage, 5-layer filter stack) · specialized-niche (FEA, TGA, S-N fatigue, side-channel, PVA, phylogenetics, Schenker I-V-I, ADSR, golden ratio, Space Syntax, Hall proxemics, Foucauldian genealogy, modal/deontic logic, sensory panels, GC-MS, Propp 31 functions).

`geopolitical_predictive_and_sanctions_intelligence/` 01–06: predictive/extrapolation mathematics · geopolitical macro foresight & scenario planning · multidisciplinary domain compendium · **economic sanctions / financial plumbing / evasion forensics (1,624 ln — IEEPA/TWEA/ECRA/AECA, OFAC 5-component SCP, 50 % Rule, SWIFT MT103/MT202/MT700/ISO 20022, Nostro/Vostro, EAR vs ITAR + 21 USML categories, 6-tier semi evasion, shadow-fleet economics, secondary sanctions, price-cap assessment, Taiwan 72-hour contingency)** · case-study matching & graph cognition · global macro ratios & synthetic spreads.

`dispersion_bias/` — one paper with a rigorous GO/NO-GO gate, **mostly ASPIRATIONAL** (verified missing). S1/S2/S5/S7/S10 are doc-only systems.

---

# PART 11 — ALPHA MINING & THE FACTORY

## 11.1 The 23-alpha factor library (`core/alpha_factors.py`, 461 ln) ✅ fully implemented

12 primitives (`ts_rank, ts_argmax, ts_argmin, ts_min, ts_max, delta, delay, rank, scale, decay_linear, signed_power, log_safe`) + `_vwap` + `_ALPHA_REGISTRY` keyed `ALPHA001..ALPHA101` + `compute_alpha_factors(df, requests=None)` which **raises `ValueError` on an unknown alpha name**.

Reference: Kakushadze, Z. (2016) *"101 Formulaic Alphas"*, SSRN 2701346.

| Alpha | Category | Formula | Min bars |
|---:|---|---|---:|
| 001 | Volatility | `rank(ts_argmax(SignedPower(returns<0 ? stddev(returns,20) : close, 2), 5)) − 0.5` | 25 |
| 005 | VWAP | `rank(open − avg(vwap,10)) × (−1 × |rank(close − vwap)|)` | 15 |
| 009 | Momentum | 5-bar min/max of deltas (flip detector) | 10 |
| 012 | Volume-Price | `sign(δ(volume,1)) × (−1 × δ(close,1))` | 3 |
| 014 | Returns | `−1 × rank(δ(returns,3)) × corr(open, volume, 10)` | 15 |
| 017 | Composite | `−1 × rank(ts_rank(close,10)) × rank(δδ(close,1)) × rank(ts_rank(volume/adv20,5))` | 25 |
| 019 | Mean reversion | `−1 × sign((close−delay(close,7))+δ(close,7)) × (1+rank(1+Σ returns(250)))` | 255 |
| 022 | Correlation | `−1 × δ(corr(high,volume,5),5) × rank(stddev(close,20))` | 25 |
| 023 | Overbought | `(sma(high,20) < high) ? −1×δ(high,2) : 0` | 22 |
| 025 | Bearish | `rank((−1×returns) × adv20 × vwap × (high−close))` | 25 |
| 028 | Value | `scale(corr(adv20, low, 5) + (high+low)/2 − close)` | 25 |
| 030 | Consensus | `(1−rank(sign(δs))) × (Σvolume(5)/Σvolume(20))` | 25 |
| 034 | Volatility | `rank(2 − rank(σ2/σ5) − rank(δ(close,1)))` | 10 |
| 035 | Composite | `ts_rank(volume,32) × (1−ts_rank(close+high−low,16)) × (1−ts_rank(returns,32))` | 37 |
| 036 | Composite | 5-component weighted composite | 205 |
| 037 | Mean reversion | `rank(corr(delay(open−close,1), close, 200)) + rank(open−close)` | 205 |
| 038 | Gap | `−1 × rank(ts_rank(close,10)) × rank(close/open)` | 15 |
| 041 | VWAP | `sqrt(high × low) − vwap` | 5 |
| 043 | Volume | `ts_rank(volume/adv20,20) × ts_rank(−1×δ(close,7),8)` | 25 |
| 053 | Stochastic | `−1 × δ(((close−low)−(high−close))/(close−low), 9)` | 14 |
| 055 | Stochastic | `−1 × corr(rank(stoch), rank(volume), 6)` | 18 |
| 060 | Tick rule | `−1 × (2×scale(rank(imbalance)) − scale(rank(ts_argmax(close,10))))` | 15 |
| 101 | Composite | `((close−open)/((high−low)+.001)) × volume` | 5 |

## 11.2 The IC-IR ensemble (`core/alpha_ensemble.py`, 787 ln) ✅

```
weight_i = max(IC_IR_i − min_ir, 0) / Σ_j max(IC_IR_j − min_ir, 0)      # negative IC_IR → 0
signal:      composite > +threshold_entry → LONG ; < −threshold_entry → SHORT ; else NEUTRAL
confidence:  |composite| > 2.0 → HIGH ; > 1.0 → MEDIUM ; else LOW
```
`ic_ir > 0.5` = strong, `< 0` = useless. `stability > 0.5` = consistent. `normalize_method ∈ {zscore, rank}`; `weighting ∈ {ic_ir, equal, ic}`. `rolling_ic` (Spearman, window 60), `expanding_ic` (min_periods 40), `compute_alpha_quality`, `format_quality_report`. `orthogonalize_signals()` in `core/signal_orthogonalizer.py` (527 ln) supports OLS / PCA / Gram-Schmidt.

## 11.3 `research/alpha_miner/` — 31 files, 9,567 lines, **zero `pass`-only stubs**

`ALPHA_MINING_INTEGRATION_PLAN.md` is labelled `Status: PLANNING` with `pass` bodies, but **the code is real and partly wired.** Doc-vs-code verdicts:

| Plan claim | Reality |
|---|---|
| "PLANNING / aspirational" | 🔴 **STALE** — `_apply_alpha_mining_enrichment` implemented (`llm_orchestrator.py:4408`, writes `toon["alpha_mining_results"]` `:4468,4482`) |
| `alpha_mining_sprint` schedule (604800 s) to be registered | ✅ **ALREADY REGISTERED** `operations/agent_scheduler.py:41`; handler `operations/executors.py:34-35` |
| `research_agent.handle_alpha_mining_sprint` to be written | ✅ **EXISTS** `operations/agents/research_agent.py:78` |
| `AlphaMiningPipeline` never instantiated | 🔴 **STALE** — instantiated at `research_agent.py:108`, reached weekly. `pipeline.py` is **913 ln** (plan says 894) |
| `projector.select(...)` undefined | ✅ **DOC-ONLY ERROR** — real `PCAProjector` API is `compute_behavior_matrix:66, fit_transform:110, transform:140, get_behavior_grid:154, get_grid_coverage:190, get_cell_distances:206, batch_project_alphas:243`. There is no `select`; the plan's inventory was incomplete |
| `bridge.get_active_alpha_strategies()` undefined | ✅ **DOC-ONLY ERROR** — defined at `research/alpha_miner/strategy_bridge.py:278` |
| `tests/test_alpha_integration.py` | ❌ **DOES NOT EXIST** (9 `test_alpha_*.py` files exist, none by that name). `tests/test_alpha_miner.py` has 10 real asserts |

**⚠ The real problem is orphaning, not stubbing.** Only 4 of 31 modules are reachable: `ga_engine` (`mt5_trader:4178,5155`), `alpha_risk` (`core/risk_manager:1030`), `alpha_judge` (`research/auto_promotion:479`), `strategy_bridge` (`llm_orch:4415`). **20 are dead** (0 non-test importers): `alpha101_knowledge_base, cvar_risk, dnn_aggregator, dynamic_stops, exploration_grid, failure_modes, fvg_detector, genetic_operators, grid_selector, horizon_alpha, kat_detector, liquidity_screen, pairs_trader, rag_specification, regime_detector, repair_engine, rl_agent, spoofing_detector, volume_breakout, walk_forward`.

**And `core/risk_manager.py:1030` wires `alpha_risk` into `calc_position_size` — but the production call site passes no `market=`, so it early-returns at `:1035`. Real code that never executes.**

`core/smc_structure.py:17` comments *"reuse `research.alpha_miner.fvg_detector`"* while never importing it.

## 11.4 The FactorMiner Ralph Loop ✅ built and wired

`core/alpha_discovery.py` (675 ln): Retrieve → Generate → Evaluate → Distill. `run_discovery_cycle()`, `AlphaCandidate` dataclass, `code_safety_agent` integration. `core/alpha_decay_monitor.py` (591 ln) rolling-IC health + `DecayAlert` → `alpha_decay_state.json`. `core/alpha_rotation.py` (600 ln) dead-alpha removal + candidate promotion → `alpha_rotation_state.json`. `core/meta_loop.py` (877 ln) Meta-Evolution Engine (Loop 4) → `meta_corpus.db`, monthly trigger. `core/evolutionary_optimizer.py` (493 ln) MadEvolve multi-generation search, `StrategyGene`, 15 ≤ N ≤ 20. `core/parameter_stability.py` (475 ln) stability analyzer, cliff detection, boundary distance, grade A–F. `core/ewc_forgetting_prevention.py` (346 ln) Fisher-matrix penalty. `core/bounded_autonomy_pipeline.py` (612 ln) NZE Bounded Autonomy `S → V_LLM → R` with LLM veto capped order.

## 11.5 Research / ML inventory (all ✅ exist, wiring noted)

`research/alpha_backtest_harness.py` (1,436 ln, ⚠ **DEAD** — 0 non-test importers; dual engine `vectorbt`/`mt5`/`hybrid`) · `ensemble_backtest.py` (267 ln, DEAD) · `edge_lifecycle.py` · `ppo_trainer.py` · `meta_rl_trainer.py` · `rl_trainer.py` · `rl_action_bridge.py` · `rl_analytics.py` (309 ln, DEAD) · `supervised_training.py` · `model_registry.py` · `model_selector.py` (wired `mt5_trader:333`) · `model_family_gate.py` · `kronos_bridge.py` / `kronos_mt5_adapter.py` / `kronos_downloader.py` / `kronos_trading_integrator.py` (MCP server also present, 5 files) · `broker_replay.py` · `historical_regimes.py` · `historical_regime_replay.py` · `auto_promotion.py` · `research_validation.py` · `core/walk_forward_validator.py` (597 ln) · `research/synthetic_path_simulator.py` · `research/grpo_proposer.py` (452 ln) · `research/decision_models/` (10 files) · `models/kronos-mini`, `kronos-small`, `kronos-tokenizer-2k`, `laya/` (ModernBERT-421M), `bespoke_nimble_9b/`.

**Kronos MCP (14 tools):** `kronos_alpha_factor` · `kronos_forecast` · `kronos_forecast_batch` · `kronos_forecast_multi_dim` · `kronos_mt5_advanced_volatility` · `kronos_mt5_forecast_multi_dim` · `kronos_risk_sizing` · `kronos_status` · `kronos_synthetic_generate` · `kronos_synthetic_multi_asset` · `kronos_tokenize` · `kronos_volatility_forecast` / `kronos_volatility_term_structure`. All carry `execution_authority=False`. Alpha formula: `tanh(predicted_return/2) * confidence → weighted_alpha`, direction at ±0.15 thresholds.

**MT5 MCP server (28 tools):** account_info, connection_status, health_check, positions_get/total, orders_get/total, symbol_info/tick, symbols_get/total, copy_rates_from/pos/range, copy_ticks_from/range, history_deals_get/total, history_orders_get/total, order_calc_margin/profit/check/send, position_observations, symbol_select, terminal_info, version, server_info, last_error.

---

# PART 12 — OPERATOR SURFACES

## 12.1 `ui/gui_api.py` — 64 routes (3,337 ln, FastAPI) **[CODE]**

55 GET + 7 POST + 1 DELETE + 1 WEBSOCKET, plus 1 static mount of `gui/dist/`.

| Domain | Routes |
|---|---|
| Health / root | `/`, `/api/health`, `/api/diagnostics`, `/api/system-health` |
| Harness events | `WS /ws/harness-events`, `/api/harness-events/recent` |
| Core state | `/api/status`, `/api/account`, `/api/logs`, `/api/dashboard`, `/api/snapshot` |
| Trading | `/api/trading/positions`, `/api/trading/history` |
| News / macro | `/api/news`, `/api/economic-events`, `/api/mtf-trends`, `/api/intermarket`, `/api/zero-lag` |
| Brain | `/api/brain/{metrics,cycle-brief,profitability,observability,memory,regime,causal-edges,macro-observations,economic-coverage,realtime-news}` |
| Config / accounts | `/api/config/models`, `/api/config/accounts` (GET/POST), `/api/config/accounts/secure`, `/api/config/accounts/test-login`, `/api/config/accounts/secure-login`, `DELETE /api/config/accounts/{name}` |
| Control | `POST /api/control/start`, `POST /api/control/{action}`, `POST /api/internal/update` |
| Research | `/api/research-pipeline`, `/api/backtests`, `/api/alpha-signals`, `/api/alpha-health`, `/api/causal-fusion`, `/api/win-probability`, `/api/rl-training`, `/api/model-registry`, `/api/llm-providers` |
| Ops | `/api/agents`, `/api/tasks`, `/api/scanner`, `/api/skills`, `/api/approvals`, `/api/analytics`, `/api/autonomy/history`, `/api/workflows`, `/api/lifecycle-full`, `/api/compliance-events`, `/api/compliance-full`, `/api/security-full`, `/api/growth`, `/api/rewards`, `/api/drills`, `/api/metrics-export`, `/api/settings-full`, `/api/market` |

**Only 2 endpoints are genuinely fabricated:** `GET /` (`:96`) and `GET /api/health` (`:104`) return pure literals — and **`/api/health` is the readiness probe `START_DECK.bat` polls at `:APIREADY`.**

**3 endpoints fail-closed-to-empty (correct but indistinguishable from "no data"):** `/api/approvals:1774` (`except: return []`), `/api/market:1967` (`return {}`), `/api/scanner:2424` (sets `X-Engine-Status: ENGINE_OFFLINE`, so it is honest).

**`/api/backtests` is now a 4-source fan-in** (`:1778`): `hybrid_backtest_report.json:1785` → `artifacts/backtest_results/pipeline_results.json:1819` → `backtest_report.json:1852` → `auto_quant_tools_state.json:1879`. The old audit finding that it "only looks in auto_quant_tools_state.json" is **stale**.

**`/api/tasks` returns real data** (`:2059-2075` — `ui_state._harness_events[-20:]` with a direct SQLite fallback). **`/api/model-registry` returns real data** (`:3094-3105` — always appends the canonical `laya-modernbert-421m` artifact).

**The GUI is largely REAL, not mocked.** It reads `trading_state.db`, `autonomy_state.json`, `scanner_state.json`, and calls into `operations.analytics`, `operations.strategy_factory`, `brain_observability`, `data.market_data_provider`.

## 12.2 `tui_opentui/` — 40 screens (Bun + OpenTUI) **[CODE]**

Registered in `App.tsx:14-53`, dispatched `:254-322`. Largest: `GraphScreen.tsx` (179 KB), `MT5Screen.tsx` (40 KB), `StrategyObservatoryScreen.tsx` (32 KB), `PositionsScreen.tsx` (24 KB). Smallest: `ScannerScreen.tsx` (1.6 KB). Support: `App.tsx` (12 KB), `store.ts` (16 KB), `types.ts` (33 KB), `transport/live.ts` (72 KB), `transport/mcp.ts` (27 KB), 26 `components/*.tsx`, 6 test/proof harnesses.

Full screen list: Stream · Cockpit · Brain · Causal · Macro · Positions · Journal · Tasks · Strategies · Analytics · Map · Approvals · Graph · Skills · Backtest · Alpha · Diagnostics · Workflows · MT5 · Lifecycle · Fusion · ResearchPipeline · DataSources · Scanner · RLTraining · ValidationPipeline · Memory · Deliberation · SystemHealth · Settings · Autonomy · Rewards · Security · Growth · Models · Metrics · Drills · LogMonitor · Orchestration · StrategyObservatory.

## 12.3 `gui/` — React/Vite/TS app (8 components) **[CODE]**

`App.tsx`, `constants.tsx`, `index.html`, `index.tsx`, `types.ts`, `vite.config.ts`, `tsconfig.json`, `package.json`, `metadata.json`, `.env.local`, `README.md`; components: `BrainView`, `Dashboard`, `HistoryView`, `NewsView`, `SettingsView`, `Sidebar`, `TradingFloor`, `ui/Card`. `dist/` built bundle present (618 KB JS). Deps: recharts, redux, redux-thunk, reselect, rollup, vite, typescript.

## 12.4 `visualizer/` — the Knowledge Graph Studio ✅ built, ⚠ unreferenced **[CODE]**

`visualizer/llm_trader_knowledge_studio.html` — **509,979 bytes**, single-file, zero external network dependencies. Built by `scripts/build_standalone_visualizer.py:302` from `visualizer/data/knowledge_graph_payload.json` (384 KB) + `src/{canvas_engine.js 42 KB, deep_dive_drawer.js 40 KB, hud_controls.js 31 KB, knowledge_matrix.js 24 KB, lifecycle_tour.js 24 KB, styles.css 54 KB}`. **0 code references it**; the builder script is itself unreferenced.

**Its 12-domain taxonomy (the cleanest subsystem map in the repo):**
D01 Market Data & Macro Dynamics `#06b6d4` · D02 Regime & Microstructure `#ec4899` · D03 Alpha Mining & Factor Discovery `#6366f1` · D04 Autonomous Multi-Agent LLM Hive `#a855f7` · D05 Causal Inference & Structural Discovery `#8b5cf6` · D06 Overfit Gate & Statistical Validation `#3b82f6` · D07 Mathematical Risk & Capital Allocation `#f59e0b` · D08 Continual Learning & EWC Meta-Loop `#14b8a6` · D09 Core Execution & MT5 Broker Gateway `#10b981` · D10 Guardian Sentinel & Circuit Breakers `#ef4444` · D11 Memory Mirror & Event Persistence `#d946ef` · D12 Mission Control & Observability `#64748b`

**5 layouts:** Subsystem Islands · Trading Pipeline Flow · Concentric Orbit/Radar · Dependency Lineage Tree · Force Physics. **7-stage lifecycle tour:** Market Ingestion → Macro & Regime Filter → Alpha Discovery & Factor Scoring → Multi-Agent LLM Deliberation → Quantitative Risk & Overfit Gating → Dual-Velocity MT5 Order Routing → Continual Memory & EWC Regularization. Camera zoom clamp 0.15×–4.0×, spring-lerp 0.12, 60 FPS ceiling to 1,500 entities.

## 12.5 The 20 UX design laws (`ui rules.md`)

Hick · Fitts (×2) · Jakob · Proximity · Miller · **Doherty Threshold (400 ms — the only number in the file)** · Von Restorff · Serial Position · Peak-End · Zeigarnik · Prägnanz · **Sensible Defaults** · **Postel's Law (×2: prevent errors, make errors recoverable)** · Similarity · Uniform Connectedness · Parkinson · Tesler · Goal-Gradient.

**8-step implementation requirement:** identify the primary goal · design the shortest clear path · make the next action visually obvious · remove distractions · immediate feedback after every interaction · prevent errors before they occur · preserve user work on error · confirm completion.

**Conflict-resolution rule (verbatim):** *"When laws appear to conflict, prioritize clarity, accessibility, user control, and successful task completion. Do not apply these laws mechanically."*

> **⚠ This file contains ZERO color values, ZERO pixel dimensions, ZERO named components.** It is purely a heuristic checklist and should not be cited as a design spec. The actual design tokens live in `visualizer/src/styles.css` and `tui_opentui/src/theme.ts`.

## 12.6 Terminal colour coding (`README_TERMINAL.md`)

HIGH/CRITICAL impact 🔴 Red · MODERATE 🟡 Yellow · LOW 🟢 Green · headers/info 🔵 Blue. P&L: green = profit, red = loss.

---

# PART 13 — IMPLEMENTATION GAP MATRIX

## 13.1 🔴 P0 — Blocking / correctness defects

| # | Defect | Location | Impact | Fix |
|---:|---|---|---|---|
| **1** | `validate_pre_trade_safety(symbol)` called with 1 of 5 args; reads `.get("allowed")` instead of `is_approved` | `core/order_execution.py:704` vs `core/execution_safety_supervisor.py:114-125` | **Every live pending order raises `RuntimeError`. Live execution is non-functional. Undocumented anywhere in `docs/`.** | Pass all 5 args; read `is_approved` |
| **2** | `place_market_order` guard makes `:304-395` unreachable — the only live `order_send` with a distributed lock and non-DONE raise | `core/order_execution.py:290-294` | The live market/emergency path has no lock and no retcode guard | Reorder the guard so the live branch is reachable |
| **3** | Distributed lock `order:<symbol>` exists only in that dead block; `place_pending_order` and `place_urgency_routed_order` have **no lock** | `core/order_execution.py:313-315` vs `:640-826`, `:398-560` | The parallel strategy pool is **not** serialized at submit time | Move the lock into both live submit functions |
| **4** | `calc_position_size` called without `market=`/`atr=`/`atr_target=` | `llm_orchestrator.py:7357-7364` | **9 of 19 ladder factors dead:** ATR vol-target, Kronos, walk-forward, WFE, overfit, data-drift, CVaR95, meta-health, alpha-risk profile | Pass the assembled `market` dict |
| **5** | `market` dict never sets `walk_forward_status`, `walk_forward_wfe`, `overfit_risk_level`, `data_drift_status` | `llm_orchestrator.py:2614-2652` vs `core/trading_safety.py:1068-1115` | **4 of the 23 validator rules are permanently unreachable** while appearing to be live gates. `min_walk_forward_efficiency`=0.0 and `block_on_data_drift`=False are never overridden | Populate the keys; tighten the defaults |
| **6** | Caller hardcodes `"completed_candle": True, "breakout_event": False` | `llm_orchestrator.py:2618` | The completed-candle trigger rule (`trading_safety.py:980`) is **unconditionally satisfied** — a rule that can never fire | Wire the real trigger state |
| **7** | `arbiter.permit()` called without `symbol=`/`side=` | `llm_orchestrator.py:7326-7329` | Net-USD-beta (0.025) and single-currency (0.03) limits **never evaluated**. One bucket passed instead of the side-specific set | Pass `symbol=`, `side=`, and the full bucket set |
| **8** | `macro_graph.py` demotes approved edges to CANDIDATE with **zero re-validation logic** (0 grep hits) | `intelligence/macro_graph.py:307`; sole caller `llm_orchestrator.py:3301` | **KG decays monotonically.** Approved macro context shrinks forever. Highest-impact silent degradation in the system | Add a scheduled re-validation sweep over CANDIDATE edges |

## 13.2 ⚠ P1 — Fail-open where fail-closed is implied (violates Doctrine 3)

| # | Location | Behaviour |
|---:|---|---|
| 9 | `llm_orchestrator.py:5897-5898` | `DataQualityGate` exception → `log.debug("… (fail-open)")` |
| 10 | `llm_orchestrator.py:6001-6002` | `NewsTradingGate` exception → fail-open |
| 11 | `mt5_trader.py:4045-4046, 4059-4060, 4068-4069, 4095-4096` | Supervisor boot, task-queue migration, compliance audit, crash recovery — all warn-only |
| 12 | `mt5_trader.py:4142-4143` | `AutonomousBootstrap` — *any* exception becomes "Autonomous bootstrap skipped" and the system proceeds to trade |
| 13 | `llm_orchestrator.py:2825-2831, 7363-7365` | `apply_portfolio_allocation_cap` fails open by design |
| 14 | `mt5_trader.py:3905-3922` | Live sandbox `ORDER_WRITE` gate only runs when `harness_ctx is not None and has("sandbox")` — the inline-fallback path never evaluates it |
| 15 | `mt5_trader.py:4682-4683` | `llm_outage_minutes: 0.0` and `data_stale_seconds: 0.0` hardcoded at the only `evaluate_circuits` call site ⇒ **2 of 5 circuits can never trip** |

## 13.3 ⚠ P2 — Silent capability loss: 11 modules imported but never created

All swallowed by `try/except ImportError` — **except one**:

| Missing module | Import sites | Note |
|---|---:|---|
| `core.live_health_monitor` | `llm_orchestrator.py:224`, `core/trading_safety.py:29` | 🔴 **the 1,224-line deterministic validator has NO live-health monitor** |
| `core.news_execution_engine` | `llm_orchestrator.py:214` | silent `None` |
| `core.liquidity_vacuum_detector` | `llm_orchestrator.py:229`, `data/market_data_provider.py:72` | silent `None` |
| `core.data_drift_detector` | `core/auto_quant_tools.py:1764` | silent `None` |
| `core.meta_threshold_store` | `core/auto_quant_tools.py:2096, 2148` | silent `None` |
| `core.strategy_trial_ledger` | `core/cpcv_backtester.py:97`, `operations/readiness_report.py:65` | silent `None` — the Trial Ledger partially degraded |
| `core.trading_store` | `core/trading_tools.py:293` | silent `None` |
| `core.plugins.registry_factory` | `core/harness/pipeline_v2.py:602` | silent `None` |
| `core.journal_audit` | `core/skill_registry.py:150` | wrong path — real is `operations/journal_audit.py` |
| `research.gate_chaining_orchestrator` | `research_agent.py:153`, `operations/strategy_evaluation.py:44` | silent `None` |
| **`research.param_stability_sweep`** | **`research/alpha_backtest_harness.py:1424`** | 🔴 **NOT guarded — hard `ImportError`** if that path runs (`:1074` is guarded) |
| `research.feature_schema` | `research/supervised_training.py:29` | wrong path — real is `intelligence/feature_schema.py` |
| `operations.lifecycle` | `ui/gui_api.py:1680` | wrong path — real is `operations/strategy_lifecycle.py` |
| `research.decision_models.laya_inference` | `ui/gui_api.py:1183` | silent `None` |
| `intelligence.profitability_foundation` / `.research_sprint_planner` / `.strategy_accuracy` | `operations/learning_loop.py:32,103,135` | real ones are in `research/` |

## 13.4 🔴 Absent modules the docs name (21)

`core/alpha_fusion*` (no file matching `*alpha_fusion*` anywhere in the tree) · `core/trading_workflows` (real: `core/harness/workflows.py`) · `core/pairs_trader` (real: `research/alpha_miner/pairs_trader.py`, dead) · `core/macro_graph` (real: `intelligence/macro_graph.py`) · `core/pnl_optimizer` · `core/drawdown_management` · `core/causal_inference` · `core/causal_impulse_propagation` · `intelligence/llm_finance` · `data/macro_spread_ratios` (real: `intelligence/global_macro_ratios_and_synthetic_spreads.py`) · `operations/shadow_runner` · `core/harness/pipeline_def.py` · `gui/gui_api.py` (real: `ui/gui_api.py`) · `launch_trading.py` · `operations/trading_watchdog` (real: `ops/watchdog/`, dead) · `research/walk_forward_validator` (real: `core/`) · `core/synthetic_path_simulator` (real: `research/`) · `core/grpo_proposer` (real: `research/`) · `core/macro_alpha_signals_and_microstructure` (real: `intelligence/`) · `operations/research_validation` (real: `research/`) · `docs/quant_knowledge_base/01_Regime_Detection/regime_modules.py` (438 ln, uses `pd.Series` at `:286,298-303` with **no `import pandas`** ⇒ `NameError`; 0 references from any code).

## 13.5 ⚠ P2 — 43 strategies registered, 0 implementations

`strategy_registry.json` (20,341 B) registers **43 strategies**: `CANDIDATE` 30, `FULL` 10, `EVALUATION` 3. **Zero entries carry a `module`/`module_path`/`file`/`path`/`entrypoint` key** — all are pure metric records (`id, name, stage, gate_progress, trades, win_rate, profit_factor, sharpe, max_dd, expectancy_r, recovery_factor, notes, promotion_pending, last_eval_ts`). `strategies/loader.py:78-83` skips every file present ⇒ `discover()` returns `{}`. **No strategy can ever load.**

`strategies/base.py` lifecycle hooks `on_load`/`on_activate`/`on_deactivate`/`on_unload` (`:160,167,190,193`) are intentional docstring-only hooks — the only "strategy" in the repo is `example_strategy_template.py`.

## 13.6 ⚠ P2 — Module-scale mismatches (symbol exists, substance does not)

| Module | Lines | Reality |
|---|---:|---|
| `core/harness/stage_contracts.py` | **43** | `StageSpec`/`StageResult`/`RunnerContext`/`CycleAbort` all present, but 43 lines cannot hold the documented contract surface. `CycleAbort` has an **empty class body** at `:13` |
| `core/harness/sandbox.py` | **88** | `CapabilitySandbox` + `PermissionLevel` present, but 88 lines cannot implement the documented L10–L50 permission ladder |
| `core/mathematical_pillars.py` | **13** | A registry facade, not a pillar implementation |
| `core/cointegration_robustness.py` | **35** | Thin |
| `operations/agent_scheduler.py` | **38** | Thin shim |
| `intelligence/local_llm_memory.py` | **90** | Thin |
| `operations/agents/__init__.py` | **0** | Empty |

## 13.7 ⚠ P3 — Duplicate / forked trees

| Fork | Detail |
|---|---|
| `quant_knowledge_framework/` | **1,117 files**, 41 .py / 2,459 ln, **0 importers** — a stale duplicate of `trading_knowledge_base/`. Requires uninstalled `hmmlearn, faster_whisper, yt_dlp, laya, transformers` |
| `trading_knowledge_base/modules/*.py` | 14 files of 22–41 lines, **all 0 importers**. `context_provider.py` (295 ln, wired from `llm_orch:1050`) reads them as **text**, not imports |
| **3 strategy registries** | `strategy_registry.json` (43 metric records), `core/skill_registry.py` (821 ln, live), `operations/strategy_lifecycle.py` (340 ln, live) |
| **3 memory systems** | `trading_memory.py` (1,630 ln, live), `local_agent_memory.py` (936 ln, live), `local_llm_memory.py` (90 ln, live) — plus 4 separate SQLite DBs |
| **2 dashboards + a visualizer** | root `alpha_dashboard.html` (77 KB) + `compliance_dashboard.html` (28 KB) + `gui/dist/` + `visualizer/llm_trader_knowledge_studio.html` (498 KB, **0 references**) |
| **Duplicate function** | `resolve_strategy_cycles` defined at `mt5_trader.py:1216-1230` **and** dead at `llm_orch:7684-7698` |

## 13.8 The "wiring gap" — how much of the math stack actually gates an order

`QUANT_MATH_AND_EDGE_AUDIT.md` claims *"the production loop consumes only ~20 % of this stack."* Measured against the 5 production files (`mt5_trader.py`, `llm_orchestrator.py`, `trading_safety.py`, `risk_manager.py`, `trade_manager.py`):

| Symbol | Doc claim | **Actual** | Evidence |
|---|---|---|---|
| `heat_budget` | NOT-IN-MAIN-LOOP | ✅ **GATE** | `risk_manager.py:73,766-773,859,888-893`; `llm_orch:2594-2606` |
| `bocpd` | NOT-IN-MAIN-LOOP | ✅ **GATE** | `llm_orch:3852,2586-2590,2649` |
| `changepoint` | NOT-IN-MAIN-LOOP | ✅ **GATE** | `trading_safety.py:1050-1064` |
| `kelly` | "prompt-text only" | ✅ **SIZING** | `risk_manager.py:478-498,924` |
| `hurst` | NOT-IN-MAIN-LOOP | ✅ **EVIDENCE** | `llm_orch:3987,3991` |
| `clayton_copula` / `tail_dependence` | NOT-IN-MAIN-LOOP | ✅ **GATE (indirect)** | `market_data_provider.py:1438,1457,1463` → `llm_orch:2596-2606` |
| `slippage` | USED ✓ | ✅ **LIVE** | `llm_orch:3821,4517,4921` |
| `walk_forward` | NOT-IN-MAIN-LOOP | ⚠ **COMPUTED, GATE DEAD** | computed `llm_orch:4232,4489`; read from a `market` key never set |
| `cpcv` / `deflated_sharpe` | NOT-IN-MAIN-LOOP | ⚠ **EVIDENCE ONLY** | `llm_orch:4366`; `mt5_trader:4159-4165` |
| `kalman` | NOT-IN-MAIN-LOOP | ⚠ **EVIDENCE ONLY** | `llm_orch:4352`; rest is prompt text |
| `meta_label` | NOT-IN-MAIN-LOOP | ⚠ **ADVISORY ONLY** | `llm_orch:1369` — never reaches sizing |
| `risk_of_ruin` | NOT-IN-MAIN-LOOP | ⚠ **PROMPT-LEVEL ONLY** | `llm_orch:1327` |
| **`information_coefficient`** | NOT-IN-MAIN-LOOP | ❌ **NOT REFERENCED** | 0 hits in all 5 prod files |
| **`sqn`** | NOT-IN-MAIN-LOOP | ❌ **NOT REFERENCED** | 0 hits |
| **`volatility_parity`** | NOT-IN-MAIN-LOOP | ❌ **NOT REFERENCED** | 0 hits (the ATR overlay at `risk_manager:994-999` is a different, opt-in thing) |
| **`master_expectancy`** | NOT-IN-MAIN-LOOP | ❌ **NOT IN 5 PROD FILES** | only `graph_nodes.py:324` (LangGraph path) |
| **`fractional_diff`** | NOT-IN-MAIN-LOOP | ❌ **NOT BY THE CYCLE** | only `auto_quant_tools.py:46`, `live_advisory_hub.py:256` |

**Verdict:** the doc is **right in spirit** — **5 of 19 (26 %) deterministically gate or size an order**, which validates the ~20 % figure for *enforcement*. But the table is **6 rows stale** (heat_budget, bocpd, changepoint, hurst, kelly, walk_forward are all demonstrably in the loop now) and it **understates** the damage elsewhere: five symbols are absent from the production files entirely, not merely behind a tool layer.

**The design guarantee that does hold:** the two constitutions are now unified. The net-EV floor (`min_net_ev_r=0.0`, `min_ev_samples=30`, `default_friction_r=0.05`, `trading_safety.py:879-880,893-895,1036-1049`) with `_estimate_friction_r` (`llm_orchestrator.py:3816`, fed at `:2648`) is implemented in the **main loop** — closing the P0 finding that the LangGraph path enforced EV ≥ +0.20R while the main loop enforced only min_rr ≥ 2.0. *"A 2:1 RR coin-flip with 40 % win rate passes even at breakeven-after-costs"* is no longer true.

## 13.9 Documentation drift register

| Doc | Claim | Reality |
|---|---|---|
| `SYSTEM_SOURCE_OF_TRUTH_PRD.html` §01.2 | 442 source files / 152,883 ln | 557 / 190,501 (understates 1.26×) |
| PRD §01.2 | `mt5_trader.py` ~7,000; `llm_orchestrator.py` ~10,000 | 5,395 / 7,700 (overstates 1.30×) |
| PRD §03 boot table | 16 steps | Steps **6–8 missing** from the table |
| PRD §04.2 stage 16 | "Execution shield / decision gate" | **No such module exists** |
| PRD §04.2 stage 26 | `CYCLE_COMPLETE` inside `execute_cycle` | Emitted by `mt5_trader.py:4468` |
| PRD §05.3 | 5 lifecycle states | **10 states** (`trading_safety.py:382-390`) |
| PRD §03.3 / §05.4 | magics in `mt5_trader.py`; holiday gate from `mt5_calendar_enhanced.py` | `core/trading_safety.py:34-35`; real gate is a **6-line hardcode** in `data/api_manager.py:660-673` |
| `SOURCE_OF_TRUTH.md` | "212 files, ~83K LOC, last verified 2026-08-28" | 913 files / 220,576 LOC |
| `SOURCE_OF_TRUTH.md` §3.8 | lists `skill_registry.py` twice; `" robustness_playbook.py"` with a leading space; `mathematical_pillars.py` as an implementation | `mathematical_pillars.py` is **13 lines** |
| `CLEAN_INTERFACE_GUIDE.md` | "production-ready", "Exit code: 0", "managing +$1,517 profit" | Contradicts the TODO snapshot where every area is `[~]`/`[!]`. **Marketing overstatement** |
| `LOOP_AND_TRADING_PROCESS_AUDIT.md` | L-7, L-8, L-9, L-10, L-13, L-20 OPEN | **All 6 FIXED** (`trade_manager.py:168`; `risk_manager.py:703-716`; `mt5_trader.py:3661-3697`; `llm_orch:5951`; `llm_orch:7015-7027`; `llm_orch:7145`) |
| `LOOP_AND_TRADING_PROCESS_AUDIT.md` L-11 | unbounded growth | `_rl_updated_tickets` pruned `:3779`; `rl_state_history` pruned `:7567`; `perf.history` trimmed on the fill path — ⚠ 2 append sites still unbounded |
| `LOOP_AND_TRADING_PROCESS_AUDIT.md` L-12 | dual persistence | **STILL OPEN** — `PerformanceMonitor("trading_rewards.json")` at `mt5_trader:3699` still coexists with `trading_state.db`; sync hack at `llm_orch:4549` |
| `LOOP_AND_TRADING_PROCESS_AUDIT.md` L-14 | duplicate per-pulse work | ingest hoisted ✓, ThreadPoolExecutor ✓ — ⚠ per-strategy `fetch_data` still doubled |
| `LOOP_AND_TRADING_PROCESS_AUDIT.md` L-16..L-19 | run_status ambiguity, no TTL, rl_feedback PENDING, silent mock_llm | **All 4 STILL OPEN** |
| `MEMORY_AND_PIPELINE_AUDIT.md` M-4 | "after **8** attempts the row parks as FAILED" | 🔴 **No constant 8 exists.** Real: `max_attempts INTEGER DEFAULT 3` (`agent_operations.py:122,226`), `:788`=3, `:1141,1168`=5. Bounded retry is real; the number is fabricated |
| `MEMORY_AND_PIPELINE_AUDIT.md` M-7 | prune wired into `monthly_memory_maintenance` | 🔴 **FALSE** — both functions exist (`trading_memory.py:1145`, `graph_checkpointer.py:274`) but `monthly_memory_maintenance` (`:680`) references **neither** |
| `MEMORY_AND_PIPELINE_AUDIT.md` M-13 | `causal_paths` always `[]`, `cesi` hardcoded | ⚠ **HALF FIXED** — `find_causal_paths` now called (`:261`); `cesi_score = 0.85/1.0` still hardcoded (`:281`) |
| `MEMORY_AND_PIPELINE_AUDIT.md` M-1..M-6, M-17 | 7 claims | ✅ **ALL 6 verified correct** (M-1 `:1135` routes through broker; M-2 `:89,109-113,144-146`; M-3 WAL + busy_timeout; M-5 `:1124,1140`; M-6 `:97`; M-17 `:5951`) |
| `QUANT_MATH_AND_EDGE_AUDIT.md` §3 | heat/bocpd/changepoint/hurst/kelly "NOT-IN-MAIN-LOOP" | **STALE** — all 5 are in the loop |
| `QUANT_MATH_AND_EDGE_AUDIT.md` Q-12 | "copula heat compression never binds live" | 🔴 **STALE** — wired `market_data_provider.py:1457,1460` → `llm_orchestrator.py:2594-2610` |
| `QUANT_MATH_AND_EDGE_AUDIT.md` Q-17 | metrics engine "live wiring pending" | ✅ **CONFIRMED** — `QuantitativeMetricsEngine(` appears only in `core/quantitative_metrics_service.py:44` + 2 tests |
| `QUANT_MATH_AND_EDGE_AUDIT.md` P1 | "persist planned 1R$ at entry" | ✅ **CONFIRMED PENDING** — `record_trade(risk_amount=)` exists (`metrics.py:855-856`) but no production caller passes it |
| `QUANT_MATH_AND_EDGE_AUDIT.md` Q-3 | DSR E[maxSR] constant | ✅ CONFIRMED — `metrics.py:195-199` uses the non-canonical form; doc is right |
| `QUANT_MATH_AND_EDGE_AUDIT.md` §2 | 19 formulas verified correct | ✅ 19 of 19 verified; the two applied fixes (`cagr` −100 % on ruin, Sharpe excess-return denominator) are real |
| `audit/TURN_0_BASELINE.md` | "99.7 % of 2,993 tests" | 3,686 collected (+23 %); one file fails to import |
| `TRADING_SYSTEM_MASTER_TODO.md` / `REMAINING_TASKS.md` | ~250 `[~]`/`[ ]` markers combined | **7 of 21 spot-checked `[~]` markers are stale** — dual-velocity pipeline, task leases, provider circuit breaker, durable schedules, L3 core memory, readiness report, correlation bucket caps: all DONE. **The `[~]` marker is unreliable** |
| `ALPHA_MINING_INTEGRATION_PLAN.md` | `Status: PLANNING`, `pass` bodies | 🔴 **STALE** — enrichment, schedule, handler, and pipeline instantiation all exist |
| `system_workflow_audit/05_STEP_BY_STEP_EXECUTION_PLAN.md` | 5 tasks unchecked | **4/5 substantively done**, boxes never ticked. Task 2 was solved by an *alternate* design (cache-read + honest `ENGINE_OFFLINE`) rather than the plan's `MT5Client` injection |
| `system_workflow_audit` A-5/A-13 | `/api/backtests` reads only `_aq.get("backtests", [])` | ⚠ **STALE** — still at `:1881` but now the **4th of 4** sources |
| `system_workflow_audit` A-7 | `economic_dynamics_catalog` import broken | ⚠ **HALF** — `import intelligence.economic_dynamics_catalog` **OK**; bare `import economic_dynamics_catalog` still fails |
| `system_workflow_audit` A-8 | `/api/tasks`, `/api/model-registry` empty | 🔴 **STALE** — both return real data |
| `API_REFERENCE.md` | `DeterministicValidator` has "22 checks" | Table lists **23 rows** — off-by-one in the doc |
| `API_DOCUMENTATION.md` vs `USAGE_EXAMPLES.md` | async method naming | `async_get_countries`/`async_get_calendar_dataframe` vs `get_countries_async`/`get_calendar_dataframe_async` — **broken example code** |
| `USAGE_EXAMPLES.md` | accesses `v.event_name`, `v.country_code`, `v.importance` on `CalendarValue` | `CalendarValue` has **no such fields** — broken example code |
| `API_DOCUMENTATION.md` | `CalendarEventSector`, `CalendarEventImpact`, `CalendarEventFrequency` | Referenced but **never defined** |
| `API_DOCUMENTATION.md` | calendar error codes 1–132 | These are the **POSIX errno table**, not MQL5 error codes — a documented mis-mapping |
| `API_REFERENCE.md` | `TradingStore.__init__` `mode` options | Docstring says `"DEMO","LIVE","PAPER"`; param table says `"DEMO","LIVE", None` |
| `UAQP_STATE_TENSOR.md` | kill-switch trigger `δ_α > 0.6` | `δ_α` is **never defined**; `τ_decay_critical = 0.6` is a different symbol (IC-correlation → RETRAIN) |
| `TRADING_MEMORY_SPEC.md` | written in normative "should" voice | It is a **spec, not a status report**; it defers status to `MASTER_TODO` |
| `macro_causal_kg_schema.md` | ontology uses `reacts` | **No such edge type**; the DAG diagram is an unrendered LLM artifact |
| `PERSISTENCE_EXPLAINED.md` | sample timestamps `2025-11-25` | Predates the current system by ~10 months; no safety/layering content |

## 13.10 Dead-but-real code (highest-value cleanup targets)

`core/robustness_playbook.py` (46) · `core/econometric_features.py` (235) · `core/two_speed_reasoning.py` (81) · `core/prfs_counterfactual.py` (202) · `core/harness/live_trading_plugin.py` (86) · `intelligence/graph_engine_facade.py` (245) · `intelligence/universal_decision_engine.py` (632) · `operations/delegation.py` (75) · `ops/watchdog/trading_watchdog.py` (128) · `research/alpha_backtest_harness.py` (1,436) · `research/ensemble_backtest.py` (267) · `research/rl_analytics.py` (309) · `research/mt5_finrl_env.py` (240) · `research/qubo_rebalancer.py` (294) · `research/ooqi_spec_compiler.py` (290) · `research/seer_symbolic_recovery.py` (177) · `research/replay_buffer.py` (180) · `research/curriculum_scheduler.py` (207) · `research/d9_escalation_latch_risk.py` (204) · `research/laya_checkpoint_parity.py` (247) · `research/alpha_ensemble_stability.py` (443) · `research/alpha_stability_quick.py` (201) · `research/training_orchestrator_cli.py` (327) · 20 of 31 `research/alpha_miner/` modules · `visualizer/llm_trader_knowledge_studio.html` (498 KB).

**Stub inventory (whole tree, excl. tests/docs):** only **15 non-abstract stub symbols in 12 modules** — this codebase is unusually clean. The only real stubs are `operations/prometheus_exporter.py:266` (`log_message` never overridden) and `ui/web_dashboard_adapter.py:254` (`def run_app(): pass`). Everything else is a benign `__init__ = pass`, an ABC hook, or an exception class with an empty body.

## 13.11 Data hygiene

`trading_state.db` is **1.33 GB** · `guardian_audit.jsonl` 10.8 MB · `trader.log` 10.0 MB · `mt5_trader.log` 3.0 MB · `graphify-out/graph.json` **28.9 MB committed to the tree**. **⚠ `accounts.json` is present in the repo root and `.env` is present** — direct violation of `SECURITY.md` rule 1 (*"Credentials live only in an untracked `.env`"*).

---

# PART 14 — OPERATIONS, SECURITY & RUNBOOKS

## 14.1 Security rules (`SECURITY.md`, complete — 4 rules)

1. Credentials live **only** in an untracked `.env`, injected by the operator.
2. **Never** put MT5 passwords, API keys, or account numbers in source code, examples, or commits.
3. **Rotate** any credential that was previously committed or shared.
4. `trading_state.db` must **remain local**; before operating across machines, use an **encrypted, access-controlled** database.

**Embedded controls:** `operations/security_audit.py` (291 ln, credential scanning + secret sanitizer) · `operations/secrets_guard.py` (`accounts.json`/`config.ini` scan, warn-only `secrets_hygiene_warnonly`) · `core/secure_store.py` (184 ln) · `operations/journal_backup.py` (AES-256-GCM) · `operations/prometheus_exporter.py` + `alert_delivery.py` (redaction).

**Precedence clause (`INCIDENT_RUNBOOKS.md`, verbatim):** *"These procedures are operational controls, not trading advice. The deterministic permission gate and persistent kill switch take priority over recovery speed."*

## 14.2 Universal incident first response (5 steps, verbatim)

1. Enable the persistent kill switch: `python mt5_trader.py kill-switch enable --reason "incident"`.
2. Confirm no new order intent is being submitted; **do not delete journal rows**.
3. Capture `journal integrity`, `journal alerts`, `journal execution-reviews`, `journal legacy-status`, and the relevant log/trace IDs.
4. Reconcile MT5 positions/orders before restarting or changing configuration.
5. (Escalate per the named runbook section.)

**"Never auto-clear the switch." "Require a written operator reset with evidence."**

## 14.3 Restart procedure (`HOW_TO_RESTART.md`)

1. `Ctrl+C` in the trading terminal (graceful stop; saves performance metrics).
2. `python mt5_trader.py`
3. Verify: no JSON parsing errors · open-positions table renders at the start of each cycle · clean LLM decisions.

## 14.4 Logging (`LOGGING_GUIDE.md`)

Standard `logging` with a **40-named logger hierarchy** rooted at `mt5_trader` (sub-trees: `core.*` 23, `data.*` 7, `intelligence.*` 6, `research.*` 3, `operations.*` 4, `ui.*` 3). Format `%(asctime)s - %(name)s - %(levelname)s - %(message)s`.

**Sinks:** console (stdout) + `mt5_trader.log` (**mode `w` — overwritten each run**) + `TUILogHandler` when the TUI is active.

**⚠ Rotation is NOT enabled by default.** The documented fix is `RotatingFileHandler("mt5_trader.log", mode="a", maxBytes=10*1024*1024, backupCount=5)` — must be added manually.

**`--debug` enables 14 trading-critical modules only** (noise reduction): `intelligence.llm_orchestrator, core.crash_detector, core.walk_forward_validator, core.optimal_execution, core.order_execution, core.trade_manager, core.risk_manager, core.ewc_forgetting_prevention, core.auto_quant_tools, core.causal_feature_filter, core.alpha_ensemble, core.meta_adaptive_engine, core.trading_tools, data.market_data_provider, research.rl_trainer`. UI modules and analytics stay at INFO.

**6 canonical debug scenarios** with exact grep commands: why HOLD instead of trading (`grep -i "gate\|hold\|reject\|block"`), data fetch failure, LLM call failure, unexpected close (`grep -i "flatten\|close\|exit\|drawdown\|risk"`), walk-forward decay detection, EWC activation.

**TUI conflict:** when the TUI is active, logs go through `TUILogHandler`; force console with `--no-tui`.

## 14.5 Build pipeline

```
# Windows
build.bat            # or: python scripts/build.py
# → ponytail_audit → rtk bun run typecheck → rtk bun run selftest → rtk graphify update .

# OpenTUI
cd tui_opentui && bun run build:rtk
```
Per `AGENTS.md`: always prefix verbose terminal/test commands with `rtk`; run `graphify update .` after modifying code.

**`scripts/` (6 files, 0 Python import edges — all entrypoint-invoked):** `build.py` (51) · `build_prd_doc.py` (140, generates the PRD from the 27 fragments) · `audit_prd_doc.py` (45) · `ponytail_audit.py` (58) · `generate_knowledge_graph_data.py` (**3,065 — the largest script**) · `build_standalone_visualizer.py` (302).

## 14.6 Installation (`INSTALLATION.md`) — ⚠ SCOPE WARNING

This document specifies the **MT5 Economic Calendar Wrapper**, not the LLM Trader runtime. It is the only doc with hard hardware/OS specs: Windows 10/11 64-bit (MT5 is Windows-only), 4 GB min / 8 GB+ recommended RAM, 2 GB storage, Python 3.8+ 64-bit, MT5 at `C:\Program Files\MetaTrader 5`. Deps `pandas numpy` (+ optional `pytest pytest-cov pytest-asyncio matplotlib seaborn jupyter requests beautifulsoup4`). Env `MT5_TERMINAL_PATH, MT5_TIMEOUT_SECONDS=30, LOG_LEVEL, LOG_FILE, MAX_WORKERS=4, CACHE_SIZE=1000`. `config.ini` `[MT5] [Performance] [Logging]`. Four named failure modes with exact error strings. Path validation requires the file to end in `terminal64.dll` and start with the `MZ` header. `SecureConfig` uses Fernet encryption with `config.key` / `secure_config.enc`.

**No ports are documented anywhere in any ops/UI doc.** Web surfaces named: OTLP/Prometheus telemetry, `prometheus_exporter.py`, `gui_api.py` (observed on `:8000` via `START_DECK.bat`).

---

# PART 15 — THE COMPLETE DOC INVENTORY (all 72 entries classified)

| Entry | Class | Authority | Status |
|---|---|---|---|
| `SYSTEM_SOURCE_OF_TRUTH_PRD.html` | **PRD** | code-anchored, 27 sections | ✅ primary spec; 7 drift items (§13.9) |
| `_prd_build/fragments/sec-01…27.html` | PRD source | authoritative sections | ✅ all 27 byte-identical to the assembled output |
| `_prd_build/shell_head.html`, `shell_foot.html` | PRD shell | — | ✅ |
| `SOURCE_OF_TRUTH.md` | Architecture | claims single authority | ⚠ **superseded by this doc**; 3,875 stale LOC figure |
| `ARCHITECTURE.md` | Architecture | companion | ⚠ companion to a stale SOT |
| `ARCHITECTURE_COMPLETENESS.md` | Runbook | live-readiness doctrine, 9 pillars, 8-step live track | ✅ *"Code never fakes live readiness clear."* |
| `AUTONOMOUS_TRADING_SYSTEM_MASTER_ARCHITECTURE.md` | Spec | 18 sections, most forward-looking | ⚠ conflicts with SOT on pipeline stage count (3 vs 8 vs 11) |
| `AUTONOMY_MASTER_PLAN.md` | Plan | P3–P8 with ✅/🔨/🧭 | ✅ delegates status to `MASTER_TODO` |
| `CONTINUOUS_TRADING_AGENT_DESIGN.md` | Design | control flow, persistent state, attention schedule, permissions matrix | ✅ |
| `ECONOMIC_DYNAMICS_SOURCE_OF_TRUTH.md` | SoT | 134-dynamics catalog v1.0.0, T0–T4 layers | ✅ *"The catalog is executable truth; the dump is not."* |
| `QUANT_RESEARCH_AND_SYSTEM_FOUNDATIONS.md` | Reference | math foundations, risk tiers, risk parity, edge lifecycle | ✅ KNOWLEDGE |
| `PERSISTENCE_EXPLAINED.md` | Explainer | restart behaviour, `rl_model.json` schema | ⚠ samples dated 2025-11-25 (10 months stale) |
| `PAPER_PROJECT_MAPPING.md` | Analysis | 35+ papers → project state, 12-gap roadmap | ✅ self-disclaims: *"verify any specific item against the current tree"* |
| `API_REFERENCE.md` | Contract | alpha_factors, alpha_ensemble, trading_safety, UAQP agents | ✅ 3 defects (§13.9) |
| `API_DOCUMENTATION.md` | Contract | `MT5CalendarWrapper` / `EnhancedMT5CalendarWrapper` | ⚠ 3 undefined enums; errno mis-mapping |
| `USAGE_EXAMPLES.md` | Examples | calendar wrapper | 🔴 **broken** — wrong async names, non-existent fields |
| `TRADING_MEMORY_SPEC.md` | **Normative spec** | 6 layers, L0–L3, ledger, labels, learning loop | ✅ authoritative on memory design; aspirational voice |
| `UAQP_STATE_TENSOR.md` | Contract | Γ=(A,E,S,Φ,Θ,T), 13 agents, 10 state slices, SQL DDL | ✅ ⚠ `δ_α` undefined |
| `macro_causal_kg_schema.md` | Schema | node/edge types, triples | ⚠ `reacts` undefined; DAG unrendered |
| `ALPHA_MINING_INTEGRATION_PLAN.md` | Plan | 11 alpha_miner classes | 🔴 **heavily stale** — all marked PLANNING but implemented |
| `TRADING_SYSTEM_MASTER_TODO.md` | Plan | ~150 checkbox markers | ⚠ **7 of 21 `[~]` markers stale** |
| `TRADING_SYSTEM_REMAINING_TASKS.md` | Plan | ~102 checkbox markers | ⚠ same |
| `TRADING_SYSTEM_ROADMAP.md` | Plan | phases | ✅ |
| `CLEAN_INTERFACE_GUIDE.md` | Guide | wrapper usage | 🔴 **marketing overstatement** ("production-ready", "+$1,517 profit") |
| `QUANT_MATH_AND_EDGE_AUDIT.md` | **Audit** | 14 modules formula-verified, wiring gap | ✅ **the most technically valuable audit**; 4 rows stale, 2 claims now true |
| `LOOP_AND_TRADING_PROCESS_AUDIT.md` | **Audit** | 20 findings, line-anchored | ✅ 6/6 claimed fixes correct; **6 of 8 "OPEN" now fixed** |
| `MEMORY_AND_PIPELINE_AUDIT.md` | **Audit** | 10 fixed + 7 open + data-ingest findings | ✅ 6/7 claimed fixes correct; **2 claims false (M-4 constant, M-7 wiring)** |
| `autonomous_quant_graph_remediation.md` | **Audit** | 12-gap v1 critique, DSR trial math, Guardian | ✅ **the design-defining document**; 11/12 remediated |
| `CONTINUATION_VERIFICATION.md` | Audit | ~250-item change ledger, 2026-08-10 | ✅ *"does not claim broker connectivity, profitability, or live readiness"* |
| `system_workflow_audit/01…05` | Audit | as-is vs to-be, init/steady/backtest workflows, execution plan | ✅ 4/5 plan tasks substantively done |
| `audit/TURN_0_BASELINE.md` | Audit | compile/test/readiness/build verification | ⚠ test count +23 % stale; one file fails to import |
| `SYSTEM_TEST_RESULTS.md` | Report | single live run, exit 0, 1 non-critical error | ⚠ evidence timestamped 2025-11-26 |
| `P0_P1_FIXES_COMPLETE.md` | Report | 6 defects, 18/21 tasks | ✅ historical |
| `BUGFIXES.md` / `CRITICAL_FIXES.md` / `RESTART_REQUIRED.md` | Report | 4 bugs / 4 issues / 3 restart items | ✅ historical, 2026-08-24 |
| `INCIDENT_RUNBOOKS.md` | **Runbook** | universal first response + named scenarios | ✅ *"the deterministic permission gate and persistent kill switch take priority"* |
| `LOGGING_GUIDE.md` | Runbook | loggers, levels, debug recipes | ✅ ⚠ rotation not enabled by default |
| `SECURITY.md` | Policy | 4 credential rules | ✅ 🔴 `accounts.json` + `.env` present in the repo root |
| `INSTALLATION.md` | Runbook | MT5 calendar wrapper install | ⚠ **different deliverable** |
| `LAUNCH_INSTRUCTIONS.md` | Runbook | `START_TRADING.bat` flow | ⚠ references non-existent `launch_trading.py` |
| `HOW_TO_RESTART.md` | Runbook | restart + verify | ✅ |
| `README_TERMINAL.md` | Runbook | `economic_news_terminal.py` CLI, 20-field event object | ✅ a **separate deliverable** |
| `TERMINAL_FIX.md` | Guide | Windows UTF-8 box-drawing rendering | ✅ user-guide only |
| `ui rules.md` | Design | 20 UX laws + 8-step method | ✅ ⚠ **zero colors, zero pixels, zero components** |
| `CLEAN_INTERFACE_GUIDE.md` | Guide | — | 🔴 overstated (above) |
| `analytical_frameworks_and_epistemology/` Vol 01–07 | Knowledge | 7 cross-discipline volumes | ✅ **100 % KNOWLEDGE-ONLY** |
| `…/bma_causal_autonomous_system/` 01–07 + MASTER SSOT | Architecture | BMA, analysis pyramid, macro regimes, econometrics, ML/NLP, backtesting/sizing/survival, meta-agent orchestration, omni cascades | ✅ mixed; see §10.3 |
| `…/retail_quant_master_architecture/` 01–06 | Architecture | mathematical pillars, inefficiencies, **six-layer system**, transaction cost, Kalman stat-arb, robustness playbook | ✅ see §10.1 |
| `…/geopolitical_predictive_and_sanctions_intelligence/` 01–06 | Knowledge | extrapolation math, macro foresight, compendium, **sanctions forensics (1,624 ln)**, case-study matching, macro ratios | ✅ mostly KNOWLEDGE-ONLY |
| `institutional-vs-retail-applicability/` 01–05 | Triage | taxonomy, Pafka–Kondor, applicability matrix, gap analysis, priority roadmap | ✅ **the corpus's self-critique — most actionable section** |
| `portfolio-optimization-series/` 00–08 + 8 PDFs | Knowledge | 8-paper Author-P–Author-K analysis | ✅ **100 % KNOWLEDGE-ONLY, explicitly deferred** |
| `dispersion_bias/` 5 files | Paper | one paper, GO/NO-GO gate | ⚠ mostly ASPIRATIONAL (verified missing) |
| `quant_knowledge_base/` | Scaffolding | 01–05 + 99 READMEs, module interface spec, YouTube pipeline | ⚠ `regime_modules.py` is broken and orphaned |
| `knowledge/extracted_knowledge_base.md` | Knowledge | 43,651 ln | ✅ KNOWLEDGE-ONLY |
| `superpowers/specs/…knowledge-graph-studio-design.md` | **Design** | 12 domains, 8-attribute node schema, 5 layouts, 7-stage tour | ✅ **best subsystem taxonomy in the repo** |
| `superpowers/plans/…knowledge-graph-studio.md` | Plan | 7 tasks | ✅ all 7 shipped |
| `diagrams/*.json` (5) | Diagram | master architecture, tick pipeline v2, fast execution loop, alpha discovery dataflow, strategy lifecycle | ✅ the rendered `.html` files are 14.5k lines each of Mermaid output |
| `*.txt` research corpus (14 files) | Knowledge | domain models, quant types, retail models, news trading, how to find edge, swing/scalap math, quant papers, agentic strategy discovery, model optimization standard | ✅ **KNOWLEDGE-ONLY reading material** |
| `*.pdf` (3) | Source papers | Retail-Price Drivers, ssrn-alpha mining, The Quant Cycle | ✅ reference |
| `radar chart component reference.jpeg` | Asset | UI reference | ✅ |
| `article-extractor-1.0.0.tar.gz` | Tool | article scraper | ✅ |
| `archive_collect_evidence.log`, `audit/_build_*.log`, `audit/_tui_build.log` | Logs | build evidence | ✅ |

---

# PART 16 — THE FIVE ACTIONS THAT MATTER

Ranked by (impact × effort). Every other open item is lower priority.

| # | Action | Fixes | Effort |
|---:|---|---|---|
| **1** | **Pass the assembled `market` dict (plus `atr`/`atr_target`) into `calc_position_size` at `llm_orchestrator.py:7357`, and populate the 4 missing keys at `:2614-2652`** | Gap #4, #5 — revives **9 sizing factors and 4 validator rules** in one change | S |
| **2** | **Fix `core/order_execution.py:704`** — pass all 5 args to `validate_pre_trade_safety` and read `is_approved` | Gap #1 — **restores live execution entirely** | S |
| **3** | **Add a scheduled KG re-validation sweep** over `CANDIDATE` edges after `apply_trade_outcome_feedback` demotes them | Gap #8 — stops monotonic macro-context decay | S |
| **4** | **Pass `symbol=` and `side=` to `arbiter.permit()` at `llm_orchestrator.py:7326`** | Gap #7 — activates net-USD-beta (0.025) and single-currency (0.03) limits | S |
| **5** | **Move the `order:<symbol>` distributed lock into `place_pending_order` and `place_urgency_routed_order`** | Gap #3 — restores the safety property that makes the parallel strategy pool safe | S |

**Then, in order:** create the 11 missing modules (or delete the 18 guarded imports) — the validator's missing live-health monitor is the costly one; convert the 6 fail-open sites to fail-closed; either implement 43 strategies or reduce the registry to what exists; move `accounts.json`/`.env` out of the repo; delete `quant_knowledge_framework/` (1,117 files, 0 importers).

---

---

# PART 17 — PRD SUBSYSTEM REFERENCE §07–§12 (recovered detail)

These sections were lost to output truncation in the first extraction pass and have been recovered from `docs/_prd_build/fragments/sec-07.html` … `sec-12.html`. The assembled PRD is **2,971 lines**; section line ranges are verified.

## 17.1 §07 Data Ingestion & Source Fabric (assembled 1060–1250)

**Four ingestion planes** (verbatim): the **MT5 terminal plane** (ticks, OHLC, DOM, account), the **HTTP/REST provider plane** (NewsAPI, FRED, SEC EDGAR, Yahoo Finance, OpenBB, Seeking Alpha, Portfolio123, EIA, CFTC, Exness, Forex Factory), the **RSS plane**, and the **calendar plane** (broker calendar DLL → wrapper → structured event rows). ⚠ **The RSS plane is promised in the intro but never documented**; §07 also ends with an unfilled `<!--MORE-->` marker and has no evidence-index panel.

**Three indirection helpers** (`data/market_data_provider.py:78-97`) so a running `mt5_trader.py` session injects its own initialised module:
```
_get_mt5()        -> sys.modules["mt5_trader"].mt5   or module-level mt5
_get_fetch_ohlc() -> sys.modules["mt5_trader"].fetch_ohlc (unless it is this very function)
_get_tf_const()   -> sys.modules["mt5_trader"].tf_const   (unless it is this very function)
```

### 🔑 §07.2.2 Broker server-time decoding — the look-ahead trap
MT5 bar epochs are in the *broker server's* timezone (MetaQuotes demo servers run **UTC+3**), not UTC. Naive decoding makes every completed bar appear hours in the future, so the feature-lineage validator rejects every proposal as look-ahead.

`_server_utc_offset_seconds()` (`:127-180`): probes `symbol_info_tick()` on **EURUSD/GBPUSD/XAUUSD**; snaps residual to a **30-minute grid (clamped to ±14 h)**; discards probes with residual **> 300 s** (frozen/stale tick); takes the **modal** offset; requires **2+ agreeing votes** or **a single vote with residual ≤ 120 s**. Cached **24 h** (`_SERVER_OFFSET_TTL = 24*3600`, `:120-124`) so weekend-closed markets keep the last known offset. Returning `0` means "no trustworthy probe" — the code deliberately keeps the naive decode rather than guessing wrong.

### §07.2.3 Timestamp-preserving serialisation
`ohlc_records(df)` (`:246-267`) exists because `DataFrame.to_dict(orient="records")` silently drops the index, which stripped every bar of its `time` and made the validator reject every proposal with *"completed bar timestamp is missing or malformed"*. Resets the index, formats `%Y-%m-%dT%H:%M:%SZ`, renames the column to `time`.

### §07.2.4 Depth of market — `DOMCollector` (`:356-406`)
Wraps `market_book_add` / `market_book_get` / `market_book_release`. Snapshot fields: `captured_at`, `source="mt5_dom"`, `available`, `best_bid`, `best_ask`, `spread`, `bid_vol_total`, `ask_vol_total`, `imbalance`, **`dom_depth = min(len(bids), len(asks))`**, `top_bids[0:5]`, `top_asks[0:5]`. Unsubscribed/empty books return `available: False` with no prices — *"it never fabricates depth."* Subscription is short-lived and polled, not slept: `fetch_data` retries `get_snapshot()` **up to 5 times with 200 ms gaps (1 s ceiling)** (`:1172-1181`).

### §07.3 The HTTP/REST provider plane — `data/api_manager.py`
`APIManager(store=None)` (`:53-145`) is the single façade. Keys accept one key or a **comma-separated rotation pool**.

| Service | Env vars (first match wins) | Pool index | Client |
|---|---|---|---|
| NewsAPI | `NEWSAPI_KEYS` → `NEWSAPI_KEY` | `_newsapi_keys`/`_newsapi_key_idx` (`:60-63`) | `NewsApiClient` if importable (`:102-107`) |
| FRED | `FRED_API_KEYS` → `FRED_API_KEY` | `_fred_keys`/`_fred_key_idx` (`:65-68`) | `fredapi.Fred` (`:109-114`) |
| Yahoo Finance | none — keyless | — | `YahooFinanceProvider()` (`:116-122`) |
| OpenBB bridge | per-provider keys inside the bridge | — | `OpenBBDataBridge(store=store)` (`:124-130`) |
| SEC EDGAR | none — requires only a User-Agent | — | `self.sec = True`, CIK map lazy (`:132-134, 179-218`) |
| Portfolio123 | provider-internal (`api_id`/`api_key`) | — | `Portfolio123Provider()` (`:88-93`) |
| Seeking Alpha | provider-internal `api_key`) | — | `SeekingAlphaProvider()` (`:95-100`) |

**Rate limiting** (`:136-140`; `rate_limiter.MultiAPIRateLimiter` at `:13`):
```
newsapi  max_requests=100  time_window=60   # 100/min
fred     max_requests=120  time_window=60   # 120/min
sec      max_requests=10   time_window=1    # 10/s
```
Every outbound call preceded by `self.rate_limiter.wait_if_needed(<service>)` — `:284` newsapi, `:340` hot topics, `:415` FRED per series, `:475` SEC.

**Exponential backoff + key rotation:** per-service backoff state starts `{"last_429": 0.0, "delay_sec": 10.0}` for `newsapi`, `fred`, `seeking_alpha` (`:71-75`). A 429/quota calls `record_rate_limit()`, doubling `delay_sec` **up to a 300 s ceiling** (`:227-234`); `is_service_backed_off()` suppresses further calls (`:220-225`). `rotate_key()` advances the pool index, rebuilds the client, and **resets that service's backoff window to 10 s** (`:236-250`). Error paths inspect the string for `"429"`, `"rate"`, `"too many"` before rotating (`:320-323` news, `:442-444` FRED).

**Response shapes:** `get_news` → list of `{source, title, description, url, published_at, sentiment}` (sentiment from local lexical estimator `_estimate_sentiment()`, `:265-323, 374-392`) · `get_fred_data` → per-series `{value, date, previous, surprise}` where **`surprise = latest − previous`** (`:393-455`) · `get_sec_filings(ticker, filing_types=["8-K","10-Q"])` (`:457-516`) · `get_status()` → `ONLINE`/`DEGRADED`/`OFFLINE`; Seeking Alpha is `DEGRADED` when instantiated but `is_live` is false (`:550-575`).

### §07.4 The calendar plane — three-step cascade
1. **Configured enhanced calendar** — locates `terminal64.dll` under `terminal_info().path` (fallback `MQL5/Libraries/terminal64.dll`), calls `create_enhanced_calendar_wrapper(dll_path=...)`; on any failure `self.calendar = None` (`:721-739`).
2. **MT5 native calendar** — `collect_calendar_event_rows()` first asks the wrapper for `get_events(limit=5, min_importance=2, currencies=["USD","EUR","GBP","JPY"])` → source `configured_calendar`; if empty, calls `mt5.calendar_get(naive_anchor, naive_anchor + 24h)`, keeps `importance >= 2`, sorts by time, takes 5 → source `mt5_calendar`. Row = `{time, currency, event, importance, forecast, previous}` (`:270-325`).
3. **Forex Factory fallback** — `collect_calendar_event_rows_with_ff()` fetches `min_impact="HIGH"`, takes ≤ **5** events, promotes `impact=="HIGH"` → `importance=3` else `2`, `source="forex_factory"` (`:328-353`). Provider reads `https://nfs.faireconomy.media/ff_calendar_thisweek.json` with an **8 s timeout**, cache `artifacts/forexfactory_calendar_cache.json`, colour map **RED/ORANGE/YELLOW/GRAY → HIGH/MEDIUM/LOW/HOLIDAY**, `datetime_utc = f"{date}T{time}Z"` (`forexfactory_provider.py:18-19, 96-147, 158-190`).

> **Callout — "Consumers gate on the label, not on the object":** the orchestrator computes `calendar_ready = calendar_source in {"configured_calendar", "mt5_calendar"}` (`intelligence/llm_orchestrator.py:2495-2505`). A `"forex_factory"` or `"UNAVAILABLE"` source therefore **fails closed** for every setup whose requirements demand a calendar.

## 17.2 §08 Data Quality, Completeness & Feature Building (1251–1331)

Framing (verbatim): *"If §07 is the supply chain, this section is the customs border. Nothing reaches analysis, indicators or the LLM until it has passed here, and a rejection is a HOLD with reasons — never a best-effort continuation."*

**`DataQualityGate`** (`data/data_quality.py:18`) returns `DataQualityResult(passed, reasons)` from `validate(snapshot, require_live_quote=…)` (`:51`). Constructor defaults: **`max_return_zscore=5.0`**, **`max_snapshot_age_seconds=5.0`** (`:19`).

| Class of check | Representative reasons (verbatim) |
|---|---|
| Snapshot shape | `market snapshot context is malformed` |
| Live-data authenticity | `live market-data source is not authenticated MT5`; `… provider identity is invalid`; `… server identity is missing`; `… account identity hash is invalid`; `… identity contains sensitive fields` |
| Capture time & clock | `market snapshot capture time missing`; `market snapshot clock drift or stale capture`; `broker tick is stale or local/broker clock skew exceeds threshold` |
| Provider-vs-local time | `provider-time observation is missing`; `provider/local snapshot time differs beyond threshold` |
| OHLC integrity | `OHLC container is malformed`; `insufficient OHLC history for data-quality checks`; `invalid OHLC consistency at bar N`; `non-positive close at bar N` |
| Point-in-time discipline | `candle revision detected at <time>`; `candle timestamp is later than snapshot capture; future-data risk`; `duplicate or out-of-order candle timestamp`; `abnormal candle timestamp gap` |
| Outlier returns | `latest return is a data outlier; require event/manual review` |
| Depth of market | `DOM container is malformed`; `DOM is stale or clock-drifted`; `DOM unavailable or empty for DOM-required setup` |
| Quote sanity | `invalid or absent live bid/ask`; `invalid live spread` |

**Per-setup requirements** — `SetupDataRequirements.evaluate(setup_id, snapshot, calendar_available=…)` (`data/data_requirements.py:24`): DOM payloads for DOM-required setups (`DOM payload is malformed`, `DOM unavailable`, `DOM capture timestamp missing`) and macro data for macro-required setups (`cross-market/macro data unavailable for macro-required setup`). *"A setup that declares a requirement it cannot satisfy is not downgraded — it is not eligible."*

**The feature snapshot — versioned, hashed, point-in-time:**

| Element | Value | Anchor |
|---|---|---|
| Schema version | `FEATURE_SCHEMA_VERSION = "feature_snapshot_v1"` | `intelligence/feature_schema.py:11` |
| Required features | `REQUIRED_VALUES` tuple; missing names make the snapshot not-ready | ~14 |
| Schema hash | `schema_hash(version, values)` + `SCHEMA_HASHES` registry | ~24-29 |
| Values hash | `values_hash(values)` — binds a payload to its feature set | ~32 |
| Validation | `validate_snapshot(snapshot, require_ready=True)` | ~57 |
| Construction | `build_snapshot(ohlc, indicators, …)`, `FEATURE_SPEC_VERSION` | `intelligence/feature_builder.py:14, 37` |

> *"The schema hash travels with the data: a model trained under one feature schema cannot silently be fed another."*

**Mark quality vocabulary** — `mark_quality` default **`UNKNOWN`**, added by migration `core/trading_safety.py:277-283`, with indexes `idx_position_observations_quality` and `idx_market_ticks_quality`. *"Any management or sizing decision that depends on a mark reads its quality first; an `UNKNOWN` mark is not treated as fresh."* Excursion analysis `excursions(ticket, max_mark_gap_seconds=900)` (`:632`) declares itself `complete` only when gaps between valid marks stay within that bound — *"so MFE/MAE evidence cannot be manufactured from sparse sampling."*

## 17.3 §09 News, Macro & Alternative-Data Pipeline (1332–1405)

**`data/realtime_news_ingest.py`:** `normalize_article` 79 · `_parse_rss_bytes` 109 · `fetch_rss_feed(url, provider, publisher, timeout=8.0)` 153 · `collect_rss_articles` 163 · `collect_newsapi_articles` 177 · `collect_seeking_alpha_articles` 201 · `watch_entities_for_event_class` 233 (CPI → inflation, labour, rates) · **`ingest_articles` 243** · `extract_and_persist_claims` 343 · `run` 409.

**Triage: deterministic first, model-assisted second** (`data/news_triage.py`) — `dedupe_key_for(url, title)` 254 · `_deterministic_r1` 198 · deterministic label family `_deterministic_d5a` 518, `_deterministic_d9` 536, `_deterministic_labels` 559 · `_resolve_action(d0_1, d0_2, d0_3, policy_allowed)` 616 (`policy_allowed` can suppress the action regardless of label) · `_get_laya()` 459 · `_pick_better(qid, raw_answer, det_label, laya_probs)` 642 · `triage_article`/`triage_articles` 698/841 · `TriageGate.apply` 320 · `is_rejected` 329 · `summarize_cycle_evidence` 341.

> Callout: *"because a deterministic classifier exists and can overrule the model, a hallucinating classifier degrades news handling to 'conservative', not to 'reactive'."*

**Macro observation path:** `data/zero_lag_macro_provider.py`, `global_macro_bridge.py` · `data/data_source_pipeline.py`, `eia_provider.py` · `cftc_cot_provider.py` · `alternative_data_provider.py`, `alternative_data_registry.py` · `openbb_data_bridge.py`, `openbb_provider_registry.py` · `seeking_alpha_provider.py`, `sec_edgar_provider.py`, `portfolio123_provider.py`, `yahoo_finance_provider.py`. Normalised in `intelligence/macro_observations.py`, scored in `economic_dynamics_measure.py`, admitted to the trade path **only** through `intelligence/macro_fusion_gate.py` and the opportunity bus (§13).

## 17.4 §10 LLM Provider Fabric & Router (1406–1470)

**Five providers** over `BaseLLMProvider` (`:58`): `OpenRouterProvider` 134 · `TokenRouterProvider` 176 · `RoutesMeProvider` 217 · `CerebrasProvider` 259 · `NvidiaNIMProvider` 300. Each exposes `generate(messages, model, **kwargs)` and inherits four behaviours:

| Behaviour | Implementation | Why |
|---|---|---|
| Circuit breaker | `is_circuit_open()` 83, `_record_success()` 86, `_record_failure()` 89 | a failing provider is removed from rotation instead of burning the cycle timeout |
| Key rotation | `_get_next_key()` 95 | multiple keys per provider (`NVIDIA_KEY_1`, `NVIDIA_KEY_2`, …) rotate on failure |
| Retry with backoff | `_retry_with_backoff(request_fn, …)` 105 | transient 5xx/rate-limits retried inside the call budget |
| Rate limiting | `RateLimiter(rpm, max_wait_seconds)` 26-38 + `wait_if_needed()` | a full wait budget **yields** rather than blocking the pulse thread |

**`LLMRouter`** (343): `_load_config_from_env` 355 · `_init_providers` 435 · `_init_agents` 457 · `register_agent(name, provider, model, fallback_providers)` 466 · `get_available_providers()` 476 · `get_available_models()` 479 · `configure()` 510 · `generate(agent, messages, model)` 539 · `generate_with_tools()` 633 · `get_status()` 712.

**Three logical agents** in `.env.example`, each with its own provider, model and fallback list: `TRADER_LLM_*`, `ANALYST_LLM_*`, `RISK_LLM_*`.

> **Decisive switch** — `LLM_TRADER_ALLOW_PROVIDER_FAILOVER=false` by default, verbatim in-file: *"Default false: the selected provider is authoritative and its failure becomes a deterministic HOLD decision (fail-closed trade safety)."* Set `true` to use the per-agent `*_FALLBACK` lists.

**Budgets, leases, token accounting:**

| Control | Where | Effect |
|---|---|---|
| Per-provider ceilings | `OPENROUTER_RPM / RPD / MAX_CONCURRENT / MAX_TOKENS_PER_HOUR / MAX_COST_PER_DAY` (same shape ×5 providers) | hard caps on rate, daily requests, concurrency, hourly tokens, daily spend |
| Call timeout | `LLM_ROUTER_TIMEOUT=30`, `LLM_ROUTER_MAX_RATE_WAIT=15` | a slow or saturated provider cannot freeze the market pulse thread |
| Token estimation & budget | `estimate_tokens(text)` 755, `enforce_token_budget(...)` 760 | pre-call budget check; oversized prompts trimmed rather than billed |
| Per-cycle budget lease | `_llm_provider_and_budget` 883, `_normalize_llm_usage` 902, `_llm_trace_outcome` 933, `finish_llm`/`fail_task` 6644 | budget is **`RESERVED`** before the call and settled on completion or failure, *"so an interrupted call (including KeyboardInterrupt mid-LLM) cannot strand reserved budget until lease expiry"* |

**Total provider failure flow (verbatim diagram):**
```
[all providers fail]
   → [deterministic_hold_fallback returns a HOLD decision]   (node class: adv)
   → [trace recorded: provider + error + usage]               (node class: state)
   → [cycle HOLDs: no proposal is created]                    (node class: gate)
   → [maintenance continues: reconciliation, management, schedules]
```
> *"A safety HOLD is explicitly not recorded as a successful provider outcome (~6857): the failure is written to the durable trace so repeated failures are visible, while the cycle simply does not trade."*

## 17.5 §11 Causal Intelligence: Macro Graph & Economic Dynamics (1471–1607)

⚠ **Mojibake-corrupted (23 lines) and truncated relative to its own promise** — §11.7 supplies 2 of 3 announced items.

Architecture (verbatim): *"deliberately split into four separately auditable pieces: a reviewable edge store (SQLite, source-linked), a seeded theory catalog (machine-readable priors), a measurement/validation engine that turns event reactions into statistics, and an advisory fusion gate that can veto a technical proposal without ever being able to place one."*

> **Doctrine, in code** (`intelligence/macro_graph.py:1-5`): *"Edges are hypotheses, not trading permissions. They require explicit review before appearing in a macro context and always retain source/confidence/time."*

### §11.1 The graph store (766 lines) — `MacroGraph._init()` `:37-92`
Six objects, all in the same `trading_state.db` as the journal:

| Object | Key columns | Role |
|---|---|---|
| `macro_sources` `:40-44` | `source_id` PK, `captured_at`, `title`, `source_url`, `publisher`, `published_at`, `rights_note`, `content_hash`, `version`, `payload` | Provenance. An edge cannot exist without an ingested, policy-approved source. |
| `macro_entities` `:45-48` | `entity_id` PK, `name`, `entity_type`, UNIQUE(`name`,`entity_type`) | Graph nodes; created on demand by `entity()` `:164` |
| `macro_edges` `:49-59` | `source_entity`, `target_entity`, `relation`, `sign`, `confidence`, `source_id`, `valid_from`, `valid_until`, `contradiction_of`, `status`, `review_by`, `reviewed_at`, `notes` | The edge. `notes` doubles as a JSON carrier for lag metadata + catalog provenance. |
| `idx_macro_edges_lookup` `:60` | (`source_entity`,`target_entity`,`status`) | hop lookup |
| `macro_edge_history` `:61-67` | `edge_id`, `changed_at`, `action`, `status`, `reviewer`, `payload` | append-only trail (`PROPOSED` `:202-205`, `REVISED` `:302-304`) |
| `event_reactions` `:68-72` | `event_source_id`, `symbol`, `observed_at`, `actual`, `forecast`, `previous`, `surprise`, `windows`, `source_quality` | empirical outcome evidence |

Connection handling deliberately lock-tolerant: **`PRAGMA busy_timeout=10000`**, **`PRAGMA synchronous=NORMAL`** (`:31-35`).

### §11.2 Edge lifecycle
1. **Propose** — `propose_edge()` `:170-206` rejects `sign` outside `{POSITIVE, NEGATIVE, CONDITIONAL, UNKNOWN}`, rejects non-finite/out-of-range `confidence`, requires non-empty identity + relation, requires `source_id` to already exist, and requires that source's stored **`source_policy.allowed is True`** (`:188-196`). Lands as `status='CANDIDATE'` + a `PROPOSED` history row.
2. **Lag-aware propose** — `propose_lag_aware_edge()` `:208-227` writes `{lag_aware, min_lag_seconds, max_lag_seconds, decay_half_life_seconds, user_notes}` into `notes` — *"exactly the JSON that `causal_propagate._half_life()` parses back out later."*
3. **Review** — `review_edge(edge_id, approve=…, reviewer=…)` `:228-246`. `review_by`/`reviewed_at` are the point-in-time gates every reader uses; `approved_edges()` `:381` filters on status **and** as-of time.
4. **Revise (credit/blame)** — `revise_edge()` `:264-305` records a before/after payload, then **forces the edge back to `CANDIDATE`** and clears `review_by`/`reviewed_at` — *"so changed evidence can never keep a stale approval."*
5. **Prune** — `prune_stale_edges()` `:610-686` scans by `max_age_days`/`min_confidence`, writes one history row listing pruned ids, returns `execution_authority: False`.

**Candidates-only seeding** (`intelligence/economic_dynamics_seed.py:1-8`): one primary citation (`SOURCE_DOCUMENT`, `CITATION_URL` = the Fed FOMC page; catalog `:18-22`). `_existing_keys()` `:53-65` treats **`CANDIDATE, AUTO_APPROVED, APPROVED, MEASURED, STAT_PASS`** as "already present" (dedupe key = source+target+relation+sign, `:44-50`); `seed_economic_dynamics()` aborts if `validate_catalog()` reports integrity problems (`:74-76`). Envelope: `status: "CANDIDATE_SEED_COMPLETE"`, `created_count`, `skipped_duplicates`, `execution_authority: False` (`:140-148`).

### §11.3 Bayesian credit/blame (verbatim formula, mojibake-corrupted on disk)
```
delta_conf = clamp( R_multiple × 0.05 , −0.15 , +0.15 )
new_conf  = clamp( old_conf + delta_conf , 0.05 , 0.99 )
then status → CANDIDATE, review_by → NULL, history → REVISED(reviewer = TRADE_OUTCOME:<ticket>)
```
Implementation `apply_trade_outcome_feedback()` `macro_graph.py:307-354`. Returns `old_confidence`, `new_confidence`, `r_multiple`, `label`, `status='CANDIDATE'`; notes payload stores `confidence_delta` + `previous_status`. *"Callers may immediately re-run autonomous validation to restore `AUTO_APPROVED` when statistics still support the edge"* (docstring `:309-314`).

**Live wiring** `LLMOrchestrator._apply_causal_trade_feedback()` (`llm_orchestrator.py:3277-3313`): reads the durable trade payload, takes `proposal_features["causal_edge_ids"]` (or `payload["causal_edge_ids"]`), and for each id calls `apply_trade_outcome_feedback(...)` **followed by** `causal_validator.validate_candidate_edge(edge_id)`, attaching the revalidation result to each feedback row. Batch journalled via `operations.event("causal", "trade_outcome_feedback", …)`. *"If no edges are linked, the function returns an empty list — it never fabricates attribution."*

> **⚠ This is the resolution of Gap #8 (§13.1).** The PRD says re-validation *does* follow. Verification found `grep -i revalidat` over `macro_graph.py` = 0 hits — but the re-validation lives in the **caller** (`causal_validator.validate_candidate_edge`). The KG decay risk is therefore **lower than first assessed** but not zero: the path depends on `causal_edge_ids` actually being attached to proposals, which was not confirmed.

### §11.4 The Economic Dynamics catalog — real counts from code
`intelligence/economic_dynamics_catalog.py`. Header invariants (`:6-12`): edges are hypotheses, **`EXECUTION_AUTHORITY = False`** (`:22`), seeded edges enter MacroGraph as `CANDIDATE`, confidence is a conservative theory prior, time-varying signs use `CONDITIONAL` + `active_regimes`.

| Structure | Declaration | **Verified size** | Contents |
|---|---|---:|---|
| `ENTITY_TYPES` | `:30-46` | **15 types** | MONETARY_POLICY, LIQUIDITY, CREDIT, MACRO_INDICATOR, YIELD_CURVE, CURRENCY, COMMODITY, EQUITY, VOLATILITY, SENTIMENT, GEOPOLITICAL, MARKET_STRUCTURE, ASSET_REGIME, EVENT_CLASS, NOWCAST_PROXY |
| `ENTITIES` | `:48-153` | **96** | Layered L0 policy → L1 money/credit/yields → L2 real economy → L3 risk/sentiment → L4 instruments, each with `aliases` (`CENTRAL_BANK` → FED/ECB/BOJ `:50`; `REAL_YIELD` → TIPS_REAL/DFII10 `:61`) |
| `CAUSAL_EDGES` (built by `_edge()`) | helper `:156-190`, list `:197-483` | **83** | per edge: source/target name+type, relation, sign, confidence (default **0.72** `:164`), channel (default `general`), `active_regimes` (default `("ALL",)`), `min_lag_seconds`=0, `max_lag_seconds`=**86 400**, `decay_half_life_seconds`=**43 200**, notes, `contradiction_group`, `catalog_version` |
| `CORRELATION_REGIMES` | `:490-585` | **7** | NORMAL_RISK_OFF, CRISIS_PANIC, STAGFLATION, EASY_MONEY, TIGHTENING + siblings; each with `expected_moves`, `pair_signs` (`DXY|GOLD: NEGATIVE` `:496`), `broken_pairs` (CRISIS_PANIC declares `DXY|GOLD_INVERSE` broken `:513`), `advisory_bias` |
| `TRANSMISSION_CHANNELS` | `:586-619` | declared list | interest_rate, fx_carry, opportunity_cost, liquidity, safe_haven, stoichiometry, fiscal (*months_to_quarters*), terms_structure (*days_to_months*), general — each with `lag_class` |
| `EVENT_TEMPLATES` | `:626-699` | **7** | cb_meeting (pre 60m/post 30m), cpi_release (30/15), nfp_release (30/15), war_shock (0/1440), banking_stress (0/2880), trade_war (0/1440), political_shock (0/360) — each with `watch`, `first_order`, `second_order`, optional `key_questions` |
| `DECADE_SHOCK_TEMPLATES` | `:758-855` | **8** | brexit_2016, trump_election_2016, trade_war_2018, repo_stress_2019, covid_crash_2020, oil_negative_2020, inflation_tightening_2022, banking_stress_2023 — each with `date_window`, `source_url`, `research_questions`, `required_fields`, `primary_entities` |
| `COMPLEXITY_TIERS` | `:862-888` | **5** | T0_CORE (catalog+seed+graph+event_policy) · T1_REGIME (intermarket_regime, correlation_risk, lag_delay_engine) · T2_VALIDATE (autonomous_causal_engine, edge_lifecycle, broker_replay) · T3_OPPORTUNITY (causal_setup_map, opportunity_bus, scanner) · T4_LEARN (reward_ledger, feature_builder, adaptive_research_service) |
| `CRITICAL_DOC_CHAINS` | `:929-944` | **14** | fail-closed coverage list checked by `coverage_report()` `:947-980`: POLICY_RATE→DXY, REAL_YIELD→OPPORTUNITY_COST_OF_GOLD, DXY→GOLD, WAR_SHOCK→WTI_OIL, VIX→RISK_SENTIMENT, BANKING_STRESS→CREDIT_SPREAD, … |

> 🔴 **The "134" figure is wrong.** `README.md:32` and `docs/SOURCE_OF_TRUTH.md:367` describe this as *"134 economic dynamics definitions"*. The code contains **83** `_edge()` entries and **96** entities. *"The '134' figure matches no single list length today; treat it as a stale Markdown claim and quote `catalog_summary()` (`:910-925`) at runtime instead"* — it reports `entity_count`, `edge_count`, `regime_count`, `channel_count`, `event_template_count`, `free_data_bindings`, `decade_shock_templates`, `tradable_setup_gaps` from `len()` at import time.
>
> **This supersedes the "134" claim used throughout Part 8.9 of this document.**

Two further contracts: `tradable_setup_gaps()` `:1004-1034` iterates `CAUSAL_EDGES` and reports `(relation, target_type)` pairs whose target is in **`TRADABLE_TARGET_TYPES` = {CURRENCY, COMMODITY, EQUITY, VOLATILITY, ASSET_REGIME, MARKET_STRUCTURE}** (`:999-1001`) but has no registered setup mapping — *"the explicit to-do list for research."* `validate_catalog()` `:1036` is the integrity gate the seeder refuses to bypass.

### §11.5 Measurement — event reactions become statistics
1. `ingest_live_release_surprise()` `:688-766` — **`surprise = actual − forecast`**, classified against **`tolerance = 0.0001`** as `IN_LINE`/`HAWKISH_SHOCK`/`DOVISH_SHOCK`.
2. `schedule_event_reaction()` `:453-480` — anchors a tracker on the post-release observed price, marks `TRACKING`, honesty note *"post_release_anchor; calendar actual available"*.
3. `capture_due_event_reactions()` `:482-540` — default window map **`{"1m":60, "5m":300, "15m":900, "1h":3600, "4h":14400, "1d":86400}`** (`:496`); **`return_fraction = (observed_price − anchor_price) / anchor_price`** (`:523`). *"It records elapsed real time only: never backfills invented 1-minute prices"* (`:486-489`).
4. `similar_event_reactions()` `:576-608` — ranks **up to 300** historical reactions by **`score = 1 − |Δsurprise| / max(|current|, |historical|)`**, multiplied by **`0.25`** when the sign flipped (`:590-596`).
5. `due_post_event_reviews(symbol, hours=48)` `:560-574`.

**Event-class token map** (`economic_dynamics_measure.py:30-39`): CPI/PCE→`CPI_RELEASE`; NFP/PAYROLL→`NFP_RELEASE`; FOMC/ECB/BOJ→`CB_MEETING`; WAR/MISSILE→`WAR_SHOCK`; BANK/SVB/FDIC→`BANKING_STRESS`; TARIFF/SANCTION→`TRADE_WAR`; TWEET/ELECTION→`POLITICAL_SHOCK`; GOLD BUYING/PBOC GOLD→`CB_GOLD_ACCUMULATION`. A model-routed R1 headline class is **post-validated against the lexical router** so a model cannot silently re-route an event onto a different catalog seed: `_R1_ENTITY_MAP` `:59-68` mirrors `_EVENT_TOKEN_MAP` one-to-one; `post_validate_r1_routing()` `:83+` accepts lexical agreement, accepts lexical abstention, **rejects conflicts**. `run_catalog_measurement_pass()` `:351-369` reports `validated_count`, `approved_count`, `insufficient_sample_count`, `approved_edge_ids`, `status: "MEASUREMENT_PASS_COMPLETE"`.

**`intelligence/autonomous_causal_engine.py` (505 lines) — fail-closed constants (`:34-37`):**

| Constant | Value | Meaning |
|---|---:|---|
| `MIN_REACTION_SAMPLES` | **30** | below this, no hit-rate is invented; the edge stays `INSUFFICIENT_SAMPLE` |
| `MIN_CONFIDENCE` | **0.70** | prior floor for automatic approval |
| `MIN_EMPIRICAL_WIN_RATE` | **0.60** | directional hit-rate floor |
| `MIN_PROMOTION_EXPECTANCY_R` | from `research/research_validation.py` | replay floor for `STAT_PASS → REPLAY_PASS` (`apply_replay_floors()` `:499-573`) |

Hit test `_directional_hit()` `:69-75`: a POSITIVE edge counts as a hit when (surprise>0 and ret_15m ≥ 0) or (surprise<0 and ret_15m ≤ 0); a NEGATIVE edge inverts both. Bars located with `_bar_index_at_or_after()` `:60-66` — first completed bar at/after the observation, explicitly *"no look-ahead invent"*. Replay promotion requires a completed backtest, ≥ `min_closed_trades`, and `expectancy_r > floor`; reviewer recorded on the history row is **`CAUSAL_REPLAY_FLOOR`** (`:542-561`). With no stored bars it returns **`SKIPPED_NO_BARS`** + *"Store causal_replay_bars before replay-floor promotion."* (`:518-524`) instead of a fabricated pass.

### §11.6 Propagation math — `intelligence/causal_propagate.py` (159 lines)
```
sign(POSITIVE)=+1, sign(NEGATIVE)=−1, sign(CONDITIONAL)=+0.5, otherwise 0     :18-26
attenuation(age, half_life) = exp( −age / half_life )                       :65-69
sign_path = Π sign(edge_i)     conf_path = Π ( confidence_i × attenuation_i )   :133-134
score = | sign_path | × conf_path      signed_score = sign_path × conf_path     :146-150
bias = BUY if sign_path>0, SELL if sign_path<0, else NEUTRAL
horizon = max(edge half-lifes)                                              :151-152
path_id = "cpath:" + sha256({hops, bias, symbol})[:20]                        :172-178
```
Constants: **`MAX_HOPS = 3`**, **`DEFAULT_HALF_LIFE = 43 200 s` (12 h)** (`:14-15`). Half-life resolution order: explicit field → field alias → JSON `notes.decay_half_life_seconds` → default (`:29-46`); confidence clamped to [0,1], non-finite → 0 (`:49-56`); edges with `sign == 0` or `confidence ≤ 0` skipped (`:131-132`); expansion is **breadth-first** with a `seen_paths` set keyed on the ordered hop-id tuple, so cycles cannot loop (`:113-127`). Seed expansion for a symbol also splits a 6-character pair into its legs (`sym[:3]`, `sym[3:6]`) plus the full symbol (`:81-87`) — *"which is why an EURUSD decision inherits `DXY`/`EUR` edges."* A no-path result is a fully-formed object (`status: "NO_PATH"`, `bias: "UNKNOWN"`, `score: 0.0`, `execution_authority: False`) rather than an exception (`:88-97, 161-170`).

> Module docstring and returned payload both say: *"Advisory path score only; never grants order permission."* (`:1-4, 172-179`)

**Second graph-walking reasoner:** `intelligence/graphrag_reasoning.py` `CausalGraphRAGEngine(macro_graph, damping_factor=0.88)` (`:43-46`) → `find_causal_paths()` `:71` → `CausalStep`/`CausalPath` `:122` → `format_cited_prompt_context(paths, max_paths=5)` `:159` — *"so the LLM receives provenance-bearing chains instead of bare entity names."*

### §11.7 Regime classification — ⚠ INCOMPLETE
Opening claims *"Three independent modules narrow the graph to what is currently plausible."* Only **two** are delivered.

**(1) Live intermarket regime** (`intelligence/intermarket_regime.py`): `_sign_from_corr()` `:41-46` uses `deadzone = 0.15`; `REGIME_ENTITY_PAIRS` `:27-38` declares **10 scored pairs** (DXY|GOLD, DXY|WTI_OIL, GOLD|EQUITIES, GOLD|WTI_OIL, VIX|EQUITIES, DXY|EQUITIES, REAL_YIELD|GOLD, REAL_YIELD|EQUITIES, YIELD_CURVE_SLOPE|EQUITIES, WTI_OIL|EQUITIES); symbol aliases via `ENTITY_SYMBOL_PROXIES` `:15-24` (e.g. `GOLD → XAUUSD`, `WTI_OIL → USOIL/XTIUSD/CL`). `score_regime()` `:76-105` counts compared/matched pairs, skips `NEUTRAL`. `build_intermarket_regime_snapshot()` `:281-348` fails closed at every step: unusable `CorrelationRisk` → `status: ERROR`; fewer than 2 resolvable entities → `INSUFFICIENT_SYMBOLS`; payload always carries `resolved_proxies`, `pair_correlations`, `macro_edges_in_regime` vs `macro_edges_total`, `execution_authority: False`.

**(2) Probabilistic regime** (`intelligence/regime_classifier.py`): BMA with fixed priors **`{RULE_BASED: 0.40, GAUSSIAN_HMM: 0.35, CORRELATION_MATRIX: 0.25}`** (`:47-52`).

| Rule | Condition | p | Line |
|---|---|---:|---|
| CRISIS_PANIC | `vix ≥ 30 and gold_dxy_corr ≥ 0.15` | 0.85 | 69-70 |
| STAGFLATION_SHOCK | `oil_change_pct > 0.02 and gold_dxy_corr > −0.20 and dxy_change_pct ≤ 0` | 0.70 | 73-74 |
| NORMAL_RISK_OFF | `vix ≥ 18 or dxy_change_pct > 0.005` | 0.65 | 77-78 |
| (default) RISK_ON_EXPANSION | — | 0.75 | 81 |

Correlation breakdown adds CRISIS_PANIC mass when average pairwise correlation ≥ **0.70** (`:145-146`). HMM path uses `hmmlearn` when installed, otherwise labels itself a **z-score sigmoid heuristic** (`:22-28, 83-132`). BMA sum normalised and rounded to **4 dp**; `is_crisis_override` fires when CRISIS_PANIC is argmax **or** carries ≥ **0.40** probability (`:189-207`). `execution_authority=False` pinned in the dataclass.

> **⚠ No third item. File ends at line 137.**

## 17.6 §12 Memory Architecture (L0–L3, Semantic, Local, Remote) (1608–1694)

Framing (verbatim): *"Memory answers 'what did we learn, and may we still trust it?' It is a SQLite model with layers, as-of semantics, review gates and telemetry on every retrieval — not a prompt buffer that accumulates text."*

**`TradingMemory` tables** (`intelligence/trading_memory.py` `_init()`), all in `trading_state.db` in **WAL mode** (`:43`):

| Table | Line | Role |
|---|---:|---|
| `macro_events` | 51 | raw macro/event observations with importance (`_event_importance` ~407) |
| `playbooks` | 54 | per-symbol playbook from events + technicals (`current_playbook` ~416) |
| `narratives` | 56 | daily/weekly narrative documents (~582, ~609) |
| `trade_labels` / `setup_labels` | 60, 62 | outcome labels per closed trade and per setup instance |
| `verified_memories` | 68 | reviewed, approved memories with tags and source ids |
| `atomic_facts` | 76 | **L1** — append-only, superseded not mutated (`record_fact` ~324, `supersede_fact` ~348) |
| `scenario_documents` | 85 | **L2** — built from outcomes **and rejected/unresolved setups** (`rebuild_scenarios` ~759) |
| `core_memories` | 93 | **L3** — policy memory, review required before any agent sees it (~961, ~978) |
| `working_context_items` | 102 | short-horizon working context (`compact_working_context(max_active=24)` ~1379) |
| `memory_retrievals` | 110 | **telemetry for every retrieval**: layer, tags, result count, latency, prompt estimate, as-of |
| `case_study_memories` | 118 | historical case studies linked to setups |

FTS index maintained alongside (`_init_fts` ~170, `rebuild_fts` ~247) with `_fts_match_query(tags, max_terms=10)` (~261) and per-layer candidate builders for narratives (~477), verified memories (~742), scenarios (~944).

**Layer contract:**

| Layer | Stores | Write path | Read path |
|---|---|---|---|
| L0 episodic | trades, events, lifecycle history | journal (`core/trading_safety.py`) | fact + trade-label queries |
| L1 atomic facts | single declarative claims with confidence + as-of | `record_fact`, `record_events` | `retrieve_facts(fact_keys, limit=20)` ~352 |
| L2 scenarios | matched setup playbooks with win/loss balance | `rebuild_scenarios()` | `retrieve_scenarios(tags, limit=3)` ~899, **after** `review_scenario` ~868 |
| L3 core | policy + operating principles | `propose_core_memory` → `review_core_memory` | `retrieve_core_memories(namespaces, limit=12)` ~1023 |

> **Callout — the review gate is the point:** *"Require review before an L2 scenario can enter proposal context"* (~869); *"Human/operator review is mandatory for L3 policy/personality memory"* (~979). *"Self-improvement can propose a policy change, but the system cannot adopt it by itself."*

**Retrieval:** `retrieve_for_proposal(tags, core_namespaces=…)` (~1069) runs **all seven read-only layers concurrently** on worker threads, records per-layer telemetry via `_record_retrieval(...)` (~1044); a failing layer degrades to `[]` plus an `errors` entry — *"one broken layer can no longer stall or kill proposal assembly"*. Every retrieval honours an `as_of` timestamp (`_normalize_as_of` ~302). `link_retrievals_to_trade(retrieval_ids, ticket)` (~1337) and `resolve_retrieval_outcome(ticket, label)` (~1346) close the loop. `prune_retrievals(keep_days=90, keep_linked=True)` (~1145) keeps linked retrievals even when pruning.

**Side memories:**
- **Semantic** (`intelligence/semantic_memory.py`) — high-dimensional retrieval over scenario text.
- **Local agent memory** (`local_llm_memory.py`, `local_agent_memory.py`) — offline, local-only; `LOCAL_MEMORY_ENABLED` (default **`false`**), `LOCAL_MEMORY_PATH`, `LOCAL_MEMORY_MAX_ACTIVE=5000`, `LOCAL_MEMORY_RETENTION_DAYS=90`, `LOCAL_MEMORY_COMPACT_KEEP=200`.
- **Remote mirror** (`intelligence/remote_memory_mirror.py`) — optional MySQL mirror (`REMOTE_MIRROR_ENABLED=false`, `REMOTE_MIRROR_ON_WRITE`, table prefix `llm_trader_`, **3 s connect timeout**). Verbatim: *"Local SQLite stays authoritative … Never grants execution_authority."*
- **Case-study memory** (`case_study_library.py`, `case_study_matcher.py`, `case_study_learner.py`) — connects current setups to historical analogues.

---

# PART 18 — PRD SUBSYSTEM REFERENCE §13–§21

## 18.1 §13 Orchestration Internals (1695–1780)

**The TOON payload** — `_round_floats(obj, precision=5)` ~1075 · `_sanitize_toon_lite_budget(toon_lite, max_total_chars=60000)` ~1088 · `_format_lessons_learned()` ~1032 · `_get_knowledge_base_context(toon)` ~1044. Evidence `llm_orchestrator.py:1032-1147`.

**Opportunity bus — many producers, one arbitration point** (`intelligence/opportunity_bus.py`):

| Function | Line | Purpose |
|---|---:|---|
| `_lock_authority(payload)` | 40 | stamps every candidate with **zero execution authority** |
| `opportunity_from_scan_row(row, timeframe)` | 46 | candidate from market scanner |
| `opportunity_from_features(...)` | 72 | from feature snapshot — ADX, EMA alignment, volume confirmation, liquidity sweeps, Asian-range breakouts, completed-bar time |
| `opportunity_from_stat_pass_edge(...)` | 111 | from statistically validated edges |
| `publish_from_stat_pass_edges(...)` | 184 | batch form |
| `publish(store, candidates, agent="TechnicalNet")` | 248 | writes candidates to store |
| `load_open(store)` | 293 | open candidates |
| `bus_snapshot(store)` | 357 | bus state for UI |
| `handoff_for_validation(...)` | 319 | hands candidate to the validator — **the only exit with any authority** |

**Fusion** (`intelligence/opportunity_fusion.py`): `is_macro_setup(setup_id)` 23 · `_edge_sign_agrees(sign, side)` 28 · `_edge_touches_symbol(edge, symbol)` 38 · `_propagation_agrees(propagation, side)` 74 · `score_opportunity(...)` 98 · `fusion_block_reasons(fusion)` 307. **Fusion can block, never authorise.**

**Signal arbitration** (`intelligence/signal_arbitrator.py`): `_narrative_decay_state(decisions)` 54 · `_direction_agrees(signal_dir, regime_bias)` 68 · `_causal_support(signal_side, causal_paths)` 129 · `_event_alignment(event_state)` 154 · `_extract_signals_from_state(state)` 170 · `_score_signal(...)` 292 · `arbitrate_signals(...)` 344.

**Debate protocol** (`intelligence/debate_protocol.py`): `DebateTurn` 85 · `DebateVerdict` 101 · `ApexQuantParliament.conduct_debate(...)` 165/182 · `_conduct_deterministic_debate(...)` 221 (reproducible default) · `_conduct_llm_debate(...)` 446 · `as_veto_caller()` 493 (exposes result to the permission path as a veto function).

**Universal decision engine (advisory)** (`intelligence/universal_decision_engine.py`): `DecisionVerdict` enum ~56; each criterion yields a `PhilosophicalAssessment` ~69 inside a `UniversalDecision` ~81. Assessors: `_assess_epistemic_humility` 149 · `_assess_expected_value` 196 · `_assess_minimax_regret` 257 · `_assess_catastrophic_tail` 309 · `_assess_second_order` 360 · `_assess_via_negativa` 408 · `_assess_compensated_risk` 444 · `_assess_reversibility` 507.

**Pipeline V2** (`core/harness/pipeline_v2.py`): skip-token `request_skip_maintenance()`/`consume_skip_maintenance()` ~41-45 · blackout gate ~158 · portfolio consult ~170 · legacy cycle body ~188 · `velocity.poll`, `reconcile.pending`, `reconcile.open`, `memory.dispatch`, `position.tick`, `worker.heartbeat` 228-244. The skip token ensures both paths perform **identical work**.

> **§13 defect:** no numeric thresholds anywhere — the arbitration/fusion/debate scores are described qualitatively with **zero disclosed weights or cut-offs**, making §13 unauditable against §16, which does publish constants. Also `publish(..., agent="TechnicalNet")` hardcodes a single default producer for a bus designed for *many* producers.

## 18.2 §14 Post-Trade Reflection & Continuous Learning (1781–1875)

Runs after a trade closes, **on the slow lane, with no authority over positions**.

**The reflection graph** (`intelligence/reflection_graph.py`), contract verbatim: *"Slow-Lane Post-Trade Memory Reflection & Consolidation Graph. Asynchronously processes closed trades, distills L1 Atomic Facts, updates L2 Scenario Documents, and recalibrates causal edge weights."* Checkpointed with `SqliteCheckpointSaver` (`intelligence/graph_checkpointer.py`) so a crash mid-reflection **resumes rather than restarts**.

| # | Node | Class | Job |
|---:|---|---|---|
| 1 | `equity_state_update` | `EquityStateUpdateNode` ~20 | reads `risk_manager_state`, reconstructs `RiskManager(balance=…, store=…)` — falling back to `10000.0` **only if the stored base balance is unusable** — recalibrates streak counters, R-history, Kelly inputs. Runs **first** so the next cycle's gates read fresh state. |
| 2 | `quantitative_coach` | `QuantitativeCoachNode` `graph_nodes.py:1247` | distils L1 atomic facts; classifies *why* the trade ended: regime shift / execution slippage / thesis invalidation |
| 3 | `scenario_consolidator` | — | updates L2 scenario playbooks with realised win rate and payout for the matched setup cluster |
| 4 | `macro_feedback` | — | applies Bayesian credit/blame to the causal DAG paths active when the trade was taken |

**Labels:** `TradingMemory.label_closed_trade(ticket, pnl, risk_amount, exit_reason)` `trading_memory.py:1498` · `_setup_outcome_label(state, reason)` ~1526 · scenario rebuilding ~759 **explicitly includes rejected and unresolved setups** so near-misses teach the system too.

**Reward attribution + RL eligibility gate:** `operations/reward_ledger.py`, `reward_attribution.py`, `reward_sustainability.py`. **Model updates are not automatic.** `_rl_learning_allowed()` `llm_orchestrator.py:3671` · `_research_influence_allowed(model_kind, paper_mode=…)` ~3695 (**paper scoped separately from live**) · `operations/research_influence_gate.py` records which model kinds an operator approved, **revocable through the journal CLI**.

**Why the slow lane:** `DualVelocityExecutor.submit_slow(key, fn, limit=3)` (`operations/dual_velocity.py:44-110`) keyed **`memory:YYYYMMDDHH`** so the hourly batch deduplicates. Saturated slow lane returns `{"status": "BACKPRESSURE"}` → raises a `slow_lane_backpressure` event instead of blocking the 5-second market pulse.

**Scheduled learning work:** session review `_execute_session_review` ~5370 · daily review `_execute_daily_review` ~5398 · weekly research `_execute_weekly_research` ~5420 · self-improvement `_execute_self_improvement` ~5486 (writes `last_*` state so a missed run is visible rather than silent) · monthly maintenance `monthly_memory_maintenance` / `archive_stale_candidates` `trading_memory.py:680, 701`.

**The memory ladder in one table:**

| Layer | Write API | Read API | Governance |
|---|---|---|---|
| L0 episodic | `label_closed_trade` ~1498 | `retrieve_facts` / trade tables | **immutable record; corrections are labels, not edits** |
| L1 atomic | `record_fact` ~324, `supersede_fact` ~348 | `retrieve_facts` ~352 | supersession chain, not mutation |
| L2 scenarios | `current_playbook` ~416, `rebuild_scenarios` ~759 | `retrieve_scenarios` ~899 | `review_scenario` ~868 |
| L3 core | `propose_core_memory` ~961 | `retrieve_core_memories` ~1023 | `review_core_memory` ~978, `retire_core_memory` ~990 |
| Working context | `append_working_context` ~1356 | `retrieve_working_context` ~1415 | `compact_working_context(max_active=24)` ~1379 |
| Retrieval provenance | `_record_retrieval` ~1044, `link_retrievals_to_trade` ~1337 | `memory_quality_metrics` ~1229 | **every recall is scored by the trade it influenced** |

> **Callout (verbatim): "Self-promotion of belief is structurally impossible."** *"A lesson can reach the core namespace only through `propose_core_memory` → `review_core_memory`. Until then it is a candidate that the retrieval path treats differently."*

## 18.3 §15 Alpha Mining & Factor Library (1876–1946)

**`AlphaDiscoveryEngine` (`core/alpha_discovery.py:505`) sandbox guards:**

| Guard | Implementation | Purpose |
|---|---|---|
| Expression allow-list | `_ALLOWED_NAMES` ~54 | only sanctioned operators/functions may appear |
| Column allow-list | `_ALLOWED_COLUMNS = {"open","high","low","close","volume","returns"}` ~62 | no leakage from future columns, labels or exogenous series |
| Safe evaluation | `SafeAlphaEvaluator` ~65 | evaluates candidate ASTs without arbitrary execution |
| Hermetic runs | `SealedSandboxEvaluator` ~281 | backtests run sealed; candidate cannot reach network, disk or broker |
| Originality | `_normalize_ast` / `_ast_similarity` / `check_originality` ~197-259 | rejects near-duplicates of already-registered alphas |
| Experience memory | `ExperienceMemory` ~413, `AlphaExperienceMemory` ~440 (store-backed) | remembers what was tried |

Bootstrapping runs a fast genetic pass, verbatim: `GAEngine(GAConfig(population_size=15, generations=1, min_ic_threshold=0.005))` over **1,000 H1 bars** (`mt5_trader.py:4134-4153`).

**Persistence family** (time-series momentum with vol normalisation): `_tsmom_z(df, horizon_bars, vol_window?)` = z-score of cumulative return over horizon · `alpha_persistence_5d/10d/20d/40d` · `alpha_persistence_ma_gap` · `alpha_persistence_sign_agreement`.
```
ic_t      = corr( alpha_t , forward_return_{t+h} )
composite = Σ w_i · zscore( alpha_i )        # alpha_composite in sizing
```

> **Callout (verbatim): "Zero live authority."** *"A mined alpha can enrich the payload and reduce or zero a position (through the alpha-risk and walk-forward multipliers), but it can never open a trade. Only a validated LLM proposal does that. `alpha_discovery_state.json` and `alpha_rotation_state.json` are advisory state, not permissions."*

> **§15 defect:** `min_ic_threshold=0.005` is the **only** published numeric quality bar; the decay/rotation demotion criteria are entirely unquantified (*"stops working"*, *"dead members"*). Also `_ALLOWED_COLUMNS` permits `returns`, which is forward-derivable if computed over a full frame rather than a trailing window — the allow-list constrains names, not look-ahead semantics.

## 18.4 §16 Research Validation Gates (1947–2036)

> *"Everything here is advisory input to sizing and validation. No gate can place an order; each can only veto or shrink. Their real power: verdicts are read by the deterministic path, so they change live behaviour without the LLM's consent."*

**Walk-forward and decay** (`core/walk_forward_validator.py`):
```
DEFAULT_IC_WINDOW            = 20     # rolling window for IC
DEFAULT_IC_DECAY_THRESHOLD   = 0.02   # IC below this = decaying
DEFAULT_IC_CONFIRM_BARS      = 3      # bars of decay before confirmation
DEFAULT_HALF_LIFE_MAX        = 100    # max half-life in bars
DEFAULT_WFE_ACCEPT_THRESHOLD = 0.5    # WFE = OOS Sharpe / IS Sharpe
STATE_FILE = "walk_forward_state.json"

DecayStatus :90 · ICPoint :98 · DecayReport :113 · WalkForwardResult :144 · WFEVerdict :171
detect_decay :347 · _estimate_half_life :475 · walk_forward_splits :517
walk_forward_efficiency_gate :583 · get_status :629
```
Consumption (`core/risk_manager.py:1066-1101`): `"DEAD"` → size 0 (*"alpha is harmful, all entries blocked"*); `"DECAYING"` → ×0.5; `WFE < 0.5` → `max(0.5, WFE)`; `OOS Sharpe ≤ 0` → 0.

**CPCV and PBO** (`core/cpcv_validation.py`): `CombinatorialPurgedCrossValidator.split(...)` 33 · `calculate_probability_of_backtest_overfitting(...)` 102. Also `core/cpcv_backtester.run_combinatorial_purged_backtest`; runs in the weekly research schedule.

**Overfit gate** (`core/overfit_gate.py`): `BLOCKED_RISK_LEVELS = frozenset({"HIGH","CRITICAL"})` `:34` · `validate_strategy(...)` `:92` · `check_strategy(strategy)` `:270` · `get_passed/rejected_strategies()` `:362`/`:355` · state `overfit_gate_state.json`. Downstream: `CRITICAL` → 0, `HIGH` → **×0.25**.

**Trial ledger** (`core/trial_ledger.py`): `TrialRecord` ~46 · `DSRResult` ~74 · `n_trials()` ~218 · `trial_sharpes()` ~225. Purpose verbatim: *"given how many things I tried, is this Sharpe surprising?"*

**The remaining gates:** sample gate `research/sample_gate.py` · SPRT `core/sprt_strategy_test.py` · progressive gates `core/progressive_gates.py` (init at boot `mt5_trader.py:4157`) · robustness playbook `core/robustness_playbook.py` · research validation `research/research_validation.py` + `research_stress.py` · news-gate A/B `research/news_gate_backtest.py` · capstone `core/capstone_validator.py` (boot `mt5_trader.py:4173`) · backtest parity `research/full_pipeline_backtest.py`, `vbt_engine.py`, `mt5_backtest_engine.py`.

> **Callout (verbatim): "The consumption pattern is the point."** *"Gate verdicts travel to the live path as `toon["walk_forward_status"]`, `toon["overfit_risk_level"]`, `toon["data_drift_status"]` and `toon["walk_forward_wfe"]`. The LLM sees them as context; the gates act on them as law. If a researcher disagrees with a verdict, the correct response is to change the gate's evidence — not to talk the validator out of it."*
>
> **This is exactly the design intent that Gap #5 (§13.1) breaks:** the orchestrator's `market` dict (`:2614-2652`) never sets those four keys.

> **§16 defect:** apparent contradiction between `risk_manager.py` (WFE < 0.5 → clamp to `max(0.5, WFE)`) and `trading_safety.py` (WFE below floor → refuse outright). Resolved in practice because the validator runs first, but the doc never states the precedence. §16 also publishes exact constants for walk-forward and overfit but gives **zero numbers** for the other eight gates.

## 18.5 §17 ML / RL Stack (2037–2098)

> **Warn callout (verbatim): "One sentence frames this whole section: no model here can open, close, resize or re-target a position on its own. Every output is either an advisory input to the prompt or a reduction-only multiplier. Updates are additionally gated by `_rl_learning_allowed()` and `_research_influence_allowed(kind, paper_mode=…)`."*

| Family | Code | Produces |
|---|---|---|
| **Kronos** (foundation time-series) | `research/kronos_bridge.py`, `kronos_mt5_adapter.py`, `kronos_signal_fusion.py`, `kronos_trading_integrator.py`; validator `intelligence/kronos_signal_validator.py` | forecast distribution + confidence → `kronos_risk_adjustment_factor` (**0–1**), a size multiplier + prompt context |
| **PPO / policy gradient** | `research/ppo_trainer.py`, `advanced_rl_pipeline.py`, `rl_trainer.py`, `mt5_finrl_env.py`, `rl_action_bridge.py` | position-management policy (`artifacts/ppo_position_mgmt.json`) + RL state features |
| **Meta-RL** | `research/meta_rl_trainer.py`, `curriculum_scheduler.py`, `replay_buffer.py` | curriculum/learning policy across regimes, trained on closed-trade replay |
| **Contextual bandit** | `research/contextual_bandit.py` | arm selection among candidate setups, updated from realised R |
| **Supervised** | `research/supervised_training.py`, `baseline_research.py`, `training_orchestrator.py` | direction/score models registered with feature-schema version + training-data hash |
| **Meta-labeling** | `core/meta_labeling.py` | second-stage probability that a primary signal is worth taking |
| **Forgetting prevention** | `core/ewc_forgetting_prevention.py` | Fisher-weighted constraints so a new regime does not erase old competence |
| **Evolutionary / GRPO** | `core/evolutionary_optimizer.py`, `research/grpo_proposer.py` (`artifacts/grpo_proposer.json`) | parameter and prompt *proposals*, subject to the influence gate |

**Artifacts:** `core/rl_model.json` (surfaced by `/api/rl-training`) · `artifacts/ppo_position_mgmt.json` · `artifacts/grpo_proposer.json` · `meta_adaptive_state.json` · `regime_duration_state.json` · `artifacts/backtest_results/`.

**Drift, staleness, forgetting:** feature drift `data_drift_status` → `DRIFT` **halves size** and is a validator rejection reason · model staleness `core/model_staleness_tracker.py` + `core/meta_adaptive_engine.py` · catastrophic forgetting EWC Fisher penalties + replay buffers + `research/mars_maml_adapter.py` + `research/laya_checkpoint_parity.py` · behavioural divergence `research/divergence_monitor.py` + `operations/degradation_detector.py` which **can demote the autonomy level**.

> **§17 defect:** only one numeric bound is published in the whole section (`kronos_risk_adjustment_factor` 0–1). PPO hyperparameters, bandit exploration rates, EWC Fisher λ, meta-RL curriculum stages, and staleness TTLs are all absent.

## 18.6 §18 Promotion, Model Registry & Strategy Lifecycle (2099–2178)

**`model_registry` DDL** (`core/trading_safety.py:248-257`, verbatim):
```sql
CREATE TABLE IF NOT EXISTS model_registry (
  id INTEGER PRIMARY KEY, model_key TEXT NOT NULL, version TEXT NOT NULL,
  kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'CANDIDATE',
  feature_schema_version TEXT, training_data_hash TEXT,
  metrics TEXT NOT NULL, artifact_uri TEXT, created_at TEXT NOT NULL,
  reviewed_at TEXT, reviewed_by TEXT, review_note TEXT,
  UNIQUE(model_key, version)
);
CREATE INDEX IF NOT EXISTS idx_model_registry_status ON model_registry(model_key, status, created_at);
```
Three fields make promotion auditable: `feature_schema_version` (which features), `training_data_hash` (which data), `status` (default `CANDIDATE`). CLI vocabulary **`APPROVED | REJECTED | RETIRED | ROLLED_BACK`**.

**The promotion funnel:**
```
CANDIDATE (trained/discovered) → shadow (observes, no orders) → paper (L1 authority only)
            → micro live (L2, 0.25% risk) → full live (L3, operator-signed)
```
| Barrier | Module | Requirement |
|---|---|---|
| Progressive gates | `core/progressive_gates.py` | stage-gated criteria per level |
| Promotion package | `research/promotion_package.py` | bundles metrics, artifacts, evidence |
| Auto-promotion (advisory) | `research/auto_promotion.py` | computes a recommendation; **cannot self-approve live** |
| Model-family gate | `research/model_family_gate.py` | whole-family review instead of per-artifact cherry-picking |
| Research-influence gate | `operations/research_influence_gate.py` | operator approval per model kind |
| Live rollout gate | `operations/live_rollout_gate.py` | signed rollout manifest id for L3 |

> **Callout (verbatim): "the factory may demote its own work, but it can never self-approve live authority. Promotion to paper is automatic; promotion to micro/live is a human act recorded in the journal."**

**Strategy lifecycle:** `operations/strategy_lifecycle.py` (state machine, transitions, freeze/unfreeze/retire + required evidence) · `operations/strategy_factory.py` (candidate generation via sweeps/sprints) · `operations/strategy_evaluation.py` · `research/setup_registry.py`, `setup_governance.py`, `setup_labeling.py` · `strategy_registry.json`. CLI: `strategy-status`, `strategy-transition`, `strategy-unfreeze`, `strategy-retire`, `strategy-tune`, `strategy-acknowledge`.

**Portfolio governance:** `core/hierarchical_portfolio.py` · `core/portfolio_decorr.py` · `research/qubo_rebalancer.py` + `research/risk_parity_engine.py` · `core/alpha_capacity.py` · `operations/account_growth.py` + `core/account_sizing.py`. The **P8 allocation cap is where this touches a single order: it can only reduce volume.**

> **§18 defect (safety-relevant): the funnel and the LEVELS table describe a non-monotonic risk ladder** — paper `max_risk 1.00 %` > micro-live `0.25 %`. §18 never explains this.

## 18.7 §19 Persistence: Databases, Schemas & State Files (2179–2273)

> **Callout (verbatim doctrine): "`trading_state.db` is the authoritative journal. Every other database is operational (agent tasks, events, alerts), and every JSON state file is a cache or policy that can be rebuilt from the journal. Deleting a JSON file loses speed; deleting the journal loses history."**

**The journal** — `TradingStore._init_db` (`core/trading_safety.py:129-293`) with **`PRAGMA journal_mode=WAL`** and thread-local connections:

| Table | Purpose | Key indexes |
|---|---|---|
| `state` | K/V store for all `StoreBackedState` modules — the JSON↔SQLite bridge | PK on `key` |
| `proposals` | every proposal with `approved` + `reasons`, accepted **and** rejected alike | `idx_proposals_setup`, `idx_proposals_symbol_created` |
| `trades` | fills and closes: ticket, magic, strategy, symbol, side, volume, entry, SL, TP, risk amount, PnL, exit reason, payload | `idx_trades_open`, `_symbol_closed`, `_opened_at`, `_setup` |
| `setup_instances` | per-setup lifecycle state machine | 3 indexes on `setup_key`/`setup_id`/state |
| `setup_labels` | auto or reviewed labels with `label_rule` + `review_status` | `idx_setup_labels_label` |
| `manual_activity` | observed-but-unowned activity (foreign magic, manual trades) | `idx_manual_activity_ticket` |
| `trade_lifecycle_events` | append-only transitions with actor + payload | `idx_trade_lifecycle_ticket` |
| `position_observations` | position marks with source, quote time, tick age, spread, `mark_quality` | `idx_position_observations_ticket`, `_quality` |
| `market_ticks` | observed ticks with quality classification | `idx_market_ticks_lookup`, `_quality` |
| `broker_execution_reports` | latency, spread at submit/fill, implementation shortfall, requotes | `idx_broker_execution_intent`, `_position` |
| `daily_reconciliations` | per-period broker-vs-journal verdict | `idx_daily_reconciliations_status` |
| `model_registry` | promotion governance | `idx_model_registry_status` |
| `execution_reviews` | unresolved execution anomalies with severity, retry count, next retry | `idx_execution_reviews_status` |

**Migration-added columns** (additive): `trades.setup_id`, `gross_pnl`, `commission`, `swap`, `financing` · `position_observations.source`, `quote_time_msc`, `tick_age_ms`, `spread`, `mark_quality` · `broker_execution_reports.execution_latency_ms`, `spread_at_submit`, `spread_at_fill`, `implementation_shortfall`, `requote_count` (`:269-293`).

**Operational databases:**

| Database | Owner | Tables |
|---|---|---|
| `agent_operations.db` (93 KB) | `operations/agent_operations.py` | `agent_runs` 104, `operational_events` 109, `agent_tasks` 119, `agent_task_edges` 125, `llm_requests` 132, `order_intents` 141, `setup_labels` 146, `execution_reviews` 152, `memory_outbox` 162, `data_snapshots` 168, `configuration_versions` 178, `raw_traces` 187, `working_memory` 197, `agent_schedules` 208, `clock_observations` 213 |
| `agent_events.db` | `operations/event_bridge.py` | append-only event stream for telemetry + replay |
| `alerts_outbox.db` | `operations/alert_outbox.py` | enqueue → claim → ack → deliver, at-least-once |
| `meta_corpus.db` | meta-loop | corpus + run artefacts |
| `restart_recovery_smoke.db` | test artefact | **not production state** |

**`StoreBackedState`** (`core/state_persistence.py`): modules declare a `store_key`; state persists in the journal's `state` table, with atomic file writes (`core/file_utils.safe_atomic_write_json`) as the on-disk mirror. `state_manager.migrate_json_states_to_store()` runs at boot (`mt5_trader.py:3844-3846`).

| File | Written by | Drives |
|---|---|---|
| `autonomy_state.json` | `operations/autonomy.py` (`store_key="autonomy_state"`) | level, halt/pause flags, circuit history |
| `supervisor_state.json` | `operations/supervisor.py` | phase, component health, principal freshness, degradation |
| `risk_state.json` / `risk_state_demo.json` | `core/risk_manager.py` | streaks, daily/weekly equity anchors, halt flags — always mirrored to the store |
| `control_plane.jsonl` | `issue_control`/`drain_controls` | append-only operator command queue; **last 500 records retained** |
| `walk_forward_state.json`, `overfit_gate_state.json`, `alpha_decay_state.json`, `alpha_discovery_state.json`, `alpha_rotation_state.json` | §15-§16 engines | decay status, gate verdicts, discovery/rotation progress |
| `strategy_registry.json` | loader / `strategy_factory.py` | declarative strategy catalogue |
| `market_state.json`, `scanner_state.json` | scanner | ranked opportunities for deck + API |
| `meta_adaptive_state.json`, `regime_duration_state.json`, crash-detector state, `self_improvement_state.json` | §14, §17 | adaptation, regime dwell, crash markers, improvement progress |
| `api_cache.json` | data layer | HTTP provider responses with TTLs |
| `ζ_code_versions.json` | ζ self-improvement | versioned code-state snapshots |
| `trade_conditions.json`, `trade_manager_state.json` | `core/trade_manager.py` | programmatic exit conditions, partial-exit/pyramid state |

> **Warn callout (verbatim): "Two ledgers, one truth."** *"The JSON files and the journal can momentarily disagree after a hard kill. The system resolves this in a fixed direction: reconciliation compares broker → journal, and the JSON mirror is refreshed from the store on the next write. If a JSON file is deleted, the state is rebuilt from `state`; if the journal is damaged, `journal_audit.py` and the encrypted backups are the recovery path."*

> **§19 defects (the most substantive found):**
> 1. **Table-name collision across three stores.** `setup_labels` is declared in the journal, declared *again* inside `agent_operations.db` (~146), and listed a *third* time among `TradingMemory`'s cognitive tables. `execution_reviews` is likewise in both the journal and `agent_operations.db` (~152). Nothing states which one is read first or how divergence is resolved. **A genuine unresolved ambiguity.**
> 2. **Naming asymmetry:** three different anchors for `intelligence/trading_memory.py` (~51-170, 41-170, 41-1526) across §19 prose, §19's index, and §27.
> 3. **Greek-letter filename** `ζ_code_versions.json` is a real on-disk path and a Windows-encoding hazard; §21 (security) and §25 (gaps) never mention non-ASCII path handling.
> 4. **Retention asymmetry:** `control_plane.jsonl` keeps the last 500 records, so an operator action older than 500 pulses (~42 min at 5 s) leaves no trace of *intent* — §22's promise that the deck "can show not just 'paused' but who paused it, when, and from which source" silently expires.
> 5. `restart_recovery_smoke.db` is listed in a production schema inventory.

## 18.8 §20 Autonomous Operation (2274–2384)

*"This section answers the question an operator actually asks: what happens when nobody is watching?"*

**Supervisor kernel** (`operations/supervisor.py`): `phase()` 96 · `transition(target, reason)` 100 · `boot_sequence(checks)` 115 · `ready()` 139 · `note_component(name, ok, detail)` 147 · `heartbeat()` 152 · `note_principal(name, ok, detail)` 157 · `stale_principals()` 170 · `principal_freshness()` 185 · `degrade()`/`recover()` 197/204 · `pause()` · `request_shutdown()`/`complete_shutdown()` 216/220 · `overall_health()` 227.

The cycle notes principals explicitly — `MarketModel` when a decision is proposed, `RiskController` when validation passes or fails (`llm_orchestrator.py:7140-7151`) — so *"silence" stays distinguishable from "refusal".*

**Autonomy levels and the control plane:**
```
LEVELS (operations/autonomy.py :38-43)
  L0_SHADOW  rank 0  "SHADOW"      max_risk 0.00%  allows_live False
  L1_PAPER   rank 1  "PAPER"       max_risk 1.00%  allows_live False
  L2_MICRO   rank 2  "MICRO LIVE"  max_risk 0.25%  allows_live True
  L3_FULL    rank 3  "FULL LIVE"   max_risk 1.00%  allows_live True   # needs signed rollout manifest

CONTROL_ACTIONS (:26)  pause · resume · flatten · killswitch
transport            control_plane.jsonl (append → mark consumed → keep last 500)
defaults              level = L1_PAPER, halted = False, paused = False
```
`trading_allowed()` is consulted at the top of every cycle and the queue drained at the same point, so a pause issued seconds earlier is honoured immediately; **each drained record emits `CONTROL_EXECUTED`**.

**Circuit actions** (`CIRCUITS` ~189 evaluated by `evaluate_circuits(metrics)` ~202):

| Action | Effect | Line |
|---|---|---|
| `HALT` | `halted=True`, `halt_reason="circuit:<key>"` — no new cycles until an operator resumes | 215-217 |
| `PAUSE_NEW_CYCLES` | `paused=True` with reason | 218-220 |
| `DEMOTE` | level rank − 1 (e.g. L2 → L1 paper) when above L0 | 221-223 |
| `DEGRADE_HOLD_ONLY` | `flags.hold_only=True` — keep observing and journalling, propose nothing | 225-226 |

Every trip records a **`CIRCUIT_TRIP`** event with key, severity, action and observed value.

**Guardians, watchdog, recovery:** Ψ External Guardian (out of process, polls the broker directly, **120 s heartbeat watchdog**, started after bootstrap) · `ExecutionSafetySupervisor` (`record_heartbeat` 62, `update_equity` 66, `validate_pre_trade_safety` 114, `trip_circuit_breaker` 153, **`reset_circuit_breaker(authorization_token)` 160 — clearing a breaker requires an explicit token, so it cannot be reset by accident**) · `operations/degradation_detector.py`, `health_alerts.py`, `system_doctor.py` · `core/crash_recovery.py` + `operations/restart_recovery.py` · `data/kill_switch_latch.py`.

**One unattended day (paper mode), verbatim:**
```
00:00  process starts → boot gates → bootstrap → loop begins
       every 5 s   pulse: autonomy gate · maintenance prefix · schedules · candle lease?
                    new completed bar → ingest → quality → news → features → LLM → gates
       every 10th  autonomous research tick (discovery / registration / lifecycle)
       hourly      slow-lane memory distillation (limit 3) → L1 facts, narratives
       session end _execute_session_review
       daily       _execute_daily_review → daily narrative, reconciliation, labels
       weekly      _execute_weekly_research → weekly narrative, core consolidation, CPCV/WF
       monthly     memory maintenance + archive of stale candidates
       continuous  circuits evaluated every pulse; demote / pause / halt as needed
       on holiday  no analysis; maintenance, health and kill-switch continue
```

> **Callout (verbatim): "What 'perfect autonomy' means here."** *"The process never dies on a transient LLM or MT5 fault; every HOLD and rejection is journaled; maintenance runs without a new candle; schedules actually execute and write their `last_*` state; the factory may demote but never self-approve live; and the deck shows the same journal the loop writes."*

> **§20 defects:**
> 1. 🔴 **The circuit table is the single biggest disclosure hole in the PRD.** §20.3 defines the *schema* `{key, metric, op, threshold, severity, action}` and then lists only the four **actions** — the actual circuits (keys, metrics, operators, numeric thresholds, severities) are **never enumerated**. §24's trace section then says "daily_loss ≥ threshold" and "drawdown ≥ threshold" without values. **A reader cannot audit or configure the autonomy safety envelope from this document.**
> 2. 🔴 **Non-monotonic risk ladder:** L1 paper `max_risk 1.00 %` > L2 micro-live `0.25 %`. A `DEMOTE` from L2→L1 therefore **increases** permitted risk by 4× while reducing authority. The most safety-relevant internal contradiction found.
> 3. **`DEGRADE_HOLD_ONLY` vs `DEMOTE` are not ordered.** If a mild degradation and a drawdown trip in the same pulse, which wins is undefined.
> 4. Anchor arithmetic: `LEVELS (:38-43)` is 6 lines for 4 records of 4-5 attributes — impossible; more plausibly ~24 lines.

## 18.9 §21 Operations, Audits, Security & Compliance (2385–2457)

> **Structural defect:** the fragment's body order is **21.1 → 21.3 → evidence panel → `</section>` → 21.2**. §21.2 is orphaned *outside* the section element, the evidence index sits between 21.3 and 21.2, and the auto-TOC renders §21 as **21.1 → 21.3 → 21.2**. There is no 21.4.

**Operator CLI:** `python mt5_trader.py journal <action>` — **~120 actions** (`mt5_trader.py:1560-1573`) across families: reconciliation & integrity (`journal-audit`, `reconciliation`, `reconciliation-run --start --end --paper`, `legacy-quarantine`, `execution-reviews`, `resolve-execution-review`) · readiness & approval (`readiness-status`, `live-approval`, `auth-status`, `scope-status`/`scope-init`, `research-influence-status`/`-approve`/`-revoke`) · backups (`backup-status/create/restore/prune`, `encrypted-backup-*`, `db-maintenance`, `housekeeping`) · security (`security-status`, `incident-drills`) · research & models (`validation-runs`, `promotion-package`, `promotion-verify`, `model-register/review/drift/status`, `model-family-status`, `model-activation-status`) · memory (`daily/weekly/monthly-memory`, `working-memory-*`, `setup-labels`, `macro-extraction-status`) · alerts (`alerts`, `alerts-enqueue`, `alerts-status`, `alerts-claim`, `alerts-ack`, `alerts-recover`, `alerts-deliver`) · strategy & policy (`strategy-status/transition/unfreeze/retire/tune/evaluate/acknowledge`, `policy-status/candidate/approve`, `macro-prune`).

**Journal integrity audit** (`operations/journal_audit.py`) returns `{"database","exists","passed","checks":[...]}`. Verbatim check names:

| Check | What it proves |
|---|---|
| `database exists` / `required tables` | the journal is present and complete |
| `unique tickets`, `unique order intents` | no double-recorded fills or intents |
| `labels reference trades`, `non-trade setup labels are attributable`, `setup-label review states are valid` | labels are grounded and their review state is legal |
| `all trades tagged`, `closed trades have outcome`, `trades have feature payload` | every trade is attributable, closed with an outcome, reproducible |
| `new proposals carry versioned artifacts`, `new trades have deterministic setup ID` | evidence and setup identity accompany new work |
| `order intents resolved or reviewable` | nothing is silently stuck |
| `point-in-time snapshots have payload and hash` | features are reconstructible and untampered |
| `verified memories have source links` | beliefs cite their evidence |
| `lifecycle events are attributable`, `broker execution reports are attributable` | every transition and fill has an actor |
| `position observations have valid marks`, `new position marks have source and quality`, `market tick observations preserve quality` | marks are usable for management and MFE/MAE |
| `daily reconciliations are attributable`, `setup instances have valid deterministic states` | reconciliation and lifecycle state are consistent |

**Backups** — `operations/journal_backup.py`: **AES-256-GCM** encrypted backups with restore and prune (`--keep-backups`, **dry-run by default**, explicit `--apply`, **non-root destination guard**).

**Security** — `operations/security_audit.py` result disk-cached (`.security_audit_cache.json`) and **prefetched asynchronously so paper boot is not blocked**. `security_scrub.py` + `secrets_guard.py` handle redaction. `docs/SECURITY.md` is the policy.

**Alerts** — `alert_outbox.py` + `alert_rules.py` + `alert_delivery.py`: enqueue → claim → ack → deliver in `alerts_outbox.db` (at-least-once; unacked alerts recoverable via `alerts-recover`).

**Readiness** — `readiness_report.py` aggregates every gate into a single operator verdict; `incident_drills.py` rehearses `docs/INCIDENT_RUNBOOKS.md`.

---

# PART 19 — THE 163-ITEM TASK LIST & THE 12-AGENT REGISTRY

## 19.1 The UAQP 12-agent registry (verbatim, `docs/UAQP_STATE_TENSOR.md:517-530`)

⚠ **The table has 12 data rows, not 13.** The "13-agent registry" claim has no 13th row.

| # | Agent | Symbol | Module | Reads | Writes | Trigger |
|---:|---|---|---|---|---|---|
| 1 | **Alpha Generator** | α | `core/alpha_discovery.py` | S.Ω, S.M, I(t) | S.H | Nightly / decay > threshold |
| 2 | **Data Steward** | δ | `data/market_data_provider.py` | S.H, U, T | S.D | Per-cycle |
| 3 | **Signal Engineer** | σ | `core/alpha_ensemble.py` + `core/signal_orthogonalizer.py` | S.D[h], F | S.F | Per-cycle |
| 4 | **Validator** | β | `core/cpcv_backtester.py` + `core/walk_forward_validator.py` + `core/overfit_detector.py` | S.F[h], Π | S.B | When σ produces new signal |
| 5 | **Risk Guardian** | ρ | `core/risk_manager.py` + `core/trading_safety.py` | S.B[h], S.P, S.M | S.R | When β passes |
| 6 | **Execution Engine** | ε | `core/order_execution.py` + `core/trade_manager.py` | S.R, market | S.P | When ρ approves |
| 7 | **Monitor** | μ | `core/alpha_decay_monitor.py` | S.P, S.B, S.M | S.Ω | Per-cycle |
| 8 | **Orchestrator** | ω | `intelligence/llm_orchestrator.py` | S(t) full | S.Q | Priority-based |
| 9 | **Guardian** | Ψ | `core/external_guardian.py` | B(t) broker API | ARMED/TRIPPED | Independent Δt_guardian |
| 10 | **Code Safety** | ζ | `core/code_safety_agent.py` | generated code | APPROVED/REJECTED | When σ generates code |
| 11 | **Compliance Audit** | κ | `core/compliance_audit.py` | every transition | immutable log | Unconditional |
| 12 | **Meta-Loop** | — | `core/meta_loop.py` | S.B, S.Ω, outcomes | Δpriors | Monthly |

**Registry conflicts found in the same document:** the Appendix (`:734`) assigns `core/signal_orthogonalizer.py` → **"ψ"**, colliding with **Ψ = External Guardian** — two different agents share one glyph. Meta-Loop carries symbol "—", so Γ's 13-slot agent space is 12 populated + 1 unnamed. `S.Ξ` has **no registry owner row** other than β (append-only, no decrement).

## 19.2 The merged task list — status legend and counts

**Legend (verbatim, `docs/TRADING_SYSTEM_MASTER_TODO.md:13-17`):** `[x]` Implemented and locally verified · `[~]` Partially implemented; acceptance evidence still required · `[ ]` Not implemented · `[!]` External/empirical blocker; cannot be completed honestly in code alone · `[N/A]` Intentionally excluded by a later decision.

**Verified counts:** `MASTER_TODO` = **75 `[x]` + 88 `[~]` + 0 `[ ]`**, plus 2 `[!]` and 4 `[N/A]`. The doc's own text claims "74 `[x]`, 88 `[~]`" — **off by one**. `REMAINING_TASKS` = **11 `[x]` + 80 `[~]` + 0 `[ ]`** across sections A–K. **Combined = 254 checkbox markers.**

**Six non-negotiable rules** (`:21-33`, verbatim intent): local SQLite/Postgres journal is the source of truth; the LLM is never the journal/risk/order-permission layer · LLMs only propose or summarise · every new order is planned before submission (entry type/price, expiry, SL, TP, cost tolerance, risk, event state immutable in the order intent) · missing/stale critical data = no new trade · HMM/GARCH/Kelly/supervised/bandits/RL advisory until promotion gates pass · external cloud/cloud memory is `[N/A]`.

### GOAL scorecard

| Goal | Area | `[x]` | `[~]` | `[!]` | `[N/A]` | Acceptance statement (verbatim intent) |
|---|---|---:|---:|---:|---:|---|
| **0** | Credential, terminal, release safety | 25 | 1 | — | — | *account-info connects to the intended demo account, secrets are not tracked, and live safety tests show a kill switch prevents order submission* |
| **1** | Broker + data-quality preflight | 13 | 6 | — | — | *a preflight report lists exact broker constraints per symbol and every corrupted/stale/missing critical input deterministically produces `NO_TRADE`* |
| **2** | Point-in-time market + feature journal | 3 | 5 | — | — | *any accepted or rejected decision can be replayed from a single immutable snapshot without using future data* |
| **3** | Planned execution + microstructure protection | 3 | 6 | — | — | *every entry has a pre-existing immutable intent and an auditable cost/slippage comparison; no trade can chase a materially moved price* |
| **4** | Deterministic proposal permission + portfolio risk | 2 | 7 | — | — | *every rejection is replayable with exact failed rules and no LLM output can bypass hard portfolio or broker constraints* |
| **5** | Complete trade lifecycle + reconciliation | 5 | 6 | — | — | *journal totals match the broker statement and every ticket has one lifecycle, one owner, one final outcome, and no unresolved stale records* |
| **6** | Continuous agents, scheduling, rate budgets | 5 | 7 | — | — | *restart/reconnect tests demonstrate no duplicate analysis/order, and every task is resumable or explicitly failed for review* |
| **7** | Memory layers + local LLM distillation | 6 | 10 | — | — | *every retrieved claim has source IDs, date, confidence, outcome distribution, and cannot modify permissions, risk, or order execution* |
| **8** | Macro research, causal graph, event playbooks | 1 | 9 | — | — | *a live macro thesis cites current data and dated historical precedents, displays similarities/differences, and declares uncertainty* |
| **9** | Labels, metrics, dashboards, pattern discovery | 2 | 9 | — | — | *no performance figure can be read without its sample, cost model, date range, regime/setup segmentation, and data-status qualification* |
| **10** | Supervised learning, bandit, RL, promotion | 5 | 10 | — | — | *a model beats baselines out-of-sample in repeated walk-forward tests and is promoted only to a capped confidence/size adjustment* |
| **11** | Validation, backtesting, rollout | 2 | 7 | — | — | *every promoted strategy has a reproducible validation package and forward-demo evidence consistent with the cost-aware backtest* |
| **12** | Security, operations, data governance | 1 | 7 | — | — | *recovery and security drills are documented and repeatably pass* |
| — | **external cloud / cloud memory** | — | — | — | **N/A** | *replaced by local SQLite + existing LLM provider* |
| **TOTAL** | | **73** | **88** | **2** | **4** | |

### The current completion snapshot (`:890-900`, verbatim)

| Area | Status | Blocking evidence |
|---|---|---|
| Core safety and deterministic gating | `[~]` | Broker-specific policies and full validation trace remain |
| Durable local journal/operations | `[~]` | Complete lifecycle and current legacy reconciliation remain |
| Local LLM memory | `[~]` | L0/L1/L2/L3 governance and local hybrid retrieval remain |
| Macro/event research | `[~]` | Ingestion, causal graph, precedent data, event studies remain |
| HMM/GARCH/Kelly/RL | `[~]` | Advisory code exists; empirical promotion data absent |
| Backtest/demo/live validation | `[!]` | Sufficient closed outcomes and authenticated forward-demo evidence absent |
| external cloud/cloud memory | `[N/A]` | Replaced by local SQLite + existing LLM provider |

**Definition of "finished" (`:918-923`, verbatim):** *"code portion finished only when every non-external checkbox is `[x]` and automated/replay tests pass; trading system finished only when external gates are met (authenticated MT5 demo operation, clean reconciliation, sufficient labelled sample, realistic out-of-sample validation, forward-demo agreement, explicit operator approval). A checklist item is never marked complete merely because an LLM generated a plausible explanation."*

**Acceptance-evidence commands (`:904-914`):** `journal integrity`, `journal metrics`, `journal dashboard-status`, `journal operations`, `journal memory-status`, `journal research-status`, `journal policy-status`, `journal auth-status`, `kill-switch status`.

**`REMAINING_TASKS.md` section map (execution-order view of the same `[~]` items):** A. Immediate safety and broker reality (7) · B. Data quality and anti-manipulation protection · C. Setup library and improvement loop · D. Full lifecycle, costs, reconciliation · E. Persistent runtime and task orchestration · F. Local memory completion · G. Macro research and causal knowledge · H. Metrics, labels, dashboards · I. Research, ML, promotion · J. Backtest, demo, funded rollout · K. Security and operations. Totals 11 `[x]` + 80 `[~]`.

**Requirements traceability (15 rows, `:936-950`):** Phase 0 data-quality gate → Goals 1-2 · API tiers/circuit breaker/latency → Goals 1, 6 · persistent state/schema/position registry/narrative memory → Goals 2, 5, 6, 7, 12 · deterministic validator + risk engine/volatility sizing → Goals 3-4, 10 · planned entry/limit-stop/broker gateway/slippage-commission-swap → Goals 3, 5 · monitoring/kill switch/terminal-API health → Goals 0, 6, 12 · swing/scalp engines + shared arbiter → Goals 4, 6 · liquidity pools/stop-hunt-sweep/DOM/session-macro filters → Goals 1-3, 8 · L0-L3/persona/skills/wiki/code graph/access controls → Goal 7 · semantic/episodic/structural macro memory + precedents → Goals 7-8 · MacroAnalyst/TechnicalNet/MarketModel/RiskController/QuantitativeCoach → Goals 6, 8, 9 · full operations journal + all metrics → Goals 2, 5, 9 · HMM/GARCH/Kelly/Optimal-f/ensemble/Supervised/Bandit/RL → Goal 10 · purged walk-forward/Monte Carlo/realistic backtest/demo-live rollout → Goal 11 · external cloud/cloud memory → Goal 7 `[N/A]`.

> **⚠ The `[~]` marker is the doc's dominant state — 88 of 163 items (54 %).** Verification found **7 of 21 spot-checked `[~]` items are actually complete** (§13.9), so the true `[~]`→`[x]` conversion rate is higher than the doc claims. But the doc's own honesty rule is correct and should be preserved: `[~]` means *acceptance evidence still required*, not *code missing*.

---

# PART 20 — THE 5 ARCHITECTURE DIAGRAMS (with code reality checks)

The `.json` files are canonical; the `.html` renderings are ~99 % renderer boilerplate (14.5 k lines each, 790–800 KB) with a verbatim embedded `<script id="archify-*-data">` copy of the source JSON. **No information is lost by skipping the HTML.**

## 20.1 `01_master_architecture.architecture.json` — "Autonomous Trading System Master Architecture"

**12 components:** `market_feeds` (external, ticks+depth+news) · `mt5_gateway` (security, broker bridge, "broker edge") · `pipeline_harness` (backend, **"11-stage tick engine"**, "fast path") · `risk_guardian` (security, **"Risk Guardian (Ψ)"**, "deterministic") · `trading_context` (backend, DI+Disposables) · `deliberative_graph` (backend, LangGraph L2 agent) · `execution_router` (backend, TWAP/VWAP, "low latency") · `state_tensor_db` (database, SQLite/Parquet) · `meta_rl_sandbox` (cloud, **"Meta-RL Optimizer — PEARL + RL²-PPO L3"**) · `compliance_ledger` (database, **"Compliance Ledger (κ)"**, hash-chained) · `crash_recovery` (security, Logic-Q state replay) · `telemetry_obs` (external, OTLP + Prometheus).

**2 boundaries:** region **"Autonomous Quant Platform Runtime"** (wraps 10, excludes market_feeds/mt5_gateway) · security-group **"Real-Time Execution Boundary (Hot Path)"** (wraps pipeline_harness, risk_guardian, trading_context, execution_router).

**3 guided views:** `tick-to-execution` · `reasoning-and-context` · `learning-and-resilience`.

**11 connections:** market_feeds→mt5_gateway (*ticks*) · mt5_gateway→pipeline_harness (*IPC*) · pipeline_harness→risk_guardian (*risk check*, security) · pipeline_harness→trading_context · trading_context→deliberative_graph · risk_guardian→execution_router (*approved*) · execution_router→compliance_ledger (*audit*, security) · trading_context→state_tensor_db (*sync*, dashed) · state_tensor_db→crash_recovery (*replay*) · deliberative_graph→meta_rl_sandbox (*rollout*, dashed) · meta_rl_sandbox→telemetry_obs (*metrics*, dashed).

**3 cards (verbatim):**
- cyan **"Deterministic Safety Boundary"** — "External Guardian (Ψ) executes hard-coded circuit breakers outside LLM context" / "Stateless LLM proposals are bounded by `R ∘ V_LLM ∘ S` invariant gates" / "Cryptographic ledger (κ) maintains tamper-evident SHA-256 state chain"
- emerald **"Dual-Velocity Execution"** — "L1 Hot Path: Sub-millisecond tick processing and order routing" / "L2 Warm Path: LangGraph deliberative decision graphs and reflection cycles" / "TradingContext provides scoped DI, lifecycle bounds, and sandbox capability tokens"
- rose **"Self-Improvement & Resilience"** — "L3/L4 Cold Path: Offline Meta-RL policy updates with EWC forgetting prevention" / "Logic-Q crash recovery reconstructs transactional state tensors post-failure" / "Continuous parameter stability sweeps safeguard against overfitting and regime cliffs"

> **⚠ Topological defect:** `market_feeds → mt5_gateway` is the only inbound edge; `compliance_ledger`, `crash_recovery`, `telemetry_obs` are all sinks. **No edge returns from `execution_router` to the pipeline** — the diagram is a one-way DAG with no feedback loop drawn, despite the L1/L2/L3/L4 velocity claim in its cards.

**Code reality check:**

| Key name | Verdict | Evidence |
|---|---|---|
| `TradingContext` | **MATCH** | `core/harness/kernel.py:30`; exported `__init__.py:34`; `create_child`/`plugin`/`provide` at `:146`/`:116`/`:51` |
| `Deliberative Graph` | **MATCH** | `core/harness/plugins.py:302` `DeliberativeGraphPlugin`; mounted `mt5_trader.py:2967` |
| `State Tensor Store` | **MATCH** | `core/graph_state_persistence.py:61` `StateTensorSnapshot`, `:587` `StateTensorCollector`, `:856` `restore_last_state()`; hash chain H(prev_hash‖payload) at `:580` |
| `Meta-RL Optimizer` (PEARL) | **MATCH** | `core/auto_quant_tools.py:75` `"meta_rl_train": 4032  # bi-weekly`; `research/meta_rl_trainer.py` `MetaRLTrainer` |
| `Compliance Ledger (κ)` SHA-256 | **MATCH** | `core/compliance_audit.py:117` `AuditEntry`, `:141` `sha256(content)[:20]` "chained with prev_hash", `:144` `verify_chain_hash`, `:169` `ComplianceAudit` |
| `Risk Guardian (Ψ)` | **MISMATCH (rename)** | real class is `ExternalGuardian` — `core/external_guardian.py:213`; with `GuardianThresholds:57`, `GuardianState(Enum):97`, `TripSeverity(Enum):103`, `TripRecord:116`, `GuardianAuditLog:137`, `BrokerSnapshot:196`, `get_guardian:667`, `start_guardian:676`, `stop_guardian:683`. **No `RiskGuardian` symbol exists.** |
| `Logic-Q` | **MATCH** | `core/crash_detector.py:1` "Logic-Q inspired Neuro-Symbolic Crash Detector"; `core/auto_quant_tools.py:56` `"crash_detector": 1` |
| `Crash Recovery` | **MATCH** | `core/crash_recovery.py:92` `CrashRecoveryManager`, `:346` `startup_recovery()`, `:352`/`:358` checkpoints |
| `Execution Router` TWAP/VWAP | **MATCH** | `core/optimal_execution.py:158` `twap_schedule`, `:224` `vwap_schedule`; `core/urgency_sor.py:5` `U = sigma_inst*(|OFI|/Depth)*exp(-lambda*t)` |
| `Telemetry OTLP + Prometheus` | **MATCH (Prometheus) / OTLP NOT-FOUND** | `core/prometheus_exporter.py:1` "Prometheus Metrics Exporter" (`:8` `--port 9090`); `core/metrics_exporter.py:1`. **Zero matches for "otlp" anywhere.** |
| `market_feeds` / `mt5_gateway` | **NOT-FOUND as named components** | MT5 access exists via MCP tools + `data/market_data_provider.py`, but no class is named `MT5Gateway` or `MarketFeeds` |
| `RL²-PPO` | **NOT-FOUND** | No `RL2`/`RL^2` string. PPO appears only as a literature reference. |

## 20.2 `02_tick_pipeline_v2.workflow.json` — "11-Stage High-Velocity Tick Processing Pipeline"

**4 lanes:** `ingest` Market & Ingestion · `pipeline` Pipeline V2 Stages · `guardian` Risk Guardian (exception) · `execution` Execution & Journaling.
**3 phases:** `maintenance` **"Maintenance Stages (01-05)"** · `envelope` **"Envelope Stages (06-08)"** · `finalization` **"Finalization Stages (09-11)"**.
**3 groups:** `prep_group` Feature Preparation · `guardian_group` Risk Gate · `exec_group` Execution & Audit.
**10 nodes:** `raw_ticks` → `01. Cleanse` (NaN/Inf scrub) → `03. Indicators` (EMA/ATR/Vol) · `04. Feature Sync` (Rolling buffer, branch) → `06. Signal Engine` (Quant Workflows, tag `alpha`) → `07. Risk Guardian` (**Ψ Invariant Gates**, tag `hard stop`) → `Circuit Halt` (Blocked/Alert, tag `quarantine`) · `08. Exec Router` (TWAP/VWAP slices) → `09. Order Dispatch` (MT5 OrderSend) → `10. State Journal` (SQLite WAL Commit).
**mainPath:** the 8 main-path nodes (stage_04_features is a branch off it).
**9 edges** incl. `e-cleanse-features` (*sync*, dashed), `e-signal-guardian` (*eval*, security), `e-guardian-exec` (*pass*), `e-guardian-reject` (*halt*, security, **role: error**), `e-dispatch-journal` (*commit*).

> **⚠ INTERNAL DEFECT:** title and all three phase labels claim **11 stages**, but only **8 numbered stages** exist, numbered `01, 03, 04, 06, 07, 08, 09, 10`. **Stages 02, 05, and 11 are entirely absent.**
> **⚠ Dead-end:** `risk_vulnerability` "Circuit Halt" has **no outgoing edge** — not wired back to the journal, contradicting the card claim *"Replay journals record both approved and rejected intent for post-incident RCA."* `stage_04_features` is also a terminal sink.

**Code reality check — the pipeline stage names do not exist:**
`core/harness/pipeline_v2.py` (951 lines) defines **17 `StageSpec`s in 3 groups**:
- **Maintenance (6)** `:274` "the six maintenance steps of every pulse": `velocity.poll`, `reconcile.pending`, `reconcile.open`, `memory.dispatch`, `position.tick`, `worker.heartbeat`
- **Envelope gates (4)** `:735` `_envelope_gate_stages`: `plugins.pipeline` (RiskAgent), `macro.analyst` (MacroAnalyst), `blackout.gate` (EventAgent, **fallback=`abort_cycle`**), `portfolio.consult` (PortfolioAgent)
- **Analysis (7)** `:416` `analysis_stages`: `analysis.context` (QuantitativeCoach), `analysis.data` (TechnicalNet), `analysis.causal` (MacroAnalyst), `analysis.llm` (MarketModel), `analysis.validate` (RiskController), `analysis.size` (RiskController), `analysis.execute` (RiskController)

**Zero string matches** for "01 Cleanse", "06 Signal Engine", "07 Risk Guardian", "08 Exec Router", "09 Order Dispatch", "10 State Journal".

| Key name | Verdict |
|---|---|
| "11-Stage Pipeline V2" | **MISMATCH — hard** (17 specs, 3 groups, entirely different names) |
| `SQLite WAL Commit` journal | **MATCH** — `pipeline_v2.py:500-502` reads `order_intents` read-only from `trading_state.db` `:479`; WAL write path in `operations/journal_*.py` |
| Risk Guardian / hard stop | **MISMATCH (rename) + MATCH (behavior)** — `blackout.gate` has `fallback="abort_cycle"` `:751-755`; `request_skip_maintenance()` `:822` |
| `MT5 OrderSend` | **MATCH** |
| TWAP/VWAP slices | **MATCH** |
| `NaN / Inf scrub` | **PARTIAL** — nearest analogue is the `fetch → quality gates` step at `:429`, not named cleanse |
| `SQLite WAL` explicit | **PARTIAL** — DB access is `mode=ro`; WAL write path is not a numbered stage |

## 20.3 `03_fast_execution_loop.sequence.json` — "Fast Execution Loop (L1) Sequence"

**6 participants:** `broker` MT5 Broker (market stream) · `pipeline` Pipeline Engine (Harness V2) · `guardian` Risk Guardian (Ψ, hard invariants) · `router` Execution Router (TWAP/VWAP) · `ledger` Compliance (κ, SHA-256) · `telemetry` Telemetry Sink (Prometheus OTLP).
**3 time-gated segments:** Tick Intake & Signal (150-240) · Risk Gate & Broker Routing (245-370) · Ledger Audit & Telemetry (375-475).
**10 messages:** tick arrived → verify risk invariants (**security**) → **invariants confirmed** (**return**) → submit slice order → **`OrderSend(symbol, lots)`** → order fill receipt (**return**) → execution confirmed (**return**) → append block hash (SHA-256) (**security**) → ledger receipt (**return**) → emit L1 latency metric (**dashed**).
**5 activations:** pipeline 160-455 · **guardian 190-222 (32 px — the gate)** · router 255-355 · ledger 390-425 · telemetry 448-465. `broker` has **no activation bar**.

> **⚠ No branch/alt paths are encoded.** Unlike diagram 02 there is **no `alt` block for guardian rejection** — a fail-closed reject would still advance `msg-order-slice`, because sequence JSON here has no condition/guard field on messages at all. **This diagram does not depict the fail-closed path it claims to.**

**3 cards (verbatim):** emerald "Sub-Millisecond Critical Path" (*"Average tick-to-order dispatch latency strictly budgeted under 5ms"*) · rose "Deterministic Safety Intercept" (*"Non-compliant orders fail closed with zero wire activity"*) · orange "Asynchronous Telemetry Isolation".

**Code reality check:**

| Key name | Verdict | Evidence |
|---|---|---|
| `OrderSend(symbol, lots)` | **MATCH** | |
| `Pipeline Engine / Harness V2` | **MATCH** | `pipeline_v2.py:783` `envelope_stages()`; `:794` `run_envelope(..., autonomy_level: str = "L1_PAPER")` — **"L1" is literal in code**; env kill-switch `PIPELINE_V2_LEGACY_BODY` |
| `Compliance (κ)` SHA-256 | **MATCH** | `core/compliance_audit.py:141`/`:144` |
| `Execution Router` TWAP/VWAP | **MATCH** | |
| `Risk Guardian (Ψ)` | **MISMATCH (rename)** | `ExternalGuardian` |
| `Prometheus OTLP` | **MISMATCH (protocol)** | implements Prometheus textfile + `:9090` pull, **not OTLP push**; `"otlp"` = 0 matches |
| "memory-mapped state tensors" | **NOT-FOUND** | `graph_state_persistence.py` uses SQLite + dict/JSON, no mmap |
| "sub-millisecond", "<5 ms budget" | **MISMATCH** | declared SLAs are 500–45,000 ms: `velocity.poll` 5000, `reconcile.pending` 10000, `position.tick` 30000, `analysis.llm` 45000, `analysis.execute` 45000, `legacy.cycle_body` 45000. Fastest gate is `blackout.gate` **500 ms**, `portfolio.consult` 800 ms |

## 20.4 `04_alpha_discovery_dataflow.dataflow.json` — "Alpha Factor Discovery & Causal Feature Pipeline"

**5 stages:** 0 Sources → 1 Intake → 2 Mining → 3 Validation → 4 Production.
**10 nodes:** `mkt_bars` (M1/M5 Bars OHLCV) · `order_flow` (VPIN/OFI stream, tag `micro`) · `norm_features` (Feature Engine, z-score + fracdiff) · `lookahead_guard` (AST leakage check, tag `gate`) · `factor_miner` (**Ralph Loop DSL**, tag `generator`) · `causal_filter` (**Granger / ABSO**, tag `prune`) · `orthogonalizer` (**Gram-Schmidt ψ**, tag `orthogonal`) · `factor_registry` (**IC > 0.03 archive**, tag `curated`) · `qubo_weights` (**Ledoit-Wolf QAOA**, tag `optimal`) · `live_portfolio` (Execution Basket, tag `live`).
**10 flows** with classification: `f-bars` (market data) · `f-flow` (microstructure) · `f-features` (feature matrix, security, **top→left**) · `f-primitives` (**syntax ops**) · `f-safe-ast` (leak-free AST, security) · `f-candidate` (raw alpha) · `f-residual` (orthogonalized, dashed, **bottom→top**) · `f-causal-dag` (**directed DAG**, security) · `f-cov-matrix` (shrinkage cov) · `f-alpha-weights` (composite alpha, dashed).

> **⚠ SEMANTIC ANOMALY:** `factor_miner` receives **no `lookahead_guard` output** — the "safe AST" edge terminates at `causal_filter` only. **The generator is structurally un-gated by the lookahead guard**, contradicting the card claim *"Symbolic AST analyzer enforces negative time shift constraints."*

**3 cards (verbatim):** emerald "Autonomous Factor Discovery" (*"Only candidate factors exceeding Information Coefficient IC > 0.03 pass to orthogonalization"*) · rose "Lookahead & Causal Guards" · cyan "QUBO Mathematical Allocation".

**Code reality check:**

| Key name | Verdict | Evidence |
|---|---|---|
| `Factor Miner` **Ralph Loop** | **MATCH** | `core/alpha_discovery.py:1` "FactorMiner Ralph Loop + AlphaAgent Regularization"; `:502`, `:506`, `:606`, `:729`; AST originality `:198`, `:230`; tests `tests/test_alpha_discovery.py:236` |
| `Causal Filter` **Granger / ABSO** | **MATCH** | `core/causal_feature_filter.py:3` "Inspired by CRL-ABSO (2026)"; `DEFAULT_GRANGER_LAG = 3` `:80`; `granger_f`/`granger_p` `:94-95`; F-test `:140-149`; weighting "Granger > Partial corr > Correlation > MI" with `0.35 * min(1.0, best_f/10.0)` `:448-450`; scheduled `auto_quant_tools.py:57` `"causal_filter": 200` |
| `Orthogonalizer` **Gram-Schmidt** | **MATCH** | `core/signal_orthogonalizer.py:21`, `:51` `GRAM_SCHMIDT = "gs"`, `:319`, `:327`, `:509` |
| `QUBO Allocator` **Ledoit-Wolf QAOA** | **MATCH** | `research/qubo_rebalancer.py:4` "Walk-Forward QUBO Scheduling via QAOA (Abraham Itzhak Weinberg, 2026)"; `:24` Ledoit-Wolf analytical shrinkage; `:46` "shrinkage intensity alpha (Ledoit & Wolf 2004)"; `:156` QUBO matrix Q ∈ R^{W×W}; `:279` Algorithm 1 & §3.8. Also `core/factor_portfolio_models.py:232` `LedoitWolfResult`; `core/skill_registry.py:640` "Skill 31: Ledoit-Wolf shrinkage covariance" |
| `Alpha Registry` **IC > 0.03** | **MISMATCH (threshold & metric)** | real gate is **IC_IR ≥ 0.15**: `core/alpha_discovery.py:290` `min_ic_ir: float = 0.15`. **No `0.03` threshold and no class named `AlphaRegistry`.** |
| `Order Flow` VPIN / OFI | **MATCH (OFI) / VPIN NOT-FOUND** | OFI: `core/meta_labeling.py:11` "OFI_t = Delta Q_bid,t - Delta Q_ask,t", `:175` `calculate_order_flow_imbalance`; `core/order_execution.py:376/425/469`; `core/urgency_sor.py:5`. **Zero matches for "VPIN" in any `.py`.** |
| `Lookahead Guard` | **MISMATCH (name) / MATCH (function)** | no `LookaheadGuard` symbol; implemented as AST canonicalization + originality similarity, not a negative-time-shift walk |
| `z-score + fracdiff` | **PARTIAL** | fracdiff real and scheduled (`auto_quant_tools.py:3`); not a single named `norm_features` module |
| `live_portfolio` "Execution Basket" | **NOT-FOUND by name** | real equivalents: `core/alpha_ensemble.py`, `research/qubo_rebalancer.py` |

## 20.5 `05_strategy_lifecycle.lifecycle.json` — "Strategy Incubation & Lifecycle Governance"

**4 lanes:** `main` Strategy promotion phases · `waiting` Incubation & decay monitor · `recovery` Evolution & parameter sweep · `terminal` Terminal governance outcomes.
**11 states:** `synthesized` (start, **01**, CreatorEngine LLM, tag `candidate`) → `backtesting` (**02**, **CPCV + DSR eval**, tag `sandboxed`) → `verifying` (**decision**, **PBO Gating**, **PBO < 0.15 threshold**, tag `gate`) → `incubating` (**04**, paper forward test, tag `paper`) → `production` (**success**, **05**, active allocation, tag `live`) · `pbo_failed` (Overfit Flag, DSR penalty trip, tag `rejected`) · `retuning` (**MadEvolve Sweep**, genetic mutation) · `decaying` (**Alpha Decayed**, **rolling Sharpe < 1.0**) · `discarded` (**failure**, spurious curve fit) · `failed_paper` (**failure**, **Incubation Fail**, **live slippage > 20 %**) · `retired` (**success**, archived to history).
**6 transitions, all failure/decay branches:** `verifying`→`pbo_failed` (**PBO ≥ 0.15**) · `pbo_failed`→`discarded` · `incubating`→`retuning` · `retuning`→`failed_paper` · `production`→`decaying` (**rolling Sharpe < 1.0**, dashed) · `decaying`→`retired`.

> **🔴 CRITICAL STRUCTURAL DEFECT:** the `main` lane's 5-state promotion rail (steps 01→05) has **NO edges between consecutive states at all**. `synthesized→backtesting`, `backtesting→verifying`, `verifying→incubating`, `incubating→production` are **all missing**. The only 6 edges drawn are all failure/decay branches. **The happy path is completely unwired**, contradicting the view's stated purpose (*"Follow candidate strategy from CreatorEngine synthesis, through CPCV gates, to live production"*) and the card claim *"Strategies advance through 5 distinct linear gates."*
> The `verifying` **decision** node has exactly one outgoing edge — and it is the **rejection** edge.

**3 cards (verbatim):** cyan "Strict Promotion Gate" · rose "Autonomous Quarantine" (*"Any strategy with Probability of Backtest Overfitting PBO >= 0.15 is rejected"*) · emerald "Alpha Decay Governance" (*"Continuous 30-day rolling Sharpe and CUSUM change-point detection"*).

**Code reality check:**

| Key name | Verdict | Evidence |
|---|---|---|
| `CreatorEngine` | **MATCH** | `research/creator_engine.py:49`; DI-provided `core/harness/plugins.py:293, 298` |
| `MadEvolve Sweep` | **MATCH** | `core/evolutionary_optimizer.py:1` "MadEvolve LLM-Driven Evolution"; `:11` "Parameter budget enforcement (MadEvolve insight)"; `:17`, `:76` |
| `CPCV + DSR` | **MATCH** | `core/auto_quant_tools.py:3`, `:11` "CPCV runs weekly (~2016 cycles on M5, ~336 on M30)", `:43` `"cpcv": 2016` |
| `Overfit Flag` / decommission | **MATCH (function)** | `core/harness/plugins.py:268` imports `evaluate_alpha_decay_decommission_thresholds`, `:277` provides it; `core/alpha_decay_monitor.py:56` `DEFAULT_STATE_FILE`, `:85` `code: str  # e.g. "ALPHA_DECAY_012"` |
| `CUSUM` change-point | **MATCH** | `core/cusum_monitor.py`; wired `core/harness/quant_models_plugins.py:36, 43, 44`; `core/harness/workflows.py:858` "- CUSUM Residual Monitor: detects alpha drift" |
| **PBO < 0.15** | **MISMATCH (threshold)** | real: `CPCV_PBO_WARNING = 0.25` `auto_quant_tools.py:100`, `CPCV_PBO_CRITICAL = 0.35` `:102`, `CPCV_DSR_WARNING = 0.70` `:101`, `CPCV_DSR_CRITICAL = 0.50` `:103`, `CPCV_ESCALATION_THRESHOLD = 3` `:99`. **No 0.15 PBO threshold exists.** The SSOT also does not specify 0.15 (it uses `p < 0.01` for Gate 2) |
| **rolling Sharpe < 1.0** | **NOT-FOUND by value** | no 1.0 rolling-Sharpe threshold is greppable. Related real gates are docs-only: `retail_quant_models_part1.txt:228` "SQN < 1.5: Decommission", `:223` "Rolling 60d Sharpe < 0.50 → Decommission" |
| **live slippage > 20 %** | **NOT-FOUND** | docs-only analogue `retail_quant_models_part1.txt:248` "If decay > 20%: halt and optimize execution routing" |
| `incubating` paper/canary | **PARTIAL** | `/incubation` is a documented slash command but no lifecycle state-machine class exists; `strategies/registry.py` is the closest real structure |

---

# PART 21 — PORTFOLIO OPTIMIZATION SERIES (8 papers, Author-P & Author-K 2001–2007)

All 7 original arXiv PDFs are stored alongside the `.md` files. ⚠ The README declares the folder as `docs/author-p-author-k-portfolio-optimization-series/`; **the actual on-disk name is `docs/portfolio-optimization-series/`** and the declared path does not exist.

**Series-level load-bearing fact (verbatim):** *"when you estimate an N×N covariance/correlation matrix from T observations, the number of parameters is O(N²) but the number of data points is O(NT). Unless T ≫ N, the matrix is dominated by measurement noise."*

**Stated relevance condition:** *"If you run an autonomous portfolio agent with N=50–500 instruments and T=250–1000 days, you live permanently in the regime this series describes."*

## 21.1 The master law: `r = N/T`

| Paper | arXiv | Published | One-line thesis |
|---|---|---|---|
| 0 RiskMetrics | `cond-mat/0103107` | Physica A 299 (2001) | RiskMetrics' apparent success is an artifact of the 1-day VaR horizon + normal tail assumption |
| 1 Noisy covariance I | `cond-mat/0111503` | Eur. Phys. J. B 27 (2002) | With 94 % random spectrum, linear-constrained variance optimization degrades only 5–15 %, but nonlinear constraints cause degeneracy |
| 2 Noisy covariance II | `cond-mat/0205119` | Physica A 319 (2003) | Effect of noise depends **only** on `r = N/T`; r≈0.6 catastrophic, r≤0.2 acceptable. Risk **measurement** noise << optimization noise |
| 3 Estimated correlation | `cond-mat/0305475` | Physica A 343 (2004) | Simulation-based benchmarking framework; RMT filtering beats single-index and shrinkage on toy models |
| 4 Exponential weighting + RMT | `cond-mat/0402573` | SFI WP 500050 (2004) | Analytic spectrum of EWMA random matrices; hybrid EW+RMT beats either alone |
| 5 Random-matrix filtering | `physics/0509235` | Acta Phys. Pol. B 36 (2005) | Controlled demonstration that RMT eigenvalue clipping restores the efficient frontier |
| 6 Noise sensitivity of risk measures | `physics/0611027` | J. Banking & Finance 31 (2007) | Universal phase transition at critical `r*`; ES & MaxLoss have **probabilistic feasibility** failure even at small r |
| 7 Synthesis | — | — | Cross-paper synthesis + actionable rules |
| 8 Absolute deviation | — | Springer (2006) ch. | Absolute deviation as LP-solvable alternative; same r-dependence but always feasible below critical r |

**Key formula (Paper 2, the series' master law):**
```
q₀(r) ≈ 1 / sqrt(1 − r)     (lowest-order analytic; diverges at r→1)
```
Measurement error scales as `1/√T`, **independent of n** to leading order, typically **< 5 % even for r = 0.6**.

**Empirical collapse:** `r=0.2 → q₀≈1.12` (12 % excess risk) · `r=0.4 → q₀≈1.29` · `r=0.6 → q₀≈1.58`. *"Points for different (n,T) pairs with same r fall on one curve."*

**Operational translation table (Paper 2, verbatim):**

| History T (daily) | Max n for r≤0.2 (q≈12 %) | Max n for r≤0.4 (q≈29 %) |
|---|---:|---:|
| 250 (1 yr) | 50 | 100 |
| 500 (2 yr) | 100 | 200 |
| 1000 (4 yr) | 200 | 400 |
| 2000 (8 yr) | 400 | 800 |

> *"Since 8-year stationarity is implausible, n=50–100 is the practical ceiling for unfiltered Markowitz with daily data if you demand < 15 % noise penalty."*

## 21.2 Paper-by-paper detail

**Paper 0 — RiskMetrics critique.** `σ²_{t+1|t} = (1-λ) Σ_{τ≥0} λ^τ r²_{t-τ}`; `VaR = σ × z_p` with **z = 1.65 for 95 %**, **z = 2.33 for 99 %**; λ = 0.94 daily / 0.97 monthly. Findings: EWMA, GARCH(1,1) and historical σ all perform similarly at 1 day; at 10–25 day horizons GARCH and historical **improve over** EWMA; `Empirical VaR ≈ 1.65·σ` at 95 % **holds coincidentally**; at 99 % the normal quantile **2.33 severely underestimates** true VaR (true is **~3–4σ**) and the hit rate is **~2× the nominal violation rate**. *Retail implication:* do not use λ=0.94 EWMA as a plug-and-play estimator for swing-horizon (3–25 day) holds; any VaR backtest must include 99 % and 99.5 % and multi-day horizons; keep λ configurable.

**Paper 1 — the 94 % random spectrum.** Method: true model (market + sectors block) → noise dressing `X ~ N(0, C⁰)`, `Ĉ = (1/T) XXᵀ` → Optimization A (linear) `min wᵀĈw` s.t. `Σw=1, Σw_iμ_i = target` ⇒ **`w* ∝ Ĉ⁻¹1`** → Optimization B (nonlinear: leverage cap, margin `max_i w_i ≤ c`, regulatory `Σ(w_i)₊`) → metrics `q = R²(ŵ; C⁰)/R²(w⁰; C⁰)` and `‖ŵ − w⁰‖`. N = 100–500, T = 500–2000. **Linear: `q − 1 ≈ 0.05–0.15`** (N=100/T=500 → ~15 %; N=400/T=600 → ~15–20 %). Weight space instability is large even when q is small — *"Do not interpret individual weights as signal."* **Nonlinear: q diverges, feasible set fragments, exponentially many portfolios in N with essentially identical minimal risk** — *"Problem becomes ill-posed as a decision tool."* *Retail implication:* since any live system has leverage caps, no-short, position limits and regulatory capital rules — all nonlinear — the 5–15 % number is a **lower bound**. **Budget-only backtests overstate robustness.** *"With N=100 and T=500 (2 years daily), r=0.2 already gives 15 % penalty. If you run N=200 on 1 year (T≈250), r=0.8 → unusable."*

**Paper 2 — r is the control parameter.** Toy models: (a) identity; (b) one-factor market with closed-form eigenvalues `C_ij = ρ₀ + (ρ−ρ₀)·δ_{sector(i),sector(j)}`. Semi-analytic `q₀ = ⟨R²(ŵ)⟩/R²(w⁰)`; Monte Carlo n = 10..400, T = 50..3000, ≥ 1000 samples. **The headline:** *"You can trust a covariance matrix to report risk of a given book, but not to choose the book."* *Retail implications:* **hard r-budget** — add config `max_r = N_eff / T_lookback` and enforce `N_eff ≤ 0.2·T` for any covariance-optimized sleeve; if the universe is larger, do dimensional reduction first, don't widen T with stale data. **T is not free** — extending lookback adds non-stationarity bias, so effective r is *worse* than nominal; prefer 1–2 year windows. **Intraday vs daily** — 1-hour bars multiply T but add microstructure noise and intraday seasonality; σ(1h) ≠ σ(1d) scaling.

**Paper 3 — benchmark on synthetic truth, not history.** 4-step reusable recipe: **Step 1** three toy correlation models (I single market factor `C_ij = ρ₀`; II market + sectors Noh-type block; III market + sectors + random factor loadings) · **Step 2** simulate N = 100–400, T = 100–1000 · **Step 3** estimators (historical `Ĉ_hist`; single-index rank-1+diagonal; **RMT filter** keep `λ_i > λ₊ = (1+√r)²`, replace bulk by mean *and renormalise trace to N*; sector-aware; shrinkage to identity) · **Step 4** optimize and evaluate `q₀ = R²(ŵ;C_true)/R²(w⁰;C_true)` (excess true risk) and `q₁ = R̂²(ŵ;Ĉ_est)/R²(w⁰;C_true)` (in-sample, often < 1 ⇒ over-optimism), 500–1000 replicas.

*Table I reconstructed, N=100, T=166 (r≈0.6), Model II:*

| Estimator | q₀ (true risk ratio) | q₁ (predicted) |
|---|---:|---:|
| Historical | **1.58** | **0.63** |
| Single-index | **1.72** | — |
| RMT-filtered | **1.18** | **0.86** |

At r = 0.6 RMT reduces q₀ by **~25 %**; at r = 0.2 the gain is **~5 %**. Single-index works **only** when the truth is single-index (q₀ ≈ 1.02–1.05 on Model I); on Models II/III it is worse than even historical — *"imposing the wrong structure adds bias > noise reduction."* **Trace preservation matters:** bulk → average **and** renormalise beats zeroing. *Retail implications:* adopt the horse-race harness before deploying any new covariance estimator; **RMT clipping is the minimum noise filter**; calibrate a correction factor from the synthetic benchmark (multiply predicted σ by ~1/q₁).

**Paper 4 — EWMA needs its own RMT edge.** Four estimators: uniform historical `Ĉ_U` · EWMA `Ĉ_EW = (1−α)Σ α^τ r_{t−τ}r_{t−τ}ᵀ` · RMT-filtered uniform · **hybrid EW+RMT (new)** using an EW-specific edge `λ₊^EW(α, N/T_eff)`.

**Analytic RMT for EW matrices:** standard support `[λ₋, λ₊]` with **`λ₊ = (1+√r)²`**; the EW case satisfies a **generalized Marčenko-Pastur equation via the Stieltjes transform**, solved by replica/free-probability methods, with `r_EW = N/T_eff*` where `T_eff* < T_eff` due to weight concentration. Band edges by `dG⁻¹/dz = 0`. **Result: `λ₊^EW > λ₊^uniform` for the same nominal T, and grows as α→1** (heavier recent weighting ⇒ fewer effective samples ⇒ wider bulk). Validated by Monte Carlo to **within ~2 %**.

*Horse-race table (relative OOS realized vol, uniform = 1.00):*

| Estimator | Relative OOS realized vol | Note |
|---|---:|---|
| Uniform historical | **1.00** | baseline |
| **EWMA only (α=0.94)** | **~1.08–1.15** | **worse** than uniform — noise amplification dominates (T_eff ≈ 16 days) |
| RMT-filtered uniform | **~0.82–0.88** | clear win |
| **Hybrid EW+RMT** | **~0.75–0.80** | best; **~8–10 %** over RMT-only |

For N = 400, `r_eff ≈ 25 ≫ 1` ⇒ matrix is singular/noise-dominated. *"EWMA without filtering is dangerous for large portfolios."* *Retail implications:* never use EWMA covariance without RMT filtering when N is large, and use the **EW-specific** edge — the uniform λ₊ under-filters; choose **τ₀ = 60–120 days** for allocation, not 16; add `ewma_half_life` and `r_effective` to config and log them.

**Paper 5 — controlled demonstration.** Market-plus-sectors model (N = 100–406, K = 5–10 sectors, ρ₀ ≈ 0.2–0.3, ρ_k ≈ 0.4–0.6), r varied 0.2–0.7. Metrics: frontier distance, weight overlap **`q = (w⁰·ŵ)/(‖w⁰‖‖ŵ‖)`** (cosine), realized risk ratio `q₀`. Findings: in-sample predicted σ underestimates true by `q₁ ≈ 0.6–0.8` at r = 0.5; OOS realized σ inflates by `q₀ ≈ 1.3–1.6`. **After filtering: `q₀: 1.4 → 1.15` at r ≈ 0.5; visual frontier gap roughly halved.** Cosine similarity improves by **~0.15–0.25**. *Retail implications:* **frontier backtests lie** — always plot in-sample predicted and OOS realized frontiers together and require them to be close; run the market-plus-sectors simulation at your live N and T as a pre-deployment smoke test; filtering is **implicit ℓ₂ regularization**, so be aware of double-shrinkage if you also impose explicit weight caps.

**Paper 6 — the capstone: probabilistic feasibility failure.** Four risk measures: variance `σ²(w) = wᵀCw` (QP) · MAD `(1/T)Σ|Σ_i w_i(x_it − μ_i)|` (LP) · **ES/CVaR** `ES_α(w) = min_ε { ε + (1/((1−α)T))Σ_t [−wᵀx_t − ε]₊ }` (LP) · **Maximal Loss** `ML(w) = max_t(−wᵀx_t)` (LP). Sensitivity `q₀(k) = risk_true(ŵ(k))/risk_true(w⁰) ≥ 1 (or ∞ if infeasible)`; **feasibility probability** `p(N,T)` = fraction of samples where the optimization is bounded. Sweep: N = 10..100, T = 50..2000, r ∈ [0.05, 2], α ∈ {0.9, 0.95, 0.97, 0.99}, 500–2000 replicas.

```
⟨q₀⟩ ∼ (r_c − r)^(−γ)   with γ ≈ 1/2 for variance/MAD
Weight fluctuations ⟨‖δw‖²⟩ ∼ (r_c − r)^(−δ)
```

| Risk measure | Critical `r_c` | Feasibility |
|---|---:|---|
| Variance | **1** | always feasible for r<1; diverges at r→1 (Wishart smallest eigenvalue → 0) |
| MAD | **≈ 0.5** | always feasible for r<0.5 |
| **ES α=0.99** | **≈ 0.3–0.5** | **probabilistic feasibility**: p<1 even for small r; `r_c(α)` decreases as α→1 |
| ES α=0.97 | ≈ 0.5 | p ≈ 0.5 near r_c |
| **ML (ES α→1)** | ≈ 0.5 (lower) | p drops very fast; even at r=0.1 finite probability of infeasibility for large N |
| MaxL at α=0.9 | ≈ 0.8 | less fragile |

**The in-sample arbitrage mechanism (the headline discovery):** there may exist a weight vector `w_dom` that dominates all others — its loss is above the α-quantile in **every** time step; the LP correctly says *"go infinitely long that portfolio"* ⇒ unbounded ⇒ no finite optimizer. Probability > 0 for any r>0, increasing with r and N. **Variance never has this** — the quadratic penalty prevents any direction appearing free.

**Filtering state of the art (verbatim):** *"Variance filtering (RMT, shrinkage) is mature and roughly halves q₀; analogous filters for MAD/ES/ML are essentially nonexistent at time of writing."* ℓ₂ or ℓ₁ constraints round off the divergence and restore feasibility at the cost of bias.

**HARD CONSTRAINTS TABLE (verbatim — "most actionable paper in series"):**

| Rule | Value |
|---|---|
| Variance sleeve max r | `N/T ≤ 0.2` safe · `≤ 0.4` tolerable · never `> 0.6` without filtering |
| MAD sleeve max r | `N/T ≤ 0.3` |
| **ES 99 % sleeve max r** | **`N/T ≤ 0.1–0.15`** |
| ES 97 % sleeve max r | `N/T ≤ 0.25` |
| ML / worst-case | **do not optimize ML directly** — use ES or variance as proxy |

> *"If your LLM trader proposes a portfolio optimization with ES_99 and N=200, T=500 (r=0.4): refuse or force regularization — the paper says infeasible with > 50 % probability."*

**Code-level guards (4, verbatim):** (1) pre-optimization feasibility check for ES/MAD/ML LP, detect unboundedness *before* allocating via an auxiliary LP, fall back to filtered variance or regularized ES-ℓ₂ · (2) ℓ₁/ℓ₂ regularization as a first-class feature — *"phase-transition regularizers"* · (3) never report in-sample ES as true risk — use OOS or cross-validated, apply q₀ correction or bootstrap · (4) prefer MAD over ES for small-T regimes, or ES at lower α (0.90–0.95).

**Monitoring (verbatim):** track `r = N_effective / T_lookback` as a live risk metric; track a `p_feas` proxy — *"among rolling windows, how often does ES optimization hit unboundedness or produce extreme ‖w‖ (> 10× budget). Rising trend ⇒ halve N or double T or tighten regularization."*

> **Bottom line (verbatim): "Papers 1–5: noise makes optimization worse. Paper 6: under tail-sensitive risk measures, noise can make optimization impossible."**

**Paper 8 — absolute deviation (MAD).** Konno & Yamazaki (1991) origin; less outlier-sensitive than variance (L1 vs L2); minimizing MAD under `Σw=1` is a **linear program** (auxiliary variables `u_t ≥ ± residual`). MAD has an r-dependent phase transition at **`r_c(MAD) ≈ 0.5`** (between variance 1 and ES_99 ≈ 0.3), and **below r_c MAD is always feasible** — *"because absolute deviation still grows linearly with ‖w‖ in any direction, preventing in-sample arbitrage."*

## 21.3 Paper 7 synthesis — the seven integration rules for this codebase

**§1 The one equation to memorize:** `r = N_effective / T_lookback`. `N_effective` is the dimension after factor reduction (K-factor model ⇒ `N_eff ≈ N−K` or the retained principal components). `T_lookback` is the number of **independent** observations — *"For daily data, T ≈ calendar days × (1 − autocorrelation)."* For EWMA, `T_eff` is *"much smaller than wall-clock T."* Verdict: **"Manage r and you manage noise. Ignore r and noise manages you."**

**§2 Tolerable operating zones:**

| Optimizer | Safe (q₀<~1.15) | Tolerable (q₀<~1.3) | Danger |
|---|---|---|---|
| Min-variance (no filter) | r ≤ 0.2 | r ≤ 0.4 | r → 1 diverges |
| Min-variance (RMT filtered) | r ≤ 0.4–0.5 | r ≤ 0.7 | diverges later |
| Min-variance (EW+RMT hybrid) | r_eff ≤ 0.4 | r_eff ≤ 0.6 | EW shifts edge |
| MAD | r ≤ 0.25 | r ≤ 0.4 | r_c ≈ 0.5 |
| ES 97 % | r ≤ 0.2 | r ≤ 0.35 | r_c ≈ 0.5 + p_feas < 1 |
| **ES 99 %** | **r ≤ 0.1** | r ≤ 0.2 | r_c ≈ 0.3, p_feas drops at 0.15 |
| Maximal Loss | avoid direct opt | — | infeasible even at r = 0.1 for large N |

**§3 Two uses, opposite reliability:** *measure* a given book (`σ² = wᵀĈw`) is **low** sensitivity (~1/√T, ~independent of N) — raw sample covariance acceptable for reporting. *Choose* the book (`w* = argmin wᵀĈw`) is **high** sensitivity — must filter/shrink/regularize. **Implementation guard:** separate two code paths — `risk_report(cov_sample, w_fixed)` and `optimal_weights(cov_filtered, constraints)`.

**§4 Why textbook Markowitz understates fragility:** *practical test* — *"run your optimizer 100 times on bootstrap resamples of the same window. If weight vectors scatter widely, you're in the degenerate regime — even if reported risk looks stable."*

**§5 What filtering does NOT fix:** non-stationarity/regime shifts (longer T adds stale-regime bias) · heavy tails (Gaussian r_c is optimistic) · **expected-return estimation error** (*"the series (wisely) focuses on covariance; μ estimation is an even harder problem … Don't let a cleaned covariance blind you to μ noise."*).

**§6 Diagnostic harness to add** — the permanent regression test to replicate Paper 3's horse-race, with a `q0_proxy = in-sample predicted σ / rolling OOS realized σ` monitor (**> 1.3 = noise regime**), a `weight_dispersion = ‖w_t − w_{t−1}‖/‖w_{t−1}‖` monitor, and an `es_feasibility_failures` weekly counter.

**§7 Integration checklist — all seven boxes are UNCHECKED (`- [ ]`):** `max_r_variance`/`max_r_es99`/`ewma_alpha`/`rmt_filter` config · pre-opt r guard · covariance factory that never returns raw sample cov to the optimizer without `allow_unfiltered=True` · EWMA routed through `lambda_plus_EW` · two-path risk API · frontier honesty (predicted + realized together) · **agent tool constraint: if the LLM proposes ES_99 with N=200, T=500, the tool should reject referencing `06:r_c(0.99)≈0.3`.**

**§9 Open problems (verbatim):** time-varying correlations (DCC, regime-switching) · heavy-tailed RMT (Marčenko-Pastur for Student-t, Biroli et al.) · transaction-cost-aware optimization under noise · hierarchical clustering / nested factors (de Prado, Raffinot) · Bayesian estimators with learned priors. *"Each of these is a natural next folder in `docs/`."*

**Verification:** none of the §7 config keys exist in the repo; `tests/test_covariance_estimators.py` does not exist; `core/portfolio_optimizer.py` and `risk/covariance.py` do not exist. **The whole series is unimplemented** — but `research/qubo_rebalancer.py:24` *does* implement Ledoit-Wolf analytical shrinkage and `core/factor_portfolio_models.py:232` `LedoitWolfResult`, so one of the recommended filters is partially present (without RMT clipping or an r-budget).

---

# PART 22 — INSTITUTIONAL-vs-RETAIL APPLICABILITY & DISPERSION BIAS

## 22.1 `institutional-vs-retail-applicability/` — the corpus's self-critique

This subtree is an **adversarial triage** of the rest of the corpus: which institutional methods transfer to a retail-scale autonomous FX/commodity system, which must be adapted, and which are outright non-transferable.

**Files:** `README.md` · `01-taxonomy-institutional-vs-retail.md` (48) · `02-pafka-kondor-applicability.md` (43) · `03-autonomous-papers-applicability-matrix.md` (95) · `04-real-problems-gap-analysis.md` (52) · `05-priority-roadmap.md` (62).

**The taxonomy (01)** establishes the transfer axes used by the matrix: institutional methods differ from retail along (a) data ownership and latency, (b) execution venue and market access, (c) capital scale and capacity, (d) regulatory reporting obligations, (e) statistical discipline available (PBO/DSR vs Sharpe-only), (f) failure recovery infrastructure.

**Pafka–Kondor applicability (02)** is a specific methodological critique: their multi-asset volatility/relative-value results assume a diversified cross-asset universe and deep cross-sectional breadth. On a retail FX/commodity book with N = 5–20 instruments and a single broker's pricing, the statistical power collapses — the paper's significance thresholds do not transfer. The doc's verdict is that the *method* (relative-value residuals, volatility normalization) transfers but the *significance claims* do not, and any imported threshold must be re-derived at retail N.

**The applicability matrix (03)** scores each autonomous-systems paper along those axes and assigns a verdict: **APPLY** (transfer unchanged) / **ADAPT** (transfer with modification) / **REJECT** (does not transfer). Notable verdicts:
- **NO-GO**: GEPA / Prototypical-MAML few-shot regime classification (needs meta-training across many regimes; a retail system sees 2–3 live regimes); RMT-3 of Richard-Stein (no `HierarchicalRiskParity` symbol exists anywhere in the repo).
- **PARTIAL / Hybrid 4-of-5**: CRL-ABSO and CLEAR — *"Recommend piloting on top of the existing causal DAG."* The full CRL-ABSO stack is P3/do-not-build — *"20 % effort for 80 % gain" from the lite version.*
- **APPLY**: Granger causality, purged/embargoed CV, CPCV, DSR, triple-barrier labeling, fractional differentiation, BOCPD, CUSUM, EWMA volatility, quarter-Kelly with hard caps, fixed-fractional sizing.
- **ADAPT**: Almgren-Chriss (liquidity parameters are not identifiable from retail fills), Hawkes/VPIN (needs L2 data), BMA pools (need more architectures than a retail system runs), Kelly (must be fractional and promotion-gated).

**Real-problems gap analysis (04)** is the most actionable file in the subtree. Its central finding:

> Triple-barrier labeling, the triple-barrier meta-classifier, calibrated slippage, and the EV machinery **all exist** in the repo — but the chain breaks at two links: **(a)** the meta-classifier is never trained or served on rolling labelled data, and **(b)** its output never replaces LLM confidence in `calc_position_size`.
>
> *"Closing this loop converts the system from 'LLM guess + fixed fraction' to 'calibrated edge + fractional Kelly'."*

This is independently confirmed by verification: `intelligence/win_probability_model.py` exists and `_sizing_confidence` (`llm_orchestrator.py:3935-3936`) correctly caps `min(blended, base)`, **but no production caller passes the blended value**, and the meta-labeling output is advisory-only (`llm_orch:1369`).

**Priority roadmap (05)** orders the transfer work by (impact × independence-from-blockers) and puts the two-link break at the top, followed by: r-budget enforcement from the portfolio series, RMT filtering on any covariance-optimized sleeve, a stress-test library (2008 / 2020 / 2022 + synthetic tail scenarios), online Ledoit-Wolf shrinkage on the live book, and rebalancing bands on live positions.

## 22.2 `dispersion_bias/` — one paper, a rigorous GO/NO-GO gate

| File | Lines | Content |
|---|---:|---|
| `README.md` | 19 | index |
| `PAPER_SUMMARY.md` | 35 | the paper's thesis and method |
| `SYSTEMS_CATALOG.md` | 41 | systems S1, S2, S5, S7, S10 — doc-only |
| `IMPLEMENTATION_BLUEPRINT.md` | 86 | every module, class, function signature, and phase |
| `USEFULESS_EVALUATION.md` | 21 | the verdict |

The paper's thesis concerns **dispersion as a source of exploitable structure** rather than a property to be minimized — a portfolio or strategy whose components are perfectly correlated has one effective degree of freedom, while a genuinely dispersed set of weak signals can aggregate into a measurable edge.

**The GO/NO-GO gate** is stated with explicit criteria and thresholds, and the blueprint specifies the full module set, class signatures, and build phases.

> **⚠ Verification verdict: mostly ASPIRATIONAL — the blueprint's modules were verified missing from the tree.** S1, S2, S5, S7, S10 exist only in `SYSTEMS_CATALOG.md` as doc-only entries. The `USEFULNESS_EVALUATION.md` verdict is negative-leaning: the paper's dispersion effect is documented in markets with thousands of constituents, and at retail N the measurement noise in the dispersion statistic exceeds the effect size being claimed — the same small-N power problem that `02-pafka-kondor-applicability.md` raises.

**This is the third independent document in the corpus to reach the same N-vs-T conclusion** (after the portfolio series `r = N/T` law and the Pafka–Kondor critique). Taken together: **at retail N, any method whose significance threshold was derived at institutional N does not transfer without re-derivation.** That is the single most important methodological through-line in the whole knowledge corpus, and it is not stated anywhere in the PRD.

---

# PART 23 — `docs/quant_knowledge_base/` — THE MODULE ABI

## 23.1 On-disk reality vs documented structure

**Documented structure** (README, verbatim comments): each of `01_Regime_Detection/` … `05_Quant_Utilities/` should contain `transcriptions/` (searchable text) and `code_snippets/` (production-ready Python), plus `05_Quant_Utilities/` should add `shared_functions/` and `data_pipelines/`.

**Actual on-disk state:**

```
docs/quant_knowledge_base/
├── MASTER_PIPELINE_STATUS.md                     51 ln
├── README.md                                     57 ln
├── UNIVERSAL_MODULE_INTERFACE_SPEC.md           168 ln
├── YOUTUBE_TRANSCRIPTION_PIPELINE.md            145 ln
├── 01_Regime_Detection/{README.md 41, regime_modules.py 504}
├── 02_Strategy_Development/README.md            41 ln
├── 03_Portfolio_Management/README.md            39 ln
├── 04_Order_Execution/{README.md 39, code_snippets/transaction_cost_engine.py 127}
├── 05_Quant_Utilities/{README.md 46, quant_agent_framework.py 236, universal_quant_interface.py 159}
└── 99_Archive_Unsorted/README.md                19 ln
```

> **⚠ The documented folder structure is ~40 % unrealized.** No `transcriptions/` directory exists in any topic folder. No `shared_functions/`, no `data_pipelines/`, no `__init__.py`, no `conftest.py`, no `.pth`, and **no `sys.path` manipulation anywhere in the tree.** Total source files indexed = **14**, not the 5 the README table claims.

## 23.2 The status contradiction

`MASTER_PIPELINE_STATUS.md` states **Total Transcription Files = 0**, **Total Code Snippets Extracted = 0 (pending transcription analysis)**, and lists `TransactionCostEngine` as pending routing to `04_Order_Execution/code_snippets/`. **Yet `04_Order_Execution/code_snippets/transaction_cost_engine.py` (127 lines) already exists on disk.** The status file is self-contradicting.

Its topic-classification reference table (6 rows) maps each topic to keywords and existing sources: `01_Regime_Detection` (HMM, BOCPD, Hurst, ADX, ATR, volatility, market state) · `02_Strategy_Development` (EV, backtesting, WFO, DSR, CPCV, signals, alpha) · `03_Portfolio_Management` (Kelly, HRP, risk parity, correlation, heat cap, allocation) · `04_Order_Execution` (Almgren-Chriss, slippage, spread, market impact, TCA) · `05_Quant_Utilities` (data pipelines, shared functions, feature engineering) · `99_Archive_Unsorted`.

**Option A** (doc's stated plan): provide YouTube URLs → confirm rights → `yt-dlp` download → Whisper transcription → sort into topic folders → extract code snippets. **Option B**: manual download to a staging folder, then run transcription + sorting.

> **⚠ Stale line citations.** The README cites `docs/retail quant model.txt` lines 258-294, 495-500 and 858-916 as sources — but the retail-quant corpus on disk is now split into `retail_quant_models_part1.txt` (251 ln), `part2.txt` (168 ln), `part3.txt` (385 ln). **Those line numbers do not exist.** All per-file line citations in topics 01-04 are dangling.

## 23.3 `UNIVERSAL_MODULE_INTERFACE_SPEC.md` — the module ABI (verbatim)

**YAML module interface contract (8 blocks):**
```yaml
module:
  id: string                    # "regime_detection.hmm"
  name: string
  version: string              # semver "1.0.0"
  category: enum                # regime_detection | strategy_development | portfolio_management | order_execution | quant_utilities
  tags: string[]
  description: string
  references: string[]
  mathematics:
    formulas: string[]          # LaTeX
    parameters: object          # with defaults
    assumptions: string[]
  execute:
    input_schema: object        # JSON Schema
    output_schema: object       # JSON Schema
    timeout_seconds: int
    memory_mb: int
  implementation:
    language: string            # "python" | "rust" | "julia"
    entry_point: string
    dependencies: string[]
  validation:
    test_cases: array           # known input/output pairs
    benchmark_data: string
  agent:
    tools_provided: string[]
    requires: string[]
    produces: string[]
```
**12 required top-level keys:** `id, name, version, category, tags, description, references, mathematics, execute, implementation, validation, agent`.
**Required sub-keys:** `mathematics.{formulas, parameters, assumptions}` · `execute.{input_schema, output_schema, timeout_seconds, memory_mb}` · `implementation.{language, entry_point, dependencies}` · `validation.{test_cases, benchmark_data}` · `agent.{tools_provided, requires, produces}`.
**`category` enum (5 values)** as listed above.

**Tool/function calling convention (spec side):**
```python
@dataclass
class QuantTool:
    """Universal tool interface for quant modules."""
    name: str                    # "regime_detection.hmm.classify"
    module_id: str
    description: str
    parameters: Dict[str, Any]   # types + defaults
    returns: Dict[str, Any]      # output schema
    def execute(self, **kwargs) -> Dict[str, Any]:
```

This spec is the design intent behind the `core/skill_registry.py` (821 ln) plugin surface and `core/harness/plugins.py` — i.e. the corpus's own module ABI was reinvented as the harness kernel.

## 23.4 The three code artifacts

**`regime_modules.py` (504 ln) — 🔴 BROKEN AND ORPHANED.** Uses `pd.Series` at lines **286, 298, 299, 300, 303** but imports only `numpy`, `typing`, `dataclasses` (lines 11-15, 41, 445) — **no `import pandas`** ⇒ `NameError` at runtime. It also depends on `universal_quant_interface` (bare import line 15) and `hmmlearn` (line 41), both inside the fully-orphaned `quant_knowledge_framework/` tree. **Zero references from any real code.** It is a doc-only artifact that would crash if imported. Its classes define the intended regime-detection ABI: HMM wrapper (`n_states` default 3, `covariance_type ∈ {full, diag, tied}`), BOCPD detector, Hurst estimator, ADX/ATR filter, regime-probability vector `P_t = [P_Trend, P_Range, P_Crisis]`.

**`quant_agent_framework.py` (236 ln)** — the agent-side framework: agent base class, task/observation/reward protocol, and the LLM-tool dispatch that the `agent.{tools_provided, requires, produces}` metadata block describes. Doc-only; 0 importers.

**`universal_quant_interface.py` (159 ln)** — the base classes the spec's `entry_point` contract resolves against. Doc-only; 0 importers.

**`transaction_cost_engine.py` (127 ln)** — ✅ **the one real, complete artifact.** The full Almgren-Chriss + Perold implementation is reproduced in Part 10.3 above. It is the only code file in this subtree that is real (though the production engine actually lives in `core/transaction_cost_engine.py`, 336 ln).

**`YOUTUBE_TRANSCRIPTION_PIPELINE.md` (145 ln)** — the ingestion pipeline spec: `yt-dlp` → Whisper → topic classification → transcription storage → code-snippet extraction. Status: **not executed** (0 transcriptions exist).

---

# PART 24 — THE RAW RESEARCH CORPUS (KNOWLEDGE-ONLY)

`docs/knowledge/extracted_knowledge_base.md` is **43,651 lines** — the largest single file in `docs/`. It is a knowledge base, not a specification, and must not be read as an unimplemented feature list.

| File | Lines | Nature |
|---|---:|---|
| `casual model deep domain knowledge .txt` | 4,948 | broad cross-domain quant/trading reading notes |
| `quan types and model.txt` | 1,774 | taxonomy of quant model types |
| `retail quant model.txt` | 1,511 | retail-scaled model design notes (superseded in practice by the part1-3 split) |
| `news trading model.txt` | 1,031 | news-driven trading models and event strategies |
| `quant papers autonomous quant trading systems .txt` | 905 | paper abstracts → the source `PAPER_PROJECT_MAPPING.md` was built from |
| `how to find edge.txt` | 548 | edge-discovery methodology |
| `knowledge/types of analysis when to apply them all the way to philosophical knowledge .txt` | 569 | the epistemology → analysis-type decision framework (feeds §10.2) |
| `retail_quant_models_part1/2/3.txt` | 203/137/362 | the current retail-quant corpus (replaces the single-file version) |
| `complete_trading_models_part1.txt` | 192 | consolidated trading model notes |
| `swing_scalp_math_models_extracted.txt` + `_supplementary.txt` | 189/397 | swing/scalp mathematical models |
| `strategy discover agenttic ai.txt` | 592 | agentic strategy-discovery design |
| `model optimization and standard` (no extension) | — | model optimization standards |

**Named-item catalogue** (every distinct model/strategy/indicator/framework across the corpus, grouped by domain):

| Domain | Items |
|---|---|
| **Trend** | Donchian channel, Bollinger-band breakout, EMA/MA cross, MACD histogram, ADX/DMI, Turtle/ATR breakout, Keltner, SuperTrend, Ichimoku, PSAR, parabolic SAR acceleration, linear regression slope, Hilbert transform, zigzag/pivots |
| **Mean reversion** | RSI(2/14), Bollinger %B, z-score vs rolling mean, OU process half-life, Kalman pair spread, Ornstein-Uhlenbeck fitted residuals, CCI, stochastic oscillator, Williams %R, Keltner reversion |
| **Stat-arb / pairs** | Engle-Granger cointegration, Johansen VECM, ADF on spread, Hurst/DFA regime classification, residual z-score entry, half-life decay targeting, cointegration-robustness re-test |
| **Breakout / volatility** | ATR channel breakout, volatility squeeze, Bollinger bandwidth contraction, Donchian N-day, range expansion, gamma squeeze (where applicable) |
| **Order flow / microstructure** | VPIN, OFI, micro-price, effective/realized spread, implementation shortfall, Almgren-Chriss TWAP/VWAP, square-root impact law, Kyle's lambda, queue-position modelling |
| **ML** | FinBERT sentiment, PPO/A2C/DDPG, meta-RL (PEARL + RL²-PPO), contextual bandits, supervised logistic baseline, triple-barrier meta-labeling, EWC forgetting prevention, MAML/Proto-MAML (rejected), LSTM/transformer sequence models (proposed only), gradient boosting (proposed only) |
| **Macro / fundamental** | FRED series ingestion, nowcast proxies, yield-curve slope (T10Y2Y), DXY regime, CFTC COT positioning, EIA energy data, SEC EDGAR filings, 22-pillar zero-lag stack, physical stoichiometry spreads, net liquidity (WALCL − TGA − ON_RRP) |
| **Risk / sizing** | fixed fractional, Kelly (3 forms), half/quarter Kelly, volatility targeting, risk parity, hierarchical risk parity, CVaR/ES, MAD, max-loss, R-multiple, SQN, drawdown ladders, heat budget |
| **Validation** | purged k-fold, embargo, CPCV, PBO, DSR, WFE, triple barrier, SPA bootstrap, White's RC, FDR/Bonferroni, deflated Sharpe, walk-forward efficiency |
| **Seasonality / calendar** | session-of-day decomposition, day-of-week effects, month-turn effects, event-window gating (CPI/NFP/FOMC), rollover/Friday-close filters |
| **Position sizing** | ATR-based sizing, volatility-scaled sizing, risk-parity weights, correlation-filtered heat caps, drawdown-multiplier ladders |
| **Execution** | TWAP, VWAP, implementation-shortfall minimisation, urgency SOR, passive maker posting at micro-price, order-lifetime guards |
| **Alternative data** | satellite (SAR, VIIRS, TROPOMI, Sentinel), night lights, AIS vessel flow, rail velocity, card velocity, SCADA grid load, on-chain FX, web/job NLP |
| **Epistemology / process** | stationarity preconditions, look-ahead prevention, survivorship-bias-free universes, purged CV, trial-count accounting, BMA over model families, falsification tripwires |

> **This is reading material, not specification.** Nothing in this table is an unimplemented feature unless it also appears in the PRD (§15-§18) or the TODO (GOAL 10/11), where it is tracked as a task.

---

# PART 25 — THE TRI-COMPENDIUM MASTER SSOT (19 modules, 7 gates, 24 commands)

Source: `docs/analytical_frameworks_and_epistemology/bma_causal_autonomous_system/MASTER_SOURCE_OF_TRUTH_QUANT_SYSTEM.md` (266 lines). This is the **cross-compendium index and claim layer** — the single document that ties all three knowledge pillars together and states the *7 Confluence Gates* and *24 slash commands*. It is DESIGN-ONLY throughout: it specifies formulas, thresholds and live telemetry values, and no code implements the command surface.

## 25.1 The three pillars and 19 modules

| Pillar | Modules | Scope |
|---|---:|---|
| **Pillar 1** — Core BMA & Causal Discovery Autonomous Architecture | 7 | BMA posterior pools, 7-tier analysis pyramid, VPIN/Hawkes/DCC-GARCH, FinBERT + alt-data, CPCV/DSR/triple-barrier, meta-agent orchestration + Bayesian fusion, physical stoichiometry + global cascades |
| **Pillar 2** — Geopolitical Predictive Analysis & Sanctions Forensics | 6 | state-space trajectories + Lyapunov horizons, 2×2 scenario cones + GPR, maritime chokepoint forensics, OFAC/SWIFT/shadow-fleet sanctions forensics, historical case-study cosine matching, synthetic macro spreads |
| **Pillar 3** — Retail Quant Master Architecture | 6 | mathematical pillars (EV/ruin/Kelly/run theory), 4 inefficiencies + OU half-life + FracDiff, six-layer architecture + 6 failure sentinels, micro-price/Almgren-Chriss/TCA, dynamic Kalman stat-arb, robustness playbook + compounding |

**Execution harness flow (verbatim from the doc's mermaid):** `INIT` (startup wizard → screen buffer wipe + terminal auto-resize → Pi TUI retained-mode component tree) → all three pillars → `EXECUTION` = `7-Gate Pre-Flight Confluence Verification` → `LLM Chain-of-Thought Deductive Synthesis` → `Glowing Decision Card: BUY / SELL / HOLD` → `MT5 Broker Bridge Dispatch + Synthetic Stealth Stops` → `Continuous Background Daemon Surveillance & Trailing Stops` → `Meta-RL Reward Attribution & Skill Compilation (SKILL.md)`.

## 25.2 Live telemetry values the doc asserts (all DESIGN-ONLY / unverifiable)

**Pillar 1:**
- BMA pool `A₁..A₆` weights `.08 / .14 / .18 / .22 / .16 / .22`; log-score memory decay **λ = 0.98**; **weight entropy `H(w) = 1.72` vs max 1.79 ⇒ "High Diversification"**
- NOTEARS + PCMCI+ validated **14 invariant edges, p = 0.0002**; do-calculus `Pr(XAUUSD | do(Fed Rate Cut)) ⇒ E[ΔP] = +0.64 %`
- 4 macro regimes: **Goldilocks** (growth↑ infl↓ ⇒ equities + long duration) · **Reflation** (growth↑ infl↑ ⇒ commodities, cyclicals, real assets) · **Stagflation** (growth↓ infl↑ ⇒ gold, cash, short duration) · **Deflation** (growth↓ infl↓ ⇒ sovereign bonds + USD)
- **VPIN = 0.184** vs toxicity hurdle 0.250 ⇒ Safe · **BVC buyer aggression 68.4 %** · **Hawkes μ = 0.42/min, α/β = 0.548 < 1.0 ⇒ subcritical** · **DCC-GARCH α = 0.064, β = 0.912, σ_ann = 10.4 %** · **Kyle's λ = 0.038 $/lot**
- **FinBERT sentiment +0.74** on the latest FOMC transcript, `S_t ∈ [-1, +1]`
- 18 physical nowcast proxies: AIS vessel flow +4.2 % YoY · US rail velocity 18.4 mph · ZORI +3.1 % · Cushing crude 24.8 M bbl
- **CPCV 16 purged splits with 1 % embargo** · **DSR = 0.942 ≥ 0.90 ⇒ Passed** · Marcos Author-C triple-barrier meta-labeling
- **CESI = 0.884 ≥ 0.75** · **Brier = 0.118 ≤ 0.15 ⇒ "Well-Calibrated"**
- **Decision-fusion state machine:** `NORMAL_OPERATING_MODE` (100 % sizing) · `CAUTION_MODE` (if Brier > 0.25 ⇒ 50 % sizing cut) · `EMERGENCY_FREEZE` (if Disagreement > 0.40 ⇒ trading halted)
- Stoichiometric floors: Haber–Bosch ammonia **$480 → $510/ton** (natural gas × 33.5 MMBtu + $120 conversion) · 3:2:1 crack **$24.50/bbl** · soy crush **$1.85/bushel**
- 8 universal global activity complexes: maritime, energy, ag, semiconductors, battery minerals, cloud power, metallurgy, sovereign liquidity
- **3-order macro cascade ripple tree:** 1st order (direct) Core PCE 2.8 % ⇒ real yields −4 bps to 1.84 % · 2nd order (transmission) real-yield compression ⇒ DXY < 104.20 · 3rd order (physical) non-dollar bullion inflow ⇒ XAUUSD +$14.20

**Pillar 2:**
- **Lyapunov horizon `T = (1/λ)·ln(ε_max/ε_0) = 26.3 days` at λ = 0.038**; *"The Parachute Principle: forecast valid within the Lyapunov horizon; model reverts to live sensory streams past it."*
- 2×2 scenario cones: A Controlled Disinflation 55 % ⇒ bullish metals · B Energy Chokepoint Spike 25 % ⇒ Brent > $95, stagflation · C Yen Carry Unwind 15 % ⇒ cross-asset margin deleveraging · D Geopolitical Truce 5 % ⇒ safe-haven premium deflation
- **GPR = 114.2** (moderate friction)
- Chokepoints: Strait of Hormuz **21.0 M bbl/day (21 % of global oil) ⇒ +$1.80/bbl risk premium** · Bab el-Mandeb/Red Sea **38 % traffic re-routed via Cape of Good Hope, +11 shipping days to Europe** · Suez transit fees **+65 % YoY** · Malacca nominal flow
- Sanctions: OFAC SDN **14,280 entities screened, 0 hit, 100 % compliant** · shadow tanker fleet **642 vessels tracked, 18 dark-AIS spoofing anomalies** · SWIFT ISO 20022 **99.8 % straight-through processing pass rate** · central bank gold reserves **+84 metric tons net non-dollar accumulation**
- Case-study cosine similarity: 2024 Fed Disinflation Pivot **91.4 %** (+1.86R ⇒ `HIGH_EDGE`) · 2023 SVB Regional Banking Shock **78.2 %** (+1.42R) · 2024 BOJ Carry Unwind **42.1 %**
- Synthetic spreads: Gold/Oil **34.2 bbl/oz** (+1.42σ above 24.0 ⇒ monetary premium) · Copper/Gold **0.00168** (−0.84σ below 0.00185 ⇒ industrial slowdown) · 3:2:1 crack **$24.50/bbl** (+0.72σ) · HY/IG OAS **3.42×** (+0.45σ ⇒ healthy corporate liquidity)

**Pillar 3:**
- **EV = (0.714 × 1.86R) − (0.286 × 1.00R) = +1.04R**
- **Balsara/Ralph-Vince ruin `RoR = ((1−A)/(1+A))^U` at A = 0.428, U = 20 ⇒ RoR < 0.0001 (< 0.01 %)**
- **Run theory `L_max = ln N / ln(1/P_loss) = ln 100 / ln(1/0.286) = 4.8` consecutive losses**
- **Quarter-Kelly `K* = (2.41 × 0.714 − 0.286)/2.41 = 0.595 ⇒ 0.25K* = 0.14 NAV (0.50 % trade cap)`**
- 4 inefficiency classes: structural, gamma/VRP, microstructure, behavioural
- **OU half-life `τ_½ = ln 2 / θ = 8.1 hours`**
- **FracDiff d = 0.40 ⇒ ADF p = 0.0004 (memory preserved)**
- **Six-layer latency 38.9 ms** (L1 2.4 + L2 4.1 + L3 8.2 + L4 1.8 + L5 11.2 + L6 11.2)
- **6 live failure-mode sentinels:** ① BOCPD changepoint `Pr(CP) = 0.12 ≤ 0.30` ② adverse selection `P_fill = 0.88` ③ copula tail risk `0.041` ④ B-book latency sentinel `18 ms` ⑤ synthetic stealth stops `1.5 × ATR₁₄` ⑥ online EWC drift `ΔW = 0.024`
- **Micro-price `P_micro = $2,634.62`** · **square-root impact `I_perm = Y·σ·√(Q/V) = $0.08/lot`** · **Almgren-Chriss IS = $2.45 (0.93 bps on 0.10 lots)**
- **Kalman stat-arb: dynamic `β_t = 1.482`, `α_t = 124.50`; `Z_t = e_t/√Q_t = −2.23`; entry `|Z| > 2.0`, exit `|Z| ≤ 0.20`, stop `|Z| ≥ 3.0`**
- **Robustness Playbook 50 % haircut rule:** backtest Sharpe 2.45 ⇒ hurdle 1.22 ⇒ realized forward Sharpe 2.14 ✓ PASSED
- **4-sleeve de-correlation at `r ≤ 0.15`:** slashes portfolio volatility **48.5 % (15.0 % → 7.8 %)**
- **Robert Carver ±10 % trade buffering** eliminates whipsaw churn
- **3-tier circuit breakers:** Tier 1 (daily) **−3.0R** ⇒ 24-hour freeze · Tier 2 (weekly) **−6.0R** ⇒ 50 % position-size cut · Tier 3 (portfolio) **−15.0 %** drawdown ⇒ emergency flatten all & lock

## 25.3 The 7 Confluence Gates (every trade must pass all 7 unanimously)

```
G1 Gate 1: Macro & Nowcasts
  → G2 Gate 2: Causal Path p < 0.01
    → G3 Gate 3: Setup Registry Life
      → G4 Gate 4: Replay Backtest >= +1.5R
        → G5 Gate 5: DOM Imbalance >= +30%
          → G6 Gate 6: Risk Arbiter 0.5% NAV
            → G7 Gate 7: Stealth Dispatch
```

| Gate | Criterion (verbatim) | Status vs code |
|---:|---|---|
| **1** Macro & Nowcasts | economic releases + 18 physical nowcasts indicate a directional tailwind | ⚠ **no equivalent 18-proxy system** — `intelligence/` has zero-lag + FRED/CFTC/EIA but not the 18-proxy stack |
| **2** Causal Graph Significance | `macro_graph.py` empirical reaction returns positive expectancy, **p < 0.01**, hit rate **≥ 65 %** | ✅ closest real match — `autonomous_causal_engine.py:34-37` uses `MIN_REACTION_SAMPLES 30`, `MIN_CONFIDENCE 0.70`, `MIN_EMPIRICAL_WIN_RATE 0.60` (0.60 ≠ 0.65, 0.70 ≠ p<0.01) |
| **3** Setup Lifecycle | strategy status **≥ `DEMO_MATCH` or `TINY_LIVE`** in `setup_registry.py` | ⚠ **status vocabulary differs** — real values are `CANDIDATE/FULL/EVALUATION` (43 registry entries, 0 with a code pointer) |
| **4** Cost-Aware Replay Backtest | 1,240-bar tick replay, **Expectancy ≥ +1.50R**, **DD ≤ 3.0 %** after **0.25 pips** slippage and **$7/lot** commission | 🔴 **NO EQUIVALENT** — `research/broker_replay.py` exists but no such gate; real DSR/PBO gates differ |
| **5** Order Book Microstructure | L2 DOM shows **≥ +30 %** buyer imbalance (observed +38.4 %) and `P_micro > P_mid` | ✅ `intelligence/market_microstructure.py` + DOM collector; ⚠ the +30 % threshold is not in code |
| **6** Portfolio Risk Arbiter | NAV risk **≤ 0.50 %** ($54.10), daily DD **< 5.0 %**, **VaR₉₅ ≤ $56.00** | ⚠ **PARTIAL** — `PortfolioRiskArbiter` has no VaR₉₅ term at all; real daily-loss limit is 3 % |
| **7** Dynamic Parameter Derivation & Stealth Dispatch | see below | ⚠ partial |

**Gate 7's full parameter recipe (verbatim):**
- **Lot volume:** `Lots = (NAV × 0.005) / (Stop Distance × Tick Value) = 0.10 Lots`
- **Stop-loss:** `Entry ± (1.5 × ATR₁₄) = $2,628.90`
- **Target 1:** `Entry ± 1.5R = $2,642.50` — closes **50 %** volume, moves SL to breakeven
- **Target 2:** `Entry ± 2.5R = $2,648.00` — closes remaining volume via dynamic ATR trailing stop

> **⚠ Gate 7's ladder conflicts with the shipped `TradeManager` ladder.** The doc says TP1 = 1.5R (50 % closed) and TP2 = 2.5R (remainder). The code uses `scale_out_r_mult = 2.0` (50 % closed) and `scale_out_two_r_mult = 3.0` (half of remainder) — `core/trade_manager.py:85-86`. **The doc and the code specify different scale-out levels.** The code's values are the ones that execute.

## 25.4 The 24 slash commands

| # | Category | Command | Function & topic |
|---:|---|---|---|
| 0 | Master Index | `/compendium` (`/master`) | master architecture index across all 3 pillars |
| 1 | P1 | `/bma` (`/dag`, `/pearl`) | BMA pool A₁-A₆, entropy H(w)=1.72, invariant DAGs |
| 2 | P1 | `/vpin` (`/garch`, `/hawkes`) | VPIN 0.184, BVC 68.4 %, Hawkes α/β=0.548 |
| 3 | P1 | `/fusion` (`/meta_agent`) | CESI 0.884, Brier 0.118, `NORMAL_OPERATING_MODE` |
| 4 | P1 | `/cascades` (`/stoichiometry`) | 3-order transmission trees, Haber-Bosch floor $510/t |
| 5 | P2 | `/geopolitics` (`/gpr`) | GPR 114.2, Lyapunov horizon 26.3 d, 2×2 cones |
| 6 | P2 | `/chokepoints` (`/domain`) | Hormuz, Malacca, Bab el-Mandeb, Suez transit flows |
| 7 | P2 | `/sanctions` (`/shadowfleet`) | OFAC 50 % rule, SWIFT ISO 20022, dark-tanker AIS spoofing |
| 8 | P2 | `/casestudies` (`/analog`) | cosine similarity (2024 Fed Pivot 91.4 %, SVB 78.2 %) |
| 9 | P2 | `/ratios_macro` (`/spreads`) | Gold/Oil 34.2, Copper/Gold 0.00168, 3:2:1 crack |
| 10 | P3 | `/math` (`/kelly`, `/ruin`) | EV +1.04R, ruin < 0.01 %, quarter-Kelly |
| 11 | P3 | `/inefficiencies` (`/halflife`) | 4 inefficiencies, OU τ½ = 8.1 h, FracDiff d=0.40 |
| 12 | P3 | `/layers` (`/sentinel`, `/bocpd`) | 6-layer 38.9 ms, BOCPD & copula sentinels |
| 13 | P3 | `/costs` (`/impact`, `/dom`) | micro-price, square-root impact, Almgren-Chriss IS |
| 14 | P3 | `/kalman` (`/statarb`, `/zscore`) | dynamic β_t = 1.482, spread Z-score gauge |
| 15 | P3 | `/robustness` (`/compounding`) | 50 % haircut, 4-sleeve de-correlation, 3-tier breakers |
| 16 | Execution | `/pipeline` (`/runall`) | execute the full 10-stage pipeline + background daemon |
| 17 | Execution | `/backtest [setup] [sym]` | deterministic in-memory tick simulation with broker costs |
| 18 | Execution | `/incubation` | forward demo drift (0.35 pips) + parameter stability |
| 19 | Execution | `/dom` (`/orderbook`) | L2 DOM ladder + buyer imbalance +38.4 % |
| 20 | Execution | `/confluence` (`/mtf`) | multi-timeframe confluence matrix (M5 → D1) |
| 21 | Execution | `/risk` | arbiter NAV limits 0.50 %, VaR₉₅ $56.00 |
| 22 | Execution | `/positions` | open tickets, trailing stops, session P&L |
| 23 | Safety | `/flatten` (`/panic`) | immediate emergency market-order burst closure |
| 24 | System | `/settings`, `/models`, `/help` | mode (Demo/Paper/Live), risk %, AI models |

> **⚠ The entire 24-command surface is DESIGN-ONLY.** The real TUI has 40 screens driven by `tui_opentui/src/App.tsx:14-53`, and the real CLI has 40 subcommands (`mt5_trader.py`). **Neither surface implements any of these 24 slash commands.** They are a knowledge-corpus UI proposal, not a shipped interface.

## 25.5 The dependency flow (from `bma_causal_autonomous_system/INDEX.md`)

`06 → 01 → 02 → 03 → 04 → 05 → 06` (a cycle — Pillar 1 is self-referential by design). Retail: `01→02→03→04→05→06→01`. Geopolitical: `01→02→03→04→01`. Root: `INIT → {P1,P2,P3} → EXECUTION`.

---

# PART 26 — COVERAGE AUDIT & FILENAME INDEX

## 26.1 Quantitative coverage

| Cluster | Files | Extracted | Depth |
|---|---:|---|---|
| Core PRD (27 fragments + assembled HTML) | 28 | 28 | ✅ all 27 sections + §25 drift register + §27 glossary/evidence index |
| Architecture & SoT docs | 10 | 10 | ✅ complete |
| Specs & contracts (API ×2, memory, state tensor, KG schema, roadmap, 2× TODO) | 16 | 16 | ✅ incl. 12-agent registry + 163-item task list |
| Audit & gap docs (loop, memory/pipeline, quant-math, graph remediation, continuation, system_workflow_audit ×5, audit/, 6 fix reports) | 19 | 19 | ✅ complete + independently re-verified |
| Knowledge corpus — analytical_frameworks Vol 01-07 | 9 | 9 | ✅ complete |
| Knowledge corpus — bma_causal_autonomous_system 01-07 + INDEX ×3 + MASTER SSOT | 11 | 11 | ✅ complete (MASTER SSOT = Part 25) |
| Knowledge corpus — retail_quant_master_architecture 01-06 + INDEX | 7 | 7 | ✅ complete |
| Knowledge corpus — geopolitical_predictive_and_sanctions_intelligence 01-06 + INDEX | 7 | 7 | ✅ complete (sanctions corpus 1,624 ln catalogued) |
| Knowledge corpus — portfolio-optimization-series (8 papers + 8 PDFs) | 18 | 18 | ✅ Part 21, all formulas |
| Knowledge corpus — institutional-vs-retail (6) + dispersion_bias (5) | 11 | 11 | ✅ Part 22 |
| Knowledge corpus — quant_knowledge_base (14 files) | 14 | 14 | ✅ Part 23, incl. the module ABI |
| Diagrams (5 JSON canonical + 5 HTML renderings) | 10 | 10 | ✅ JSON fully extracted + code reality checks; HTML confirmed as pure derivative |
| Ops / runbooks / UI / security / logging | 21 | 21 | ✅ Part 14 |
| Raw research .txt corpus + `knowledge/` | 16 | 16 | ✅ Part 24, catalogued by domain |
| PDFs (source papers, non-portfolio) | 3 | 3 | ✅ referenced |
| **TOTAL** | **200** | **200** | |

## 26.2 Filename index — where every file's content lives

| File / subtree | Part |
|---|---|
| `SYSTEM_SOURCE_OF_TRUTH_PRD.html`, `_prd_build/**` | 1-9, 12-14, **17-19** |
| `SOURCE_OF_TRUTH.md`, `ARCHITECTURE.md`, `ARCHITECTURE_COMPLETENESS.md`, `AUTONOMOUS_TRADING_SYSTEM_MASTER_ARCHITECTURE.md`, `AUTONOMY_MASTER_PLAN.md`, `CONTINUOUS_TRADING_AGENT_DESIGN.md`, `ECONOMIC_DYNAMICS_SOURCE_OF_TRUTH.md`, `QUANT_RESEARCH_AND_SYSTEM_FOUNDATIONS.md`, `PERSISTENCE_EXPLAINED.md`, `PAPER_PROJECT_MAPPING.md` | 1-3, 6-9, 15 |
| `API_DOCUMENTATION.md`, `API_REFERENCE.md`, `USAGE_EXAMPLES.md`, `CLEAN_INTERFACE_GUIDE.md` | 15, 13.9 |
| `TRADING_MEMORY_SPEC.md`, `UAQP_STATE_TENSOR.md`, `macro_causal_kg_schema.md` | 8, **19** |
| `ALPHA_MINING_INTEGRATION_PLAN.md` | 11 |
| `TRADING_SYSTEM_MASTER_TODO.md`, `TRADING_SYSTEM_REMAINING_TASKS.md`, `TRADING_SYSTEM_ROADMAP.md` | **19.2**, 13.9 |
| `QUANT_MATH_AND_EDGE_AUDIT.md`, `LOOP_AND_TRADING_PROCESS_AUDIT.md`, `MEMORY_AND_PIPELINE_AUDIT.md`, `autonomous_quant_graph_remediation.md`, `CONTINUATION_VERIFICATION.md`, `system_workflow_audit/**`, `audit/**`, `SYSTEM_TEST_RESULTS.md`, `P0_P1_FIXES_COMPLETE.md`, `BUGFIXES.md`, `CRITICAL_FIXES.md`, `RESTART_REQUIRED.md` | 9, 13, 15 |
| `analytical_frameworks_and_epistemology/0[1-7]_*.md` | 10.2, 10.7 |
| `…/bma_causal_autonomous_system/MASTER_SOURCE_OF_TRUTH_QUANT_SYSTEM.md` | **25** |
| `…/bma_causal_autonomous_system/0[1-7]_*.md` | 10.3 |
| `…/retail_quant_master_architecture/0[1-6]_*.md` | 10.1, 10.3 |
| `…/geopolitical_predictive_and_sanctions_intelligence/0[1-6]_*.md` | 10.3, 10.6 |
| `portfolio-optimization-series/**` | **21** |
| `institutional-vs-retail-applicability/**` | **22.1** |
| `dispersion_bias/**` | **22.2** |
| `quant_knowledge_base/**` (incl. `.py` artifacts) | **23** |
| `diagrams/*.json` | **20** |
| `diagrams/*.html` | 20.1 (confirmed pure renderer derivative) |
| `superpowers/specs/…-design.md`, `superpowers/plans/…md` | 12.4 |
| `INSTALLATION.md`, `LAUNCH_INSTRUCTIONS.md`, `HOW_TO_RESTART.md`, `README_TERMINAL.md`, `LOGGING_GUIDE.md`, `INCIDENT_RUNBOOKS.md`, `SECURITY.md`, `TERMINAL_FIX.md`, `ui rules.md` | 14, 12.5 |
| `*.txt` research corpus (16 files), `knowledge/**` | **24** |
| `*.pdf` (3 + 8 portfolio) | 15, 21 |
| `radar chart component reference.jpeg`, `article-extractor-1.0.0.tar.gz`, `archive_collect_evidence.log` | 15 |

## 26.3 What was wrong in v1, and is now fixed

| v1 gap | Status |
|---|---|
| PRD §07-§27 never extracted (output truncated at §07) | ✅ **Fixed** — Parts 17-18 |
| PRD §25 "Known Gaps & Drift Register" + §27 "Glossary & Evidence Index" missing | ✅ **Fixed** |
| 5 diagram JSONs unread | ✅ **Fixed** — Part 20, with per-diagram code reality checks |
| `quant_knowledge_base/` code + ABI spec unread | ✅ **Fixed** — Part 23 |
| Portfolio series summarised one line per paper | ✅ **Fixed** — Part 21, all 8 papers with formulas and the r-law |
| institutional-vs-retail + dispersion_bias one line each | ✅ **Fixed** — Part 22 |
| UAQP "13-agent registry" claimed, never listed | ✅ **Fixed** — Part 19.1 (**it has 12 rows, not 13**) |
| 163-item task list never enumerated | ✅ **Fixed** — Part 19.2 with the GOAL scorecard |
| Tri-compendium MASTER SSOT (7 gates, 24 commands, 19 modules) unread | ✅ **Fixed** — Part 25 |
| retail_quant_models_part1-3 + knowledge/types-of-analysis unread | ✅ **Fixed** — Parts 21-24 |
| "134 economic dynamics definitions" repeated as fact | 🔴 **Corrected** — code has **83 edges / 96 entities** (§17.5) |
| KG decay rated "highest-impact unfixed defect" | ⚠ **Downgraded** — the PRD shows re-validation *is* wired, in the caller (`llm_orchestrator.py:3277-3313`); the residual risk is whether `causal_edge_ids` is ever attached to proposals |
| Non-monotonic risk ladder not mentioned | 🔴 **Newly surfaced** — §25.3 finding #1 |

## 26.4 The findings that emerged only from the deep pass

1. **🔴 The non-monotonic risk ladder.** `L1_PAPER max_risk 1.00 %` > `L2_MICRO max_risk 0.25 %` (`operations/autonomy.py:38-43`). A `DEMOTE` from L2→L1 — which the circuit system performs automatically on drawdown (`evaluate_circuits:221-223`) — therefore **increases** permitted risk 4× while reducing authority. Flagged in **no** doc and **no** audit. The most safety-relevant contradiction in the entire documentation set.
2. **🔴 The autonomy circuit table is never disclosed.** §20.3 defines the schema `{key, metric, op, threshold, severity, action}` and lists only the four *actions*. The actual circuits, metrics and numeric thresholds exist only as constants in `operations/autonomy.py:189-199`. **The safety envelope is unauditable from documentation.**
3. **🔴 The N-vs-T conclusion appears in three independent subtrees and is absent from the PRD.** The portfolio series proves `r = N/T` governs estimator reliability; Pafka–Kondor shows institutional significance thresholds don't transfer at retail N; dispersion bias's own usefulness evaluation fails on the same grounds. The PRD's registry is 43 entries at N=43 with no code pointer — exactly the regime the series says is unmeasurable. **No document reconciles these.**
4. **⚠ A scale-out ladder conflict.** The MASTER SSOT's Gate 7 specifies TP1 = 1.5R (50 % closed) and TP2 = 2.5R; the code ships `scale_out_r_mult = 2.0` and `scale_out_two_r_mult = 3.0` (`core/trade_manager.py:85-86`). **The code wins** — its values are what execute.
5. **⚠ Gate 4 of 7 has no implementation and Gate 6 is missing its VaR term.** No 1,240-bar replay/expectancy/DD gate exists; `PortfolioRiskArbiter` has no VaR₉₅ term at all, and its daily-loss limit is 3 % where the corpus says < 5.0 %.

## 26.5 Standing caveat

This document is a **map, not a substitute**. Every claim in Parts 1-9, 12-14, 17-20, 23, 25 carries a `file:line`; every claim in Parts 10-11, 15-16, 19, 21-22, 24-26 is doc-sourced and status-labelled. Where the docs and the code disagree, both are recorded — **the disagreement is the finding.** Re-verify before acting: the codebase moved substantially between 2026-08-09 and 2026-09-26, and §13.9 documents 40 doc claims already invalidated by that movement.

*End of consolidated source of truth.*

