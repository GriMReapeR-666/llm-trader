# Paper 5 — Random Matrix Filtering in Portfolio Optimization (physics/0509235)

> **Bibliography:** Papp, G., Author-P, S., Nowak, M.A. & Author-K, I. (2005). *Random Matrix Filtering in Portfolio Optimization.* arXiv:physics/0509235. Published as Acta Phys. Pol. B 36, 2757 (2005).  
> **PDF:** `0509235.pdf` (9 pp., 3 figs.) — conference proceedings (Kraków, May 2005).  
> **Role in series:** Cleanest controlled demonstration of filtering. Complements Paper 4's empirical test with a *simulation where the efficient frontier improvement is unambiguous*.

---

## Abstract (essence)

Study empirical covariance matrices with finite-sample noise. Test a recently introduced RMT filtering procedure in a controlled simulation where the true covariance is known (market-plus-sectors model). Show that filtering restores proximity to the true efficient frontier.

---

## Core thesis

> **In simulation where you can compute the *true* optimal portfolio, filtered portfolios are measurably closer to truth — on both risk and weight error — than unfiltered.**

---

## Method (reconstructable, market-plus-sectors model)

1. **True correlation C_true (N×N):** Market-plus-sectors block model (same as Noh 2000, used in Papers 2–3):
   - Off-diagonal outside blocks: ρ₀ ≥ 0 (market mode).
   - Inside each of K sector blocks: ρ_k ≥ ρ₀ (sector mode), constant within block.
   - Diagonal = 1 (correlation matrix).
   - Example tested: N=100–406, K=5–10 sectors, ρ₀≈0.2–0.3, ρ_k≈0.4–0.6.
   - Known spectrum: 1 large market eigenvalue, K sector eigenvalues, bulk near 1.

2. **Estimators:**
   - **True** (oracle, uses C_true) → w⁰, efficient frontier F_true.
   - **Historical** → Ĉ_hist = (1/T) XXᵀ from simulated Gaussian returns, T such that r=N/T varied 0.2–0.7.
   - **Filtered** → eigen-decompose Ĉ_hist, keep λ_i > λ₊=(1+√r)², replace bulk eigenvalues by their mean (trace-preserving reconstruction) → Ĉ_filt.

3. **Optimization:** For each target expected return μ_p, solve min-variance with Σw=1 and wᵀμ=μ_p → efficient frontier. Compare F_true vs F_hist vs F_filt.

4. **Metrics:** (a) Frontier distance (vertical gap in σ at given μ_p), (b) weight overlap q = (w⁰·ŵ)/(‖w⁰‖‖ŵ‖), (c) realized risk ratio q₀ as in earlier papers.

---

## Key results

### Frontier restoration (Fig. 2–3)

- **Historical frontier** is *over-optimistic at low risk and realized risk is higher than frontier suggests*: in-sample predicted σ underestimates true σ by factor ~q₁ (≈0.6–0.8 at r=0.5), while out-of-sample realized σ is inflated by q₀ (≈1.3–1.6). The historical frontier appears *to the left* of the true frontier (apparently better) but its out-of-sample realization lies *to the right* (worse).
- **Filtered frontier** lies between the two and tracks F_true much more closely: both predicted and realized σ converge toward truth, gap roughly halved (e.g. at r≈0.5, q₀: 1.4 → 1.15 after filtering; frontier visual gap reduced by ~50%).
- At low r=0.2, filtering gain is smaller but still positive; at high r=0.6, filtering is essential — without it the frontier is economically meaningless.

### Weight fidelity

- Cosine similarity between w⁰ and ŵ_filt > ŵ_hist by ~0.15–0.25 depending on μ_p. Filtered weights are less dispersed (lower ‖w‖²) → implicitly more diversified.

### Bulk-replacement choice

- Replacing bulk eigenvalues by their *average* outperforms zeroing them (which would understate total variance) and matches trace-preserving theory from Paper 3. The paper validates this choice again.

---

## Trading-system implications

- **Frontier backtests lie:** If you plot an efficient frontier from sample covariance without filtering, you see a better-than-truth frontier that will not survive out-of-sample. Always plot *two* frontiers: in-sample predicted vs out-of-sample realized, and require them to be close. Large gap = noise, not alpha.
- **Use as a smoke test:** Before live deployment, run the market-plus-sectors simulation at your live N and T (e.g. N=100, T=500 → r=0.2) and confirm that your pipeline's filtering reduces q₀ and shrinks the frontier gap. If it doesn't, your filter threshold is miscalibrated (likely λ₊ wrong for your effective r, see Paper 4).
- **Diversification pressure:** Filtering naturally reduces extreme weights — equivalent to an implicit ℓ₂ regularization on w. If you also impose explicit weight caps, the two regularizations stack; be aware of double-shrinkage.

---

## Limitations

- Gaussian, stationary, known block structure — real markets have time-varying correlations and eigenvalue dynamics not captured.
- Only variance objective; non-variance risk measures (Paper 6) need different treatment.
- Short proceedings paper — fewer robustness checks than Papers 2–4; no empirical data test (that was Paper 4's job).

---

## Connection onward

This paper closes the variance-optimization arc (Papers 1–5). Paper 6 (`0611027`) asks: *what if the objective isn't variance?* — and finds the answer is qualitatively worse.
