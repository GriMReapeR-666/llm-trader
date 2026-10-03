"""
Time Series Forecasting
============================================================

Source: Educational Platform YouTube Channel
Category: Forecasting

This module implements quantitative finance concepts discussed in the Educational Platform video lectures.

Concepts:
Time series forecasting requires handling non-stationarity, seasonality, and structural breaks.
"""

import numpy as np
import pandas as pd

def holt_winters_forecast(series, seasonal_periods=12, forecast_horizon=5):
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    model = ExponentialSmoothing(series, seasonal_periods=seasonal_periods, trend='add', seasonal='add')
    fit = model.fit()
    return fit.forecast(forecast_horizon), fit

def train_val_test_split(data, train_frac=0.7, val_frac=0.15):
    n = len(data)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    return data.iloc[:train_end], data.iloc[train_end:val_end], data.iloc[val_end:]

def calculate_forecast_metrics(actual, predicted):
    mae = np.mean(np.abs(actual - predicted))
    rmse = np.sqrt(np.mean((actual - predicted) ** 2))
    mape = np.mean(np.abs((actual - predicted) / actual)) * 100
    return {'MAE': mae, 'RMSE': rmse, 'MAPE': mape}
