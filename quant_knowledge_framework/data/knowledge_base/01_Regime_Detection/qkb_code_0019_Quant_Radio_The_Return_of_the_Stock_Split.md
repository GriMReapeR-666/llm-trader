# Quant Radio The Return of the Stock Split

Topic: 01_Regime_Detection
Confidence: 0.50
Keywords: volatility, regime

---

## Quant Radio: The Return of the Stock Split

**Source**: https://www.youtube.com/watch?v=xXceDil80ZU  
**Video ID**: `xXceDil80ZU`  
**Category**: Factor Investing

### Key Concepts

The value premium arises from buying cheap assets versus expensive growth assets. The cycle is driven by interest rates, regimes, and sentiment.

### Code Module

```python
import numpy as np
import pandas as pd

def value_vs_growth_signal(prices, book_values):
    pb_ratios = prices / book_values
    value_score = 1.0 / pb_ratios
    value_score = (value_score - value_score.mean()) / value_score.std()
    allocation = pd.Series(np.where(value_score > 0, 1.0, -1.0), index=prices.index)
    return allocation

def regime_adjusted_value_growth(returns, value_score, vol_window=60):
    rolling_vol = returns.rolling(window=vol_window).std()
    vol_adjustment = 1.0 - (rolling_vol / rolling_vol.rolling(window=252).mean())
    vol_adjustment = vol_adjustment.clip(0.3, 1.0)
    return value_score * vol_adjustment
```

### Explanation

Calculates value scores from P/B ratios and adjusts them based on volatility regimes.

---