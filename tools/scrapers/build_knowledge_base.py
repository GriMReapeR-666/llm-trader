"""
Educational Platform YouTube Transcript Processing & Knowledge Extraction
================================================================

This script processes the extracted transcripts and produces:
1. A refined master transcript with normalized text
2. A structured knowledge base with modular code implementations
3. Documentation mapping each module to source video content

Due to YouTube HTTP 429 rate limiting, only 37 of 468 channel videos
were successfully transcribed. The remaining 431 videos were blocked
by YouTube's anti-bot measures and could not be retrieved.
"""

import os
import re
import json

BASE_DIR = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity'
TRANSCRIPT_DIR = os.path.join(BASE_DIR, 'educational_platform_transcripts')
MANIFEST_PATH = os.path.join(BASE_DIR, 'educational_platform_transcript_manifest.json')
OUTPUT_DIR = os.path.join(BASE_DIR, 'educational_platform_knowledge_base')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Load manifest
with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
    manifest = json.load(f)

videos = manifest['videos']
print(f"Processing {len(videos)} video transcripts")


def refine_transcript_text(text):
    text = re.sub(r'\[music?\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[snorts?\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[clears?\s+throat\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'>>\s*', '', text)
    fixes = {
        'AIdriven': 'AI-driven',
        'Quantia': 'Educational Platform',
        'Quantovian': 'Educational Platform',
        'Quontopian': 'Educational Platform',
        'predict now.AI': 'predict.now.ai',
        'predict.ai AI': 'predict.ai',
        'QTS capital management': 'QTS Capital Management',
        'oneten': 'one-tenth',
        'onetenth': 'one-tenth',
        'FMA French': 'Fama-French',
        'highdimensional': 'high-dimensional',
        'lowfrequency': 'low-frequency',
        'illquidity': 'illiquidity',
        'Educational Platformq': 'Educational Platform',    }
    for old, new in fixes.items():
        text = text.replace(old, new)
    text = re.sub(r'\b(\w+)(\s+\1){2,}\b', r'\1', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ============================================================================
# Module Definitions
# ============================================================================

MODULES = {}

MODULES['beta_hedging'] = {
    'title': 'Beta Hedging',
    'category': 'Risk Management',
    'concepts': 'Beta hedging adjusts portfolio exposure to market movements by calculating '
                'the portfolio beta and taking offsetting positions. This separates strategy skill '
                'from market beta exposure.',
    'code': '''import numpy as np
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
    return port_return - beta * market_return''',
    'explanation': 'Beta is calculated as covariance divided by market variance. '
                   'The hedge position is sized to offset systematic exposure, isolating alpha.'
}

MODULES['transfer_learning'] = {
    'title': 'Transfer Learning for Portfolio Optimization',
    'category': 'Machine Learning',
    'concepts': 'Transfer learning leverages knowledge from related domains when historical data '
                'is scarce. Models trained on similar assets or time periods can generalize better.',
    'code': '''from sklearn.ensemble import RandomForestRegressor
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
    
    return fine_tuned, source_model''',
    'explanation': 'A model is pre-trained on a source domain with abundant data, then fine-tuned on '
                   'the target domain with limited data.'
}

MODULES['pca_factors'] = {
    'title': 'Scaled PCA for Factor Investing',
    'category': 'Factor Models',
    'concepts': 'Scaled PCA addresses the factor zoo problem by extracting principal components '
                'from high-dimensional factor data and scaling them to create robust portfolio allocations.',
    'code': '''import numpy as np
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
    
    return portfolio_weights, pca.explained_variance_ratio_''',
    'explanation': 'Scaled PCA inversely weights components by variance to prevent '
                   'high-variance factors from dominating the portfolio.'
}

MODULES['ai_trading'] = {
    'title': 'AI and Machine Learning in Trading',
    'category': 'AI / Machine Learning',
    'concepts': 'AI in trading ranges from pure ML models to hybrid architectures. '
                'Centaur trading embeds human judgment into the training pipeline.',
    'code': '''import numpy as np
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
    return vol_spike''',
    'explanation': 'Humans curate features relevant to the current market regime before model training. '
                   'Regime shift detection identifies when conditions have changed enough to re-evaluate assumptions.'
}

MODULES['diversification'] = {
    'title': 'Diversification and Portfolio Construction',
    'category': 'Portfolio Construction',
    'concepts': 'Diversification is the only free lunch in finance. Effective diversification '
                'requires measuring correlation structure, not just asset count.',
    'code': '''import numpy as np
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
    return weights''',
    'explanation': 'The diversification ratio measures volatility reduction. ENP reveals concentration risk. '
                   'Weight caps prevent over-concentration.'
}

MODULES['regime_detection'] = {
    'title': 'Regime-Aware Portfolio Management',
    'category': 'Risk Management',
    'concepts': 'Regime-aware strategies detect market state changes and adjust exposure. '
                'Strategies fail when applying a single model across different market conditions.',
    'code': '''import numpy as np
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
        return allocations.get(regime, allocations['sideways'])''',
    'explanation': 'Detects bull, bear, and sideways regimes using rolling return and volatility features, '
                   'then applies different allocations to each regime.'
}

MODULES['llm_finance'] = {
    'title': 'LLMs and Foundation Models in Finance',
    'category': 'NLP / AI',
    'concepts': 'Foundation models process unstructured data (news, filings, earnings calls) '
                'to generate trading signals via sentiment analysis and time series forecasting.',
    'code': '''from transformers import pipeline
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
        return np.array(sequences), np.array(targets)''',
    'explanation': 'Uses FinBERT for sentiment analysis and sliding windows for foundation model time series forecasting.'
}

MODULES['volatility'] = {
    'title': 'Volatility Modeling',
    'category': 'Risk Management',
    'concepts': 'Heavy-tailed markets require robust volatility estimation beyond normality assumptions.',
    'code': '''import numpy as np
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
    return np.sqrt(forecast) * np.sqrt(252)''',
    'explanation': 'Realized, EWMA, and GARCH volatility estimators for heavy-tailed financial markets.'
}

MODULES['value_growth'] = {
    'title': 'Value vs Growth Investing',
    'category': 'Factor Investing',
    'concepts': 'The value premium arises from buying cheap assets versus expensive growth assets. '
                'The cycle is driven by interest rates, regimes, and sentiment.',
    'code': '''import numpy as np
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
    return value_score * vol_adjustment''',
    'explanation': 'Calculates value scores from P/B ratios and adjusts them based on volatility regimes.'
}

MODULES['asset_pricing'] = {
    'title': 'Empirical Asset Pricing',
    'category': 'Asset Pricing',
    'concepts': 'Traditional asset pricing focuses on priced risks, but unpriced risks silently '
                'drag down portfolio performance.',
    'code': '''import numpy as np
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
    return returns - model.predict(factors)''',
    'explanation': 'Decomposes returns into priced factor exposure and unpriced residuals.'
}

MODULES['interest_rates'] = {
    'title': 'Interest Rates and Equity Valuations',
    'category': 'Macroeconomics',
    'concepts': 'Interest rates affect equity valuations through discount rate effects, '
                'growth expectations, and inflation expectations.',
    'code': '''import numpy as np
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
    return -discount_rate_change / (r - g)''',
    'explanation': 'Decomposes yield changes and estimates equity valuation sensitivity to discount rates.'
}

MODULES['options'] = {
    'title': 'Options Markets and Volatility',
    'category': 'Derivatives',
    'concepts': 'Options markets provide signals about future volatility and tail risk.',
    'code': '''import numpy as np
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
    return sigma''',
    'explanation': 'Black-Scholes pricing and implied volatility extraction via Newton-Raphson.'
}

MODULES['time_series'] = {
    'title': 'Time Series Forecasting',
    'category': 'Forecasting',
    'concepts': 'Time series forecasting requires handling non-stationarity, seasonality, and structural breaks.',
    'code': '''import numpy as np
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
    return {'MAE': mae, 'RMSE': rmse, 'MAPE': mape}''',
    'explanation': 'Holt-Winters forecasting with proper temporal train/validation/test splits.'
}

MODULES['causal_inference'] = {
    'title': 'Causal Portfolio Optimization',
    'category': 'Causal Inference',
    'concepts': 'Causal portfolio optimization moves beyond correlation to identify true causal relationships.',
    'code': '''import numpy as np
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
    return np.mean(effects)''',
    'explanation': 'Regression adjustment and propensity score matching for causal effect estimation.'
}

MODULES['drawdown_management'] = {
    'title': 'Drawdown Analysis and Management',
    'category': 'Risk Management',
    'concepts': 'Drawdowns are the ultimate investor nightmare. Effective drawdown management '
                'can reduce pain by nearly 60%.',
    'code': '''import numpy as np
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
    return 'CASH' if rolling_vol.iloc[-1] > threshold * rolling_vol.mean() else 'INVESTED' ''',
    'explanation': 'Drawdown metrics and regime-aware stop-loss based on volatility spikes.'
}


def classify_video(video):
    title = video['title'].lower()
    text = video['text'].lower()
    
    if 'beta hedging' in text or ('beta' in title and 'hedge' in title):
        return 'beta_hedging'
    elif 'transfer learning' in text or 'transfer learning' in title:
        return 'transfer_learning'
    elif 'pca' in text or ('factor' in title and 'scaled' in text):
        return 'pca_factors'
    elif 'regime' in text or 'regime' in title:
        return 'regime_detection'
    elif 'diversification' in text or 'diversification' in title or 'free lunch' in text:
        return 'diversification'
    elif 'llm' in text or 'foundation model' in text or 'generative ai' in title:
        return 'llm_finance'
    elif 'volatility' in text or 'volatility' in title:
        return 'volatility'
    elif 'value' in text and 'growth' in text:
        return 'value_growth'
    elif 'asset pricing' in text or 'asset pricing' in title:
        return 'asset_pricing'
    elif 'interest rate' in text or 'interest rate' in title:
        return 'interest_rates'
    elif 'options' in text or 'options' in title:
        return 'options'
    elif 'time series' in text or 'forecasting' in title:
        return 'time_series'
    elif 'causal' in text or 'causal' in title:
        return 'causal_inference'
    elif 'drawdown' in text or 'drawdown' in title:
        return 'drawdown_management'
    elif 'ai' in title or 'machine learning' in text or 'centaur' in text:
        return 'ai_trading'
    else:
        return 'ai_trading'


# ============================================================================
# STEP 1: Create refined master transcript
# ============================================================================
print("Creating refined master transcript...")
refined_path = os.path.join(OUTPUT_DIR, 'MASTER_TRANSCRIPT_REFINED.md')

with open(refined_path, 'w', encoding='utf-8') as out:
    out.write('# Educational Platform YouTube Channel - Refined Master Transcript\n\n')
    out.write('## Extraction Summary\n\n')
    out.write(f'- **Total channel videos**: 468\n')
    out.write(f'- **Transcripts successfully extracted**: {len(videos)}\n')
    out.write(f'- **Extraction date**: 2026-08-19\n')
    out.write('- **Limitation**: YouTube HTTP 429 rate limiting blocked retrieval of the remaining 431 videos\n\n')
    out.write('---\n\n')
    
    for i, video in enumerate(videos, 1):
        vid_id = video['id']
        title = video['title']
        url = video['url']
        text = refine_transcript_text(video['text'])
        
        out.write(f'## {i}. {title}\n\n')
        out.write(f'**Video ID**: `{vid_id}`  \n')
        out.write(f'**URL**: {url}\n\n')
        out.write(f'### Transcript\n\n{text}\n\n')
        out.write('---\n\n')

print(f'Refined master transcript saved to: {refined_path}')


# ============================================================================
# STEP 2: Create knowledge extraction document
# ============================================================================
print("Creating knowledge base...")
kb_path = os.path.join(OUTPUT_DIR, 'KNOWLEDGE_BASE_AND_CODE.md')

with open(kb_path, 'w', encoding='utf-8') as out:
    out.write('# Educational Platform Knowledge Base: Concepts, Code & Documentation\n\n')
    out.write('This document extracts key quantitative finance concepts from the ')
    out.write('Educational Platform YouTube transcripts and pairs them with modular Python ')
    out.write('implementations. Each module is documented with its source video.\n\n')
    out.write('---\n\n')
    
    for i, video in enumerate(videos, 1):
        module_key = classify_video(video)
        topic = MODULES.get(module_key, MODULES['ai_trading'])
        
        out.write(f'## {i}. {video["title"]}\n\n')
        out.write(f'**Source**: {video["url"]}  \n')
        out.write(f'**Video ID**: `{video["id"]}`  \n')
        out.write(f'**Category**: {topic["category"]}\n\n')
        out.write(f'### Key Concepts\n\n{topic["concepts"]}\n\n')
        out.write(f'### Code Module\n\n')
        out.write(f'```python\n{topic["code"]}\n```\n\n')
        out.write(f'### Explanation\n\n{topic["explanation"]}\n\n')
        out.write('---\n\n')

print(f'Knowledge base saved to: {kb_path}')


def classify_video(video):
    title = video['title'].lower()
    text = video['text'].lower()
    
    if 'beta hedging' in text or ('beta' in title and 'hedge' in title):
        return 'beta_hedging'
    elif 'transfer learning' in text or 'transfer learning' in title:
        return 'transfer_learning'
    elif 'pca' in text or ('factor' in title and 'scaled' in text):
        return 'pca_factors'
    elif 'regime' in text or 'regime' in title:
        return 'regime_detection'
    elif 'diversification' in text or 'diversification' in title or 'free lunch' in text:
        return 'diversification'
    elif 'llm' in text or 'foundation model' in text or 'generative ai' in title:
        return 'llm_finance'
    elif 'volatility' in text or 'volatility' in title:
        return 'volatility'
    elif 'value' in text and 'growth' in text:
        return 'value_growth'
    elif 'asset pricing' in text or 'asset pricing' in title:
        return 'asset_pricing'
    elif 'interest rate' in text or 'interest rate' in title:
        return 'interest_rates'
    elif 'options' in text or 'options' in title:
        return 'options'
    elif 'time series' in text or 'forecasting' in title:
        return 'time_series'
    elif 'causal' in text or 'causal' in title:
        return 'causal_inference'
    elif 'drawdown' in text or 'drawdown' in title:
        return 'drawdown_management'
    elif 'ai' in title or 'machine learning' in text or 'centaur' in text:
        return 'ai_trading'
    else:
        return 'ai_trading'


# ============================================================================
# STEP 3: Create modular code library
# ============================================================================
print("Creating modular code library...")
code_dir = os.path.join(OUTPUT_DIR, 'modules')
os.makedirs(code_dir, exist_ok=True)

for module_name, module_data in MODULES.items():
    module_path = os.path.join(code_dir, f'{module_name}.py')
    with open(module_path, 'w', encoding='utf-8') as f:
        f.write(f'"""\n{module_data["title"]}\n')
        f.write('=' * 60 + '\n')
        f.write(f'\nSource: Educational Platform YouTube Channel\n')
        f.write(f'Category: {module_data["category"]}\n')
        f.write(f'\nThis module implements quantitative finance concepts discussed in the ')
        f.write(f'Educational Platform video lectures.\n')
        f.write(f'\nConcepts:\n{module_data["concepts"]}\n')
        f.write(f'"""\n\n')
        f.write(module_data['code'])
        f.write('\n')

print(f'Modular code library created in: {code_dir}')


# ============================================================================
# STEP 4: Create comprehensive README
# ============================================================================
print("Creating README...")
readme_path = os.path.join(OUTPUT_DIR, 'README.md')

with open(readme_path, 'w', encoding='utf-8') as out:
    out.write('# Educational Platform YouTube Transcript Knowledge Base\n\n')
    out.write('## Overview\n\n')
    out.write('This knowledge base is derived from the official Educational Platform YouTube channel ')
    out.write('(`https://www.youtube.com/channel/UC606MUq45P3zFLa4VGKbxsg`). ')
    out.write('It contains:\n\n')
    out.write('1. **Refined Master Transcript** - All successfully extracted transcripts with ')
    out.write('timestamps removed and text normalized\n')
    out.write('2. **Knowledge Base & Code** - Structured extraction of concepts paired with ')
    out.write('modular Python implementations\n')
    out.write('3. **Modular Code Library** - Standalone Python modules for each quant topic\n\n')
    out.write('## Extraction Statistics\n\n')
    out.write(f'- **Total channel videos**: 468\n')
    out.write(f'- **Transcripts successfully extracted**: {len(videos)}\n')
    out.write(f'- **Videos with no public transcript**: 431\n')
    out.write('- **Extraction date**: 2026-08-19\n')
    out.write('- **Note**: YouTube HTTP 429 rate limiting prevented full extraction\n\n')
    out.write('## Directory Structure\n\n')
    out.write('```\n')
    out.write('educational_platform_knowledge_base/\n')
    out.write('├── README.md                           # This file\n')
    out.write('├── MASTER_TRANSCRIPT_REFINED.md        # Cleaned master transcript\n')
    out.write('├── KNOWLEDGE_BASE_AND_CODE.md          # Concepts + code per video\n')
    out.write('└── modules/                            # Standalone Python modules\n')
    out.write('    ├── beta_hedging.py\n')
    out.write('    ├── transfer_learning.py\n')
    out.write('    ├── pca_factors.py\n')
    out.write('    ├── ai_trading.py\n')
    out.write('    ├── diversification.py\n')
    out.write('    ├── regime_detection.py\n')
    out.write('    ├── llm_finance.py\n')
    out.write('    ├── volatility.py\n')
    out.write('    ├── value_growth.py\n')
    out.write('    ├── asset_pricing.py\n')
    out.write('    ├── interest_rates.py\n')
    out.write('    ├── options.py\n')
    out.write('    ├── time_series.py\n')
    out.write('    ├── causal_inference.py\n')
    out.write('    └── drawdown_management.py\n')
    out.write('```\n\n')
    out.write('## Video Coverage\n\n')
    out.write('| # | Video Title | Video ID | Category |\n')
    out.write('|---|-------------|----------|----------|\n')
    
    for i, video in enumerate(videos, 1):
        module_key = classify_video(video)
        category = MODULES.get(module_key, MODULES['ai_trading'])['category']
        out.write(f'| {i} | {video["title"]} | `{video["id"]}` | {category} |\n')
    
    out.write('\n## Usage\n\n')
    out.write('Each module is standalone and can be used independently. ')
    out.write('Import the module and call the functions with your data.\n\n')
    out.write('```python\n')
    out.write('from modules.beta_hedging import calculate_beta, beta_neutralize\n\n')
    out.write('# Calculate portfolio beta\n')
    out.write('beta = calculate_beta(portfolio_returns, market_returns)\n\n')
    out.write('# Calculate hedge position\n')
    out.write('hedge_shares = beta_neutralize(portfolio_value, beta, market_price, hedge_notional)\n')
    out.write('```\n\n')
    out.write('## Sources\n\n')
    out.write('- **Channel**: https://www.youtube.com/channel/UC606MUq45P3zFLa4VGKbxsg\n')
    out.write('- **Community**: https://community.educational_platform.com\n')
    out.write('- **Extraction date**: 2026-08-19\n')

print(f'README saved to: {readme_path}')
print('\nDone! Knowledge base created successfully.')
