# Quant Radio Short-Term Correlated Stress Reversal Trading

Topic: 01_Regime_Detection
Confidence: 0.40
Keywords: volatility, regime

---

## Quant Radio: Short-Term Correlated Stress Reversal Trading

**Source**: https://www.youtube.com/watch?v=6I_v9CyVd30  
**Video ID**: `6I_v9CyVd30`  
**Category**: AI / Machine Learning

### Key Concepts

AI in trading ranges from pure ML models to hybrid architectures. Centaur trading embeds human judgment into the training pipeline.

### Code Module

```python
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier

class CentaurTradingSystem:
    """Hybrid AI-human trading system."""
    
    def __init__(self):
        self.model = GradientBoostingClassifier(n_estimators=100, random_state=42)
    
    def train(self, X, y, regime_context=None):
        """Train with human-curated feature selection."""
        if regime_context is not None:
            X = self.human_curate_features(X, regime_context)
        self.model.fit(X, y)
        return self.model
    
    def predict(self, X, regime_context=None):
        """Generate predictions with regime-aware feature curation."""
        if regime_context is not None:
            X = self.human_curate_features(X, regime_context)
        return self.model.predict(X)

def detect_regime_shift(returns, window=60, threshold=2.0):
    """Detect market regime shifts using volatility patterns."""
    rolling_vol = returns.rolling(window=window).std()
    vol_spike = rolling_vol > (rolling_vol.mean() + threshold * rolling_vol.std())
    return vol_spike
```

### Explanation

Humans curate features relevant to the current market regime before model training. Regime shift detection identifies when conditions have changed enough to re-evaluate assumptions.

---