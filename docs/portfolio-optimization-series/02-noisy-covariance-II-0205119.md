# Paper 2 — Noisy Covariance Matrices and Portfolio Optimization II (cond-mat/0205119)

> **Bibliography:** Author-P, S. & Author-K, I. (2002). *Noisy Covariance Matrices and Portfolio Optimization II.* arXiv:cond-mat/0205119 v2 (20 May 2002). Published as Physica A 319, 487–494 (2003). DOI: 10.1016/S0378-4371(02)01499-1.  
> **PDF:** `0205119.pdf` (9 pp.)  
> **Role in series:** Paper I's qualitative finding → quantitative law. Introduces the control parameter **r = n/T** as the single determinant of noise damage.

---

## Abstract (essence)

The paradox: RMT says covariance matrices are 94% noise, yet finance uses them daily. Resolution: the damage depends *strongly* on r = n/T. For large r (≈0.6) noise is devastating (as feared); for small r (≤0.2) noise drops to acceptable levels. Since T is capped (≈500–1500 daily obs) in practice, bounding noise ⇒ bounding portfolio size n. A second, equally important finding: using the covariance matrix to *measure* risk of a fixed portfolio is far less noise-sensitive than using it to *optimize*.

---

## Core thesis

> **r = n/T is the master variable.** Asymptotic noise-induced error depends only on r, not on n or T separately. Every noise tolerance translates directly into a maximum allowable portfolio size for a given history length.

---

## Method

- **Toy models:** (a) Identity (all assets independent), (b) one-factor market model: C_ij = ρ₀ + (ρ−ρ₀)δ_{sector(i),sector(j)} — analytically tractable, eigenvalues known in closed form. These give clean benchmarks; bulk eigenvalues cluster near 1, one large market eigenvalue ≈ 1+(n−1)ρ₀, sector eigenvalues ≈ 1+ (sector_size−1)(ρ−ρ₀).
- **Semi-analytic calculation:** For the toy models, the expected out-of-sample risk inflation q₀ = ⟨R²(ŵ)⟩ / R²(w⁰) can be derived analytically as a function of r. For identity model: q₀ = 1/√(1−r) for 0<r<1 (exact in thermodynamic limit n,T→∞ with r fixed). More generally q₀ depends on true spectrum but functional form is similar.
- **Monte Carlo:** Generate Gaussian returns from true C, estimate Ĉ from T samples, solve min-variance portfolio ŵ(Ĉ), evaluate true risk R²(ŵ; C). Average over 1000+ samples. Sweep n=10..400, T=50..3000 to verify collapse onto r.
- **Risk-measurement experiment:** Fix ŵ (e.g. equally weighted or previously optimized), measure its risk using Ĉ vs true C — compare error to optimization case.

---

## Key results

### 1. The r-law

For the identity / market model:

```
q₀(r) ≈ 1 / sqrt(1 − r)     (lowest-order analytic; diverges at r→1)
```

More accurate numerically: at r=0.2 → q₀≈1.12 (12% excess risk); r=0.4 → q₀≈1.29; r=0.6 → q₀≈1.58. The empirical collapse is striking: points for different (n,T) pairs with same r fall on one curve (Fig. 1–2).

**Operational translation:**

| History T (daily) | Max n for r≤0.2 (q≈12%) | Max n for r≤0.4 (q≈29%) |
|---|---|---|
| 250 (1 yr) | 50 | 100 |
| 500 (2 yr) | 100 | 200 |
| 1000 (4 yr) | 200 | 400 |
| 2000 (8 yr) | 400 | 800 |

> Since 8-year stationarity is implausible, **n=50–100 is the practical ceiling for unfiltered Markowitz with daily data** if you demand <15% noise penalty.

### 2. Risk measurement vs optimization

- **Optimization:** q₀(r) grows as above; inversion amplifies noise via small eigenvalues.
- **Measurement:** Fix w, estimate risk σ̂² = wᵀĈw. Then relative error ≈ √(2/T) · √(Σ_i (something)) — scales as 1/√T, *independent of n* to leading order, and is typically <5% even for r=0.6. **You can trust a covariance matrix to report risk of a given book, but not to choose the book.**

### 3. Filtering preview

Briefly revisits Laloux et al. / Bouchaud filtering: truncating eigenvalues below RMT edge reduces q₀ substantially at high r, but gains are modest at low r where noise is already small. Sets up dedicated benchmark in Paper 3.

---

## Trading-system implications

- **Hard r-budget:** Add a config parameter `max_r = N_eff / T_lookback` and enforce `N_eff ≤ 0.2 · T` for any covariance-optimized sleeve. If universe is larger, do *dimensionality reduction first* (factor model, clustering, RMT filter) — don't just widen T by using stale data.
- **Separate measurement from allocation:** Risk reporting (VaR, expected shortfall of current book) can use raw sample covariance with modest error. Allocation decisions (re-optimization) must use filtered/shrunk covariance or robust optimization. Don't conflate the two uses.
- **T is not free:** Extending lookback to increase T adds non-stationarity bias. The paper's r-law assumes i.i.d. — in reality, longer T violates stationarity, so effective r is *worse* than nominal. Prefer 1–2 year windows and accept smaller n.
- **Intraday vs daily tradeoff:** Moving to 1-hour bars multiplies T but introduces microstructure noise and intraday seasonality; r-law still applies but σ(1h) ≠ σ(1d) scaling.

---

## Limitations

- Gaussian, i.i.d., stationary — ignores heteroskedasticity, fat tails, regime changes (partially addressed in `04`).
- Toy correlation models are stylized; empirical portfolios have heterogeneous volatilities and more complex sector structure.
- No short-sale constraints in this paper (those make q worse; see `06`).

---

## Connection onward

Paper 2 tells you *when* noise hurts (high r). Papers 3–5 tell you *what to do about it* (benchmark estimators and build the best one).
