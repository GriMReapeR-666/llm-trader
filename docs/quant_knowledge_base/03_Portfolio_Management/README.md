# 03_Portfolio Management

## Purpose

Portfolio management covers capital allocation, risk budgeting, volatility targeting, and multi-strategy ensemble construction. Ensures that individual strategy drawdowns cancel at the portfolio level.

## Folder Contents

- `transcriptions/` - Video transcriptions on portfolio construction, risk parity, Kelly criterion
- `code_snippets/` - Reusable implementations:
  - Hierarchical Risk Parity (HRP) optimizer
  - Kelly Criterion / Fractional Kelly calculator
  - Inverse volatility parity allocator
  - Correlation matrix & heat-map generator
  - Portfolio heat & exposure tracker

## Key Concepts

| Concept | Formula / Threshold | Source |
| :--- | :--- | :--- |
| Quarter-Kelly | $\text{Applied Risk \%} = 0.25 \times K^*$ | retail quant model.txt |
| Portfolio Heat Cap | $\sum \text{Open Risk}_i \le 3.0R - 5.0R$ | Module 01/03 |
| Correlation Gate | Rolling $r < 0.30$ | retail quant model.txt |
| Volatility Parity | $w_i \propto 1/\sigma_i$ | Module 06 |
| Calmar Ratio | $\text{Calmar} = \text{CAGR} / |\text{MaxDD}| \ge 1.5$ | Module 01 |

## Existing Sources

- `docs/retail quant model.txt` (lines 286-295, 349-354, 486-493)
- `docs/QUANT_RESEARCH_AND_SYSTEM_FOUNDATIONS.md`
- Module 01: Mathematical Pillars
- Module 06: Robustness Playbook Playbook & Compounding Systems

## Usage

```python
# Placeholder for portfolio management snippets
# After transcription extraction, code snippets will be indexed here
```
