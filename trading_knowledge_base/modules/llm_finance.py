"""
LLMs and Foundation Models in Finance
============================================================

Source: Educational Platform YouTube Channel
Category: NLP / AI

This module implements quantitative finance concepts discussed in the Educational Platform video lectures.

Concepts:
Foundation models process unstructured data (news, filings, earnings calls) to generate trading signals via sentiment analysis and time series forecasting.
"""

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
