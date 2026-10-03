# Systems Catalog — Extracted from The Dispersion Bias

Every formal system/tool/theorem extracted. `Inputs → Outputs → Invariants`.

## S1 — Population Spiked Covariance Model (Sec 2.1)
* **Description:** Single-factor return `R = β f + ε`, `β_i > 0` positively dispersed, `f ⟂ ε`.
* **Inputs:** `β ∈ ℝ^N`, `σ_f^2`, `σ_ε^2`
* **Outputs:** `Σ = σ_f^2 ββ' + Diag(σ_ε^2)`
* **Repo analog:** `hierarchical_portfolio.RegimeDetection` assumes factor; `metrics.calculate_hurst_exponent` etc.

## S2 — Sample PCA Eigen-dispersion Theorem (Thm 2.1–2.6)
* **Inputs:** `X ∈ ℝ^{T×N}` T=250, `S = (1/T)X'X`, `N→∞`
* **Outputs:** Bias `E[Var(\hat q_{1,i}) - Var(q_{1,i})] = (σ_ε^2)/(T σ_f^2) * c > 0`
* **Artifact:** Proof that eigenvalue bias irrelevant for MVP, eigenvector bias dominates.

## S3 — Oracle Dispersion Correction (Thm 3.1, 3.3) ⭐ PRIMARY
* **Formula:** `q^* = α \hat q + (1-α) \bar{\hat q} 1`, `α* = 1 / (1 + σ_ε^2/(T σ_f^2 * ?) )`
* **Inputs:** `\hat q_1` (sample), `\bar{\hat q}` mean, `α`
* **Outputs:** `\tilde q` shrunk toward equal weight
* **Property:** `execution_authority:False` — rebuilds `Σ_tilde` then MVP weights; risk forecast multiplied back to 1.

## S4 — Data-Driven α Estimator (Sec 4, Algorithm 1)
* **Inputs:** trailing eigenvalues `λ_2..T`, `T`, `N`
* **Outputs:** `\hat α` via `γ = σ_ε^2/σ_f^2` estimated from residual spectrum (Bai 2008)
* **Steps:** `SVD → \hat q_1 → residual res → γ_hat → α_hat → \tilde q`

## S5 — MVP Weight Distortion Model (Sec 2.2)
* **Inputs:** true `q`, biased `\hat q`, `Σ`
* **Outputs:** Weight error `||w(\hat q)-w(q)||` driven by first PC, tracking error ↑

## S6 — Variance Forecast Ratio (VFR) Metric (Sec 4.3, Fig 3–4)
* **Inputs:** `VFR = forecast^2 / realized^2` per portfolio
* **Outputs:** Bias diagnosis; `VFR≈0.3` without fix, `≈1` with. Equally-weighted VFR unaffected.

## S7 — Large-N, Fixed-T RMT Asymptotics (Appendix A/D)
* **Inputs:** `N large, T fixed` spiked model
* **Outputs:** Limit laws for `\hat q` dispersion, `α*` optimal

## S8 — Empirical Beta Bayesian Adjacent ( vasicek/blume )
* **Inputs:** OLS betas
* **Outputs:** Shrunk β (author cites as informal predecessor — not their theorem)

## S9 — Analyst Forecast Dispersion Bias (Secondary literature, NOT S3)
* **Inputs:** analysts’ strategic forecasts
* **Outputs:** Observed dispersion understates true by 53.4%; anomaly: high dispersion → low future returns only under high optimism + short-sale constraints. State-dependent mispricing (ScienceDirect 2025).
* **Distinct:** cross-sectional disagreement, not eigenvector.

## S10 — Simulation Test Harness (Fig 3–4)
* **Inputs:** `N 500/3000, T 250, 50 datasets, γ_{β,z} sweep`
* **Outputs:** Tracking error, vol, VFR panels (a)–(d) for MVP vs equal-weight

## Plumbing not counted as systems
Ledoit-Péché / Jagannathan-Ma shrinkage, Green & Hollifield factor structure — cited as contrast, not proposed.

