"""Lightweight context provider for trading_knowledge_base modules.

Maps market conditions to relevant knowledge base excerpts for LLM injection.
Returns short, bounded context strings — never full module dumps.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

log = logging.getLogger("KnowledgeBaseContext")

_MODULES_DIR = os.path.join(os.path.dirname(__file__), "modules")

# Module → short excerpt mapping (extracted from docstrings)
_EXCERPTS: Dict[str, str] = {
    "regime_detection": (
        "Regime-aware strategies detect market state changes and adjust exposure. "
        "Strategies fail when applying a single model across different market conditions. "
        "Detect regime via rolling return/vol: bull (ret>0.001, vol<70th pctile), "
        "bear (ret<-0.001, vol>70th pctile), sideways (between)."
    ),
    "volatility": (
        "Volatility clustering: high-vol periods cluster together. "
        "GARCH(1,1) models conditional variance. "
        "Implied vol > realized vol = risk premium. "
        "Vol regime switching affects position sizing and stop placement."
    ),
    "drawdown_management": (
        "Max drawdown triggers: reduce exposure at 5% DD, halt at 10%. "
        "Kelly criterion for position sizing: f* = (bp - q) / b. "
        "Half-Kelly (f*/2) reduces variance by 75% with only 25% return reduction."
    ),
    "beta_hedging": (
        "Beta hedging removes market exposure: hedge_qty = beta * portfolio_value / futures_price. "
        "Rolling beta (60-day) captures time-varying market sensitivity. "
        "Residual alpha after hedging is the true active return."
    ),
    "diversification": (
        "Correlation breakdown in crises: diversification fails when most needed. "
        "Minimum variance portfolios outperform in risk-adjusted terms. "
        "Risk parity equalizes risk contribution across assets."
    ),
    "asset_pricing": (
        "Fama-French 3-factor: market, size (SMB), value (HML). "
        "Carhart 4-factor adds momentum (UMD). "
        "Alpha = excess return unexplained by factor exposures."
    ),
    "time_series": (
        "Mean reversion: z-score entry at ±2σ, exit at 0. "
        "Momentum: trend following via dual MA crossover. "
        "Cointegration for pairs trading: Engle-Granger or Johansen test."
    ),
    "interest_rates": (
        "Yield curve inversion (10Y-2Y < 0) signals recession 12-18 months ahead. "
        "Fed Funds rate changes transmit to FX via interest rate differential. "
        "Duration risk: 1bp rate change ≈ 0.01% price change per year of duration."
    ),
    "options": (
        "Delta hedging: maintain delta-neutral portfolio. "
        "Gamma risk: rapid delta changes near expiration. "
        "Implied vol surface: skew reflects crash protection demand."
    ),
    "causal_inference": (
        "Granger causality: does X help predict Y beyond Y's own lags? "
        "Instrumental variables for causal identification. "
        "Difference-in-differences for policy impact assessment."
    ),
    "pca_factors": (
        "PCA extracts dominant modes of variation. "
        "First PC often explains 60-80% of variance (market mode). "
        "PC2-3 capture sector/style rotations."
    ),
    "llm_finance": (
        "LLM sentiment from news/earnings calls predicts short-term returns. "
        "Sentiment decay: signal half-life ~3 days for news, ~15 days for 10-K. "
        "Combine with technical signals for robust alpha."
    ),
    "transfer_learning": (
        "Transfer learning: pre-train on liquid markets, fine-tune on target. "
        "Domain adaptation bridges distribution shift between asset classes. "
        "Meta-learning (MAML) enables few-shot regime adaptation."
    ),
    "value_growth": (
        "Value factor: book-to-market, earnings yield, FCF yield. "
        "Growth factor: revenue growth, earnings momentum. "
        "Value-growth rotation cycles last 5-10 years."
    ),
    "ai_trading": (
        "Reinforcement learning for execution: minimize market impact. "
        "PPO/GRPO for position management with risk constraints. "
        "Ensemble of RL + rules beats either alone in live trading."
    ),
    "options_gamma": (
        "Dealer gamma exposure (GEX) determines how options market-makers delta-hedge. "
        "Positive GEX: dealers sell rallies, buy dips → suppress vol. "
        "Negative GEX: dealers buy rallies, sell dips → amplify vol."
    ),
    "cumulative_volume_delta": (
        "CVD tracks net aggressive buying vs selling over time. "
        "Absorption: CVD rises but price fails to follow → large order absorbing."
    ),
    "har_rv": (
        "HAR-RV (Corsi 2009) forecasts realized volatility using daily, weekly, "
        "monthly RV components. Captures long-memory in vol persistence."
    ),
    "index_rebalancing": (
        "Index additions cause pre-announcement front-running (structural buying). "
        "Deletions cause indiscriminate dumping. Post-reconstitution: reversal effect."
    ),
    "cross_sectional_momentum": (
        "12-1 CSMOM: rank assets by cumulative return over [t-12m, t-1m], skip month 1. "
        "Long winners, short losers. Strongest after risk-off periods."
    ),
    "market_profile": (
        "VPOC = price level with highest volume. Value Area = 70% of volume range. "
        "Buy tail = volume below VPOC, sell tail = volume above VPOC."
    ),
    "merger_arb": (
        "Merger arb: deal spread = (deal_price - current_price) / current_price. "
        "Annualized spread gives yield if deal closes."
    ),
    "turn_of_month": (
        "TOTM effect: positive drift during last trading day + first 3 days of month. "
        "Driven by pension/401(k) inflows and corporate dividend deployment."
    ),
    "crypto_funding": (
        "Crypto perpetual funding rate = 8-hour rate between longs and shorts. "
        "Cash-and-carry: long spot + short perp = delta-neutral yield."
    ),
    "roll_model": (
        "Roll (1984): effective bid-ask spread from tick autocovariance. "
        "Cov(ΔP_t, ΔP_{t-1}) = -c², where c = half-spread."
    ),
    "vrp_harvest": (
        "Variance Risk Premium harvest: sell options when IV > RV. "
        "IV > RV ~85% of the time. Use iron condors for range-bound, "
        "short straddles for high vol compression expectations."
    ),
    "pead": (
        "Post-Earnings Announcement Drift: SUE > 2.5 → long drift 20-60 days. "
        "SUE = (Actual EPS - Consensus) / StdDev. Volume must confirm (>3x avg)."
    ),
    "spinoff": (
        "Spinoff undervaluation: institutions dump mandated shares on day 1. "
        "Wait 5-15 days for volume exhaustion, then enter. ~12% 12m outperformance."
    ),
    "buyback_10b18": (
        "Rule 10b-18: companies cannot repurchase during 2-4 week blackout pre-earnings. "
        "Post-blackout resumption creates upward drift in buyback-heavy equities."
    ),
    "max_pain": (
        "Max Pain: strike where total options OI payout is minimized. "
        "Dealers' delta hedging pins price near max pain on OPEX day."
    ),
    "lottery_effect": (
        "Retail overpays for high-skew OTM calls (lottery tickets). "
        "When call skew premium is extreme, sell call spreads to harvest overpricing."
    ),
    "s_score": (
        "S-Score: PCA residual spread normalized by OU volatility. "
        "s < -1.5 → buy stock, short eigen-factors. "
        "s > 1.5 → short stock, buy eigen-factors."
    ),
    "kyle_lambda": (
        "Kyle (1985): lambda = Cov(dP, V) / Var(V). "
        "High lambda = informed traders dominate, thin market. "
        "Low lambda = noise traders, deep market."
    ),
    "ledoit_wolf": (
        "Ledoit-Wolf shrinkage: regularize covariance when N > T. "
        "Shrinks sample cov toward identity: (1-α)S + αμI."
    ),
    "black_litterman": (
        "Black-Litterman: Bayesian portfolio from market equilibrium + investor views. "
        "Pi = δΣw_mkt. Posterior blends equilibrium with views via confidence."
    ),
}

# Keyword → module relevance mapping
_KEYWORD_MAP: Dict[str, List[str]] = {
    "regime": ["regime_detection", "volatility"],
    "volatil": ["volatility", "options"],
    "drawdown": ["drawdown_management"],
    "risk": ["drawdown_management", "diversification", "beta_hedging"],
    "crisis": ["drawdown_management", "diversification", "volatility"],
    "hedg": ["beta_hedging", "options"],
    "correl": ["diversification", "pca_factors"],
    "yield": ["interest_rates", "asset_pricing"],
    "interest": ["interest_rates"],
    "option": ["options"],
    "momentum": ["time_series", "asset_pricing"],
    "mean_revert": ["time_series"],
    "pairs": ["time_series"],
    "sentiment": ["llm_finance"],
    "news": ["llm_finance"],
    "kelly": ["drawdown_management"],
    "position": ["drawdown_management", "ai_trading"],
    "factor": ["asset_pricing", "pca_factors"],
    "alpha": ["asset_pricing", "llm_finance"],
    "reinforcement": ["ai_trading"],
    "ppo": ["ai_trading"],
    "rl": ["ai_trading"],
    "causal": ["causal_inference"],
    "transfer": ["transfer_learning"],
    "meta_learn": ["transfer_learning"],
    "gamma": ["options_gamma"],
    "gex": ["options_gamma"],
    "cvd": ["cumulative_volume_delta"],
    "volume_delta": ["cumulative_volume_delta"],
    "realized_vol": ["har_rv"],
    "rv_forecast": ["har_rv"],
    "reconstitution": ["index_rebalancing"],
    "russell": ["index_rebalancing"],
    "cs_mom": ["cross_sectional_momentum"],
    "vpoc": ["market_profile"],
    "value_area": ["market_profile"],
    "deal_spread": ["merger_arb"],
    "merger": ["merger_arb"],
    "totm": ["turn_of_month"],
    "funding_rate": ["crypto_funding"],
    "perpetual": ["crypto_funding"],
    "roll_spread": ["roll_model"],
    "autocovariance": ["roll_model"],
    "vrp": ["vrp_harvest", "volatility"],
    "options_selling": ["vrp_harvest"],
    "straddle": ["vrp_harvest"],
    "iron_condor": ["vrp_harvest"],
    "pead": ["pead"],
    "earnings_drift": ["pead"],
    "sue": ["pead"],
    "spinoff": ["spinoff"],
    "buyback": ["buyback_10b18"],
    "blackout": ["buyback_10b18"],
    "opex": ["max_pain"],
    "max_pain": ["max_pain"],
    "pinning": ["max_pain"],
    "lottery": ["lottery_effect"],
    "lotto": ["lottery_effect"],
    "call_skew": ["lottery_effect"],
    "s_score": ["s_score"],
    "stat_arb": ["s_score"],
    "ornstein": ["s_score"],
    "kyle": ["kyle_lambda"],
    "price_impact": ["kyle_lambda"],
    "adverse_selection": ["kyle_lambda", "roll_model"],
    "shrinkage": ["ledoit_wolf"],
    "covariance": ["ledoit_wolf"],
    "black_litterman": ["black_litterman"],
    "bl_model": ["black_litterman"],
}


def get_knowledge_base_context(
    regime: str = "",
    keywords: List[str] | None = None,
    max_chars: int = 800,
) -> str:
    """Return relevant knowledge base excerpts based on current market context.

    Args:
        regime: Current regime label (e.g. "CRISIS_PANIC", "RISK_ON_EXPANSION")
        keywords: Additional keywords to match modules
        max_chars: Maximum character budget for returned context

    Returns:
        Bounded context string for LLM injection, or empty string if no match.
    """
    matched_modules: set = set()

    # Match by regime
    regime_lower = regime.lower()
    for kw, modules in _KEYWORD_MAP.items():
        if kw in regime_lower:
            matched_modules.update(modules)

    # Match by keywords
    for kw in (keywords or []):
        kw_lower = str(kw).lower()
        for map_kw, modules in _KEYWORD_MAP.items():
            if map_kw in kw_lower:
                matched_modules.update(modules)

    if not matched_modules:
        return ""

    lines: List[str] = []
    char_count = 0
    for mod in sorted(matched_modules):
        excerpt = _EXCERPTS.get(mod, "")
        if not excerpt:
            continue
        line = f"[KB:{mod}] {excerpt}"
        if char_count + len(line) > max_chars:
            break
        lines.append(line)
        char_count += len(line)

    return "\n".join(lines)


# Aliases & Class Facade
get_relevant_context = get_knowledge_base_context


class KnowledgeContextProvider:
    """Class interface for trading knowledge base context retrieval."""
    @staticmethod
    def get_relevant_context(*args, **kwargs) -> str:
        return get_knowledge_base_context(*args, **kwargs)

    @staticmethod
    def list_available_modules() -> List[str]:
        return sorted(_EXCERPTS.keys())
