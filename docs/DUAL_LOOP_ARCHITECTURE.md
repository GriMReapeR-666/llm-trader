# Dual-Loop Autonomous Trading Architecture

**Source:** User's institutional analysis (2026-10-12) of how this codebase's
autonomous LLM learns from trading. **Verified against the working tree
2026-10-12:** every module cited below was opened at source level and its real
repo-relative path confirmed before being documented here. The paste's
hard-coded `file:///` absolute links have been converted to relative repo
paths, and any section truncated in the paste (§4, §5) has been completed
strictly from the actual code — nothing invented.

**Companion doc:** see [ARCHITECTURE_DUPLICATE_PREVENTION.md](ARCHITECTURE_DUPLICATE_PREVENTION.md)
for the reuse/do-not-build map these loops are composed from. This document
describes how those verified components *wire together*; that document governs
what may or may not be built. Cross-references only — no duplicated map here.

Core doctrine: the LLM **never** performs online gradient backpropagation on
its base model weights during live trading (catastrophic forgetting,
non-deterministic drift, latency, hallucination risk). Learning happens in two
separated loops: in-context memory (fast loop context injection) and offline
parametric RL (slow loop training), never the reverse.

---

## The Dual-Loop Diagram (verbatim from source analysis)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        DUAL-LOOP AUTONOMOUS TRADING ARCHITECTURE                       │
│                                                                                        │
│  FAST DELIBERATIVE LOOP (Per-Bar / Per-Tick Execution)                                 │
│  [News/RSS/Macro APIs] ──► [GraphRAG Multi-Hop DAG] ──► [3-Layer Memory Retrieval]     │
│                                                                 │                      │
│                                                                 ▼                      │
│  [MT5 Broker Execution] ◄── [Deterministic Risk Gates] ◄── [Deliberative LLM Graph]    │
│            │                                                    ▲                      │
│            │ (Execution Ticket & PnL)                           │ (Context Injection)  │
│            ▼                                                    │                      │
│  SLOW REFLECTION & REPLAY LOOP (Post-Trade / Daily / Offline)   │                      │
│  [Append-Only Reward Ledger] ──► [LangGraph Reflection Graph] ──┘                      │
│            │                                                                           │
│            ├──► L1 Atomic Facts ──► L2 Scenario Playbooks ──► L3 Audited Core Rules    │
│            ├──► Bayesian Edge Weight Recalibration on MacroGraph                       │
│            └──► Meta-RL (PEARL) & PPO Context Adaptation + EWC Forgetting Prevention   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. In-Context Meta-Learning (Prompt & Memory Space)

The LLM updates its knowledge via the **3-Layer Memory System**
(`Atomic → Scenario → Core`), all inside
[intelligence/trading_memory.py](../intelligence/trading_memory.py) with the
governance and point-in-time guarantees described in
[ARCHITECTURE_DUPLICATE_PREVENTION.md](ARCHITECTURE_DUPLICATE_PREVENTION.md) §1.1:

- Closed-trade outcomes are decomposed into R-multiples, MAE and MFE
  (trade-management metrics tracked via journal columns on the trade tables;
  see [docs/MFE_MAE_FINDING_CORRECTION.md](MFE_MAE_FINDING_CORRECTION.md))
  and distilled into durable scenario playbooks (L2).
- Distillation and audit run per trade → L1 atomic fact; repeated/at-scale
  facts graduate to L2 scenario documents; only human-reviewed, repeatedly
  confirmed facts promote to L3 core memories
  (`propose/review/retire_core_memory`, review queue,
  `format_approved_lessons`).
- On subsequent sessions, the retrieval engine injects matching past
  experiences into prompt context (`retrieve_for_proposal`,
  `retrieve_scenarios`, retrieval-outcome linking
  `link_retrievals_to_trade` for retrieval-attribution feedback). The
  reflection lane that performs the distillation is
  [intelligence/reflection_graph.py](../intelligence/reflection_graph.py)
  (LangGraph slow lane: closed trade → atomic fact → scenario update → edge
  weight recalibration).

_NO NEW BUILD license: §1.1 of the duplicate map. Extend,
never re-implement._

_Fast-loop input surface note: the analysis session opened
[intelligence/geopolitical_macro_foresight.py](../intelligence/geopolitical_macro_foresight.py)
(Module 02) alongside the memory work — verified content: COMPASS 7 strategic
bearings, Caldara-Iacoviello GPR threat/action divergence, SIPRI 8-domain
early-warning tripwires, yield-curve-inversion 4-stage causal transmission,
2×2 scenario matrix, and Bayesian Model Averaging across 5 geopolitical
regimes (A1–A5). It feeds the deliberative graph's macro-context side of the
fast loop and is listed in the duplicate map's reuse territory — not a
buildable gap._

## 2. Parametric Reinforcement Learning (Action & Weight Space)

Downstream RL agents adapt trade-management actions —
`HOLD`, `TIGHTEN_SL`, `TRAIL`, `PARTIAL_CLOSE`, `FULL_EXIT`
(`MANAGEMENT_ACTIONS`, defined in
[research/kronos_trading_integrator.py](../research/kronos_trading_integrator.py)
and consumed by the trainer) — using **PPO+GAE**
([research/ppo_trainer.py](../research/ppo_trainer.py)) conditioned on a
**PEARL-style latent context vector** z ∈ ℝ⁸
(`LATENT_DIM = 8` in [research/meta_rl_trainer.py](../research/meta_rl_trainer.py)):

- [research/ppo_trainer.py](../research/ppo_trainer.py) trains
  **only inside a simulator** (`KronosRLMarketSimulator` /
  `MT5KronosRLSimulator` via `_make_simulator`); episodes start with an
  already-open position (`open_management_episode`) so the policy learns
  manage-not-enter. Torch MLP actor-critic with a pure-numpy fallback.
- [research/meta_rl_trainer.py](../research/meta_rl_trainer.py) encodes
  in-task transitions into the latent (`ContextEncoderNet`, PEARL-style)
  then conditions a PPO policy on it (RL²-PPO); tasks are
  symbol × regime × setup (`task_key`). Pure-numpy RL² fallback when torch
  is absent.
- Complementary proposer-side training: offline GRPO updates only the
  BUY/SELL/HOLD proposer policy with a frozen deterministic risk gate
  ([research/grpo_proposer.py](../research/grpo_proposer.py)).
- Every public result hard-codes `execution_authority=False` and
  `promotion_safe=False` — the frozen-gate boundary that separates this
  loop from execution.

_NO NEW BUILD license: §1.4 of the duplicate map._

## 3. Catastrophic Forgetting Prevention (EWC)

**Elastic Weight Consolidation** adapted for Q-learning —
[core/ewc_forgetting_prevention.py](../core/ewc_forgetting_prevention.py) —
snapshots importance Ω(s,a) so adapting to high-volatility regimes does not
degrade performance in calm trending regimes:

- In neural EWC importance = Fisher information; here (Q-table regime):
  `importance(s,a) = visits(s,a) × |Q(s,a)|` (visit count × magnitude —
  high traffic + high value ⇒ important).
- Update rule: Q-new drops by `λ × importance(s,a) × (Q_new − Q_old)²`
  (`DEFAULT_EWC_LAMBDA = 0.5`, higher ⇒ less forgetting but slower
  adaptation), with per-regime `ImportanceSnapshot`s retained
  (`DEFAULT_MAX_SNAPSHOTS = 5`) and multi-task consolidation combining
  importance across all past regimes (`get_combined_importance`), not just
  the latest.
- Wired into the Q-learning path via
  [research/rl_trainer.py](../research/rl_trainer.py)
  (`snapshot_regime` / `ewc_update`) and orchestrated through
  [intelligence/llm_orchestrator.py](../intelligence/llm_orchestrator.py).
- Tests: `tests/test_ewc_forgetting.py` (snapshot/importance/penalty/
  combined-importance/xdist-safe persistence).

_NO NEW BUILD license: this is a verified core/ module with test coverage._

## 4. Walk-Forward Sealed Replay (AQuA)

The **AQuA Sealed Sandbox** —
[core/self_improvement_loop.py](../core/self_improvement_loop.py) — is the
improvement-proposal validation environment:

- 6-step cycle: BASELINE (capture
  [core/self_improvement_loop.py](../core/self_improvement_loop.py)
  `BaselineSnapshot`: IC/IR, regime state, heat, win-rate/Sharpe, CPCV
  PBO/DSR) → ANALYZE (LLM identifies underperforming components) →
  PROPOSE (LLM suggests parameter/weight adjustments within a constrained
  DSL) → VALIDATE (sealed sandbox evaluates proposals against baseline) →
  APPLY (only beat-baseline improvements deploy) → TRACK (improvement
  history prevents regression).
- Sealed-sandbox principle (AQuA 2026): the evaluation environment is
  FIXED and cannot be modified by the proposals being evaluated; this
  prevents generation leakage and selection leakage.
- The walk-forward replay inside it
  (`create_replay_simulator`) is deliberately honest: it replays
  broker-reconciled closed trades and applies exactly ONE class of change
  legitimately — position sizing (R multiples are size-invariant, so a
  sizing change moves only the cost-scaled equity path/drawdown, never
  per-trade edge). Proposals outside replay capability (alpha re-weights,
  entry thresholds, regime params) return **None** and the engine records
  DEFERRED rather than fabricating a baseline-copy result.
- Statistical sealing downstream: promotion quantifies with deflated-Sharpe
  over append-only true trial counts
  ([core/strategy_trial_ledger.py](../core/strategy_trial_ledger.py)) and
  combinatorial purged CV ([core/cpcv_backtester.py](../core/cpcv_backtester.py));
  no agent may decrement `n_trials`.

_NO NEW BUILD license: §1.6/overfit-gate territory of the duplicate map._

---

## Fast-loop ↔ Slow-loop boundaries (the load-bearing seams)

| Seam | Owner modules | Invariant |
|---|---|---|
| Context injection | intelligence/knowledge_retrieval.py, intelligence/semantic_memory.py, retrieval_for_proposal | Relevance-ranked, point-in-time, source-linked |
| Reward ledger | operations/reward_ledger.py | Append-only (SQLite triggers abort UPDATE/DELETE), R_net clipped |
| Edge recalibration | intelligence/reflection_graph.py + intelligence/autonomous_causal_engine.py (`reinforce_approved_edges_from_reactions`) | Bounded Δ/pass, lifecycle-gated |
| RL training | research/ppo_trainer.py, research/meta_rl_trainer.py, research/grpo_proposer.py | Simulator-only, offline, frozen risk gate |
| EWC anchors | core/ewc_forgetting_prevention.py via research/rl_trainer.py | Per-regime snapshot, bounded λ penalty |
| Sealed evaluation | core/self_improvement_loop.py | Fixed sandbox, honest replay, DEFERRED > fabricated |
| Execution authority | core/risk_manager.py + core/bounded_autonomy_pipeline.py + edge lifecycle | The ONLY authority path; nothing above flips it |

## What NOT to do (pointers, not a duplicate map)

- Every module above is listed in
  [ARCHITECTURE_DUPLICATE_PREVENTION.md](ARCHITECTURE_DUPLICATE_PREVENTION.md)
  §1 with a DO NOT BUILD verdict. This doc adds the wiring/seam view; consult
  the map before proposing any new engine that overlaps them.
- Online base-model fine-tuning during live trading is prohibited doctrine —
  adaptation is in-context (§1) or offline-simulated parametric (§2/§3/§4).
- The forwards-keeping rule of the map applies here too: if a seam owner is
  replaced or deleted, update BOTH docs in the same change set.
