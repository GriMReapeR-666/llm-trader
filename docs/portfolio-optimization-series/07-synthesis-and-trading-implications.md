# Synthesis — What the Author-P–Author-K Series Means for This Trading System

> Cross-paper synthesis and actionable integration plan for `llm-trader`. All page/equation numbers refer to papers in this folder.

---

## 1. The one equation to memorize

```
r = N_effective / T_lookback      — control parameter for every optimizer
```

* `N_effective` is *not* your universe size. It's the dimension after factor reduction: if you use a K-factor model, N_eff ≈ N−K or the number of retained principal components. If you use no reduction, N_eff = N.
* `T_lookback` is the number of *independent* return observations used to estimate the matrix. For daily data, T ≈ calendar days × (1 − autocorrelation). For EWMA with decay α, T_eff = (1+α)/(1−α) or 1/(1−α) depending on normalization — see `04:§3`; it's *much* smaller than wall-clock T.
* Every paper collapses its results onto r. **Manage r and you manage noise. Ignore r and noise manages you.**

---

## 2. Tolerable operating zones (Gaussian baseline — real data is stricter)

| Optimizer | Safe (q₀<~1.15) | Tolerable (q₀<~1.3) | Danger (phase-transition) | Source |
|---|---|---|---|---|
| Min-variance (no filter) | r ≤ 0.2 | r ≤ 0.4 | r → 1 diverges | `02`, `06` |
| Min-variance (RMT filtered) | r ≤ 0.4–0.5 | r ≤ 0.7 | still diverges but later | `03`, `05` |
| Min-variance (EW+RMT hybrid) | r_eff ≤ 0.4 | r_eff ≤ 0.6 | EW shifts edge | `04` |
| MAD | r ≤ 0.25 | r ≤ 0.4 | r_c≈0.5 | `06` |
| ES 97% (CVaR) | r ≤ 0.2 | r ≤ 0.35 | r_c≈0.5, plus p_feas<1 | `06` |
| ES 99% | **r ≤ 0.1** | r ≤ 0.2 | r_c≈0.3, p_feas drops at 0.15 | `06` |
| Maximal Loss | avoid direct opt | — | infeasible even at r=0.1 for large N | `06` |

**Rule of thumb for daily data (T=500 ≈ 2 yr):**
- Variance sleeve: N ≤ 100 unfiltered, ≤ 200 filtered.
- ES 99 sleeve: N ≤ 50 (and even then bootstrap feasibility).
- Any sleeve with r>0.6 is *noise*, not optimization.

---

## 3. Two uses of a covariance matrix — opposite reliability

| Use | Noise sensitivity | Action |
|---|---|---|
| **Measure** risk of a *given* book: `σ² = wᵀĈw` | Low. Error ~ 1/√T, ~independent of N (`02:§4`). | Raw sample covariance is acceptable for reporting. |
| **Choose** the book: `w* = argmin_w wᵀĈw` | High. Inversion amplifies noise via small eigenvalues; error diverges at r_c. | Must filter/shrink/regularize. Never optimize on raw Ĉ when r>0.2. |

**Implementation guard:** separate two code paths — `risk_report(cov_sample, w_fixed)` and `optimal_weights(cov_filtered, constraints)`. Don't pipe the same matrix to both without comment.

---

## 4. Why textbook Markowitz understates fragility

Paper `01` shows 5–15% risk inflation for linear constraints (budget only). **Live trading always adds nonlinear constraints:** leverage cap ‖w‖₁ ≤ L, no-short w_i ≥ 0, max position, regulatory sector limits, turnover penalty. Under such constraints, `01:§4` shows degeneracy — exponentially many portfolios with indistinguishable risk but completely different holdings. The optimizer's output becomes arbitrary.

Practical test: run your optimizer 100 times on bootstrap resamples of the same window. If weight vectors scatter widely (high ‖δw‖ variance, low cosine similarity), you're in the degenerate regime — even if reported risk looks stable.

---

## 5. Filtering that works (and when it doesn't)

### Tier 1 — Always do this

- **RMT eigenvalue clipping** (`03`, `05`): eigen-decompose Ĉ, keep λ_i > λ₊, replace bulk by mean. Cheap, robust, assumption-light.
  - Uniform: λ₊ = (1 + √r)²
  - EWMA: λ₊^EW from `04:Table/Fig.1` — larger; don't use uniform threshold on EW matrix.

### Tier 2 — Structural models (powerful but biased)

- Single-index / factor models — only if factor structure *is* the truth. Paper `03` shows they hurt when truth has sectors but model has one factor.
- Ledoit-Wolf shrinkage to identity / constant-correlation / factor target — generally safe, bounded bias.

### Tier 3 — Regularization (phase-transition-aware)

- ℓ₂ penalty `λ‖w‖²` and ℓ₁ cap `‖w‖₁ ≤ c` push you away from r_c, restore feasibility for ES/ML (`06:§7` anticipates this; later Caccioli et al. quantifies). Treat λ and c as *noise-regularizers*, not just business limits.
- Weight smoothing / turnover penalty implicitly regularizes.

### What filtering does NOT fix

- Non-stationarity / regime shifts — longer T to lower r adds bias from stale regimes. The r-law assumes iid. In practice, optimal T balances noise (want large) vs non-stationarity (want small). 1–2 yr daily is a common compromise.
- Heavy tails — Gaussian r_c is optimistic. Fat-tailed data needs even smaller r for ES/ML, or a parametric tail model.
- Expected-return estimation error — the series (wisely) focuses on covariance; μ estimation is an even harder problem (see Michaud resampled frontier, Black-Litterman). Don't let a cleaned covariance blind you to μ noise.

---

## 6. Diagnostic harness to add to the codebase

Replicate Paper `03`'s horse-race as a permanent regression test:

```python
# tests/test_covariance_estimators.py — suggested harness
# 1. Define C_true as market+sectors model calibrated to your universe (estimate rho0, rho_sector from live data)
# 2. For r in [0.1, 0.2, 0.4, 0.6]:
#       simulate 500 replicas of size N x T (Gaussian)
#       estimate C_hist, C_rmt, C_shrink, C_ew_rmt ...
#       optimize each -> w_hat, evaluate q0 = true_risk(w_hat)/true_risk(w_star)
# 3. Assert that filtered q0 < unfiltered q0 and that q0 < 1.3 for safe r
# 4. For ES_99, assert feasibility rate > 0.95 at r=0.1
```

Run this in CI whenever you change `core/portfolio_optimizer.py` or `risk/covariance.py`. It catches threshold miscalibration and silent regressions.

Additional live monitors (prometheus / log):

- `r_effective` gauge per sleeve
- `q0_proxy` = in-sample predicted σ / rolling out-of-sample realized σ (should be ~1; >1.3 = noise regime)
- `weight_dispersion` = ‖w_t − w_{t-1}‖ / ‖w_{t-1}‖ across rebalances on overlapping windows (high = noise-amplified rebalancing)
- `es_feasibility_failures` counter per week

---

## 7. Integration checklist for `llm-trader`

- [ ] **Config:** add `max_r_variance`, `max_r_es99`, `ewma_alpha`, `rmt_filter: {enabled, method: eigenvalue_clip, bulk: mean}`.
- [ ] **Pre-opt guard:** compute `r = N_eff / T_eff` before any optimization; if r > threshold for chosen risk measure, either (a) refuse, (b) auto-reduce N via clustering/PCA, or (c) force ℓ₁/ℓ₂ regularization and log a warning.
- [ ] **Covariance factory:** `get_cov(method)` returns a filtered matrix; raw sample cov is *never* returned to optimizer without explicit `allow_unfiltered=True` flag.
- [ ] **EWMA path:** if using EWMA, route through `lambda_plus_EW` computation (`04`) not uniform edge.
- [ ] **Two-path risk:** `measure_risk(w, cov_raw)` vs `allocate(cov_filtered)` are separate functions with different inputs.
- [ ] **Frontier honesty:** any efficient-frontier plot shows in-sample predicted and out-of-sample realized frontiers together; large gap triggers alert.
- [ ] **Agent prompt / tool constraint:** if the LLM proposes ES_99 optimization with N=200, T=500, the tool should reject with message referencing `06:r_c(0.99)≈0.3`.

---

## 8. Reading order for a new engineer

1. This file (`07`) — 10 min.
2. `02-noisy-covariance-II-0205119.md` — r-law (core mental model).
3. `06-noise-sensitivity-various-risk-measures-0611027.md` — phase transition & feasibility (risk-measure choice).
4. `03-estimated-correlation-0305475.md` — horse-race framework.
5. `04-exponential-weighting-RMT-0402573.md` — if you use EWMA.
6. `01-noisy-covariance-I-0111503.md` + `05-random-matrix-filtering-0509235.md` — historical context / controlled demo.
7. Follow-on: Caccioli et al. (2015), Papp et al. (2016) for regularized ES if tail-risk optimization is needed.

---

## 9. Open problems / next steps not covered by this series

- **Time-varying correlations (DCC, regime-switching):** extension beyond iid/ stationary.
- **Heavy-tailed RMT:** Marčenko-Pastur for Student-t returns (Biroli et al.).
- **Transaction-cost-aware optimization under noise:** how does turnover penalty shift r_c?
- **Hierarchical clustering / nested factors:** modern alternative to eigenvalue clipping (de Prado, Raffinot).
- **Bayesian estimators with learned priors:** empirical Bayes / Ledoit-Wolf nonlinear shrinkage.

Each of these is a natural next folder in `docs/`.

---

## 10. One-sentence takeaway per paper (for code comments)

```python
# 0103107: RiskMetrics VaR@95% looks good by accident; test VaR@99% and multi-day horizons.
# 0111503: Linear Markowitz is 5-15% robust; nonlinear constraints cause degeneracy.
# 0205119: r=N/T is the only thing that matters; r<=0.2 safe, r>=0.6 broken; measurement << optimization in noise sensitivity.
# 0305475: Benchmark estimators on synthetic truth, not history; RMT clip is the robust default.
# 0402573: EWMA needs EW-specific RMT edge; EW alone without filtering is worse than uniform.
# 0509235: RMT filtering halves the frontier gap in simulation.
# 0611027: Every risk measure has a critical r_c where q0 diverges; ES/ML become infeasible with finite prob — add l1/l2 regularization.
```
