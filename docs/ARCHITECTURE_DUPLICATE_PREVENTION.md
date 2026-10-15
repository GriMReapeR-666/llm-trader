# Architecture Duplicate-Prevention Map

**Purpose:** Before any agent implements a component from an external design
(pasted research chat, paper, vendor spec), check this map. It records which
capabilities from the October 2026 architecture-review request already exist in
this codebase, which are genuinely missing, and which must NOT be built because
they would duplicate a working, tested implementation.

**Verified 2026-10-12 against the working tree.** Every module named below was
read at the source level during the analysis (not guessed from filenames).

Inherited doctrine for anything new built after this audit:
`execution_authority=False` on every research path, fail-closed on missing
data, append-only ledgers, minimal-diff ("Ponytail") editing.

---

## 1. REUSE — already implemented, do not build

### 1.1 Layered trading memory (atomic / scenario / core)
- **Composing module:** [intelligence/trading_memory.py](../intelligence/trading_memory.py) — atomic facts
  (L1), scenario documents (L2), core memories with promote/retire review,
  verified memory, narratives, per-regime retrieval, point-in-time retrieval,
  scheduled consolidation (`generate_daily_narrative`,
  `consolidate_weekly_core`, `monthly_memory_maintenance`), review queue,
  quality metrics.
- **Hierarchy wrapper:** [operations/memory_orchestrator.py](../operations/memory_orchestrator.py)
  — 4-layer L0→L3 (working/episodic/semantic/core) with importance-weighted
  decay and consolidation.
- **Retrieval backend:** [intelligence/semantic_memory.py](../intelligence/semantic_memory.py)
  — deterministic hash-token backend offline; dense neural embeddings optional
  via `TRADING_SEMANTIC_EMBEDDINGS=sentence_transformers` (no code change needed).
- **Tests:** test_memory_distillation_governance.py, test_memory_point_in_time.py,
  test_memory_quality_metrics.py, test_memory_architecture_hardening.py, etc.
- **DO NOT BUILD:** any new memory store, new embedding index, or any port of the
  external "3-layer Tencent memory" design — the structure, governance, and
  test coverage already exceed the external proposal.

### 1.2 Causal Graph RAG + multi-hop propagation
- **Graph:** [intelligence/macro_graph.py](../intelligence/macro_graph.py) —
  source-linked SQLite macro graph, edges-as-hypotheses, event-reaction journal,
  edge history, lifecycle pruning.
- **GraphRAG traversal:** [intelligence/graphrag_reasoning.py](../intelligence/graphrag_reasoning.py)
  — 2–4 hop path finding over approved edges with damped path confidence,
  lag totals, and cited prompt synthesis.
- **Propagation:** [intelligence/causal_propagate.py](../intelligence/causal_propagate.py)
  — cause→mechanism→effect scoring with lag/half-life attenuation, MAX_HOPS=3.
- **Edge governance:** [research/edge_lifecycle.py](../research/edge_lifecycle.py)
  — CANDIDATE→MEASURED→STAT_PASS→REPLAY_PASS→DEMO_MATCH→TINY_LIVE→FULL→RETIRED.
- **Autonomous validation/backtest:** [intelligence/autonomous_causal_engine.py](../intelligence/autonomous_causal_engine.py)
  — statistical validation, setup discovery, replay-floor-gated backtests,
  bounded per-pass edge reinforcement from measured reactions.
- **Markets covered:** policy entities ([intelligence/economic_dynamics_catalog.py](../intelligence/economic_dynamics_catalog.py)),
  behavioral/cascade semantics ([intelligence/behavioral_macro_catalog.py](../intelligence/behavioral_macro_catalog.py)),
  event-structural edges ([core/event_structural_edges.py](../core/event_structural_edges.py)).
- **DO NOT BUILD:** another causal engine, another knowledge-graph store, or a
  general-purpose RAG layer. Composition or new *seed* catalogs extend what exists.

### 1.3 Event cascades (1st/2nd/3rd order)
- [core/macro_event_alpha_engine.py](../core/macro_event_alpha_engine.py) —
  surprise scorecards (NFP/CPI/CB), Economic Surprise Index, standardized
  Z-delta shock decomposition, COT-regime interaction arbitrage, 3-phase event
  state machine, 4 execution models.
- [core/news_trading.py](../core/news_trading.py) — event tier classification
  (T1/T2/T3), relevance filter, blackout gate phases, LLM news summary.
- [core/hawkes_process.py](../core/hawkes_process.py) — self-exciting point
  processes for momentum-cascade / contagion regime classification (branching
  ratio regimes: POISSON_BASELINE → MODERATE_CLUSTERING → MOMENTUM_CASCADE →
  SUPER_CRITICAL).
- **DO NOT BUILD:** hand-rolled order-N cascade modeling. Hawkes branching +
  damped multi-hop propagation already cover transverse orders of impact.

### 1.4 Meta-learning (MAML / PEARL / MARS)
- [core/meta_adaptive_engine.py](../core/meta_adaptive_engine.py) — MAML-style
  few-shot regime adaptation, regime transition detection, policy blending.
- [research/mars_maml_adapter.py](../research/mars_maml_adapter.py) — explicit
  MAML outer loop over regime alpha multipliers with MARS safety-critic.
- [research/meta_rl_trainer.py](../research/meta_rl_trainer.py) — PEARL
  latent-context / RL²-PPO position-management trainer.
- Orchestration: [operations/meta_improvement_orchestrator.py](../operations/meta_improvement_orchestrator.py).
- **DO NOT BUILD:** a new MAML loop. Extend the existing adapters if regimes
  change shape, and keep it offline-only.

### 1.5 Journal reflection / post-trade distillation (ReflectEvo-style)
- [intelligence/meta_reflector.py](../intelligence/meta_reflector.py) — health
  audit: train-test generalization gap, regime-flip frequency, rolling
  expectancy/alpha-decay, automated remediation throttles.
- [intelligence/reflection_graph.py](../intelligence/reflection_graph.py) —
  LangGraph slow lane: closed trade → atomic-fact distillation → scenario
  update → causal edge weight recalibration.
- Journal infrastructure: [operations/agent_operations.py](../operations/agent_operations.py)
  (durable local-first state, event audit, memory outbox), journal_scope /
  journal_audit / journal_migration / journal_backup under operations/.
- **DO NOT BUILD:** a new reflection framework or journal store.

### 1.6 Replay / backtesting / walk-forward / PER
- Broker-cost replay: [research/broker_replay.py](../research/broker_replay.py)
  (refuses invented bars/event anchors); regime-observation replay:
  [research/historical_regime_replay.py](../research/historical_regime_replay.py)
  with cited `source_span` requirements.
- Prioritized Experience Replay: [research/replay_buffer.py](../research/replay_buffer.py)
  — TD-error priority, IS correction (α/β), thread-safe.
- Validation: [core/cpcv_backtester.py](../core/cpcv_backtester.py) (CPCV, DSR,
  PBO), [core/walk_forward_validator.py](../core/walk_forward_validator.py)
  (rolling IC + decay half-life), append-only trial ledger with tamper-evident
  hash chain: [core/strategy_trial_ledger.py](../core/strategy_trial_ledger.py).
- Purged/embargoed splits (2026-10 macro-architecture §4 request):
  [research/research_validation.py](../research/research_validation.py)
  `purged_embargoed_splits` (L287) and `purged_walk_forward_splits` (L324)
  remove overlapping label horizons and add a post-test embargo, returning
  explicit `purged`/`embargoed` index sets; CPCV adds `embargo_pct`
  (Author-C 2018 §7). Point-in-time/vintage semantics are covered by the D1c
  revision-risk decisions + `as_of` graph retrieval — **a vintage TSDB stays
  forbidden** (§1.12).
- Microstructure execution inputs (2026-10 §4): CVD via
  `calculate_cvd` in [core/market_structure_edges.py](../core/market_structure_edges.py)
  (divergence/absorption), Volume Profile via `calculate_market_profile`
  (VPOC, Value Area High/Low), DOM/order-book imbalance via
  [intelligence/market_microstructure.py](../intelligence/market_microstructure.py).
  Macro×execution fusion exists as D8 arbitration + core/bounded_autonomy_pipeline.py
  + intelligence/macro_fusion_gate.py — **DO NOT BUILD** another OBI/CVD engine.
- Dynamic risk sizing (2026-10 §4): quarter-Kelly + cost-adjusted and
  fractional variants ([core/metrics.py](../core/metrics.py)
  `calculate_quarter_kelly_sizing` / `calculate_fractional_kelly_allocation`,
  [core/risk_manager.py](../core/risk_manager.py)
  `quarter_kelly_scalar` L647), volatility budget via
  `calculate_volatility_parity_size` (core/metrics.py L630) + vol-targeting
  overlay in risk_manager (L1223).
- Harnesses: [research/alpha_backtest_harness.py](../research/alpha_backtest_harness.py),
  [research/ensemble_backtest.py](../research/ensemble_backtest.py),
  [research/continuous_backtest_pipeline.py](../research/continuous_backtest_pipeline.py),
  research/mt5_backtest_engine.py.
- **DO NOT BUILD:** another backtester, another replay buffer, another
  OBI/CVD/volume-profile engine, another fractional-Kelly/vol-budget sizer,
  or any bypass of the walk-forward/CPCV gates.

### 1.7 Regime detection (HMM + changepoints)
- [intelligence/regime_classifier.py](../intelligence/regime_classifier.py) —
  2-state GaussianHMM (Baum-Welch) with BMA weights and labeled heuristic
  fallback when hmmlearn absent.
- Changepoints: [core/bocpd.py](../core/bocpd.py) (Bayesian online changepoint
  detection). Duration/autocorrelation: core/regime_duration_tracker.py,
  core/regime_autocorrelation.py. Intermarket: intelligence/intermarket_regime.py.
  Filter integration: core/spmc_regime_filter.py, core/regime_alpha_selector.py.
- **DO NOT BUILD:** another HMM classifier or regime labeler.

### 1.8 Narrative analysis engine
- [research/decision_models/narrative_state.py](../research/decision_models/narrative_state.py)
  — versioned narrative theses with evidence-for/against logs, quantitative
  falsification tripwires (live condition evaluation), time-decay, audit trail.
- Headline triage: [data/news_triage.py](../data/news_triage.py) — Layer-0
  per-article triage (D0_1 credibility / D0_2 dedupe / D0_3 fact-vs-opinion /
  D5a escalation / D9 kill-switch / R1 routing) with deterministic fallback.
- Narrative archetypes: [intelligence/case_study_library.py](../intelligence/case_study_library.py)
  (50+ researched cases across 10 domains) matched by
  [intelligence/case_study_matcher.py](../intelligence/case_study_matcher.py)
  (CBR + Jaccard + graph-isomorphic path matching + dark-node detection).
- **DO NOT BUILD:** another narrative state machine or case-study store.

### 1.9 Wisdom extraction
- No component named "wisdom" exists **and none should**: the function is
  filled by memory distillation governance — `propose_case_study_memory`,
  `save_verified_memory`, `format_approved_lessons`, `propose/review/retire_core_memory`
  in intelligence/trading_memory.py with the review queue and quality metrics.
- **DO NOT BUILD:** a parallel "wisdom" layer. Extend the distillation review path.

### 1.10 Playbook library
- Session playbook: `TradingMemory.current_playbook` / `latest_playbook` /
  `current_playbook` retrieval-for-proposal API.
- Persistent regime-gated policies: [core/policy_library.py](../core/policy_library.py)
  (ReCAP-style library + regime-gate smooth blending, performance updates).
- Robustness playbook: [core/robustness_playbook.py](../core/robustness_playbook.py).
- **DO NOT BUILD:** another policy store.

### 1.11 Risk three-limits framework
- [core/risk_manager.py](../core/risk_manager.py) — daily drawdown,
  consecutive-loss breaker, active-trade limits, portfolio heat in R,
  equity floor, quarter-Kelly, parametric CVaR, ATR volatility scalar,
  emergency flatten, cooldown/rate limits, scale-anchor repair.
- Latency architecture tension resolved by [core/two_speed_reasoning.py](../core/two_speed_reasoning.py)
  (deliberation-tax guard) + [core/bounded_autonomy_pipeline.py](../core/bounded_autonomy_pipeline.py).
- **DO NOT BUILD:** a second risk gate. Any new metric joins RiskManager.

### 1.13 Macro liquidity triad + curve decomposition + swarm roles (2026-10 macro-architecture §1/§2/§5)
- Net USD Liquidity (Fed Assets − TGA − ON RRP):
  [data/zero_lag_macro_provider.py](../data/zero_lag_macro_provider.py)
  `compute_net_liquidity` (L473) with FRED live refine; stance naming aligns
  with `LiquidityStance` in
  [research/decision_models/macro_ontology.py](../research/decision_models/macro_ontology.py)
  (incl. `LIQUIDITY_CRUNCH` — the systemic-crisis row's stance input).
- Cross-currency basis / Pozsar CIP strain:
  intelligence/canonical_literary_mastery_engine.py (basis computation) and
  case_study_library spread distortions — DO NOT BUILD another basis model.
- Geopolitical supply shocks: D5a/b/c escalation + physical-verification
  questions, intelligence/geopolitical_macro_foresight.py,
  `CHOKEPOINT_DISRUPTION` — DO NOT BUILD another geopolitical classifier.
- Curve decomposition inputs: TIPS breakevens already registered
  (T5YIE/T10YIE in data/alternative_data_registry.py L321); real-yield and
  gold-negative-correlation semantics live in case studies + v01/v03 engines.
  Term premium is an acknowledged unmodeled input (caller-supplied estimate
  only; not fabricated).
- The institutional "4-agent swarm" maps 1:1 onto existing components
  (documented and routed by core/macro_playbook_router.py `SWARM_ROLE_MAP`):
  ingestion → data/news_triage.py; CB tone →
  intelligence/sovereign_macro_and_literary_forensics.py
  `parse_central_bank_speech_forensics` + macro_event_alpha_engine
  `speaker_tone_score`; cross-asset → intelligence/intermarket_regime.py;
  risk/sizing → core/risk_manager.py + core/bounded_autonomy_pipeline.py
  (sole promotion authority). **DO NOT BUILD** real agent processes, a
  Neo4j/NebulaGraph store, Leiden/Louvain community detection, or a vector
  DB — the graph has dozens of edges and macro_graph.py already provides
  temporal `as_of` retrieval + graphrag_REASONing multi-hop paths.

- The repo convention is **one audited SQLite store per journal scope**
  ([operations/journal_scope.py](../operations/journal_scope.py)), with hash-chained
  ledgers, working-memory compaction/retention, and housekeeping. The memory
  orchestrator's layer TTLs already express tiering semantics.
- **DO NOT BUILD:** an external TSDB (TimescaleDB/Influx/QuestDB). It would
  create a second source of truth and break point-in-time guarantees.

---

## 2. GENUINE GAPS — the only buildable items

1. **Dense semantic retrieval enablement.** Code exists
   (intelligence/semantic_memory.py backend switch); only the optional
   `sentence_transformers` install / env var is missing. Zero new code.
2. **GNN edge-weight learning over the macro graph.** Not present. Default
   recommendation: DO NOT BUILD. The graph has dozens of edges, not thousands;
   bounded reinforcement (`reinforce_approved_edges_from_reactions`) and PEARL/
   MARS adapters already adapt weights; a GNN would erode auditability. If
   ever needed, extend the existing per-pass reinforcement instead.
3. **Graded news-impact rubric.** Was missing → now implemented as a
   composition-only module:
   - [core/news_impact_score.py](../core/news_impact_score.py) — 7 dimensions
     (tier_reach, surprise_magnitude, refresh_dynamics, evidence_quality,
     source_credibility, novelty_allowance, crisis_overlay) composed from
     classify_event_tier + decompose_surprise + EconomicSurpriseIndex +
     TriageVerdict labels. Substance-dominant weights (0.60/0.20/0.15/0.10),
     unreliable-dampening (×0.5 when evidence×credibility<0.5), crisis overlay
     `(0.5 + 0.5·grade)` modulation-only, `execution_authority=False`.
   - [tests/test_news_impact_score.py](../tests/test_news_impact_score.py) —
     18/18 passing; 87/87 upstream regression passed in the four consumed
     subsystems.
   - NOT yet wired into data.data_source_pipeline / realtime_news_ingest /
     llm_orchestrator callers — wiring is the remaining (optional) work.
4. **Meta-Playbook Router + curve/quadrant/Taylor composition.
   (2026-10 macro-architecture §1/§3/§5).** Was missing → now implemented
   as a composition-only module:
   - [core/macro_playbook_router.py](../core/macro_playbook_router.py) —
     5-row event-playbook table (NFP / CPI / CB decision / geopolitical / systemic
     liquidity crisis), 4-row curve-regime table with `detect_curve_regime`
     (2s10s-slope convention, dead-band fail-closed), growth×inflation quadrant
     classifier (`classify_quadrant`, Reflation/Goldilocks/Stagflation/Deflation),
     Taylor-rule divergence (`taylor_rule_divergence` — the one genuine §1
     computation gap; sign convention: market-implied rate BELOW the rule-implied
     rate ⇒ market prices more easing ⇒ `MARKET_PRICED_DOVISH`), `route_event`
     meta-router composing classify_event_tier + detect_news_phase +
     decompose_surprise + evaluate_regime_and_crowding, and `route_intent`
     mapping the pasted 4-agent swarm onto existing components
     (`SWARM_ROLE_MAP`) without spawning any agent. All verdicts
     `execution_authority=False`; unmatched/missing inputs fail closed to
     `INSUFFICIENT_DATA`.
   - **SOURCE PROVENANCE — Bear Flattener row:** the source paste was
     truncated mid-row ("Front-end yields rise faster …"). The
     `BEAR_FLATTENER` row in `CURVE_REGIME_PLAYBOOKS` was **completed by pattern** (hawkish front-end repricing with anchored long end; driver =
     cut-expectations unwind / term-premium bid exhausted; playbook = short
     front-end duration, curve flatteners, favor cash) — flagged likewise in
     the module docstring. The other three rows are verbatim.
   - [tests/test_macro_playbook_router.py](../tests/test_macro_playbook_router.py)
     — 27 cases; covers all playbook/quadrant/curve rows, Taylor signs,
     fail-closed paths, and the `execution_authority=False` invariant.
   - NOT yet wired into llm_orchestrator callers — wiring is the remaining
     (optional) work, same status as news_impact_score.

---

## 3. CHECKLIST FOR FUTURE AGENTS

Before implementing any component from an external architecture proposal:

1. Search this file for the capability name. If §1 lists it — **stop**; extend
   the existing module that owns it, or compose, or don't build.
2. If extending `intelligence/trading_memory.py`,
   `intelligence/macro_graph.py`, `core/risk_manager.py`, or the journal
   tables: preserve point-in-time guarantees, fail-closed defaults, and
   `execution_authority=False`. Run the matching test files.
3. If a new score/flag/knowledge feed emerges: prefer a thin composition over
   existing outputs (like news_impact_score.py) rather than a new engine.
4. Respect minimal-diff edits: new files only when the capability truly does
   not exist (the bar is high; §2 shows how rarely).
5. Never grant execution authority from research/validation layers; promotion
   authority lives exclusively in the edge lifecycle and RiskManager paths.

---

*This doc is an audit record. If a §1 module gets deleted or replaced in the
future, update the map in the same change set (Ponytail: the map documents why
the duplicate-prevention line exists, so it must not go stale silently).*
