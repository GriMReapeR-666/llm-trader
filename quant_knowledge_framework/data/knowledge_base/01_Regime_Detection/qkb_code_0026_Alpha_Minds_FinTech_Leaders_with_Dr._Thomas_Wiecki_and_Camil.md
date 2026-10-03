# Alpha Minds FinTech Leaders with Dr. Thomas Wiecki and Camilo Saldarriaga #Finance #Bayesian #AI

Topic: 01_Regime_Detection
Confidence: 0.38
Keywords: market state, volatility, regime

---

## Alpha Minds: FinTech Leaders with Dr. Thomas Wiecki and Camilo Saldarriaga #Finance #Bayesian #AI

**Source**: https://www.youtube.com/watch?v=rfOoHkhYs_0  
**Video ID**: `rfOoHkhYs_0`  
**Category**: Risk Management

### Key Concepts

Regime-aware strategies detect market state changes and adjust exposure. Strategies fail when applying a single model across different market conditions.

### Code Module

```python
import numpy as np
import pandas as pd

class RegimeAwarePortfolio:
    """Portfolio that switches between regimes based on market state."""
    
    def detect_regime(self, returns, window=60):
        rolling_return = returns.rolling(window=window).mean()
        rolling_vol = returns.rolling(window=window).std()
        
        conditions = {
            'bull': (rolling_return > 0.001) & (rolling_vol < rolling_vol.quantile(0.7)),
            'bear': (rolling_return < -0.001) & (rolling_vol > rolling_vol.quantile(0.7)),
            'sideways': (rolling_return >= -0.001) & (rolling_return <= 0.001)
        }
        
        for regime, mask in conditions.items():
            if mask.iloc[-1]:
                return regime
        return 'sideways'
    
    def get_allocation(self, regime):
        allocations = {
            'bull': {'equities': 0.7, 'bonds': 0.2, 'cash': 0.1},
            'bear': {'equities': 0.2, 'bonds': 0.3, 'cash': 0.5},
            'sideways': {'equities': 0.5, 'bonds': 0.3, 'cash': 0.2}
        }
        return allocations.get(regime, allocations['sideways'])
```

### Explanation

Detects bull, bear, and sideways regimes using rolling return and volatility features, then applies different allocations to each regime.

---