# Quant Radio Transforming Empirical Asset Pricing

Topic: 03_Portfolio_Management
Confidence: 0.40
Keywords: portfolio, allocation

---

## Quant Radio: Transforming Empirical Asset Pricing

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