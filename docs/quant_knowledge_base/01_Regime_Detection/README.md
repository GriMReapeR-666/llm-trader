# 01_Regime Detection

## Purpose

Regime detection identifies the current market state (trending, range-bound, high-volatility crisis) to route capital to the appropriate sub-strategy and adjust position sizing dynamically.

## Folder Contents

- `transcriptions/` - Video transcriptions on regime detection, HMM, Hurst exponent, BOCPD
- `code_snippets/` - Reusable implementations:
  - Hurst exponent calculator
  - Hidden Markov Model regime classifier
  - Bayesian Online Changepoint Detection (BOCPD)
  - ADX/ATR regime filters
  - Regime probability state vector

## Key Concepts

| Concept | Formula / Threshold | Source |
| :--- | :--- | :--- |
| Hurst Exponent | $H > 0.55$ (Trend), $H < 0.45$ (Range) | retail quant model.txt |
| ADX Threshold | $\text{ADX}_{14} > 25$ (Trend), $< 20$ (Range) | retail quant model.txt |
| ATR Percentile | 85th/90th percentile for high-vol gate | retail quant model.txt |
| BOCPD | Run-length probability $P(r_t \mid x_{1:t})$ | Module 03 |
| Regime Router | $w_{\text{trend}} \to 80\%$, $w_{\text{range}} \to 80\%$ | Module 03 |

## Existing Sources

- `docs/retail quant model.txt` (lines 258-294, 495-500)
- `docs/analytical_frameworks_and_epistemology/bma_causal_autonomous_system/geopolitical_predictive_and_sanctions_intelligence/`
- Module 03: Six-Layer Autonomous System

## Usage

```python
# STATUS (2026-09-20): `regime_modules.py` EXISTS in this folder — the
# "placeholder / no snippets yet" note below is stale. Content indexing
# happens through the plugin layer (core/harness/extracted_module_plugins.py).
# Placeholder for regime detection snippets
# After transcription extraction, code snippets will be indexed here
```
