"""
Empirical Asset Pricing
============================================================

Source: Educational Platform YouTube Channel
Category: Asset Pricing

This module implements quantitative finance concepts discussed in the Educational Platform video lectures.

Concepts:
Traditional asset pricing focuses on priced risks, but unpriced risks silently drag down portfolio performance.
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

def calculate_alpha_beta(asset_returns, factor_returns):
    model = LinearRegression()
    model.fit(factor_returns, asset_returns)
    return model.intercept_, model.coef_

def identify_unpriced_risk(returns, factors):
    model = LinearRegression()
    model.fit(factors, returns)
    residuals = returns - model.predict(factors)
    return np.std(residuals), residuals

def factor_neutralize(returns, factors):
    model = LinearRegression()
    model.fit(factors, returns)
    return returns - model.predict(factors)
