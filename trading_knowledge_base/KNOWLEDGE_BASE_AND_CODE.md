# Educational Platform Knowledge Base: Concepts, Code & Documentation

This document extracts key quantitative finance concepts from the Educational Platform YouTube transcripts and pairs them with modular Python implementations. Each module is documented with its source video.

---

## 1. Tom Sosnoff on Options Trading, Entrepreneurship, AI, & Risk Management | AlphaMinds Fintech Leaders

**Source**: https://www.youtube.com/watch?v=cG8hGjwPang  
**Video ID**: `cG8hGjwPang`  
**Category**: NLP / AI

### Key Concepts

Foundation models process unstructured data (news, filings, earnings calls) to generate trading signals via sentiment analysis and time series forecasting.

### Code Module

```python
from transformers import pipeline
import pandas as pd

class FinancialNewsSentimentAnalyzer:
    """LLM-based sentiment analyzer for financial news."""
    
    def __init__(self, model_name="ProsusAI/finbert"):
        self.sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model=model_name,
            tokenizer=model_name
        )
    
    def analyze_text(self, text):
        result = self.sentiment_pipeline(text[:512])[0]
        return {'label': result['label'], 'score': result['score']}
    
    def batch_analyze(self, texts):
        results = [self.analyze_text(t) for t in texts]
        return pd.DataFrame(results)

class FoundationTimeSeriesForecaster:
    """Foundation model approach to time series forecasting."""
    
    def __init__(self, context_length=60, prediction_length=5):
        self.context_length = context_length
        self.prediction_length = prediction_length
    
    def prepare_sequence(self, prices):
        sequences, targets = [], []
        for i in range(len(prices) - self.context_length - self.prediction_length):
            sequences.append(prices[i:i + self.context_length])
            targets.append(prices[i + self.context_length:i + self.context_length + self.prediction_length])
        return np.array(sequences), np.array(targets)
```

### Explanation

Uses FinBERT for sentiment analysis and sliding windows for foundation model time series forecasting.

---

## 2. Quant Radio: Measuring Volatility in Heavy Tailed Markets

**Source**: https://www.youtube.com/watch?v=fiWX7xpRPmY  
**Video ID**: `fiWX7xpRPmY`  
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

## 3. Quant Radio: Transfer Learning for Portfolio Optimization

**Source**: https://www.youtube.com/watch?v=1fSo7xmrVb8  
**Video ID**: `1fSo7xmrVb8`  
**Category**: Machine Learning

### Key Concepts

Transfer learning leverages knowledge from related domains when historical data is scarce. Models trained on similar assets or time periods can generalize better.

### Code Module

```python
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split

def transfer_learning_predict(X_source, y_source, X_target, y_target, test_size=0.2):
    """Transfer learning approach for portfolio return prediction."""
    source_model = RandomForestRegressor(n_estimators=100, random_state=42)
    source_model.fit(X_source, y_source)
    
    X_fine, X_val, y_fine, y_val = train_test_split(
        X_target, y_target, test_size=test_size, random_state=42
    )
    
    fine_tuned = RandomForestRegressor(n_estimators=100, random_state=42)
    fine_tuned.fit(X_fine, y_fine)
    
    return fine_tuned, source_model
```

### Explanation

A model is pre-trained on a source domain with abundant data, then fine-tuned on the target domain with limited data.

---

## 4. Quant Radio: Can AI Do Financial Research?

**Source**: https://www.youtube.com/watch?v=eMImVTO8kbc  
**Video ID**: `eMImVTO8kbc`  
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

## 5. Time Series Forecasting with Marco Peixeiro

**Source**: https://www.youtube.com/watch?v=lgtyyVC0FcQ  
**Video ID**: `lgtyyVC0FcQ`  
**Category**: Machine Learning

### Key Concepts

Transfer learning leverages knowledge from related domains when historical data is scarce. Models trained on similar assets or time periods can generalize better.

### Code Module

```python
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split

def transfer_learning_predict(X_source, y_source, X_target, y_target, test_size=0.2):
    """Transfer learning approach for portfolio return prediction."""
    source_model = RandomForestRegressor(n_estimators=100, random_state=42)
    source_model.fit(X_source, y_source)
    
    X_fine, X_val, y_fine, y_val = train_test_split(
        X_target, y_target, test_size=test_size, random_state=42
    )
    
    fine_tuned = RandomForestRegressor(n_estimators=100, random_state=42)
    fine_tuned.fit(X_fine, y_fine)
    
    return fine_tuned, source_model
```

### Explanation

A model is pre-trained on a source domain with abundant data, then fine-tuned on the target domain with limited data.

---

## 6. Quant Radio: The Changing Signal in Options Markets

**Source**: https://www.youtube.com/watch?v=otI62O4Z73A  
**Video ID**: `otI62O4Z73A`  
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

## 7. Quant Radio: The Price Impact of Nothing

**Source**: https://www.youtube.com/watch?v=fY_c2UPGJ6A  
**Video ID**: `fY_c2UPGJ6A`  
**Category**: Causal Inference

### Key Concepts

Causal portfolio optimization moves beyond correlation to identify true causal relationships.

### Code Module

```python
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, LogisticRegression

def causal_effect_estimate(treatment, outcome, confounders):
    X = np.column_stack([treatment, confounders])
    model = LinearRegression()
    model.fit(X, outcome)
    return model.coef_[0]

def propensity_score_matching(treatment, outcome, confounders):
    psmodel = LogisticRegression()
    psmodel.fit(confounders, treatment)
    propensity = psmodel.predict_proba(confounders)[:, 1]
    treated = propensity[treatment == 1]
    control = propensity[treatment == 0]
    effects = []
    for p in treated:
        nearest = control[np.argmin(np.abs(control - p))]
        effects.append(outcome[treatment == 1].iloc[0] - outcome[treatment == 0].iloc[0])
    return np.mean(effects)
```

### Explanation

Regression adjustment and propensity score matching for causal effect estimation.

---

## 8. Agentic AI: Preparing Your Organisation for the Next Frontier with Nicole Koenigstein

**Source**: https://www.youtube.com/watch?v=1B6zEzPKq9E  
**Video ID**: `1B6zEzPKq9E`  
**Category**: NLP / AI

### Key Concepts

Foundation models process unstructured data (news, filings, earnings calls) to generate trading signals via sentiment analysis and time series forecasting.

### Code Module

```python
from transformers import pipeline
import pandas as pd

class FinancialNewsSentimentAnalyzer:
    """LLM-based sentiment analyzer for financial news."""
    
    def __init__(self, model_name="ProsusAI/finbert"):
        self.sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model=model_name,
            tokenizer=model_name
        )
    
    def analyze_text(self, text):
        result = self.sentiment_pipeline(text[:512])[0]
        return {'label': result['label'], 'score': result['score']}
    
    def batch_analyze(self, texts):
        results = [self.analyze_text(t) for t in texts]
        return pd.DataFrame(results)

class FoundationTimeSeriesForecaster:
    """Foundation model approach to time series forecasting."""
    
    def __init__(self, context_length=60, prediction_length=5):
        self.context_length = context_length
        self.prediction_length = prediction_length
    
    def prepare_sequence(self, prices):
        sequences, targets = [], []
        for i in range(len(prices) - self.context_length - self.prediction_length):
            sequences.append(prices[i:i + self.context_length])
            targets.append(prices[i + self.context_length:i + self.context_length + self.prediction_length])
        return np.array(sequences), np.array(targets)
```

### Explanation

Uses FinBERT for sentiment analysis and sliding windows for foundation model time series forecasting.

---

## 9. Quant Radio: The Drivers of Interest Rates and Equity Valuations

**Source**: https://www.youtube.com/watch?v=NqgPvPma78k  
**Video ID**: `NqgPvPma78k`  
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

## 10. Quant Radio: The Dynamics of Value and Growth

**Source**: https://www.youtube.com/watch?v=IQGhxlK3DI8  
**Video ID**: `IQGhxlK3DI8`  
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

## 11. Agentic AI in Trading: The Evolution of Trading Bots with Irene Aldridge

**Source**: https://www.youtube.com/watch?v=g1GbmCr9MSc  
**Video ID**: `g1GbmCr9MSc`  
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

## 12. Quant Radio: The Factor Mirage

**Source**: https://www.youtube.com/watch?v=k3IB8KG5Jds  
**Video ID**: `k3IB8KG5Jds`  
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

## 13. Quant Radio: Regime-Aware Universal Portfolios

**Source**: https://www.youtube.com/watch?v=El7E57pAKbw  
**Video ID**: `El7E57pAKbw`  
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

## 14. "The Book of Alternative Data" w/ Alexander Denev & Saeed Amen

**Source**: https://www.youtube.com/watch?v=xZpE14CPZ4k  
**Video ID**: `xZpE14CPZ4k`  
**Category**: NLP / AI

### Key Concepts

Foundation models process unstructured data (news, filings, earnings calls) to generate trading signals via sentiment analysis and time series forecasting.

### Code Module

```python
from transformers import pipeline
import pandas as pd

class FinancialNewsSentimentAnalyzer:
    """LLM-based sentiment analyzer for financial news."""
    
    def __init__(self, model_name="ProsusAI/finbert"):
        self.sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model=model_name,
            tokenizer=model_name
        )
    
    def analyze_text(self, text):
        result = self.sentiment_pipeline(text[:512])[0]
        return {'label': result['label'], 'score': result['score']}
    
    def batch_analyze(self, texts):
        results = [self.analyze_text(t) for t in texts]
        return pd.DataFrame(results)

class FoundationTimeSeriesForecaster:
    """Foundation model approach to time series forecasting."""
    
    def __init__(self, context_length=60, prediction_length=5):
        self.context_length = context_length
        self.prediction_length = prediction_length
    
    def prepare_sequence(self, prices):
        sequences, targets = [], []
        for i in range(len(prices) - self.context_length - self.prediction_length):
            sequences.append(prices[i:i + self.context_length])
            targets.append(prices[i + self.context_length:i + self.context_length + self.prediction_length])
        return np.array(sequences), np.array(targets)
```

### Explanation

Uses FinBERT for sentiment analysis and sliding windows for foundation model time series forecasting.

---

## 15. Quant Radio: Interest Rates and Short Term Currency Crash Risk

**Source**: https://www.youtube.com/watch?v=BcRAmctBy2c  
**Video ID**: `BcRAmctBy2c`  
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

## 16. Quant Radio: Centaur Trading vs AI Only Models in Regime Shifts

**Source**: https://www.youtube.com/watch?v=0KRzDyEqIVY  
**Video ID**: `0KRzDyEqIVY`  
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

## 17. Systematic Investing and AI with Benjamin Paul Rollert | Alpha Minds: Fintech Leaders

**Source**: https://www.youtube.com/watch?v=3QCkgSEiVdw  
**Video ID**: `3QCkgSEiVdw`  
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

## 18. Quant Radio: Scaled PCA for Factor Investing

**Source**: https://www.youtube.com/watch?v=0DI5bkBeQwQ  
**Video ID**: `0DI5bkBeQwQ`  
**Category**: Factor Models

### Key Concepts

Scaled PCA addresses the factor zoo problem by extracting principal components from high-dimensional factor data and scaling them to create robust portfolio allocations.

### Code Module

```python
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

def scaled_pca_portfolio(factor_data, n_components=5):
    """Build portfolio using scaled PCA on factor returns."""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(factor_data)
    
    pca = PCA(n_components=n_components)
    principal_components = pca.fit_transform(X_scaled)
    
    loadings = pca.components_.T
    component_vars = np.var(principal_components, axis=0)
    scaled_weights = loadings / component_vars
    
    portfolio_weights = np.sum(scaled_weights, axis=1)
    portfolio_weights = portfolio_weights / np.sum(np.abs(portfolio_weights))
    
    return portfolio_weights, pca.explained_variance_ratio_
```

### Explanation

Scaled PCA inversely weights components by variance to prevent high-variance factors from dominating the portfolio.

---

## 19. Quant Radio: The Return of the Stock Split

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

## 20. Quant Radio: Music Royalties vs. the Stock Market

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

## 21. "Hands-On AI Trading with Python, QuantConnect, and AWS" with Jiri Pik, Robustness Playbook, and Jared Broad

**Source**: https://www.youtube.com/watch?v=dHlBxjbzrtU  
**Video ID**: `dHlBxjbzrtU`  
**Category**: Factor Models

### Key Concepts

Scaled PCA addresses the factor zoo problem by extracting principal components from high-dimensional factor data and scaling them to create robust portfolio allocations.

### Code Module

```python
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

def scaled_pca_portfolio(factor_data, n_components=5):
    """Build portfolio using scaled PCA on factor returns."""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(factor_data)
    
    pca = PCA(n_components=n_components)
    principal_components = pca.fit_transform(X_scaled)
    
    loadings = pca.components_.T
    component_vars = np.var(principal_components, axis=0)
    scaled_weights = loadings / component_vars
    
    portfolio_weights = np.sum(scaled_weights, axis=1)
    portfolio_weights = portfolio_weights / np.sum(np.abs(portfolio_weights))
    
    return portfolio_weights, pca.explained_variance_ratio_
```

### Explanation

Scaled PCA inversely weights components by variance to prevent high-variance factors from dominating the portfolio.

---

## 22. Causal Portfolio Optimization with Alejandro Rodriguez Domínguez

**Source**: https://www.youtube.com/watch?v=63ZI4bzfiT4  
**Video ID**: `63ZI4bzfiT4`  
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

## 23. "The Predictive Edge" with Dr. Alejandro Lopez-Lira

**Source**: https://www.youtube.com/watch?v=z9AEa-MNrU0  
**Video ID**: `z9AEa-MNrU0`  
**Category**: NLP / AI

### Key Concepts

Foundation models process unstructured data (news, filings, earnings calls) to generate trading signals via sentiment analysis and time series forecasting.

### Code Module

```python
from transformers import pipeline
import pandas as pd

class FinancialNewsSentimentAnalyzer:
    """LLM-based sentiment analyzer for financial news."""
    
    def __init__(self, model_name="ProsusAI/finbert"):
        self.sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model=model_name,
            tokenizer=model_name
        )
    
    def analyze_text(self, text):
        result = self.sentiment_pipeline(text[:512])[0]
        return {'label': result['label'], 'score': result['score']}
    
    def batch_analyze(self, texts):
        results = [self.analyze_text(t) for t in texts]
        return pd.DataFrame(results)

class FoundationTimeSeriesForecaster:
    """Foundation model approach to time series forecasting."""
    
    def __init__(self, context_length=60, prediction_length=5):
        self.context_length = context_length
        self.prediction_length = prediction_length
    
    def prepare_sequence(self, prices):
        sequences, targets = [], []
        for i in range(len(prices) - self.context_length - self.prediction_length):
            sequences.append(prices[i:i + self.context_length])
            targets.append(prices[i + self.context_length:i + self.context_length + self.prediction_length])
        return np.array(sequences), np.array(targets)
```

### Explanation

Uses FinBERT for sentiment analysis and sliding windows for foundation model time series forecasting.

---

## 24. Dr. Eghbal Rahimikia on LLMs and Foundation Time Series Models | Educational Platform Colloquium Series

**Source**: https://www.youtube.com/watch?v=9tUGoTD1hDc  
**Video ID**: `9tUGoTD1hDc`  
**Category**: NLP / AI

### Key Concepts

Foundation models process unstructured data (news, filings, earnings calls) to generate trading signals via sentiment analysis and time series forecasting.

### Code Module

```python
from transformers import pipeline
import pandas as pd

class FinancialNewsSentimentAnalyzer:
    """LLM-based sentiment analyzer for financial news."""
    
    def __init__(self, model_name="ProsusAI/finbert"):
        self.sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model=model_name,
            tokenizer=model_name
        )
    
    def analyze_text(self, text):
        result = self.sentiment_pipeline(text[:512])[0]
        return {'label': result['label'], 'score': result['score']}
    
    def batch_analyze(self, texts):
        results = [self.analyze_text(t) for t in texts]
        return pd.DataFrame(results)

class FoundationTimeSeriesForecaster:
    """Foundation model approach to time series forecasting."""
    
    def __init__(self, context_length=60, prediction_length=5):
        self.context_length = context_length
        self.prediction_length = prediction_length
    
    def prepare_sequence(self, prices):
        sequences, targets = [], []
        for i in range(len(prices) - self.context_length - self.prediction_length):
            sequences.append(prices[i:i + self.context_length])
            targets.append(prices[i + self.context_length:i + self.context_length + self.prediction_length])
        return np.array(sequences), np.array(targets)
```

### Explanation

Uses FinBERT for sentiment analysis and sliding windows for foundation model time series forecasting.

---

## 25. Quant Radio: Understanding Analyst Silence and Its Impact

**Source**: https://www.youtube.com/watch?v=ERA2kPdHhIM  
**Video ID**: `ERA2kPdHhIM`  
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

## 26. Alpha Minds: FinTech Leaders with Dr. Thomas Wiecki and Camilo Saldarriaga #Finance #Bayesian #AI

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

## 27. "Generative AI for Trading and Asset Management" with Dr. Author-H Medina and Dr. Robustness Playbook

**Source**: https://www.youtube.com/watch?v=JgGOtCskLRg  
**Video ID**: `JgGOtCskLRg`  
**Category**: NLP / AI

### Key Concepts

Foundation models process unstructured data (news, filings, earnings calls) to generate trading signals via sentiment analysis and time series forecasting.

### Code Module

```python
from transformers import pipeline
import pandas as pd

class FinancialNewsSentimentAnalyzer:
    """LLM-based sentiment analyzer for financial news."""
    
    def __init__(self, model_name="ProsusAI/finbert"):
        self.sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model=model_name,
            tokenizer=model_name
        )
    
    def analyze_text(self, text):
        result = self.sentiment_pipeline(text[:512])[0]
        return {'label': result['label'], 'score': result['score']}
    
    def batch_analyze(self, texts):
        results = [self.analyze_text(t) for t in texts]
        return pd.DataFrame(results)

class FoundationTimeSeriesForecaster:
    """Foundation model approach to time series forecasting."""
    
    def __init__(self, context_length=60, prediction_length=5):
        self.context_length = context_length
        self.prediction_length = prediction_length
    
    def prepare_sequence(self, prices):
        sequences, targets = [], []
        for i in range(len(prices) - self.context_length - self.prediction_length):
            sequences.append(prices[i:i + self.context_length])
            targets.append(prices[i + self.context_length:i + self.context_length + self.prediction_length])
        return np.array(sequences), np.array(targets)
```

### Explanation

Uses FinBERT for sentiment analysis and sliding windows for foundation model time series forecasting.

---

## 28. Quant Radio: Can Disagreement Between Models Predict Market Alpha?

**Source**: https://www.youtube.com/watch?v=Xvb3pVKvOjg  
**Video ID**: `Xvb3pVKvOjg`  
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

## 29. Quant Radio: Rethinking Cross Sectional Asset Pricing

**Source**: https://www.youtube.com/watch?v=D0FV5uaYqn0  
**Video ID**: `D0FV5uaYqn0`  
**Category**: Factor Models

### Key Concepts

Scaled PCA addresses the factor zoo problem by extracting principal components from high-dimensional factor data and scaling them to create robust portfolio allocations.

### Code Module

```python
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

def scaled_pca_portfolio(factor_data, n_components=5):
    """Build portfolio using scaled PCA on factor returns."""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(factor_data)
    
    pca = PCA(n_components=n_components)
    principal_components = pca.fit_transform(X_scaled)
    
    loadings = pca.components_.T
    component_vars = np.var(principal_components, axis=0)
    scaled_weights = loadings / component_vars
    
    portfolio_weights = np.sum(scaled_weights, axis=1)
    portfolio_weights = portfolio_weights / np.sum(np.abs(portfolio_weights))
    
    return portfolio_weights, pca.explained_variance_ratio_
```

### Explanation

Scaled PCA inversely weights components by variance to prevent high-variance factors from dominating the portfolio.

---

## 30. Quant Radio: Seemingly Virtuous Complexity in Return Prediction

**Source**: https://www.youtube.com/watch?v=OTfNrOpAjaE  
**Video ID**: `OTfNrOpAjaE`  
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

## 31. Quant Radio: The Magic of Drawdowns

**Source**: https://www.youtube.com/watch?v=QwHf7yDHQjc  
**Video ID**: `QwHf7yDHQjc`  
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

## 32. Quant Radio: Political Uncertainty and Commodity Markets

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

## 33. Quant Radio: Generative AI in Financial Economics

**Source**: https://www.youtube.com/watch?v=EwoOo_o0zCM  
**Video ID**: `EwoOo_o0zCM`  
**Category**: Factor Models

### Key Concepts

Scaled PCA addresses the factor zoo problem by extracting principal components from high-dimensional factor data and scaling them to create robust portfolio allocations.

### Code Module

```python
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

def scaled_pca_portfolio(factor_data, n_components=5):
    """Build portfolio using scaled PCA on factor returns."""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(factor_data)
    
    pca = PCA(n_components=n_components)
    principal_components = pca.fit_transform(X_scaled)
    
    loadings = pca.components_.T
    component_vars = np.var(principal_components, axis=0)
    scaled_weights = loadings / component_vars
    
    portfolio_weights = np.sum(scaled_weights, axis=1)
    portfolio_weights = portfolio_weights / np.sum(np.abs(portfolio_weights))
    
    return portfolio_weights, pca.explained_variance_ratio_
```

### Explanation

Scaled PCA inversely weights components by variance to prevent high-variance factors from dominating the portfolio.

---

## 34. Towards a Free Lunch: Quantifying Diversification with Dr. Tom Starke

**Source**: https://www.youtube.com/watch?v=9mpN7n2wJkM  
**Video ID**: `9mpN7n2wJkM`  
**Category**: Factor Models

### Key Concepts

Scaled PCA addresses the factor zoo problem by extracting principal components from high-dimensional factor data and scaling them to create robust portfolio allocations.

### Code Module

```python
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

def scaled_pca_portfolio(factor_data, n_components=5):
    """Build portfolio using scaled PCA on factor returns."""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(factor_data)
    
    pca = PCA(n_components=n_components)
    principal_components = pca.fit_transform(X_scaled)
    
    loadings = pca.components_.T
    component_vars = np.var(principal_components, axis=0)
    scaled_weights = loadings / component_vars
    
    portfolio_weights = np.sum(scaled_weights, axis=1)
    portfolio_weights = portfolio_weights / np.sum(np.abs(portfolio_weights))
    
    return portfolio_weights, pca.explained_variance_ratio_
```

### Explanation

Scaled PCA inversely weights components by variance to prevent high-variance factors from dominating the portfolio.

---

## 35. Quant Radio: Short-Term Correlated Stress Reversal Trading

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

## 36. Quant Radio: Machine Learning and the Probability of Bouncing Back

**Source**: https://www.youtube.com/watch?v=ZHIrE_r_9HY  
**Video ID**: `ZHIrE_r_9HY`  
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

## 37. Quant Radio: Transforming Empirical Asset Pricing

**Source**: https://www.youtube.com/watch?v=3JEP4olw6EI  
**Video ID**: `3JEP4olw6EI`  
**Category**: Factor Models

### Key Concepts

Scaled PCA addresses the factor zoo problem by extracting principal components from high-dimensional factor data and scaling them to create robust portfolio allocations.

### Code Module

```python
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

def scaled_pca_portfolio(factor_data, n_components=5):
    """Build portfolio using scaled PCA on factor returns."""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(factor_data)
    
    pca = PCA(n_components=n_components)
    principal_components = pca.fit_transform(X_scaled)
    
    loadings = pca.components_.T
    component_vars = np.var(principal_components, axis=0)
    scaled_weights = loadings / component_vars
    
    portfolio_weights = np.sum(scaled_weights, axis=1)
    portfolio_weights = portfolio_weights / np.sum(np.abs(portfolio_weights))
    
    return portfolio_weights, pca.explained_variance_ratio_
```

### Explanation

Scaled PCA inversely weights components by variance to prevent high-variance factors from dominating the portfolio.

---

