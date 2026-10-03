# Quant Radio Political Uncertainty and Commodity Markets

Topic: 05_Quant_Utilities
Confidence: 0.75
Keywords: pandas, numpy, python

---

## Quant Radio: Political Uncertainty and Commodity Markets

**Source**: https://www.youtube.com/watch?v=iitvZA0tpUU  
**Video ID**: `iitvZA0tpUU`  
**Category**: Risk Management

### Key Concepts

Heavy-tailed markets require robust volatility estimation beyond normality assumptions.

### Code Module

```python
import numpy as np
import pandas as pd

def realized_volatility(returns, window=20):
    return returns.rolling(window=window).std() * np.sqrt(252)

def ewma_volatility(returns, lambda_param=0.94):
    squared_returns = returns ** 2
    return squared_returns.ewm(alpha=1 - lambda_param).mean().apply(np.sqrt) * np.sqrt(252)

def garch_volatility_forecast(returns, omega=0.01, alpha=0.1, beta=0.85, horizon=5):
    sigma2 = np.zeros(len(returns) + horizon)
    sigma2[0] = np.var(returns)
    for t in range(1, len(returns)):
        sigma2[t] = omega + alpha * returns[t-1]**2 + beta * sigma2[t-1]
    forecast = np.zeros(horizon)
    forecast[0] = sigma2[-1]
    for h in range(1, horizon):
        forecast[h] = omega + (alpha + beta) * forecast[h-1]
    return np.sqrt(forecast) * np.sqrt(252)
```

### Explanation

Realized, EWMA, and GARCH volatility estimators for heavy-tailed financial markets.

---