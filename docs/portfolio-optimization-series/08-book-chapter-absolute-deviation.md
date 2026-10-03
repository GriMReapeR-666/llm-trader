# Supplement — Portfolio Selection in a Noisy Environment Using Absolute Deviation (Springer 2006)

> **Bibliography:** Author-K, I., Author-P, S., Karádi, R. & Nagy, G. (2006). *Portfolio Selection in a Noisy Environment Using Absolute Deviation as a Risk Measure.* In H. Takayasu (ed.), *Practical Fruits of Econophysics*, pp. 220–225. Springer. (Preceded by physics/0611027 analysis; this chapter isolates MAD.)  
> **Preprint note:** An earlier working version *Estimation Noise in Portfolio Optimization with Absolute Deviation* is closely related; the Springer chapter is the archival version.  
> **Role in series:** Companion to the JBF paper `06` — zooms in on MAD as the LP-solvable risk measure between variance and tail coherent measures.

---

## Core content

- **MAD definition:** MAD(w) = (1/T) Σ_t | Σ_i w_i (x_it − μ_i) |. Minimizing MAD under Σw=1 is a **linear program** (fix with auxiliary variables u_t ≥ ± residual), far cheaper than QP for large N.
- **Motivation for MAD:** Historically proposed by Konno & Yamazaki (1991) as computationally efficient alternative to variance; also less outlier-sensitive than variance (L1 vs L2).
- **Noise sensitivity of MAD:** Paper shows (both simulation and analytic scaling argument):
  - MAD also exhibits an r-dependent phase transition, but with **r_c(MAD) ≈ 0.5** (between variance r_c=1 and ES_99 r_c≈0.3).
  - **Below r_c, MAD optimization is always feasible** (unlike ES/ML, no probabilistic arbitrage) — because absolute deviation still grows linearly with ‖w‖ in any direction, preventing in-sample arbitrage.
  - Weight fluctuations and q₀ diverge with similar power law as variance; sample-to-sample dispersion of w is large near r_c.
  - At small r (≈0.1–0.2), MAD and variance give similar q₀; MAD's LP advantage is then "free."
- **Why MAD matters for this codebase:** If you need an LP-solvable, tail-light alternative to variance that scales to N=1000+ and is more noise-robust than ES, MAD is the candidate Paper 6 leaves standing. The chapter quantifies exactly how much more robust than ES but less than variance it is.

---

## Trading-system implication

- **Large-N sleeve where QP solver struggles:** Consider MAD as drop-in LP replacement for variance when N>500 and you need fast re-optimization (intraday). Keep r ≤ 0.25 to stay well below MAD's r_c.
- **Not a tail-risk measure:** MAD, like variance, is not coherent in the tail — it underweights extreme events. Don't use MAD if your objective is explicitly drawdown / tail protection; use regularized ES at low r instead.

---

## Link to Paper 6

Read this chapter as **Paper 6, Section on MAD, expanded**. The phase-transition picture, r-collapse, and divergence of weight fluctuations are identical; only the critical value shifts. The chapter's figures mirror `06:Fig. 2–3` but for MAD's LP.
