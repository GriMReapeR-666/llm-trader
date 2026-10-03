# Author-P–Author-K Portfolio Optimization Series — Complete Analysis

**Authors:** Szilárd Author-P & Imre Author-K (with Gabor Papp, Maciej A. Nowak, Gabor Nagy, Marc Potters)  
**Series span:** 2001–2007 (7 core papers, 1 book chapter) — the foundational econophysics critique of Markowitz optimization under estimation noise.  
**Folder:** `docs/author-p-author-k-portfolio-optimization-series/`  
**PDFs stored alongside:** `0103107.pdf` ... `0611027.pdf` (original arXiv versions)

---

## Why this series matters for an LLM trader

Every paper in this series hammers the same load-bearing fact: **when you estimate an N×N covariance/correlation matrix from T observations, the number of parameters is O(N²) but the number of data points is O(NT).** Unless T ≫ N, the matrix is dominated by measurement noise. Since any trading system that does mean-variance optimization, risk parity, or covariance-weighted allocation must invert that matrix, noise gets amplified through small eigenvalues. The series quantifies *exactly* when optimization breaks, when it merely degrades, and what filtering actually helps.

If you run an autonomous portfolio agent with N=50–500 instruments and T=250–1000 days, you live permanently in the regime this series describes.

---

## Series map (chronological)

| # | File in this folder | arXiv | Published | One-line thesis |
|---|---|---|---|---|
| 0 | `00-riskmetrics-0103107.md` | `cond-mat/0103107` | Physica A 299 (2001) | RiskMetrics' apparent success is an artifact of 1-day VaR horizon + normal tail assumption |
| 1 | `01-noisy-covariance-I-0111503.md` | `cond-mat/0111503` | Eur. Phys. J. B (2002) | With 94% random spectrum, linear-constrained variance optimization degrades only 5–15%, but nonlinear constraints cause degeneracy |
| 2 | `02-noisy-covariance-II-0205119.md` | `cond-mat/0205119` | Physica A 319 (2003) | Effect of noise depends *only* on r=N/T; r≈0.6 catastrophic, r≤0.2 acceptable. Risk measurement noise << optimization noise |
| 3 | `03-estimated-correlation-0305475.md` | `cond-mat/0305475` | Physica A 343 (2004) | Simulation-based benchmarking framework for covariance estimators; RMT filtering wins over single-index, shrinkage on toy models |
| 4 | `04-exponential-weighting-RMT-0402573.md` | `cond-mat/0402573` | SFI WP (2004) | Analytic spectrum of EWMA random matrices + hybrid EW+RMT estimator beats either alone |
| 5 | `05-random-matrix-filtering-0509235.md` | `physics/0509235` | Acta Phys. Pol. B 36 (2005) | Controlled demonstration that RMT eigenvalue clipping restores efficient frontier |
| 6 | `06-noise-sensitivity-various-risk-measures-0611027.md` | `physics/0611027` | J. Banking & Finance 31 (2007) | Universal phase transition at critical r*(risk measure); ES & MaxLoss have *probabilistic feasibility* failure even at small r |
| 7 | `07-synthesis-and-trading-implications.md` | — | — | Cross-paper synthesis + actionable rules for the current trading system |
| — | `08-book-chapter-absolute-deviation.md` | — | Springer 2006 ch. | Absolute deviation as LP-solvable alternative; same r-dependence but always feasible below critical r |

---

## The intellectual arc

```
[0] RiskMetrics critique
        ↓ "why does a flawed model look good?"
[1] Noise exists (94% random) — but linear Markowitz is surprisingly robust
        ↓ "under what conditions?"
[2] r = N/T is the control parameter. Bound r → bound portfolio size.
        ↓ "how to compare fixes?"
[3] Build toy correlation models → benchmark estimators fairly
        ↓ "combine best ideas"
[4] EWMA (heteroskedasticity) × RMT (noise) → joint estimator + analytic RMT band edge for EW matrices
        ↓ "does filtering actually work in simulation?"
[5] Yes — demonstrate on market+sectors model
        ↓ "what about non-variance risk measures?"
[6] All risk measures diverge at critical r; coherent measures (ES, MaxLoss) can become *infeasible*
        ↓ "what does this mean for practice?"
[7] Synthesis: filtering requirements differ by risk measure; robustness must be a first-class risk-measure criterion
```

---

## Key quantitative results to carry forward

- **r = N/T is the only thing that matters asymptotically** (`02`, `06`). Not N or T alone.
- **Variance optimization:** relative error `q0 ~ 1/sqrt(1-r)` diverges at r→1. Practically: r=0.6 → large error; r=0.2 → tolerable (~10% excess risk).
- **Risk measurement vs optimization:** measuring risk of a *fixed* portfolio is far less noise-sensitive than *optimizing* (`02`).
- **Nonlinear constraints** (margin, regulatory) make the problem degenerate — exponentially many near-optimal portfolios (`01`).
- **RMT filtering:** keep only eigenvalues above upper RMT edge λ₊ = (1+√r)² (and adjusted edge for EW matrices in `04`). Replace noisy bulk with average or zero.
- **ES / MaxLoss:** phase transition at r_c(α) < 1 depends on confidence level α; below r_c the problem may already be infeasible with finite probability (`06`). This is an *algorithmic* phase transition in the replica sense.
- **No free lunch:** imposing structure (single-index, shrinkage) reduces variance but adds bias — tradeoff must be evaluated out-of-sample via simulation framework of `03`.

---

## How to read this folder

1. Start here (`README.md`) for the big picture.
2. Read `01`→`02`→`06` for the core noise-sensitivity theory.
3. Read `03`→`04`→`05` for estimation/filtering solutions.
4. Read `07` for what it means for *this* codebase.

Each `0N-*.md` file follows a standard template: Bibliography | Abstract essence | Core thesis | Method | Key results (with figures/tables reconstructed) | Trading implications | Limitations.

---

## Citation

Author-P, S. & Author-K, I. Noisy Covariance Matrices and Portfolio Optimization (I & II), Estimated Correlation Matrices..., Exponential Weighting and RMT Filtering..., Random Matrix Filtering..., Noise Sensitivity under Various Risk Measures (2001–2007). arXiv:cond-mat/0103107, 0111503, 0205119, 0305475, 0402573; physics/0509235, 0611027.
