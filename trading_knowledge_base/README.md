# Educational Platform YouTube Transcript Knowledge Base

## Overview

This knowledge base is derived from the official Educational Platform YouTube channel (`https://www.youtube.com/channel/UC606MUq45P3zFLa4VGKbxsg`). It contains:

1. **Refined Master Transcript** - All successfully extracted transcripts with timestamps removed and text normalized
2. **Knowledge Base & Code** - Structured extraction of concepts paired with modular Python implementations
3. **Modular Code Library** - Standalone Python modules for each quant topic

## Extraction Statistics

- **Total channel videos**: 468
- **Transcripts successfully extracted**: 37
- **Videos with no public transcript**: 431
- **Extraction date**: 2026-08-19
- **Note**: YouTube HTTP 429 rate limiting prevented full extraction

## Directory Structure

```
educational_platform_knowledge_base/
├── README.md                           # This file
├── MASTER_TRANSCRIPT_REFINED.md        # Cleaned master transcript
├── KNOWLEDGE_BASE_AND_CODE.md          # Concepts + code per video
└── modules/                            # Standalone Python modules
    ├── beta_hedging.py
    ├── transfer_learning.py
    ├── pca_factors.py
    ├── ai_trading.py
    ├── diversification.py
    ├── regime_detection.py
    ├── llm_finance.py
    ├── volatility.py
    ├── value_growth.py
    ├── asset_pricing.py
    ├── interest_rates.py
    ├── options.py
    ├── time_series.py
    ├── causal_inference.py
    └── drawdown_management.py
```

## Video Coverage

| # | Video Title | Video ID | Category |
|---|-------------|----------|----------|
| 1 | Tom Sosnoff on Options Trading, Entrepreneurship, AI, & Risk Management | AlphaMinds Fintech Leaders | `cG8hGjwPang` | NLP / AI |
| 2 | Quant Radio: Measuring Volatility in Heavy Tailed Markets | `fiWX7xpRPmY` | Portfolio Construction |
| 3 | Quant Radio: Transfer Learning for Portfolio Optimization | `1fSo7xmrVb8` | Machine Learning |
| 4 | Quant Radio: Can AI Do Financial Research? | `eMImVTO8kbc` | Portfolio Construction |
| 5 | Time Series Forecasting with Marco Peixeiro | `lgtyyVC0FcQ` | Machine Learning |
| 6 | Quant Radio: The Changing Signal in Options Markets | `otI62O4Z73A` | Risk Management |
| 7 | Quant Radio: The Price Impact of Nothing | `fY_c2UPGJ6A` | Causal Inference |
| 8 | Agentic AI: Preparing Your Organisation for the Next Frontier with Nicole Koenigstein | `1B6zEzPKq9E` | NLP / AI |
| 9 | Quant Radio: The Drivers of Interest Rates and Equity Valuations | `NqgPvPma78k` | Risk Management |
| 10 | Quant Radio: The Dynamics of Value and Growth | `IQGhxlK3DI8` | Factor Investing |
| 11 | Agentic AI in Trading: The Evolution of Trading Bots with Irene Aldridge | `g1GbmCr9MSc` | Risk Management |
| 12 | Quant Radio: The Factor Mirage | `k3IB8KG5Jds` | Risk Management |
| 13 | Quant Radio: Regime-Aware Universal Portfolios | `El7E57pAKbw` | Risk Management |
| 14 | "The Book of Alternative Data" w/ Alexander Denev & Saeed Amen | `xZpE14CPZ4k` | NLP / AI |
| 15 | Quant Radio: Interest Rates and Short Term Currency Crash Risk | `BcRAmctBy2c` | Risk Management |
| 16 | Quant Radio: Centaur Trading vs AI Only Models in Regime Shifts | `0KRzDyEqIVY` | Risk Management |
| 17 | Systematic Investing and AI with Benjamin Paul Rollert | Alpha Minds: Fintech Leaders | `3QCkgSEiVdw` | Risk Management |
| 18 | Quant Radio: Scaled PCA for Factor Investing | `0DI5bkBeQwQ` | Factor Models |
| 19 | Quant Radio: The Return of the Stock Split | `xXceDil80ZU` | Factor Investing |
| 20 | Quant Radio: Music Royalties vs. the Stock Market | `IS-EDw_dLwI` | Portfolio Construction |
| 21 | "Hands-On AI Trading with Python, QuantConnect, and AWS" with Jiri Pik, Robustness Playbook, and Jared Broad | `dHlBxjbzrtU` | Factor Models |
| 22 | Causal Portfolio Optimization with Alejandro Rodriguez Domínguez | `63ZI4bzfiT4` | Portfolio Construction |
| 23 | "The Predictive Edge" with Dr. Alejandro Lopez-Lira | `z9AEa-MNrU0` | NLP / AI |
| 24 | Dr. Eghbal Rahimikia on LLMs and Foundation Time Series Models | Educational Platform Colloquium Series | `9tUGoTD1hDc` | NLP / AI |
| 25 | Quant Radio: Understanding Analyst Silence and Its Impact | `ERA2kPdHhIM` | Risk Management |
| 26 | Alpha Minds: FinTech Leaders with Dr. Thomas Wiecki and Camilo Saldarriaga #Finance #Bayesian #AI | `rfOoHkhYs_0` | Risk Management |
| 27 | "Generative AI for Trading and Asset Management" with Dr. Author-H Medina and Dr. Robustness Playbook | `JgGOtCskLRg` | NLP / AI |
| 28 | Quant Radio: Can Disagreement Between Models Predict Market Alpha? | `Xvb3pVKvOjg` | Risk Management |
| 29 | Quant Radio: Rethinking Cross Sectional Asset Pricing | `D0FV5uaYqn0` | Factor Models |
| 30 | Quant Radio: Seemingly Virtuous Complexity in Return Prediction | `OTfNrOpAjaE` | Risk Management |
| 31 | Quant Radio: The Magic of Drawdowns | `QwHf7yDHQjc` | Risk Management |
| 32 | Quant Radio: Political Uncertainty and Commodity Markets | `iitvZA0tpUU` | Risk Management |
| 33 | Quant Radio: Generative AI in Financial Economics | `EwoOo_o0zCM` | Factor Models |
| 34 | Towards a Free Lunch: Quantifying Diversification with Dr. Tom Starke | `9mpN7n2wJkM` | Factor Models |
| 35 | Quant Radio: Short-Term Correlated Stress Reversal Trading | `6I_v9CyVd30` | AI / Machine Learning |
| 36 | Quant Radio: Machine Learning and the Probability of Bouncing Back | `ZHIrE_r_9HY` | Risk Management |
| 37 | Quant Radio: Transforming Empirical Asset Pricing | `3JEP4olw6EI` | Factor Models |

## Usage

Each module is standalone and can be used independently. Import the module and call the functions with your data.

```python
from modules.beta_hedging import calculate_beta, beta_neutralize

# Calculate portfolio beta
beta = calculate_beta(portfolio_returns, market_returns)

# Calculate hedge position
hedge_shares = beta_neutralize(portfolio_value, beta, market_price, hedge_notional)
```

## Sources

- **Channel**: https://www.youtube.com/channel/UC606MUq45P3zFLa4VGKbxsg
- **Community**: https://community.educational_platform.com
- **Extraction date**: 2026-08-19
