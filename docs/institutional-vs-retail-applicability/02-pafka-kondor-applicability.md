# 02 — Author-P–Author-K Series: Applicability to This Project

> 7 papers, 2001–2007, `docs/author-p-author-k-portfolio-optimization-series/`. Verdict: **Institutional-only today, P0-critical tomorrow.**

---

## 1. What the series actually studies

All 7 papers concern the **estimation error of an N×N covariance/correlation matrix from T observations**, with control parameter **r = N/T**. Core results:

- Noise divergence `q₀ ~ 1/√(1−r)`, bulk edge `λ₊ = (1+√r)²` (`02-noisy-covariance-II-0205119.md`).
- Phase transition at critical `r_c`: variance `r_c=1`, MAD `≈0.5`, ES_99 `≈0.3` (`06-noise-sensitivity-various-risk-measures-0611027.md`).
- RMT eigenvalue clipping restores frontier (`05-random-matrix-filtering-0509235.md`); EW+RMT hybrid handles heteroskedasticity (`04-exponential-weighting-RMT-0402573.md`).

*Prerequisite for any of this math to matter: N must be large enough to have a spectrum.*

---

## 2. Institutional vs Retail classification

| Criterion | Series assumption | This project's reality | Grade |
|---|---|---|---|
| **N** | 100–400 (simulated N=100, T=500 → r=0.2) | N=1 (one FX symbol), max 2 positions (`core/risk_manager.py:510`) | Institutional |
| **T** | 500–1500 daily obs, cross-section | 60–250 bars of M15/H1 for single symbol (`core/alpha_ensemble.py:205` IC window 60) | Retail |
| **Optimization** | `min wᵀCw` s.t. Σw=1 → requires C⁻¹ (`01-noisy-covariance-I-0111503.md:§3`) | Ensemble z-score (`core/alpha_ensemble.py:438`) + 1R stop, no inversion | Retail |
| **Risk measure** | Portfolio variance / ES_99 LP | Per-trade 1R + daily 3% + heat cap 4R (`core/risk_manager.py:65`, `238`, `448`) | Retail |
| **Data** | Gaussian iid cross-sectional returns | Non-stationary FX time-series, fat tails | Hybrid (series is optimistic) |
| **Filtering target** | Full N×N matrix spectrum | Nothing to filter with N=1 (single variance) | Not applicable |

**Conclusion:** Direct application today = over-engineering. Implementing Marčenko-Pastur clipping on a 1×1 matrix is a no-op.

---

## 3. What *is* retail-relevant (extracted lessons, not code)

| Lesson | Transfer to retail | Where to enforce |
|---|---|---|
| **r = N/T is the only control parameter** (`02:§2`) | Even with N=1, the concept survives as **lookback vs estimation error**: using T=60 bars to estimate IC/alphas is noisy; paper says <100 obs is unstable regardless of N. | `core/alpha_ensemble.py:223` `ic_window=60` → enforce `T_eff ≥ 100` and log warning if below. |
| **Measurement vs optimization sensitivity** (`02:§4`) | Measuring risk of a *fixed* position (w fixed) is far less noisy than *choosing* the position. Applies retail: reporting `wᵀĈw` on a proposed trade is safer than optimizing 23 alphas weights via IC inversion. | Separate `measure_risk()` from `allocate_weights()`; don't conflate. |
| **Nonlinear constraints cause degeneracy** (`01:§4`) | Retail analogue: leverage caps, `volume_min/step`, and discrete lot sizing create similar degenerate feasible sets — many weight vectors hit same risk, optimizer output becomes arbitrary. | Bootstrap resample alphas 100×; if weight dispersion is high, freeze ensemble weights (`equal`) instead of IC-weighting. |
| **Predicted frontier is over-optimistic** (`05:§3`, `03:§4`) | Retail analogue: in-sample ensemble IC (`ic_mean`) overstates live IC; `q₁ < 1` optimism → realized Sharpe < backtest. | Mandate out-of-sample CPCV (`core/cpcv_validation.py`) for every alpha weight change; report predicted vs OOS gap. |
| **Heavy tails make Gaussian r_c optimistic** (`06:§6`) | FX tails > Gaussian, so real `r_c` is smaller — even stricter lookback discipline needed. | Don't extend T by adding stale regimes to "fix" r; prefer robust IC (Spearman) already in `core/alpha_ensemble.py:224`. |

---

## 4. The four tripwires that make the series P0

The day any of these is approved in `TRADING_SYSTEM_ROADMAP.md` or `strategies/README.md`, Author-P–Author-K moves from *reference shelf* to *build queue*:

| # | Tripwire | Evidence in codebase | What to build when tripped |
|---|---|---|---|
| 1 | **Multi-symbol portfolio sleeve** (`N≥10`) | `core/hierarchical_portfolio.py:148` already has a `DynamicStrategyRouter` that blends 4 strategy weights — currently single-symbol, but structure invites N>1. | RMT clipping `λ₊=(1+√r)²`, shrinkage, r-budget `N_eff ≤0.2·T` (`07-synthesis-and-trading-implications.md:§2`). |
| 2 | **Cross-asset hedging (CTA_TREND, CASH_HEDGE)** | Same file: `CTA_TREND` is a multi-asset placeholder (`hierarchical_portfolio.py:155`). Activating it creates correlation dependence. | Same + EW+RMT hybrid (`04:§3`) if using EWMA vols. |
| 3 | **ES / CVaR optimization at the portfolio level** | `core/copula_tail_risk.py` already computes tail dependence — adding ES optimization is a small step. | Feasibility check + ℓ₁/ℓ₂ regularization (`06:§7`), refuse ES_99 if `r>0.1`. |
| 4 | **Universe scanning (N≥50 FX/crypto pairs)** | Not today, but `data/market_data_provider.py` + `mt5_trader.py trade --symbol` is one loop away from scan. | Strict r-guard: refuse optimization without filtering when `r>0.2`. |

**Pre-emptive action (cost ≈ 1 hour):** Add a `max_r` guard *now* even with N=1, so the tripwire is loud rather than silent. Pseudocode in `docs/author-p-author-k-portfolio-optimization-series/07-synthesis-and-trading-implications.md:§7`.

---

## 5. Bottom line

> **Do not implement RMT filtering today. Do implement the r-budget mindset today.** The institutional math is not needed at N=1, but the *discipline* (bound lookback vs estimator complexity, separate measurement from optimization, never trust predicted frontier) is retail-critical and costs nothing.

**Reference:** Full 7-paper analysis with retail implications per paper is in `docs/author-p-author-k-portfolio-optimization-series/0*.md` and synthesis `07-synthesis-and-trading-implications.md`.
