# Implementation Blueprint — Dispersion Bias Correction (GO System S3+S4)

## Scope
Single-factor dominant eigenvector shrink, **dominant only**, `N` = number of assets in heat basket (our heat uses 4–6), `T` = rolling 250 closes (H1 ≈ 10 days). Not per-tick, not multi-factor until empirical win.

## Feature flag
```env
DISPERSION_BIAS_ADVISORY=1      # 0 off
DISPERSION_BIAS_PRODUCTION=0    # 0 advisory (log + toon), 1 gate (requires risk_clamp)
```

## Files to touch (minimal, fail-closed)
* **New:** `core/dispersion_bias.py` — pure `correct_eigenvector(q_hat, T, gamma_hat) -> q_tilde, alpha`
* **Edit:** `core/hierarchical_portfolio.py:60` `detect_regime()` — inject corrected `\tilde q` into `p_crisis/p_hv_trend` weighting (or just pass `alpha` as telemetry first)
* **Edit:** `core/risk_manager.py:615` `calc_position_size()` — accept `dispersion_alpha` to scale `effective_risk_pct *= (0.8 + 0.2*alpha)` (under-estimated risk → shrink more when α low)
* **Edit:** `core/live_advisory_hub.py:1` — add `dispersion_advisory` to `build_live_advisory_snapshot()`
* **Edit:** `intelligence/llm_orchestrator.py:860` `toon_lite` — add `dispersion_advisory` (like `hierarchical_advisory`)
* **New test:** `tests/test_dispersion_bias.py` — simulation N=500 T=250 VFR 0.3→1.0 + weight distortion down + alpha bounds [0,1]
* **Metric:** `operations/daily_reconciliation.py` or `research/profitability_foundation.py` add `VFR = forecast_vol²/realized_vol²`

## Pseudocode `core/dispersion_bias.py`
```python
def estimate_gamma_hat(eigenvalues, T, N, factor_idx=0) -> float:
    # Bai 2008: residual var from bulk eigenvalues beyond first factor
    bulk = eigenvalues[factor_idx+1: min(T, N)]
    sigma_eps2 = np.mean(bulk)  # simplest; refined uses Marchenko-Pastur edge
    sigma_f2 = max(eigenvalues[factor_idx] - sigma_eps2, 1e-9)
    return float(sigma_eps2 / max(sigma_f2, 1e-9))

def correct_eigenvector(q_hat: np.ndarray, T: int, gamma_hat: float) -> tuple[np.ndarray, float]:
    # q_hat: sample leading eigenvector, normalized, positive mean
    if len(q_hat) < 5 or T <= 0: return q_hat, 1.0
    q_hat = q_hat / np.linalg.norm(q_hat)
    q_bar = float(np.mean(q_hat))
    # paper Thm 3.1 optimal alpha; simplified finite-sample
    alpha = 1.0 / (1.0 + gamma_hat / max(T,1))
    alpha = float(np.clip(alpha, 0.2, 1.0))  # never fully collapse to equal weight
    q_tilde = alpha * q_hat + (1-alpha) * q_bar
    q_tilde = q_tilde / np.linalg.norm(q_tilde)
    return q_tilde, alpha

def variance_forecast_ratio(forecast_vol, realized_vol) -> float:
    return float((forecast_vol**2) / max(realized_vol**2, 1e-12))
```

## Integration steps

### Step 0 — Tests first (P0)
* `test_dispersion_bias.py`: 
  - `test_alpha_bounds`: `0.2 ≤ α ≤ 1` for any gamma/T
  - `test_shrink_reduces_dispersion`: `Var(q_tilde) < Var(q_hat)`
  - `test_vfr_improves`: simulate `R = β f + ε`, `β_i ~ U[0.8,1.2]`, `T=250, N=500`, compute MVP with/without → `VFR_before <0.5, VFR_after >0.8`
  - `test_no_crash_on_equal_weight`: `q_hat = 1/√N` → `q_tilde ≈ q_hat`

### Step 1 — Pure function `core/dispersion_bias.py` (<0.2ms)
No I/O, no DB. Unit-testable. `execution_authority:False`.

### Step 2 — Live advisory hub (advisory, no gate)
```python
# live_advisory_hub.py
def dispersion_advisory(prices_matrix, T=250):
    q_hat = PCA(prices_matrix).eigenvectors[0]  # or reuse hierarchical detector's hurst/adx path
    gamma = estimate_gamma_hat(eigenvalues, T, N)
    q_tilde, alpha = correct_eigenvector(q_hat, T, gamma)
    return {
        "enabled": _flag("DISPERSION_BIAS_ADVISORY"),
        "alpha": round(alpha,3),
        "gamma_hat": round(gamma,4),
        "dispersion_before": round(float(np.var(q_hat)),6),
        "dispersion_after": round(float(np.var(q_tilde)),6),
        "vfr_proxy": None, # filled after reconciliation
        "execution_authority": False,
        "advisory_only": True,
    }
```
Add to `build_live_advisory_snapshot()` → `toon_lite["dispersion_advisory"]`.

### Step 3 — Profitability: promote to risk gate (only after 2 weeks advisory)
Gate condition: if `DISPERSION_BIAS_PRODUCTION=1` **and** `VFR_before <0.6` for 10 consecutive reconciliations **and** `alpha <0.8` → `effective_risk_pct *= (0.8+0.2*alpha)` and `heat_cap *= alpha`. Still fail-closed: if `alpha` NaN → `alpha=1` no effect.

### Step 4 — Documentation & audit
* `journal_reconciliation` add `variance_forecast_ratio` and `alpha` columns
* `daily_reconciliation.compare_day` logs `dispersion_bias: {alpha, vfr}`
* `incident_drills` includes dispersion scenario

## What NOT to do
* Not eigenvalue shrinkage (keep Ledoit out)
* Not multi-factor (keep single dominant; N=4–6 too small for 2nd PC)
* Not per-tick (run once per H1 close, not per 1.8s MCP poll)
* Not analyst dispersion (separate file `docs/dispersion_bias/ANALYST_DISPERSION_NO-GO.md` if needed)

## Latency budget
* `correct_eigenvector` + `estimate_gamma` <0.2ms, no pandas, no network, fits inside existing `hierarchical_advisory` 0.5ms. No HFT penalty.

## Rollback
Flip `DISPERSION_BIAS_ADVISORY=0` — no code path. No DB migration.

## Senior sign-off checklist
- [ ] Tests pass VFR improvement on synthetic `N=500 T=250`
- [ ] Advisory logs `alpha` for 2 weeks before gate
- [ ] `heat_cap` never increased above 4.0R by this system (only shrinks)
- [ ] No plaintext secrets, no new `execution_authority`

