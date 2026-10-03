"""
Interest Rates and Equity Valuations
============================================================

Source: Educational Platform YouTube Channel
Category: Macroeconomics

This module implements quantitative finance concepts discussed in the Educational Platform video lectures.

Concepts:
Interest rates affect equity valuations through discount rate effects, growth expectations, and inflation expectations.
"""

import numpy as np
import pandas as pd

def decompose_yield_change(yield_change, inflation_expectations, growth_expectations):
    real_rate_change = growth_expectations
    inflation_change = inflation_expectations
    term_premium = yield_change - real_rate_change - inflation_change
    return {'real_rate': real_rate_change, 'inflation': inflation_change, 'term_premium': term_premium}

def stock_bond_correlation(returns_stocks, returns_bonds, window=60):
    return returns_stocks.rolling(window=window).corr(returns_bonds)

def equity_valuation_impact(discount_rate_change, cash_flow_growth, horizon=10):
    r, g = 0.08, 0.03
    if discount_rate_change >= r - g:
        return -1.0
    return -discount_rate_change / (r - g)
