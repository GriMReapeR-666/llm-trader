"""
Beta Hedging
============================================================

Source: Educational Platform YouTube Channel
Category: Risk Management

This module implements quantitative finance concepts discussed in the Educational Platform video lectures.

Concepts:
Beta hedging adjusts portfolio exposure to market movements by calculating the portfolio beta and taking offsetting positions. This separates strategy skill from market beta exposure.
"""

import numpy as np
import pandas as pd

def calculate_beta(portfolio_returns, market_returns):
    """Calculate portfolio beta against market returns."""
    covariance = np.cov(portfolio_returns, market_returns)[0, 1]
    market_variance = np.var(market_returns, ddof=1)
    return covariance / market_variance

def beta_neutralize(portfolio_value, beta, market_price, hedge_notional):
    """Calculate hedge position to neutralize market exposure."""
    portfolio_exposure = portfolio_value * beta
    hedge_shares = -portfolio_exposure / market_price
    return hedge_shares

def hedged_portfolio_return(port_return, market_return, beta):
    """Return beta-adjusted portfolio performance."""
    return port_return - beta * market_return
