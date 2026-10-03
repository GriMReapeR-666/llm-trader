# Paper 1 — Noisy Covariance Matrices and Portfolio Optimization (cond-mat/0111503)

> **Bibliography:** Author-P, S. & Author-K, I. (2001). *Noisy Covariance Matrices and Portfolio Optimization.* arXiv:cond-mat/0111503. Published as Eur. Phys. J. B 27, 277–280 (2002). DOI: 10.1140/epjb/e20020153.  
> **PDF:** `0111503.pdf` (7 pp., 3 figs.)  
> **Role in series:** Foundational paper I. Directly confronts Laloux et al. (1999) and Plerou et al. (1999) — 94% of spectrum is random — and asks: does Markowitz optimization survive?

---

## Abstract (essence)

If 94% of eigenvalues are noise, is covariance-based portfolio theory dead? Simulate a *known* correlation matrix whose small eigenvalues are dressed with noise by sampling T observations and re-estimating the covariance. Compare true vs estimated efficient portfolios. Result: under **linear constraints** (budget + target return), excess risk due to noise is only 5–15% — second-order effect. Under **nonlinear constraints** (margin accounts, regulatory capital adequacy which impose leverage caps / piecewise-linear constraints), noise causes instability and exponential degeneracy — many wildly different portfolios with nearly identical risk.

---

## Core thesis

> **RMT noise is real, but its damage depends on the optimization geometry.** Linear constraints project onto the stable large-eigenvalue subspace; nonlinear constraints probe the noisy small-eigenvalue subspace and break down.

---

## Method (reconstructable)

1. **True model:** N=100–500, T=500–2000. True correlation C^0 is constructed to mimic empirical findings: one large market eigenvalue, few sector eigenvalues, bulk = identity (or small constants). Specifically: block-diagonal-ish market-plus-sectors structure.
2. **Noise dressing:** Generate X ~ N(0, C^0) of size N×T, compute sample covariance Ĉ = (1/T) XXᵀ.
3. **Optimization A (linear):** minimize wᵀĈw s.t. Σw_i=1, Σw_i μ_i = target (or just min-variance with budget). Solve via Lagrange multipliers → w* ∝ Ĉ⁻¹1.
4. **Optimization B (nonlinear):** add realistic constraints like Σ|w_i| ≤ leverage cap, or margin requirement max_i w_i ≤ c, or regulatory constraint Σ (w_i)₊ ≤ ... (nonlinear / non-convex feasible set).
5. **Metrics:** 
   - q = R²(ŵ; C^0) / R²(w^0; C^0)  where R² is true risk — measures how much worse the estimated portfolio *really* is.
   - Angle / L2 distance between ŵ and w^0.
   - Distribution of near-optimal portfolios (degeneracy count).

---

## Key results

| Result | Quantitative statement |
|---|---|
| **Linear case: risk inflation is modest** | q − 1 ≈ 0.05–0.15 depending on N, T. E.g. N=100, T=500 → ~15%; N=400, T=600 → ~15–20%. Despite ŵ being far from w^0 in weight space (large dispersion), the *value* at optimum is flat — second-order effect (risk is quadratic, minimum is broad). |
| **Weights are unstable** | ‖ŵ − w^0‖ can be Large (≈ comparable to ‖w^0‖) even when q is small. Diversification appears "random." Do not interpret individual weights as signal. |
| **Nonlinear constraints: phase change** | Under margin/regulatory constraints, q diverges and the feasible set fragments. Many (exponential in N) portfolios achieve essentially the same minimal risk but with completely different compositions. Problem becomes *ill-posed as a decision tool*. |
| **Large eigenvalues dominate** | Linear solution is controlled by large stable eigenvalues; noise bulk contributes only at O(r) where r=N/T. This explains robustness. Nonlinear constraints couple to small eigenvalues → noise dominates. |

*Figure reconstruction (Fig. 1):* q vs T for fixed N=100 shows monotonic decay ~ 1/√T; Fig. 2–3: histograms of weight deviations and degeneracy under nonlinear constraints — broad / multimodal.

---

## Critical insight for practitioners

The paper is **optimistic for classic Markowitz but pessimistic for real-world constraints.** Since any live trading system has leverage caps, no-short or limited-short constraints, position limits, and regulatory capital rules — all nonlinear — the optimistic 5–15% number is a *lower bound*. The paper is a warning that the textbook problem understates practical fragility.

---

## Trading-system implications (for this codebase)

- **Budget-only backtests overstate robustness.** If you test min-variance with only Σw=1, you'll measure q≈1.05–1.15 and think noise is manageable. Add realistic constraints (max leverage 2×, max single-name 10%, no-short where applicable) and re-test — expect much larger q and weight instability.
- **Weight-level attribution is noisy.** Do not over-interpret day-to-day changes in optimized weights; the optimizer is amplifying noise in small-eigenvalue directions. Consider shrinkage or weight smoothing.
- **Small-N heuristic:** The paper's "small" q requires T ≫ N. With N=100 and T=500 (2 years daily), r=0.2 already gives 15% penalty. If you run N=200 on 1 year (T≈250), r=0.8 → unusable. Enforce a max r budget (see Paper 2).
- **Nonlinear constraints need special handling:** Either filter the matrix first (Papers 3–5) or reformulate constraints to be noise-aware.

---

## Limitations

- Gaussian returns — fat tails and autocorrelation ignored (addressed later via EWMA in `04`).
- Single toy correlation model; no empirical portfolio backtest.
- No filtering yet — that is the subject of the rest of the series. This paper only *diagnoses*.

---

## Connection to next paper

Paper 2 (`0205119`) sharpens the diagnosis: the vague "small r is OK" becomes a precise quantitative law q(r) with a critical r_c where the problem breaks down entirely.
