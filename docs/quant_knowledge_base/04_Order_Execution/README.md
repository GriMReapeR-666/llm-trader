# 04_Order Execution

## Purpose

Order execution covers transaction cost modeling, market impact, spread dynamics, and smart order routing. Bridges the gap between theoretical backtest alpha and realized live returns.

## Folder Contents

- `transcriptions/` - Video transcriptions on execution algos, TCA, market microstructure
- `code_snippets/` - Reusable implementations:
  - Almgren-Chriss market impact calculator
  - Dynamic spread model
  - Implementation Shortfall decomposer
  - Smart order router (urgency-based)
  - VPIN / order flow toxicity estimator

## Key Concepts

| Concept | Formula / Threshold | Source |
| :--- | :--- | :--- |
| Dynamic Spread | $S_t = S_{\text{base}} \times [1 + \alpha(\sigma/\bar{\sigma}) + \beta(\bar{V}/V) + \gamma \mathbb{I}_{\text{event}}] \times M_{\text{session}}$ | Module 04 |
| Square-Root Impact | $I_{\text{total}} = Y \cdot \sigma_{\text{daily}} \cdot \sqrt{Q/V_{\text{ADV}}}$ | Module 04 |
| Permanent Impact | $I_{\text{perm}} = \gamma \cdot \sigma_{\text{daily}} \cdot P_0 \cdot (Q/V_{\text{ADV}})$ | Module 04 |
| Temporary Impact | $I_{\text{temp}} = \eta \cdot \sigma_{\text{daily}} \cdot P_0 \cdot (v/V_{\text{bar}})^\alpha$ | Module 04 |
| Implementation Shortfall | $\text{IS} = \text{Side} \times (P_{\text{exec}} - P_0) \times Q + \text{Explicit Costs}$ | Module 04 |

## Existing Sources

- `docs/retail quant model.txt` (lines 858-916)
- Module 04: Transaction Cost Engine & Market Impact

## Usage

```python
# STATUS (2026-09-20): `code_snippets/transaction_cost_engine.py` EXISTS in
# this folder — the "placeholder / no snippets yet" note below is stale.
# Placeholder for order execution snippets
# After transcription extraction, code snippets will be indexed here
```
