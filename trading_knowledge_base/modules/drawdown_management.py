"""
Drawdown Analysis and Management
============================================================

Source: Educational Platform YouTube Channel
Category: Risk Management

This module implements quantitative finance concepts discussed in the Educational Platform video lectures.

Concepts:
Drawdowns are the ultimate investor nightmare. Effective drawdown management can reduce pain by nearly 60%.
"""

import numpy as np
import pandas as pd

def calculate_drawdowns(returns):
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.cummax()
    return (cumulative - running_max) / running_max

def max_drawdown(returns):
    return calculate_drawdowns(returns).min()

def calmar_ratio(returns, risk_free_rate=0.0):
    annual_return = (1 + returns).prod() ** (252 / len(returns)) - 1
    mdd = abs(max_drawdown(returns))
    return (annual_return - risk_free_rate) / mdd if mdd > 0 else np.inf

def regime_aware_stop(returns, vol_window=20, threshold=2.0):
    rolling_vol = returns.rolling(window=vol_window).std()
    return 'CASH' if rolling_vol.iloc[-1] > threshold * rolling_vol.mean() else 'INVESTED' 
