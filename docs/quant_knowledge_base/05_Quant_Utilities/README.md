# 05_Quant Utilities

## Purpose

Shared, cross-topic utility functions, data pipelines, and mathematical helpers used across all quant domains. Contains reusable code that doesn't belong to a single specialized topic.

## Folder Contents

- `shared_functions/` - Cross-cutting utilities:
  - R-multiple and expectancy calculators
  - Drawdown and streak probability analyzers
  - Sharpe/Sortino/Calmar ratio calculators
  - Fractional differentiation engine
  - ADF / stationarity test wrappers
  - Data validation and cleaning helpers
- `data_pipelines/` - Data ingestion and feature engineering:
  - yfinance / FRED / CCXT ingestion wrappers
  - Feature engineering pipelines
  - Point-in-time data alignment
  - survivorship-bias-free universe builders

## Key Concepts

| Utility | Description | Source |
| :--- | :--- | :--- |
| R-Multiple | $R = \frac{P_{\text{exit}} - P_{\text{entry}}}{P_{\text{entry}} - P_{\text{stop}}}$ | Module 01 |
| EV Calculator | $\text{EV} = (P_{\text{win}} \times W) - (P_{\text{loss}} \times L)$ | Module 01 |
| Streak Probability | $P(k) = (1 - P_{\text{win}})^k$ | Module 01 |
| Fractional Diff | $(1 - B)^d X_t$ with $d \in [0.30, 0.70]$ | Module 02/03 |
| Max Drawdown | $\text{MaxDD} = \max_t \frac{\text{Peak}_t - \text{Equity}_t}{\text{Peak}_t}$ | Module 01 |

## Existing Sources

- `docs/retail quant model.txt` (all modules)
- `docs/QUANT_RESEARCH_AND_SYSTEM_FOUNDATIONS.md`
- All `retail_quant_master_architecture/` modules

## Usage

```python
# STATUS (2026-09-20): `quant_agent_framework.py` (236L) and
# `universal_quant_interface.py` (159L) EXIST in this folder — the
# "placeholder / no snippets yet" note below is stale.
# Placeholder for shared utilities
# After transcription extraction, cross-topic snippets will be indexed here
```
