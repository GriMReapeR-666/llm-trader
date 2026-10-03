# Quant Radio Music Royalties vs. the Stock Market

Topic: 05_Quant_Utilities
Confidence: 0.38
Keywords: pandas, numpy, python

---

## Quant Radio: Music Royalties vs. the Stock Market

**Source**: https://www.youtube.com/watch?v=IS-EDw_dLwI  
**Video ID**: `IS-EDw_dLwI`  
**Category**: Portfolio Construction

### Key Concepts

Diversification is the only free lunch in finance. Effective diversification requires measuring correlation structure, not just asset count.

### Code Module

```python
import numpy as np
import pandas as pd

def diversification_ratio(weights, returns):
    """Calculate the diversification ratio of a portfolio."""
    portfolio_vol = np.sqrt(np.dot(weights.T, np.dot(returns.cov(), weights)))
    weighted_vol = np.sum(np.abs(weights) * returns.std())
    return weighted_vol / portfolio_vol

def effective_number_of_assets(weights):
    """Calculate effective number of positions (ENP)."""
    enp = 1.0 / np.sum(np.square(weights))
    return enp

def optimize_for_diversification(expected_returns, cov_matrix, max_weight=0.1):
    """Build portfolio maximizing diversification while targeting return."""
    n_assets = len(expected_returns)
    weights = np.ones(n_assets) / n_assets
    weights = np.clip(weights, 0, max_weight)
    weights = weights / np.sum(weights)
    return weights
```

### Explanation

The diversification ratio measures volatility reduction. ENP reveals concentration risk. Weight caps prevent over-concentration.

---