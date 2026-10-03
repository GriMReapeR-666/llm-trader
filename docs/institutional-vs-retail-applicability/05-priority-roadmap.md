# 05 — Priority Roadmap: What to Build, in What Order, and What Not to Build

> Evidence-backed against `04-real-problems-gap-analysis.md` problems `1–9`. All effort estimates for a solo engineer with the current codebase.

---

## P0 — Build now (1–3 days, directly prevents P&L bleed)

| # | Item | Solves | Effort | Acceptance criteria |
|---|---|---|---|---|
| **P0-1** | **Wire alpha decay → ensemble knockout** | #1 | 0.5 day | `AlphaDecayMonitor.check_decay()` (`core/alpha_decay_monitor.py:246`) output filters `combine_alphas(alpha_columns=alive_only, min_ir=0.3)`; dead alphas (`DEAD`/`REVERSED` `alpha_decay_monitor.py:333`) get weight 0 without manual restart; reason logged to `lessons.jsonl` + `trading_memory.py` Level 2. Test: degrade an alpha's IC to -0.05 and confirm weight→0 within `consecutive_bars=5`. |
| **P0-2** | **Enforce T_eff ≥ 100 or shrink** | #2 | 0.5 day | Wherever `ic_window=60` (`alpha_ensemble.py:205`, `alpha_decay_monitor.py:39`) is used with N≈23, compute `r=N/T` and log `WARNING` if `r>0.25`; add James-Stein shrinkage toward equal weights: `w_shrunk = (1-λ)w_ic + λ(1/N)` with `λ = min(0.5, r)`. No new ML needed. Backtest: IC_IR stability (first/second half correlation `alpha_ensemble.py:308`) must not degrade vs baseline. |
| **P0-3** | **AQuA-style sealed sandbox audit** | #4 | 0.5 day | Freeze `intelligence/feature_schema.py` + evaluator (`core/cpcv_validation.py`) for any alpha-gen loop; assert that proposed alphas use only primitives in `core/alpha_factors.py:24` DSL (no arbitrary `exec`). Add CI check: if `feature_schema.py` hash changes without `OPERATOR_SIGNATURE`, fail `research/promotion_package.py`. |
| **P0-4** | **Separate measurement vs optimization** | #2 | 0.25 day | Split `combine_alphas()` into `measure_ic()` (report) vs `allocate_weights()` (decide). `risk_report` path never calls shrinkage override; `allocation` path always does. Doc in `core/alpha_ensemble.py:358`. |

**Why P0:** Each fixes a hot path where current code silently degrades (dead-alpha bleed, noisy weights, overfit). No new model, no GPU.

---

## P1 — Build next (1–2 weeks, regime & causal leverage)

| # | Item | Solves | Effort | Acceptance criteria |
|---|---|---|---|---|
| **P1-1** | **Learned regime→alpha multipliers (bandit)** | #3, #7 | 3–5 days | Replace static `REGIME_ALPHA_MULTIPLIERS` (`alpha_ensemble.py:90`) with bandit/learned table: `research/contextual_bandit.py` Thompson sampling updates `multiplier[regime][category]` from realized trade R. Keep static as Bayesian prior; learn posterior online. Gate by `core/bocpd.py` posterior instead of threshold mix where available. Success: multiplier stability ↑ and decay alerts ↓ by >20% in 2-week paper trading. |
| **P1-2** | **Causal alpha pruning (CLEAR/CRL-ABSO lite)** | #2, #5 | 4–6 days | Use `intelligence/autonomous_causal_engine.py` + `economic_dynamics_catalog.py:134` to score each alpha's causal path (e.g., `alpha_012` volume→price divergence has causal plausibility; `alpha_036` composite may not). Prune alphas with no causal chain before `compute_ic_weights`. Add 23×23 alpha correlation → hierarchical clustering (one per cluster) as first cheap step (≤1 day) before causal filter. |
| **P1-3** | **BOCPD → regime posterior** | #7 | 2 days | Feed `core/bocpd.py` / `regime_duration_tracker.py` posterior into `RegimeDetectionEngine.detect_regime()` (`hierarchical_portfolio.py:66`) as an additional input, not replacement. Blends threshold prior with Bayesian changepoint likelihood. Log `bocpd_confidence` alongside `RegimeState`. |
| **P1-4** | **Kronos+ensemble AND-gate audit** | #8 | 1 day | Inspect + log `intelligence/opportunity_fusion.py` fusion logic; enforce that `trade` requires `kronos_direction == ensemble_direction` and `causal_agreement==True` (already conceptually in `opportunity_fusion.py`) with a measurable `fusion_disagreement_rate` metric. Alert if disagreement > 60%. |

---

## P2 — Institutional tripwires (build only if triggered — see `02-author-p-author-k-applicability.md:§4`)

| # | Item | Tripwire | Effort | What to build |
|---|---|---|---|---|
| P2-1 | RMT eigenvalue clipping + r-budget | N≥10 multi-symbol sleeve approved | 1 day | `get_cov_filtered()` with `λ₊=(1+√r)²`, bulk→mean, trace preservation; hard refusal if `r>0.2` without filter (`docs/author-p-author-k-portfolio-optimization-series/07-synthesis-and-trading-implications.md:§7` guard). |
| P2-2 | EW+RMT hybrid edge | EWMA covariance with N≥10 | 1 day | `lambda_plus_EW(r_eff, alpha)` per `04-exponential-weighting-RMT-0402573.md:§3`. |
| P2-3 | MARS Safety-Critic (full) | Multi-strategy portfolio with distinct risk profiles | 1 week | Per-strategy safety critic estimating tail risk; dynamic ensemble weight from meta-controller; replaces `DynamicStrategyRouter` `hierarchical_portfolio.py:148` for N>1. |
| P2-4 | ES_99 / MAD optimization | Portfolio ES optimization approved | 3 days | Feasibility check (detect in-sample arbitrage LP unboundedness) + ℓ₁/ℓ₂ regularization; refuse ES_99 when `r>0.1` (`06-noise-sensitivity-various-risk-measures-0611027.md`). |
| P2-5 | Hierarchical Risk Parity / Ledoit-Wolf | N≥50 universe scanning | 2 days | Alternative to RMT where RMT may over-shrink. |

---

## P3 — Do NOT build (theater ledger)

| Item | Why not | Opportunity cost if built |
|---|---|---|
| LA-NAS-Lock sub-ms NAS | No latency problem at M15; spread dominates | Diverts 2 weeks to GPU infra that increases tail risk |
| vLLM / Dynamo serving | Single polling process, not 1000 QPS | Over-engineers ops at the expense of P0 decay fix |
| A-share / crypto-specific tuning | No A-share/crypto account; FX microstructure differs | Pollutes `alpha_factors.py` with non-portable assumptions |
| Generic Transformer MSFT R² 0.972 path | Likely leakage; Kronos already superior | Adds a second forecaster to fuse badly |
| Standalone CRL-ABSO full research stack | Concept ports, but full reimplementation before causal pruning lite is waste | P1-2 lite is 20% effort for 80% gain |

> **Rule of thumb:** If an item appears only in Kimi's "Cutting-Edge (2024–2026)" Tier 3 without a `file:line` it plugs into, it's P3 until proven otherwise.

---

## 30/60/90 plan

- **Day 0–7 (P0):** Ship decay knockout + shrinkage + sealed audit + measurement split. Paper-trade; measure dead-alpha bleed reduction and OOS Sharpe vs baseline with `cpcv_backtester.py`.
- **Day 8–30 (P1.1–P1.3):** Bandit regime multipliers + BOCPD posterior + correlation clustering. Require `journal readiness-status` green before turning on learned multipliers.
- **Day 31–90 (P1.4+P2 if tripped):** Causal pruning + fusion audit; only if P0/P1 metrics stable, consider tripwire-triggered RMT work.

---

## Verification checklist (run after every lane)

```
pytest -q
python mt5_trader.py journal integrity
python mt5_trader.py journal readiness-status
# Paper-specific:
python -m core.cpcv_validation  -- gap < 30%
python -m research.broker_replay --predicted_vs_realized frontier
# New metrics (add these):
# - alive_alpha_count, dead_alpha_count, alpha_rotation_rate
# - ic_ir_stability, alpha_cluster_count
# - regime_multiplier_learned_vs_static delta
# - fusion_disagreement_rate
```

---

## One-line decision rule for future paper proposals

> **"Show me the `file:line` it fixes and the CPCV gain, or it goes to P3."**

Any paper that cannot be mapped to a hot `core/*.py:line` in `04-gap-analysis` without adding a new hot path for an account type you don't have is institutional theater — read it, don't build it.
