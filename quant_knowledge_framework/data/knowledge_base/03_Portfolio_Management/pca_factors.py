"""
Scaled PCA for Factor Investing
============================================================

Source: Educational Platform YouTube Channel
Category: Factor Models

This module implements quantitative finance concepts discussed in the Educational Platform video lectures.

Concepts:
Scaled PCA addresses the factor zoo problem by extracting principal components from high-dimensional factor data and scaling them to create robust portfolio allocations.
"""

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
