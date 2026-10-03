# Paper 0 — Evaluating the RiskMetrics Methodology in Measuring Volatility and Value-at-Risk (cond-mat/0103107)

> **Bibliography:** Author-P, S. & Author-K, I. (2001). *Evaluating the RiskMetrics Methodology in Measuring Volatility and Value-at-Risk in Financial Markets.* arXiv:cond-mat/0103107. Published as Physica A 299, 305–310 (2001).  
> **PDF:** `0103107.pdf` (7 pp., 1 fig.)  
> **Role in series:** Prequel / motivation. Before tackling covariance-matrix noise, the authors dismantle the industry-standard RiskMetrics framework and show its empirical success is largely artefactual — setting up why naive historical estimators should not be trusted.

---

## Abstract (essence)

RiskMetrics assumes conditionally normal returns and uses an exponentially weighted moving average (EWMA, λ=0.94 for daily data) for volatility, then obtains VaR by multiplying σ by the normal quantile (1.65 for 95% VaR). Despite ignoring fat tails, backtests look good. Author-P–Author-K show this success is due to (i) the very short 1-day forecasting horizon for σ and (ii) the particular 95% quantile where normal and empirical tails accidentally coincide. At longer horizons or higher confidence (e.g. 99%), VaR is severely underestimated.

---

## Core thesis

> **RiskMetrics works *despite* its assumptions, not because of them, and only under the specific risk-measurement choices where errors cancel.** Change the horizon or the quantile and the method fails — a warning that pervades the whole series: estimator quality is inseparable from the loss function and evaluation setup.

---

## Method

- Data: USD/DEM (now EUR) daily FX returns + S&P 500 (details in paper).
- Estimator: σ²_{t+1|t} = (1-λ) Σ_{τ≥0} λ^τ r²_{t-τ}  (eq. 1, EWMA, λ=0.94 daily, 0.97 monthly).
- Benchmark: GARCH(1,1) and plain historical σ.
- Evaluation: compare 1-day-ahead σ forecasts vs realized |r_{t+1}|; and VaR hit rates at p=95% vs 99%; plus multi-day horizon tests where EWMA forecast degrades.

---

## Key results

| Finding | Detail |
|---|---|
| **Volatility forecasting** | For 1-day horizon all three estimators (EWMA, GARCH, historical) perform similarly (Fig. 1). EWMA's advantage is negligible because squared returns are nearly unpredictable at 1 day. |
| **Horizon dependence** | At 10–25 day horizons, GARCH and historical improve over EWMA; EWMA's infinite-memory decay is suboptimal for longer forecasts. |
| **VaR at 95%** | Empirical VaR ≈ 1.65·σ holds coincidentally — the normal quantile at 95% happens to match the fat-tailed empirical quantile for these series. |
| **VaR at 99%** | Normal quantile 2.33 severely underestimates true VaR (which is ~3–4σ for fat tails). RiskMetrics VaR hit rate at 99% is ~2× the nominal violation rate. |
| **Fat tails matter** | Quantile-quantile plots show Gaussian assumption violated in tails; but VaR@95% is not tail-sensitive enough to reveal it. |

---

## Why it belongs in the portfolio-optimization series

This paper establishes the authors' methodological stance that will recur:

1. **Simulate / backtest honestly** — don't let a convenient evaluation choice hide estimator weakness.
2. **Heteroskedasticity (EWMA) vs noise filtering** are distinct problems; RiskMetrics addresses the first but not the second. Paper `04` will later *combine* them.
3. **VaR's incoherence** (not subadditive) is noted in passing and motivates the later shift to Expected Shortfall / coherent measures in paper `06`.

---

## Trading-system implications

- **Do not use RiskMetrics λ=0.94 EWMA as a plug-and-play vol estimator for multi-day holding periods.** For the swing-horizon (3–25 days) common in this codebase, a GARCH or realized-vol model will dominate.
- **VaR@95% is a weak validation target.** Any VaR backtest should include 99% and 99.5% and multi-day horizons — otherwise you cannot distinguish a good model from a lucky quantile coincidence.
- **Precursor to `04`:** the EWMA estimator analyzed here becomes the *base* that the hybrid EW+RMT filter will later improve upon. Keep λ configurable; don't hard-code 0.94.

---

## Limitations / what the paper does not do

- Small empirical sample (FX + S&P only); no cross-sectional covariance analysis (that starts in `01`).
- No portfolio optimization yet — single-asset risk measurement only.
- Normal vs Student-t comparison is qualitative; no formal tail-index estimation.
