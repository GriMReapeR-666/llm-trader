"""
AI and Machine Learning in Trading
============================================================

Source: Educational Platform YouTube Channel
Category: AI / Machine Learning

This module implements quantitative finance concepts discussed in the Educational Platform video lectures.

Concepts:
AI in trading ranges from pure ML models to hybrid architectures. Centaur trading embeds human judgment into the training pipeline.
"""

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
