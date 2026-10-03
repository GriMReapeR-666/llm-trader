"""
Options Markets and Volatility
============================================================

Source: Educational Platform YouTube Channel
Category: Derivatives

This module implements quantitative finance concepts discussed in the Educational Platform video lectures.

Concepts:
Options markets provide signals about future volatility and tail risk.
"""

import numpy as np
from scipy.stats import norm

def black_scholes_call(S, K, T, r, sigma):
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)

def implied_volatility(market_price, S, K, T, r, tol=1e-6, max_iter=100):
    sigma = 0.2
    for _ in range(max_iter):
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        vega = S * norm.pdf(d1) * np.sqrt(T)
        price = black_scholes_call(S, K, T, r, sigma)
        diff = market_price - price
        if abs(diff) < tol:
            return sigma
        sigma += diff / vega
        sigma = max(sigma, 0.01)
    return sigma
