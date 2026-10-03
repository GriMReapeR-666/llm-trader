# Paper Summary — The Dispersion Bias (Goldberg et al. 2017-2018)

## Bibliography
* **Title:** The Dispersion Bias
* **Authors:** Lisa Goldberg (Berkeley Econ/Stat, Aperio), Alex Papanicolaou (NYU), Alex Shkolnik
* **Versions:** arXiv:1711.05360v1 Nov 2017 → v4 Feb 2018. Final: *SIAM Journal on Financial Mathematics*.
* **ESCHOLARSHIP mirror:** https://escholarship.org/content/qt4kt5g2x3/qt4kt5g2x3.pdf
* **Dedication / lineage:** Markowitz 1952 → Treynor 1962 / Sharpe 1964 CAPM → Ross 1976 APT → Jagannathan & Ma 2003 (constraints).

## Problem it solves
Sample covariance eigenvectors are **over-dispersed** in the large-N, fixed-T regime (number of assets N grows, history T fixed ≈ 250). Standard PCA on equity returns (dominant market factor with uniformly positive betas) estimates the leading eigenvector with elements *too spread out*. Using that biased eigenvector in a minimum-variance portfolio (MVP) **distorts weights** (over-bets low-beta, under-bets high-beta) and makes **risk forecasts severely biased downward** (you think you are hedged, you are not).

Crucially, the authors show the **eigenvalue bias is irrelevant** for this portfolio: the dominant eigenvalue error does NOT affect MVP. Only eigenvector dispersion matters — opposite to eigenvalue-shrinkage literature (Ledoit & Péché 2011, Wang & Fan 2017, El Karoui 2010/2013).

## Model & assumptions
* Single strong factor: `R = β f + ε`, `β_i > 0` for overwhelming majority, `β` has non-vanishing dispersion (true) vs sample estimate even more dispersed.
* Large-N asymptotics: `N → ∞, T fixed` (250 obs is the canonical experiment), factor strength grows linearly with `N` (not bounded eigenvalues). Contrasts weak-factor theory (Yata et al).
* Spiked covariance: truth `Σ = σ_f^2 ββ' + Diag(σ_ε^2)`. Sample is `S = (1/T) X'X`.
* Random-matrix results in **“large N, fixed T, large factor strength”** branch (statistics, not classical `N/T → c`).

## Core result (Theorems)
**Theorem 2.1–2.6 + 3.1–3.5 + D.1–D.3**: For the dominant population eigenvector `q_1` (normalized true betas) and sample eigenvector `\hat q_1`:
* Limit: as `N→∞` (T fixed), the dispersion (cross-sectional variance) of `\hat q_1` converges to `(σ_ε^2 / (T σ_f^2)) * something > 0` **above** true dispersion. I.e., upward bias in `Std(\hat q_{1,i})`.
* Orthogonal bias: `\hat q_1` angle error is characterized; correction shrinks `\hat q_1,i` **toward its cross-sectional mean** `\bar{\hat q}`.
* **Oracle correction**: `q^* = α \hat q_1 + (1-α) \bar{\hat q} 1` with optimal `α* ≈ 1 / (1 + T^{-1} * σ_ε^2 / σ_f^2 * (something))`. In simulation, `α*` estimated data-driven via variances of PCA residuals.
* Tracking error, volatility, variance forecast ratio (VFR = forecast² / realized²) all improve. Figs 3–4: `N=500→3000` shows VFR moves **0.3→0.95 → 1.0** after correction; before correction VFR ~0.3 (3× under-estimation). Equally-weighted portfolios unaffected (no dispersion).

## Oracle estimator & data-driven correction (Sec 4)
* Algorithm 1: compute sample PCA `\hat q_1`; estimate `γ = σ_ε^2 / σ_f^2` from trailing eigenvalues beyond factor (Bai 2008 style); compute `\hat α`; return ` \tilde q = \hat α \hat q_1 + (1-\hat α) \bar q 1` normalized.
* Estimation: `T=250, N=500` simulation → hat α stable; plug into MVP weight formula `w ∝ Σ^{-1} 1` with corrected eigenvector rebuild of `Σ`. Not eigenvalue shrinkage — **eigenvector shrinkage**.

## Experiments
* Simulation `N 500–3000, T 250`: MVP tracking error, vol, VFR compress after correction. No benefit without correction, even `N=3000`.
* Empirical hint: equity betas overly dispersed by PCA is documented (Blume 1975, Vasicek 1973, Bayesian beta adjustments). This paper gives the random-matrix justification and optimal shrinkage intensity.

## What it is NOT
* Not eigenvalue shrinkage (Ledoit-Wolf).
* Not regularization / positivity constraints (Jagannathan & Ma) — though those *implicitly* shrink dispersion, not optimally.
* Not analyst forecast dispersion (Diether 2002, ScienceDirect 2025 state-dependent mispricing). Different bias (strategic behavior → observed dispersion understates true 53%) — summarized in secondary section below.

## Secondary paper (Analyst Dispersion Anomaly, for disambiguation)
* ScienceDirect 2025: dispersion in analysts’ earnings forecasts predicts **lower future returns** only in **high optimism** state. Observed dispersion understates true disagreement by 53.4% due to strategic herding; bias varies cross-sectionally. Mechanism: disagreement + short-sale constraints + optimism → overpricing. Findings relevant to **sentiment-aware** trading (our `query_seeking_alpha_news`, `query_cftc_cot`), not to covariance.

## Limitations the authors state
* Single-factor, one bias source (dominant). Extensions to multiple factors, other bias forms, finite-sample size/likelihood estimation, empirical out-of-sample all “much work remains.”

