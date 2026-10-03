# Usefulness Evaluation — Project Mapping

**Evaluation gate:** `Useful` ⇔ (a) matches a live path where we *actually* do MVP/covariance/heat, (b) low latency & safe, (c) not already covered better by existing `Hierarchical`/`Ledoit`. Scored 0-5.

| System | What it fixes in THIS repo | Latency | Risk if misapplied | Existing overlap | Score | Verdict |
|---|---|---|---|---|---|---|
| **S3+S4 Oracle + Data-Driven α** (shrink `\hat q` → mean) | **Direct fix for our MVP weight distortion** — we have `HierarchicalMetaPortfolioEngine` (4 regimes, heat 4→6R) + `RiskManager.calc_position_size` + `live_advisory_hub.hierarchical_advisory` that currently use **uncorrected** PCA/beta. Sample N=500, T=250 matches exactly our 30m/H1 history. VFR 0.3→1.0 matters because `daily_reconciliation` & `quant_audit` currently under-forecast risk. | <0.2ms per call (mean + scalar α) | Low (shrink toward equal weight, never lever) | No — `hierarchical_portfolio:291` has no eigenvector step; Ledoit not used | **5/5** | **✅ GO — implement as advisory → gate** |
| **S2 Dispersion Theorem** | Justification for S3 — not standalone | 0 | — | — | 5/5 | **GO as doc** |
| **S6 VFR** | Diagnostic already mimicked in `quant_audit`/`profitability_foundation` but without benchmark. Add VFR= forecast/realized check to `daily_reconciliation` | 0 | — | `quant_audit` exists | **4/5** | **✅ GO as metric** |
| **S5 MVP Weight Model** | Explains why our `Hierarchical` `master_expectancy` mis-allocates when `\hat q` over-dispersed | 0 | — | — | 4/5 | **GO as doc** |
| **S1 Population Model** | Single-factor `R=βf+ε` is too narrow for 6-asset FX+magnificent but useful as *first factor only* | 0 | Medium if multi-factor needed | — | 3/5 | **GO with scope limit** |
| **S10 Simulation Harness** | Reuse N sweep to validate S3 before live — already have `research/ensemble_backtest.py` pattern | batch | — | — | 4/5 | **GO as test harness** |
| **S9 Analyst Dispersion** | Would gate `query_seeking_alpha_news` / `cftc_cot` under optimism proxy — but adds cross-sectional stock logic irrelevant to 6-asset FX H1/M15 live loop; state = high optimism not observable without order book | low but noisy | High false positives (overpricing signal vs our mean-reversion) | `opportunity_fusion` already has macro bias | **2/5** | **❌ NO-GO live; keep as RESEARCH notebook only** |
| **S8 Empirical Beta Bayes** | Vague shrinkage — S3 is optimal; no need | — | — | S3 supersedes | 2/5 | **NO-GO** |
| **S7 RMT Asymptotics** | Theory, not code | — | — | — | 5/5 doc | **GO as doc** |

## HFT / Senior lens

* **HFT relevance:** 5/5 for portfolio construction / risk forecasting at *swing* scale (our `poll 5s + LLM 15s` = H1/H4). **0/5 for tick HFT** — correction is `T=250` bars history, not microsecond edge. Do not market as HFT; keep as **heat & sizing** overlay.
* **Why HIGH for this repo:** We have **3 heat notions fighting** (`RiskManager 4.0R`, `pipeline_v2 6.0R`, `hierarchical heat 3.0R`) and `RiskManager.calc_position_size:598` uses `dist*100000` FX assumption — dispersion bias directly reduces the 3× under-estimation of risk that causes our `variance_forecast_ratio` blind spot. Cost is one scalar mean + α per regime; no matrix inversion change.
* **Why Analyst dispersion is NO-GO:** Requires short-sale constraint + optimism regime classifier not in FX; our instruments (EURUSD, XAUUSD) not driven by earnings forecast dispersion; adds lagging state filter that will conflict with `event_policy` blackout.

## Decision summary
* **Implement now:** S3+S4 + S6 metric, scoped to **dominant factor only**, behind `DISPERSION_BIAS_ADVISORY` flag.
* **Document only:** S1,S2,S7,S5,S10.
* **Do not implement live:** S9 (analyst), S8.

