# 01 — Taxonomy: Institutional vs Retail Grade

> Use this to label any quant paper/framework in 30 seconds without reading it fully.

---

## 1. Working definitions

| Grade | Definition (for this project) |
|---|---|
| **Institutional** | Assumes N≫1 assets, cross-sectional ranking, portfolio covariance inversion, sub-ms execution, colocation, prime-broker leverage, or regulatory capital — none of which a retail MT5 account (`core/risk_manager.py:15` caps risk_per_trade ≤2%) can use without leverage blowup. |
| **Retail** | Works with T observations of **one** instrument (OHLCV + symbol-specific volume), single-asset time-series signals, MT5 tick constraints, fixed 1R stops, max 2 concurrent positions (`core/risk_manager.py:510`), daily/weekly circuit breakers, and no short-borrow desk. |
| **Hybrid** | Core idea ports with modification (e.g., factor shrinkage concept yes, identity-covariance math no). |

---

## 2. Ten discriminants

| # | Discriminant | Institutional signal | Retail signal | Example |
|---|---|---|---|---|
| 1 | **Asset universe N** | N=100–3000 cross-section (Quant Platform, Barra) | N=1 (EURUSD time-series) — `core/alpha_factors.py:1` | Author-P–Author-K N=400 |
| 2 | **Covariance usage** | Invert N×N covariance for Markowitz weight `w ∝ C⁻¹1` | No inversion; ensemble z-score `core/alpha_ensemble.py:438` | Author-P–Author-K |
| 3 | **Latency budget** | 425 µs (LA-NAS-Lock), smart order routing | M15 bar (~15 min); `core/execution_policy.py` | LA-NAS-Lock |
| 4 | **Market structure** | A-share T+1, price limits, SSE-50; crypto tick | FX spot MT5 tick, weekend gaps | FinPFN A-share |
| 5 | **Data assumption** | Cross-sectional rank across 1000 stocks | Time-series rank `ts_rank(window=10)` `core/alpha_factors.py:24` | 101 Alphas |
| 6 | **Risk measure** | ES_99 CVaR portfolio LP `physics/0611027` | Single-trade 1R stop + daily 3% cap `core/risk_manager.py:238` | Author-K ES |
| 7 | **Evaluation** | Long-only cross-section IC across universe | Forward one-bar return `core/alpha_ensemble.py:398` + trade P&L | FactorMiner |
| 8 | **Compute** | GPU NAS, Dynamo orchestration | Single Python process polling MT5 | LA-NAS-Lock |
| 9 | **Regulatory** | Basel capital, margin-account NL constraints `cond-mat/0111503:§4` | Broker `volume_min/step` `core/risk_manager.py:721` | Author-P I |
| 10 | **Alpha source** | Vendor news, OpenBB `data/openbb_data_bridge.py` vs alternative data | Same, but must be *single-asset* portable | DeepFund |

---

## 3. Common mislabelings (this project specifically)

| Paper/concept | Often mislabeled | Correct grade | Why |
|---|---|---|---|
| Author-P–Author-K RMT filtering | "Retail risk tool" | Institutional | Requires N≥50 to have a Marčenko-Pastur bulk; with N=1 there is no spectrum to filter (`02-author-p-author-k-applicability.md`). |
| 101 Alphas | "Retail alphas" | Hybrid (institutional-origin, retail-adapted) | Kakushadze 2016 is cross-sectional (rank across stocks); `core/alpha_factors.py:4` time-series-ifies 23 of them — the adaptation *is* the contribution. |
| MARS Safety-Critic | "Institutional only" | Hybrid | Heterogeneous ensemble idea ports to retail as regime-conditioned risk multiplier (`core/hierarchical_portfolio.py:277` already does a coarse version). |
| LA-NAS-Lock | "Must-have for autonomy" | Institutional | Retail M15 does not have a latency problem to solve; optimizing 425µs is theater when broker spread is 0.8 pip. |
| FactorMiner / AlphaAgent | "Institutional mining" | Retail-applicable | Experience-memory / regularization ideas apply to single-asset factor lifecycle even without cross-section. |

---

## 4. The retail litmus test (copy-paste checklist for PR review)

Before adding a paper's method, answer:

- [ ] Does it operate on **1 symbol's time-series** without requiring rank across 100+ symbols?
- [ ] Can it be validated with **1 forward return** (`shift(-1)`) not a cross-sectional IC across a universe?
- [ ] Does it respect **MT5 execution**: `symbol_info().volume_step/min/max` and 1R stops, not zero-cost long-short?
- [ ] Does it survive with **T=250–1000 bars** (`r≈0.05–0.2` for N=1) not T=5000 cross-section points?
- [ ] Does it add **risk control** beyond `check_daily_drawdown()` / `check_portfolio_heat()` `core/risk_manager.py:604`?

If any box is unchecked, the paper is institutional-grade and belongs behind a feature flag, not in the default path.

---

## 5. Why this distinction matters right now

This codebase currently trades **one symbol at a time** with `max 2 positions` and `R=1%`. Misapplying institutional portfolio math (covariance inversion, ES_99 LP) will:

1. Introduce O(N²) estimation error where N=1 has none (phantom problem).
2. Add latency/complexity that creates real risk (fragmentation, overfitting) without real benefit.
3. Hide the *actual* retail risks: alpha decay, single-asset regime misclassification, spread/slippage, and leverage mis-sizing (`core/margin_safety.py`).

The institutional stack becomes relevant only at the milestone: **multi-symbol portfolio sleeve** (see `02-author-p-author-k-applicability.md:§4 Tripwires`).
