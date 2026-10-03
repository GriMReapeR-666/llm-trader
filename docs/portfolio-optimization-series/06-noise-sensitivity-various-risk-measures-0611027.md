# Paper 6 — Noise Sensitivity of Portfolio Selection under Various Risk Measures (physics/0611027)

> **Bibliography:** Author-K, I., Author-P, S. & Nagy, G. (2006). *Noise Sensitivity of Portfolio Selection under Various Risk Measures.* arXiv:physics/0611027. Published as J. Banking & Finance 31, 1545–1573 (2007). DOI: 10.1016/j.jbankfin.2006.12.001.  
> **PDF:** `0611027.pdf` (33 pp. — the longest and most important paper in the series)  
> **Role in series:** Capstone theoretical paper. Extends the r-law from variance to **all major risk measures** and uncovers a **phase transition** with feasibility collapse for coherent measures.

---

## Abstract (essence)

Study four risk measures: **variance, mean absolute deviation (MAD), Expected Shortfall (ES), and Maximal Loss (ML, worst-case loss).** Introduce a unified estimation-error measure. Via simulation (Gaussian ensemble, varying N and T) find: (i) noise is very strong under *all* measures; (ii) asymptotically error depends only on r=N/T and diverges at a **critical r_c** that depends on the risk measure; (iii) divergence is a phase transition with critical exponents, sample-to-sample weight fluctuations, etc.; (iv) **variance & MAD are always feasible below r_c, while ES & ML become infeasible with finite probability even well below r_c** (probabilistic feasibility problem — the optimization may have *no finite solution* because a dominating arbitrage portfolio appears in-sample). Filtering exists for variance but not for the others, so robustness must become a first-class criterion for choosing a risk measure.

---

## Core thesis

> **Not all risk measures are equally noise-robust, and coherence (sub-additivity + homogeneity) comes at a steep noise-sensitivity cost.** ES and ML, though theoretically superior (coherent, tail-sensitive), are far more fragile to estimation error than variance — and can become *undefined* (unbounded below) due to an in-sample arbitrage that doesn't exist in the true distribution.

---

## Risk measures defined (as used in paper)

| Measure | Definition (minimization form, budget Σw=1) | Properties |
|---|---|---|
| **Variance** | σ²(w) = wᵀC w | quadratic, convex, sample estimate is Wishart |
| **MAD** | MAD(w) = (1/T) Σ_t |Σ_i w_i (x_it − μ_i)| | LP-solvable, convex, less tail-sensitive |
| **Expected Shortfall (ES, CVaR)** at level α | ES_α(w) = min_ε { ε + (1/((1−α)T)) Σ_t [−wᵀx_t − ε]₊ } | coherent, LP-solvable, avg loss beyond VaR; α=0.97–0.99 typical |
| **Maximal Loss (ML)** | ML(w) = max_t (−wᵀx_t) | coherent, LP-solvable, limit α→1 of ES; extremely tail-sensitive |

All are optimized via parametric/non-parametric estimators: sample covariance for variance, sample mean of functions for MAD/ES/ML (historical / empirical estimator). No parametric tail model.

### Sensitivity measure

Define for each replica k (sample):
```
q₀(k) = risk_true( ŵ(k) ) / risk_true( w⁰ )    ≥ 1  (or ∞ if infeasible)
```
and aggregate as ⟨q₀⟩ or distribution over replicas. Divergence of ⟨q₀⟩ signals phase transition. Define also **feasibility probability** p(N,T) = fraction of samples where optimization is bounded (has finite solution). For variance/MAD, p=1 below r=1; for ES/ML, p<1 and drops with r.

---

## Method

- **Ensemble:** For each (N,T,α) point, generate 500–2000 replicas of T iid returns from N(0, I) (identity covariance — cleanest; later also market model as robustness check). For each replica:
  1. Estimate risk empirically from the sample.
  2. Solve portfolio optimization (QP for variance, LP for MAD/ES/ML) with budget constraint Σw=1 (and optionally expected-return constraint).
  3. If LP is unbounded (ES/ML), flag as infeasible.
  4. If feasible, compute ŵ and evaluate q₀ using *true* risk (known analytically for N(0,I)).
- **Sweep:** N=10..100, T=50..2000, r=N/T in [0.05, 2], α ∈ {0.9, 0.95, 0.97, 0.99} for ES.
- **Finite-size scaling:** Extract critical r_c(N,T) and check collapse onto r=N/T in thermodynamic limit.
- **Replica-theory analysis:** Map ES/ML optimization to a statistical-mechanics problem with quenched disorder; derive critical line r_c(α) analytically (corroborates numerics).

---

## Key results

### 1. Universal r-dependence, shifted critical point

For every risk measure, ⟨q₀⟩ diverges as r → r_c^− with power law:

```
⟨q₀⟩ ∼ (r_c − r)^(−γ)   with γ ≈ 1/2 for variance/MAD side, different for ES
Weight fluctuations ⟨‖δw‖²⟩ ∼ (r_c − r)^(−δ)
```

| Risk measure | Critical r_c (thermodynamic limit) | Feasibility |
|---|---|---|
| **Variance** | r_c = 1 | always feasible for r<1 (matrix invertible); diverges at r→1 due to Wishart smallest eigenvalue →0 |
| **MAD** | r_c ≈ 0.5 | always feasible for r<0.5; hedging argument maps MAD to a variant of variance |
| **ES α=0.99** | r_c ≈ 0.3–0.5 depending on α | **probabilistic feasibility**: p<1 even for small r; phase boundary r_c(α) decreases as α→1 (more tail-sensitive → more fragile) |
| **ES α=0.97** | r_c ≈ 0.5 | p≈0.5 near r_c |
| **ML (ES α→1)** | r_c ≈ 0.5 (lower) | p drops very fast; even at r=0.1, finite probability of infeasibility for large N |
| **MaxL / finite α** | r_c(α) ↑ as α ↓ | At α=0.9, r_c≈0.8 — less fragile |

*Fig. 2–5:* ⟨q₀⟩ vs r collapse for different N; sharp rise near r_c, sample-to-sample variance of q₀ also diverges (critical fluctuations). *Fig. 6:* p(r) (feasibility probability) drops from ~1 to ~0 in a narrow interval around r_c for ES/ML — looks like a first-order-like transition.

### 2. Probabilistic feasibility (ES/ML) — the headline discovery

- **Mechanism:** For ES/ML, optimization is `min_w max` or `min_w avg_tail_loss`. For a given sample, there may exist a weight vector w_dom that **dominates** all others: its loss is *above* the α-quantile in *every* time step (for ML: `−w_domᵀx_t > 0 ∀t`; for ES: average tail loss can be driven to −∞ by scaling w_dom → ∞). This is an **in-sample arbitrage** — a spurious "good deal" created by the finite sample that doesn't exist in the true distribution. LP correctly says "go infinitely long that portfolio" → problem unbounded → no finite optimizer.
- **Probability of arbitrage:** >0 for any r>0, increases with r and N at fixed r, decreases with α lowered. For N=100, T=500 (r=0.2), p_infeas ≈ a few percent for ES_0.99 — small but non-zero, and grows quickly with N.
- **Variance never has this:** Quadratic penalty prevents any direction from appearing free; worst case is just large finite weights, not infinite.
- **Analytic phase boundary (replica):** r_c(α) solves an implicit equation involving α and the tail of the normal distribution (eq. ~35–40 in paper). For Gaussian, r_c(0.99)≈0.3, r_c(0.975)≈0.5, r_c(0.9)≈0.9. Confirmed numerically.

### 3. Sample-to-sample fluctuations diverge

Not just the mean q₀ blows up; its *variance across samples* and weight dispersion also diverge at r_c. Near the transition, two samples of the same true distribution give wildly different "optimal" portfolios. Reported variance of q₀ grows by an order of magnitude between r=0.2 and r=0.4 for ES_0.99.

### 4. Implications for filtering & risk-measure choice

- **Variance filtering (RMT, shrinkage) is mature** and roughly halves q₀; analogous filters for MAD/ES/ML are essentially nonexistent at time of writing.
- **Regularization helps:** Adding ℓ₂ (‖w‖²) or ℓ₁ (gross exposure) constraints rounds off the divergence and restores feasibility, at the cost of bias. This anticipates later ℓ₁/ℓ₂-regularized ES literature (Caccioli et al. 2014–2016 follow-ups).
- **Message:** *Robustness (noise tolerance) should be an explicit criterion for selecting a risk measure*, alongside coherence, elicibility, etc. A theoretically beautiful measure that requires 10× more data to estimate is practically inferior.

---

## Trading-system implications (most actionable paper in series)

### Hard constraints for an autonomous trader

| Rule | Value | Source |
|---|---|---|
| **Variance sleeve max r** | N/T ≤ 0.2 (safe), ≤ 0.4 (tolerable), never >0.6 without filtering | Papers 2 + 6 |
| **MAD sleeve max r** | N/T ≤ 0.3 | Paper 6 |
| **ES 99% sleeve max r** | N/T ≤ 0.1–0.15 (safe) | Paper 6 replica boundary |
| **ES 97% sleeve max r** | N/T ≤ 0.25 | Paper 6 |
| **ML / worst-case** | Do not optimize ML directly — use ES or variance as proxy | Paper 6 |

If your LLM trader proposes a portfolio optimization with ES_99 and N=200, T=500 (r=0.4): **refuse or force regularization** — paper says infeasible with >50% probability.

### Code-level guards

- **Pre-optimization feasibility check:** For ES/MAD/ML LP, detect unboundedness *before* allocating: check for in-sample dominating portfolio via auxiliary LP. If detected, fall back to filtered variance or regularized ES-ℓ₂.
- **ℓ₁ / ℓ₂ regularization as first-class feature:** Exposure cap ‖w‖₁ ≤ c and weight-norm penalty λ‖w‖₂² are not just "risk limits" — they are *phase-transition regularizers* that move you away from r_c. Log effective r and regularization strength together.
- **Honest ES estimation:** Never report in-sample ES as true risk. Use out-of-sample or cross-validated ES; apply the paper's q₀ correction or a bootstrap.
- **Allocator choice:** If you must optimize tail risk (e.g. for drawdown control), prefer MAD over ES for small-T regimes, or ES at lower α (0.90–0.95) rather than 0.99 — less coherent but far more estimable.

### Monitoring

- Track `r = N_effective / T_lookback` as a live risk metric (effective N after factoring factor-model dimension reduction).
- Track `p_feas` proxy: among rolling windows, how often does ES optimization hit unboundedness or produce extreme ‖w‖ (>10× budget). Rising trend ⇒ halving N or doubling T or tightening regularization.

---

## Limitations

- Identity covariance (iid N(0,I)) is the cleanest ensemble but not the most realistic — true correlations make effective N smaller (good), but heavy tails make estimation harder (bad). Paper checks market model briefly and finds qualitatively same transition at slightly shifted r_c.
- Gaussian tails — real financial tails are fatter, which will worsen ES/ML feasibility (more extreme samples → more spurious arbitrage opportunities). Paper's Gaussian r_c is therefore *optimistic* for real data.
- No filtering benchmark for ES/ML — open problem at time; follow-up papers (Papp et al. 2016–2018, Author-K et al. 2014–2017) develop ℓ₁/ℓ₂-regularized analytic solutions.

---

## Connection to follow-on literature (not in this folder but referenced)

The phase-transition picture directly spawned:

- Ciliberti–Author-K–Mézard (2007) *On the feasibility of portfolio optimization under ES* — sharper replica solution.
- Caccioli–Author-K–Papp (2015) *Contour maps of ES estimation error* — visualizes q₀ in (r, α) plane.
- Papp–Caccioli–Author-K (2016) *Bias-variance tradeoff under ES with ℓ₂ regularization* — how much λ to add.

If you want to push beyond variance, read those next.

---

## Bottom line

> **Papers 1–5: noise makes optimization worse. Paper 6: under tail-sensitive risk measures, noise can make optimization *impossible*.** The phase-transition framework turns "estimation error" from a quantitative nuisance into a qualitative feasibility guardrail — one that any autonomous agent must respect.
