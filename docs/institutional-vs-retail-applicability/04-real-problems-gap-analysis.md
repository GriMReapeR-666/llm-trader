# 04 — Real Problems in This Codebase: Gap Analysis

> Every row has a **file:line** anchor. If a "problem" has no anchor, it's hypothetical — ignore it.

---

## Current system snapshot (2026-08-28)

| Layer | Module | What it does | Grade today |
|---|---|---|---|
| Alpha generation | `core/alpha_factors.py:1` | 23 time-series-ified 101 Alphas + primitives | Retail ✓ |
| Alpha ensemble | `core/alpha_ensemble.py:1` | IC/IR-weighted composite + regime multipliers | Retail ✓ (naive weights) |
| Alpha decay | `core/alpha_decay_monitor.py:1`, `alpha_decay_state.json` | Rolling Spearman IC (60-bar), decay/reversal/dead state machine | Retail ⚠️ thresholds brittle |
| Regime | `core/hierarchical_portfolio.py:60` | 4-regime detector (Hurst+ADX+ATR)+ router + heat cap | Retail ⚠️ rule-based, no learning |
| Risk | `core/risk_manager.py:15`, `trading_safety.py`, `margin_safety.py` | 4 circuit breakers, 1R sizing, heat cap, margin gate, streak/Kelly scaling | Retail ✓ (strongest layer) |
| Validation | `core/cpcv_validation.py`, `cpcv_backtester.py`, `research/sample_gate.py`, `promotion_package.py` | Purge/CPCV, 100-sample gate, promotion guard | Retail ✓ (exists, coverage unknown) |
| RL / Meta | `research/ppo_trainer.py`, `meta_rl_trainer.py`, `kronos_mt5_adapter.py` | PPO, meta-RL, Kronos tokenization | Hybrid (exists, utilization low) |
| Causality | `intelligence/autonomous_causal_engine.py`, `macro_graph.py`, `economic_dynamics_catalog.py` | 134-rule dynamics catalog, causal DAG, agreement gate | Hybrid ✓ |
| Execution | `core/order_execution.py`, `execution_policy.py`, `trade_manager.py` | PaperBroker, per-symbol policy, trailing/BE | Retail ✓ |

---

## 9 real problems vs 6 false problems

### Real (fix these — money at stake)

| # | Real problem | Evidence (file:line) | Current gap | Paper that fixes it | What to build |
|---|---|---|---|---|---|
| 1 | **Alpha decay is detected but not acted on** — monitor flags `DEGRADED/REVERSED/DEAD` (`alpha_decay_monitor.py:325`) but `alpha_ensemble.py:329` still IC-weights dead alphas until next manual retune; `alpha_decay_state.json` stores IC history but not *cause*. | `alpha_decay_monitor.py:325` returns status, `alpha_ensemble.py:422` weights by `ic_ir` with no dead-alpha knockout; `lessons.jsonl` not wired to alphas. | No experience memory, no auto-rotation. | **FactorMiner** (experience memory) + **AlphaAgent** (originality/complexity) `03:Tier A`. | Wire `AlphaDecayMonitor` → `combine_alphas(min_ir=0.3)` knockout + write cause → `lessons.jsonl` + `trading_memory.py` Level 2 (pattern). See `05-roadmap P0-1`. |
| 2 | **IC weighting is naive and noisy** — `compute_ic_weights()` (`alpha_ensemble.py:329`) does `max(IC_IR - min_ir, 0)` then normalize; with T=60, IC_IR estimate itself has high variance; no shrinkage, no causal pre-filter → weight overfits. | `alpha_ensemble.py:205` `window=60`, `_compute_half_life:294` may return 0; `combine_alphas:358` no shrinkage. | Author-P `02:§2` r-budget ignored: T_eff=60 for N=23 signals ⇒ r≈0.38, already noisy. | **Author-P–Author-K discipline** (`02`) + **CLEAR / CRL-ABSO** causal feature selection + Ledoit shrinkage (not in Kimi list but implied by `02`). | Add `shrinkage_ic_weights` (James-Stein toward equal/Bayes prior) + causal pre-filter from `autonomous_causal_engine` before weighting. Budget `T≥100` or shrink harder. |
| 3 | **Regime router is rule-based and stale** — thresholds `VIX>=32 or atr_pct>=95 → CRISIS 0.80` (`hierarchical_portfolio.py:93`) are hard-coded, never learned; `REGIME_ALPHA_MULTIPLIERS` (`alpha_ensemble.py:90`) are static 1.5/0.3 tables, never updated from P&L. | `hierarchical_portfolio.py:93`, `alpha_ensemble.py:90` — no gradient, no meta-learning. | Crises evolve; static cutoffs misclassify. | **MARS** Safety-Critic meta-adaptive controller (`03:Tier C`). | Replace static table with learned `regime→alpha_category` multiplier updated via Thompson/bandit (`research/contextual_bandit.py` exists) or MAML init per `meta_rl_trainer.py`. Keep rule-based as fallback. |
| 4 | **Overfitting / leakage risk is structurally mitigated but not sealed** — CPCV gates exist (`cpcv_validation.py`, `sample_gate.py`) but AQuA "sealed sandbox" principle is violated wherever researcher code can mutate evaluator (e.g., factor redefinition without fixed `feature_schema.py` lock). | `research/research_validation.py`, `core/cpcv_backtester.py`, `intelligence/feature_schema.py` — need audit that evaluator is immutable across AQuA-style loops. | An LLM agent generating alphas (future) could cheat metrics. | **AQuA** sealed sandbox (`03:Tier A`). | Formalize: factor DSL (only primitives in `alpha_factors.py:24`), frozen `feature_schema.py`, immutable evaluator; agent proposes only via config diff, not code. |
| 5 | **Spurious correlations among 23 alphas** — many alphas reuse primitives (`ts_rank`, `corr`, `rank`) and likely correlate; ensemble with no decorrelation double-counts same signal, inflates turnover `alpha_ensemble.py:274`. | `alpha_factors.py:105`–`442` share motifs; `ALPHA_CATEGORIES` has 6 buckets but no clustering. | `Author-P 03:§3` RMT intuition: noisy bulk among alphas, not just assets. | **CRL-ABSO / CLEAR** causal filtering + hierarchical clustering (Author-C HRP) even at N=23 alpha correlation matrix (small but real). | Alpha correlation matrix (23×23) → hierarchical clustering → one representative per cluster before weighting; causal DAG prune. |
| 6 | **Risk is trade-level, not sequence-level** — `risk_manager.py` is excellent at per-trade 1R + daily/weekly/peak hot halts, but has no **sequence-aware** tail control (consecutive correlated losses beyond `consecutive_losses:57`). No copula tail state beyond heat compression. | `risk_manager.py:65` `max_portfolio_heat_r=4.0`, `check_portfolio_heat:604` — static cap, only compressible via `set_heat_budget_r:558` when copula triggers. | Needs dynamic heat tied to live tail regime, not just 4R→1R binary. | **MARS** Safety-Critic + **copula_tail_risk.py** already exists — wire tighter. | Connect `copula_tail_risk.py` → `set_heat_budget_r()` continuously (already partially done), and add per-regime heat (CRISIS→0.5R). |
| 7 | **Meta-learning lane exists but is unused** — `research/meta_rl_trainer.py` and `core/bocpd.py` / `regime_duration_tracker.py` are present but router doesn't consume them; regime probabilities are cheap heuristics, not posterior updates. | `hierarchical_portfolio.py:65` `detect_regime()` ignores `bocpd.py`, `regime_duration_tracker.py`. | Leaving BOCPD on the shelf. | **MARS** + **BOCPD** (Fourier / Bayesian) | Feed `bocpd.py` change-point posterior into `RegimeState.probabilities` instead of threshold mix; keep thresholds as prior. |
| 8 | **Kronos vs ensemble non-integration** — `research/kronos_bridge.py` forecasts price, `alpha_ensemble.py` forecasts direction; no fusion, no agreement gate; two brains, one mouth. | `intelligence/opportunity_fusion.py` + `opportunity_bus.py` exist but fusion logic unknown (not inspected). | Need evidence fusion is real, not placeholder. | **FinAgent** multimodal fusion idea (tool-augmented verification) ported minimally. | Audit `opportunity_fusion.py:??` that Kronos + ensemble must agree (AND gate) before `DeterministicValidator` pass; log disagreement rate. |
| 9 | **Walk-forward validation coverage unknown** — `research/baseline_research.py`, `broker_replay.py` claim replay fidelity, but 154 test suites (`tests/`) not inspected for walk-forward OOS vs IS gap (`Author-P 05:§3` frontier honesty). | Tests exist but no recent `readiness_report.py` output inspected. | Could be overfitting silently. | **Author-C CPCV + deflated Sharpe** (`Advances in Financial ML`) + **AQuA** metric discipline. | Run `pytest -q && python mt5_trader.py journal readiness-status` and enforce predicted vs OOS frontier gap < 30% before live. |

### False / theater problems (do NOT fix — diverts capital from real problems)

| # | False problem | Why it's not real here | Distractor paper |
|---|---|---|---|
| F1 | Sub-ms execution latency | M15 bar, broker spread >> latency; `core/order_execution.py` already handles tick. | LA-NAS-Lock |
| F2 | A-share T+1 / price limits | FX spot has no T+1; no A-share account. | FinPFN, Logic-Q A-share |
| F3 | N=400 covariance RMT | N=1; no covariance to invert. | Author-P–Author-K direct RMT |
| F4 | Multi-agent GPU serving | Single process polling MT5. | vLLM, Dynamo |
| F5 | ES_99 portfolio optimization | No multi-asset ES sleeve; retail risk is 1R discrete. | Author-K ES phase transition |
| F6 | Crypto tick microstructure | No crypto account today (`mt5_trader.py --symbol EURUSD`). | FinRL crypto, CTBench |

---

## Dependency graph (real problems only)

```
[4] Sealed sandbox (AQuA)
        ↓ gates
[1] Alpha decay memory + rotation ──→ [2] Shrinkage / causal IC weighting ──→ [5] Alpha clustering
                                                      ↓
[3] Learned regime→alpha multipliers ←── [7] BOCPD posterior ──→ [6] Dynamic heat (Safety-Critic)
                                                      ↓
                                          [8] Kronos+ensemble fusion
                                                      ↓
                                          [9] Walk-forward honesty check (CPCV gap)
```

Fix in topological order — `4` and `1` are roots; `9` is the exit gate that certifies all prior fixes.
