"""Strategy Universe Matrix Engine — Core Taxonomy & Fast Evaluation.

Formalizes the 15 strategy styles and 12 trading types across market regimes,
capital scales, and asset classes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Tuple, Sequence
import numpy as np


class StrategyStyle(str, Enum):
    TREND_FOLLOWING = "TREND_FOLLOWING"
    MEAN_REVERSION = "MEAN_REVERSION"
    BREAKOUT = "BREAKOUT"
    MOMENTUM = "MOMENTUM"
    VOLATILITY = "VOLATILITY"
    CARRY = "CARRY"
    STATISTICAL_ARBITRAGE = "STATISTICAL_ARBITRAGE"
    MARKET_NEUTRAL = "MARKET_NEUTRAL"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    CROSS_ASSET_MACRO = "CROSS_ASSET_MACRO"
    INTRADAY_SCALPING = "INTRADAY_SCALPING"
    SWING = "SWING"
    POSITION_TRADING = "POSITION_TRADING"
    ML_SIGNAL = "ML_SIGNAL"
    RL_POLICY = "RL_POLICY"


class TradingType(str, Enum):
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"
    LONG_SHORT = "LONG_SHORT"
    DIRECTIONAL = "DIRECTIONAL"
    PAIRS_SPREAD = "PAIRS_SPREAD"
    FUTURES_PERP = "FUTURES_PERP"
    OPTIONS_BASED = "OPTIONS_BASED"
    SPOT = "SPOT"
    CROSS_EXCHANGE_ARB = "CROSS_EXCHANGE_ARB"
    EXECUTION_ALGO = "EXECUTION_ALGO"
    HEDGED_PORTFOLIO = "HEDGED_PORTFOLIO"
    MULTI_ASSET_ALLOCATION = "MULTI_ASSET_ALLOCATION"


class MarketRegime(str, Enum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    SIDEWAYS_CONSOLIDATION = "SIDEWAYS_CONSOLIDATION"
    HIGH_VOL_EXPANSION = "HIGH_VOL_EXPANSION"
    LOW_VOL_COMPRESSION = "LOW_VOL_COMPRESSION"
    STRESS_LIQUIDITY_SHOCK = "STRESS_LIQUIDITY_SHOCK"


class CapitalScale(str, Enum):
    MICRO = "MICRO"            # $500 - $2,000
    SMALL = "SMALL"            # $10,000
    MEDIUM = "MEDIUM"          # $100,000
    INSTITUTIONAL = "INSTITUTIONAL"  # $1,000,000+


@dataclass
class StrategySpec:
    strategy_id: str
    name: str
    style: StrategyStyle
    trading_type: TradingType
    regime_suitability: List[MarketRegime]
    target_symbols: List[str]
    timeframes: List[str]
    capital_scale: CapitalScale = CapitalScale.SMALL
    parameters: Dict[str, Any] = field(default_factory=dict)
    execution_authority: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "style": self.style.value,
            "trading_type": self.trading_type.value,
            "regime_suitability": [r.value for r in self.regime_suitability],
            "target_symbols": self.target_symbols,
            "timeframes": self.timeframes,
            "capital_scale": self.capital_scale.value,
            "parameters": self.parameters,
            "execution_authority": self.execution_authority,
            "created_at": self.created_at,
        }
